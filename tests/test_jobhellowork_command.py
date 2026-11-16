import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from applypilot.discovery.hellowork import (
    search_hellowork_student_jobs,
    search_hellowork_interim_jobs,
    EXCLU_STAGE_RE,
    EXCLU_QUALIF_RE,
)
from applypilot import telegram_bot, bot_core


def test_filters_exclude_stage_and_qualified():
    assert EXCLU_STAGE_RE.search("Stage Développeur Python")
    assert EXCLU_STAGE_RE.search("Alternance Assistant RH")
    assert EXCLU_STAGE_RE.search("Stagiaire communication")
    assert EXCLU_STAGE_RE.search("Apprenti électricien")
    assert not EXCLU_STAGE_RE.search("Hôte de caisse temps partiel")
    assert not EXCLU_STAGE_RE.search("Équipier polyvalent de restauration")

    assert EXCLU_QUALIF_RE.search("Infirmier de nuit H/F")
    assert EXCLU_QUALIF_RE.search("Médecin coordonnateur")
    assert EXCLU_QUALIF_RE.search("Vétérinaire canin")
    assert not EXCLU_QUALIF_RE.search("Vendeur prêt-à-porter")
    assert not EXCLU_QUALIF_RE.search("Préparateur de commandes")


def test_search_hellowork_student_jobs_parsing():
    fake_html = """
    <ul>
      <li data-cy="offer-card">
        <a href="/fr-fr/emplois/123456.html" title="Hôte de Caisse H/F - E.Leclerc"
           aria-label="Voir offre de Hôte de Caisse H/F à Saint-Doulchard - 18, chez E.Leclerc">
          Hôte de Caisse H/F - E.Leclerc
        </a>
        <div class="meta">
          <span>Saint-Doulchard - 18</span>
          <span>hier</span>
        </div>
      </li>
      <li data-cy="offer-card">
        <a href="/fr-fr/emplois/234567.html" title="Stage Marketing - BrandCo"
           aria-label="Voir offre de Stage Marketing à Bourges - 18, chez BrandCo">
          Stage Marketing - BrandCo
        </a>
        <div class="meta">
          <span>aujourd'hui</span>
        </div>
      </li>
      <li data-cy="offer-card">
        <a href="/fr-fr/emplois/345678.html" title="Infirmier H/F - Clinique"
           aria-label="Voir offre de Infirmier H/F à Bourges - 18, chez Clinique">
          Infirmier H/F - Clinique
        </a>
        <div class="meta">
          <span>aujourd'hui</span>
        </div>
      </li>
      <li data-cy="offer-card">
        <a href="/fr-fr/emplois/456789.html" title="Aide à Domicile H/F - Vitalliance"
           aria-label="Voir offre de Aide à Domicile H/F à Mehun-sur-Yèvre - 18, chez Vitalliance">
          Aide à Domicile H/F - Vitalliance
        </a>
        <div class="meta">
          <span>aujourd'hui</span>
        </div>
      </li>
    </ul>
    """

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = fake_html

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        with patch("applypilot.bot_core.is_job_decided", return_value=False):
            jobs = search_hellowork_student_jobs(location="Bourges", limit=10, max_age_days=14, queries=["test"])

            assert len(jobs) == 2
            # Offre du jour (Aide à Domicile) en premier
            assert jobs[0]["title"] == "Aide à Domicile H/F"
            assert jobs[0]["employer_name"] == "Vitalliance"
            assert jobs[0]["location"] == "Mehun-sur-Yèvre - 18"
            assert jobs[0]["anciennete_jours"] == 0

            # Offre d'hier (Hôte de Caisse) en second
            assert jobs[1]["title"] == "Hôte de Caisse H/F"
            assert jobs[1]["employer_name"] == "E.Leclerc"
            assert jobs[1]["location"] == "Saint-Doulchard - 18"
            assert jobs[1]["anciennete_jours"] == 1


