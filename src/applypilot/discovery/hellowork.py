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

import json
import logging
import re
import time

import httpx
from bs4 import BeautifulSoup

from applypilot import config
from applypilot.database import get_connection, init_db, transition_state
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
    """Extrait les offres d'une page de résultats HelloWork.

    Essaie plusieurs sélecteurs dans l'ordre pour résister aux changements
    de structure HTML du site.
    """
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[dict] = []
    seen: set[str] = set()

    # Sélecteurs candidats, du plus spécifique au plus générique.
    # HelloWork a changé data-cy="offerTitle" → on tente tous les patterns connus.
    CANDIDATE_SELECTORS = [
        'a[data-cy="offerTitle"]',           # ancien sélecteur stable
        'a[data-testid="job-card-title"]',   # variante testid
        'a[data-testid*="offer"]',
        'li[data-cy*="offer"] a[href*="/emplois/"]',
        'article a[href*="/emplois/"]',
        'div[class*="offer"] a[href*="/emplois/"]',
        'div[class*="job-card"] a[href*="/emplois/"]',
        'a[href*="/emplois/"][class*="title"]',
        # Fallback large : tout lien vers une offre
        'a[href*="/emplois/"]',
    ]

    for selector in CANDIDATE_SELECTORS:
        try:
            anchors = soup.select(selector)
        except Exception:
            continue
        if not anchors:
            continue

        for a in anchors:
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

        if jobs:
            # Un sélecteur a fonctionné → on s'arrête
            log.debug("HelloWork _parse_cards : sélecteur %r → %d offres", selector, len(jobs))
            break

    return jobs


def _jsonld_jobposting(html: str) -> dict:
    """Extrait le JSON-LD schema.org JobPosting d'une page d'offre HelloWork :
    description COMPLÈTE + entreprise + lieu + date. Données structurées =
    stables (bien plus fiable que le parsing HTML)."""
    soup = BeautifulSoup(html, "html.parser")
    for s in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = s.string or s.get_text() or ""
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        for it in (data if isinstance(data, list) else [data]):
            if not isinstance(it, dict):
                continue
            t = it.get("@type")
            if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                desc = BeautifulSoup(it.get("description") or "", "html.parser").get_text(" ", strip=True)
                org = (it.get("hiringOrganization") or {})
                loc = it.get("jobLocation") or {}
                if isinstance(loc, list):
                    loc = loc[0] if loc else {}
                addr = (loc.get("address") or {}) if isinstance(loc, dict) else {}
                city = addr.get("addressLocality")
                return {
                    "full_description": desc or None,
                    "employer_name": org.get("name") if isinstance(org, dict) else None,
                    "location": city,
                    "posted_at": it.get("datePosted"),
                }
    return {}


def _fetch_detail(client: httpx.Client, url: str) -> dict:
    """Récupère la description complète d'une offre via son JSON-LD."""
    try:
        r = client.get(url, headers=_HEADERS, timeout=20)
        r.raise_for_status()
        return _jsonld_jobposting(r.text)
    except Exception as e:
        log.debug("HelloWork détail %s : %s", url, e)
        return {}


def _fetch(client: httpx.Client, query: str, contract: str, page: int,
           location: str = "") -> str:
    params = {"st": "date"}  # tri par DATE (offres les plus récentes d'abord)
    if query:
        params["k"] = query
    if contract:
        params["c"] = contract
    if location:
        params["l"] = location
    if page > 1:
        params["p"] = page
    r = client.get(SEARCH_URL, params=params, headers=_HEADERS, timeout=20)
    r.raise_for_status()
    return r.text


