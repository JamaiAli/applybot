"""Module de découverte des stages en cybersécurité en Suède.

Combine en direct :
1. Platsbanken (API officielle JobTech Dev — Arbetsförmedlingen, N°1 en Suède).
2. LinkedIn Jobs Suède & Indeed Suède (portails #8 et #9 de la liste Suède).

Toutes les offres sont filtrées par `cyber_filter` pour ne retourner que de vrais
stages / traineeships / mémoires en cybersécurité (comme `/offres`).
"""

from __future__ import annotations

import logging
from datetime import datetime
import httpx

from applypilot.discovery.cyber_filter import (
    filter_cyber_internships,
    norm_text,
    scrape_jobspy_cyber_internships,
)

log = logging.getLogger(__name__)

JOBTECH_SEARCH_URL = "https://jobsearch.api.jobtechdev.se/search"


def scrape_platsbanken_offers(query: str | None = None, limit: int = 12) -> list[dict]:
    """Récupère les stages cybersécurité depuis l'API officielle JobTech Dev / Platsbanken."""
    clean_q = (query or "").strip()
    for w in ["stage", "stages", "internship", "internships", "praktik"]:
        clean_q = clean_q.replace(w, "").strip()

    if clean_q and norm_text(clean_q) not in ("cyber", "cybersecurite", "cybersecurity"):
        search_terms = [f"internship {clean_q}", f"praktik {clean_q}", f"thesis {clean_q}"]
    else:
        search_terms = [
            "cybersecurity internship",
            "security internship",
            "praktik säkerhet",
            "trainee cybersecurity",
            "exjobb cybersecurity",
        ]

    raw_offers: list[dict] = []
    seen_ids: set[str] = set()
    headers = {"accept": "application/json"}

    for term in search_terms:
        params = {
            "q": term,
            "limit": max(limit, 15),
            "sort": "pubdate-desc",
        }
        try:
            r = httpx.get(JOBTECH_SEARCH_URL, params=params, headers=headers, timeout=10)
            if r.status_code != 200:
                log.warning("Platsbanken API code HTTP inattendu : %d", r.status_code)
                continue
            data = r.json()
        except Exception as exc:
            log.warning("Platsbanken API indisponible pour '%s' : %s", term, exc)
            continue

        for hit in data.get("hits", []):
            hit_id = str(hit.get("id") or "")
            if not hit_id or hit_id in seen_ids:
                continue
            seen_ids.add(hit_id)

            title = (hit.get("headline") or "").strip()
            if not title:
                continue

            employer_dict = hit.get("employer") or {}
            employer_name = (employer_dict.get("name") or "Entreprise en Suède").strip()

            address = hit.get("workplace_address") or {}
            muni = (address.get("municipality") or address.get("city") or "Suède").strip()
            if "suède" in muni.lower() or "sweden" in muni.lower():
                location = muni
            else:
                location = f"{muni}, Suède"

            url = hit.get("webpage_url") or f"https://arbetsformedlingen.se/platsbanken/annonser/{hit_id}"
            posted_at = hit.get("publication_date") or datetime.now().isoformat()
            desc = ((hit.get("description") or {}).get("text") or "").strip()

            raw_offers.append({
                "title": title,
                "employer_name": employer_name,
                "location": location,
                "url": url,
                "posted_at": posted_at,
                "source": "platsbanken",
                "description": desc,
            })

    return filter_cyber_internships(raw_offers, custom_query=query, limit=limit)


def scrape_sweden_offers(query: str | None = None, limit: int = 12) -> list[dict]:
    """Combine Platsbanken + LinkedIn Jobs Suède + Indeed Suède, filtré strictement cybersécurité."""
    all_raw: list[dict] = []

    # 1. Platsbanken (Arbetsförmedlingen)
    try:
        all_raw.extend(scrape_platsbanken_offers(query=query, limit=limit))
    except Exception as exc:
        log.warning("Platsbanken erreur : %s", exc)

    # 2. LinkedIn Jobs Suède & Indeed Suède (liste utilisateur #8 et #9)
    q_clean = (query or "").strip()
    if q_clean and norm_text(q_clean) not in ("cyber", "cybersecurite", "cybersecurity", "stage"):
        jobspy_queries = [f"{q_clean} intern", f"{q_clean} thesis"]
    else:
        jobspy_queries = ["cybersecurity intern", "information security intern"]

    try:
        all_raw.extend(
            scrape_jobspy_cyber_internships(
                country="Sweden",
                country_fr="Suède",
                queries=jobspy_queries,
                sites=["linkedin", "indeed"],
                limit=limit,
            )
        )
    except Exception as exc:
        log.warning("JobSpy Suède erreur : %s", exc)

    return filter_cyber_internships(all_raw, custom_query=query, limit=limit)
