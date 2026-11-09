"""Tests pour le filtrage strict (<= 7 jours) et la priorisation du jour même.

Vérifie que :
1. Les offres et jobs étudiants/intérim de plus de 7 jours sont strictement écartés.
2. Les offres sorties le jour même (aujourd'hui / 0 jour) sont TOUJOURS affichées en premier.
3. À date égale, Indeed est prioritaire sur les autres sources, et les meilleurs scores en tête.
4. L'affichage Telegram met en valeur les offres parues le jour même.
"""

from datetime import datetime, timedelta, timezone
import pytest

from applypilot import bot_core
from applypilot.discovery import indeed_student
from applypilot.telegram_bot import format_age_fr, _offer_text


def _iso_days(days_ago: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.isoformat()


def test_resolve_max_age_days_defaults_to_7():
    # Par défaut, sans configuration, la limite doit être 7 jours
    assert bot_core._resolve_max_age_days(None) == 7
    # Paramètre explicite respecté
    assert bot_core._resolve_max_age_days(3) == 3


def test_filter_by_age_strictly_within_7_days():
    rows = [
        {"url": "u1", "posted_at": _iso_days(0.1)},   # Aujourd'hui
        {"url": "u2", "posted_at": _iso_days(1.0)},   # 1 jour
        {"url": "u3", "posted_at": _iso_days(6.0)},   # 6 jours
        {"url": "u4", "posted_at": _iso_days(8.1)},   # 8 jours -> rejeté (> 7j)
        {"url": "u5", "posted_at": _iso_days(15)},    # 15 jours -> rejeté (> 7j)
        {"url": "u6", "posted_at": None},              # Sans date -> conservé en fin
    ]
    kept = bot_core._filter_by_age(rows, max_age_days=7)
    urls = [r["url"] for r in kept]
    assert urls == ["u1", "u2", "u3", "u6"]


def test_sort_offers_by_freshness_today_first():
    rows = [
        {"url": "u_hier_indeed", "strategy": "indeed_scrape", "fit_score": 10, "posted_at": _iso_days(1)},
        {"url": "u_today_hellowork", "strategy": "hellowork_scrape", "fit_score": 9, "posted_at": _iso_days(0.2)},
        {"url": "u_today_indeed_score8", "strategy": "indeed_scrape", "fit_score": 8, "posted_at": _iso_days(0.1)},
        {"url": "u_today_indeed_score10", "strategy": "indeed_scrape", "fit_score": 10, "posted_at": _iso_days(0.05)},
        {"url": "u_3d", "strategy": "indeed_scrape", "fit_score": 10, "posted_at": _iso_days(3)},
    ]
    sorted_rows = bot_core._sort_offers_by_freshness(rows)
    urls = [r["url"] for r in sorted_rows]

    # Indeed prioritaire sur HelloWork, et au sein d'Indeed : jour même (0j) en tout premier !
    # 1. u_today_indeed_score10 (aujourd'hui, Indeed, 10/10)
    # 2. u_today_indeed_score8  (aujourd'hui, Indeed, 8/10)
    # 3. u_hier_indeed          (hier, Indeed, 10/10)
    # 4. u_3d                   (il y a 3j, Indeed, 10/10)
    # 5. u_today_hellowork      (aujourd'hui, HelloWork, 9/10)
    assert urls == [
        "u_today_indeed_score10",
        "u_today_indeed_score8",
        "u_hier_indeed",
        "u_3d",
        "u_today_hellowork",
    ]


def test_indeed_student_sort_offers_freshness():
    offers = [
        {"title": "Job 3j", "anciennete_jours": 3, "pertinence": 10, "posted_at": _iso_days(3)},
        {"title": "Job Today", "anciennete_jours": 0, "pertinence": 5, "posted_at": _iso_days(0.1)},
        {"title": "Job Hier", "anciennete_jours": 1, "pertinence": 8, "posted_at": _iso_days(1)},
        {"title": "Job 20j (trop vieux)", "anciennete_jours": 20, "pertinence": 10, "posted_at": _iso_days(20)},
    ]
    # Tri par date avec filtre 7 jours
    sorted_jobs = indeed_student.sort_offers(offers, sort="date", max_age_days=7)
    titles = [j["title"] for j in sorted_jobs]
    assert titles == ["Job Today", "Job Hier", "Job 3j"]


def test_telegram_today_highlight():
    today_iso = _iso_days(0.1)
    age_str = format_age_fr(today_iso)
    assert "aujourd'hui" in age_str

    offer = {
        "title": "Ingénieur Cyber",
        "company": "Thales",
        "fit_score": 9,
        "posted_at": today_iso,
        "url": "https://example.com/job1",
    }
    card_text = _offer_text(offer)
    assert "🔥 <b>Parue aujourd'hui" in card_text


def test_matches_query_metier_aliases():
    soc_row = {"title": "Stage - Analyste SOC / SIEM (H/F)", "domaines": '["SOC / Blue team"]'}
    pentest_row = {"title": "Stage Auditeur Pentest / Red Team", "domaines": '["Pentest / Offensive"]'}
    ad_row = {"title": "Stage Ingénieur Active Directory / IAM", "domaines": '["AD / IAM"]'}
    cloud_row = {"title": "Stage DevOps / Kubernetes Sécurité", "domaines": '["DevSecOps / Cloud"]'}
    admin_row = {"title": "Stage Administrateur Systèmes", "domaines": '["Support / Infra"]'}

    # SOC
    assert bot_core._matches_query("soc", soc_row) is True
    assert bot_core._matches_query("siem", soc_row) is True
    assert bot_core._matches_query("pentest", soc_row) is False

    # Pentest
    assert bot_core._matches_query("pentest", pentest_row) is True
    assert bot_core._matches_query("offensive", pentest_row) is True

    # AD : doit matcher Active Directory mais PAS "Administrateur"
    assert bot_core._matches_query("ad", ad_row) is True
    assert bot_core._matches_query("ad", admin_row) is False

    # Cloud
    assert bot_core._matches_query("cloud", cloud_row) is True
    assert bot_core._matches_query("devops", cloud_row) is True


def test_parse_search_args_flexible():
    from applypilot.telegram_bot import _parse_search_args

    assert _parse_search_args(["soc"]) == ("soc", None, 6)
    assert _parse_search_args(["pentest", "Paris"]) == ("pentest", "paris", 6)
    assert _parse_search_args(["ad", "10"]) == ("ad", None, 10)
    assert _parse_search_args(["soc", "à", "Lyon", "5"]) == ("soc", "lyon", 5)
    assert _parse_search_args(["Paris"]) == (None, "paris", 6)
    assert _parse_search_args([]) == (None, None, 6)
    assert _parse_search_args(["grc"]) == ("grc", None, 6)
    assert _parse_search_args(["cloud", "10"]) == ("cloud", None, 10)


def test_fetch_new_scan_offers_isolation(tmp_path, monkeypatch):
    import sqlite3
    db_file = tmp_path / "test_scan.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("""
        CREATE TABLE jobs (
            url TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            site TEXT,
            source TEXT,
            domaines TEXT,
            strategy TEXT,
            location TEXT,
            fit_score REAL,
            score_reasoning TEXT,
            application_url TEXT,
            tailored_resume_path TEXT,
            cover_letter_path TEXT,
            posted_at TEXT,
            eligibility TEXT,
            apply_status TEXT
        )
    """)
    # Insertion de 2 anciennes offres (déjà en base)
    conn.execute("INSERT INTO jobs (url, title, fit_score, posted_at) VALUES ('old1', 'Stage SOC', 9, '2026-11-08')")
    conn.execute("INSERT INTO jobs (url, title, fit_score, posted_at) VALUES ('old2', 'Stage Pentest', 8, '2026-11-08')")

    # Insertion de 2 nouvelles offres scannées en direct
    conn.execute("INSERT INTO jobs (url, title, fit_score, posted_at, location) VALUES ('new1', 'Stage Analyste SOC', 9, '2026-11-09', 'Paris')")
    conn.execute("INSERT INTO jobs (url, title, fit_score, posted_at, location) VALUES ('new2', 'Stage Développeur Python', 3, '2026-11-09', 'Lyon')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(bot_core, "get_connection", lambda: sqlite3.connect(str(db_file)))

    all_urls = bot_core.get_all_job_urls()
    assert all_urls == {"old1", "old2", "new1", "new2"}

    # On teste que fetch_new_scan_offers NE retourne QUE les offres de new_urls
    new_urls = ["new1", "new2"]
    offers = bot_core.fetch_new_scan_offers(new_urls, min_score=5)

    assert len(offers) == 1
    assert offers[0]["url"] == "new1"
    assert "old1" not in [o["url"] for o in offers]
    assert "old2" not in [o["url"] for o in offers]

    # Test avec filtre métier
    soc_offers = bot_core.fetch_new_scan_offers(new_urls, min_score=5, query="soc")
    assert len(soc_offers) == 1
    assert soc_offers[0]["url"] == "new1"

    pentest_offers = bot_core.fetch_new_scan_offers(new_urls, min_score=5, query="pentest")
    assert len(pentest_offers) == 0
