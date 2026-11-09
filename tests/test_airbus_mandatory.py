import sys
from applypilot.cv_latex import generate_cv, _render_tex, _default_selection, load_experiences, MANDATORY_EXP_IDS
from pypdf import PdfReader


def test_airbus_mandatory_in_cv(monkeypatch):
    # Désactive les retries LLM pour un test rapide
    import applypilot.cv_latex as cv_mod
    monkeypatch.setattr(cv_mod, "_llm_selection", lambda offer, exp_data: None)

    exp_data = load_experiences()
    assert "etude-hydrogene-airbus" in MANDATORY_EXP_IDS

    sample_offer = {
        "title": "Stage - Auditeur Cybersécurité & Conformité GRC",
        "company": "Sopra Steria",
        "location": "Paris",
        "description": "Analyse de risques ISO 27001, conformité et audits organisationnels."
    }

    sel = _default_selection(exp_data, sample_offer)
    assert "etude-hydrogene-airbus" in sel["selected_experience_ids"]

    # Vérification que même avec un budget serré (max_exp=3), Airbus est présent
    for max_exp in [7, 5, 4, 3]:
        tex = _render_tex(exp_data, sel, sample_offer, max_exp=max_exp, max_bullets=2)
        assert "Airbus" in tex

    pdf_path = generate_cv(sample_offer)
    reader = PdfReader(str(pdf_path))
    assert len(reader.pages) == 1

    import fitz
    doc = fitz.open(str(pdf_path))
    text = doc[0].get_text()
    assert "Airbus" in text
