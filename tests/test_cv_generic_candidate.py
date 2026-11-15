"""Le moteur de CV doit fonctionner pour n'importe quel candidat : avec le fichier d'exemple
fourni dans le dépôt, le CV produit ne contient rien de l'auteur d'origine."""
from pathlib import Path

import fitz  # pymupdf
import pytest

import applypilot.cv_latex as cv_mod

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "experiences.example.json"
STUDENT_EXAMPLE = ROOT / "cv_job_etudiant.example.json"
FOREIGN = ("Ali", "Jamai", "INSA", "Bourges", "Airbus", "avril 2027", "Shellter")


@pytest.fixture
def other_candidate(monkeypatch, tmp_path):
    monkeypatch.setattr(cv_mod, "EXPERIENCES_PATH", EXAMPLE)
    monkeypatch.setattr(cv_mod, "_llm_selection", lambda offer, exp_data: None)
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(cv_mod, "STUDENT_CV_DATA", STUDENT_EXAMPLE)
    monkeypatch.setattr(cv_mod, "STUDENT_CV_PDF", tmp_path / "cv_job_etudiant.pdf")


def test_example_file_is_fictional():
    text = EXAMPLE.read_text(encoding="utf-8")
    assert "Camille Martin" in text and not any(w in text for w in FOREIGN)


def test_cv_for_another_candidate_carries_only_their_data(other_candidate):
    assert cv_mod.cv_prefix() == "CV_C_Martin"
    pdf = cv_mod.generate_cv({"title": "Stage - Analyste SOC H/F", "description": "SOC, SIEM, Wazuh."})
    doc = fitz.open(str(pdf))
    text = doc[0].get_text()

    assert pdf.name == "CV_C_Martin_SOC.pdf" and len(pdf.name) <= 25
    assert doc.page_count == 1
    assert "Camille Martin" in text and "École Exemple" in text
    assert "camille.martin@example.com" in text and "linkedin.com/in/camille-martin-exemple" in text
    assert doc.metadata.get("author") == "Camille Martin"
    assert not [w for w in FOREIGN if w in text]
    # Son stage (type experience_pro) est dans les expériences, ses projets dans les projets.
    pro = text.split("EXPÉRIENCES PROFESSIONNELLES", 1)[1].split("COMPÉTENCES", 1)[0]
    assert "Stage support informatique" in pro and "Wazuh" not in pro
    # Sans LLM, l'accroche est son objectif, tel qu'elle l'a écrit.
    assert "à partir de mars 2027" in " ".join(text.split())


@pytest.mark.parametrize("name, expected", [
    ("Ali Jamai", "CV_Ali_Jamai"),
    ("Camille Martin", "CV_C_Martin"),
    ("Élodie Dupont-Lefèvre", "CV_E_Lefevre"),
    ("Zoé", "CV_Zoe"),
    ("", "CV_Candidat"),
])
def test_cv_prefix_follows_the_candidate_name_and_stays_short(name, expected):
    prefix = cv_mod.cv_prefix({"candidate": {"full_name": name}})
    assert prefix == expected and len(prefix) <= 12


def test_student_cv_for_another_candidate(other_candidate):
    pdf = cv_mod.build_student_cv()
    text = fitz.open(str(pdf))[0].get_text()
    assert "Camille Martin" in text and "Boulangerie Exemple" in text
    assert not [w for w in FOREIGN if w in text]
