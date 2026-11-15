from applypilot import cv_latex

def test_extract_custom_title():
    assert cv_latex._extract_custom_title("titre cv : Stage analyse cybersécurité") == "Stage analyse cybersécurité"
    assert cv_latex._extract_custom_title("titre: Consultant Pentest") == "Consultant Pentest"
    assert cv_latex._extract_custom_title("intitulé : Stage Audit Cybersécurité") == "Stage Audit Cybersécurité"
    assert cv_latex._extract_custom_title("Stage analyse cybersécurité") == "Stage analyse cybersécurité"
    assert cv_latex._extract_custom_title("pour le titre du cv met ca : Stagiaire ingénieure en cybersécurité") == "Stagiaire ingénieure en cybersécurité"
    assert cv_latex._extract_custom_title("mets comme titre Stagiaire ingénieur cyber") == "Stagiaire ingénieur cyber"
    assert cv_latex._extract_custom_title("change le titre en : Analyste SOC") == "Analyste SOC"
    assert cv_latex._extract_custom_title("insister sur Active Directory et retirer DVWA") is None


def test_render_tex_with_custom_title():
    exp_data = cv_latex.load_experiences()
    offer = {
        "title": "Analyste Cybersecurite - IT (Systemes d'information) - Fontenay-Sous-Bois, Ile de France, France",
        "company": "Société Générale",
        "user_note": "titre cv : Stage analyse cybersécurité",
    }
    sel = cv_latex._default_selection(exp_data, offer)
    tex = cv_latex._render_tex(exp_data, sel, offer, max_exp=4, max_bullets=2)
    assert "STAGE --- ANALYSE CYBERSÉCURITÉ" in tex
    assert "CV Ali Jamai - Stage analyse cybersécurité" in tex


def test_render_tex_with_user_phrase_stagiaire():
    exp_data = cv_latex.load_experiences()
    offer = {
        "title": "Stagiaire ingénieure/ingénieur en cybersécurité à la mission qualité, performance, risques",
        "company": "CNIL",
        "user_note": "pour le titre du cv met ca : Stagiaire ingénieure en cybersécurité",
    }
    sel = cv_latex._default_selection(exp_data, offer)
    tex = cv_latex._render_tex(exp_data, sel, offer, max_exp=4, max_bullets=2)
    assert "STAGE --- INGÉNIEURE EN CYBERSÉCURITÉ" in tex
    assert "CV Ali Jamai - Stage ingénieure en cybersécurité" in tex
