"""Module de découverte des stages en Suisse (Jobup.ch & SwissDevJobs).

Scrape en direct :
1. Jobup.ch (N°1 de l'emploi et des stages en Suisse romande).
2. SwissDevJobs (spécialisé IT, Tech & Cybersécurité en Suisse).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from bs4 import BeautifulSoup
import httpx
from playwright.sync_api import sync_playwright

log = logging.getLogger(__name__)


def scrape_swissdevjobs(query: str | None = None, limit: int = 10) -> list[dict]:
    """Récupère les stages et postes junior tech depuis l'API publique SwissDevJobs."""
    try:
        r = httpx.get("https://swissdevjobs.ch/api/jobsLight", timeout=12)
        if r.status_code != 200:
            return []
        jobs = r.json()
    except Exception as exc:
        log.warning("SwissDevJobs API indisponible : %s", exc)
        return []

    q_low = query.lower().strip() if query else None
    offers = []
    for j in jobs:
        name = (j.get("name") or "").strip()
        name_low = name.lower()
        seniority = (j.get("seniority") or "").lower()
        jobType = (j.get("jobType") or "").lower()
        technologies = [t.lower() for t in (j.get("technologies") or [])]

        is_intern = any(k in name_low or k in seniority or k in jobType for k in ["intern", "stage", "trainee", "student", "junior"])
        if not is_intern:
            continue

        if q_low:
            matches_q = q_low in name_low or any(q_low in t for t in technologies)
            if not matches_q:
                continue

        job_slug = j.get("jobUrl") or ""
        url = f"https://swissdevjobs.ch/jobs/{job_slug}" if job_slug else "https://swissdevjobs.ch"
        company = j.get("company") or "Entreprise Tech Suisse"
        city = j.get("cityCategory") or "Suisse"
        location = f"{city}, Suisse" if "suisse" not in city.lower() else city

        offers.append({
            "title": name,
            "employer_name": company,
            "location": location,
            "url": url,
            "posted_at": datetime.now().isoformat(),
            "source": "swissdevjobs",
        })
        if len(offers) >= limit:
            break

    return offers


def scrape_jobup(query: str | None = None, limit: int = 15) -> list[dict]:
    """Scrape les offres de stage en direct sur Jobup.ch."""
    url = "https://www.jobup.ch/fr/emplois/?term=stage"
    if query and query.strip():
        q_clean = query.strip()
        if "stage" not in q_clean.lower():
            url = f"https://www.jobup.ch/fr/emplois/?term=stage+{q_clean}"
        else:
            url = f"https://www.jobup.ch/fr/emplois/?term={q_clean}"

    log.info("Jobup.ch : scraping %s (limite=%d)", url, limit)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                locale="fr-CH"
            )
            page.goto(url, timeout=20000, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            html = page.content()
            browser.close()
    except Exception as exc:
        log.warning("Jobup.ch scraping indisponible : %s", exc)
        return []

    soup = BeautifulSoup(html, "html.parser")
    links = soup.find_all("a", href=lambda h: h and "/emplois/detail/" in h)

    offers = []
    seen = set()
    for a in links:
        href = a.get("href", "")
        if not href or href in seen:
            continue
        if not href.startswith("http"):
            href = "https://www.jobup.ch" + href
        seen.add(href)

        parts = [p.strip() for p in a.get_text(separator=" /// ", strip=True).split(" /// ") if p.strip()]

        # Location
        location = "Suisse"
        if "Lieu de travail" in parts:
            idx = parts.index("Lieu de travail")
            for item in parts[idx + 1:idx + 4]:
                if item != ":":
                    location = f"{item}, Suisse"
                    break

        # Title
        title = parts[1] if len(parts) > 1 else "Stage en Suisse"
        if any(w in title.lower() for w in ["hier", "jours", "semaine", "aujourd", "mois"]):
            title = parts[2] if len(parts) > 2 else title
        title = re.sub(r"^[^\w\s]+", "", title).strip()

        # Company
        company = "Entreprise en Suisse"
        candidates = [
            p for p in parts
            if p not in (title, location.replace(", Suisse", ""), ":", "Stage", "Temporaire", "Fixe", "100%", "Lieu de travail", "Type de contrat", "Taux d'activité", "Candidature simplifiée", "Mis en avant", "Offre pertinente ?")
            and not any(w in p.lower() for w in ["jours", "semaine", "hier", "chf", "taux", "%"])
        ]
        if candidates:
            company = candidates[-1]

        offers.append({
            "title": title or "Stage en Suisse",
            "employer_name": company,
            "location": location,
            "url": href,
            "posted_at": datetime.now().isoformat(),
            "source": "jobup",
        })
        if len(offers) >= limit:
            break

    return offers


def scrape_swiss_offers(query: str | None = None, limit: int = 20) -> list[dict]:
    """Combine les offres fraîches de Jobup et SwissDevJobs."""
    jobup_list = scrape_jobup(query=query, limit=limit)
    dev_list = scrape_swissdevjobs(query=query, limit=max(5, limit // 2))

    combined = []
    seen_urls = set()
    # Interleave or merge
    for o in (jobup_list + dev_list):
        if o["url"] not in seen_urls:
            seen_urls.add(o["url"])
            combined.append(o)
        if len(combined) >= limit:
            break
    return combined
