"""Module de découverte des stages en cybersécurité en Suisse.

Combine en direct (sans Playwright bloquant) :
1. Jobs.ch & Jobup.ch (API publique officielle avec filtre contrat Stage `employment-type-ids[]=3`).
2. SwissDevJobs (API JSON tech & cybersécurité en Suisse).
3. LinkedIn & Indeed Suisse (stages cybersécurité temps réel : NCSC, CHANEL, SwissBorg, Lombard Odier, EY, Siemens, Hilti…).

Toutes les offres passent par le filtre strict cybersécurité + stage (`cyber_filter`),
comme la commande `/offres` en France.
"""

from __future__ import annotations

import json
import logging
import subprocess
import urllib.parse
from datetime import datetime
import httpx

from applypilot.discovery.cyber_filter import (
    filter_cyber_internships,
    is_cyber_offer,
    is_stage_offer,
    norm_text,
    scrape_jobspy_cyber_internships,
)

log = logging.getLogger(__name__)

SWISSDEVJOBS_API = "https://swissdevjobs.ch/api/jobsLight"


def _curl_json(url: str, timeout: int = 8) -> dict | list | None:
    """Télécharge un JSON rapidement via curl.exe avec fallback httpx."""
    try:
        proc = subprocess.run(
            ["curl.exe", "-s", "--max-time", str(timeout), url],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        out = (proc.stdout or "").strip()
        if proc.returncode == 0 and out and out[0] in ("{", "["):
            return json.loads(out)
    except Exception as exc:
        log.debug("curl.exe échec pour %s : %s", url, exc)

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json",
        }
        r = httpx.get(url, headers=headers, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception as exc:
        log.warning("HTTPX échec pour %s : %s", url, exc)
    return None


def _fetch_swissdevjobs_json() -> list[dict]:
    """Télécharge la liste des offres depuis SwissDevJobs."""
    data = _curl_json(SWISSDEVJOBS_API, timeout=8)
    return data if isinstance(data, list) else []


def scrape_swissdevjobs(query: str | None = None, limit: int = 10) -> list[dict]:
    """Récupère les stages cybersécurité depuis l'API SwissDevJobs."""
    jobs = _fetch_swissdevjobs_json()
    if not jobs:
        return []

    raw_offers = []
    for j in jobs:
        name = (j.get("name") or "").strip()
        seniority = (j.get("seniority") or "").strip()
        job_type = (j.get("jobType") or "").strip()
        techs = " ".join(j.get("technologies") or [])

        if not is_stage_offer(name, f"{seniority} {job_type}"):
            continue

        job_slug = j.get("jobUrl") or ""
        url = f"https://swissdevjobs.ch/jobs/{job_slug}" if job_slug else "https://swissdevjobs.ch"
        company = j.get("company") or "Entreprise Tech Suisse"
        city = j.get("cityCategory") or "Suisse"
        location = f"{city}, Suisse" if "suisse" not in city.lower() else city

        raw_offers.append({
            "title": name,
            "employer_name": company,
            "location": location,
            "url": url,
            "posted_at": datetime.now().isoformat(),
            "source": "swissdevjobs",
            "contract": job_type or seniority,
            "description": techs,
        })

    return filter_cyber_internships(raw_offers, custom_query=query, limit=limit)


def scrape_jobcloud_stages(query: str | None = None, limit: int = 10) -> list[dict]:
    """Interroge les API publiques Jobs.ch et Jobup.ch avec filtre Stage (`employment-type-ids[]=3`)."""
    search_terms = [query.strip()] if (query and query.strip()) else ["cyber", "cybersecurity", "security"]
    raw_offers: list[dict] = []

    for base_url, source_name, detail_key, url_prefix in [
        ("https://www.jobs.ch", "jobs.ch", "detail_fr", "https://www.jobs.ch/fr/offres-emplois/detail/"),
        ("https://www.jobup.ch", "jobup", "detail_fr", "https://www.jobup.ch/fr/emplois/detail/"),
    ]:
        for term in search_terms:
            api_url = (
                f"{base_url}/api/v1/public/search"
                f"?employment-type-ids%5B%5D=3&query={urllib.parse.quote(term)}&rows=15"
            )
            data = _curl_json(api_url, timeout=6)
            if not isinstance(data, dict):
                continue

            for doc in data.get("documents") or []:
                job_id = doc.get("job_id")
                title = (doc.get("title") or "").strip()
                if not job_id or not title:
                    continue

                links = doc.get("_links") or {}
                detail_url = (
                    (links.get(detail_key) or {}).get("href")
                    or (links.get("detail_en") or {}).get("href")
                    or (links.get("detail_de") or {}).get("href")
                    or f"{url_prefix}{job_id}/"
                )
                company = (doc.get("company_name") or "Entreprise en Suisse").strip() or "Entreprise en Suisse"
                place = (doc.get("place") or "Suisse").strip()
                location = f"{place}, Suisse" if "suisse" not in place.lower() and "switzerland" not in place.lower() else place
                pub_date = doc.get("publication_date") or datetime.now().isoformat()
                preview = (doc.get("preview") or "").strip()

                raw_offers.append({
                    "title": title,
                    "employer_name": company,
                    "location": location,
                    "url": detail_url,
                    "posted_at": pub_date,
                    "source": source_name,
                    "contract": "Stage",
                    "description": preview,
                })

    return filter_cyber_internships(raw_offers, custom_query=query, limit=limit)


def scrape_swiss_offers(query: str | None = None, limit: int = 12) -> list[dict]:
    """Récupère les offres de stage en CYBERSÉCURITÉ en Suisse (Jobs.ch, Jobup, SwissDevJobs, LinkedIn & Indeed CH)."""
    all_raw: list[dict] = []

    # 1. API Jobs.ch & Jobup.ch (filtre Stage officiel)
    try:
        all_raw.extend(scrape_jobcloud_stages(query=query, limit=limit))
    except Exception as exc:
        log.warning("Jobs.ch / Jobup API erreur : %s", exc)

    # 2. API SwissDevJobs (tech & cyber Suisse)
    try:
        all_raw.extend(scrape_swissdevjobs(query=query, limit=limit))
    except Exception as exc:
        log.warning("SwissDevJobs erreur : %s", exc)

    # 3. Flux temps réel cybersécurité Suisse (LinkedIn + Indeed CH)
    q_clean = (query or "").strip()
    if q_clean and norm_text(q_clean) not in ("cyber", "cybersecurite", "cybersecurity", "stage"):
        jobspy_queries = [f"{q_clean} intern", f"stage {q_clean}"]
    else:
        jobspy_queries = ["cybersecurity intern", "stage cybersécurité"]

    try:
        all_raw.extend(
            scrape_jobspy_cyber_internships(
                country="Switzerland",
                country_fr="Suisse",
                queries=jobspy_queries,
                sites=["linkedin", "indeed"],
                limit=limit,
            )
        )
    except Exception as exc:
        log.warning("JobSpy Suisse erreur : %s", exc)

    return filter_cyber_internships(all_raw, custom_query=query, limit=limit)
