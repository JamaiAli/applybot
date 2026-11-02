"""Adaptateur de découverte France Travail (« Offres d'emploi v2 »).

API officielle et gratuite de France Travail (ex-Pôle emploi). Authentification
OAuth2 client-credentials, puis un endpoint de recherche. Ciblé sur les STAGES
(et, en option, l'alternance) du marché français.

Identifiants : crée une application sur https://francetravail.io et abonne-la à
l'API « Offres d'emploi v2 » pour obtenir un client_id / client_secret, puis
place-les dans ~/.applypilot/.env :

    FRANCE_TRAVAIL_CLIENT_ID=...
    FRANCE_TRAVAIL_CLIENT_SECRET=...

Bloc de configuration (optionnel) dans searches.yaml — valeurs par défaut
raisonnables si absent :

    france_travail:
      queries:               # mots-clés recherchés (un appel par entrée)
        - "stage cybersécurité"
        - "stage administrateur systèmes"
      type_contrat: ""        # code contrat côté serveur, optionnel (voir filtresPossibles)
      departement: ""         # ex. "18" (Cher) — optionnel
      region: ""              # code région France Travail — optionnel
      distance_km: 0          # rayon autour de la commune/département — optionnel
      max_pages: 2            # pages de 150 offres par requête
      include_alternance: true

Le code contrat « stage » de l'API n'étant pas documenté de façon fiable, le
filtre stage est fait côté client sur le libellé (stage / alternance /
apprentissage), ce qui garantit le résultat quel que soit le code serveur. Le
champ ``type_contrat`` permet, si tu le souhaites, un filtrage serveur une fois
le code confirmé via ``filtresPossibles``.

Pas de LLM, pas de navigateur — HTTP pur. L'API renvoie la description complète,
donc les offres arrivent déjà « enriched ».
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


TOKEN_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire"
SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
SCOPE = "api_offresdemploiv2 o2dsoffre"

STRATEGY = "france_travail_api"
DEFAULT_SITE = "France Travail"
_PAGE_SIZE = 150

_DEFAULT_QUERIES = [
    "stage cybersécurité",
    "stage administrateur systèmes et réseaux",
    "stage intégration IT",
]

# Libellés indiquant un stage / une alternance (filtre côté client).
_STAGE_RE = re.compile(
    r"stage|stagiaire|alternan|apprenti|professionnalisation|contrat\s+pro",
    re.IGNORECASE,
)
_STAGE_STRICT_RE = re.compile(r"stage|stagiaire", re.IGNORECASE)


# ── Authentification OAuth2 ───────────────────────────────────────────────

_token_cache: dict = {"token": None, "expires_at": 0.0}


def _request_token(client_id: str, client_secret: str, timeout: float = 20.0) -> tuple[str, int]:
    """Demande un jeton client-credentials. Renvoie (token, expires_in_secondes)."""
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": SCOPE,
    }).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_URL, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["access_token"], int(data.get("expires_in", 1500))


def _get_token(client_id: str, client_secret: str) -> str:
    """Jeton mis en cache (renouvelé un peu avant expiration)."""
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]
    token, ttl = _request_token(client_id, client_secret)
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + max(60, ttl - 60)
    return token


# ── Recherche ─────────────────────────────────────────────────────────────

def _build_params(query: str, cfg: dict, start: int, end: int) -> dict:
    params: dict = {"motsCles": query, "range": f"{start}-{end}", "sort": "1"}
    if cfg.get("type_contrat"):
        params["typeContrat"] = cfg["type_contrat"]
    if cfg.get("departement"):
        params["departement"] = str(cfg["departement"])
    if cfg.get("region"):
        params["region"] = str(cfg["region"])
    if cfg.get("distance_km"):
        params["distance"] = str(cfg["distance_km"])
    return params


def _search_offres(token: str, params: dict, timeout: float = 20.0) -> tuple[list[dict], bool]:
    """Renvoie (liste_offres, autres_pages). 204 = aucune offre, 206 = partiel."""
    url = SEARCH_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.getcode()
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 204:
            return [], False
        raise
    if code == 204 or not raw:
        return [], False
    data = json.loads(raw)
    offres = data.get("resultats", []) or []
    return offres, code == 206


def _collect(token: str, query: str, cfg: dict, max_pages: int) -> list[dict]:
    out: list[dict] = []
    for page in range(max_pages):
        start = page * _PAGE_SIZE
        end = start + _PAGE_SIZE - 1
        offres, has_more = _search_offres(token, _build_params(query, cfg, start, end))
        out.extend(offres)
        if not has_more or not offres:
            break
        time.sleep(0.3)
    return out


# ── Normalisation / filtre stage ──────────────────────────────────────────

def _is_stage(offre: dict, include_alternance: bool = True) -> bool:
    """Vrai si l'offre est un stage (ou une alternance si autorisée)."""
    hay = " ".join(
        str(offre.get(k, "")) for k in ("intitule", "typeContratLibelle", "natureContrat")
    )
    pattern = _STAGE_RE if include_alternance else _STAGE_STRICT_RE
    return bool(pattern.search(hay))


