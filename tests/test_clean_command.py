from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest

from applypilot import telegram_bot


@pytest.mark.anyio
async def test_clean_cmd_uses_delete_messages_in_bulk():
    """Vérifie que /clean utilise delete_messages par lots de 100 et balaie 2000 messages par défaut."""
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=123),
        message=SimpleNamespace(message_id=2500),
    )
    mock_bot = SimpleNamespace(
        delete_messages=AsyncMock(),
        delete_message=AsyncMock(),
        send_message=AsyncMock(),
    )
    context = SimpleNamespace(
        args=[],
        bot=mock_bot,
    )

    with patch.object(telegram_bot, "_authorized", return_value=True), \
         patch("asyncio.sleep", new_callable=AsyncMock):

        await telegram_bot.clean_cmd(update, context)

        # 2000 messages / 100 = 20 lots appelés
        assert mock_bot.delete_messages.call_count == 20
        # Vérifie le premier lot
        first_call_args = mock_bot.delete_messages.call_args_list[0][0]
        assert first_call_args[0] == 123
        assert len(first_call_args[1]) == 100
        assert first_call_args[1][0] == 2500
        assert first_call_args[1][-1] == 2401

        # Vérifie l'envoi de la confirmation
        mock_bot.send_message.assert_called_once()
        msg_text = mock_bot.send_message.call_args[0][1]
        assert "Discussion nettoyée" in msg_text


@pytest.mark.anyio
async def test_clean_cmd_handles_custom_arg_and_48h_limit():
    """Vérifie que /clean accepte un paramètre (ex. 500 ou all) et s'arrête proprement à la limite 48h."""
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=123),
        message=SimpleNamespace(message_id=500),
    )
    # Simuler une exception "can't be deleted" dès le 2ème lot (limite 48h)
    mock_bot = SimpleNamespace(
        delete_messages=AsyncMock(side_effect=[True, Exception("BadRequest: message can't be deleted")]),
        delete_message=AsyncMock(side_effect=Exception("BadRequest: message can't be deleted")),
        send_message=AsyncMock(),
    )
    context = SimpleNamespace(
        args=["all"],
        bot=mock_bot,
    )

    with patch.object(telegram_bot, "_authorized", return_value=True), \
         patch("asyncio.sleep", new_callable=AsyncMock):

        await telegram_bot.clean_cmd(update, context)

        # Le premier lot a réussi, le second a échoué avec can't be deleted et a stoppé immédiatement
        assert mock_bot.delete_messages.call_count == 2
        mock_bot.send_message.assert_called_once()


@pytest.mark.anyio
async def test_clean_cmd_unauthorized():
    """Vérifie le blocage pour les utilisateurs non autorisés."""
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=999),
        message=SimpleNamespace(message_id=100),
    )
    context = SimpleNamespace(args=[], bot=SimpleNamespace())

    with patch.object(telegram_bot, "_authorized", return_value=False), \
         patch.object(telegram_bot, "_deny", new_callable=AsyncMock) as mock_deny:

        await telegram_bot.clean_cmd(update, context)
        mock_deny.assert_called_once_with(update)