def search_live(query: str = "", location: str = "", contract: str = "",
                max_pages: int = 1, limit: int = 15) -> list[dict]:
    """Recherche HelloWork « à la volée » (SANS insertion en base) — pour /job.
    Renvoie une liste de {url, title, employer_name, location, ...} plafonnée.

    Contrats HelloWork : "" (tous), "Stage", "Alternance", "CDI", "CDD",
    "Travail_temp" (= Intérim).
    """
    out: list[dict] = []
    seen: set[str] = set()
    try:
        with httpx.Client(follow_redirects=True) as client:
            for page in range(1, max_pages + 1):
                html = _fetch(client, query, contract, page, location)
                cards = _parse_cards(html)
                if not cards:
                    # Diagnostic : logguer le début du HTML pour identifier si
                    # HelloWork retourne un 200 vide, un captcha, ou a changé sa structure.
                    snippet = html[:300].replace("\n", " ").strip()
                    log.warning(
                        "HelloWork search_live : 0 carte(s) trouvée(s) pour "
                        "query=%r location=%r contract=%r page=%d. "
                        "Début HTML : %s",
                        query, location, contract, page, snippet)
                    break
                for c in cards:
                    if c["url"] in seen:
                        continue
                    seen.add(c["url"])
                    out.append(c)
                    if len(out) >= limit:
                        return out
                time.sleep(1.0)
    except Exception as e:
        log.warning("HelloWork search_live échouée (%s / %s) : %s", query, location, e)
    return out


def _insert_jobs(conn, jobs: list[dict]) -> tuple[int, int]:
    """Shim de stockage (utilisé par les tests), via le helper commun."""
    return insert_normalized_jobs(conn, jobs, DEFAULT_SITE, STRATEGY)


# ── Point d'entrée ─────────────────────────────────────────────────────────

def run_hellowork_discovery(workers: int = 1, queries: list[str] | None = None) -> dict:
    """Découvre des offres de STAGE via le scraping de HelloWork.

    No-op gracieux si le site bloque (403) ou renvoie du contenu vide.
    """
    config.load_env()
    cfg = (config.load_search_config() or {}).get("hellowork", {}) or {}
    queries = queries or cfg.get("queries") or _DEFAULT_QUERIES
    contract = str(cfg.get("contract", "Stage"))
    max_pages = int(cfg.get("max_pages", 2))
    delay = float(cfg.get("delay_seconds", 2.0))
    fetch_details = bool(cfg.get("fetch_details", True))   # description complète via JSON-LD
    detail_delay = float(cfg.get("detail_delay", 1.0))
    max_details = int(cfg.get("max_details", 80))          # plafond de pages détail/run
    details_left = max_details

    conn = get_connection()
    init_db()

    grand_new = grand_existing = grand_found = 0
    errors: list[str] = []

    log.info("HelloWork : %d requête(s), max %d page(s)/requête (rythme lent)",
             len(queries), max_pages)
    with httpx.Client(follow_redirects=True) as client:
        # Backfill : complète les descriptions manquantes des offres HelloWork
        # déjà en base (via leur JSON-LD), pour qu'elles deviennent scorables.
        if fetch_details:
            rows = conn.execute(
                "SELECT url FROM jobs WHERE strategy = ? "
                "AND (full_description IS NULL OR full_description = '') "
                "LIMIT ?", (STRATEGY, max_details)).fetchall()
            back = 0
            for (u,) in rows:
                if details_left <= 0:
                    break
                detail = _fetch_detail(client, u)
                details_left -= 1
                if detail.get("full_description"):
                    conn.execute(
                        "UPDATE jobs SET full_description = ?, description = ?, "
                        "posted_at = COALESCE(?, posted_at) WHERE url = ?",
                        (detail["full_description"], detail["full_description"][:500],
                         detail.get("posted_at"), u))
                    conn.commit()
                    try:
                        transition_state(conn, u, "enriched",
                                         reason="hellowork jsonld", force=True)
                    except Exception:
                        pass
                    back += 1
                time.sleep(detail_delay)
            if back:
                log.info("HelloWork : %d description(s) complétée(s) (backfill)", back)

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
                # Enrichit chaque offre avec sa description complète (JSON-LD),
                # dans la limite du plafond, à rythme lent.
                if fetch_details:
                    for job in jobs:
                        if details_left <= 0:
                            break
                        detail = _fetch_detail(client, job["url"])
                        details_left -= 1
                        if detail.get("full_description"):
                            job["full_description"] = detail["full_description"]
                            job["description"] = detail["full_description"][:500]
                        if detail.get("employer_name"):
                            job["employer_name"] = detail["employer_name"]
                        if detail.get("location"):
                            job["location"] = detail["location"]
                        if detail.get("posted_at"):
                            job["posted_at"] = detail["posted_at"]
                        time.sleep(detail_delay)
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
