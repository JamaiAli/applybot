"""Tests de l'adaptateur Indeed : normalisation, filtrage/tagging, déduplication.

Aucun réseau : on part d'un FAUX résultat Indeed (dicts en dur au format du
scraper omkarcloud) et on teste les fonctions pures, puis la dédup intra-source
(clé primaire URL) et inter-sources (titre + entreprise + ville normalisés).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from applypilot.bot_core import fetch_pending_offers
from applypilot.discovery.ats_common import insert_normalized_jobs
from applypilot.discovery.dates import age_days, job_date, to_dt
from applypilot.discovery.indeed import filter_and_tag, normalize_offer


# ── Faux résultat Indeed (format du scraper) ────────────────────────────────
JOB_STAGE_CYBER = {
    "id": "aaa",
    "title": "Stage Pentester Cybersécurité (H/F)",
    "link": "https://fr.indeed.com/viewjob?jk=aaa",
    "apply_link": "https://fr.indeed.com/apply?jk=aaa",
    "company": {"name": "Acme Sécurité"},
    "location": {"display_name": "Paris (75)"},
    "description": "<p>Stage de 6 mois en <b>test d'intrusion</b>, SIEM et red team.</p>",
    "date_posted": "2026-11-01",
}
JOB_NON_STAGE = {
    "id": "bbb",
    "title": "Ingénieur Cybersécurité Confirmé",
    "link": "https://fr.indeed.com/viewjob?jk=bbb",
    "company": {"name": "Acme"},
    "location": {"display_name": "Lyon"},
    "description": "<p>Poste CDI en sécurité opérationnelle.</p>",
}
JOB_HORS_CYBER = {
    "id": "ccc",
    "title": "Stage Développeur Web",
    "link": "https://fr.indeed.com/viewjob?jk=ccc",
    "company": {"name": "WebCorp"},
    "location": {"display_name": "Nantes"},
    "description": "<p>Stage React, HTML, CSS, zéro sécurité.</p>",
}
JOB_ALTERNANCE = {
    "id": "ddd",
    "title": "Alternance Analyste SOC",
    "link": "https://fr.indeed.com/viewjob?jk=ddd",
    "company": {"name": "BlueTeam Inc"},
    "location": {"display_name": "Rennes"},
    "description": "<p>Alternance en SOC / SIEM.</p>",
}


def _records():
    return [
        {"job": JOB_STAGE_CYBER, "domaines_req": ["Pentest / Offensive"]},
        {"job": JOB_NON_STAGE, "domaines_req": ["Généraliste cyber"]},
        {"job": JOB_HORS_CYBER, "domaines_req": ["Généraliste cyber"]},
        {"job": JOB_ALTERNANCE, "domaines_req": ["SOC / Blue team"]},
    ]


# ── Filtrage + tagging ──────────────────────────────────────────────────────
def test_filter_keeps_only_cyber_stages():
    kept = filter_and_tag(_records(), keep_alt=False)
    ids = {r["job"]["id"] for r in kept}
    assert ids == {"aaa"}  # non-stage, hors-cyber et alternance écartés


def test_filter_includes_alternance_when_opted_in():
    kept = filter_and_tag(_records(), keep_alt=True)
    ids = {r["job"]["id"] for r in kept}
    assert ids == {"aaa", "ddd"}


def test_principal_domain_from_title():
    kept = filter_and_tag(_records(), keep_alt=False)
    rec = next(r for r in kept if r["job"]["id"] == "aaa")
    assert rec["domaine_principal"] == "Pentest / Offensive"
    # le texte mentionne aussi SIEM → SOC détecté dans les domaines
    assert "SOC / Blue team" in rec["domaines"]


# ── Normalisation (format HelloWork + extras) ───────────────────────────────
def test_normalize_offer_matches_hellowork_shape():
    rec = filter_and_tag(_records(), keep_alt=False)[0]
    off = normalize_offer(rec)
    # mêmes clés que le format HelloWork
    for key in ("url", "title", "location", "description", "full_description",
                "application_url", "employer_name", "posted_at"):
        assert key in off
    assert off["url"] == JOB_STAGE_CYBER["link"]
    assert off["application_url"] == JOB_STAGE_CYBER["apply_link"]
    assert off["employer_name"] == "Acme Sécurité"
    assert off["location"] == "Paris (75)"
    assert off["posted_at"].startswith("2026-11-01")
    # HTML nettoyé, pas de balises
    assert "<" not in (off["full_description"] or "")
    assert "intrusion" in off["full_description"].lower()
    # extras
    assert off["source"] == "indeed"
    assert off["domaines"][0] == "Pentest / Offensive"


# ── Déduplication intra-source (clé primaire URL) ───────────────────────────
def test_insert_dedups_by_url_within_source(tmp_db):
    conn = tmp_db()
    off = normalize_offer(filter_and_tag(_records(), keep_alt=False)[0])
    new, existing = insert_normalized_jobs(conn, [off], "Indeed", "indeed_scrape")
    assert (new, existing) == (1, 0)
    # ré-insertion de la même URL → ignorée (IntegrityError)
    new2, existing2 = insert_normalized_jobs(conn, [off], "Indeed", "indeed_scrape")
    assert (new2, existing2) == (0, 1)
    # la colonne source/domaines a bien été remplie
    row = conn.execute("SELECT source, domaines FROM jobs WHERE url = ?",
                       (off["url"],)).fetchone()
    assert row[0] == "indeed"
    assert "Pentest / Offensive" in row[1]


# ── Déduplication inter-sources (titre + entreprise + ville) ────────────────
def test_cross_source_dedup_in_fetch_pending(tmp_db, seed_job):
    conn = tmp_db()
    # Même offre, deux sources : titre/entreprise/ville équivalents (accents/casse).
    seed_job(conn, url_suffix="hw1", strategy="hellowork_scrape", source="hellowork",
             title="Stage SOC Analyst", company="Acme Sécurité", location="Paris",
             fit_score=9)
    seed_job(conn, url_suffix="in1", strategy="indeed_scrape", source="indeed",
             title="stage soc analyst", company="ACME Securite", location="paris",
             fit_score=8)
    offers = fetch_pending_offers(min_score=8, limit=10)
    assert len(offers) == 1  # un seul exemplaire après dédup inter-sources
    assert offers[0]["source"] == "indeed"  # Indeed prioritaire gagne le doublon


def test_indeed_ranked_before_hellowork(tmp_db, seed_job):
    conn = tmp_db()
    # HelloWork plus récente ET mieux scorée, Indeed plus ancienne : Indeed passe devant.
    seed_job(conn, url_suffix="hwR", strategy="hellowork_scrape", source="hellowork",
             title="Stage HW récent", company="A", location="Paris", fit_score=9,
             posted_at=_iso_days_ago(0))
    seed_job(conn, url_suffix="inO", strategy="indeed_scrape", source="indeed",
             title="Stage Indeed ancien", company="B", location="Lyon", fit_score=8,
             posted_at=_iso_days_ago(10))
    offers = fetch_pending_offers(min_score=8, limit=10, max_age_days=0)
    assert [o["source"] for o in offers] == ["indeed", "hellowork"]


def test_fetch_pending_includes_both_sources(tmp_db, seed_job):
    conn = tmp_db()
    seed_job(conn, url_suffix="hw2", strategy="hellowork_scrape", source="hellowork",
             title="Stage GRC", company="Foo", location="Lille", fit_score=9)
    seed_job(conn, url_suffix="in2", strategy="indeed_scrape", source="indeed",
             title="Stage Pentest", company="Bar", location="Lyon", fit_score=9)
    offers = fetch_pending_offers(min_score=8, limit=10)
    sources = {o.get("source") for o in offers}
    assert sources == {"hellowork", "indeed"}


# ── Parsing des dates (to_dt / job_date) ────────────────────────────────────
def test_to_dt_iso():
    assert to_dt("2026-11-01").date() == datetime(2026, 11, 1).date()


def test_to_dt_relative_fr():
    assert age_days(to_dt("il y a 5 jours")) == 5
    assert age_days(to_dt("hier")) == 1


def test_to_dt_relative_en():
    assert age_days(to_dt("Just posted")) == 0
    assert age_days(to_dt("30+ days ago")) == 30


def test_to_dt_empty_and_none():
    assert to_dt(None) is None
    assert to_dt("") is None
    assert to_dt("poste à pourvoir") is None  # texte sans date


def test_to_dt_timestamp_seconds():
    dt = to_dt(1700000000)  # secondes epoch → nov. 2023
    assert dt is not None and dt.year == 2023


def test_job_date_prefers_known_key_then_fallback():
    assert age_days(job_date({"date_posted": "il y a 2 jours", "title": "x"})) == 2
    # filet de sécurité : clé non listée mais contenant « posted »
    assert age_days(job_date({"custom_posted_on": "hier"})) == 1
    assert job_date({"title": "aucune date ici"}) is None


def test_normalize_offer_fills_posted_at_via_job_date():
    rec = filter_and_tag(_records(), keep_alt=False)[0]  # JOB_STAGE_CYBER, date_posted ISO
    off = normalize_offer(rec)
    assert off["posted_at"].startswith("2026-11-01")
    assert off["date_publication"] == off["posted_at"]


# ── Tri par date + filtre d'ancienneté dans fetch_pending_offers ────────────
def _iso_days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).isoformat(timespec="minutes")


def test_sort_recent_first_undated_last(tmp_db, seed_job):
    conn = tmp_db()
    seed_job(conn, url_suffix="old", strategy="indeed_scrape", source="indeed",
             title="Vieux stage", company="A", location="Paris", fit_score=9,
             posted_at=_iso_days_ago(20))
    seed_job(conn, url_suffix="recent", strategy="indeed_scrape", source="indeed",
             title="Stage récent", company="B", location="Lyon", fit_score=9,
             posted_at=_iso_days_ago(1))
    seed_job(conn, url_suffix="none", strategy="hellowork_scrape", source="hellowork",
             title="Stage sans date", company="C", location="Lille", fit_score=9,
             posted_at=None)
    offers = fetch_pending_offers(min_score=8, limit=10, max_age_days=0)  # pas de filtre d'âge
    order = [o["url"].rsplit("/", 1)[-1] for o in offers]
    assert order == ["recent", "old", "none"]  # récent → vieux → sans date


def test_max_age_days_drops_old_offers(tmp_db, seed_job):
    conn = tmp_db()
    seed_job(conn, url_suffix="old60", strategy="indeed_scrape", source="indeed",
             title="Stage ancien", company="A", location="Paris", fit_score=9,
             posted_at=_iso_days_ago(60))
    seed_job(conn, url_suffix="fresh2", strategy="indeed_scrape", source="indeed",
             title="Stage frais", company="B", location="Lyon", fit_score=9,
             posted_at=_iso_days_ago(2))
    seed_job(conn, url_suffix="nodate", strategy="hellowork_scrape", source="hellowork",
             title="Stage sans date", company="C", location="Lille", fit_score=9,
             posted_at=None)
    offers = fetch_pending_offers(min_score=8, limit=10, max_age_days=30)
    kept = {o["url"].rsplit("/", 1)[-1] for o in offers}
    assert kept == {"fresh2", "nodate"}  # 60 j écarté, sans date conservé
