"""Module de découverte des stages en Suède (Platsbanken - Arbetsförmedlingen).

Interroge l'API officielle JobTech Dev de l'Agence suédoise pour l'emploi (Arbetsförmedlingen) :
- N°1 national de l'emploi et des stages en Suède (Platsbanken).
- Offres de stage (« internship » et « praktik ») en Suède.
"""

from __future__ import annotations

import logging
from datetime import datetime
import httpx

log = logging.getLogger(__name__)

JOBTECH_SEARCH_URL = "https://jobsearch.api.jobtechdev.se/search"


def scrape_platsbanken_offers(query: str | None = None, limit: int = 20) -> list[dict]:
    """Récupère les offres de stage en Suède depuis l'API officielle JobTech Dev / Platsbanken.

    Args:
        query: Mot-clé optionnel (ex: 'cyber', 'informatique', 'python').
        limit: Nombre max d'offres à retourner (défaut : 20).
    """
    clean_q = (query or "").strip()
    for w in ["stage", "stages", "internship", "internships", "praktik"]:
        clean_q = clean_q.replace(w, "").strip()

    search_terms = (
        [f"internship {clean_q}".strip(), f"praktik {clean_q}".strip()]
        if clean_q
        else ["internship", "praktik"]
    )

    offers: list[dict] = []
    seen_ids: set[str] = set()

    headers = {"accept": "application/json"}

    for term in search_terms:
        params = {
            "q": term,
            "limit": limit,
            "sort": "pubdate-desc",
        }
        try:
            r = httpx.get(JOBTECH_SEARCH_URL, params=params, headers=headers, timeout=12)
            if r.status_code != 200:
                log.warning("Platsbanken API code HTTP inattendu : %d", r.status_code)
                continue
            data = r.json()
        except Exception as exc:
            log.warning("Platsbanken API indisponible pour le terme '%s' : %s", term, exc)
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

            offers.append({
                "title": title,
                "employer_name": employer_name,
                "location": location,
                "url": url,
                "posted_at": posted_at,
                "source": "platsbanken",
            })
            if len(offers) >= limit:
                break

        if len(offers) >= limit:
            break

    return offers


# Alias générique pour cohérence avec switzerland.py et moovijob.py
scrape_sweden_offers = scrape_platsbanken_offers
