"""Module de gestion et d'enrichissement du profil d'expériences (experiences.json).

Permet d'ajouter de nouveaux projets ou expériences techniques en langage naturel,
de les structurer automatiquement au standard ATS (puces d'action chiffrées,
technologies, tags, etc.) et de les persister dans ~/.applypilot/experiences.json.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from applypilot import config

log = logging.getLogger(__name__)

EXPERIENCES_PATH = config.APP_DIR / "experiences.json"


def get_experiences_path(override_path: Path | str | None = None) -> Path:
    """Retourne le chemin absolu vers le fichier experiences.json."""
    if override_path:
        return Path(override_path)
    return EXPERIENCES_PATH


def load_experiences(path: Path | str | None = None) -> dict[str, Any]:
    """Charge le fichier JSON d'expériences avec fallback si vide."""
    p = get_experiences_path(path)
    if not p.exists():
        log.warning("Fichier %s inexistant, création d'une structure de base.", p)
        return {
            "candidate": {
                "full_name": "Ali Jamai",
                "title": "Élève-ingénieur en cybersécurité — INSA Centre Val de Loire",
                "objective": "Stage de 6 mois en cybersécurité",
            },
            "education": [],
            "experiences": [],
            "skills": {},
            "certifications": [],
            "languages": [],
            "achievements": [],
            "soft_skills": [],
            "loisirs": [],
        }
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        log.error("Erreur de lecture de %s: %s", p, exc)
        raise


def save_experiences(data: dict[str, Any], path: Path | str | None = None) -> Path:
    """Sauvegarde le profil dans experiences.json avec création d'une sauvegarde (.bak)."""
    p = get_experiences_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        bak_path = p.with_suffix(".json.bak")
        try:
            shutil.copy2(p, bak_path)
        except Exception as exc:
            log.warning("Échec de la sauvegarde préalable .bak : %s", exc)

    # Écriture atomique
    tmp_path = p.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp_path.replace(p)
    log.info("Fichier d'expériences sauvegardé avec succès : %s", p)
    return p


