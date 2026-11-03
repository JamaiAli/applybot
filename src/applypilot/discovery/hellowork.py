"""Adaptateur de découverte HelloWork (scraping HTML de la page de recherche).

HelloWork n'a PAS d'API publique : on récupère la page de recherche rendue côté
serveur et on parse les cartes d'offres. Sélecteurs stables repérés sur le site :
chaque offre est un lien `a[data-cy="offerTitle"]` portant :
  - href      = /fr-fr/emplois/{id}.html
  - title     = "Intitulé - Entreprise"
  - aria-label= "Voir offre de {intitulé} à {ville}"

⚠️ HelloWork lutte contre le scraping automatisé : on y va à **rythme lent**, et
l'adaptateur est un **no-op gracieux** si le site bloque (403 / contenu vide).
C'est de l'usage PERSONNEL de recherche de stage, pas du scraping massif.

Bloc de configuration (optionnel) dans searches.yaml :

    hellowork:
      queries:
        - "cybersécurité"
        - "administrateur systèmes réseaux"
        - "intégration IT"
      contract: Stage          # Stage | Alternance
      max_pages: 2             # pages de résultats par requête
      delay_seconds: 2.0       # délai entre requêtes (rythme lent)

Pas de LLM. HTTP + parsing HTML (httpx + BeautifulSoup).
"""

from __future__ import annotations

import logging
import re
import time

import httpx
from bs4 import BeautifulSoup

from applypilot import config
from applypilot.database import get_connection, init_db
from applypilot.discovery.ats_common import insert_normalized_jobs

log = logging.getLogger(__name__)

BASE = "https://www.hellowork.com"
SEARCH_URL = BASE + "/fr-fr/emploi/recherche.html"
STRATEGY = "hellowork_scrape"
DEFAULT_SITE = "HelloWork"

_DEFAULT_QUERIES = [
    "cybersécurité",
    "administrateur systèmes réseaux",
    "intégration IT",
]

# En-têtes d'un vrai navigateur (réduit les refus ; pas de contournement de
# détection — si HelloWork bloque, on abandonne proprement).
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}


def _parse_cards(html: str) -> list[dict]:
    """Extrait les offres d'une page de résultats HelloWork."""
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[dict] = []
    seen: set[str] = set()
    for a in soup.select('a[data-cy="offerTitle"]'):
        href = a.get("href") or ""
        if not re.search(r"/emplois/\d+", href):
            continue
        if href in seen:
            continue
        seen.add(href)
        full = (a.get("title") or a.get_text(strip=True) or "").strip()
        if " - " in full:
            title, company = full.rsplit(" - ", 1)
        else:
            title, company = full, DEFAULT_SITE
        aria = a.get("aria-label") or ""
        location = aria.split(" à ", 1)[1].strip() if " à " in aria else None
        url = href if href.startswith("http") else BASE + href
        desc = (f"{title} chez {company}."
                + (f" Lieu : {location}." if location else "")
                + " Contrat : Stage.")
        jobs.append({
            "url": url,
            "title": title.strip(),
            "location": location,
            "description": desc,
            "full_description": None,   # le détail complet est sur la page offre
            "application_url": url,
            "employer_name": company.strip() or DEFAULT_SITE,
            "posted_at": None,
        })
    return jobs


def _fetch(client: httpx.Client, query: str, contract: str, page: int) -> str:
    params = {"k": query, "c": contract}
    if page > 1:
        params["p"] = page
    r = client.get(SEARCH_URL, params=params, headers=_HEADERS, timeout=20)
    r.raise_for_status()
    return r.text


def _insert_jobs(conn, jobs: list[dict]) -> tuple[int, int]:
    """Shim de stockage (utilisé par les tests), via le helper commun."""
    return insert_normalized_jobs(conn, jobs, DEFAULT_SITE, STRATEGY)


# ── Point d'entrée ─────────────────────────────────────────────────────────

def run_hellowork_discovery(workers: int = 1) -> dict:
    """Découvre des offres de STAGE via le scraping de HelloWork.

    No-op gracieux si le site bloque (403) ou renvoie du contenu vide.
    """
    config.load_env()
    cfg = (config.load_search_config() or {}).get("hellowork", {}) or {}
    queries = cfg.get("queries") or _DEFAULT_QUERIES
    contract = str(cfg.get("contract", "Stage"))
    max_pages = int(cfg.get("max_pages", 2))
    delay = float(cfg.get("delay_seconds", 2.0))

    conn = get_connection()
    init_db()

    grand_new = grand_existing = grand_found = 0
    errors: list[str] = []

    log.info("HelloWork : %d requête(s), max %d page(s)/requête (rythme lent)",
             len(queries), max_pages)
    with httpx.Client(follow_redirects=True) as client:
        for query in queries:
            for page in range(1, max_pages + 1):
                try:
                    html = _fetch(client, query, contract, page)
                except Exception as e:
                    log.warning("HelloWork : '%s' page %d échouée (%s) — "
                                "le site bloque peut-être le scraping.", query, page, e)
                    errors.append(f"{query} p{page}: {e}")
                    break  # inutile d'insister sur les pages suivantes
                jobs = _parse_cards(html)
                if not jobs:
                    break  # plus de résultats
                new, existing = insert_normalized_jobs(conn, jobs, DEFAULT_SITE, STRATEGY)
                grand_new += new
                grand_existing += existing
                grand_found += len(jobs)
                log.info("HelloWork '%s' p%d : %d offres → %d new, %d existing",
                         query, page, len(jobs), new, existing)
                time.sleep(delay)

    log.info("HelloWork terminé : %d offres (%d new, %d existing)",
             grand_found, grand_new, grand_existing)
    return {"found": grand_found, "new": grand_new,
            "existing": grand_existing, "errors": errors}