def _normalize_offre(offre: dict) -> dict:
    """Convertit une offre France Travail vers le dict normalisé du pipeline."""
    oid = offre.get("id") or ""
    origine = offre.get("origineOffre") or {}
    url = origine.get("urlOrigine") or (
        f"https://candidat.francetravail.fr/offres/recherche/detail/{oid}" if oid else None
    )
    lieu = offre.get("lieuTravail") or {}
    entreprise = offre.get("entreprise") or {}
    desc = (offre.get("description") or "").strip()
    return {
        "url": url,
        "title": offre.get("intitule") or "",
        "location": lieu.get("libelle") or None,
        "description": (desc[:500] or None),
        "full_description": desc if len(desc) > 200 else None,
        "application_url": url,
        "employer_name": entreprise.get("nom") or DEFAULT_SITE,
        "posted_at": offre.get("dateCreation") or None,
    }


def _insert_jobs(conn, jobs: list[dict]) -> tuple[int, int]:
    """Shim de stockage (utilisé par les tests), via le helper commun."""
    return insert_normalized_jobs(conn, jobs, DEFAULT_SITE, STRATEGY)


# ── Point d'entrée ─────────────────────────────────────────────────────────

def run_francetravail_discovery(workers: int = 1) -> dict:
    """Découvre des offres de STAGE via l'API France Travail « Offres d'emploi v2 ».

    No-op gracieux (aucune erreur) si les identifiants ne sont pas configurés.
    """
    config.load_env()
    client_id = os.environ.get("FRANCE_TRAVAIL_CLIENT_ID")
    client_secret = os.environ.get("FRANCE_TRAVAIL_CLIENT_SECRET")
    if not (client_id and client_secret):
        log.warning(
            "France Travail : FRANCE_TRAVAIL_CLIENT_ID / FRANCE_TRAVAIL_CLIENT_SECRET "
            "absents du .env — source ignorée. Voir francetravail.io pour créer une application."
        )
        return {"found": 0, "new": 0, "existing": 0,
                "skipped": "missing credentials", "errors": []}

    ft_cfg = (config.load_search_config() or {}).get("france_travail", {}) or {}
    queries = ft_cfg.get("queries") or _DEFAULT_QUERIES
    max_pages = int(ft_cfg.get("max_pages", 2))
    include_alt = ft_cfg.get("include_alternance", True)

    try:
        token = _get_token(client_id, client_secret)
    except Exception as e:
        log.error("France Travail : échec de l'authentification OAuth : %s", e)
        return {"found": 0, "new": 0, "existing": 0, "errors": [f"auth: {e}"]}

    conn = get_connection()
    init_db()

    grand_new = grand_existing = grand_found = 0
    errors: list[str] = []

    log.info("France Travail : %d requête(s), max %d page(s)/requête", len(queries), max_pages)
    for query in queries:
        try:
            offres = _collect(token, query, ft_cfg, max_pages)
        except Exception as e:
            log.warning("France Travail : recherche '%s' échouée : %s", query, e)
            errors.append(f"{query}: {e}")
            continue

        jobs: list[dict] = []
        for offre in offres:
            if not _is_stage(offre, include_alt):
                continue
            norm = _normalize_offre(offre)
            if norm["url"]:
                jobs.append(norm)

        new, existing = insert_normalized_jobs(conn, jobs, DEFAULT_SITE, STRATEGY)
        grand_new += new
        grand_existing += existing
        grand_found += len(jobs)
        log.info("France Travail '%s' : %d offres, %d stage → %d new, %d existing",
                 query, len(offres), len(jobs), new, existing)
        time.sleep(0.5)  # politesse / rate-limit

    log.info("France Travail terminé : %d stages trouvés (%d new, %d existing)",
             grand_found, grand_new, grand_existing)
    return {
        "found": grand_found,
        "new": grand_new,
        "existing": grand_existing,
        "errors": errors,
    }
