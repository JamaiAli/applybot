"""Commande /hellowork : scan en temps réel de HelloWork seul (/scan garde les deux sites)."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from applypilot import bot_core, pipeline, telegram_bot


def _update_and_context(args):
    status = SimpleNamespace(edit_text=AsyncMock())
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=1),
        message=SimpleNamespace(reply_text=AsyncMock(return_value=status)),
    )
    context = SimpleNamespace(args=args, bot=SimpleNamespace(send_chat_action=AsyncMock(), send_message=AsyncMock()))
    return update, context, status


@pytest.fixture
def scan_env(monkeypatch):
    calls = {}
    urls = [{"https://example.com/a"}, {"https://example.com/a", "https://example.com/nouvelle"}]
    monkeypatch.setattr(telegram_bot, "_authorized", lambda update: True)
    monkeypatch.setattr(bot_core, "get_all_job_urls", lambda: urls.pop(0) if len(urls) > 1 else urls[0])
    monkeypatch.setattr(pipeline, "_run_discover", lambda **kw: calls.update(discover=kw))
    monkeypatch.setattr(pipeline, "_run_score", lambda **kw: calls.update(scored=True))
    offer = {"url": "https://example.com/nouvelle", "title": "Stage SOC"}
    monkeypatch.setattr(bot_core, "fetch_new_scan_offers", lambda *a, **k: [offer])
    monkeypatch.setattr(bot_core, "mark_notified", lambda urls: calls.update(notified=urls))
    sent = []

    async def fake_send(bot, chat_id, row):
        sent.append(row)
    monkeypatch.setattr(telegram_bot, "_send_offer", fake_send)
    calls["sent"] = sent
    return calls


def test_hellowork_scans_hellowork_only_with_plain_queries(scan_env):
    update, context, status = _update_and_context(["soc"])
    asyncio.run(telegram_bot.hellowork_cmd(update, context))

    discover = scan_env["discover"]
    assert discover["sources"] == ["hellowork"]                      # Indeed n'est pas interrogé
    assert discover["queries"] == ["stage soc", "alternance soc", "soc"]
    assert scan_env["scored"] and scan_env["sent"] and scan_env["notified"] == ["https://example.com/nouvelle"]
    first_message = update.message.reply_text.call_args.args[0]
    assert "HelloWork" in first_message and "Indeed" not in first_message


def test_hellowork_without_argument_uses_default_queries(scan_env):
    update, context, _ = _update_and_context([])
    asyncio.run(telegram_bot.hellowork_cmd(update, context))
    assert scan_env["discover"]["sources"] == ["hellowork"]
    assert scan_env["discover"]["queries"] is None                   # requêtes par défaut de searches.yaml


def test_scan_still_covers_both_sites(scan_env):
    update, context, _ = _update_and_context([])
    asyncio.run(telegram_bot.scan_cmd(update, context))
    assert scan_env["discover"]["sources"] == ["hellowork", "indeed"]
    assert "HelloWork et Indeed" in update.message.reply_text.call_args.args[0]


def test_hellowork_reports_when_nothing_is_new(scan_env, monkeypatch):
    monkeypatch.setattr(bot_core, "get_all_job_urls", lambda: {"https://example.com/a"})
    update, context, status = _update_and_context([])
    asyncio.run(telegram_bot.hellowork_cmd(update, context))
    assert "Aucune nouvelle offre détectée sur HelloWork" in status.edit_text.call_args.args[0]
    assert scan_env["sent"] == []
