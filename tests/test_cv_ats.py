"""Lisibilité ATS des CV générés : ce qu'un parseur extrait réellement du PDF."""
import re

import fitz  # pymupdf — extracteur tolérant (reconstruit les espaces)
import pytest
from pypdf import PdfReader  # extracteur strict (flux de contenu brut)

import applypilot.cv_latex as cv_mod
from applypilot.cv_latex import (
    _clean_banner_title,
    _default_profil,
    build_cv_filename,
    generate_cv,
    load_experiences,
)

DEVOPS_OFFER = {
    "title": "Stage - Ingénieur DevOps / CI-CD (F/H)",
    "company": "Wavestone",
    "location": "Paris",
    "description": "Kubernetes, CI/CD, durcissement Ansible, scans SAST/DAST.",
}


@pytest.fixture
def no_llm(monkeypatch):
    monkeypatch.setattr(cv_mod, "_llm_selection", lambda offer, exp_data: None)
    # Certaines offres de test ont une URL : pas de liens traceurs écrits dans la vraie base.
    monkeypatch.setattr("applypilot.tracking.tracer.get_or_create_all_tracers", lambda offer: {})


@pytest.mark.parametrize("title, expected", [
    ("Stage - Ingénieur DevOps / CI-CD (F/H)", "Ingénieur DevOps / CI-CD"),
    ("STAGE - Ingénieur Cybersécurité GRC F/H", "Ingénieur Cybersécurité GRC"),
    ("Stage - Pentester Evaluation LLM F/H (2026-54964)", "Pentester Evaluation LLM"),
    ("Stage Ingénieur·e R&D - Cybersécurité Offensive H/F", "Ingénieur·e R&D - Cybersécurité Offensive"),
    ("Analyste SOC (H/F/X) - Toulouse", "Analyste SOC - Toulouse"),
    ("Stagiaire Cybersécurité [H/F]", "Cybersécurité"),
])
def test_banner_title_has_no_leftover_brackets(title, expected):
    cleaned = _clean_banner_title(title)
    assert cleaned == expected
    assert "()" not in cleaned and "[]" not in cleaned


@pytest.mark.parametrize("raw, expected", [
    ("Avril 2025 -- Juillet 2025", "04/2025 -- 07/2025"),
    ("Juillet 2025 – Août 2025", "07/2025 -- 08/2025"),
    ("Mai 2026", "05/2026"),
    ("Février 2026", "02/2026"),
    ("Décembre 2025", "12/2025"),
    ("sept. 2026", "09/2026"),
    ("09/2023 – 05/2028", "09/2023 -- 05/2028"),
    ("2026", "2026"),
    ("", ""),
])
def test_dates_use_a_single_numeric_format(raw, expected):
    assert cv_mod._format_dates(raw) == expected


def test_education_dates_come_from_start_and_end_fields():
    assert cv_mod._education_dates({"start_date": "01/09/2023", "end_date": "01/05/2028"}) == "09/2023 – 05/2028"
    assert cv_mod._education_dates({"start_date": "01/09/2023"}) is None


def test_no_month_names_left_in_generated_cv(no_llm):
    text = fitz.open(str(generate_cv(DEVOPS_OFFER)))[0].get_text()
    text = text.split("FORMATION", 1)[1]  # l'accroche, elle, dit « à partir d'avril 2027 » en toutes lettres
    months = "janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre"
    assert not re.findall(rf"\b(?:{months})\s+20\d\d\b", text, flags=re.IGNORECASE)


@pytest.mark.parametrize("raw, expected", [
    ("07/2025 -- 08/2025", (2025, 8)),
    ("Avril 2025 -- Juillet 2025", (2025, 7)),
    ("05/2026", (2026, 5)),
    ("2026", (2026, 0)),
    ("", (0, 0)),
])
def test_end_date_key_orders_most_recent_first(raw, expected):
    assert cv_mod._end_date_key(raw) == expected


def test_every_project_shows_its_nature_after_the_title():
    exp_data = load_experiences()
    sel = {"selected_experience_ids": ["pentest-ad", "pentai"]}
    tex = cv_mod._render_tex(exp_data, sel, {"title": "Stage - Pentester"}, 2, 1)
    assert "(projet académique)" in tex and "(projet personnel)" in tex
    # Shellter (08/2025) avant Airbus (07/2025) : la plus récente en premier.
    assert tex.index("Stage DevOps --- Shellter") < tex.index("Airbus")


