"""Tests for Gmail response tracking, IMAP integration, French triage, and Telegram formatting."""

import pytest
from unittest.mock import MagicMock, patch

from applypilot.tracking.triage import triage_email, triage_batch
from applypilot.tracking.gmail_client import (
    check_gmail_setup,
    get_auth_mode,
    _decode_mime_header,
    _parse_email_address,
    _html_to_plain_text,
    _extract_body_and_snippet,
)
from applypilot.telegram_bot import _format_tracking_event


# ---------------------------------------------------------------------------
# 1. Setup & Auth Mode Detection
# ---------------------------------------------------------------------------

def test_check_gmail_setup_missing(monkeypatch):
    """When no GMAIL_USER/APP_PASSWORD or MCP exists, check_gmail_setup returns False."""
    with patch("applypilot.config.load_env"):
        monkeypatch.delenv("GMAIL_USER", raising=False)
        monkeypatch.delenv("GMAIL_EMAIL", raising=False)
        monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
        monkeypatch.delenv("GMAIL_PASSWORD", raising=False)

        with patch("applypilot.tracking.gmail_client.OAUTH_KEYS_PATH") as mock_path:
            mock_path.exists.return_value = False
            ok, msg = check_gmail_setup()
            assert ok is False
            assert "myaccount.google.com/apppasswords" in msg


def test_check_gmail_setup_imap(monkeypatch):
    """When GMAIL_USER and GMAIL_APP_PASSWORD are set, IMAP mode is detected."""
    monkeypatch.setenv("GMAIL_USER", "test.candidat@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")

    assert get_auth_mode() == "imap"
    ok, msg = check_gmail_setup()
    assert ok is True
    assert "test.candidat@gmail.com" in msg


# ---------------------------------------------------------------------------
# 2. French Recruitment Triage
# ---------------------------------------------------------------------------

def test_french_triage_confirmation():
    """Verify French confirmation emails are auto-classified without LLM."""
    email = {
        "subject": "Accusé de réception de votre candidature",
        "sender": "recrutement@bouyguestelecom.fr",
        "snippet": "Nous avons bien reçu votre candidature pour le poste de Stage DevOps. Nous reviendrons vers vous.",
    }
    result = triage_email(email)
    assert result.classification == "confirmation"
    assert result.confidence >= 0.85


def test_french_triage_rejection():
    """Verify French rejection emails are auto-classified without LLM."""
    email = {
        "subject": "Votre candidature chez Société Générale",
        "sender": "no-reply@socgen.com",
        "snippet": "Malheureusement, malgré la qualité de votre parcours, nous avons décidé de poursuivre avec un autre profil.",
    }
    result = triage_email(email)
    assert result.classification == "rejection"
    assert result.confidence >= 0.85


def test_french_triage_interview_sent_to_llm():
    """Verify French interview keywords are prioritized for LLM (date/contact extraction)."""
    email = {
        "subject": "Convocation entretien technique - Thales",
        "sender": "talent@thalesgroup.com",
        "snippet": "Bonjour, nous souhaiterions planifier un entretien visio avec vous la semaine prochaine.",
    }
    result = triage_email(email)
    assert result.classification == "llm_needed"


def test_french_triage_noise():
    """Verify French job alerts and newsletters are flagged as noise."""
    email = {
        "subject": "Nouvelles offres d'emploi recommandées pour vous",
        "sender": "alerte@hellowork.com",
        "snippet": "Découvrez les dernières offres de stage en région Centre-Val de Loire. Se désabonner.",
    }
    result = triage_email(email)
    assert result.classification == "noise"


# ---------------------------------------------------------------------------
# 3. IMAP Parser & MIME Decoder
# ---------------------------------------------------------------------------

def test_decode_mime_header():
    # RFC 2047 encoded French string: "Votre candidature - Échange"
    encoded = "=?UTF-8?B?Vm90cmUgY2FuZGlkYXR1cmUgLSDDiWNoYW5nZQ==?="
    decoded = _decode_mime_header(encoded)
    assert "Votre candidature" in decoded
    assert "Échange" in decoded


