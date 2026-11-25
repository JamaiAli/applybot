import asyncio
import sqlite3
from unittest.mock import AsyncMock, MagicMock
import pytest

from applypilot import bot_core, telegram_bot


def test_clean_company_for_dedup():
    assert bot_core.clean_company_for_dedup("Sopra Steria Group") == "sopra steria"
    assert bot_core.clean_company_for_dedup("Sopra Steria France") == "sopra steria"
    assert bot_core.clean_company_for_dedup("Orange SA") == "orange"
    assert bot_core.clean_company_for_dedup("Thales DIS France SAS") == "thales dis"
    assert bot_core.clean_company_for_dedup("Capgemini Technologies") == "capgemini"


def test_clean_title_for_dedup():
    t1 = "Stage - Consultant(e) - Prevent_Risk - Cybersécurité - Île-de-France"
    t2 = "Stage - Consultant - PreventRisk - Cybersécurité - Île-De-France H/F"
    assert "stage" not in bot_core.clean_title_for_dedup(t1)
    assert "h/f" not in bot_core.clean_title_for_dedup(t2)
    assert "(e)" not in bot_core.clean_title_for_dedup(t1)


def test_canonicalize_job_url():
    u1 = "https://fr.indeed.com/viewjob?jk=2594f3817660a179&tk=abc&from=serp"
    u2 = "https://www.indeed.com/viewjob?jk=2594f3817660a179"
    assert bot_core.canonicalize_job_url(u1) == bot_core.canonicalize_job_url(u2) == "indeed:2594f3817660a179"

    hw1 = "https://www.hellowork.com/fr-fr/emplois/84577920.html"
    hw2 = "https://www.hellowork.com/emplois/84577920.html?utm_source=indeed"
    assert bot_core.canonicalize_job_url(hw1) == bot_core.canonicalize_job_url(hw2) == "hellowork:84577920"


def test_is_duplicate_offer_sopra_steria():
    t1 = "Stage - Consultant(e) - Prevent_Risk - Cybersécurité - Île-de-France"
    u1 = "https://fr.indeed.com/viewjob?jk=2594f3817660a179"
    c1 = "Sopra Steria"

    t2 = "Stage - Consultant - PreventRisk - Cybersécurité - Île-De-France H/F"
    u2 = "https://www.hellowork.com/fr-fr/emplois/84577920.html"
    c2 = "Sopra Steria France"

    assert bot_core.is_duplicate_offer(t1, c1, u1, t2, c2, u2) is True


def test_is_duplicate_offer_orange_reposts():
    t1 = "Stage sécurité - Outillage de gestion et de suivi d'incidents sécurité F/H"
    u1 = "https://fr.indeed.com/viewjob?jk=7cca3a45ad11b852"
    c1 = "Orange"

    t2 = "Stage sécurité - Outillage de gestion et de suivi d'incidents sécurité F/H"
    u2 = "https://fr.indeed.com/viewjob?jk=19aa17026bd8fa7d"
    c2 = "Orange"

    assert bot_core.is_duplicate_offer(t1, c1, u1, t2, c2, u2) is True


def test_is_duplicate_offer_different_jobs_rejected():
    # Deux postes différents chez la même entreprise
    t1 = "Stage - Analyste Voc - Cybersécurité - Toulouse H/F"
    t2 = "Stage - Chef - Fe de Projet - Cybersécurité - Toulouse H/F"
    assert bot_core.is_duplicate_offer(t1, "Sopra Steria", "u1", t2, "Sopra Steria", "u2") is False

    # Même titre chez deux entreprises différentes
    t3 = "Stage Pentest Cybersécurité"
    assert bot_core.is_duplicate_offer(t3, "Thales", "u3", t3, "Airbus", "u4") is False


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test_dedup.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("""
        CREATE TABLE jobs (
            url TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            site TEXT,
            location TEXT,
            description TEXT,
            full_description TEXT,
            application_url TEXT,
            tailored_resume_path TEXT,
            cover_letter_path TEXT,
            fit_score INTEGER,
            score_reasoning TEXT,
            eligibility TEXT,
            apply_status TEXT,
            applied_at TEXT,
            discovered_at TEXT,
            strategy TEXT,
            state TEXT
        )
    """)
    conn.commit()
    monkeypatch.setattr(bot_core, "get_connection", lambda: sqlite3.connect(str(db_file)))
    return db_file


def test_check_already_applied(test_db):
    conn = sqlite3.connect(str(test_db))
    # Postulé chez Orange
    conn.execute(
        "INSERT INTO jobs (url, title, company, site, apply_status, applied_at) "
        "VALUES ('https://fr.indeed.com/viewjob?jk=7cca3a45ad11b852', "
        "'Stage sécurité - Outillage de gestion et de suivi d incidents sécurité F/H', "
        "'Orange', 'Orange', 'applied', '2026-11-25T14:50:00Z')"
    )
    conn.commit()

    # Repost Orange avec un autre jk
    repost_url = "https://fr.indeed.com/viewjob?jk=19aa17026bd8fa7d"
    is_app, prev = bot_core.check_already_applied(
        repost_url,
        "Stage sécurité - Outillage de gestion et de suivi d'incidents sécurité F/H",
        "Orange France"
    )
    assert is_app is True
    assert prev["company"] == "Orange"
    assert "sécurité" in prev["title"]

    # Offre jamais postulée
    is_app2, prev2 = bot_core.check_already_applied("https://autre.com", "Stage SOC Junior", "Thales")
    assert is_app2 is False
    assert prev2 is None


