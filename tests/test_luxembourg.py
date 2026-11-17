from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace
import pytest

from applypilot import telegram_bot
from applypilot.discovery import moovijob
from applypilot.discovery import indeed


def test_parse_search_args_luxembourg():
    """Vérifie que Luxembourg est bien reconnu comme ville/pays par _parse_search_args."""
    metier, city, limit = telegram_bot._parse_search_args(["luxembourg"])
    assert city == "luxembourg"
    assert metier is None
    assert limit == telegram_bot.MAX_CARDS

    metier, city, limit = telegram_bot._parse_search_args(["stage", "luxembourg"])
    assert city == "luxembourg"
    assert metier == "stage"

    metier, city, limit = telegram_bot._parse_search_args(["cyber", "luxembourg", "5"])
    assert city == "luxembourg"
    assert metier == "cyber"
    assert limit == 5


def test_indeed_search_all_luxembourg_uses_country_lu():
    """Vérifie que pour Luxembourg, Indeed utilise bien country='LU'."""
    mock_jobs_mod = MagicMock()
    mock_jobs_mod.search.return_value = {"jobs": []}

    indeed._search_all(
        mock_jobs_mod,
        query="stage",
        location="Luxembourg",
        pages=1,
        page_kw="page",
        delay_min=0,
        delay_max=0,
    )

    mock_jobs_mod.search.assert_called_once()
    kwargs = mock_jobs_mod.search.call_args[1]
    assert kwargs["country"] == "LU"
    assert kwargs["location"] == "Luxembourg"


def test_filter_and_tag_luxembourg_allows_all_internships():
    """Vérifie que filter_cyber=False conserve les offres de stages variés pour le Luxembourg."""
    records = [
        {
            "job": {
                "title": "Machine Learning Intern",
                "description": "6 months internship in AI and data science in Luxembourg",
                "link": "https://lu.indeed.com/viewjob?jk=123",
            },
            "domaines_req": ["Stages Luxembourg"],
        },
        {
            "job": {
                "title": "Senior Cloud Architect",
                "description": "CDI 10 years experience",
                "link": "https://lu.indeed.com/viewjob?jk=456",
            },
            "domaines_req": ["Stages Luxembourg"],
        },
    ]

    kept = indeed.filter_and_tag(records, keep_alt=False, filter_cyber=False)
    assert len(kept) == 1
    assert kept[0]["job"]["title"] == "Machine Learning Intern"


@pytest.mark.anyio
async def test_luxembourg_cmd():
    """Vérifie que la commande /luxembourg lance le scraping rapide Moovijob et diffuse les portails."""
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=987),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )
    context = SimpleNamespace(
        args=[],
        bot=SimpleNamespace(send_message=AsyncMock()),
    )

    sample_offers = [{
        "url": "https://www.moovijob.com/offres-emploi/bank/stage-it",
        "title": "Stage IT",
        "employer_name": "Banque de Luxembourg",
        "location": "Luxembourg",
        "posted_at": "2026-11-17T12:00:00",
    }]

    with patch.object(telegram_bot, "_authorized", return_value=True), \
         patch("applypilot.discovery.moovijob.scrape_moovijob_offers", return_value=sample_offers), \
         patch.object(telegram_bot, "_send_job_list", new_callable=AsyncMock) as mock_send_list:

        await telegram_bot.luxembourg_cmd(update, context)

        mock_send_list.assert_called_once()
        assert context.bot.send_message.call_count >= 2
        portails_msg = context.bot.send_message.call_args_list[-1][0][1]
        assert "Moovijob" in portails_msg
        assert "Student.lu" in portails_msg
        assert "ICTJob.lu" in portails_msg
        assert "Jobs.lu" in portails_msg
        assert "Paperjam" in portails_msg
        assert "ADEM" in portails_msg


def test_moovijob_html_parsing():
    sample_html = """
    <div class="card-job-offer-new" href="/offres-emploi/bank/stage-it-123">
        <a class="card-job-offer-new-title">Stage Développeur Python</a>
        <img alt="Banque Luxembourg logo" />
        <span class="badge">Findel</span>
        <span class="badge">Stage</span>
    </div>
    """
    with patch("applypilot.discovery.moovijob.sync_playwright") as mock_pw:
        mock_browser = mock_pw.return_value.__enter__.return_value.chromium.launch.return_value
        mock_page = mock_browser.new_page.return_value
        mock_page.content.return_value = sample_html
        
        offers = moovijob.scrape_moovijob_offers()
        assert len(offers) == 1
        assert offers[0]["title"] == "Stage Développeur Python"
        assert offers[0]["employer_name"] == "Banque Luxembourg"
        assert offers[0]["location"] == "Findel, Luxembourg"
        assert offers[0]["source"] == "moovijob"
        assert offers[0]["url"] == "https://www.moovijob.com/offres-emploi/bank/stage-it-123"
