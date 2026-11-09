import pytest
from pathlib import Path
from pypdf import PdfReader
import fitz  # pymupdf
from applypilot.cv_latex import generate_cv, _render_tex, load_experiences, FIXED_PRO_EXP_IDS, MANDATORY_EXP_IDS

SAMPLE_OFFERS = [
    {
        "title": "Stage - Auditeur Cybersécurité & Conformité GRC",
        "company": "Vinci Energies",
        "location": "Paris",
        "description": "Audit organisationnel ISO 27001, analyse de risques, conformité et revue des habilitations Active Directory.",
    },
    {
        "title": "Stage Pentest & Sécurité Offensive",
        "company": "Wavestone",
        "location": "Paris",
        "description": "Tests d'intrusion web OWASP Top 10, audit interne Active Directory, découverte de vulnérabilités et rédaction de rapports.",
    },
    {
        "title": "Stage Ingénieur Systèmes & Active Directory",
        "company": "Thales",
        "location": "Vélizy",
        "description": "Administration Windows Server 2022, durcissement GPO, sécurité des identités et infrastructure réseau.",
    },
]


def test_fixed_pro_exp_ids_definition():
    assert FIXED_PRO_EXP_IDS == [
        "etude-hydrogene-airbus",
        "shellter",
        "cyberquest",
    ]
    assert "etude-hydrogene-airbus" in MANDATORY_EXP_IDS


@pytest.mark.parametrize("offer", SAMPLE_OFFERS)
def test_experiences_professionnelles_verbatim(offer, monkeypatch):
    import applypilot.cv_latex as cv_mod
    monkeypatch.setattr(cv_mod, "_llm_selection", lambda off, exp_data: None)

    pdf_path = generate_cv(offer)
    assert pdf_path.exists()

    # Exactement 1 page impérative
    reader = PdfReader(str(pdf_path))
    assert len(reader.pages) == 1, f"Le CV généré pour {offer['company']} doit tenir sur 1 seule page, obtenu: {len(reader.pages)}"

    # Vérification textuelle dans le PDF extrait
    doc = fitz.open(str(pdf_path))
    text = doc[0].get_text()

    # Les 3 expériences professionnelles doivent être présentes
    assert "Airbus" in text
    assert "Hambourg" in text
    assert "LCOH" in text or "hydrogène" in text.lower()
    assert "Shellter" in text
    assert "Kubernetes" in text
    assert "SAST/DAST" in text
    assert "CyberQuest" in text
    assert "MENTOR" in text or "gamifiée" in text


def test_exact_latex_section_rendering():
    exp_data = load_experiences()
    sel = {"selected_experience_ids": ["pentest-ad", "pentest-web-dvwa", "pentai", "lab-ad"]}
    tex = _render_tex(exp_data, sel, SAMPLE_OFFERS[0], max_exp=4, max_bullets=2)

    # Vérifie que la section Expériences professionnelles contient les 3 blocs exacts
    assert "\\section{Expériences professionnelles}" in tex
    assert "\\entry{Étude Hydrogène vert \\& SAF --- Airbus $\\times$ Université de Hambourg" in tex
    assert "Avril 2025 -- Juillet 2025" in tex
    assert "Stage DevOps --- Shellter" in tex
    assert "Juillet 2025 -- Août 2025" in tex
    assert "Plateforme CyberQuest --- Pilotage de projet IA \\& Formation" in tex
    assert "Mai 2026" in tex

    # Vérifie les 3 puces pour Airbus
    assert "Conduit une étude technico-économique" in tex
    assert "Modélisé le coût actualisé de l'hydrogène (LCOH)" in tex
    assert "Géré et coordonné une équipe internationale anglophone" in tex

    # Vérifie la puce Shellter
    assert "Conçu et sécurisé une plateforme Kubernetes" in tex
    assert "SAST/DAST" in tex

    # Vérifie la puce CyberQuest
    assert "Piloté la création d'une plateforme de formation gamifiée" in tex
