from pypdf import PdfReader
from applypilot import cv_latex


def test_offer_language_detection():
    # Offres en anglais -> "en"
    assert cv_latex.offer_language({"title": "Cybersecurity Intern", "description": "Join our security team"}) == "en"
    assert cv_latex.offer_language({"title": "Internship - Network Engineer - Network, Observability and Security"}) == "en"
    assert cv_latex.offer_language({"title": "Information Security Trainee", "company": "Nomura"}) == "en"
    assert cv_latex.offer_language({"title": "Working Student - Cybersecurity Governance (OT/IT)"}) == "en"
    assert cv_latex.offer_language({"title": "Master Thesis: Securing the Future of Agentic AI"}) == "en"
    assert cv_latex.offer_language({"title": "Graduate - Cyber Security"}) == "en"

    # Offres en français -> "fr"
    assert cv_latex.offer_language({"title": "Stage Analyste SOC H/F", "description": "Au sein de notre équipe sécurité"}) == "fr"
    assert cv_latex.offer_language({"title": "Stagiaire Administrateur Microsoft 365 & Entra ID H/F"}) == "fr"
    assert cv_latex.offer_language({"title": "Stage Ingénieur Cybersécurité - GRC"}) == "fr"

    # Forçage explicite par consigne utilisateur
    assert cv_latex.offer_language({"title": "Stage Cybersécurité", "user_note": "mets le CV en anglais stp"}) == "en"
    assert cv_latex.offer_language({"title": "Cybersecurity Intern", "user_note": "en français"}) == "fr"


def test_generate_english_cv_one_page_and_english_headers():
    """Vérifie qu'une offre en anglais génère un CV 1 page 100% en anglais."""
    offer = {
        "title": "Cybersecurity Intern",
        "company": "SwissBorg",
        "location": "Lausanne, Switzerland",
        "url": "https://example.com/test-en-cv-cyber",
        "description": "We are looking for a Cybersecurity Intern with SIEM, IAM, and Active Directory skills.",
    }
    pdf = cv_latex.generate_cv(offer)
    assert pdf.exists()
    assert pdf.stem.endswith("_EN")

    reader = PdfReader(str(pdf))
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text() or ""

    # En-têtes et sections en anglais
    assert "EDUCATION" in text
    assert "TECHNICAL SKILLS" in text
    assert "PROFESSIONAL EXPERIENCE" in text
    assert "CERTIFICATIONS, CTF & LANGUAGES" in text
    assert "Location:" in text
    assert "INTERNSHIP" in text
    # Aucune section française résiduelle
    assert "COMPÉTENCES TECHNIQUES" not in text
    assert "EXPÉRIENCES PROFESSIONNELLES" not in text
