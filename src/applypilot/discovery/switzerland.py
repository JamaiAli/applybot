"""Module de découverte des stages en Suisse (Jobup.ch & SwissDevJobs).

Scrape en direct :
1. Jobup.ch (N°1 de l'emploi et des stages en Suisse romande).
2. SwissDevJobs (spécialisé IT, Tech & Cybersécurité en Suisse).
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from datetime import datetime
from bs4 import BeautifulSoup
import httpx
from playwright.sync_api import sync_playwright

log = logging.getLogger(__name__)

SWISSDEVJOBS_API = "https://swissdevjobs.ch/api/jobsLight"


def _fetch_swissdevjobs_json() -> list[dict]:
    """Télécharge la liste des offres depuis SwissDevJobs (curl.exe ultra-rapide ou httpx)."""
    # 1. Tentative rapide via curl.exe (évite tout hang Cloudflare sur Windows)
    try:
        proc = subprocess.run(
            ["curl.exe", "-s", "--max-time", "8", SWISSDEVJOBS_API],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if proc.returncode == 0 and proc.stdout.strip().startswith("["):
            data = json.loads(proc.stdout)
            if isinstance(data, list):
                return data
    except Exception as exc:
        log.debug("curl.exe pour SwissDevJobs a échoué : %s", exc)

    # 2. Fallback httpx avec headers navigateur
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json",
        }
        r = httpx.get(SWISSDEVJOBS_API, headers=headers, timeout=8)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                return data
    except Exception as exc:
        log.warning("SwissDevJobs API indisponible : %s", exc)

    return []


def scrape_swissdevjobs(query: str | None = None, limit: int = 10) -> list[dict]:
    """Récupère les stages et programmes trainee tech depuis l'API SwissDevJobs."""
    jobs = _fetch_swissdevjobs_json()
    if not jobs:
        return []

    q_low = query.lower().strip() if query else None
    offers = []
    for j in jobs:
        name = (j.get("name") or "").strip()
        name_low = name.lower()
        seniority = (j.get("seniority") or "").lower()
        job_type = (j.get("jobType") or "").lower()
        technologies = [t.lower() for t in (j.get("technologies") or [])]

        # Filtre STRICT de stage : exclusif aux stages / trainees / étudiants (PAS les CDI juniors !)
        is_intern = any(
            k in name_low or k in seniority or k in job_type
            for k in ["intern", "stage", "stagiaire", "trainee", "praktik", "student", "working student"]
        )
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
                locale="fr-CH",
            )
            page.goto(url, timeout=25000, wait_until="domcontentloaded")
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
    q_low = query.lower().strip() if query else None

    for a in links:
        href = a.get("href", "")
        if not href or href in seen:
            continue
        if not href.startswith("http"):
            href = "https://www.jobup.ch" + href
        seen.add(href)

        parts = [p.strip() for p in a.get_text(separator=" /// ", strip=True).split(" /// ") if p.strip()]

        # Extraction du type de contrat
        contract = ""
        if "Type de contrat" in parts:
            c_idx = parts.index("Type de contrat")
            if c_idx + 1 < len(parts) and parts[c_idx + 1] != ":":
                contract = parts[c_idx + 1]
            elif c_idx + 2 < len(parts):
                contract = parts[c_idx + 2]

        # Extraction du lieu
        location = "Suisse"
        if "Lieu de travail" in parts:
            idx = parts.index("Lieu de travail")
            for item in parts[idx + 1:idx + 4]:
                if item != ":":
                    location = f"{item}, Suisse"
                    break

        # Extraction du titre
        title = ""
        for p in parts[1:4]:
            if p not in ("Lieu de travail", ":", location.replace(", Suisse", "")) and not any(
                w in p.lower() for w in ["taux", "type de contrat", "salaire", "candidature simplifiée", "nouveau"]
            ):
                title = p
                break
        title = re.sub(r"^[^\w\s]+", "", title).strip() or "Offre de stage"

        title_low = title.lower()
        contract_low = contract.lower()

        # Filtrage strict STAGE : doit explicitement être un stage / stagiaire / intern
        is_stage = any(
            k in title_low for k in ["stage", "stagiaire", "intern", "trainee", "praktik"]
        ) or any(
            k in contract_low for k in ["stage", "intern", "trainee", "praktik"]
        )

        # Exclure les apprentissages scolaires / CFC et les postes fixes/intérim ordinaires
        if any(k in title_low for k in ["apprenti", "apprentissage", "cfc"]):
            is_stage = False
        if contract_low in ["durée indéterminée", "temporaire", "fixe"] and not any(
            k in title_low for k in ["stage", "stagiaire", "intern", "trainee", "praktik"]
        ):
            is_stage = False

        if not is_stage:
            continue

        if q_low and q_low not in title_low:
            continue

        # Extraction de l'entreprise
        company = "Entreprise en Suisse"
        contract_noise = {
            "stage", "temporaire", "fixe", "100%", "durée indéterminée", "cdd", "cdi",
            "apprentissage", "taux d'activité", "type de contrat", "lieu de travail",
            "candidature simplifiée", "mis en avant", "offre pertinente ?", "nouveau", "salaire"
        }
        candidates = [
            p for p in parts
            if p not in (title, location.replace(", Suisse", ""), ":")
            and p.lower() not in contract_noise
            and not any(w in p.lower() for w in ["jours", "semaine", "hier", "chf", "taux", "%", "heure", "mois", "trimestre", "an"])
        ]
        if candidates:
            company = candidates[-1]

        offers.append({
            "title": title,
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
    for o in (jobup_list + dev_list):
        if o["url"] not in seen_urls:
            seen_urls.add(o["url"])
            combined.append(o)
        if len(combined) >= limit:
            break
    return combined
