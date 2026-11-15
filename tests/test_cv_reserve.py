"""CV de réserve par branche : ce sont eux que « Préparer ma candidature » envoie.
Le LLM n'intervient que si le candidat donne une consigne d'adaptation."""
import json

import fitz  # pymupdf
import pytest

import applypilot.cv_latex as cv_mod
from applypilot.cv_latex import (
    RESERVE_BRANCHES,
    build_reserve,
    generate_cv,
    generation_notice,
    generation_source,
    load_experiences,
    offer_branch,
    reserve_dir,
)

PENTEST_OFFER = {
    "title": "Stage - Pentest Web & Active Directory H/F",
    "company": "Orange",
    "url": "https://example.com/offre/pentest-1",
    "description": "Tests d'intrusion web et Active Directory.",
}
NOTE = "insiste sur le web"  # consigne ✏️ du candidat : c'est elle qui déclenche le LLM
# Sans apostrophe : LaTeX la rend en apostrophe typographique dans le PDF.
MARKER = "Accroche de réserve rédigée en avance pour la branche"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)  # relances LLM sans attente
    # Les offres de test ont une URL : pas de liens traceurs écrits dans la vraie base.
    monkeypatch.setattr("applypilot.tracking.tracer.get_or_create_all_tracers", lambda offer: {})


def _fake_llm(monkeypatch, available: bool, calls: list | None = None):
    """LLM simulé : rédige une accroche reconnaissable, ou ne répond pas."""
    def fake(offer, exp_data):
        if calls is not None:
            calls.append(offer)
        if not available:
            return None
        sel = cv_mod._default_selection(exp_data, offer)
        sel["profil"] = f"{MARKER} {offer_branch(offer)}, sans entreprise citée."
        return sel
    monkeypatch.setattr(cv_mod, "_llm_selection", fake)


def _text(pdf) -> str:
    return " ".join(fitz.open(str(pdf))[0].get_text().split())


# --- Choix de la branche -----------------------------------------------------

@pytest.mark.parametrize("branch", list(RESERVE_BRANCHES))
def test_each_reserve_title_maps_to_its_own_branch(branch):
    assert offer_branch(RESERVE_BRANCHES[branch]) == branch


def test_unknown_title_falls_in_generic_branch():
    assert offer_branch({"title": "Stage Cryptographie Appliquée"}) == "Cyber"
    assert offer_branch({"title": ""}) == "Cyber"


@pytest.mark.parametrize("description, expected", [
    ("Au sein du SOC, vous analysez les alertes du SIEM Splunk, qualifiez les incidents de sécurité et "
     "participez à la détection et à l'investigation.", "SOC"),
    ("Vous réalisez des tests d'intrusion web et internes, des missions Red Team et des audits offensifs "
     "selon l'OWASP, avec Burp Suite. Pentest d'applications.", "Pentest"),
    ("Vous contribuez à la gouvernance : analyse de risques EBIOS, conformité ISO 27001, mise à jour de "
     "la PSSI et sensibilisation des équipes au RGPD.", "GRC"),
    ("Administration Kubernetes et Docker, pipelines CI/CD GitLab, automatisation Ansible et Terraform "
     "dans une équipe DevSecOps.", "DevOps"),
    ("Vous rejoignez une équipe dynamique pour participer à divers projets.", "Cyber"),
    # Deux branches au coude à coude : pas de signal net, on reste générique.
    ("Pentest et SOC : tests d'intrusion, détection, incidents, OWASP, SIEM.", "Cyber"),
])
def test_generic_title_uses_the_description_to_pick_the_branch(description, expected):
    offer = {"title": "Stagiaire Cybersécurité H/F", "description": description}
    assert offer_branch(offer) == expected


def test_explicit_title_wins_over_the_description():
    offer = {"title": "Stage - Pentester", "description": "SOC SIEM Splunk détection incidents alertes supervision"}
    assert offer_branch(offer) == "Pentest"


# --- Constitution de la réserve ----------------------------------------------

def test_build_reserve_writes_pdf_and_selection(monkeypatch):
    _fake_llm(monkeypatch, available=True)
    results = build_reserve(["Pentest", "SOC"])

    for branch in ("Pentest", "SOC"):
        assert results[branch]["ok"], results[branch]
        pdf = reserve_dir() / f"CV_Ali_Jamai_{branch}.pdf"
        assert results[branch]["pdf"] == pdf and pdf.exists()
        assert results[branch]["pages"] == 1
        assert len(pdf.name) <= 25
        data = json.loads((reserve_dir() / f"{branch}.json").read_text(encoding="utf-8"))
        assert data["profil"].startswith(MARKER)
        assert data["selected_experience_ids"]
    assert f"{MARKER} Pentest" in _text(reserve_dir() / "CV_Ali_Jamai_Pentest.pdf")


