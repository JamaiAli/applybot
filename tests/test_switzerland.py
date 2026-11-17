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
    with patch("applypilot.discovery.switzerland._fetch_swissdevjobs_json", return_value=sample_jobs):
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
         patch.object(telegram_bot, "_send_stage_list", new_callable=AsyncMock) as mock_send_list:

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


@pytest.mark.anyio
async def test_send_stage_list_creates_stage_cards_not_student_jobs():
    """Vérifie que _send_stage_list génère des boutons prep: (stage ingénieur) et non jobapply: (job étudiant)."""
    mock_bot = SimpleNamespace(send_message=AsyncMock())
    sample_jobs = [{
        "url": "https://swissdevjobs.ch/jobs/kpmg-cyber",
        "title": "Internship - Cyber Security",
        "employer_name": "KPMG Suisse",
        "location": "Genève, Suisse",
        "posted_at": "2026-11-17T12:00:00",
        "source": "swissdevjobs",
    }]

    with patch.object(telegram_bot, "_save_stage_job") as mock_save, \
         patch("applypilot.bot_core.is_job_decided", return_value=False), \
         patch("applypilot.bot_core.set_card_message"):

        await telegram_bot._send_stage_list(mock_bot, 12345, "🇨🇭 Offres Suisse", sample_jobs)

        mock_save.assert_called_once()
        # Vérifie l'envoi du message d'en-tête + message de l'offre
        assert mock_bot.send_message.call_count == 2
        card_call = mock_bot.send_message.call_args_list[1]
        markup = card_call[1]["reply_markup"]

        # Récupère tous les boutons du clavier
        all_buttons = [btn for row in markup.inline_keyboard for btn in row]
        button_texts = [b.text for b in all_buttons]
        button_callbacks = [b.callback_data for b in all_buttons if b.callback_data]

        assert "📨 Préparer ma candidature" in button_texts
        assert any(cb.startswith("prep:") for cb in button_callbacks)
        # S'assurer qu'aucun callback jobapply (job étudiant) n'est présent
        assert not any(cb.startswith("jobapply:") for cb in button_callbacks)
        assert any(b.url == "https://swissdevjobs.ch/jobs/kpmg-cyber" for b in all_buttons)

