import pytest
from pathlib import Path
from pypdf import PdfReader
from applypilot.cv_latex import build_cv_filename, generate_cv


SAMPLE_OFFERS = [
    ({"title": "STAGE - Ingénieur Cybersécurité GRC F/H", "company": "Thales"}, "CV_Ali_Jamai_GRC"),
    ({"title": "Stage - Pentester Evaluation LLM F/H (2026-54964)", "company": "Orange"}, "CV_Ali_Jamai_Pentest"),
    ({"title": "Stage - Auditeur Cybersécurité & Conformité GRC", "company": "Vinci Energies"}, "CV_Ali_Jamai_Audit"),
    ({"title": "Analyste Cybersecurite - IT (Systèmes d'information)", "company": "Thales"}, "CV_Ali_Jamai_Cyber"),
    ({"title": "Stage - Ingénieur Réseaux & Cybersécurité H/F", "company": "Sopra Steria"}, "CV_Ali_Jamai_Reseau"),
    ({"title": "Devops Cloud Infra", "company": "Wavestone"}, "CV_Ali_Jamai_DevOps"),
    ({"title": "Stage Déploiement Active Directory", "company": "Capgemini"}, "CV_Ali_Jamai_AD"),
    ({"title": "Analyste SOC Détection & Réponse aux Incidents", "company": "Thales"}, "CV_Ali_Jamai_SOC"),
    ({"title": "Stage IA et Cybersécurité", "company": "Airbus"}, "CV_Ali_Jamai_IA"),
    ({"title": "Stage Cryptographie Appliquée", "company": "ANSSI"}, "CV_Ali_Jamai_Cryptogr"),
    ({"title": "", "company": ""}, "CV_Ali_Jamai_Cyber"),
]


@pytest.mark.parametrize("offer, expected_base", SAMPLE_OFFERS)
def test_build_cv_filename_short_and_ats_safe(offer, expected_base):
    filename = build_cv_filename(offer)
    assert filename == expected_base
    full_pdf_name = f"{filename}.pdf"
    # Doit être STRICTEMENT <= 25 caractères pour être accepté partout sans exception (Thales/Workday, etc.)
    assert len(full_pdf_name) <= 25, f"Filename {full_pdf_name} est trop long ({len(full_pdf_name)} car > 25)"


def test_generate_cv_short_filename(monkeypatch):
    import applypilot.cv_latex as cv_mod
    monkeypatch.setattr(cv_mod, "_llm_selection", lambda off, exp_data: None)

    offer = {
        "title": "STAGE - Ingénieur Cybersécurité GRC F/H",
        "company": "Thales",
        "location": "Vélizy",
        "description": "Analyse de risques ISO 27001 et GRC",
    }
    pdf_path = generate_cv(offer)
    assert pdf_path.exists()
    assert pdf_path.name == "CV_Ali_Jamai_GRC.pdf"
    assert len(pdf_path.name) <= 25
    assert len(PdfReader(str(pdf_path)).pages) == 1