def test_build_reserve_rejects_unknown_branch():
    with pytest.raises(ValueError):
        build_reserve(["Cuisine"])


def test_failed_rebuild_keeps_previous_reserve(monkeypatch):
    _fake_llm(monkeypatch, available=True)
    build_reserve(["GRC"])
    before = (reserve_dir() / "GRC.json").read_text(encoding="utf-8")

    _fake_llm(monkeypatch, available=False)
    results = build_reserve(["GRC"])
    assert not results["GRC"]["ok"]
    assert results["GRC"]["pdf"] == reserve_dir() / "CV_Ali_Jamai_GRC.pdf"
    assert (reserve_dir() / "GRC.json").read_text(encoding="utf-8") == before


def test_recompile_from_saved_applies_manual_edit_without_llm(monkeypatch):
    _fake_llm(monkeypatch, available=True)
    build_reserve(["SOC"])
    path = reserve_dir() / "SOC.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["profil"] = "Accroche corrigée par le candidat."
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    _fake_llm(monkeypatch, available=False)  # le LLM ne doit pas être nécessaire
    results = build_reserve(["SOC", "IA"], from_saved=True)
    assert results["SOC"]["ok"]
    assert "Accroche corrigée par le candidat." in _text(reserve_dir() / "CV_Ali_Jamai_SOC.pdf")
    assert not results["IA"]["ok"] and results["IA"]["pdf"] is None  # jamais générée


def test_reserve_cv_creates_no_tracker_links(monkeypatch):
    calls = []
    monkeypatch.setattr("applypilot.tracking.tracer.get_or_create_all_tracers",
                        lambda offer: calls.append(offer) or {})
    _fake_llm(monkeypatch, available=True)
    build_reserve(["Cloud"])
    generate_cv({"title": "Stage Sécurité Cloud"}, tailor=True)  # offre sans URL
    assert calls == []


def test_reserve_drops_experience_ids_removed_from_catalog(monkeypatch):
    _fake_llm(monkeypatch, available=True)
    build_reserve(["AD"])
    path = reserve_dir() / "AD.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["selected_experience_ids"].append("experience-supprimee")
    path.write_text(json.dumps(data), encoding="utf-8")

    reserve = cv_mod.load_reserve("AD", load_experiences())
    assert reserve and "experience-supprimee" not in reserve["selected_experience_ids"]


# --- « Préparer ma candidature » : le CV de réserve, tel quel, sans LLM -------

def test_default_sends_the_branch_reserve_cv_untouched_and_never_calls_the_llm(monkeypatch):
    _fake_llm(monkeypatch, available=True)
    build_reserve(["Pentest"])
    stock = reserve_dir() / "CV_Ali_Jamai_Pentest.pdf"
    stock_bytes = stock.read_bytes()

    calls: list = []
    _fake_llm(monkeypatch, available=True, calls=calls)
    pdf = generate_cv(PENTEST_OFFER)

    assert calls == []                                   # le LLM n'a pas été sollicité
    assert generation_source(pdf) == "reserve:Pentest"
    assert pdf != stock and pdf.read_bytes() == stock_bytes   # copie fidèle, octet pour octet
    assert stock.read_bytes() == stock_bytes             # la réserve n'est jamais modifiée
    assert pdf.name == "CV_Ali_Jamai_Pentest.pdf" and len(pdf.name) <= 25
    assert "réserve" in generation_notice(pdf) and "Modifier" in generation_notice(pdf)


def test_default_picks_the_reserve_from_the_description_when_title_is_generic(monkeypatch):
    _fake_llm(monkeypatch, available=True)
    build_reserve(["SOC", "Cyber"])
    offer = {"title": "Stagiaire Cybersécurité H/F", "url": "https://example.com/offre/soc-2",
             "description": "Au sein du SOC : alertes SIEM Splunk, incidents de sécurité, détection, investigation."}
    pdf = generate_cv(offer)
    assert generation_source(pdf) == "reserve:SOC"
    assert pdf.name == "CV_Ali_Jamai_SOC.pdf"


