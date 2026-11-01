"""Tests du pré-filtre de scoring, version marché français / recherche de stage.

Le pré-filtre ne rejette que les cas évidents :
  - rôle clairement hors cible (non technique),
  - offre restreinte à une zone géographique qui EXCLUT la France.
Les titres « stage / alternance / junior / graduate » ne sont PAS rejetés :
ce sont les cibles du candidat. L'Europe / EMEA incluent la France.
"""

import pytest

from applypilot.scoring.scorer import _check_ineligible


def _job(title="Stage Ingénieur Cybersécurité", location="Paris, France",
         description="Stage de 6 mois en intégration IT, Active Directory, Azure."):
    return {"title": title, "location": location, "full_description": description}


# ── Les titres de stage / junior NE sont PAS rejetés ────────────────

@pytest.mark.parametrize("title", [
    "Stage Cybersécurité - Active Directory",
    "Stage Administrateur Systèmes et Réseaux",
    "Alternance Ingénieur Intégration IT",
    "Stagiaire Sécurité Infrastructure (H/F)",
    "Junior Software Engineer",
    "Graduate Security Analyst",
    "Software Engineering Internship",
    "Stage Projet Entra ID / M365",
])
def test_titres_stage_non_rejetes(title):
    assert _check_ineligible(_job(title=title)) is None


# ── Localisations françaises / Europe / télétravail NON rejetées ────

@pytest.mark.parametrize("loc", [
    "Paris, France",
    "Lyon",
    "Toulouse (31)",
    "Télétravail - France",
    "Remote - Europe",
    "EMEA",
    "Hybride, Bourges",
    "Remote France, déplacements ponctuels à London",  # France l'emporte
])
def test_localisations_en_zone_non_rejetees(loc):
    assert _check_ineligible(_job(location=loc)) is None


# ── Localisations clairement hors zone (hors France) rejetées ───────

@pytest.mark.parametrize("loc", [
    "Seattle, WA",
    "New York, NY",
    "United States",
    "Remote (USA)",
    "London, United Kingdom",
    "Bangalore, India",
    "Toronto, Canada",
    "Berlin, Germany",
    "Madrid, Spain",
    "Singapore",
    "Tel Aviv, Israel",
])
def test_localisations_hors_zone_rejetees(loc):
    assert _check_ineligible(_job(location=loc)) is not None


# ── Marqueurs de région dans le titre ───────────────────────────────

@pytest.mark.parametrize("title", [
    "Security Engineer, APAC",
    "DevOps Engineer - LATAM",
    "Engineer, MENA",
    "Staff Engineer (ANZ)",
    "US-only Senior Engineer",
    "Only hiring in Vietnam | Engineer",
])
def test_regions_hors_zone_titre_rejetees(title):
    assert _check_ineligible(_job(title=title, location="Remote")) is not None


def test_emea_titre_non_rejete():
    """EMEA et Europe incluent la France → ne doivent pas être rejetés."""
    assert _check_ineligible(_job(title="Security Engineer, EMEA", location="Remote")) is None
    assert _check_ineligible(_job(title="Security Engineer, Europe", location="Remote")) is None


# ── Rôles hors cible (non techniques) rejetés ───────────────────────

@pytest.mark.parametrize("title", [
    "Ingénieur Commercial",
    "Business Developer",
    "Technical Recruiter",
    "Chargé de recrutement",
    "Senior UX Designer",
    "Product Designer",
    "Account Manager",
    "Account Executive",
    "Sales Engineer",
    "Solutions Engineer",
    "Cashier",
    "Vendeur",
])
def test_roles_hors_cible_rejetes(title):
    assert _check_ineligible(_job(title=title)) is not None


# ── Restrictions enfouies dans la description ───────────────────────

def test_us_based_only_dans_description_rejete():
    desc = "Great team. " * 50 + " This is a US-based only position."
    assert _check_ineligible(_job(title="Security Engineer", location="Remote",
                                  description=desc)) is not None


def test_right_to_work_uk_rejete():
    desc = "Nice role. " * 50 + " You must have the right to work in the United Kingdom."
    assert _check_ineligible(_job(title="Security Engineer", location="Remote",
                                  description=desc)) is not None


def test_description_us_mais_france_mentionnee_non_rejete():
    """Si la description mentionne la France, on ne rejette pas sur la géo."""
    desc = ("Poste basé en France, télétravail partiel. "
            + "Our HQ is in the United States.")
    assert _check_ineligible(_job(title="Security Engineer", location="Remote",
                                  description=desc)) is None


# ── _parse_score_response : extraction du tag ELIGIBILITY ───────────

def test_parser_eligible():
    from applypilot.scoring.scorer import _parse_score_response
    response = ("ELIGIBILITY: eligible\nSCORE: 9\nKEYWORDS: Active Directory, Azure\n"
                "REASONING: Très bon stage cyber en France.")
    parsed = _parse_score_response(response)
    assert parsed["score"] == 9
    assert parsed["eligibility"] == "eligible"


def test_parser_hors_zone():
    from applypilot.scoring.scorer import _parse_score_response
    response = "ELIGIBILITY: hors_zone\nSCORE: 7\nKEYWORDS: backend\nREASONING: Poste UK-only."
    parsed = _parse_score_response(response)
    assert parsed["eligibility"] == "hors_zone"


def test_parser_hors_zone_avec_crochets():
    from applypilot.scoring.scorer import _parse_score_response
    response = "ELIGIBILITY: [hors_zone]\nSCORE: 6\nKEYWORDS: foo\nREASONING: bar"
    parsed = _parse_score_response(response)
    assert parsed["eligibility"] == "hors_zone"


def test_parser_non_us_only_retrocompat():
    """Rétro-compat : un ancien tag 'non_us_only' est traité comme hors_zone."""
    from applypilot.scoring.scorer import _parse_score_response
    response = "ELIGIBILITY: non_us_only\nSCORE: 5\nKEYWORDS: foo\nREASONING: bar"
    parsed = _parse_score_response(response)
    assert parsed["eligibility"] == "hors_zone"


def test_parser_eligibility_absente_defaut_eligible():
    from applypilot.scoring.scorer import _parse_score_response
    response = "SCORE: 8\nKEYWORDS: foo\nREASONING: bar"
    parsed = _parse_score_response(response)
    assert parsed["eligibility"] == "eligible"


# ── _build_candidate_summary : robustesse ───────────────────────────

def test_candidate_summary_profil_vide():
    from applypilot.scoring.scorer import _build_candidate_summary
    summary = _build_candidate_summary({})
    assert "stage" in summary.lower()
    assert "6 mois" in summary


def test_candidate_summary_profil_rempli():
    from applypilot.scoring.scorer import _build_candidate_summary
    profile = {
        "experience": {
            "current_job_title": "Étudiant ingénieur cybersécurité (STI)",
            "school": "INSA Centre Val de Loire",
            "target_role": "stage en intégration IT & cybersécurité",
        },
        "internship": {"duration": "6 mois", "start_date": "avril 2027"},
        "skills_boundary": {
            "languages": ["Python", "PowerShell"],
            "platforms": ["Active Directory", "Azure", "Entra ID"],
        },
    }
    summary = _build_candidate_summary(profile)
    assert "INSA" in summary
    assert "avril 2027" in summary
    assert "Active Directory" in summary
