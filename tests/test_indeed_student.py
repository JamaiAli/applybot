"""Tests de la recherche de jobs étudiants Indeed (filtre, tags, tri).

Aucun réseau : faux résultats Indeed en dur, on teste les fonctions pures.
"""

from __future__ import annotations

from applypilot.discovery.indeed_student import (
    departement,
    filter_and_tag,
    normalize_offer,
    sort_offers,
)

JOB_WE_ETU = {
    "id": "a",
    "title": "Équipier polyvalent (H/F)",
    "link": "https://fr.indeed.com/viewjob?jk=a",
    "apply_link": "https://fr.indeed.com/apply?jk=a",
    "company": {"name": "FastFood"},
    "location": {"display_name": "Bourges (18)", "postal_code": "18000"},
    "description": "<p>Travail le week-end (samedi, dimanche), temps partiel, "
                   "compatible avec vos etudes.</p>",
    "date_posted": "il y a 1 jour",
}
JOB_STAGE = {
    "id": "b",
    "title": "Stage marketing digital",
    "link": "https://fr.indeed.com/viewjob?jk=b",
    "company": {"name": "Agence"},
    "location": {"display_name": "Bourges (18)", "postal_code": "18000"},
    "description": "<p>Stage de 6 mois.</p>",
}
JOB_FULLTIME = {
    "id": "c",
    "title": "Vendeur",
    "link": "https://fr.indeed.com/viewjob?jk=c",
    "company": {"name": "Magasin"},
    "location": {"display_name": "Bourges (18)", "postal_code": "18000"},
    "description": "<p>CDI temps plein 35h, du lundi au vendredi.</p>",
}
JOB_WEEK_NO = {  # temps partiel mais PAS week-end
    "id": "d",
    "title": "Serveur",
    "link": "https://fr.indeed.com/viewjob?jk=d",
    "company": {"name": "Brasserie"},
    "location": {"display_name": "Bourges (18)", "postal_code": "18000"},
    "description": "<p>Service en semaine, temps partiel.</p>",
}
JOB_PARIS = {  # hors Cher
    "id": "e",
    "title": "Livreur",
    "link": "https://fr.indeed.com/viewjob?jk=e",
    "company": {"name": "Coursier"},
    "location": {"display_name": "Paris (75)", "postal_code": "75001"},
    "description": "<p>Job étudiant, week-end.</p>",
}


def _records(*jobs):
    return [{"job": j, "categories": ["Général"]} for j in jobs]


def test_departement_from_postal_and_display():
    assert departement(JOB_WE_ETU) == "18"
    assert departement({"location": {"display_name": "Lyon (69)"}}) == "69"
    assert departement({"location": "texte libre"}) == ""


def test_filter_excludes_stage_and_fulltime():
    kept = filter_and_tag(_records(JOB_WE_ETU, JOB_STAGE, JOB_FULLTIME))
    ids = {r["job"]["id"] for r in kept}
    assert ids == {"a"}  # stage et temps-plein-sec écartés
    rec = kept[0]
    assert rec["week_end"] and rec["etudiant"] and rec["temps_partiel"]
    assert rec["pertinence"] == 3 * 1 + 2 * 1 + 2 * 1  # we + etu + partiel


def test_weekend_only():
    kept = filter_and_tag(_records(JOB_WE_ETU, JOB_WEEK_NO), weekend_only=True)
    assert {r["job"]["id"] for r in kept} == {"a"}


def test_cher_only():
    kept = filter_and_tag(_records(JOB_WE_ETU, JOB_PARIS), cher_only=True)
    assert {r["job"]["id"] for r in kept} == {"a"}  # Paris (75) écarté


def test_normalize_offer_shape():
    rec = filter_and_tag(_records(JOB_WE_ETU))[0]
    off = normalize_offer(rec)
    for key in ("url", "title", "employer_name", "location", "posted_at", "source"):
        assert key in off
    assert off["url"] == JOB_WE_ETU["link"]
    assert off["employer_name"] == "FastFood"
    assert off["location"] == "Bourges (18)"
    assert off["source"] == "indeed"
    assert off["week_end"] is True
    assert off["posted_at"] is not None  # "il y a 1 jour" parsé


def test_sort_by_date_then_pertinence():
    old_relevant = {"posted_at": "2026-10-01T00:00", "pertinence": 7}
    recent_meh = {"posted_at": "2026-11-01T00:00", "pertinence": 1}
    by_date = sort_offers([old_relevant, recent_meh], "date")
    assert by_date[0] is recent_meh  # plus récent d'abord
    by_rel = sort_offers([recent_meh, old_relevant], "pertinence")
    assert by_rel[0] is old_relevant  # pertinence la plus haute d'abord


def test_merge_bourges_and_saint_doulchard_jobs():
    from applypilot.telegram_bot import _merge_student_jobs

    bourges_jobs = [
        {"url": "u1", "title": "Equipier Polyvalent", "employer_name": "McDo", "location": "Bourges", "posted_at": "2026-11-08T08:00", "anciennete_jours": 0},
        {"url": "u2", "title": "Hote de caisse", "employer_name": "Carrefour", "location": "Bourges", "posted_at": "2026-11-07T10:00", "anciennete_jours": 1},
    ]
    saint_doulchard_jobs = [
        # Doublon de u1 (même titre et employeur)
        {"url": "u1_dup", "title": "equipier polyvalent", "employer_name": "mcdo", "location": "bourges", "posted_at": "2026-11-08T08:00", "anciennete_jours": 0},
        # Offre spécifique Saint-Doulchard
        {"url": "u3", "title": "Agent de residence", "employer_name": "Korian", "location": "Saint-Doulchard", "posted_at": "2026-11-08T09:00", "anciennete_jours": 0},
    ]

    merged = _merge_student_jobs(bourges_jobs, saint_doulchard_jobs)
    sorted_jobs = sort_offers(merged, sort="date", max_age_days=7)

    assert len(sorted_jobs) == 3
    urls = [j["url"] for j in sorted_jobs]
    assert "u1" in urls
    assert "u3" in urls
    assert "u2" in urls
    # Les offres d'aujourd'hui (u1 et u3, age 0) doivent devancer hier (u2, age 1)
    assert sorted_jobs[-1]["url"] == "u2"

