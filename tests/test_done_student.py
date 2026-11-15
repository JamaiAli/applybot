from unittest.mock import AsyncMock, MagicMock
import pytest
from applypilot import bot_core, config, telegram_bot, database


@pytest.fixture(autouse=True)
def setup_tmp_db(tmp_path, monkeypatch):
    app_dir = tmp_path / "applypilot_test"
    app_dir.mkdir(parents=True, exist_ok=True)
    db_path = app_dir / "jobs.db"
    student_cv = app_dir / "cv_job_etudiant.pdf"
    student_cv.write_bytes(b"%PDF-1.4 dummy")

    monkeypatch.setattr(config, "APP_DIR", app_dir)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)
    if hasattr(database._local, "connections"):
        database._local.connections.clear()

    monkeypatch.setattr(bot_core, "_LAST_PREPARED", app_dir / "last_prepared.txt")
    monkeypatch.setattr(bot_core, "_LAST_PREPARED_CV", app_dir / "last_prepared_cv.txt")
    monkeypatch.setattr(bot_core, "_LAST_PREPARED_TYPE", app_dir / "last_prepared_type.txt")
    monkeypatch.setattr(bot_core, "_LAST_PREPARED_TIME", app_dir / "last_prepared_time.txt")
    monkeypatch.setattr(bot_core, "_STUDENT_JOBS_PATH", app_dir / "student_jobs.json")
    monkeypatch.setattr(bot_core, "_CARDS_PATH", app_dir / "card_messages.json")
    monkeypatch.setattr(telegram_bot, "JOB_CV", student_cv)

    database.init_db(db_path)


@pytest.mark.anyio
async def test_done_marks_student_job_properly_and_deletes_card():
    student_url = "https://fr.indeed.com/viewjob?jk=convivio123"
    card_message_id = 998877
    bot_core.mark_student_job(student_url, title="Magasinier (H/F)", employer="Convivio")
    bot_core.set_card_message(student_url, card_message_id)
    bot_core.set_last_prepared(student_url, cv_path=str(telegram_bot.JOB_CV), job_type="student")

    update = MagicMock()
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.effective_chat.id = 12345
    update.effective_user.id = telegram_bot.OWNER_ID

    context = MagicMock()
    context.args = []
    context.user_data = {"last_prepared_url": student_url}
    context.bot = MagicMock()
    context.bot.delete_message = AsyncMock()

    # Exécuter done_cmd
    await telegram_bot.done_cmd(update, context)

    # 1. Vérifier que delete_message a été appelé avec l'ID du message de la carte
    context.bot.delete_message.assert_called_once_with(12345, card_message_id)

    # 2. Vérifier que le message de confirmation mentionne bien le job étudiant
    update.message.reply_text.assert_called_once()
    reply_text = update.message.reply_text.call_args[0][0]
    assert "Magasinier (H/F)" in reply_text
    assert "Convivio" in reply_text

    # 3. Vérifier en base que le job est bien marqué applied
    row = bot_core.fetch_one(student_url)
    assert row is not None
    assert row["apply_status"] == "applied"
    assert row["company"] == "Convivio"


@pytest.mark.anyio
async def test_done_with_keyword_for_student_job():
    student_url = "https://fr.indeed.com/viewjob?jk=carrefour456"
    bot_core.mark_student_job(student_url, title="Hôte de caisse", employer="Carrefour")

    update = MagicMock()
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.effective_chat.id = 12345
    update.effective_user.id = telegram_bot.OWNER_ID

    context = MagicMock()
    context.args = ["carrefour"]
    context.user_data = {}
    context.bot = MagicMock()
    context.bot.delete_message = AsyncMock()

    await telegram_bot.done_cmd(update, context)

    update.message.reply_text.assert_called_once()
    reply_text = update.message.reply_text.call_args[0][0]
    assert "Hôte de caisse" in reply_text
    assert "Carrefour" in reply_text

    row = bot_core.fetch_one(student_url)
    assert row is not None
    assert row["apply_status"] == "applied"


def test_merge_student_jobs_excludes_applied_job():
    job1 = {
        "url": "https://fr.indeed.com/viewjob?jk=job_applied_1",
        "title": "Employé polyvalent",
        "employer_name": "Lidl",
        "location": "Bourges",
    }
    job2 = {
        "url": "https://fr.indeed.com/viewjob?jk=job_fresh_2",
        "title": "Vendeur",
        "employer_name": "Fnac",
        "location": "Bourges",
    }

    # Marquer job1 comme déjà postulé
    bot_core.mark_student_job(job1["url"], title=job1["title"], employer=job1["employer_name"])
    bot_core.set_apply_status(job1["url"], "applied")

    merged = telegram_bot._merge_student_jobs([job1, job2])

    # Seul job2 doit être conservé dans la liste
    assert len(merged) == 1
    assert merged[0]["url"] == job2["url"]
    assert merged[0]["title"] == "Vendeur"
