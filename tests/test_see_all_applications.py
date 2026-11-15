import pytest
from unittest.mock import AsyncMock, MagicMock
from telegram import Update, Message, CallbackQuery
from applypilot import bot_core
from applypilot.telegram_bot import (
    _build_compteur_message,
    _build_all_apps_page,
    compteur_cmd,
    toutes_cmd,
    on_button,
    OWNER_ID,
)


def test_get_all_applied_jobs_returns_sorted_list():
    jobs = bot_core.get_all_applied_jobs()
    assert isinstance(jobs, list)
    assert len(jobs) > 0
    first = jobs[0]
    for key in ("title", "company", "url", "dt", "time", "date", "date_short", "is_today"):
        assert key in first


def test_build_compteur_message():
    counts = {
        "today_count": 5,
        "total_count": 94,
        "today_jobs": [{"title": "Poste Test", "company": "Entreprise X", "time": "14:30"}],
        "daily_counts": {"2026-11-11": 5},
    }
    msg, markup = _build_compteur_message(counts)
    assert "94" in msg
    assert "Aujourd'hui" in msg
    buttons = [btn for row in markup.inline_keyboard for btn in row]
    callback_data_list = [btn.callback_data for btn in buttons]
    assert "all_apps:1" in callback_data_list
    assert "export_apps_txt" in callback_data_list


def test_build_all_apps_page_pagination():
    mock_jobs = [
        {"title": f"Job {i}", "company": f"Comp {i}", "url": f"https://example.com/{i}",
         "dt": None, "time": "12:00", "date": "11/11/2026", "date_short": "11/11", "is_today": True}
        for i in range(25)
    ]
    # Page 1 sur 3 (page_size = 10)
    msg, markup = _build_all_apps_page(mock_jobs, page=1, page_size=10)
    assert "Page 1/3 — 25 au total" in msg
    assert "Comp 0" in msg
    assert "Comp 9" in msg
    assert "Comp 10" not in msg

    buttons = [btn for row in markup.inline_keyboard for btn in row]
    callbacks = [btn.callback_data for btn in buttons]
    assert "all_apps:2" in callbacks
    assert "back_compteur" in callbacks

    # Page 2
    msg2, markup2 = _build_all_apps_page(mock_jobs, page=2, page_size=10)
    assert "Page 2/3" in msg2
    assert "Comp 10" in msg2
    callbacks2 = [btn.callback_data for row in markup2.inline_keyboard for btn in row]
    assert "all_apps:1" in callbacks2
    assert "all_apps:3" in callbacks2


@pytest.mark.anyio
async def test_compteur_cmd_reply():
    update = MagicMock(spec=Update)
    update.effective_user.id = OWNER_ID
    message = MagicMock(spec=Message)
    message.reply_text = AsyncMock()
    update.message = message
    update.effective_message = message

    context = MagicMock()
    await compteur_cmd(update, context)

    assert message.reply_text.called
    kwargs = message.reply_text.call_args.kwargs
    assert "reply_markup" in kwargs
    assert kwargs["reply_markup"] is not None


@pytest.mark.anyio
async def test_toutes_cmd_reply():
    update = MagicMock(spec=Update)
    update.effective_user.id = OWNER_ID
    message = MagicMock(spec=Message)
    message.reply_text = AsyncMock()
    update.message = message
    update.effective_message = message

    context = MagicMock()
    await toutes_cmd(update, context)

    assert message.reply_text.called
    kwargs = message.reply_text.call_args.kwargs
    assert "reply_markup" in kwargs
    assert "Page 1/" in message.reply_text.call_args[0][0]


@pytest.mark.anyio
async def test_on_button_all_apps_pagination():
    update = MagicMock(spec=Update)
    update.effective_user.id = OWNER_ID
    query = MagicMock(spec=CallbackQuery)
    query.data = "all_apps:2"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update.callback_query = query

    context = MagicMock()
    await on_button(update, context)

    assert query.edit_message_text.called
    assert "Page 2/" in query.edit_message_text.call_args[0][0]


@pytest.mark.anyio
async def test_on_button_back_compteur():
    update = MagicMock(spec=Update)
    update.effective_user.id = OWNER_ID
    query = MagicMock(spec=CallbackQuery)
    query.data = "back_compteur"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update.callback_query = query

    context = MagicMock()
    await on_button(update, context)

    assert query.edit_message_text.called
    assert "Compteur de candidatures" in query.edit_message_text.call_args[0][0]


@pytest.mark.anyio
async def test_on_button_export_apps_txt():
    update = MagicMock(spec=Update)
    update.effective_user.id = OWNER_ID
    query = MagicMock(spec=CallbackQuery)
    query.data = "export_apps_txt"
    query.message.chat_id = OWNER_ID
    query.answer = AsyncMock()
    update.callback_query = query

    context = MagicMock()
    context.bot.send_document = AsyncMock()

    await on_button(update, context)

    assert context.bot.send_document.called
    args, kwargs = context.bot.send_document.call_args
    assert "document" in kwargs
    doc = kwargs["document"]
    content = doc.getvalue().decode("utf-8")
    assert "HISTORIQUE COMPLET" in content
