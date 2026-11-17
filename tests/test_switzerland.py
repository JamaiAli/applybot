from unittest.mock import AsyncMock, patch
import pytest
from types import SimpleNamespace
from applypilot import telegram_bot
from applypilot.discovery import switzerland


def test_swissdevjobs_parsing():
    sample_jobs = [
        {
            "name": "Internship - Cyber Security",
            "company": "KPMG Switzerland",
            "cityCategory": "Geneva",
            "jobUrl": "kpmg-cyber-security",
            "seniority": "Junior",
            "jobType": "Internship",
            "technologies": ["Cyber Security", "Python"],
        },
        {
            "name": "Senior Java Architect",
            "company": "UBS",
            "cityCategory": "Zurich",
            "jobUrl": "ubs-senior-java",
            "seniority": "Lead",
            "jobType": "Full-Time",
            "technologies": ["Java"],
        }
    ]
    with patch("httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = sample_jobs

        res = switzerland.scrape_swissdevjobs(query=None)
        assert len(res) == 1
        assert res[0]["title"] == "Internship - Cyber Security"
        assert res[0]["employer_name"] == "KPMG Switzerland"
        assert "Geneva" in res[0]["location"]
        assert res[0]["source"] == "swissdevjobs"
        assert "kpmg-cyber-security" in res[0]["url"]


@pytest.mark.anyio
async def test_suisse_cmd():
    """Vérifie que la commande /suisse lance le scraping suisse et diffuse l'annuaire."""
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=987),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )
    context = SimpleNamespace(
        args=[],
        bot=SimpleNamespace(send_message=AsyncMock()),
    )

    sample_offers = [{
        "url": "https://www.jobup.ch/fr/emplois/detail/123",
        "title": "Stage Développeur",
        "employer_name": "Swissquote Bank",
        "location": "Gland, Suisse",
        "posted_at": "2026-11-17T12:00:00",
    }]

    with patch.object(telegram_bot, "_authorized", return_value=True), \
         patch("applypilot.discovery.switzerland.scrape_swiss_offers", return_value=sample_offers), \
         patch.object(telegram_bot, "_send_job_list", new_callable=AsyncMock) as mock_send_list:

        await telegram_bot.suisse_cmd(update, context)

        mock_send_list.assert_called_once()
        assert context.bot.send_message.call_count >= 2
        portails_msg = context.bot.send_message.call_args_list[-1][0][1]
        assert "Jobs.ch" in portails_msg
        assert "Jobup" in portails_msg
        assert "JobScout24" in portails_msg
        assert "Jobwinner" in portails_msg
        assert "ICTcareer" in portails_msg
        assert "ITJobs" in portails_msg
        assert "SwissDevJobs" in portails_msg
        assert "INGJobs" in portails_msg
        assert "Academic Positions" in portails_msg
        assert "eFinancialCareers" in portails_msg