def _slugify(text: str) -> str:
    """Génère un identifiant court en kebab-case."""
    s = text.lower()
    s = re.sub(r"[àáâãäå]", "a", s)
    s = re.sub(r"[èéêë]", "e", s)
    s = re.sub(r"[ìíîï]", "i", s)
    s = re.sub(r"[òóôõö]", "o", s)
    s = re.sub(r"[ùúûü]", "u", s)
    s = re.sub(r"[ç]", "c", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s[:32] or "projet-tech"


def _fallback_structure_project(raw_text: str, existing_ids: set[str]) -> dict[str, Any]:
    """Structure déterministe minimale en cas d'indisponibilité du LLM."""
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    first_line = lines[0] if lines else raw_text[:50]
    # Titre simplifié
    title = re.sub(r"^(?:projet|lab|stage|développement)\s*[:–—\-]\s*", "", first_line, flags=re.I).strip()
    if not title:
        title = "Nouveau Projet Cybersécurité"
    title = title[:60].capitalize()

    base_id = _slugify(title)
    cand_id = base_id
    counter = 2
    while cand_id in existing_ids:
        cand_id = f"{base_id}-{counter}"
        counter += 1

    bullets = []
    if len(lines) > 1:
        for l in lines[1:4]:
            clean_l = l.lstrip("-*•> ").strip()
            if clean_l:
                bullets.append(f"**Déployé et configuré** {clean_l}")
    else:
        bullets.append(f"**Conçu et mis en œuvre** le projet : {raw_text.strip()}")

    # Extraction naïve de technos
    known_techs = [
        "Python", "Bash", "PowerShell", "Linux", "Windows", "Docker", "Active Directory",
        "Splunk", "Wazuh", "Elasticsearch", "Kibana", "Wireshark", "Nmap", "Burp Suite",
        "Snort", "Suricata", "AWS", "Azure", "GCP", "Git", "C", "SQL", "Debian", "Kali"
    ]
    found_techs = [t for t in known_techs if re.search(rf"\b{re.escape(t)}\b", raw_text, re.I)]
    if not found_techs:
        found_techs = ["Cybersécurité", "Linux"]

    now = datetime.now()
    french_months = [
        "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
        "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"
    ]
    cur_date = f"{french_months[now.month - 1]} {now.year}"

    return {
        "id": cand_id,
        "type": "projet_technique",
        "title": title,
        "org": "Projet académique INSA CVL",
        "team": False,
        "dates": cur_date,
        "role": "Concepteur & développeur sécurité",
        "context": raw_text[:140].strip(),
        "bullets": bullets,
        "tech": found_techs,
        "results": ["Livrable fonctionnel et validé"],
        "links": [],
        "tags": [t.lower().replace(" ", "-") for t in found_techs[:5]],
        "relevance": "high",
    }


def structure_project_with_ai(raw_text: str, existing_ids: set[str]) -> dict[str, Any]:
    """Utilise le LLM pour formater le projet brut au standard ATS d'Ali Jamai."""
    try:
        from applypilot.llm import get_client
        client = get_client(quality=True)
    except Exception as exc:
        log.warning("Client LLM non disponible : %s. Utilisation du fallback.", exc)
        return _fallback_structure_project(raw_text, existing_ids)

    now = datetime.now()
    french_months = [
        "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
        "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"
    ]
    cur_date = f"{french_months[now.month - 1]} {now.year}"

    system_prompt = (
        "Tu es un expert en recrutement tech & cybersécurité et en mise en valeur ATS. "
        "Tu aides Ali Jamai (élève-ingénieur cybersécurité à l'INSA Centre Val de Loire) "
        "à ajouter un projet ou une expérience dans sa base de compétences.\n"
        "RÈGLES D'OR DU FORMAT ATS ALI JAMAI :\n"
        "1. Chaque puce de réalisation (bullets) DOIT impérativement commencer par un verbe d'action "
        "au passé composé EN GRAS : ex. '**Déployé une architecture...**', '**Configuré les sondes...**', "
        "'**Réalisé l'analyse...**'.\n"
        "2. Formulations précises, concrètes, techniques et sans blabla vague.\n"
        "3. Aucun chiffre farfelu inventé, mais valorisation des aspects techniques réels mentionnés.\n"
        "4. Tu réponds UNIQUEMENT par un objet JSON valide, sans balises markdown ```json et sans texte avant/après."
    )

    user_prompt = (
        f"DESCRIPTION FOURNIE PAR LE CANDIDAT :\n«««\n{raw_text}\n»»»\n\n"
        f"DATE PAR DÉFAUT SI NON PRÉCISÉE : {cur_date}\n"
        f"IDENTIFIANTS DÉJÀ EXISTANTS (À NE PAS RÉUTILISER) : {list(existing_ids)[:20]}\n\n"
        "Génère un JSON avec STRICTEMENT cette structure :\n"
        "{\n"
        '  "id": "identifiant-unique-court-kebab-case",\n'
        '  "type": "projet_technique",\n'
        '  "title": "Titre clair et valorisant du projet",\n'
        '  "org": "Projet académique INSA CVL ou Projet personnel ou nom organisation",\n'
        '  "team": false,\n'
        f'  "dates": "{cur_date}",\n'
        '  "role": "Intitulé du rôle (ex: Ingénieur sécurité, Analyste SOC, Pentester...)",\n'
        '  "context": "1 phrase synthétique de contexte",\n'
        '  "bullets": [\n'
        '    "**Verbe au passé composé** : détail technique et impact.",\n'
        '    "**Verbe au passé composé** : détail technique et impact."\n'
        '  ],\n'
        '  "tech": ["Outil1", "Langage2", "Protocole3"],\n'
        '  "results": ["Résultat ou livrable concret"],\n'
        '  "links": [],\n'
        '  "tags": ["tag1", "tag2", "tag3"],\n'
        '  "relevance": "high"\n'
        "}"
    )

    try:
        raw_res = client.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=1500,
            temperature=0.3,
        )
        if not raw_res:
            return _fallback_structure_project(raw_text, existing_ids)

        # Extraction JSON
        m = re.search(r"\{.*\}", raw_res, re.DOTALL)
        if not m:
            return _fallback_structure_project(raw_text, existing_ids)
        parsed = json.loads(m.group(0))

        # Garantir l'unicité de l'ID
        base_id = _slugify(str(parsed.get("id") or parsed.get("title") or "projet"))
        cand_id = base_id
        counter = 2
        while cand_id in existing_ids:
            cand_id = f"{base_id}-{counter}"
            counter += 1
        parsed["id"] = cand_id

        # Vérification des champs requis
        parsed.setdefault("type", "projet_technique")
        parsed.setdefault("title", "Nouveau Projet")
        parsed.setdefault("org", "Projet académique INSA CVL")
        parsed.setdefault("team", False)
        parsed.setdefault("dates", cur_date)
        parsed.setdefault("role", "Concepteur & développeur cybersécurité")
        parsed.setdefault("context", raw_text[:140].strip())
        parsed.setdefault("bullets", [f"**Conçu et déployé** le projet : {raw_text[:100]}."])
        parsed.setdefault("tech", ["Cybersécurité"])
        parsed.setdefault("results", ["Livrable fonctionnel"])
        parsed.setdefault("links", [])
        parsed.setdefault("tags", [t.lower().replace(" ", "-") for t in parsed["tech"][:5]])
        parsed.setdefault("relevance", "high")

        # Formatage des puces si le gras a été omis par le LLM
        clean_bullets = []
        for b in parsed["bullets"]:
            b_str = str(b).strip()
            if not b_str.startswith("**") and ":" in b_str:
                prefix, rest = b_str.split(":", 1)
                b_str = f"**{prefix.strip()}** :{rest}"
            clean_bullets.append(b_str)
        parsed["bullets"] = clean_bullets

        return parsed
    except Exception as exc:
        log.warning("Échec structuration IA (%s). Utilisation du fallback.", exc)
        return _fallback_structure_project(raw_text, existing_ids)


