import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, Message
from applypilot.discovery.indeed_student import (
    is_truly_remote_job,
    search_remote_student_jobs,
    search_remote_interim_jobs,
)
from applypilot import telegram_bot


def test_is_truly_remote_job_positive():
    # 1. Location Télétravail
    j1 = {
        "title": "Téléconseiller étudiant H/F",
        "location": {"display_name": "Télétravail"},
        "work_setting": "remote",
        "description": "Poste 100% à distance.",
    }
    assert is_truly_remote_job(j1) is True

    # 2. Titre explicite
    j2 = {
        "title": "Professeur de mathématiques en ligne",
        "location": {"display_name": "Paris (75)"},
        "description": "Donnez des cours depuis chez vous.",
    }
    assert is_truly_remote_job(j2) is True

    # 3. Work setting remote avec description confirmant le télétravail
    j3 = {
        "title": "Agent de support client",
        "location": {"display_name": "Lyon (69)"},
        "work_setting": "remote",
        "description": "Mission en télétravail complet.",
    }
    assert is_truly_remote_job(j3) is True


def test_is_truly_remote_job_negative():
    # 1. Métier physique sur site même si marqué remote par erreur
    j1 = {
        "title": "Commis de cuisine (H/F)",
        "location": {"display_name": "Télétravail"},
        "work_setting": "remote",
        "description": "Préparation des plats.",
    }
    assert is_truly_remote_job(j1) is False

    j2 = {
        "title": "Hôte de caisse",
        "location": {"display_name": "Télétravail"},
        "description": "Encaissement clients.",
    }
    assert is_truly_remote_job(j2) is False

    # 2. Stage ou alternance
    j3 = {
        "title": "Stage marketing digital télétravail",
        "location": {"display_name": "Télétravail"},
        "description": "Convention de stage obligatoire.",
    }
    assert is_truly_remote_job(j3) is False

    # 3. Poste physique en DOM-TOM
    j4 = {
        "title": "Employé de bureau",
        "location": {"display_name": "Guadeloupe", "state": "DOM-TOM"},
        "description": "Accueil du public.",
    }
    assert is_truly_remote_job(j4) is False


def test_search_remote_student_and_interim_mock():
    mock_jobs = [
        {
            "id": "1",
            "title": "Tuteur en ligne (H/F)",
            "link": "https://fr.indeed.com/viewjob?jk=1",
            "apply_link": "https://fr.indeed.com/apply?jk=1",
            "company": {"name": "EduOnline"},
            "location": {"display_name": "Télétravail"},
            "work_setting": "remote",
            "description": "100% télétravail.",
            "date_posted": "il y a 1 jour",
        }
    ]

    with patch("applypilot.discovery.indeed._import_jobs") as mock_import:
        mock_mod = MagicMock()
        mock_mod.search.return_value = {"jobs": mock_jobs}
        mock_import.return_value = mock_mod

        res_etu = search_remote_student_jobs(limit=5, max_age_days=14)
        assert len(res_etu) >= 1
        assert res_etu[0]["title"] == "Tuteur en ligne (H/F)"
        assert res_etu[0]["is_remote"] is True

        res_inter = search_remote_interim_jobs(limit=5, max_age_days=14)
        assert len(res_inter) >= 1
        assert res_inter[0]["is_remote"] is True


@pytest.mark.anyio
async def test_jobdistant_cmd_authorized():
    update = MagicMock(spec=Update)
    update.effective_user.id = telegram_bot.OWNER_ID
    update.effective_chat.id = telegram_bot.OWNER_ID
    message = MagicMock(spec=Message)
    message.reply_text = AsyncMock()
    update.message = message
    update.effective_message = message

    context = MagicMock()
    context.bot.send_message = AsyncMock()

    with patch("applypilot.discovery.indeed_student.search_remote_student_jobs", return_value=[{
        "url": "https://example.com/etu1",
        "title": "Tuteur en ligne",
        "employer_name": "Edu",
        "location": "Télétravail",
        "posted_at": "2026-11-13T10:00:00",
        "is_remote": True,
    }]), patch("applypilot.discovery.indeed_student.search_remote_interim_jobs", return_value=[{
        "url": "https://example.com/inter1",
        "title": "Téléconseiller",
        "employer_name": "CallCenter",
        "location": "Télétravail",
        "posted_at": "2026-11-13T11:00:00",
        "is_remote": True,
    }]):
        await telegram_bot.jobdistant_cmd(update, context)

    # Doit avoir envoyé la notification initiale et les 2 listes
    assert context.bot.send_message.called
    calls = context.bot.send_message.call_args_list
    # Au moins la notification initiale + 2 messages pour les offres
    assert len(calls) >= 3
    all_texts = " ".join(call.args[1] if len(call.args) > 1 else str(call.kwargs.get("text", "")) for call in calls)
    assert "télétravail" in all_texts.lower() or "distance" in all_texts.lower()