def test_default_without_own_reserve_uses_the_generic_one(monkeypatch):
    _fake_llm(monkeypatch, available=True)
    build_reserve(["Cyber"])
    pdf = generate_cv({"title": "Stage - Analyste SOC H/F", "url": "https://example.com/offre/soc-1"})
    assert generation_source(pdf) == "reserve:Cyber"
    assert f"{MARKER} Cyber" in _text(pdf)


def test_same_branch_offers_get_their_own_copy(monkeypatch):
    _fake_llm(monkeypatch, available=True)
    build_reserve(["Pentest"])
    a = generate_cv(PENTEST_OFFER)
    b = generate_cv({**PENTEST_OFFER, "url": "https://example.com/offre/pentest-2"})
    assert a != b and a.name == b.name == "CV_Ali_Jamai_Pentest.pdf"


def test_without_any_reserve_falls_back_to_tailored_generation(monkeypatch):
    _fake_llm(monkeypatch, available=False)
    pdf = generate_cv(PENTEST_OFFER)
    assert generation_source(pdf) == "secours"
    assert "cv-reserve" in generation_notice(pdf)
    assert "sécurité offensive" in _text(pdf)


# --- L'offre exige un élément que le CV de réserve ne montre pas : LLM automatique ---

GAP_OFFER = {
    "title": "Stage - Pentester H/F",
    "company": "Orange",
    "url": "https://example.com/offre/pentest-gap",
    "description": "Tests d'intrusion web. Une première pratique de Splunk et de Wazuh est exigée, "
                   "ainsi que la méthode EBIOS RM.",
}


def test_offer_gaps_only_lists_what_the_candidate_really_has():
    exp = load_experiences()
    cv_text = "Tests d'intrusion, Burp Suite, Nmap."
    gaps = cv_mod.offer_gaps(GAP_OFFER, cv_text, exp)
    assert "Splunk" in gaps and "Wazuh" in gaps
    assert not any("EBIOS" in g for g in gaps)            # absent du catalogue : jamais ajouté
    assert "Burp Suite" not in gaps                        # non demandé par l'offre
    # Déjà sur le CV : plus rien à ajouter.
    assert cv_mod.offer_gaps(GAP_OFFER, cv_text + " Splunk, Wazuh", exp) == []
    # Mots courants : « ou » n'est pas l'unité d'organisation, « c » n'est pas le langage C.
    assert cv_mod.offer_gaps({"title": "x", "description": "Python ou Java, c'est selon"}, "Python", exp) == []
    # Sigle court : la casse compte.
    assert "SPL" not in cv_mod.offer_gaps({"title": "x", "description": "le spleen du stagiaire, spl"}, "", exp)
    assert "SPL" in cv_mod.offer_gaps({"title": "x", "description": "Requêtes SPL avancées"}, "", exp)


def test_missing_required_elements_trigger_the_llm_automatically(monkeypatch):
    _fake_llm(monkeypatch, available=True)
    build_reserve(["Pentest"])
    stock = reserve_dir() / "CV_Ali_Jamai_Pentest.pdf"
    stock_bytes = stock.read_bytes()
    assert "Splunk" not in _text(stock) and "Wazuh" not in _text(stock)

    calls: list = []
    _fake_llm(monkeypatch, available=True, calls=calls)
    pdf = generate_cv(GAP_OFFER)                            # aucune consigne du candidat
    text = _text(pdf)

    assert len(calls) == 1                                   # le LLM est intervenu de lui-même
    assert set(calls[0]["_must"]) >= {"Splunk", "Wazuh"}
    assert calls[0]["_base"]["profil"].startswith(MARKER)
    assert generation_source(pdf) == "llm_auto:Pentest"
    assert "Splunk" in text and "Wazuh" in text              # les éléments exigés sont sur le CV
    assert "EBIOS" not in text                               # rien d'inventé
    assert f"{MARKER} Pentest" in text                       # accroche de réserve conservée
    assert "STAGE — PENTESTER / SÉCURITÉ OFFENSIVE" in text  # bandeau de réserve conservé
    # La première ligne de compétences du CV de réserve reste la première.
    first_skill_line = lambda t: t.split("COMPÉTENCES TECHNIQUES", 1)[1].split(":", 1)[0].strip()
    assert first_skill_line(text) == first_skill_line(_text(stock))
    assert pdf.name == "CV_Ali_Jamai_Pentest.pdf" and len(fitz.open(str(pdf))) == 1
    assert stock.read_bytes() == stock_bytes                 # la réserve elle-même n'a pas bougé
    notice = generation_notice(pdf)
    assert "automatiquement" in notice and "Splunk" in notice and "Wazuh" in notice


