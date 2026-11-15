from pypdf import PdfReader

from applypilot.cv_latex import generate_cv

SAMPLE_OFFER = {
    "title": "Stage - Pentester Evaluation LLM F/H (2026-54964)",
    "company": "Orange",
    "location": "Rennes / Télétravail",
    "description": (
        "Recherche stagiaire en cybersécurité offensive, évaluation de la robustesse "
        "des modèles de langage (LLM), pentest d'applications intégrant de l'IA générative, "
        "audit de code et sécurité applicative OWASP."
    ),
}


def test_new_template_compiles_on_one_page(monkeypatch):
    # LLM neutralisé : pas d'appel réseau ni de quota consommé pendant les tests.
    import applypilot.cv_latex as cv_mod
    monkeypatch.setattr(cv_mod, "_llm_selection", lambda offer, exp_data: None)

    pdf_path = generate_cv(SAMPLE_OFFER)
    assert pdf_path.exists()
    assert len(PdfReader(str(pdf_path)).pages) == 1
