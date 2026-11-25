from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from applypilot.discovery.cyber_filter import (
    is_handicap_targeted,
    is_cyber_offer,
    filter_cyber_internships,
)
from applypilot.discovery.indeed_student import filter_and_tag


def test_is_handicap_targeted_exclusions():
    """Vérifie que les offres réservées ou destinées au handicap sont bien détectées."""
    # Titres ciblés
    assert is_handicap_targeted(title="Stage Analyste SOC (RQTH)")
    assert is_handicap_targeted(title="Ingénieur Cybersécurité - Tremplin Handicap")
    assert is_handicap_targeted(title="Handi-stage Consultant GRC")
    assert is_handicap_targeted(title="Stagiaire Pentest - Travailleur handicapé")
    assert is_handicap_targeted(title="Security Intern - Disability Confident Scheme")

    # Employeurs ciblés (organismes / entreprises adaptées)
    assert is_handicap_targeted(title="Téléconseiller SAV", employer="APF France handicap")
    assert is_handicap_targeted(title="Assistant IT", employer="Tremplin Handicap")
    assert is_handicap_targeted(title="Stagiaire Réseau", employer="Arpejeh")
    assert is_handicap_targeted(title="Analyste Données", employer="Club Être")
    assert is_handicap_targeted(title="Opérateur Saisie", employer="ESAT Les Ateliers")
    assert is_handicap_targeted(title="Stage Consultant GRC", employer="Groupe Talents Handicap")
    assert is_handicap_targeted(title="Stage Consultant GRC", site="Groupe Talents Handicap")
    assert is_handicap_targeted(title="Stage Consultant GRC", site="Talents Handicap")

    # Descriptions réservées
    assert is_handicap_targeted(
        title="Stage Cybersécurité",
        employer="Thales",
        extra_text="Ce poste est exclusivement réservé aux personnes en situation de handicap.",
    )
    assert is_handicap_targeted(
        title="Stage Pentest",
        employer="Airbus",
        extra_text="Poste réservé RQTH dans le cadre de notre accord d'entreprise.",
    )
    assert is_handicap_targeted(
        title="Stage Consultant",
        employer="Sopra Steria",
        extra_text="Profil recherché : titulaire d'une RQTH exigé pour ce programme.",
    )


def test_is_handicap_targeted_preserves_standard_inclusive_offers():
    """Vérifie que les mentions légales d'ouverture / inclusion classiques ne sont PAS exclues."""
    # Mention légale classique d'ouverture
    assert not is_handicap_targeted(
        title="Stage Consultant(e) Cybersécurité",
        employer="Thales",
        extra_text="À compétences égales, tous nos postes sont ouverts aux personnes en situation de handicap.",
    )
    assert not is_handicap_targeted(
        title="Stage Ingénieur Sécurité Réseau",
        employer="Orange",
        extra_text="Entreprise handi-accueillante, nous veillons à l'égalité des chances.",
    )
    assert not is_handicap_targeted(
        title="Cybersecurity Intern",
        employer="SwissBorg",
        extra_text="We are an equal opportunity employer and value diversity.",
    )


def test_is_cyber_offer_rejects_handicap_targeted_offers():
    """Vérifie que le filtre cyber rejette une offre même si elle contient 'cyber' si elle est ciblée handicap."""
    assert not is_cyber_offer(
        title="Stage Cybersécurité (RQTH)",
        employer="Thales",
    )
    assert not is_cyber_offer(
        title="Stage Cyber",
        employer="Tremplin Handicap",
    )


def test_filter_cyber_internships_drops_handicap_jobs():
    """Vérifie que filter_cyber_internships ignore les offres ciblées handicap."""
    offers = [
        {
            "url": "https://example.com/stage1",
            "title": "Stage - Analyste SOC Cybersécurité",
            "employer_name": "Thales",
            "contract": "Stage",
            "description": "Analyse d'incidents SIEM / SOC.",
        },
        {
            "url": "https://example.com/stage2",
            "title": "Stage - Analyste SOC (Réservé RQTH)",
            "employer_name": "Thales",
            "contract": "Stage",
            "description": "Poste réservé aux candidats titulaires d'une RQTH.",
        },
        {
            "url": "https://example.com/stage3",
            "title": "Stage Pentest & Red Team",
            "employer_name": "Tremplin Handicap",
            "contract": "Stage",
            "description": "Tests d'intrusion web et infra.",
        },
    ]

    kept = filter_cyber_internships(offers)
    assert len(kept) == 1
    assert kept[0]["title"] == "Stage - Analyste SOC Cybersécurité"


def test_indeed_student_filter_and_tag_drops_handicap_jobs():
    """Vérifie que filter_and_tag dans indeed_student écarte les offres type APF France handicap."""
    records = [
        {
            "job": {
                "title": "Téléconseiller SAV à distance",
                "company": {"name": "APF France handicap"},
                "description": "Service client à distance le week-end et temps partiel.",
            },
            "categories": ["Service client"],
        },
        {
            "job": {
                "title": "Vendeur Polyvalent Week-end",
                "company": {"name": "Decathlon"},
                "description": "Poste étudiant le samedi et dimanche.",
            },
            "categories": ["Vente"],
        },
    ]

    kept = filter_and_tag(records)
    assert len(kept) == 1
    assert kept[0]["job"]["company"]["name"] == "Decathlon"


@pytest.mark.anyio
async def test_send_offer_drops_handicap_job():
    """Vérifie que _send_offer n'envoie jamais d'offre ciblée handicap sur Telegram."""
    from applypilot import telegram_bot
    mock_bot = SimpleNamespace(send_message=AsyncMock())
    row = {
        "url": "https://fr.indeed.com/viewjob?jk=63c898be4802b19f",
        "title": "Stage : Consultant/e Cybersécurité GRC - Défense & Sécurité - Le Plessis Robinson",
        "company": None,
        "site": "Groupe Talents Handicap",
        "description": "GRC, gouvernance...",
    }
    await telegram_bot._send_offer(mock_bot, 123, row)
    mock_bot.send_message.assert_not_called()

