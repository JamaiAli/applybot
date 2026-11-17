"""Adaptateur de découverte Moovijob pour les stages au Luxembourg.

Scrape les offres de stages directement depuis Moovijob.com (N°1 de l'emploi
et des stages au Luxembourg), avec support des requêtes métier optionnelles.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from applypilot.database import get_connection, init_db
from applypilot.discovery.ats_common import insert_normalized_jobs

log = logging.getLogger(__name__)

STRATEGY = "moovijob_scrape"
DEFAULT_SITE = "Moovijob"
SOURCE = "moovijob"


def scrape_moovijob_offers(query: str | None = None, limit: int = 25) -> list[dict]:
    """Scrape les offres de stages sur Moovijob Luxembourg."""
    url = "https://www.moovijob.com/offres-emploi/jobs-luxembourg/contrat-stage"
    if query and query.strip():
        q_clean = query.strip()
        url += f"?q={q_clean}"

    log.info("Moovijob : scraping %s (limite=%d)", url, limit)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
            page.goto(url, timeout=20000, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            html = page.content()
            browser.close()
    except Exception as exc:
        log.warning("Moovijob scraping indisponible : %s", exc)
        return []

    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(".card-job-offer-new, .card-job-offer")
    log.info("Moovijob : %d cartes brutes trouvées", len(cards))

    now_iso = datetime.now(timezone.utc).isoformat()
    offers: list[dict] = []

    for c in cards:
        href = c.get("href")
        if not href:
            continue
        if not href.startswith("http"):
            href = "https://www.moovijob.com" + href

        title_el = c.select_one(".card-job-offer-new-title, p.m-0, h2, h3")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue

        # Entreprise
        company = ""
        img = c.select_one("img[alt]")
        if img and img.get("alt"):
            company = img["alt"].replace(" logo", "").replace(" Logo", "").strip()
        if not company:
            co_el = c.select_one(".company, .card-job-offer-new-company")
            if co_el:
                company = co_el.get_text(strip=True)
        if not company:
            company = "Entreprise au Luxembourg"

        # Localisation
        texts = [b.get_text(strip=True) for b in c.select("span, p, .badge") if b.get_text(strip=True)]
        location = "Luxembourg"
        for t in texts:
            if t in ["Stage", "Alternance / Apprentissage", "CDI", "CDD", "Voir l'offre", title, company]:
                continue
            if "il y a" in t or "sem." in t or "jour" in t or "h" in t:
                continue
            if len(t) < 35 and not any(ch.isdigit() for ch in t):
                location = f"{t}, Luxembourg" if "luxembourg" not in t.lower() else t
                break

        desc = f"Offre de stage {title} chez {company} ({location}) publiée sur Moovijob Luxembourg."

        offers.append({
            "url": href,
            "title": title,
            "employer_name": company,
            "location": location,
            "description": desc,
            "full_description": desc,
            "application_url": href,
            "posted_at": now_iso,
            "source": SOURCE,
            "domaines": ["Stages Luxembourg"],
        })
        if len(offers) >= limit:
            break

    return offers


def run_moovijob_discovery(queries: list[str] | dict | None = None, limit: int = 25) -> dict:
    """Lance la découverte des stages sur Moovijob."""
    q_str = None
    if isinstance(queries, str):
        q_str = queries
    elif isinstance(queries, list) and queries:
        q_str = queries[0]
    elif isinstance(queries, dict) and queries:
        first_val = next(iter(queries.values()))
        if isinstance(first_val, list) and first_val:
            q_str = first_val[0]

    offers = scrape_moovijob_offers(query=q_str, limit=limit)
    if not offers and q_str:
        offers = scrape_moovijob_offers(query=None, limit=limit)

    conn = get_connection()
    init_db()
    new, existing = insert_normalized_jobs(conn, offers, DEFAULT_SITE, STRATEGY)
    log.info("Moovijob terminé : %d retenu(s) (%d new, %d existing)", len(offers), new, existing)
    return {"found": len(offers), "new": new, "existing": existing, "offers": offers, "errors": []}


def scrape_ictjob_lu(query: str | None = None, limit: int = 10) -> list[dict]:
    """Scrape les stages IT / cybersécurité sur ICTjob.lu (spécialiste IT Luxembourg)."""
    import subprocess
    import urllib.parse
    from applypilot.discovery.cyber_filter import filter_cyber_internships

    kw = f"stage {query.strip()}" if (query and query.strip()) else "stage"
    url = f"https://www.ictjob.lu/fr/chercher-emplois-it?keywords={urllib.parse.quote(kw)}"
    try:
        proc = subprocess.run(
            ["curl.exe", "-s", "-L", "--max-time", "8", url],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        html = proc.stdout or ""
    except Exception as exc:
        log.warning("ICTjob.lu indisponible : %s", exc)
        return []

    soup = BeautifulSoup(html, "html.parser")
    now_iso = datetime.now(timezone.utc).isoformat()
    raw_offers: list[dict] = []

    for item in soup.select("li.search-item, .search-item"):
        a = item.select_one("a.job-title, h2 a")
        if not a:
            continue
        title = a.get_text(strip=True)
        href = a.get("href") or ""
        if not title or not href:
            continue
        if not href.startswith("http"):
            href = "https://www.ictjob.lu" + href

        co_el = item.select_one(".job-company, .company")
        company = co_el.get_text(strip=True) if co_el else "Entreprise IT Luxembourg"
        loc_el = item.select_one(".job-location, .location")
        loc = loc_el.get_text(strip=True) if loc_el else "Luxembourg"
        if "luxembourg" not in loc.lower():
            loc = f"{loc}, Luxembourg"

        raw_offers.append({
            "url": href,
            "title": title,
            "employer_name": company,
            "location": loc,
            "posted_at": now_iso,
            "source": "ictjob.lu",
            "contract": "Stage",
        })

    return filter_cyber_internships(raw_offers, custom_query=query, limit=limit)


def scrape_luxembourg_cyber_offers(query: str | None = None, limit: int = 12) -> list[dict]:
    """Récupère les stages en CYBERSÉCURITÉ au Luxembourg (ICTjob.lu + Moovijob + LinkedIn LU)."""
    from applypilot.discovery.cyber_filter import (
        filter_cyber_internships,
        norm_text,
        scrape_jobspy_cyber_internships,
    )

    all_raw: list[dict] = []

    # 1. ICTjob.lu (portail IT & Cyber Luxembourg, très rapide)
    try:
        all_raw.extend(scrape_ictjob_lu(query=query, limit=limit))
    except Exception as exc:
        log.warning("ICTjob.lu erreur : %s", exc)

    # 2. Moovijob (stages filtrés cyber)
    try:
        moovi_q = query if (query and query.strip()) else "cyber"
        moovi_raw = scrape_moovijob_offers(query=moovi_q, limit=limit)
        for o in moovi_raw:
            o.setdefault("contract", "Stage")
        all_raw.extend(moovi_raw)
    except Exception as exc:
        log.warning("Moovijob erreur : %s", exc)

    # 3. LinkedIn Luxembourg (sans Indeed, conformément à la demande utilisateur)
    q_clean = (query or "").strip()
    if q_clean and norm_text(q_clean) not in ("cyber", "cybersecurite", "cybersecurity", "stage"):
        jobspy_queries = [f"{q_clean} intern", f"stage {q_clean}"]
    else:
        jobspy_queries = ["cybersecurity intern", "stage cybersécurité"]

    try:
        all_raw.extend(
            scrape_jobspy_cyber_internships(
                country="Luxembourg",
                country_fr="Luxembourg",
                queries=jobspy_queries,
                sites=["linkedin"],
                limit=limit,
            )
        )
    except Exception as exc:
        log.warning("LinkedIn Luxembourg erreur : %s", exc)

    return filter_cyber_internships(all_raw, custom_query=query, limit=limit)