def test_search_hellowork_interim_jobs_parsing():
    fake_html = """
    <ul>
      <li data-cy="offer-card">
        <a href="/fr-fr/emplois/987654.html" title="Manutentionnaire H/F - Triangle Intérim"
           aria-label="Voir offre de Manutentionnaire H/F à Bourges - 18, chez Triangle Intérim">
          Manutentionnaire H/F - Triangle Intérim
        </a>
        <div class="meta">
          <span>Bourges - 18</span>
          <span>aujourd'hui</span>
        </div>
      </li>
    </ul>
    """

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = fake_html

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        with patch("applypilot.bot_core.is_job_decided", return_value=False):
            jobs = search_hellowork_interim_jobs(location="Bourges", limit=5, max_age_days=14, queries=["manutentionnaire"])
            assert len(jobs) == 1
            assert jobs[0]["title"] == "Manutentionnaire H/F"
            assert jobs[0]["employer_name"] == "Triangle Intérim"


@pytest.mark.anyio
async def test_jobhellowork_cmd_default_bourges_and_saint_doulchard():
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=123),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )
    context = SimpleNamespace(
        args=[],
        bot=SimpleNamespace(send_message=AsyncMock()),
    )

    mock_etu = [
        {
            "url": "https://www.hellowork.com/fr-fr/emplois/123.html",
            "title": "Employé polyvalent",
            "employer_name": "McDo",
            "location": "Bourges",
            "posted_at": "2026-11-13T10:00",
            "anciennete_jours": 0,
        }
    ]
    mock_interim = [
        {
            "url": "https://www.hellowork.com/fr-fr/emplois/456.html",
            "title": "Manutentionnaire",
            "employer_name": "Adecco",
            "location": "Saint-Doulchard",
            "posted_at": "2026-11-13T10:00",
            "anciennete_jours": 0,
        }
    ]

    with patch.object(telegram_bot, "_authorized", return_value=True), \
         patch("applypilot.discovery.hellowork.search_hellowork_student_jobs", return_value=mock_etu) as mock_search_etu, \
         patch("applypilot.discovery.hellowork.search_hellowork_interim_jobs", return_value=mock_interim) as mock_search_interim, \
         patch.object(telegram_bot, "_send_job_list", new_callable=AsyncMock) as mock_send_list:

        await telegram_bot.jobhellowork_cmd(update, context)

        mock_search_etu.assert_called_once_with(["Bourges", "Saint-Doulchard"], 10, 14)
        mock_search_interim.assert_called_once_with(["Bourges", "Saint-Doulchard"], 10, 14)
        assert mock_send_list.call_count == 2
        
        # 1ère liste : jobs étudiants
        titre_etu = mock_send_list.call_args_list[0][0][2]
        assert "Jobs étudiants" in titre_etu
        assert "Bourges & Saint-Doulchard" in titre_etu

        # 2ème liste : missions intérim
        titre_interim = mock_send_list.call_args_list[1][0][2]
        assert "Missions intérim" in titre_interim
        assert "Bourges & Saint-Doulchard" in titre_interim


@pytest.mark.anyio
async def test_jobhellowork_cmd_custom_city():
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=123),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )
    context = SimpleNamespace(
        args=["Vierzon"],
        bot=SimpleNamespace(send_message=AsyncMock()),
    )

    with patch.object(telegram_bot, "_authorized", return_value=True), \
         patch("applypilot.discovery.hellowork.search_hellowork_student_jobs", return_value=[]) as mock_search_etu, \
         patch("applypilot.discovery.hellowork.search_hellowork_interim_jobs", return_value=[]) as mock_search_interim, \
         patch.object(telegram_bot, "_send_job_list", new_callable=AsyncMock) as mock_send_list:

        await telegram_bot.jobhellowork_cmd(update, context)

        mock_search_etu.assert_called_once_with(["Vierzon"], 10, 14)
        mock_search_interim.assert_called_once_with(["Vierzon"], 10, 14)
        mock_send_list.assert_not_called()
        # Fallback message sent when empty
        assert context.bot.send_message.call_count >= 2
        last_msg = context.bot.send_message.call_args_list[-1][0][1]
        assert "Aucune offre récente" in last_msg
        assert "Vierzon" in last_msg
