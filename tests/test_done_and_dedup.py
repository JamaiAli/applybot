import asyncio
import sqlite3
import pytest
from unittest.mock import AsyncMock, MagicMock
from applypilot import bot_core
from applypilot import telegram_bot


@pytest.fixture
def mock_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        """
        CREATE TABLE jobs (
            url TEXT PRIMARY KEY,
            title TEXT,
            salary TEXT,
            description TEXT,
            location TEXT,
            site TEXT,
            strategy TEXT,
            discovered_at TEXT,
            company TEXT,
            full_description TEXT,
            application_url TEXT,
            detail_scraped_at TEXT,
            fit_score INTEGER,
            score_reasoning TEXT,
            tailored_resume_path TEXT,
            cover_letter_path TEXT,
            applied_at TEXT,
            apply_status TEXT,
            posted_at TEXT,
            state TEXT,
            eligibility TEXT,
            source TEXT,
            domaines TEXT,
            user_note TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE job_state_transitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_url TEXT,
            from_state TEXT,
            to_state TEXT,
            at TEXT,
            reason TEXT,
            metadata TEXT
        )
        """
    )
    conn.commit()

    monkeypatch.setattr(bot_core, "get_connection", lambda: sqlite3.connect(str(db_file)))
    return db_file


def test_set_apply_status_propagates_to_duplicates(mock_db):
    conn = sqlite3.connect(str(mock_db))
    conn.execute(
        "INSERT INTO jobs (url, title, company, site, fit_score, eligibility, apply_status) "
        "VALUES ('u1', 'Stage Pentest', 'Orange', 'Orange', 9, 'eligible', NULL)"
    )
    conn.execute(
        "INSERT INTO jobs (url, title, company, site, fit_score, eligibility, apply_status) "
        "VALUES ('u2', 'Stage Pentest', 'Orange', 'Orange', 9, 'eligible', NULL)"
    )
    conn.commit()

    bot_core.set_apply_status("u1", "applied")

    row1 = conn.execute("SELECT apply_status, applied_at FROM jobs WHERE url = 'u1'").fetchone()
    row2 = conn.execute("SELECT apply_status, applied_at FROM jobs WHERE url = 'u2'").fetchone()

    assert row1[0] == "applied"
    assert row1[1] is not None
    assert row2[0] == "applied"
    assert row2[1] is not None


def test_fetch_pending_offers_excludes_duplicates_of_applied_jobs(mock_db):
    conn = sqlite3.connect(str(mock_db))
    conn.execute(
        "INSERT INTO jobs (url, title, company, site, fit_score, eligibility, apply_status, posted_at, strategy) "
        "VALUES ('u1', 'Stagiaire Pentest F/H', 'Orange', 'Orange', 9, 'eligible', 'applied', '2026-11-10T10:00:00Z', 'indeed_scrape')"
    )
    conn.execute(
        "INSERT INTO jobs (url, title, company, site, fit_score, eligibility, apply_status, posted_at, strategy) "
        "VALUES ('u2', 'Stagiaire Pentest F/H', 'Orange', 'Orange', 9, 'eligible', NULL, '2026-11-10T10:00:00Z', 'indeed_scrape')"
    )
    conn.execute(
        "INSERT INTO jobs (url, title, company, site, fit_score, eligibility, apply_status, posted_at, strategy) "
        "VALUES ('u3', 'Stagiaire SOC Analyst', 'Thales', 'Thales', 9, 'eligible', NULL, '2026-11-10T10:00:00Z', 'indeed_scrape')"
    )
    conn.commit()

    offers = bot_core.fetch_pending_offers(min_score=8, limit=10, strategies=("indeed_scrape",))
    urls = [o["url"] for o in offers]

    assert "u1" not in urls
    assert "u2" not in urls
    assert "u3" in urls


def test_done_cmd_resolves_pending_edit_or_prep(mock_db, monkeypatch):
    conn = sqlite3.connect(str(mock_db))
    conn.execute(
        "INSERT INTO jobs (url, title, company, site, fit_score, eligibility, apply_status, discovered_at) "
        "VALUES ('u_old', 'Stage Pentest', 'Orange', 'Orange', 9, 'eligible', 'applied', '2026-11-10T08:00:00Z')"
    )
    conn.execute(
        "INSERT INTO jobs (url, title, company, site, fit_score, eligibility, apply_status, discovered_at) "
        "VALUES ('u_cnil', 'Stage Cyber CNIL', 'CNIL', 'CNIL', 9, 'eligible', 'edit_requested', '2026-11-10T12:00:00Z')"
    )
    conn.commit()

    monkeypatch.setattr(bot_core, "get_last_prepared", lambda: "u_old")
    monkeypatch.setattr(telegram_bot, "_authorized", lambda u: True)

    update = MagicMock()
    update.effective_chat.id = 12345
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.args = []
    context.user_data = {}
    context.bot.delete_message = AsyncMock()

    asyncio.run(telegram_bot.done_cmd(update, context))

    row_cnil = conn.execute("SELECT apply_status FROM jobs WHERE url = 'u_cnil'").fetchone()
    assert row_cnil[0] == "applied"
    update.message.reply_text.assert_called_once()
    assert "Stage Cyber CNIL" in update.message.reply_text.call_args[0][0]


def test_done_cmd_with_args(mock_db, monkeypatch):
    conn = sqlite3.connect(str(mock_db))
    conn.execute(
        "INSERT INTO jobs (url, title, company, site, fit_score, eligibility, apply_status, discovered_at) "
        "VALUES ('u_soc', 'Stage SOC L1', 'Sopra Steria', 'Sopra Steria', 9, 'eligible', NULL, '2026-11-10T08:00:00Z')"
    )
    conn.commit()

    monkeypatch.setattr(telegram_bot, "_authorized", lambda u: True)

    update = MagicMock()
    update.effective_chat.id = 12345
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.args = ["sopra"]
    context.user_data = {}
    context.bot.delete_message = AsyncMock()

    asyncio.run(telegram_bot.done_cmd(update, context))

    row = conn.execute("SELECT apply_status FROM jobs WHERE url = 'u_soc'").fetchone()
    assert row[0] == "applied"
    assert "Stage SOC L1" in update.message.reply_text.call_args[0][0]