def test_missing_required_elements_are_added_even_when_the_llm_is_down(monkeypatch):
    _fake_llm(monkeypatch, available=True)
    build_reserve(["Pentest"])
    _fake_llm(monkeypatch, available=False)

    pdf = generate_cv(GAP_OFFER)
    text = _text(pdf)
    assert generation_source(pdf) == "reserve_completee:Pentest"
    assert "Splunk" in text and "Wazuh" in text
    assert f"{MARKER} Pentest" in text
    assert "LLM indisponible" in generation_notice(pdf)
    assert cv_mod.generation_details(pdf)["added"] and len(fitz.open(str(pdf))) == 1


def test_element_living_only_in_an_experience_brings_that_experience_in():
    exp = load_experiences()
    sel = {"selected_experience_ids": ["pentest-ad", "pentest-web-dvwa"], "skills_order": list(exp["skills"])}
    out = cv_mod._with_required_experiences(sel, ["DFIR-IRIS", "Flask"], exp)
    assert out["must_have"] == ["DFIR-IRIS", "Flask"]
    assert out["selected_experience_ids"][0] == "pentest-ad"         # la première reste en tête
    assert sel["selected_experience_ids"] == ["pentest-ad", "pentest-web-dvwa"]  # l'original n'est pas modifié


# --- ✏️ Modifier : le candidat demande lui-même une adaptation -----------------

def test_instruction_calls_the_llm_starting_from_the_reserve_cv(monkeypatch):
    _fake_llm(monkeypatch, available=True)
    build_reserve(["Pentest"])
    stock_bytes = (reserve_dir() / "CV_Ali_Jamai_Pentest.pdf").read_bytes()
    live = "Accroche adaptée selon la consigne du candidat."
    calls: list = []

    def tailored(offer, exp_data):
        calls.append(offer)
        sel = cv_mod._default_selection(exp_data, offer)
        sel["profil"] = live
        return sel
    monkeypatch.setattr(cv_mod, "_llm_selection", tailored)

    pdf = generate_cv({**PENTEST_OFFER, "user_note": NOTE})
    assert len(calls) == 1
    assert calls[0]["_base"]["profil"].startswith(MARKER)      # le LLM reçoit le CV de réserve comme base
    assert calls[0]["user_note"] == NOTE
    assert generation_source(pdf) == "llm"
    assert live in _text(pdf) and MARKER not in _text(pdf)
    assert "STAGE — PENTEST WEB & ACTIVE DIRECTORY" in _text(pdf)   # bandeau de CETTE offre
    assert (reserve_dir() / "CV_Ali_Jamai_Pentest.pdf").read_bytes() == stock_bytes  # réserve intacte


def test_tailor_flag_forces_the_llm_even_without_instruction(monkeypatch):
    calls: list = []
    _fake_llm(monkeypatch, available=True)
    build_reserve(["Pentest"])
    _fake_llm(monkeypatch, available=True, calls=calls)
    pdf = generate_cv(PENTEST_OFFER, tailor=True)
    assert len(calls) == 1 and generation_source(pdf) == "llm"


def test_instruction_with_llm_down_recompiles_reserve_with_offer_banner(monkeypatch):
    _fake_llm(monkeypatch, available=True)
    build_reserve(["Pentest"])
    _fake_llm(monkeypatch, available=False)

    pdf = generate_cv({**PENTEST_OFFER, "user_note": NOTE})
    text = _text(pdf)
    assert generation_source(pdf) == "reserve_adaptee:Pentest"
    assert f"{MARKER} Pentest" in text                           # accroche de réserve
    assert "STAGE — PENTEST WEB & ACTIVE DIRECTORY" in text      # bandeau de CETTE offre
    assert "LLM indisponible" in generation_notice(pdf)
    assert len(fitz.open(str(pdf))) == 1


def test_instruction_with_compile_down_sends_reserve_pdf_as_is(monkeypatch):
    _fake_llm(monkeypatch, available=True)
    build_reserve(["Pentest"])
    stock = reserve_dir() / "CV_Ali_Jamai_Pentest.pdf"
    stock_bytes = stock.read_bytes()

    def broken(tex_path, passes=2):
        raise RuntimeError("pdflatex introuvable")
    monkeypatch.setattr(cv_mod, "_compile", broken)

    pdf = generate_cv({**PENTEST_OFFER, "user_note": NOTE})
    assert generation_source(pdf) == "reserve_sans_compilation:Pentest"
    assert pdf != stock and pdf.read_bytes() == stock_bytes
    assert stock.exists()
    assert "tel quel" in generation_notice(pdf)
