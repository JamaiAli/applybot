from pathlib import Path
import pytest
from aiohttp.test_utils import TestServer, TestClient

from applypilot import bot_core, config, database
from applypilot.tracking import tracer


@pytest.fixture(autouse=True)
def ensure_dummy_student_cv(tmp_path, monkeypatch):
    """S'assure qu'un fichier cv_job_etudiant.pdf existe pour les tests."""
    app_dir = tmp_path / "applypilot_test"
    app_dir.mkdir(parents=True, exist_ok=True)
    db_path = app_dir / "jobs.db"
    student_cv = app_dir / "cv_job_etudiant.pdf"
    student_cv.write_bytes(b"%PDF-1.4 dummy student resume content")

    cyber_dir = app_dir / "cv_latex"
    cyber_dir.mkdir(parents=True, exist_ok=True)
    cyber_cv = cyber_dir / "CV_Ali_Jamai_Cyber.pdf"
    cyber_cv.write_bytes(b"%PDF-1.4 dummy cyber resume content")

    monkeypatch.setattr(config, "APP_DIR", app_dir)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)
    if hasattr(database._local, "connections"):
        database._local.connections.clear()

    monkeypatch.setattr(config, "RESUME_PDF_PATH", cyber_cv)
    monkeypatch.setattr(bot_core, "_LAST_PREPARED", app_dir / "last_prepared.txt")
    monkeypatch.setattr(bot_core, "_LAST_PREPARED_CV", app_dir / "last_prepared_cv.txt")
    monkeypatch.setattr(bot_core, "_LAST_PREPARED_TYPE", app_dir / "last_prepared_type.txt")
    monkeypatch.setattr(bot_core, "_LAST_PREPARED_TIME", app_dir / "last_prepared_time.txt")
    monkeypatch.setattr(bot_core, "_STUDENT_JOBS_PATH", app_dir / "student_jobs.json")

    database.init_db(db_path)


def test_is_student_job_detection():
    # 1. URL avec mots-clés job étudiant
    student_url = "https://fr.indeed.com/viewjob?jk=abcdef1234567890&q=equipier+polyvalent"
    assert bot_core.is_student_job(student_url) is True

    # 2. URL sans mots-clés dans l'URL mais marquée via mark_student_job
    custom_url = "https://www.custom-co-bourges.fr/emploi/poste-999"
    assert bot_core.is_student_job(custom_url) is False
    bot_core.mark_student_job(custom_url, title="Agent d'accueil H/F", employer="CustomCo")
    assert bot_core.is_student_job(custom_url) is True

    # 3. Offre cyber qui ne doit PAS être détectée comme student job
    cyber_url = "https://orange.jobs/stage-securite-multi-agents-2027"
    assert bot_core.is_student_job(cyber_url) is False


def test_resolve_cv_path_student_vs_cyber():
    student_url = "https://fr.indeed.com/viewjob?jk=abcdef1234567890&q=equipier+polyvalent"
    cyber_url = "https://orange.jobs/stage-securite-multi-agents-2027"

    # Marquer l'URL étudiante
    bot_core.mark_student_job(student_url, title="Équipier polyvalent", employer="McDonald's")

    # Résolution pour le job étudiant
    p_student = tracer.resolve_cv_path(student_url)
    assert p_student is not None
    assert p_student.name == "cv_job_etudiant.pdf"

    # Résolution pour l'offre cyber
    p_cyber = tracer.resolve_cv_path(cyber_url)
    assert p_cyber is not None
    assert "Cyber" in p_cyber.name


def test_get_candidate_data_student_fields():
    student_url = "https://fr.indeed.com/viewjob?jk=fedcba0987654321&q=manutentionnaire+etudiant"
    bot_core.mark_student_job(student_url, title="Manutentionnaire étudiant", employer="Adecco")

    data = tracer.get_candidate_data(student_url)
    assert data["has_cv"] is True
    assert data["cv_name"] == "CV_Ali_Jamai_Job_Etudiant.pdf"
    assert "Job étudiant" in data["qa"]["type_contrat"]
    assert "Immédiate" in data["qa"]["disponibilite"]


@pytest.mark.anyio
async def test_api_candidate_and_get_cv_endpoints():
    app = tracer.create_tracer_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        student_url = "https://fr.indeed.com/viewjob?jk=9876543210abcdef&q=hote+caisse"
        bot_core.mark_student_job(student_url, title="Hôte de caisse étudiant", employer="Auchan")

        # 1. Test GET /api/candidate?url=...
        resp = await client.get(f"/api/candidate?url={student_url}")
        assert resp.status == 200
        data = await resp.json()
        assert "Job_Etudiant.pdf" in data["cv_name"]
        assert data["personal"]["full_name"] in ("Candidat", "Ali Jamai")

        # 2. Test GET /api/get-cv?url=...
        cv_resp = await client.get(f"/api/get-cv?url={student_url}")
        assert cv_resp.status == 200
        assert cv_resp.content_type == "application/pdf"
        disp = cv_resp.headers.get("Content-Disposition", "")
        assert "Job_Etudiant.pdf" in disp
        body = await cv_resp.read()
        assert b"dummy student resume content" in body

        # 3. Test GET /api/cv?url=... (pour téléchargement direct)
        cv_resp2 = await client.get(f"/api/cv?url={student_url}")
        assert cv_resp2.status == 200
        assert "Job_Etudiant.pdf" in cv_resp2.headers.get("Content-Disposition", "")
    finally:
        await client.close()
