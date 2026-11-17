from unittest.mock import AsyncMock, patch
import pytest
from types import SimpleNamespace
from applypilot import telegram_bot
from applypilot.discovery import sweden


def test_platsbanken_parsing():
    sample_response = {
        "total": {"value": 2},
        "hits": [
            {
                "id": "31723959",
                "headline": "Internship Logistics Procurement Planning",
                "employer": {"name": "Bosch Thermoteknik AB"},
                "workplace_address": {"municipality": "Tranås", "country": "Sverige"},
                "publication_date": "2026-11-12T18:17:15",
                "webpage_url": "https://arbetsformedlingen.se/platsbanken/annonser/31723959",
            },
            {
                "id": "31693185",
                "headline": "Cyber Security Trainee",
                "employer": {"name": "Ericsson AB"},
                "workplace_address": {"municipality": "Stockholm", "country": "Sverige"},
                "publication_date": "2026-11-15T10:00:00",
                "webpage_url": "https://arbetsformedlingen.se/platsbanken/annonser/31693185",
            },
        ],
    }

    with patch("httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = sample_response

        res = sweden.scrape_platsbanken_offers(query=None, limit=10)
        # Seul le stage cybersécurité doit être conservé (la logistique est écartée)
        assert len(res) == 1
        assert res[0]["title"] == "Cyber Security Trainee"
        assert res[0]["employer_name"] == "Ericsson AB"
        assert "Stockholm, Suède" in res[0]["location"]
        assert res[0]["source"] == "platsbanken"
        assert "31693185" in res[0]["url"]


@pytest.mark.anyio
async def test_suede_cmd():
    """Vérifie que la commande /suede lance le scraping Platsbanken et diffuse l'annuaire des 10 sites."""
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=999),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )
    context = SimpleNamespace(
        args=[],
        bot=SimpleNamespace(send_message=AsyncMock()),
    )

    sample_offers = [{
        "url": "https://arbetsformedlingen.se/platsbanken/annonser/123",
        "title": "Internship Software Engineer",
        "employer_name": "Volvo Group",
        "location": "Göteborg, Suède",
        "posted_at": "2026-11-17T12:00:00",
        "source": "platsbanken",
    }]

    with patch.object(telegram_bot, "_authorized", return_value=True), \
         patch("applypilot.discovery.sweden.scrape_sweden_offers", return_value=sample_offers), \
         patch.object(telegram_bot, "_send_stage_list", new_callable=AsyncMock) as mock_send_list:

        await telegram_bot.suede_cmd(update, context)

        mock_send_list.assert_called_once()
        assert context.bot.send_message.call_count >= 2
        portails_msg = context.bot.send_message.call_args_list[-1][0][1]

        # Les 10 portails demandés par l'utilisateur
        assert "Platsbanken" in portails_msg
        assert "Traineeguiden" in portails_msg
        assert "JobTeaser" in portails_msg
        assert "Academic Work" in portails_msg
        assert "Academic Positions" in portails_msg
        assert "Graduateland" in portails_msg
        assert "Jobs in Stockholm" in portails_msg
        assert "LinkedIn Jobs" in portails_msg
        assert "Indeed" in portails_msg
        assert "Jobbsafari" in portails_msg


@pytest.mark.anyio
async def test_search_routing_suede():
    """Vérifie que /search cyber suede est redirigé vers suede_cmd."""
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=999),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )
    context = SimpleNamespace(
        args=["cyber", "suede"],
        bot=SimpleNamespace(send_message=AsyncMock()),
    )

    with patch.object(telegram_bot, "_authorized", return_value=True), \
         patch.object(telegram_bot, "suede_cmd", new_callable=AsyncMock) as mock_suede:

        await telegram_bot.search_cmd(update, context)

        mock_suede.assert_called_once()
        assert context.args == ["cyber"]