def test_layout_is_balanced_and_paragraphs_do_not_end_on_stray_words(no_llm):
    pdf_path = generate_cv(DEVOPS_OFFER)
    page = fitz.open(str(pdf_path))[0]
    text_width = page.rect.width - 2 * 42.5

    # Le blanc restant est réparti entre les sections : le texte descend jusqu'en bas de page.
    bottom = max(b[3] for b in page.get_text("blocks") if b[4].strip())
    assert bottom > page.rect.height - 40

    # Aucun paragraphe ne se termine sur une ligne de deux ou trois mots.
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:
            continue
        rows: dict[int, list[float]] = {}
        for line in block["lines"]:
            r = rows.setdefault(round(line["bbox"][1]), [line["bbox"][0], line["bbox"][2]])
            r[0], r[1] = min(r[0], line["bbox"][0]), max(r[1], line["bbox"][2])
        if len(rows) >= 2:
            x0, x1 = rows[max(rows)]
            assert (x1 - x0) / text_width >= 0.25, "fin de paragraphe orpheline"

    # Rien ne dépasse dans la marge droite, et le nom garde son espace.
    right = max(l["bbox"][2] for b in page.get_text("dict")["blocks"] if b["type"] == 0 for l in b["lines"])
    assert right <= page.rect.width - 41
    assert "Ali Jamai" in PdfReader(str(pdf_path)).pages[0].extract_text()


def test_generated_pdf_is_ats_readable(no_llm):
    pdf_path = generate_cv(DEVOPS_OFFER)
    tolerant = fitz.open(str(pdf_path))[0].get_text()
    strict = PdfReader(str(pdf_path)).pages[0].extract_text()

    # Bandeau propre (ancien bug : « STAGE — INGÉNIEUR DEVOPS / CI-CD () »).
    assert "STAGE — INGÉNIEUR DEVOPS / CI-CD" in tolerant
    assert "()" not in tolerant

    # Aucune césure en fin de ligne (« vul-\nnérabilités » casse le mot-clé).
    assert not re.findall(r"\w-\n\w", tolerant)

    # Les espaces survivent aux changements gras/normal avec l'extracteur strict
    # (anciens mots collés : « Kubernetesen », « sécuritéSAST/DAST », « unstage »).
    for glued in ("Kubernetesen", "sécuritéSAST", "unstage", "boutd", "technico-économiquecomparant"):
        assert glued not in strict
    assert "plateforme Kubernetes en équipe" in " ".join(strict.split())
    assert "sécurité SAST/DAST" in " ".join(strict.split())

    # Sigle de certification de langue préservé.
    assert "TOEIC" in tolerant

    assert len(PdfReader(str(pdf_path)).pages) == 1


@pytest.mark.parametrize("title, must, must_not", [
    ("Stage Pentest & Sécurité Offensive", "sécurité offensive", "audit et gouvernance"),
    ("Stage Ingénieur Systèmes & Active Directory", "Active Directory", "audit et gouvernance"),
    ("Stage - Ingénieur DevOps / CI-CD (F/H)", "DevSecOps", "audit et gouvernance"),
    ("Stage - Assistant Administrateur Système & Réseaux H/F", "réseaux", "audit et gouvernance"),
    ("Analyste SOC Détection & Réponse aux Incidents", "sécurité des systèmes d'information", "audit et gouvernance"),
    ("Stage - Auditeur Cybersécurité & Conformité GRC", "audit et gouvernance", "DevSecOps"),
    ("", "audit et gouvernance", "DevSecOps"),  # sans titre, le bandeau affiche « AUDIT CYBERSÉCURITÉ & GRC »
])
def test_fallback_profil_follows_offer_angle(title, must, must_not):
    profil = _default_profil(load_experiences(), {"title": title})
    assert must in profil
    assert must_not not in profil


def test_same_domain_offers_do_not_overwrite_each_other(no_llm):
    a = {"title": "Analyste Cybersécurité", "url": "https://example.com/offre/a", "description": "SOC"}
    b = {"title": "Stagiaire ingénieur en cybersécurité", "url": "https://example.com/offre/b", "description": "GRC"}
    assert build_cv_filename(a) == build_cv_filename(b) == "CV_Ali_Jamai_Cyber"

    pdf_a, pdf_b = generate_cv(a), generate_cv(b)
    assert pdf_a != pdf_b
    assert pdf_a.exists() and pdf_b.exists()
    # Le nom envoyé aux portails reste court et identique.
    assert pdf_a.name == pdf_b.name == "CV_Ali_Jamai_Cyber.pdf"
    assert len(pdf_a.name) <= 25
    # Régénérer la même offre réutilise son emplacement.
    assert generate_cv(a) == pdf_a


def test_tests_never_write_into_real_cv_dir(no_llm):
    from applypilot import config

    pdf_path = generate_cv(DEVOPS_OFFER)
    assert (config.APP_DIR / "cv_latex") not in pdf_path.parents
