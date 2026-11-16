from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace
import pytest

from applypilot import bot_core, config, database, telegram_bot


@pytest.fixture(autouse=True)
def setup_test_env(tmp_path, monkeypatch):
    app_dir = tmp_path / "applypilot_test"
    app_dir.mkdir(parents=True, exist_ok=True)
    db_path = app_dir / "jobs.db"
    student_cv = app_dir / "cv_job_etudiant.pdf"
    student_cv.write_bytes(b"%PDF-1.4 dummy student resume")

    monkeypatch.setattr(config, "APP_DIR", app_dir)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)
    if hasattr(database._local, "connections"):
        database._local.connections.clear()

    monkeypatch.setattr(bot_core, "_STUDENT_JOBS_PATH", app_dir / "student_jobs.json")
    database.init_db(db_path)


@pytest.mark.anyio
async def test_send_job_list_has_ignore_button():
    """Vérifie que chaque job envoyé contient bien le bouton Ignorer."""
    mock_bot = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=999))
    )
    jobs = [
        {
            "url": "https://fr.indeed.com/viewjob?jk=testjob1",
            "title": "Hôte de caisse",
            "employer_name": "Carrefour",
            "location": "Bourges",
            "posted_at": "2026-11-16T10:00:00Z",
        }
    ]

    await telegram_bot._send_job_list(mock_bot, 12345, "Jobs de test", jobs)

    # 1er message = titre, 2e message = la carte de job
    assert mock_bot.send_message.call_count == 2
    card_call = mock_bot.send_message.call_args_list[1]
    _, kwargs = card_call
    kb = kwargs["reply_markup"]

    buttons = kb.inline_keyboard[0]
    labels = [b.text for b in buttons]
    assert "🚀 Postuler" in labels
    assert "Voir l'offre" in labels
    assert "❌ Ignorer" in labels

    ignore_btn = [b for b in buttons if "Ignorer" in b.text][0]
    h = bot_core.remember(jobs[0]["url"])
    assert ignore_btn.callback_data == f"no:{h}"


@pytest.mark.anyio
async def test_job_ignore_marks_decided_and_filters_future_list():
    """Vérifie qu'ignorer un job le marque dans student_jobs.json et l'exclut des futurs listes."""
    mock_bot = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=1001))
    )
    url = "https://fr.indeed.com/viewjob?jk=ignorejob2"
    job = {
        "url": url,
        "title": "Équipier polyvalent",
        "employer_name": "Burger King",
        "location": "Bourges",
    }

    # 1. Envoi initial
    await telegram_bot._send_job_list(mock_bot, 12345, "Jobs", [job])
    assert bot_core.is_job_decided(url) is False

    # 2. Clic sur Ignorer via callback handler
    h = bot_core.remember(url)
    mock_query = SimpleNamespace(
        data=f"no:{h}",
        message=SimpleNamespace(
            chat_id=12345,
            message_id=1001,
            delete=AsyncMock(),
            edit_message_reply_markup=AsyncMock(),
        ),
        answer=AsyncMock(),
    )
    mock_context = SimpleNamespace(
        bot=mock_bot,
        user_data={},
    )
    mock_update = SimpleNamespace(
        callback_query=mock_query,
        effective_user=SimpleNamespace(id=telegram_bot.OWNER_ID),
    )

    await telegram_bot.on_button(mock_update, mock_context)

    # Vérifie que le message est supprimé et l'offre marquée comme ignorée
    mock_query.message.delete.assert_called_once()
    assert bot_core.is_job_decided(url) is True

    # 3. Le job ne doit plus être envoyé lors d'un nouvel appel à _send_job_list
    mock_bot.send_message.reset_mock()
    await telegram_bot._send_job_list(mock_bot, 12345, "Jobs", [job])
    # Puisque l'offre est ignorée, aucun message de carte ne doit être envoyé (seul le message d'absence d'offre est envoyé)
    assert mock_bot.send_message.call_count == 1
    last_msg = mock_bot.send_message.call_args[0][1]
    assert "aucune offre" in last_msg
