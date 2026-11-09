import sys
sys.stdout.reconfigure(encoding="utf-8")
from applypilot.cv_latex import generate_cv
from pypdf import PdfReader

sample_offer = {
    "title": "Stage - Pentester Evaluation LLM F/H (2026-54964)",
    "company": "Orange",
    "location": "Rennes / Télétravail",
    "description": (
        "Recherche stagiaire en cybersécurité offensive, évaluation de la robustesse "
        "des modèles de langage (LLM), pentest d'applications intégrant de l'IA générative, "
        "audit de code et sécurité applicative OWASP."
    )
}

print("Starting generate_cv...")
pdf_path = generate_cv(sample_offer)
print(f"Generated PDF: {pdf_path}")
print(f"PDF exists: {pdf_path.exists()}")
reader = PdfReader(str(pdf_path))
page_count = len(reader.pages)
print(f"Page count: {page_count}")
print(f"File size: {pdf_path.stat().st_size / 1024:.1f} KB")

assert page_count == 1, f"Expected 1 page, got {page_count}"
print("VERIFICATION COMPLETE: EXACTLY 1 PAGE GENERATED WITH 0 ERRORS!")