def test_parse_email_address():
    raw = '"Recrutement Bouygues" <recrutement@bouyguestelecom.fr>'
    addr, name = _parse_email_address(raw)
    assert addr == "recrutement@bouyguestelecom.fr"
    assert name == "Recrutement Bouygues"


def test_html_to_plain_text():
    html = """
    <html>
      <head><style>p { color: red; }</style></head>
      <body>
        <h2>Bonjour Ali,</h2>
        <p>Nous vous confirmons la r&eacute;ception de votre <b>candidature</b>.</p>
        <br/>
        <a href="https://example.com">Voir le portail</a>
      </body>
    </html>
    """
    text = _html_to_plain_text(html)
    assert "Bonjour Ali," in text
    assert "Nous vous confirmons la réception de votre candidature." in text
    assert "color: red" not in text


# ---------------------------------------------------------------------------
# 4. Telegram Event Formatting
# ---------------------------------------------------------------------------

def test_format_tracking_event_interview():
    ev = {
        "type": "interview",
        "company": "Thales",
        "title": "Stage Ingénieur DevOps",
        "subject": "Entretien visio",
        "sender_name": "Claire Dupont",
        "sender": "claire.dupont@thalesgroup.com",
        "dates": [{"date": "2026-11-15", "description": "14h00"}],
        "action_items": [{"task": "Confirmer par retour de mail"}],
        "summary": "Premier échange RH en visioconférence.",
    }
    msg = _format_tracking_event(ev)
    assert "INVITATION EN ENTRETIEN" in msg
    assert "Thales" in msg
    assert "Stage Ingénieur DevOps" in msg
    assert "Claire Dupont" in msg
    assert "2026-11-15" in msg
    assert "Confirmer par retour de mail" in msg


def test_format_tracking_event_rejection():
    ev = {
        "type": "rejection",
        "company": "Société Générale",
        "title": "Analyste Cybersécurité",
        "summary": "Poste pourvu en interne.",
    }
    msg = _format_tracking_event(ev)
    assert "Réponse négative" in msg
    assert "Société Générale" in msg
    assert "Poste pourvu en interne" in msg


def test_format_tracking_event_ghosted():
    ev = {
        "type": "ghosted",
        "company": "Dassault Aviation",
        "title": "Stage Cloud",
    }
    msg = _format_tracking_event(ev)
    assert "Ghosting" in msg
    assert "Dassault Aviation" in msg


def test_format_date_fr():
    from applypilot.telegram_bot import _format_date_fr
    # RFC 2822
    formatted = _format_date_fr("Thu, 5 Nov 2026 08:00:05 +0000 (UTC)")
    assert "05/11/2026" in formatted
    # ISO
    formatted_iso = _format_date_fr("2026-11-05T14:30:00Z")
    assert "05/11/2026" in formatted_iso
    # Empty
    assert _format_date_fr("") == "Date inconnue"


def test_get_classified_emails(tmp_db):
    """Test retrieving emails by classification."""
    from applypilot.database import store_tracking_email, get_classified_emails

    conn = tmp_db()
    store_tracking_email({
        "email_id": "test_conf_1",
        "job_url": "https://test.com/job1",
        "sender": "rh@test.com",
        "sender_name": "RH Test",
        "subject": "Accusé de réception",
        "received_at": "Thu, 5 Nov 2026 08:00:05 +0000 (UTC)",
        "snippet": "Bien reçu",
        "body_text": "Merci pour votre candidature",
        "classification": "confirmation",
        "extracted_data": "{\"summary\": \"Candidature enregistrée\"}",
    }, conn)

    results = get_classified_emails("confirmation", conn=conn)
    assert len(results) >= 1
    assert results[0]["email_id"] == "test_conf_1"
    assert results[0]["classification"] == "confirmation"

    none_found = get_classified_emails("offer", conn=conn)
    assert len(none_found) == 0
