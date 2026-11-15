from pathlib import Path
import json
import pytest
from applypilot import experiences_manager


def test_fallback_structure_project():
    existing_ids = {"lab-wazuh-sysmon"}
    text = """Lab Wazuh & Sysmon
    Déploiement d'un agent Wazuh sur Windows Server.
    Collecte des journaux Sysmon pour analyse d'incidents."""
    res = experiences_manager._fallback_structure_project(text, existing_ids)

    assert res["id"] == "lab-wazuh-sysmon-2"
    assert "Wazuh" in res["tech"] or "Windows" in res["tech"] or "Linux" in res["tech"]
    assert len(res["bullets"]) >= 1
    assert res["relevance"] == "high"


def test_add_experience_to_tmp_file(tmp_path, monkeypatch):
    test_json = tmp_path / "experiences.json"
    initial_data = {
        "candidate": {"full_name": "Ali Jamai"},
        "experiences": [
            {"id": "existing-proj", "title": "Ancien Projet"}
        ],
        "skills": {
            "securite_defensive_systeme": ["Linux", "Windows"]
        }
    }
    test_json.write_text(json.dumps(initial_data), encoding="utf-8")

    # Mock client LLM pour tester un retour structuré
    class MockClient:
        def chat(self, messages, max_tokens, temperature):
            return json.dumps({
                "id": "siem-splunk-detection",
                "type": "projet_technique",
                "title": "Plateforme SIEM Splunk & Détection d'Attaques",
                "org": "Projet académique INSA CVL",
                "team": False,
                "dates": "Novembre 2026",
                "role": "Analyste SOC",
                "context": "Déploiement d'un lab Splunk pour analyser les journaux d'authentification.",
                "bullets": [
                    "**Déployé une architecture Splunk Enterprise** et configuré les forwarders.",
                    "**Créé 10 règles de corrélation** pour détecter les attaques par force brute."
                ],
                "tech": ["Splunk", "Python", "Linux"],
                "results": ["Détection en temps réel validée"],
                "tags": ["splunk", "siem", "soc"],
                "relevance": "high"
            })

    from applypilot import llm
    monkeypatch.setattr(llm, "get_client", lambda quality=True: MockClient())

    new_proj, saved_path = experiences_manager.add_experience(
        "Déploiement de Splunk et création de règles de détection",
        path=test_json
    )

    assert new_proj["id"] == "siem-splunk-detection"
    assert new_proj["title"] == "Plateforme SIEM Splunk & Détection d'Attaques"
    assert saved_path == test_json

    # Vérification fichier sauvegardé
    updated_data = json.loads(test_json.read_text(encoding="utf-8"))
    assert len(updated_data["experiences"]) == 2
    assert any(e["id"] == "siem-splunk-detection" for e in updated_data["experiences"])
    # Vérification enrichissement des skills
    assert "Splunk" in updated_data["skills"]["securite_defensive_systeme"]

    # Vérification présence de la sauvegarde .bak
    bak_file = tmp_path / "experiences.json.bak"
    assert bak_file.exists()


def test_add_experience_empty_error():
    with pytest.raises(ValueError):
        experiences_manager.add_experience("   ")
