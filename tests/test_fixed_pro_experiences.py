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

    # Airbus et Shellter sont sur tous les CV
    assert "Airbus" in text
    assert "Hambourg" in text
    assert "LCOH" in text or "hydrogène" in text.lower()
    assert "Shellter" in text
    assert "Kubernetes" in text
    assert "SAST/DAST" in text
    # CyberQuest ne sort que sur les branches IA, GRC et Cyber (ici : Audit, Pentest, AD)
    assert "CyberQuest" not in text
    # Bas de page allégé : plus de savoir-être ni de loisirs, CTF conservé
    assert "Savoir-être" not in text and "Loisirs" not in text
    assert "4e/42" in text


@pytest.mark.parametrize("title, expected", [
    ("Stage - Ingénieur IA & Cybersécurité", True),
    ("Stage - Consultant Cybersécurité GRC", True),
    ("Stage - Ingénieur Cybersécurité", True),
    ("Stage - Analyste SOC", False),
    ("Stage - Pentester", False),
    ("Stage - Ingénieur Réseaux & Sécurité", False),
])
def test_cyberquest_only_on_relevant_branches(title, expected):
    exp_data = load_experiences()
    sel = {"selected_experience_ids": ["pentest-ad", "lab-ad"]}
    tex = _render_tex(exp_data, sel, {"title": title}, max_exp=4, max_bullets=2)
    assert ("Plateforme CyberQuest" in tex) is expected
    assert "Stage DevOps --- Shellter" in tex and "Airbus" in tex


@pytest.mark.parametrize("title, expected", [
    ("Stage - Consultant Cybersécurité GRC", True),
    ("Stage - Auditeur Cybersécurité", True),
    ("Stage - Pentester", False),
    ("Stage - Analyste SOC", False),
])
def test_writing_distinction_only_on_grc_and_audit(title, expected):
    exp_data = load_experiences()
    tex = _render_tex(exp_data, {"selected_experience_ids": ["pentest-ad"]}, {"title": title}, 4, 2)
    assert ("Concours de Nouvelles" in tex) is expected
    assert "CTF Cybersup" in tex                       # le résultat de CTF, lui, est partout
    assert "\\section{Certifications, CTF \\& Langues}" in tex
    assert "\\section{Engagement}" not in tex and "Loisirs" not in tex


def test_skills_block_is_four_lines_of_six_items_at_most():
    exp_data = load_experiences()
    tex = _render_tex(exp_data, {"selected_experience_ids": ["pentest-ad"],
                                 "skills_order": list(exp_data["skills"])}, {"title": "Stage - Pentester"}, 4, 2)
    block = tex.split("\\section{Compétences techniques}", 1)[1].split("\\section{", 1)[0]
    lines = [l for l in block.splitlines() if l.startswith("\\lbl{")]
    assert len(lines) == 4
    for line in lines:
        assert line.split("}", 1)[1].count(",") <= 5


def test_exact_latex_section_rendering():
    exp_data = load_experiences()
    sel = {"selected_experience_ids": ["pentest-ad", "pentest-web-dvwa", "pentai", "lab-ad"]}
    # Offre IA : les trois expériences fixes sont présentes (CyberQuest inclus).
    tex = _render_tex(exp_data, sel, {"title": "Stage - Ingénieur IA & Cybersécurité"}, max_exp=4, max_bullets=2)

    # Vérifie que la section Expériences professionnelles contient les 3 blocs exacts
    assert "\\section{Expériences professionnelles}" in tex
    assert "\\entry{Étude hydrogène vert \\& SAF --- Airbus $\\times$ Université de Hambourg" in tex
    # Dates au format unique MM/AAAA
    assert "04/2025 -- 07/2025" in tex
    assert "Stage DevOps --- Shellter" in tex
    assert "07/2025 -- 08/2025" in tex
    assert "Plateforme CyberQuest --- pilotage de projet IA \\& formation" in tex
    assert "05/2026" in tex
    # La plus récente en premier : CyberQuest (05/2026), Shellter (08/2025), Airbus (07/2025)
    assert tex.index("Plateforme CyberQuest") < tex.index("Stage DevOps --- Shellter") < tex.index("Étude hydrogène vert")
    assert not any(m in tex for m in ("Avril 2025", "Juillet 2025", "Mai 2026", "Octobre 2026"))

    # Airbus : une seule puce synthétique (la place va aux projets cybersécurité)
    airbus_block = tex.split("Université de Hambourg", 1)[1].split("\\entry{", 1)[0]
    assert airbus_block.count("\\item") == 1
    assert "Coordonné une équipe internationale anglophone" in airbus_block
    assert "LCOH" in airbus_block

    # Vérifie la puce Shellter
    assert "Conçu et sécurisé une plateforme Kubernetes" in tex
    assert "SAST/DAST" in tex

    # Vérifie la puce CyberQuest
    assert "Piloté la création d'une plateforme de formation gamifiée" in tex
