"""Adaptateur de découverte Adzuna (agrégateur d'offres, endpoint France).

API officielle et gratuite, dont la clé est **ouverte aux développeurs, y compris
les particuliers** (contrairement à l'API France Travail, réservée aux
collectivités/entreprises). Inscription : https://developer.adzuna.com →
créer une application → App ID + App Key, à placer dans ~/.applypilot/.env :

    ADZUNA_APP_ID=...
    ADZUNA_APP_KEY=...

Bloc de configuration (optionnel) dans searches.yaml :

    adzuna:
      country: fr
      queries:
        - "stage cybersécurité"
        - "stage administrateur systèmes et réseaux"
        - "stage intégration IT"
      where: ""               # ville / département, optionnel
      max_days_old: 30
      max_pages: 3            # pages de `results_per_page` offres par requête
      results_per_page: 50
      include_alternance: true

Adzuna n'expose pas de filtre « stage » côté serveur : on recherche par mots-clés
puis on filtre côté client sur le libellé (stage / alternance / apprentissage).

Attribution « Jobs by Adzuna » requise en cas d'affichage public (usage personnel
de recherche de stage : OK). Limites gratuites : ~250 appels/jour.
Pas de LLM, pas de navigateur — HTTP pur.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from applypilot import config
from applypilot.database import get_connection, init_db
from applypilot.discovery.ats_common import insert_normalized_jobs

log = logging.getLogger(__name__)

SEARCH_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
STRATEGY = "adzuna_api"
DEFAULT_SITE = "Adzuna"

_DEFAULT_QUERIES = [
    "stage cybersécurité",
    "stage administrateur systèmes et réseaux",
    "stage intégration IT",
]

_STAGE_RE = re.compile(
    r"stage|stagiaire|alternan|apprenti|professionnalisation|contrat\s+pro",
    re.IGNORECASE,
)
_STAGE_STRICT_RE = re.compile(r"stage|stagiaire", re.IGNORECASE)


# ── Recherche ─────────────────────────────────────────────────────────────

def _search(app_id: str, app_key: str, country: str, query: str, page: int,
            cfg: dict, timeout: float = 20.0) -> tuple[list[dict], int]:
    """Un appel de recherche Adzuna. Renvoie (resultats, nombre_total)."""
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": int(cfg.get("results_per_page", 50)),
        "what": query,
        "content-type": "application/json",
        "sort_by": "date",
    }
    if cfg.get("where"):
        params["where"] = str(cfg["where"])
    if cfg.get("max_days_old"):
        params["max_days_old"] = int(cfg["max_days_old"])

    url = SEARCH_URL.format(country=country, page=page) + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code} {e.reason}: {detail}".strip()) from e
    return data.get("results", []) or [], int(data.get("count", 0) or 0)


def _collect(app_id: str, app_key: str, country: str, query: str,
             cfg: dict, max_pages: int) -> list[dict]:
    rpp = int(cfg.get("results_per_page", 50))
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        results, _count = _search(app_id, app_key, country, query, page, cfg)
        out.extend(results)
        if len(results) < rpp:
            break  # dernière page
        time.sleep(0.4)  # politesse / rate-limit
    return out


# ── Normalisation / filtre stage ──────────────────────────────────────────

def _is_stage(result: dict, include_alternance: bool = True) -> bool:
    """Vrai si l'offre est un stage (ou une alternance si autorisée)."""
    category = result.get("category") or {}
    hay = " ".join([
        str(result.get("title", "")),
        str(category.get("label", "")),
        str(result.get("contract_time", "")),
    ])
    pattern = _STAGE_RE if include_alternance else _STAGE_STRICT_RE
    return bool(pattern.search(hay))


def _normalize_result(result: dict) -> dict:
    """Convertit un résultat Adzuna vers le dict normalisé du pipeline."""
    url = result.get("redirect_url") or None
    location = result.get("location") or {}
    company = result.get("company") or {}
    desc = (result.get("description") or "").strip()
    return {
        "url": url,
        "title": result.get("title") or "",
        "location": location.get("display_name") or None,
        "description": (desc[:500] or None),
        "full_description": desc if len(desc) > 200 else None,
        "application_url": url,
        "employer_name": company.get("display_name") or DEFAULT_SITE,
        "posted_at": result.get("created") or None,
    }


def _insert_jobs(conn, jobs: list[dict]) -> tuple[int, int]:
    """Shim de stockage (utilisé par les tests), via le helper commun."""
    return insert_normalized_jobs(conn, jobs, DEFAULT_SITE, STRATEGY)


# ── Point d'entrée ─────────────────────────────────────────────────────────

def run_adzuna_discovery(workers: int = 1) -> dict:
    """Découvre des offres de STAGE via l'API Adzuna (endpoint France par défaut).

    No-op gracieux (aucune erreur) si les identifiants ne sont pas configurés.
    """
    config.load_env()
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        log.warning(
            "Adzuna : ADZUNA_APP_ID / ADZUNA_APP_KEY absents du .env — source ignorée. "
            "Inscription libre sur https://developer.adzuna.com"
        )
        return {"found": 0, "new": 0, "existing": 0,
                "skipped": "missing credentials", "errors": []}

    cfg = (config.load_search_config() or {}).get("adzuna", {}) or {}
    country = str(cfg.get("country", "fr"))
    queries = cfg.get("queries") or _DEFAULT_QUERIES
    max_pages = int(cfg.get("max_pages", 3))
    include_alt = cfg.get("include_alternance", True)

    conn = get_connection()
    init_db()

    grand_new = grand_existing = grand_found = 0
    errors: list[str] = []

    log.info("Adzuna (%s) : %d requête(s), max %d page(s)/requête", country, len(queries), max_pages)
    for query in queries:
        try:
            results = _collect(app_id, app_key, country, query, cfg, max_pages)
        except Exception as e:
            log.warning("Adzuna : recherche '%s' échouée : %s", query, e)
            errors.append(f"{query}: {e}")
            continue

        jobs: list[dict] = []
        for result in results:
            if not _is_stage(result, include_alt):
                continue
            norm = _normalize_result(result)
            if norm["url"]:
                jobs.append(norm)

        new, existing = insert_normalized_jobs(conn, jobs, DEFAULT_SITE, STRATEGY)
        grand_new += new
        grand_existing += existing
        grand_found += len(jobs)
        log.info("Adzuna '%s' : %d offres, %d stage → %d new, %d existing",
                 query, len(results), len(jobs), new, existing)
        time.sleep(0.5)

    log.info("Adzuna terminé : %d stages trouvés (%d new, %d existing)",
             grand_found, grand_new, grand_existing)
    return {
        "found": grand_found,
        "new": grand_new,
        "existing": grand_existing,
        "errors": errors,
    }