def add_experience(raw_text: str, path: Path | str | None = None) -> tuple[dict[str, Any], Path]:
    """Ajoute une nouvelle expérience dans experiences.json à partir d'une description libre.

    Retourne le dictionnaire du projet structuré et le chemin du fichier enregistré.
    """
    raw_clean = (raw_text or "").strip()
    if not raw_clean:
        raise ValueError("La description du projet est vide.")

    exp_data = load_experiences(path)
    existing_exps = exp_data.setdefault("experiences", [])
    existing_ids = {e.get("id") for e in existing_exps if isinstance(e, dict) and e.get("id")}

    # Structuration par IA
    new_project = structure_project_with_ai(raw_clean, existing_ids)

    # Ajout au catalogue d'expériences (sera sélectionné par l'IA selon la pertinence de l'offre)
    existing_exps.append(new_project)

    # Enrichissement dynamique de la section skills si applicable
    skills = exp_data.setdefault("skills", {})
    tech_set = set(new_project.get("tech", []))
    for t in tech_set:
        tl = t.lower()
        if any(k in tl for k in ["splunk", "wazuh", "elk", "elastic", "suricata", "zeek", "snort", "qradar"]):
            cat = "securite_defensive_systeme"
            if cat in skills and t not in skills[cat]:
                skills[cat].append(t)
        elif any(k in tl for k in ["burp", "metasploit", "nmap", "hashcat", "hydra", "impacket", "sqlmap"]):
            cat = "securite_offensive"
            if cat in skills and t not in skills[cat]:
                skills[cat].append(t)
        elif any(k in tl for k in ["python", "c", "c++", "rust", "go", "bash", "powershell", "react", "fastapi"]):
            cat = "developpement"
            if cat in skills and t not in skills[cat]:
                skills[cat].append(t)

    saved_path = save_experiences(exp_data, path)
    return new_project, saved_path
