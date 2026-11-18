import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest

from applypilot import telegram_bot


@pytest.mark.anyio
async def test_entretien_cmd_displays_interviews_with_slots_and_actions():
    """Vérifie que /entretien affiche les entretiens obtenus avec créneaux, contacts et actions."""
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=123),
        effective_message=SimpleNamespace(reply_text=AsyncMock()),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )
    context = SimpleNamespace(
        args=[],
        bot=SimpleNamespace(send_message=AsyncMock()),
    )

    sample_interviews = [
        {
            "email_id": "msg1",
            "subject": "Invitation: Entretien Cybersécurité - Thales",
            "company": "Thales",
            "title": "Stage Analyste SOC",
            "sender": "recrutement@thales.com",
            "sender_name": "Claire Martin",
            "received_at": "2026-11-17T10:30:00+00:00",
            "snippet": "Nous souhaitons vous rencontrer pour un entretien.",
            "extracted_data": json.dumps({
                "summary": "Invitation à un entretien technique SOC avec Thales.",
                "dates": [{"date": "2026-11-20", "description": "Entretien visio 14h00 CET"}],
                "action_items": [{"task": "Confirmer la présence via Teams", "deadline": "2026-11-19"}],
            }),
        }
    ]

    with patch.object(telegram_bot, "_authorized", return_value=True), \
         patch("applypilot.database.get_classified_emails", return_value=sample_interviews) as mock_get:

        await telegram_bot.entretien_cmd(update, context)

        mock_get.assert_called_once_with("interview", limit=20)
        update.effective_message.reply_text.assert_called_once()
        msg = update.effective_message.reply_text.call_args[0][0]
        assert "Entretiens obtenus (1)" in msg
        assert "Thales" in msg
        assert "Stage Analyste SOC" in msg
        assert "Claire Martin" in msg
        assert "Créneau" in msg
        assert "2026-11-20" in msg
        assert "À faire" in msg
        assert "Confirmer la présence via Teams" in msg


@pytest.mark.anyio
async def test_entretien_cmd_empty():
    """Vérifie le message propre quand aucun entretien n'est encore enregistré."""
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=123),
        effective_message=SimpleNamespace(reply_text=AsyncMock()),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )
    context = SimpleNamespace(
        args=[],
        bot=SimpleNamespace(send_message=AsyncMock()),
    )

    with patch.object(telegram_bot, "_authorized", return_value=True), \
         patch("applypilot.database.get_classified_emails", return_value=[]):

        await telegram_bot.entretien_cmd(update, context)

        update.effective_message.reply_text.assert_called_once()
        msg = update.effective_message.reply_text.call_args[0][0]
        assert "Aucun entretien" in msg
        assert "/track" in msg


@pytest.mark.anyio
async def test_commandes_mentions_entretien():
    """Vérifie que /commandes mentionne bien /entretien."""
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=123),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )
    context = SimpleNamespace(args=[])

    with patch.object(telegram_bot, "_authorized", return_value=True):
        await telegram_bot.commandes_cmd(update, context)
        msg = update.message.reply_text.call_args[0][0]
        assert "/entretien" in msg
