import pytest
from unittest.mock import AsyncMock, MagicMock
from applypilot import bot_core
from applypilot import telegram_bot

def test_get_application_counts():
    counts = bot_core.get_application_counts()
    assert "today_count" in counts
    assert "total_count" in counts
    assert "today_jobs" in counts
    assert "daily_counts" in counts
    assert isinstance(counts["today_count"], int)
    assert isinstance(counts["total_count"], int)
    assert counts["today_count"] >= 0
    assert counts["total_count"] >= counts["today_count"]
    for j in counts["today_jobs"]:
        assert "title" in j
        assert "company" in j
        assert "time" in j


@pytest.mark.anyio
async def test_compteur_cmd():
    update = MagicMock()
    update.effective_user.id = 8673210207
    update.effective_chat.id = 8673210207
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()

    context = MagicMock()

    await telegram_bot.compteur_cmd(update, context)

    update.message.reply_text.assert_called_once()
    msg = update.message.reply_text.call_args[0][0]
    assert "Compteur de candidatures" in msg
    assert "Aujourd'hui" in msg
    assert "Total cumulé" in msg
