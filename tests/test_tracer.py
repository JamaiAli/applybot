"""Tests pour le système de liens traceurs (JobNavigator Tracer Links)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from applypilot import config
from applypilot.database import (
    create_cv_tracker,
    get_recent_trackers,
    get_tracker,
    get_tracker_by_job_url,
    get_tracker_stats,
    record_tracker_hit,
)
from applypilot.tracking.tracer import (
    create_tracer_app,
    format_hit_notification,
    get_or_create_tracer,
    parse_user_agent,
)


def test_parse_user_agent():
    assert "iPhone" in parse_user_agent(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15"
    )
    assert "Android" in parse_user_agent(
        "Mozilla/5.0 (Linux; Android 13; SM-S908B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Mobile"
    )
    assert "Windows" in parse_user_agent(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    assert "🤖" in parse_user_agent(
        "Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)"
    )
    assert "🤖" in parse_user_agent(
        "LinkedInBot/1.0 (compatible; Mozilla/5.0; Apache-HttpClient +http://www.linkedin.com)"
    )
    assert parse_user_agent("") == "Inconnu"


def test_create_and_get_tracker(tmp_db):
    conn = tmp_db()
    token = "testtoken123"
    job_url = "https://fr.indeed.com/viewjob?jk=123456"
    company = "Thales"
    title = "Ingénieur DevSecOps"
    target = "https://jamaiali.github.io"

    create_cv_tracker(
        token=token,
        job_url=job_url,
        company=company,
        title=title,
        target_url=target,
        conn=conn,
    )

    tr = get_tracker(token, conn=conn)
    assert tr is not None
    assert tr["token"] == token
    assert tr["company"] == "Thales"
    assert tr["title"] == "Ingénieur DevSecOps"
    assert tr["open_count"] == 0
    assert tr["opened_at"] is None

    by_job = get_tracker_by_job_url(job_url, conn=conn)
    assert by_job is not None
    assert by_job["token"] == token


def test_record_tracker_hit(tmp_db):
    conn = tmp_db()
    token = "hittoken789"
    create_cv_tracker(
        token=token,
        job_url="https://fr.indeed.com/viewjob?jk=999",
        company="Airbus",
        title="Analyste SOC",
        target_url="https://jamaiali.github.io",
        conn=conn,
    )

    # 1er hit
    res1 = record_tracker_hit(
        token=token,
        ip="192.168.1.50",
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        conn=conn,
    )
    assert res1 is not None
    assert res1["open_count"] == 1
    assert res1["opened_at"] is not None
    assert res1["ip"] == "192.168.1.50"

    # 2e hit
    res2 = record_tracker_hit(
        token=token,
        ip="192.168.1.55",
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17)",
        conn=conn,
    )
    assert res2 is not None
    assert res2["open_count"] == 2
    assert res2["opened_at"] == res1["opened_at"]
    assert res2["last_opened_at"] is not None
    assert res2["ip"] == "192.168.1.55"

    stats = get_tracker_stats(conn=conn)
    assert stats["total_trackers"] == 1
    assert stats["opened_trackers"] == 1
    assert stats["total_opens"] == 2

    recent = get_recent_trackers(5, conn=conn)
    assert len(recent) == 1
    assert recent[0]["token"] == token


def test_get_or_create_tracer_idempotent(tmp_db):
    tmp_db()
    offer = {
        "url": "https://fr.indeed.com/viewjob?jk=abcdef",
        "company": "Dassault",
        "title": "Ingénieur Cyber",
    }
    tok1, url1 = get_or_create_tracer(offer)
    assert tok1 in url1

    # Second appel avec la même offre -> doit renvoyer le même lien stable
    tok2, url2 = get_or_create_tracer(offer)
    assert tok1 == tok2
    assert url1 == url2


def test_format_hit_notification():
    tracker = {
        "company": "Airbus",
        "title": "Analyste SOC",
        "ip": "82.65.12.34",
        "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17)",
        "open_count": 1,
        "job_url": "https://fr.indeed.com/job/123",
    }
    notif = format_hit_notification(tracker)
    assert "Airbus" in notif
    assert "Analyste SOC" in notif
    assert "iPhone" in notif
    assert "82.65.12.34" in notif
    assert "n° :</b> 1" in notif


@pytest.mark.anyio
async def test_tracer_http_redirect_and_hit(tmp_db):
    conn = tmp_db()
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()

    token = "webtest123"
    create_cv_tracker(
        token=token,
        job_url="https://fr.indeed.com/viewjob?jk=web1",
        company="Orange Cyberdefense",
        title="Consultant Pentest",
        target_url="https://jamaiali.github.io/portfolio",
        conn=conn,
    )

    app = create_tracer_app(bot=mock_bot)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        # GET /t/{token} (sans suivre automatiquement les redirections 302)
        resp = await client.get(
            f"/t/{token}",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            allow_redirects=False,
        )
        assert resp.status == 302
        assert resp.headers["Location"] == "https://jamaiali.github.io/portfolio"

        # Vérifie que la visite a bien été consignée en base
        tr = get_tracker(token, conn=conn)
        assert tr["open_count"] == 1
        assert "Windows" in parse_user_agent(tr["user_agent"])

        # Vérifie la route health
        health = await client.get("/health")
        assert health.status == 200
        data = await health.json()
        assert data["status"] == "ok"

        # Vérifie /api/candidate
        cand_resp = await client.get("/api/candidate")
        assert cand_resp.status == 200
        cand_data = await cand_resp.json()
        assert cand_data["status"] == "ok"
        assert "personal" in cand_data

        # Vérifie /api/smart-answer avec question Q&A
        qa_resp = await client.post(
            "/api/smart-answer",
            json={"question": "Avez-vous le permis de conduire ?"}
        )
        assert qa_resp.status == 200
        qa_data = await qa_resp.json()
        assert "answer" in qa_data

        # Vérifie /api/smart-select
        sel_resp = await client.post(
            "/api/smart-select",
            json={
                "question": "Niveau d'études",
                "options": [{"value": "1", "text": "Bac"}, {"value": "2", "text": "Bac+5"}]
            }
        )
        assert sel_resp.status == 200
        sel_data = await sel_resp.json()
        assert "ok" in sel_data
    finally:
        await client.close()


def test_cv_latex_embeds_tracer(tmp_db, monkeypatch):
    tmp_db()
    import json
    from applypilot import cv_latex
    from applypilot.database import get_tracker_by_job_url

    # Écrit un experiences.json minimal dans le APP_DIR temporaire
    exp_data = {
        "candidate": {
            "full_name": "Ali Jamai",
            "title": "Élève-ingénieur en cybersécurité",
            "location": "Bourges",
            "phone": "+33 7 00 00 00 00",
            "email": "ali@example.com",
            "linkedin": "https://linkedin.com/in/ali",
            "github": "https://github.com/ali",
            "portfolio": "https://jamaiali.github.io",
        },
        "education": [],
        "skills": {"devops_devsecops": ["Docker", "CI/CD"]},
        "languages": [],
        "certifications": [],
        "achievements": [],
        "experiences": [],
        "projets": [],
    }
    (config.APP_DIR / "experiences.json").write_text(
        json.dumps(exp_data, ensure_ascii=False), encoding="utf-8"
    )

    offer = {
        "url": "https://fr.indeed.com/viewjob?jk=thales123",
        "title": "Ingénieur DevOps Réseaux d'infrastructure",
        "company": "Thales",
    }
    exp = cv_latex.load_experiences()
    sel = cv_latex._default_selection(exp, offer)
    tex = cv_latex._render_tex(exp, sel, offer, 4, 3)

    # Par défaut (localhost), la règle de sécurité ATS empêche l'injection d'URL locale
    # pour éviter que les scanners (Orange/Phenom People, Workday) ne rejettent le CV.
    tracker = get_tracker_by_job_url(offer["url"])
    assert tracker is not None
    expected_token = tracker["token"]
    assert "https://jamaiali.github.io" in tex
    assert "localhost" not in tex

    # Lorsque TRACER_BASE_URL est configuré avec un domaine public HTTPS, le lien est injecté
    monkeypatch.setattr(config, "TRACER_BASE_URL", "https://track.applypilot.fr")
    tex_public = cv_latex._render_tex(exp, sel, offer, 4, 3)
    assert f"/t/{expected_token}" in tex_public
    assert f"\\href{{https://track.applypilot.fr/t/{expected_token}}}{{Portfolio}}" in tex_public

