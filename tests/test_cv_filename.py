import pytest
from pathlib import Path
from pypdf import PdfReader
from applypilot.cv_latex import build_cv_filename, generate_cv


def test_build_cv_filename_nominal():
    offer = {
        "title": "Stage - Auditeur Cybersécurité & Conformité GRC",
        "company": "Vinci Energies",
    }
    filename = build_cv_filename(offer, candidate_name="Ali Jamai")
    assert filename == "CV_Ali_Jamai_Vinci_Energies_Stage_Auditeur_Cybersecurite_Conformite_GRC"


def test_build_cv_filename_strips_gender_and_codes():
    offer = {
        "title": "Stage - Pentester Evaluation LLM F/H (2026-54964)",
        "company": "Orange",
    }
    filename = build_cv_filename(offer, candidate_name="Ali Jamai")
    assert filename == "CV_Ali_Jamai_Orange_Stage_Pentester_Evaluation_LLM"
    assert "F_H" not in filename
    assert "2026" not in filename


def test_build_cv_filename_no_company_duplication():
    offer = {
        "title": "Stage Cybersécurité Offensive chez Airbus",
        "company": "Airbus",
    }
    filename = build_cv_filename(offer, candidate_name="Ali Jamai")
    # Airbus déjà présent dans le titre -> pas de doublon "Airbus_..._Airbus"
    assert filename.count("Airbus") == 1
    assert filename == "CV_Ali_Jamai_Stage_Cybersecurite_Offensive_chez_Airbus"


def test_build_cv_filename_without_company():
    offer = {
        "title": "Stage - Analyste VOC - Cybersécurité - Toulouse H/F",
        "company": "",
    }
    filename = build_cv_filename(offer, candidate_name="Ali Jamai")
    assert filename == "CV_Ali_Jamai_Stage_Analyste_VOC_Cybersecurite_Toulouse"


def test_generate_cv_uses_offer_name(monkeypatch):
    import applypilot.cv_latex as cv_mod
    monkeypatch.setattr(cv_mod, "_llm_selection", lambda off, exp_data: None)

    offer = {
        "title": "Stage - Pentester Sécurité Offensive",
        "company": "Wavestone",
        "location": "Paris",
        "description": "Pentest web et interne AD",
    }
    pdf_path = generate_cv(offer)
    assert pdf_path.exists()
    assert pdf_path.name.startswith("CV_Ali_Jamai_Wavestone_Stage_Pentester_Securite_Offensive")
    assert pdf_path.suffix == ".pdf"
    assert len(PdfReader(str(pdf_path)).pages) == 1