def test_prep_blocked_if_already_applied(test_db, monkeypatch):
    conn = sqlite3.connect(str(test_db))
    applied_url = "https://fr.indeed.com/viewjob?jk=2594f3817660a179"
    new_url = "https://www.hellowork.com/fr-fr/emplois/84577920.html"

    conn.execute(
        "INSERT INTO jobs (url, title, company, site, apply_status, applied_at) "
        "VALUES (?, 'Stage - Consultant(e) - Prevent_Risk - Cybersécurité - Île-de-France', 'Sopra Steria', 'Sopra Steria', 'applied', '2026-11-18T19:13:00Z')",
        (applied_url,)
    )
    conn.execute(
        "INSERT INTO jobs (url, title, company, site, apply_status) "
        "VALUES (?, 'Stage - Consultant - PreventRisk - Cybersécurité - Île-De-France H/F', 'Sopra Steria France', 'Sopra Steria', NULL)",
        (new_url,)
    )
    conn.commit()

    monkeypatch.setattr(telegram_bot, "_authorized", lambda u: True)
    cv_gen_called = False

    async def fake_gen_cv(*args, **kwargs):
        nonlocal cv_gen_called
        cv_gen_called = True
        return None

    monkeypatch.setattr("applypilot.cv_latex.generate_cv", fake_gen_cv)

    h = bot_core.remember(new_url)

    update = MagicMock()
    query = MagicMock()
    query.data = f"prep:{h}"
    query.message.chat_id = 12345
    query.answer = AsyncMock()
    update.callback_query = query
    update.effective_chat.id = 12345

    context = MagicMock()
    context.user_data = {}
    context.bot.send_message = AsyncMock()

    asyncio.run(telegram_bot.on_button(update, context))

    # Le bot doit avoir affiché l'alerte et bloqué la génération de CV !
    assert cv_gen_called is False
    query.answer.assert_called_once()
    assert "déjà envoyée" in query.answer.call_args[0][0]

    context.bot.send_message.assert_called_once()
    msg_sent = context.bot.send_message.call_args[0][1]
    assert "Tu as DÉJÀ postulé" in msg_sent
    assert "Sopra Steria" in msg_sent


def test_jobapply_blocked_if_already_applied(test_db, monkeypatch):
    conn = sqlite3.connect(str(test_db))
    applied_url = "https://fr.indeed.com/viewjob?jk=vendeur123"
    repost_url = "https://fr.indeed.com/viewjob?jk=vendeur456"

    conn.execute(
        "INSERT INTO jobs (url, title, company, site, apply_status, applied_at) "
        "VALUES (?, 'Job Etudiant - Vendeur Polyvalent H/F', 'Zara', 'Zara', 'applied', '2026-11-20T10:00:00Z')",
        (applied_url,)
    )
    conn.commit()

    monkeypatch.setattr(telegram_bot, "_authorized", lambda u: True)
    monkeypatch.setattr(bot_core, "_load_student_jobs", lambda: {
        repost_url: {"title": "Job Etudiant - Vendeur Polyvalent", "employer": "Zara France"}
    })

    h = bot_core.remember(repost_url)

    update = MagicMock()
    query = MagicMock()
    query.data = f"jobapply:{h}"
    query.message.chat_id = 12345
    query.answer = AsyncMock()
    update.callback_query = query
    update.effective_chat.id = 12345

    context = MagicMock()
    context.user_data = {}
    context.bot.send_message = AsyncMock()

    asyncio.run(telegram_bot.on_button(update, context))

    query.answer.assert_called_once()
    assert "déjà envoyée" in query.answer.call_args[0][0]

    context.bot.send_message.assert_called_once()
    msg_sent = context.bot.send_message.call_args[0][1]
    assert "Tu as DÉJÀ postulé" in msg_sent
    assert "Zara" in msg_sent


def test_send_offer_skips_if_already_applied(test_db):
    conn = sqlite3.connect(str(test_db))
    conn.execute(
        "INSERT INTO jobs (url, title, company, site, apply_status, applied_at) "
        "VALUES ('https://fr.indeed.com/viewjob?jk=abc', 'Consultant Cyber GRC', 'Orange', 'Orange', 'applied', '2026-11-20T10:00:00Z')"
    )
    conn.commit()

    bot = MagicMock()
    bot.send_message = AsyncMock()

    # Tentative d'envoi d'un doublon
    duplicate_row = {
        "url": "https://fr.indeed.com/viewjob?jk=xyz",
        "title": "Stage - Consultant Cyber GRC H/F",
        "company": "Orange France",
        "site": "Orange",
    }
    asyncio.run(telegram_bot._send_offer(bot, 12345, duplicate_row))

    # Ne doit PAS avoir été envoyé !
    bot.send_message.assert_not_called()
