from pathlib import Path
import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from applypilot import telegram_bot, experiences_manager


@pytest.mark.anyio
async def test_experience_cmd_with_args(monkeypatch, tmp_path):
    test_json = tmp_path / "experiences.json"
    test_json.write_text(json.dumps({
        "candidate": {"full_name": "Ali Jamai"},
        "experiences": [],
        "skills": {}
    }), encoding="utf-8")

    monkeypatch.setattr(experiences_manager, "EXPERIENCES_PATH", test_json)
    monkeypatch.setattr(telegram_bot, "_authorized", lambda u: True)

    update = MagicMock()
    reply_msg = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=reply_msg)
    context = MagicMock()
    context.args = ["Lab", "Wazuh", "et", "Suricata", "detection"]
    context.user_data = {}

    await telegram_bot.experience_cmd(update, context)

    update.message.reply_text.assert_called_once()
    reply_msg.edit_text.assert_called_once()
    args, kwargs = reply_msg.edit_text.call_args
    assert "Nouveau projet ajouté à ton profil" in args[0]

    # Vérification fichier sauvegardé
    saved = json.loads(test_json.read_text(encoding="utf-8"))
    assert len(saved["experiences"]) == 1
    assert "wazuh" in saved["experiences"][0]["id"] or "suricata" in saved["experiences"][0]["id"]


@pytest.mark.anyio
async def test_experience_cmd_without_args(monkeypatch):
    monkeypatch.setattr(telegram_bot, "_authorized", lambda u: True)

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = []
    context.user_data = {}

    await telegram_bot.experience_cmd(update, context)

    assert context.user_data.get("awaiting_experience") is True
    update.message.reply_text.assert_called_once()
    call_text = update.message.reply_text.call_args[0][0]
    assert "Ajouter un nouveau projet" in call_text


@pytest.mark.anyio
async def test_on_text_awaiting_experience(monkeypatch, tmp_path):
    test_json = tmp_path / "experiences.json"
    test_json.write_text(json.dumps({
        "candidate": {"full_name": "Ali Jamai"},
        "experiences": [],
        "skills": {}
    }), encoding="utf-8")

    monkeypatch.setattr(experiences_manager, "EXPERIENCES_PATH", test_json)
    monkeypatch.setattr(telegram_bot, "_authorized", lambda u: True)

    update = MagicMock()
    reply_msg = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=reply_msg)
    update.message.text = "Audit Active Directory et durcissement GPO"
    context = MagicMock()
    context.user_data = {"awaiting_experience": True}

    await telegram_bot.on_text(update, context)

    assert "awaiting_experience" not in context.user_data
    update.message.reply_text.assert_called_once()
    reply_msg.edit_text.assert_called_once()
    assert "Nouveau projet ajouté à ton profil" in reply_msg.edit_text.call_args[0][0]


def test_command_handlers_registered():
    app = telegram_bot.build_application()
    handlers = app.handlers.get(0, [])
    cmd_handlers = [h for h in handlers if isinstance(h, telegram_bot.CommandHandler)]
    all_cmds = set()
    for h in cmd_handlers:
        all_cmds.update(h.commands)

    assert "experience" in all_cmds
    assert "experiences" in all_cmds
    assert "projet" in all_cmds
    assert "projets" in all_cmds
