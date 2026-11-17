"""Génération d'un CV LaTeX adapté à une offre — sans invention.

Principe anti-invention (règle absolue d'Ali) : le LLM ne fait que
**sélectionner et ordonner** des éléments RÉELS déjà présents dans
`~/.applypilot/experiences.json`. Les puces d'expériences sont reprises
**verbatim** depuis ce fichier ; le modèle ne réécrit jamais les faits et
n'invente aucune compétence, expérience ni chiffre. Seul le court paragraphe
« Profil » est rédigé, en se limitant aux faits du fichier (objectif, écoles,
compétences listées).

Rendu : template LaTeX maison (photo circulaire, aucune parenthèse explicative),
compilé en PDF via latexmk/pdflatex (MiKTeX).
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

from applypilot import config

log = logging.getLogger("applypilot.cv")

CV_LATEX_DIR = config.APP_DIR / "cv_latex"
EXPERIENCES_PATH = config.APP_DIR / "experiences.json"
EXPERIENCES_EN_PATH = config.APP_DIR / "experiences_en.json"
PHOTO_REL = "assets/photo.jpg"  # relatif au dossier de compilation

# Libellés d'affichage des catégories de compétences.
SKILL_LABELS = {
    "securite_audits": "Audit \\& GRC",
    "active_directory_identite": "Active Directory",
    "systemes_reseaux": "Systèmes \\& Réseau",
    "ia_data": "IA \\& Data",
    "devops_devsecops": "DevOps \\& Scripts",
    "securite_offensive_pentest": "Sécurité offensive \\& Pentest",
    "developpement": "Développement",
    "infrastructure_reseau": "Infrastructure réseau",
    "securite_defensive_systeme": "Détection \\& SIEM",
    "cloud_iam": "Cloud \\& IAM",
}

SKILL_LABELS_EN = {
    "securite_audits": "Audit \\& GRC",
    "active_directory_identite": "Active Directory \\& IAM",
    "systemes_reseaux": "Systems \\& Networking",
    "ia_data": "AI \\& Data",
    "devops_devsecops": "DevSecOps \\& Automation",
    "securite_offensive_pentest": "Offensive Security \\& Pentest",
    "developpement": "Development",
    "infrastructure_reseau": "Network Infrastructure",
    "securite_defensive_systeme": "Detection \\& SIEM",
    "cloud_iam": "Cloud \\& IAM",
}

RESERVE_BRANCHES_EN: dict[str, dict[str, str]] = {
    "SOC": {
        "title": "SOC Analyst",
        "profil": "Cybersecurity engineering student at INSA Centre Val de Loire (TOEIC 980/990), seeking a 6-month SOC Analyst & Incident Response internship starting April 2027. Hands-on experience with Splunk (SPL rules), Wazuh SIEM, DFIR-IRIS investigations, and threat hunting.",
    },
    "Pentest": {
        "title": "Penetration Testing & Offensive Security",
        "profil": "Cybersecurity engineering student at INSA Centre Val de Loire (HTB CPTS in progress, 4th/42 CTF Cybersup x Root-Me Pro), seeking a 6-month Offensive Security / Penetration Testing internship starting April 2027. Proven hands-on audits across Active Directory and OWASP Top 10 web applications.",
    },
    "GRC": {
        "title": "Cybersecurity GRC & Risk Management",
        "profil": "Cybersecurity engineering student at INSA Centre Val de Loire (TOEIC 980/990), seeking a 6-month Cybersecurity GRC & Risk internship starting April 2027. Practical experience building an ISO/IEC 27001 ISMS and conducting Azure cloud risk assessments mapped to NIST CSF and CIS Controls.",
    },
    "Audit": {
        "title": "IT Security & Cybersecurity Audit",
        "profil": "Cybersecurity engineering student at INSA Centre Val de Loire (TOEIC 980/990), seeking a 6-month IT & Cybersecurity Audit internship starting April 2027. Strong technical foundation spanning Active Directory audits, ISO/IEC 27001 compliance, NIST CSF risk assessments, and technical reporting.",
    },
    "AD": {
        "title": "IAM, Active Directory & Entra ID",
        "profil": "Cybersecurity engineering student at INSA Centre Val de Loire (TOEIC 980/990), seeking a 6-month IAM & Active Directory / Microsoft Entra ID internship starting April 2027. Experienced in AD DS deployment, PowerShell automation, Entra ID RBAC/SSO/MFA, and Active Directory security hardening.",
    },
    "Reseau": {
        "title": "Network & Security Engineering",
        "profil": "Cybersecurity engineering student at INSA Centre Val de Loire (TOEIC 980/990), seeking a 6-month Network & Security Engineering internship starting April 2027. Hands-on experience with pfSense segmentation, OpenVPN/IPsec tunnels, Active Directory integration, and network attack-surface reduction.",
    },
    "DevOps": {
        "title": "DevSecOps & Cloud Security",
        "profil": "Cybersecurity engineering student at INSA Centre Val de Loire (TOEIC 980/990), seeking a 6-month DevSecOps internship starting April 2027. Experience securing Kubernetes platforms (RBAC, Ansible hardening, TLS) and automating SAST/DAST CI pipelines (Semgrep, Trivy, gitleaks).",
    },
    "Cloud": {
        "title": "Cloud Security & IAM",
        "profil": "Cybersecurity engineering student at INSA Centre Val de Loire (TOEIC 980/990), seeking a 6-month Cloud Security internship starting April 2027. Hands-on projects across Azure cloud risk assessment (NIST CSF / CIS Controls), Microsoft Entra ID IAM, cloud Active Directory, and Kubernetes hardening.",
    },
    "IA": {
        "title": "AI & Cybersecurity Engineering",
        "profil": "Cybersecurity engineering student at INSA Centre Val de Loire (TOEIC 980/990), seeking a 6-month AI & Cybersecurity internship starting April 2027. Built an AI vulnerability triage engine (SecBERT, 11,000+ cases), ML anomaly detection on Wazuh SIEM logs, and least-privilege AI automation workflows.",
    },
    "Cyber": {
        "title": "Cybersecurity Engineering",
        "profil": "Cybersecurity engineering student at INSA Centre Val de Loire (TOEIC 980/990, HTB CPTS in progress), seeking a 6-month Cybersecurity internship starting April 2027. Broad hands-on foundation spanning Active Directory / Entra ID, offensive audits, SIEM/SOC investigations, network hardening, and ISO 27001 GRC.",
    },
}

# Types d'expériences classés comme « Expériences » (le reste = « Projets »).
_PRO_TYPES = {"experience_pro", "challenge"}

# Expériences obligatoires : le projet Airbus doit apparaître dans TOUS les CV sans exception.
MANDATORY_EXP_IDS = ["etude-hydrogene-airbus"]

# Expériences professionnelles fixes : apparaissent TOUJOURS dans cet ordre exact
# dans \section{Expériences professionnelles}, avec leur première puce verbatim (une puce chacune).
FIXED_PRO_EXP_IDS = [
    "etude-hydrogene-airbus",
    "shellter",
    "cyberquest",
]

# Exception : CyberQuest (plateforme de formation, sans employeur) n'apparaît que
# sur les branches où il sert l'offre. Les expériences absentes de ce
# dictionnaire apparaissent sur toutes les branches.
FIXED_PRO_BRANCHES = {"cyberquest": ("IA", "GRC", "Cyber")}

# Branches où les distinctions hors CTF (concours de nouvelles…) sont affichées.
DISTINCTION_BRANCHES = ("GRC", "Audit")

# Bloc compétences : 4 lignes de 6 éléments au plus.
MAX_SKILL_LINES = 4
MAX_SKILLS_PER_LINE = 6


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------
def load_experiences(lang: str = "fr") -> dict:
    base = json.loads(EXPERIENCES_PATH.read_text(encoding="utf-8"))
    if lang == "en" and EXPERIENCES_EN_PATH.exists():
        en = json.loads(EXPERIENCES_EN_PATH.read_text(encoding="utf-8"))
        # Fusionner automatiquement toute nouvelle expérience ajoutée à experiences.json
        en_ids = {e["id"] for e in en.get("experiences", [])}
        for exp in base.get("experiences", []):
            if exp.get("id") not in en_ids:
                en.setdefault("experiences", []).append(exp)
        return en
    return base


_EN_TITLE_RE = re.compile(
    r"(?<![a-z])("
    r"intern|internship|working student|graduate|trainee|master thesis|bachelor thesis|thesis|"
    r"cybersecurity|cyber security|information security|network engineer|security analyst|"
    r"governance|compliance|resilience|access control|developer|software engineer|securing|"
    r"praktikum|praktikant|hochschulpraktikant|praktikplats|exjobb|werkstudent|m/f/x|m/w/d|f/m/d"
    r")(?![a-z])"
)
_FR_TITLE_RE = re.compile(
    r"(?<![a-z])("
    r"stage|stagiaire|stagiaires|alternance|alternant|cybersecurite|securite|"
    r"ingenieur|administrateur|analyste|developpeur|reseau|reseaux|systeme|systemes|"
    r"conseil|audit it|charge|chef de projet|h/f|f/h|h/f/x"
    r")(?![a-z])"
)
_EN_STOPWORDS_RE = re.compile(r"\b(the|and|with|for|your|will|our|team|experience|skills|responsibilities|requirements|join|you|we are looking)\b")
_FR_STOPWORDS_RE = re.compile(r"\b(le|la|les|des|pour|avec|vous|nous|equipe|missions|profil|competences|stage|entreprise|rejoignez|votre)\b")


def offer_language(offer: dict) -> str:
    """Détecte la langue de l'offre ('en' ou 'fr') pour adapter la langue du CV."""
    note = _plain(offer.get("user_note") or offer.get("note") or "")
    if re.search(r"\b(en anglais|in english|english|cv anglais|cv en)\b", note):
        return "en"
    if re.search(r"\b(en francais|in french|french|cv francais|cv fr)\b", note):
        return "fr"

    title_raw = str(offer.get("title") or "")
    title = _plain(title_raw)
    desc = _plain(" ".join(str(offer.get(k) or "") for k in ("full_description", "description")))

    has_fr_title = bool(_FR_TITLE_RE.search(title))
    has_en_title = bool(_EN_TITLE_RE.search(title))
    has_french_accents = bool(re.search(r"[éèêëàâçùûôîïœ]", title_raw.lower()))

    if has_en_title and not has_fr_title and not has_french_accents:
        return "en"
    if has_fr_title and not has_en_title:
        return "fr"

    if desc:
        en_hits = len(_EN_STOPWORDS_RE.findall(desc[:2500]))
        fr_hits = len(_FR_STOPWORDS_RE.findall(desc[:2500]))
        if en_hits >= 5 and en_hits > fr_hits * 1.3:
            return "en"
        if fr_hits >= 5 and fr_hits > en_hits * 1.3:
            return "fr"

    if re.search(r"\b(stage|stagiaire|h/f|f/h)\b", title):
        return "fr"
    if has_en_title:
        return "en"
    return "fr"


_LATEX_SPECIALS = {
    "\\": r"\textbackslash{}",
    "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}",
    "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}
_UNICODE_FIX = {
    "→": " vers ", "×": " @@TIMES@@ ", "…": "...", "\u00a0": "~", "‰": "pour mille",
    "’": "'", "‘": "'", "“": "\"", "”": "\"",
}


# Remplacements supplémentaires de symboles Unicode non gérés par pdflatex.
_EXTRA_FIX = {
    "←": " ", "↔": " ", "⇒": " vers ", "÷": "/", " ": "~",
    "‚": ",", "„": "\"", "–": "--", "—": "---", "‑": "-", "−": "-",
    "•": "- ", "·": "-", "◦": "- ", "≥": ">=", "≤": "<=", "≈": "~",
    "≠": "!=", "€": " EUR", "£": " GBP", "²": "2", "³": "3", "¹": "1",
    "½": "1/2", "¼": "1/4", "¾": "3/4", "™": "", "®": "", "©": "",
    "​": "", "﻿": "",
}
# Lettres acceptées au-delà de l'ASCII (babel french les gère) ; le reste est retiré.
_FRENCH_OK = set("àâäçéèêëîïòôöùûüÿœæÀÂÄÇÉÈÊËÎÏÒÔÖÙÛÜŸŒÆ«»°")


def latex_escape(s: str) -> str:
    """Échappe le texte brut pour LaTeX et neutralise tout caractère Unicode
    non géré par pdflatex (sinon « Unicode character not set up » = échec)."""
    if not s:
        return ""
    # Gestion propre du symbole multiplication avec un seul espace autour
    s = re.sub(r"\s*×\s*", " @@TIMES@@ ", s)
    for uni, repl in _UNICODE_FIX.items():
        if uni != "×":
            s = s.replace(uni, repl)
    for uni, repl in _EXTRA_FIX.items():
        s = s.replace(uni, repl)
    out = []
    for ch in s:
        if ch in _LATEX_SPECIALS:
            out.append(_LATEX_SPECIALS[ch])
        elif ord(ch) < 128 or ch in _FRENCH_OK:
            out.append(ch)
        # tout autre caractère Unicode exotique est ignoré (évite l'échec LaTeX)
    return "".join(out).replace("@@TIMES@@", r"$\times$")


def cv_prefix(exp_data: dict | None = None) -> str:
    """Préfixe des fichiers de CV, d'après le nom du candidat : « CV_Prenom_Nom ».
    12 caractères au plus, pour que « <préfixe>_<Branche>.pdf » reste sous les 25
    caractères qu'acceptent tous les portails ; un nom long devient « CV_P_Nom »."""
    import unicodedata

    name = ((exp_data or load_experiences()).get("candidate", {}) or {}).get("full_name") or "Candidat"
    ascii_name = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
    words = re.findall(r"[A-Za-z]+", ascii_name) or ["Candidat"]
    full = "CV_" + "_".join(words)
    if len(full) <= 12:
        return full
    short = f"CV_{words[0][0]}_{words[-1]}" if len(words) > 1 else f"CV_{words[0]}"
    return short[:12].rstrip("_")


def _link_label(url: str) -> str:
    """Texte affiché pour un lien : l'adresse sans « https://www. »."""
    return re.sub(r"^https?://(www\.)?", "", str(url or "")).rstrip("/")


def _is_reference_catalog(exp_data: dict) -> bool:
    """Vrai pour le catalogue de l'auteur d'origine, dont certaines phrases de
    secours citent nommément les projets. Tout autre candidat reçoit les textes
    génériques, construits à partir de son propre fichier."""
    ids = {e.get("id") for e in exp_data.get("experiences", [])}
    return {"pentest-ad", "shellter"} <= ids


def _offer_text(offer: dict) -> str:
    return " ".join(str(offer.get(k, "")) for k in
                    ("title", "company", "site", "location",
                     "full_description", "description", "score_reasoning")).lower()


# ---------------------------------------------------------------------------
# Sélection des expériences pertinentes
# ---------------------------------------------------------------------------
def _default_selection(exp_data: dict, offer: dict) -> dict:
    """Sélection déterministe (repli sans LLM) : score par recouvrement de tags
    et de technologies avec le texte de l'offre, priorité à relevance=high."""
    text = _offer_text(offer)
    rel_weight = {"high": 3, "medium": 1, "low": 0}
    scored = []
    for exp in exp_data["experiences"]:
        s = rel_weight.get(exp.get("relevance"), 0)
        for tag in exp.get("tags", []):
            if tag.replace("-", " ") in text or tag.replace("-", "") in text:
                s += 2
        for tech in exp.get("tech", []):
            if tech.lower() in text:
                s += 1
        scored.append((s, exp["id"]))
    scored.sort(key=lambda x: x[0], reverse=True)
    # Expériences obligatoires (paramétrables dans cv_settings.mandatory_experience_ids)
    mand_cfg = exp_data.get("cv_settings", {}).get("mandatory_experience_ids", MANDATORY_EXP_IDS)
    mandatory = [m for m in mand_cfg if any(e['id'] == m for e in exp_data['experiences'])]
    other_ids = [i for _, i in scored if i not in mand_cfg]
    ids = mandatory + other_ids[:6]

    # Ordre des compétences : catégories dont un terme apparaît dans l'offre d'abord.
    skills = exp_data["skills"]
    def skill_hits(cat: str) -> int:
        return sum(1 for t in skills[cat] if t.lower() in text)
    skills_order = sorted(skills.keys(), key=skill_hits, reverse=True)

    return {
        "profil": _default_profil(exp_data, offer),
        "selected_experience_ids": ids,
        "skills_order": skills_order,
    }


def _default_profil(exp_data: dict, offer: dict) -> str:
    """Accroche de secours (si le LLM est indisponible) : professionnelle,
    concrète et alignée sur le template ATS.

    Elle suit l'angle du bandeau (même détection que le nom de fichier) pour ne
    pas annoncer « audit et gouvernance » sous un titre Pentest ou Active
    Directory. Chaque variante ne cite que des faits de experiences.json.
    """
    # Courte, et tournée vers la motivation et l'envie d'apprendre : la
    # disponibilité, le domaine, ce qui m'y attire et ce que je veux y apprendre.
    # Les projets sont détaillés plus bas dans le CV : elle ne les répète pas.
    if not _is_reference_catalog(exp_data):
        # Aucun texte propre à un candidat : son objectif, tel qu'il l'a écrit.
        objective = str((exp_data.get("candidate", {}) or {}).get("objective") or "").strip()
        return objective or "Disponible pour un stage, je cherche une équipe auprès de laquelle apprendre vite."
    intro = "Disponible pour un **stage de 4 à 5 mois à partir d'avril 2027** (prolongation possible) en "

    # Sans titre, le bandeau affiche « AUDIT CYBERSÉCURITÉ & GRC » : même angle ici.
    tag = _title_tag(_norm_offer_title(offer)) if _norm_offer_title(offer).strip() else "GRC"
    if tag in ("Audit", "GRC"):
        return (intro + "audit et gouvernance cybersécurité. J'aime comprendre un système assez finement pour "
                "dire où il est fragile et comment le corriger. Je veux apprendre la démarche complète, du "
                "cadrage à la restitution, auprès d'une équipe qui la pratique chaque jour.")
    if tag == "Pentest":
        return (intro + "test d'intrusion et sécurité offensive. J'aime chercher la faille que personne n'a "
                "vue, et je progresse en pratiquant : CTF, labos personnels, certification CPTS en "
                "préparation. Je cherche un stage où confronter cette pratique à de vrais périmètres et "
                "apprendre la méthode d'une équipe d'audit.")
    if tag in ("AD", "Reseau"):
        return (intro + "administration systèmes, réseaux et Active Directory. J'ai appris les bases en "
                "montant mes propres maquettes. Je veux maintenant apprendre à exploiter et sécuriser une "
                "vraie infrastructure, avec les contraintes de production que mes labos n'ont pas.")
    if tag in ("DevOps", "Cloud"):
        return (intro + "DevOps et DevSecOps. Mon premier stage m'a donné le goût d'intégrer la sécurité dès "
                "la chaîne de build plutôt qu'après coup. Je veux approfondir cette pratique dans une équipe "
                "qui livre en continu, et apprendre à automatiser les contrôles sans ralentir les développeurs.")
    return (intro + "sécurité des systèmes d'information. Je me forme autant par la pratique que par mes "
            "cours : labos personnels, CTF, certification CPTS en préparation. Je cherche un stage qui me "
            "confronte à de vrais systèmes, dans une équipe auprès de laquelle apprendre vite.")


def _llm_selection(offer: dict, exp_data: dict) -> dict | None:
    """Demande à Groq la sélection + un court profil. Renvoie None si échec."""
    try:
        from applypilot.llm import get_client
    except Exception as exc:  # pragma: no cover
        log.warning("LLM indisponible : %s", exc)
        return None

    # Catalogue compact (ids + titres + tags) et les deux premières puces de
    # chaque expérience : sans elles, le modèle ne connaît pas les chiffres réels
    # et brode sur les titres pour rédiger l'accroche.
    catalog = [
        {"id": e["id"], "titre": e["title"], "type": e["type"],
         "tags": e.get("tags", []), "tech": e.get("tech", [])[:8],
         "faits": [str(b).replace("**", "") for b in e.get("bullets", [])[:2]]}
        for e in exp_data["experiences"]
    ]
    skills_cats = list(exp_data["skills"].keys())
    cand = exp_data["candidate"]

    # Preuves d'initiative que l'accroche peut citer (tirées du catalogue, jamais inventées).
    preuves_list = ["projets et labos personnels menés sur mon temps libre"]
    if any("ctf" in str(a).lower() for a in exp_data.get("achievements", [])):
        preuves_list.append("participation à des CTF")
    preuves_list += [f"certification {c}" for c in exp_data.get("certifications", [])[:1]]
    preuves = " ; ".join(preuves_list)

    # Consigne « accroche » : la motivation et l'envie d'apprendre, alignées sur
    # l'angle de l'offre. Les projets sont déjà détaillés dans le CV : l'accroche
    # ne les répète pas.
    availability = str(cand.get("availability") or "").strip()
    if not availability and _is_reference_catalog(exp_data):
        availability = "Disponible pour un stage de 6 mois à partir d'avril 2027."
    phrase1 = (f"Phrase 1, mot pour mot : « {availability} » " if availability else
               "Phrase 1 : ma disponibilité (durée et date de début), reprise de OBJECTIF DU CANDIDAT. ")
    profil_rule = (
        "Rédige une accroche de CV en français qui montre ma MOTIVATION et mon "
        "ENVIE D'APPRENDRE, pas un résumé de mon parcours. COURTE : 3 phrases, "
        "60 mots MAXIMUM au total, à la première personne, ton sincère et "
        "direct, comme si je parlais à un recruteur. "
        + phrase1 +
        "Phrase 2 : ce qui m'attire CONCRÈTEMENT dans CE poste, d'après la "
        "description de l'offre (une mission, un type de problème à résoudre, "
        "un domaine technique : gouvernance/GRC/audit, Active Directory, "
        "pentest, SOC/détection, cloud/DevSecOps, réseau…). Parle du métier, "
        "pas de l'entreprise : aucun compliment à l'employeur. "
        "Phrase 3 : ce que je veux y APPRENDRE, et le fait que j'apprends par "
        "la pratique et de ma propre initiative. Tu peux citer UNE seule preuve "
        "de cette initiative parmi : " + preuves + " — sans décrire aucun projet. "
        "INTERDIT : résumer, lister ou chiffrer mes projets et expériences (ils "
        "sont déjà détaillés plus bas dans le CV) ; répéter « élève-ingénieur en "
        "cybersécurité », le nom de l'école ou l'intitulé du poste ; prétendre "
        "maîtriser un sujet. "
        "N'utilise JAMAIS les formules IA / CV bateau : « Fort de », « Passionné "
        "par », « Doté d'une solide », « mettre à profit », « évoluer au sein "
        "de », « environnement stimulant/dynamique », « alliant ... et ... », "
        "« au carrefour de », « je suis convaincu », « rigoureux et motivé », "
        "« dynamique », « curieux de nature », ni les tirets cadratins. Aucun "
        "fait inventé, aucun chiffre, aucune parenthèse."
    )

    system = (
        "Tu aides à ADAPTER un CV à une offre de stage, en français. RÈGLE ABSOLUE : le projet Airbus (etude-hydrogene-airbus) DOIT TOUJOURS être inclus dans selected_experience_ids. "
        "RÈGLE ABSOLUE : tu ne sélectionnes et n'ordonnes que des éléments RÉELS "
        "fournis ; tu n'inventes AUCUNE compétence, expérience, entreprise, ÉCOLE, "
        "ville d'études ni chiffre. L'école du candidat est TOUJOURS celle indiquée "
        "ci-dessous, jamais celle de la ville de l'offre. Tu réponds UNIQUEMENT par "
        "un objet JSON valide, sans texte autour."
    )
    edu = (exp_data.get("education") or [{}])[0]
    school = " — ".join(str(x) for x in (edu.get("institution"), edu.get("degree"), edu.get("dates")) if x)
    fixed = (f"{cand.get('full_name')}, {cand.get('title')}. Formation en cours : {school}. "
             "NE JAMAIS remplacer cette école par une autre.")
    user_note = offer.get("user_note") or offer.get("note")
    note_prompt = ""
    if user_note:
        note_prompt = f"\nCONSIGNE DU CANDIDAT (À RESPECTER EN PRIORITÉ ABSOLUE DANS L'ACCROCHE ET LA SÉLECTION DES PROJETS/COMPÉTENCES) :\n« {user_note} »\n"
    base = offer.get("_base")
    if base:
        # Adaptation d'un CV de réserve : le modèle part de ce que le candidat a
        # validé et ne touche qu'à ce que la consigne ou l'offre exigent.
        note_prompt += (
            "\nCV DE BASE, RELU ET VALIDÉ PAR LE CANDIDAT (à modifier le moins possible) :\n"
            f"- accroche : « {base.get('profil')} »\n"
            f"- expériences, dans l'ordre : {base.get('selected_experience_ids')}\n"
            f"- compétences, dans l'ordre : {base.get('skills_order', [])[:MAX_SKILL_LINES]}\n"
            "PARS DE CETTE BASE. Ne change que ce que la consigne du candidat demande ou ce que "
            "l'offre rend nécessaire. Ce qui convient déjà, renvoie-le À L'IDENTIQUE : si l'accroche "
            "de base convient, recopie-la mot pour mot ; ne remplace une expérience que si une autre "
            "du catalogue colle nettement mieux à l'offre.\n")
    must = offer.get("_must")
    if must:
        note_prompt += (
            "\nCETTE OFFRE EXIGE DES ÉLÉMENTS QUE LE CANDIDAT POSSÈDE mais que le CV de base ne montre "
            f"pas : {', '.join(must)}.\nIL FAUT LES FAIRE APPARAÎTRE : mets dans selected_experience_ids "
            "les expériences qui les contiennent (regarde leurs champs tech et faits) et place en tête "
            "de skills_order les catégories de compétences correspondantes.\n")
    if offer.get("_reserve"):
        # CV de réserve : l'accroche resservira pour toutes les offres de la branche.
        note_prompt += ("\nCETTE ACCROCHE EST UN MODÈLE DE RÉSERVE pour toutes les offres de ce domaine : "
                        "ne cite AUCUNE entreprise, ville, équipe ni détail propre à une offre précise ; "
                        "parle de ce qui m'attire dans ce domaine et de ce que je veux y apprendre.\n")
    user = (
        f"CANDIDAT (faits fixes, à ne JAMAIS modifier) : {fixed}\n"
        f"OBJECTIF DU CANDIDAT : {cand.get('objective')}\n"
        f"{note_prompt}\n"
        f"OFFRE :\nTitre : {offer.get('title')}\nEntreprise : "
        f"{offer.get('company') or offer.get('site')}\nLieu : {offer.get('location')}\n"
        f"Description : {(offer.get('full_description') or offer.get('description') or offer.get('score_reasoning') or '')[:1500]}\n\n"
        f"EXPÉRIENCES DISPONIBLES (identifiants) :\n{json.dumps(catalog, ensure_ascii=False)}\n\n"
        f"CATÉGORIES DE COMPÉTENCES : {skills_cats}\n\n"
        "Renvoie un JSON avec EXACTEMENT ces clés :\n"
        '{\n'
        '  "profil": ' + json.dumps(profil_rule, ensure_ascii=False) + ',\n'
        '  "selected_experience_ids": ["<4 à 6 ids les plus pertinents POUR L\'ANGLE '
        'de cette offre, ordonnés du plus pertinent au moins>"],\n'
        '  "skills_order": ["<catégories de compétences, pertinentes en premier>"]\n'
        "}"
    )
    try:
        client = get_client(quality=True)  # meilleur suivi des instructions
        raw = client.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            max_tokens=2000, temperature=0.4)
    except Exception as exc:
        log.warning("Appel LLM échoué : %s", exc)
        return None

    data = _extract_json(raw)
    if not data:
        log.warning("Accroche LLM : réponse non-JSON (len=%d)", len(raw or ""))
        return None

    valid_ids = {e["id"] for e in exp_data["experiences"]}
    sel = [i for i in data.get("selected_experience_ids", []) if i in valid_ids]
    if not sel:
        return None
    order = [c for c in data.get("skills_order", []) if c in exp_data["skills"]]
    # Complète avec les catégories manquantes pour ne rien perdre.
    order += [c for c in exp_data["skills"] if c not in order]
    profil = (data.get("profil") or "").strip() or _default_profil(exp_data, offer)
    mand_cfg = exp_data.get("cv_settings", {}).get("mandatory_experience_ids", MANDATORY_EXP_IDS)
    for mand in mand_cfg:
        if mand in valid_ids and mand not in sel:
            sel.append(mand)
    return {"profil": profil, "selected_experience_ids": sel[:7], "skills_order": order}


def _extract_json(raw: str) -> dict | None:
    if not raw:
        return None
    # 1. Retirer d'éventuelles balises de raisonnement (<think>...</think>)
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    # 2. Chercher dans les blocs markdown ```json ... ``` ou ``` ... ```
    fence_matches = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    for block in fence_matches:
        try:
            res = json.loads(block)
            if isinstance(res, dict):
                return res
        except ValueError:
            cleaned = re.sub(r",\s*([\]\}])", r"\1", block)
            try:
                res = json.loads(cleaned)
                if isinstance(res, dict):
                    return res
            except ValueError:
                pass

    # 3. Recherche du bloc JSON { ... } équilibré
    start_indices = [i for i, ch in enumerate(text) if ch == "{"]
    for start in start_indices:
        for end in range(len(text) - 1, start, -1):
            if text[end] == "}":
                candidate = text[start:end + 1]
                try:
                    res = json.loads(candidate)
                    if isinstance(res, dict):
                        return res
                except ValueError:
                    cleaned = re.sub(r",\s*([\]\}])", r"\1", candidate)
                    try:
                        res = json.loads(cleaned)
                        if isinstance(res, dict):
                            return res
                    except ValueError:
                        pass
                break

    # 4. Fallback
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        res = json.loads(m.group(0))
        if isinstance(res, dict):
            return res
    except ValueError:
        cleaned = re.sub(r",\s*([\]\}])", r"\1", m.group(0))
        try:
            res = json.loads(cleaned)
            if isinstance(res, dict):
                return res
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Rendu LaTeX
# ---------------------------------------------------------------------------
_MONTHS = {
    "janvier": 1, "janv": 1, "jan": 1, "fevrier": 2, "fevr": 2, "fev": 2, "mars": 3, "avril": 4, "avr": 4,
    "mai": 5, "juin": 6, "juillet": 7, "juil": 7, "aout": 8, "septembre": 9, "sept": 9, "sep": 9,
    "octobre": 10, "oct": 10, "novembre": 11, "nov": 11, "decembre": 12, "dec": 12,
}


def _format_dates(raw_dates: str) -> str:
    """Un seul format de date sur tout le CV : MM/AAAA (« Avril 2025 -- Juillet
    2025 » devient « 04/2025 -- 07/2025 »). Plus lisible d'un coup d'œil et plus
    sûr pour les parseurs ATS qu'un mélange de mois en lettres et en chiffres."""
    if not raw_dates:
        return ""
    import unicodedata

    def month_to_number(m: re.Match) -> str:
        key = unicodedata.normalize("NFKD", m.group(1).lower()).encode("ascii", "ignore").decode("ascii")
        return f"{_MONTHS[key]:02d}/{m.group(2)}" if key in _MONTHS else m.group(0)

    s = re.sub(r"\s*(?:--|[–—−-])\s*", " -- ", raw_dates.strip())
    s = re.sub(r"([A-Za-zÀ-ÿ]+)\.?\s+((?:19|20)\d{2})", month_to_number, s)
    return latex_escape(s)


def tracer_is_public() -> bool:
    """Vrai si les liens traceurs sont joignables par un recruteur : adresse HTTPS
    publique. Une adresse locale ne doit JAMAIS finir dans un CV (lien mort pour
    le recruteur, et motif de rejet par les scanners des portails)."""
    base = config.TRACER_BASE_URL.lower()
    return base.startswith("https://") and not any(
        h in base for h in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "192.168.", "10.", "172."))


def _apply_tracer_links(pdf_path: Path, offer: dict) -> int:
    """Remplace, dans un PDF déjà compilé, les liens LinkedIn / GitHub / portfolio
    par les liens traceurs de l'offre. Rien d'autre ne change : ni le texte
    affiché, ni la mise en page. Sert aux CV de réserve, envoyés sans recompilation.
    Renvoie le nombre de liens remplacés (0 si le suivi n'est pas public)."""
    if not tracer_is_public() or not str(offer.get("url") or "").strip():
        return 0
    try:
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import NameObject, TextStringObject

        from applypilot.tracking.tracer import get_candidate_links, publish_tracers

        direct = get_candidate_links()
        tracers = publish_tracers(offer)
        if not tracers:
            return 0  # traceur hébergé injoignable : on garde les liens directs
        swap = {direct[k].rstrip("/"): tracers[k] for k in ("portfolio", "linkedin", "github")
                if direct.get(k) and tracers.get(k)}
        writer = PdfWriter(clone_from=PdfReader(str(pdf_path)))
        replaced = 0
        for page in writer.pages:
            for annot in page.get("/Annots") or []:
                action = (annot.get_object().get("/A") or {})
                action = action.get_object() if hasattr(action, "get_object") else action
                uri = str(action.get("/URI") or "").rstrip("/")
                if uri in swap:
                    action[NameObject("/URI")] = TextStringObject(swap[uri])
                    replaced += 1
        if replaced:
            tmp = pdf_path.with_suffix(".tmp.pdf")
            with open(tmp, "wb") as fh:
                writer.write(fh)
            tmp.replace(pdf_path)
        return replaced
    except Exception as exc:
        log.warning("Liens traceurs non posés sur %s : %s", pdf_path.name, exc)
        return 0


def _end_date_key(raw_dates: str) -> tuple[int, int]:
    """(année, mois) de la date de fin, pour trier du plus récent au plus ancien."""
    found = re.findall(r"(?:(\d{1,2})/)?((?:19|20)\d{2})", _format_dates(raw_dates))
    if not found:
        return (0, 0)
    month, year = found[-1]
    return (int(year), int(month or 0))


def _education_dates(ed: dict) -> str | None:
    """« MM/AAAA – MM/AAAA » à partir des champs start_date / end_date (JJ/MM/AAAA)."""
    parts = []
    for key in ("start_date", "end_date"):
        m = re.fullmatch(r"\s*\d{1,2}/(\d{1,2})/(\d{4})\s*", str(ed.get(key) or ""))
        if not m:
            return None
        parts.append(f"{int(m.group(1)):02d}/{m.group(2)}")
    return " – ".join(parts)


def _render_bullet(b: str) -> str:
    """Rend une puce en préservant le balisage gras **...** -> \\textbf{...}
    tout en échappant proprement le reste pour LaTeX."""
    if not b:
        return ""
    parts = re.split(r"(\*\*.*?\*\*)", b)
    out = []
    for p in parts:
        if p.startswith("**") and p.endswith("**"):
            inner = latex_escape(p[2:-2])
            out.append(f"\\textbf{{{inner}}}")
        else:
            out.append(latex_escape(p))
    return "".join(out)


def _render_xp_block(exp: dict, max_bullets: int = 3, lang: str = "fr") -> str:
    title = latex_escape(exp["title"])
    dates = _format_dates(exp.get("dates", ""))
    org = exp.get("org", "").strip()

    heading = title
    # Même convention pour tous : la nature du projet entre parenthèses après le titre.
    if org and org not in title:
        short_org = org
        if "challenge" in org.lower():
            short_org = "ECIU Challenge, Hamburg" if lang == "en" else "challenge ECIU, Hambourg"
        elif "ormvag" in org.lower() or "stage devops" in org.lower():
            short_org = "ORMVAG"
        elif "projet personnel" in org.lower() or "personal project" in org.lower():
            short_org = "personal project" if lang == "en" else "projet personnel"
        elif "projet académique" in org.lower() or "projet de cours" in org.lower() or "academic project" in org.lower():
            short_org = "academic project" if lang == "en" else "projet académique"
        heading = f"{title} \\textnormal{{\\color{{midgray}}({latex_escape(short_org)})}}"

    lines = [f"\\entry{{{heading}}}{{{dates}}}"]
    bullets = exp.get("bullets", [])[:max_bullets]
    if bullets:
        lines.append("\\begin{itemize}")
        for b in bullets:
            lines.append(f"  \\item {_render_bullet(b)}")
        lines.append("\\end{itemize}")
    return "\n".join(lines)


_GENDER_TAG = r"(?:H/F/X|H/F/D|M/F/X|M/F/D|M/W/D|F/M/D|F/M/N|F/H|H/F|M/F|F/M|W/M|H/X)"


def _clean_banner_title(title: str, lang: str = "fr") -> str:
    """Nettoie un intitulé d'offre pour le bandeau : retire la mention de genre
    (avec ses parenthèses, sinon il reste « () »), la référence numérique et le
    préfixe « Stage » / « Internship », puis les séparateurs laissés en bout de ligne."""
    t = re.sub(rf"[\(\[]\s*{_GENDER_TAG}\s*[\)\]]", "", title, flags=re.IGNORECASE)
    t = re.sub(rf"\b{_GENDER_TAG}\b", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\(\d+[-_]?\d*\)", "", t)
    t = re.sub(r"[\(\[]\s*[\)\]]", "", t)  # parenthèses vidées par les retraits ci-dessus
    while True:
        prev = t
        t = re.sub(
            r"^\s*(?:stage(?:\s+de\s+fin\s+d['’]études|\s+pfe|\s+d['’]ingénieur)?|stagiaire|élève[- ]ingénieur"
            + (r"|internships?|intern|working\s+student|graduate\s+program|graduate|trainee" if lang == "en" else "")
            + r")\s*[-–—:]?\s*",
            "",
            t,
            flags=re.IGNORECASE,
        )
        if t == prev:
            break
    if lang == "en":
        t = re.sub(r"\s*[-–—:]?\s*\b(?:internships?|intern)\b\s*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+", " ", t).strip()
    return re.sub(r"\s*[-–—/|,:]+$", "", t).strip()


def _extract_custom_title(note: str | None) -> str | None:
    """Extrait un intitulé de poste cible à partir de la consigne utilisateur."""
    if not note:
        return None
    note_clean = str(note).strip().strip("«»\"'")

    # 1. Nettoyage d'une formulation conversationnelle demandant un titre
    # ex: "pour le titre du cv met ca : Stagiaire...", "mets comme titre : ...", "change le titre en : ..."
    cleaned = re.sub(
        r"^(?:(?:s['’]il\s+te\s+pla[iî]t|stp|merci|bonjour)[,\s]+)*"
        r"(?:(?:pour\s+|en\s+|comme\s+)?(?:le\s+)?(?:titre|intitul[ée]|poste)(?:[\s_-]+(?:du[\s_-]*)?(?:cv|stage|poste))?"
        r"|(?:(?:change|remplace|modifie|mets?)\s+(?:comme\s+)?(?:le\s+)?(?:titre|intitul[ée]|poste))"
        r")"
        r"(?:\s+(?:du\s+cv|du\s+stage|du\s+poste))?"
        r"(?:\s+(?:met[ts]?|mettre|change|remplace|en|par|soit|c['’]est|ca|ça|ceci))*"
        r"\s*[:=\-–—]?\s*",
        "",
        note_clean,
        flags=re.IGNORECASE,
    ).strip().strip("«»\"'")

    if cleaned and cleaned != note_clean:
        cleaned = re.sub(r"[\.\!\?]+$", "", cleaned).strip().strip("«»\"'")
        if cleaned:
            return cleaned

    # 2. Format explicite classique : titre cv : ..., titre : ..., intitulé : ...
    m = re.search(
        r"(?:titre[\s_-]*(?:du[\s_-]*)?cv|titre|intitul[ée])\s*[:=-]\s*([^\n\r]+)",
        note_clean,
        re.IGNORECASE,
    )
    if m:
        val = m.group(1).strip().strip("«»\"'")
        val = re.sub(r"[\.\!\?]+$", "", val).strip()
        if val:
            return val

    # 3. Si la consigne est directement un nom de poste court (sans verbes de consigne technique)
    action_verbs = {"insister", "retirer", "mettre", "ajouter", "supprimer", "changer", "enlever", "modifier", "focus"}
    words = note_clean.lower().split()
    if len(words) <= 8 and not any(w in words for w in action_verbs):
        if words[0].startswith("stage") or words[0].startswith("stagiaire") or any(k in note_clean.lower() for k in [
            "cyber", "audit", "pentest", "securit", "devops", "cloud", "reseau", "analys", "ingénieur", "grc", "soc"
        ]):
            return note_clean

    return None


def _render_tex(exp_data: dict, selection: dict, offer: dict,
                max_exp: int = 7, max_bullets: int = 2, rich_projects: int = 1,
                lang: str = "fr") -> str:
    """``max_bullets`` puces pour les ``rich_projects`` premiers projets, une
    seule pour les suivants. Supporte ``lang='fr'`` (défaut) et ``lang='en'``."""
    is_en = (lang == "en")
    cand = exp_data["candidate"]
    by_id = {e["id"]: e for e in exp_data["experiences"]}

    # 1. Expériences professionnelles : toujours les mêmes, dans le même ordre,
    # une puce chacune (l'étude Airbus tient en une puce synthétique : la place
    # va aux projets cybersécurité).
    branch = offer_branch(offer)
    user_fixed = exp_data.get("cv_settings", {}).get("fixed_pro_experience_ids", FIXED_PRO_EXP_IDS)
    fixed_ids = [fid for fid in user_fixed
                 if fid in by_id and branch in FIXED_PRO_BRANCHES.get(fid, (branch,))]
    if not any(fid in by_id for fid in user_fixed):
        # Catalogue d'un autre candidat : ses expériences de type « experience_pro » / « challenge ».
        fixed_ids = [e["id"] for e in exp_data["experiences"] if e.get("type") in _PRO_TYPES]
    pro_ids = set(fixed_ids) | set(user_fixed)
    # La plus récente en premier (tri stable : sans date lisible, l'ordre de la liste est gardé).
    fixed_ids.sort(key=lambda fid: _end_date_key(by_id[fid].get("dates", "")), reverse=True)
    fixed_pro_entries = [_render_xp_block(by_id[fid], max_bullets=1, lang=lang) for fid in fixed_ids]

    exp_sec_title = "Professional Experience" if is_en else "Expériences professionnelles"
    sec_exp = (f"\\section{{{exp_sec_title}}}\n\n" + "\n\n".join(fixed_pro_entries)
               if fixed_pro_entries else "")

    # 2. Audits & projets cybersécurité : sélectionnés selon l'offre et adaptés à la page
    raw_sel = selection.get("selected_experience_ids", [])
    selected_proj_ids = [pid for pid in raw_sel if pid not in pro_ids and pid in by_id]
    if len(selected_proj_ids) < max_exp:
        for e in exp_data.get("experiences", []):
            if e["id"] not in pro_ids and e["id"] not in selected_proj_ids:
                selected_proj_ids.append(e["id"])
                if len(selected_proj_ids) >= max_exp:
                    break

    projets = [by_id[pid] for pid in selected_proj_ids[:max_exp]]
    proj_blocks = []
    for idx, p in enumerate(projets):
        b_count = max_bullets if idx < rich_projects else 1
        proj_blocks.append(_render_xp_block(p, max_bullets=b_count, lang=lang))

    sec_projets = ""
    if proj_blocks:
        default_proj_title = "Security Audits & Technical Projects" if is_en else "Audits & projets cybersécurité"
        projects_title = str((exp_data.get("cv_settings") or {}).get("projects_title")
                             or default_proj_title)
        sec_projets = f"\\section{{{latex_escape(projects_title)}}}\n\n" + "\n\n".join(proj_blocks)

    # Formation (format ATS propre)
    formation_lines = []
    for i, ed in enumerate(exp_data.get("education", [])):
        raw_inst = str(ed.get("institution") or "")
        raw_deg = str(ed.get("degree") or "")
        raw_dates = str(ed.get("dates") or "")

        # Remplacement de l'intitulé pour le profil de référence uniquement
        if _is_reference_catalog(exp_data) and any("insa" in s.lower() for s in [raw_inst, raw_deg]):
            if is_en:
                inst_text = "Master of Engineering (M.Eng.), Information Systems Security (STI)"
                deg_text = "INSA Centre Val de Loire, Bourges, France"
            else:
                inst_text = "Diplôme d'ingénieur (Bac+5), spécialité STI"
                deg_text = "INSA Centre Val de Loire, Bourges"
            dates_text = "09/2023 – 2028"
        else:
            inst_text = raw_inst
            deg_text = raw_deg
            dates_text = raw_dates

        inst = latex_escape(inst_text)
        deg = latex_escape(deg_text)
        dates = _format_dates(dates_text)
        vspace = r"\par\vspace{3pt}" if i < len(exp_data["education"]) - 1 else r"\par"
        formation_lines.append(f"\\noindent\\textbf{{\\color{{darkgray}}{inst}}} --- {{\\color{{midgray}}{deg}}}\\datesep\\chip{{{dates}}}{vspace}")
    formation = "\n".join(formation_lines)

    # Compétences : catégories ordonnées, format ATS \lbl{Cat}Items. Peu de lignes
    # et peu d'éléments par ligne : les plus parlants d'abord (ordre du catalogue).
    skills = exp_data.get("skills", {})
    comp_lines = []
    # « must_have » : éléments exigés par l'offre. Les catégories qui en contiennent
    # remontent (la première catégorie du CV de base garde sa place) et, dans chaque
    # ligne, ces éléments passent devant.
    must = {_plain(m) for m in selection.get("must_have", [])}
    ordered_cats = [c for c in selection.get("skills_order", []) if c in skills and skills[c]]
    if not ordered_cats:
        ordered_cats = list(skills.keys())
    if must:
        ordered_cats += [c for c in skills if c not in ordered_cats and skills[c]]
        head, tail = ordered_cats[:1], ordered_cats[1:]
        tail.sort(key=lambda c: -sum(1 for t in skills[c] if _plain(t) in must))  # tri stable
        ordered_cats = head + tail
    top_cats = ordered_cats[:MAX_SKILL_LINES]
    labels_map = SKILL_LABELS_EN if is_en else SKILL_LABELS
    for i, cat in enumerate(top_cats):
        label = labels_map.get(cat, cat.replace("_", " ").title())
        cat_items = sorted(skills[cat], key=lambda t: _plain(t) not in must)  # tri stable : exigés d'abord
        items = ", ".join(latex_escape(t) for t in cat_items[:MAX_SKILLS_PER_LINE])
        sep = r"\par\vspace{2.5pt}" if i < len(top_cats) - 1 else r"\par"
        comp_lines.append(f"\\lbl{{{label}}}{items}.{sep}")
    competences = "\n".join(comp_lines)

    # Certifications & Langues
    certs = " ; ".join(latex_escape(c) for c in exp_data.get("certifications", []))
    if certs and not certs.endswith("."):
        certs += "."
    langs = " ; ".join(
        # Seule l'initiale passe en minuscule : « TOEIC » doit rester un sigle lisible.
        f"{latex_escape(l['langue'])} --- {latex_escape(l['niveau'])[:1].lower()}{latex_escape(l['niveau'])[1:]}"
        for l in exp_data.get("languages", [])
    )
    if langs and not langs.endswith("."):
        langs += "."
    # Un seul bloc « Certifications, CTF & Langues ». Les résultats de CTF sont un
    # argument cyber sur tous les CV ; les autres distinctions (concours de
    # nouvelles…) ne sortent que là où la rédaction compte (GRC, audit).
    # Savoir-être et loisirs ne sont plus affichés : place rendue à l'aération.
    achievements = [str(a) for a in exp_data.get("achievements", [])]
    ctf = [a for a in achievements if "ctf" in a.lower()]
    other_distinctions = [a for a in achievements if "ctf" not in a.lower()]
    bottom_lines = [f"\\lbl{{Certifications}}{certs}"] if certs else []
    if ctf:
        bottom_lines.append(f"\\lbl{{CTF}}{' '.join(_render_bullet(a) for a in ctf)}")
    if langs:
        lang_lbl = "Languages" if is_en else "Langues"
        bottom_lines.append(f"\\lbl{{{lang_lbl}}}{langs}")
    if other_distinctions and offer_branch(offer) in DISTINCTION_BRANCHES:
        dist_lbl = "Awards" if is_en else "Distinction"
        bottom_lines.append(f"\\lbl{{{dist_lbl}}}{' '.join(_render_bullet(a) for a in other_distinctions)}")
    certifs_langues = "\\par\\vspace{2.5pt}\n".join(bottom_lines) + "\\par"

    # Titre du bandeau cible (prise en compte prioritaire de la consigne utilisateur)
    user_note = offer.get("user_note") or offer.get("note")
    custom_title = offer.get("custom_title") or _extract_custom_title(user_note)
    default_banner = "CYBERSECURITY ENGINEERING" if is_en else "AUDIT CYBERSÉCURITÉ & GRC"
    if custom_title:
        clean_title = _clean_banner_title(custom_title, lang=lang) or custom_title.strip()
    else:
        clean_title = _clean_banner_title(offer.get("title") or default_banner, lang=lang)
    if not clean_title:
        clean_title = default_banner
    if is_en:
        candidature = f"INTERNSHIP --- {latex_escape(clean_title.upper())}"
        pdftitle = f"CV {latex_escape(cand['full_name'])} - Internship {latex_escape(clean_title)}"
    else:
        candidature = f"STAGE --- {latex_escape(clean_title.upper())}"
        pdftitle = f"CV {latex_escape(cand['full_name'])} - Stage {latex_escape(clean_title)}"

    # Profil
    profil = _render_bullet(selection.get("profil") or _default_profil(exp_data, offer))

    # Photo node
    photo_path = CV_LATEX_DIR / PHOTO_REL
    if photo_path.exists():
        photo_node = f"\\node at (0,0) {{\\includegraphics[width=2.45cm]{{{PHOTO_REL}}}}};"
    else:
        photo_node = (r"\node at (0,0) {\fontsize{20}{20}\selectfont\bfseries\color{primary} "
                      + "".join(w[0] for w in str(cand["full_name"]).split()[:2]).upper() + "};")

    # Liens traceurs (JobNavigator Tracer Links)
    is_ref = _is_reference_catalog(exp_data)
    portfolio_link = cand.get("portfolio") or ("https://jamaiali.github.io" if is_ref else "")
    linkedin_link = cand.get("linkedin") or ("https://www.linkedin.com/in/ali-jamai-078602297" if is_ref else "")
    github_link = cand.get("github") or ("https://github.com/JamaiAli" if is_ref else "")

    try:
        from applypilot.tracking.tracer import get_or_create_all_tracers, publish_tracers
        # Un traceur se rattache à l'URL d'une offre : rien à créer pour un CV de
        # réserve ou une offre sans URL (sinon la table se remplit de lignes orphelines).
        trackable = bool(str(offer.get("url") or "").strip()) and not offer.get("_reserve")
        if trackable and tracer_is_public():
            # None si le traceur hébergé n'a pas pu enregistrer les liens : on garde les liens directs.
            tracers = publish_tracers(offer) or {}
        else:
            tracers = get_or_create_all_tracers(offer) if trackable else {}
        if tracer_is_public():
            if tracers.get("portfolio"):
                portfolio_link = tracers["portfolio"]
            if tracers.get("linkedin"):
                linkedin_link = tracers["linkedin"]
            if tracers.get("github"):
                github_link = tracers["github"]
    except Exception as exc:
        log.warning("Traceurs CV non initialisés : %s", exc)

    # Nettoyage de la localisation pour l'en-tête (ex: '18000 Bourges')
    loc = cand.get("location", "")
    # Ville brute telle que saisie dans candidate.location
    pass

    fields = {
        "NAME": latex_escape(cand["full_name"]),
        "SUBTITLE": latex_escape(str(cand.get("title") or "")),
        "PDFAUTHOR": latex_escape(cand["full_name"]),
        "LOCATION": latex_escape(loc),
        "PHONE": latex_escape(cand["phone"]),
        "EMAIL": cand["email"],
        "LINKEDIN": linkedin_link,
        "LINKEDIN_LABEL": latex_escape(_link_label(cand.get("linkedin") or linkedin_link)),
        "GITHUB": github_link,
        "GITHUB_LABEL": latex_escape(_link_label(cand.get("github") or github_link)),
        "PORTFOLIO": portfolio_link,
        "PORTFOLIO_LABEL": latex_escape(_link_label(cand.get("portfolio") or portfolio_link)),
        "PHOTO_NODE": photo_node,
        "CANDIDATURE": candidature,
        "PDFTITLE": pdftitle,
        "PROFIL": profil,
        "FORMATION": formation,
        "PROJETS": sec_projets,
        "EXPERIENCES": sec_exp,
        "COMPETENCES": competences,
        "CERTIFS_LANGUES": certifs_langues,
    }
    tex = _TEMPLATE
    if is_en:
        tex = (
            tex.replace(r"\newcommand{\lbl}[1]{\textbf{\color{primary}#1\,:}\enspace}",
                        r"\newcommand{\lbl}[1]{\textbf{\color{primary}#1:}\enspace}")
               .replace(r"\textbf{\color{primary}Lieu\,:}", r"\textbf{\color{primary}Location:}")
               .replace(r"\textbf{\color{primary}Tél\,:}", r"\textbf{\color{primary}Phone:}")
               .replace(r"\textbf{\color{primary}Email\,:}", r"\textbf{\color{primary}Email:}")
               .replace(r"\textbf{\color{primary}LinkedIn\,:}", r"\textbf{\color{primary}LinkedIn:}")
               .replace(r"\textbf{\color{primary}GitHub\,:}", r"\textbf{\color{primary}GitHub:}")
               .replace(r"\textbf{\color{primary}Web\,:}", r"\textbf{\color{primary}Portfolio:}")
               .replace(r"\section{Formation}", r"\section{Education}")
               .replace(r"\section{Compétences techniques}", r"\section{Technical Skills}")
               .replace(r"\section{Certifications, CTF \& Langues}", r"\section{Certifications, CTF \& Languages}")
        )
    for key, val in fields.items():
        tex = tex.replace(f"@@{key}@@", val)
    return tex


_TEMPLATE = r"""\documentclass[10pt,a4paper]{article}
% ---- ATS : table Unicode propre pour chaque glyphe (copier-coller / parsing fiables) ----
\input{glyphtounicode}
\pdfgentounicode=1
% Vrais caractères « espace » dans le PDF : sans cela, les extracteurs stricts
% collent les mots aux changements de police (« Kubernetesen », « pfSenseet »).
\pdfinterwordspaceon
% Aucune césure : « vul-nérabilités » coupé en fin de ligne n'est plus reconnu
% comme mot-clé par un ATS. \emergencystretch absorbe l'espace en trop.
\hyphenpenalty=10000
\exhyphenpenalty=10000
\emergencystretch=2em

\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage[scaled=0.92]{helvet}
\renewcommand{\familydefault}{\sfdefault}
\usepackage[a4paper,top=1.0cm,bottom=0.9cm,left=1.5cm,right=1.5cm]{geometry}
\usepackage{xcolor}
\usepackage{enumitem}
\usepackage{titlesec}
\usepackage{graphicx}
\usepackage{tikz}
\usepackage{amssymb}
\usepackage{hyperref}

% ---- Palette ----
\definecolor{primary}{HTML}{16425B}
\definecolor{accent}{HTML}{2980B9}
\definecolor{chipbg}{HTML}{E8F0F6}
\definecolor{darkgray}{HTML}{2B2B2B}
\definecolor{midgray}{HTML}{5A5A5A}
\definecolor{rule}{HTML}{C9D6DF}

\hypersetup{colorlinks=true,urlcolor=accent,linkcolor=accent,pdftitle={@@PDFTITLE@@},pdfauthor={@@PDFAUTHOR@@}}

% ---- Titres de section : VRAIES majuscules (pas de petites capitales -> lisible par les ATS) ----
\titleformat{\section}
  {\color{primary}\bfseries\normalsize}
  {}{0em}
  {\raisebox{0.05ex}{\textcolor{accent}{\rule{2mm}{2mm}}}\hspace{6pt}\MakeUppercase}
  [{\vspace{-1.5pt}\color{rule}\titlerule[1pt]}]
% Page aérée : de l'espace avant chaque section et entre les entrées. Le
% « plus 1fill » répartit entre les sections le blanc qui resterait en bas de
% page : la page est toujours équilibrée de haut en bas.
\titlespacing{\section}{0pt}{9pt plus 1fill}{4pt}

\setlength{\parindent}{0pt}
\pagestyle{empty}
\linespread{1.04}

% ---- Dates : collées au titre (alignées à droite, certains parseurs ATS les rattachent à la mauvaise ligne) ----
\newcommand{\chip}[1]{{\small\bfseries\color{accent}#1}}
\newcommand{\datesep}{\hspace{0.5em}{\color{rule}\textbar}\hspace{0.5em}}

% ---- Entrée : titre + date ----
\newcommand{\entry}[2]{%
  \vspace{5pt}%
  \noindent\textbf{\color{darkgray}#1}\datesep\chip{#2}\par\vspace{2pt}%
}

% ---- Alignement à gauche « souple » ----
% Marge droite à étirement FINI (au lieu de l'infini de \raggedright) : TeX peut
% alors comparer les coupures de ligne et éviter de finir un paragraphe sur deux
% ou trois mots isolés (\parfillskip : la dernière ligne vise au moins ~30 % de
% la largeur). Le texte n'est pas modifié, seules les coupures changent.
% \softspaces fige l'espace entre les mots à sa largeur naturelle (pour la police
% courante) : seule la marge droite absorbe le jeu, les mots ne s'écartent jamais.
\makeatletter
\newcommand{\softspaces}{\spaceskip=\fontdimen2\font\relax \xspaceskip=\fontdimen2\font\relax}
\newcommand{\softparfill}{\parfillskip=0pt plus 0.45\linewidth\relax}
\newcommand{\softragged}{%
  \raggedright
  \@rightskip=0pt plus 12em\relax \rightskip=\@rightskip
  \softparfill \softspaces
  \pretolerance=-1 \tolerance=3000 \hbadness=10000 \hfuzz=1pt}
\makeatother

\newcommand{\arrowbullet}{\textcolor{accent}{\small\raisebox{0.1ex}{$\blacktriangleright$}}}
\setlist[itemize]{leftmargin=1.3em,itemsep=2pt,topsep=2pt,parsep=0pt,label=\arrowbullet,first=\softparfill}% « first » et non « before » : une liste remet \parfillskip à sa valeur par défaut en s'ouvrant

% ---- Libellé de ligne (compétences, langues...) ----
\newcommand{\lbl}[1]{\textbf{\color{primary}#1\,:}\enspace}

\begin{document}
% Texte aligné à gauche : sans césure, la justification étirait certaines lignes
% (grands blancs entre les mots).
\softragged

% ======================= EN-TÊTE =======================
\begin{minipage}[c]{0.15\textwidth}
  \begin{tikzpicture}
    \begin{scope}
      \clip (0,0) circle (1.08cm);
      @@PHOTO_NODE@@
    \end{scope}
    \draw[primary,line width=1.1pt] (0,0) circle (1.08cm);
  \end{tikzpicture}
\end{minipage}%
\hspace{0.015\textwidth}%
\begin{minipage}[c]{0.835\textwidth}
  {\fontsize{26}{28}\selectfont\bfseries\color{primary}\softspaces @@NAME@@}\\[3pt]
  % Statut en texte courant, discret : le seul titre fort sous le nom est le bandeau.
  {\color{midgray}@@SUBTITLE@@}\\[5pt]
  {\small\softspaces\color{midgray}%
    \textbf{\color{primary}Lieu\,:} @@LOCATION@@ \;\textcolor{rule}{\textbar}\;
    \textbf{\color{primary}Tél\,:} @@PHONE@@ \;\textcolor{rule}{\textbar}\;
    \textbf{\color{primary}Email\,:} \href{mailto:@@EMAIL@@}{@@EMAIL@@}\\[3pt]
    \textbf{\color{primary}LinkedIn\,:} \href{@@LINKEDIN@@}{@@LINKEDIN_LABEL@@} \;\textcolor{rule}{\textbar}\;
    \textbf{\color{primary}GitHub\,:} \href{@@GITHUB@@}{@@GITHUB_LABEL@@} \;\textcolor{rule}{\textbar}\;
    \textbf{\color{primary}Web\,:} \href{@@PORTFOLIO@@}{@@PORTFOLIO_LABEL@@}%
  }
\end{minipage}

\vspace{5pt}
% ---- Bandeau cible ----
\noindent\colorbox{primary}{\makebox[\dimexpr\textwidth-2\fboxsep\relax][c]{%
  \color{white}\bfseries\normalsize @@CANDIDATURE@@%
}}

\vspace{6pt}
% Accroche : première chose lue, donc à la taille et à la couleur du corps de texte.
@@PROFIL@@\par

% ======================= FORMATION =======================
\section{Formation}
@@FORMATION@@

% ======================= AUDITS & PROJETS =======================
@@PROJETS@@

% ======================= EXPÉRIENCES =======================
@@EXPERIENCES@@

% ======================= BAS DE PAGE : UNE SEULE COLONNE (ordre de lecture linéaire) =======================
\section{Compétences techniques}
{\small\softspaces
@@COMPETENCES@@
}

\section{Certifications, CTF \& Langues}
{\small\softspaces
@@CERTIFS_LANGUES@@
}

\end{document}
"""


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------
def _page_count(pdf_path: Path) -> int:
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(pdf_path)).pages)
    except Exception:
        return 1  # en cas de souci, on ne bloque pas


def _compile(tex_path: Path, passes: int = 2) -> Path:
    """Compile le .tex en PDF dans son dossier (``passes`` passes de pdflatex).
    Renvoie le chemin du PDF."""
    workdir = tex_path.parent
    pdf_path = tex_path.with_suffix(".pdf")
    # On PRÉFÈRE pdflatex (vrai .exe MiKTeX, sans dépendance Perl) à latexmk
    # (script Perl qui échoue silencieusement si Perl n'est pas installé — cas
    # fréquent sous PowerShell/Windows).
    pdflatex = shutil.which("pdflatex")
    latexmk = shutil.which("latexmk")
    if pdflatex:
        cmd = [pdflatex, "-interaction=nonstopmode", "-halt-on-error", tex_path.name]
        # 2 passes par défaut pour tikz/hyperref
    elif latexmk:
        cmd = [latexmk, "-pdf", "-interaction=nonstopmode", "-halt-on-error", tex_path.name]
        passes = 1
    else:
        raise RuntimeError("pdflatex introuvable — installe MiKTeX.")
    last = None
    for _ in range(passes):
        last = subprocess.run(cmd, cwd=str(workdir), capture_output=True,
                              text=True, timeout=180)
    if not pdf_path.exists():
        # Extrait les vraies erreurs LaTeX (lignes « ! … ») du fichier .log.
        errs = ""
        try:
            logtxt = tex_path.with_suffix(".log").read_text(
                encoding="utf-8", errors="ignore")
            errs = " | ".join(l.strip() for l in logtxt.splitlines()
                              if l.startswith("!"))[:400]
        except OSError:
            pass
        if not errs:
            errs = (last.stdout or "")[-400:] if last else ""
        raise RuntimeError(f"Compilation LaTeX échouée : {errs}")
    return pdf_path


def _norm_offer_title(offer: dict) -> str:
    """Intitulé visé (consigne utilisateur prioritaire), en minuscules sans accents."""
    import unicodedata

    user_note = offer.get("user_note") or offer.get("note")
    custom_title = offer.get("custom_title") or _extract_custom_title(user_note)
    raw_title = str(custom_title or offer.get("title") or "").lower()
    return unicodedata.normalize("NFKD", raw_title).encode("ascii", "ignore").decode("ascii")


def _title_tag(norm_title: str) -> str | None:
    """Domaine clé de l'offre (termes courts et ciblés), ou None si aucun ne ressort.
    Sert au nom de fichier ET à l'angle de l'accroche de secours."""
    def has(pattern: str) -> bool:
        return re.search(pattern, norm_title) is not None

    # Les sigles courts sont cherchés en mots entiers : « soc » ne doit pas
    # reconnaître « socle », ni « ssi » « assistant ».
    # Le test d'intrusion passe avant l'audit : un « audit technique et tests
    # d'intrusion » est un poste de pentest.
    if has(r"pentest|offensiv|intrusion|red ?team"):
        return "Pentest"
    if has(r"\bia\b|\bllm\b|intelligence artificielle|multi.?agents?|\bagents?\b"):
        return "IA"
    if has(r"audit"):
        return "Audit"
    if has(r"\bgrc\b|gouvernance|conformit|risque|resilience|continuite d.activite"):
        return "GRC"
    if has(r"\bsoc\b|incident|detection|\bsiem\b|threat"):
        return "SOC"
    if has(r"devops|devsecops|\bsre\b"):
        return "DevOps"
    if has(r"active directory|\bad\b|\bentra\b"):
        return "AD"
    if has(r"identit|\biam\b"):
        if has(r"cloud|api|web|software|distribu"):
            return "Cloud"
        return "AD"
    if has(r"reseau|telecom"):
        return "Reseau"
    if has(r"cloud|azure|\baws\b"):
        return "Cloud"
    if has(r"cyber|securit|\bssi\b"):
        return "Cyber"
    return None


def _offer_key(offer: dict) -> str | None:
    """Identifiant court et stable de l'offre (hash de son URL), ou None sans URL."""
    import hashlib

    url = str(offer.get("url") or "").strip()
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:8] if url else None


def _publish_pdf(built: Path, out_name: str, offer_key: str | None,
                 keep_source: bool = False) -> Path:
    """Range le PDF compilé dans le dossier propre à l'offre, sous son nom court.

    Le nom court (≤ 25 caractères, exigé par les portails) est le même pour
    toutes les offres d'un même domaine : sans dossier par offre, chaque CV
    « Cyber » écrasait le précédent et la base pointait vers un CV adapté à une
    autre offre. Sans URL (appel hors pipeline), le PDF reste où il est.
    """
    if not offer_key:
        return built
    dest = CV_LATEX_DIR / "offres" / offer_key / f"{out_name}.pdf"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(built, dest)
    except OSError as exc:  # PDF de destination ouvert dans un lecteur (Windows)
        log.warning("Copie du CV vers %s impossible (%s) — CV laissé dans %s", dest, exc, built)
        return built
    if keep_source:
        return dest
    for stale in (built, *(built.with_suffix(ext) for ext in (".aux", ".out", ".log"))):
        try:
            stale.unlink()
        except OSError:
            pass
    return dest


def build_cv_filename(offer: dict, candidate_name: str | None = None) -> str:
    """Génère un nom de fichier court et percutant basé sur l'offre,
    strictement limité à <= 25 caractères (.pdf inclus) pour être accepté
    sans exception par tous les portails ATS (Workday/Thales, Taleo, etc.).
    Exemples : CV_Ali_Jamai_Cyber.pdf (22 car.), CV_Ali_Jamai_Audit.pdf (22 car.)
    """
    norm_title = _norm_offer_title(offer)
    tag = _title_tag(norm_title)

    if not tag:
        clean = re.sub(r"\b(stage|stagiaire|ingenieur|ingenieure|h/f|f/h|m/f|de|du|en|pour|et|le|la|un|une)\b", "", norm_title)
        words = re.findall(r"[a-z]{3,}", clean)
        tag = words[0].capitalize()[:8] if words else "Cyber"

    return f"{cv_prefix()}_{tag}"


def _llm_selection_with_retries(offer: dict, exp_data: dict, attempts: int = 2) -> dict | None:
    """Sélection + accroche par le LLM, avec relances. None si le LLM ne répond pas."""
    import time as _t

    # Priorité LLM : met le scoring de fond en pause pendant la génération de
    # l'accroche (tâche demandée en direct par l'utilisateur).
    config.set_llm_priority(True)
    try:
        for attempt in range(attempts):
            selection = _llm_selection(offer, exp_data)
            if selection:
                return selection
            if attempt < attempts - 1:
                log.info("Accroche LLM indisponible, relance (%d/%d)…", attempt + 2, attempts)
                _t.sleep(3)
    finally:
        config.set_llm_priority(False)
    return None


def _compile_one_page(exp_data: dict, selection: dict, offer: dict, tex_path: Path, lang: str = "fr") -> Path:
    """Compile la sélection avec une mise en page adaptative : on réduit puces
    puis expériences jusqu'à tenir sur une seule page (CV de stage = 1 page
    impérative). Lève l'erreur de compilation si rien ne compile."""
    pdf = None
    last_err: Exception | None = None
    # (projets, puces des projets « riches », nombre de projets riches), du plus
    # fourni au plus sobre : on garde la première mise en page qui tient en 1 page.
    for max_exp, max_bullets, rich in [(4, 3, 4), (4, 2, 4), (4, 3, 2), (4, 2, 2), (4, 2, 1), (4, 1, 1),
                                       (3, 2, 1), (3, 1, 1), (2, 1, 1)]:
        tex = _render_tex(exp_data, selection, offer, max_exp, max_bullets, rich_projects=rich, lang=lang)
        tex_path.write_text(tex, encoding="utf-8")
        try:
            pdf = _compile(tex_path, passes=1)  # une passe suffit pour compter les pages
        except Exception as exc:
            last_err = exc
            continue
        if _page_count(pdf) <= 1:
            return _compile(tex_path, passes=1)  # seconde passe (signets hyperref) sur la version retenue
    if pdf is not None:
        return pdf  # a compilé mais >1 page : on accepte faute de mieux
    raise RuntimeError(str(last_err))


def generate_cv(offer: dict, out_name: str | None = None, tailor: bool | None = None,
                lang: str | None = None) -> Path:
    """Renvoie le CV à envoyer pour l'offre (chemin du PDF).

    Détecte automatiquement la langue de l'offre : si l'offre est en anglais,
    compile un CV 1 page en anglais (données factuelles d'experiences_en.json,
    même sélection de branche validée, zéro invention).

    Lève RuntimeError/FileNotFoundError en cas d'échec (données ou compilation).
    """
    cv_lang = lang or offer_language(offer)
    exp_data = load_experiences(lang=cv_lang)
    branch = offer_branch(offer)
    offer_key = _offer_key(offer)
    if tailor is None:
        tailor = bool(str(offer.get("user_note") or offer.get("note") or "").strip())

    CV_LATEX_DIR.mkdir(parents=True, exist_ok=True)

    # Branche ANGLAIS : compilation rapide 1 page à partir de la réserve + catalogue anglais
    if cv_lang == "en":
        en_meta = RESERVE_BRANCHES_EN.get(branch) or RESERVE_BRANCHES_EN[_GENERIC_BRANCH]
        base_sel = load_reserve(branch, exp_data) or _default_selection(exp_data, offer)
        gaps_en = offer_gaps(offer, "", exp_data)
        selection_en = {
            **base_sel,
            "profil": en_meta["profil"],
        }
        if gaps_en:
            selection_en = _with_required_experiences(selection_en, gaps_en[:6], exp_data)

        en_out_name = out_name or f"{cv_prefix(exp_data)}_{branch}_EN"
        build_name = f"{en_out_name}_{offer_key}" if offer_key else en_out_name
        tex_path = CV_LATEX_DIR / f"{build_name}.tex"
        if tex_path.with_suffix(".pdf").exists():
            try:
                with open(tex_path.with_suffix(".pdf"), "a+b"):
                    pass
            except OSError:
                import time as _t
                tex_path = CV_LATEX_DIR / f"{build_name}_{_t.strftime('%H%M%S')}.tex"

        en_offer = {**offer}
        if not en_offer.get("custom_title") and not _extract_custom_title(en_offer.get("user_note") or en_offer.get("note")):
            cleaned = _clean_banner_title(str(offer.get("title") or ""), lang="en")
            en_offer["custom_title"] = cleaned if (cleaned and len(cleaned) <= 45) else en_meta["title"]

        pdf = _compile_one_page(exp_data, selection_en, en_offer, tex_path, lang="en")
        final = _publish_pdf(pdf, en_out_name, offer_key)
        _GENERATION_SOURCE[str(final)] = f"en_reserve:{branch}"
        _GENERATION_DETAILS[str(final)] = {"added": [], "left": [], "lang": "en"}
        log.info("CV ANGLAIS « %s » compilé (1 page) pour « %s »", branch, str(offer.get("title") or "")[:60])
        return final

    # 1. Sans consigne du candidat : le CV de réserve de la branche.
    stock = reserve_pdf(branch)
    stock_tag = stock.stem.rsplit("_", 1)[-1] if stock else None
    # Ce que l'offre exige, que le candidat possède, et que ce CV ne montre pas.
    gaps = offer_gaps(offer, _pdf_text(stock), exp_data) if stock else []
    auto_reserve = None
    if not tailor:
        if stock and gaps:
            auto_reserve = load_reserve(stock_tag, exp_data, fallback=False)
        if stock and not auto_reserve:
            # Rien à ajouter (ou réserve sans fichier de sélection) : le CV tel quel, sans LLM.
            final = _publish_pdf(stock, out_name or stock.stem, offer_key, keep_source=True)
            if final != stock:  # jamais sur le CV de réserve lui-même, seulement sur la copie de l'offre
                _apply_tracer_links(final, offer)
            _GENERATION_SOURCE[str(final)] = f"reserve:{stock_tag}"
            _GENERATION_DETAILS[str(final)] = {"added": [], "left": gaps}
            log.info("CV de réserve « %s » retenu tel quel pour « %s »", stock_tag, str(offer.get("title") or "")[:60])
            return final
        if not stock:
            log.warning("Aucun CV de réserve prêt : génération sur mesure (lance « applypilot cv-reserve »).")

    candidates: list[tuple[str, dict]] = []
    if auto_reserve:
        # 2a. Adaptation AUTOMATIQUE : l'offre exige des éléments absents du CV de
        # réserve. Le LLM choisit comment les faire entrer ; l'accroche et le
        # bandeau du CV de réserve, validés par le candidat, ne bougent pas.
        log.info("Offre « %s » : éléments à ajouter au CV « %s » : %s",
                 str(offer.get("title") or "")[:60], stock_tag, ", ".join(gaps))
        out_name = out_name or stock.stem
        offer = {**offer, "custom_title": RESERVE_BRANCHES[stock_tag]["title"]}
        llm_sel = _llm_selection_with_retries({**offer, "_base": auto_reserve, "_must": gaps}, exp_data, attempts=2)
        if llm_sel:
            # Du LLM, on ne retient que le choix des expériences : l'accroche et
            # l'ordre des compétences restent ceux du CV de réserve (la catégorie
            # phare de la branche reste en tête, les éléments exigés remontent).
            selection = {**llm_sel, "profil": auto_reserve["profil"],
                         "skills_order": auto_reserve["skills_order"]}
            candidates.append((f"llm_auto:{stock_tag}", _with_required_experiences(selection, gaps, exp_data)))
        # LLM indisponible : les éléments exigés sont ajoutés quand même, sans lui.
        candidates.append((f"reserve_completee:{stock_tag}",
                           _with_required_experiences(auto_reserve, gaps, exp_data)))
    else:
        # 2b. Consigne ✏️ du candidat (ou réserve absente) : le LLM part du CV de
        # réserve. S'il ne répond pas : la réserve avec le bandeau de l'offre ; sinon le secours.
        reserve = load_reserve(branch, exp_data)
        llm_offer = {**offer, "_base": reserve, "_must": gaps} if reserve else offer
        llm_sel = _llm_selection_with_retries(llm_offer, exp_data)
        if llm_sel:
            candidates.append(("llm", _with_required_experiences(llm_sel, gaps, exp_data)))
        elif reserve:
            candidates.append((f"reserve_adaptee:{reserve['tag']}",
                               _with_required_experiences(reserve, gaps, exp_data)))
        candidates.append(("secours", _default_selection(exp_data, offer)))

    if not (CV_LATEX_DIR / PHOTO_REL).exists():
        log.warning("Photo %s absente — le CV compilera sans photo si le fichier manque.",
                    CV_LATEX_DIR / PHOTO_REL)

    if not out_name:
        out_name = build_cv_filename(offer)

    # Compilation sous un nom propre à l'offre (nom court + hash de l'URL), puis
    # rangement du PDF dans le dossier de l'offre sous son nom court (_publish_pdf).
    build_name = f"{out_name}_{offer_key}" if offer_key else out_name
    tex_path = CV_LATEX_DIR / f"{build_name}.tex"
    pdf_path = tex_path.with_suffix(".pdf")
    # Si le PDF existe déjà et est verrouillé par un visualiseur (Windows),
    # on ajoute un suffixe temporel pour ne jamais bloquer la compilation.
    if pdf_path.exists():
        try:
            with open(pdf_path, "a+b"):
                pass
        except OSError:
            import time as _t
            build_name = f"{build_name}_{_t.strftime('%H%M%S')}"
            tex_path = CV_LATEX_DIR / f"{build_name}.tex"

    # Si une sélection ne compile pas, on essaie la suivante.
    last_err = None
    for source, selection in candidates:
        try:
            pdf = _compile_one_page(exp_data, selection, offer, tex_path)
        except Exception as exc:
            last_err = exc
            continue
        final = _publish_pdf(pdf, out_name, offer_key)
        _GENERATION_SOURCE[str(final)] = source
        # Ce qui a effectivement été ajouté, vérifié dans le PDF produit.
        left = offer_gaps(offer, _pdf_text(final), exp_data) if gaps else []
        _GENERATION_DETAILS[str(final)] = {"added": [g for g in gaps if g not in left], "left": left}
        if not source.startswith("llm"):
            log.warning("CV généré sans le LLM (source : %s) pour « %s »",
                        source, str(offer.get("title") or "")[:60])
        return final

    # Rien n'a compilé (MiKTeX en panne…) : dernier recours, le PDF de réserve
    # de la branche, déjà prêt, envoyé tel quel.
    if stock:
        final = _publish_pdf(stock, out_name, offer_key, keep_source=True)
        _GENERATION_SOURCE[str(final)] = f"reserve_sans_compilation:{stock_tag}"
        _GENERATION_DETAILS[str(final)] = {"added": [], "left": gaps}
        log.warning("Compilation impossible (%s) — CV de réserve « %s » renvoyé tel quel", last_err, stock_tag)
        return final
    raise RuntimeError(f"Génération CV impossible : {last_err}")


# ---------------------------------------------------------------------------
# CV « job étudiant » : même gabarit que les CV de stage, contenu à part
# ---------------------------------------------------------------------------

STUDENT_CV_DATA = config.APP_DIR / "cv_job_etudiant.json"
STUDENT_CV_PDF = config.APP_DIR / "cv_job_etudiant.pdf"


def build_student_cv() -> Path:
    """Compile le CV job étudiant à partir de ~/.applypilot/cv_job_etudiant.json
    (bandeau, accroche, expériences, compétences, pratique) avec le gabarit des
    CV de stage : une colonne, pas de petites capitales, texte lisible par les
    ATS. Les coordonnées et la formation viennent de experiences.json.
    L'ancien PDF est conservé en .bak. Renvoie le chemin du PDF."""
    data = json.loads(STUDENT_CV_DATA.read_text(encoding="utf-8"))
    exp_data = load_experiences()
    cand = exp_data["candidate"]

    head_end = _TEMPLATE.index("% ======================= FORMATION")
    # Contenu plus court qu'un CV de stage : corps en 11 pt pour remplir la page
    # sans creuser de grands blancs entre les sections.
    tex = _TEMPLATE[:head_end].replace(r"\documentclass[10pt,a4paper]", r"\documentclass[11pt,a4paper]", 1)
    # En-tête : LinkedIn seul (GitHub et portfolio n'apportent rien pour un job étudiant).
    start = tex.index(r" \;\textcolor{rule}{\textbar}\;" + "\n" + r"    \textbf{\color{primary}GitHub\,:}")
    stop = tex.index(r"{@@PORTFOLIO_LABEL@@}%") + len(r"{@@PORTFOLIO_LABEL@@}")
    tex = tex[:start] + tex[stop:]

    loc = cand.get("location", "")
    linkedin = cand.get("linkedin") or ""
    photo = (f"\\node at (0,0) {{\\includegraphics[width=2.45cm]{{{PHOTO_REL}}}}};"
             if (CV_LATEX_DIR / PHOTO_REL).exists()
             else (r"\node at (0,0) {\fontsize{20}{20}\selectfont\bfseries\color{primary} "
                      + "".join(w[0] for w in str(cand["full_name"]).split()[:2]).upper() + "};"))
    fields = {
        "NAME": latex_escape(cand["full_name"]),
        "SUBTITLE": latex_escape(data.get("subtitle", "")),
        "LOCATION": latex_escape(loc),
        "PHONE": latex_escape(cand["phone"]),
        "EMAIL": cand["email"],
        "LINKEDIN": linkedin,
        "LINKEDIN_LABEL": latex_escape(_link_label(linkedin)),
        "PHOTO_NODE": photo,
        "CANDIDATURE": latex_escape(data["banner"].upper()),
        "PDFTITLE": latex_escape(f"CV {cand['full_name']} - Job étudiant"),
        "PDFAUTHOR": latex_escape(cand["full_name"]),
        "PROFIL": _render_bullet(data["profil"]),
    }
    for key, val in fields.items():
        tex = tex.replace(f"@@{key}@@", val)

    parts = [r"\section{Expériences professionnelles}"]
    for job in data["experiences"]:
        org = f" \\textnormal{{\\color{{midgray}}({latex_escape(job['org'])})}}" if job.get("org") else ""
        parts.append(f"\\entry{{{latex_escape(job['title'])}{org}}}{{{_format_dates(job.get('dates', ''))}}}")
        parts.append("\\begin{itemize}")
        parts += [f"  \\item {_render_bullet(b)}" for b in job.get("bullets", [])]
        parts.append("\\end{itemize}")

    parts.append(r"\section{Formation}")
    edu = exp_data.get("education", [])
    for i, ed in enumerate(edu):
        dates = _education_dates(ed) or ed.get("dates", "")
        sep = r"\par\vspace{3pt}" if i < len(edu) - 1 else r"\par"
        parts.append(f"\\noindent\\textbf{{\\color{{darkgray}}{latex_escape(str(ed.get('institution', '')))}}} --- "
                     f"{{\\color{{midgray}}{latex_escape(str(ed.get('degree', '')))}}}"
                     f"\\datesep\\chip{{{_format_dates(str(dates))}}}{sep}")

    for title, lines in (("Compétences", data.get("skills", [])), ("Pratique", data.get("practical", []))):
        if not lines:
            continue
        parts.append(f"\\section{{{latex_escape(title)}}}")
        rows = [f"\\lbl{{{latex_escape(l['label'])}}}{_render_bullet(l['text'])}" for l in lines]
        parts.append("{\\small\\softspaces\n" + "\\par\\vspace{2.5pt}\n".join(rows) + "\\par\n}")

    tex += "\n".join(parts) + "\n\n\\end{document}\n"

    CV_LATEX_DIR.mkdir(parents=True, exist_ok=True)
    tex_path = CV_LATEX_DIR / "cv_job_etudiant.tex"
    tex_path.write_text(tex, encoding="utf-8")
    built = _compile(tex_path)
    if _page_count(built) != 1:
        raise RuntimeError(f"Le CV job étudiant fait {_page_count(built)} pages : raccourcis cv_job_etudiant.json.")
    if STUDENT_CV_PDF.exists():
        shutil.copyfile(STUDENT_CV_PDF, STUDENT_CV_PDF.with_suffix(".pdf.bak"))
    shutil.copyfile(built, STUDENT_CV_PDF)
    return STUDENT_CV_PDF


# ---------------------------------------------------------------------------
# Origine du dernier CV généré (pour prévenir l'utilisateur sur Telegram)
# ---------------------------------------------------------------------------

_GENERATION_SOURCE: dict[str, str] = {}
# Par PDF : {"added": éléments exigés par l'offre et ajoutés, "left": ceux restés hors du CV}.
_GENERATION_DETAILS: dict[str, dict] = {}


def generation_details(pdf_path) -> dict:
    return _GENERATION_DETAILS.get(str(pdf_path), {"added": [], "left": []})


def generation_source(pdf_path) -> str:
    """D'où vient le CV renvoyé par generate_cv :
    - « reserve:<Branche> » : CV de réserve tel quel (l'offre n'exige rien de plus, sans LLM) ;
    - « llm_auto:<Branche> » : l'offre exige des éléments absents du CV de réserve,
      le LLM les a fait entrer (accroche et bandeau de réserve conservés) ;
    - « reserve_completee:<Branche> » : même cas, LLM indisponible, éléments ajoutés sans lui ;
    - « llm » : adapté par le LLM à la demande du candidat ;
    - « reserve_adaptee:<Branche> » : adaptation demandée mais LLM indisponible,
      réserve recompilée avec le bandeau de l'offre ;
    - « reserve_sans_compilation:<Branche> » : LaTeX en panne, réserve telle quelle ;
    - « secours » : ni LLM ni réserve ; « inconnu » : PDF non produit par generate_cv.
    """
    return _GENERATION_SOURCE.get(str(pdf_path), "inconnu")


def generation_notice(pdf_path) -> str:
    """Phrase à joindre au CV envoyé sur Telegram : dit lequel a été choisi et
    comment en obtenir un autre. Chaîne vide si l'origine est inconnue."""
    source = generation_source(pdf_path)
    kind, _, tag = source.partition(":")
    details = generation_details(pdf_path)
    added, left = ", ".join(details["added"]), ", ".join(details["left"])
    not_placed = f" Pas réussi à caser : {left}." if left else ""
    if kind == "reserve":
        return (f"🗄️ CV « {tag} » de ta réserve, tel quel : l'offre ne demande rien que tu possèdes "
                "et qu'il ne montre déjà. Pas assez adapté ? Appuie sur ✏️ Modifier et dis-moi quoi changer.")
    if kind == "llm_auto":
        return (f"🤖 CV « {tag} » de ta réserve, adapté automatiquement par le LLM : l'offre demande "
                f"{added or left}, que ce CV ne montrait pas. Accroche et bandeau inchangés.{not_placed}")
    if kind == "reserve_completee":
        return (f"⚠️ LLM indisponible : j'ai ajouté moi-même à ton CV « {tag} » ce que l'offre demande "
                f"({added or left}), sans changer le reste.{not_placed}")
    if kind == "llm":
        return ("✏️ Version adaptée par le LLM à partir de ton CV de réserve, selon ta consigne."
                + (f" Ajouté pour l'offre : {added}." if added else "") + not_placed)
    if kind == "reserve_adaptee":
        return (f"⚠️ LLM indisponible : ta consigne n'a pas pu être appliquée. Voici ton CV de réserve "
                f"« {tag} » avec le bandeau de cette offre. Renvoie ta consigne plus tard pour retenter.")
    if kind == "reserve_sans_compilation":
        return (f"⚠️ Compilation LaTeX indisponible : voici ton CV de réserve « {tag} » tel quel, "
                "sans ta consigne.")
    if kind == "secours":
        return ("⚠️ LLM indisponible et aucun CV de réserve pour cette branche : accroche de "
                "secours générique. Lance « applypilot cv-reserve » pour constituer la réserve.")
    return ""


# ---------------------------------------------------------------------------
# Réserve de CV par branche : les CV envoyés par défaut, relus par le candidat
# ---------------------------------------------------------------------------

# Offre type de chaque branche : sert à faire rédiger l'accroche de réserve et à
# choisir les expériences. Les clés sont celles de _title_tag (+ « Cyber »).
_NO_COMPANY = "aucune (modèle générique : ne cite aucune entreprise)"
RESERVE_BRANCHES: dict[str, dict] = {
    "SOC": {"title": "Stage - Analyste SOC",
            "description": "Surveillance et détection des incidents de sécurité, analyse des journaux et "
                           "des alertes, supervision, investigation et réponse aux incidents."},
    "Pentest": {"title": "Stage - Pentester / Sécurité offensive",
                "description": "Tests d'intrusion web et infrastructure, audit Active Directory, exploitation "
                               "de vulnérabilités, rédaction de rapports et de recommandations."},
    "GRC": {"title": "Stage - Consultant Cybersécurité GRC",
            "description": "Gouvernance, gestion des risques et conformité : analyse de risques, politiques "
                           "de sécurité, suivi des plans d'action, sensibilisation."},
    "Audit": {"title": "Stage - Auditeur Cybersécurité",
              "description": "Audits de sécurité techniques et organisationnels, contrôle de conformité, "
                             "analyse des écarts, rapports et recommandations."},
    "AD": {"title": "Stage - Ingénieur Systèmes & Active Directory",
           "description": "Administration et sécurisation Active Directory / Entra ID, Windows Server, GPO, "
                          "gestion des identités et des accès, automatisation PowerShell."},
    "Reseau": {"title": "Stage - Ingénieur Réseaux & Sécurité",
               "description": "Administration et sécurisation des réseaux : pare-feu, VPN, segmentation, "
                              "supervision, durcissement des équipements."},
    "DevOps": {"title": "Stage - Ingénieur DevSecOps",
               "description": "CI/CD, conteneurs et Kubernetes, automatisation Ansible, intégration de la "
                              "sécurité dans les chaînes de build (SAST/DAST)."},
    "Cloud": {"title": "Stage - Ingénieur Sécurité Cloud",
              "description": "Sécurisation d'infrastructures cloud, conteneurs, automatisation, gestion des "
                             "identités et des accès, durcissement."},
    "IA": {"title": "Stage - Ingénieur IA & Cybersécurité",
           "description": "Application de l'IA à la cybersécurité, sécurité des modèles de langage (LLM), "
                          "automatisation de l'analyse de vulnérabilités."},
    "Cyber": {"title": "Stage - Ingénieur Cybersécurité",
              "description": "Missions variées en sécurité des systèmes d'information : audit, durcissement, "
                             "analyse de vulnérabilités, sensibilisation."},
}
_GENERIC_BRANCH = "Cyber"


# Vocabulaire de chaque branche dans une DESCRIPTION d'offre (texte en minuscules,
# sans accents). Sert quand l'intitulé est trop générique (« Stagiaire cybersécurité »).
_BRANCH_KEYWORDS: dict[str, tuple[str, ...]] = {
    "SOC": (r"\bsoc\b", r"siem", r"detection", r"incident", r"supervision", r"splunk", r"sentinel", r"qradar",
            r"wazuh", r"\bedr\b", r"\bxdr\b", r"\bcti\b", r"threat", r"alerte", r"forensi", r"\bcsirt\b",
            r"\bcert\b", r"investigation", r"mitre"),
    "Pentest": (r"pentest", r"tests? d.intrusion", r"offensi", r"red team", r"bug bounty", r"owasp", r"burp",
                r"ethical hacking", r"exploitation de vulnerabilit"),
    "GRC": (r"gouvernance", r"\bgrc\b", r"analyses? de risque", r"gestion des risques", r"27001", r"ebios",
            r"conformite", r"\bpssi\b", r"\brgpd\b", r"\bnis ?2\b", r"politiques? de securite",
            r"sensibilisation", r"\bsmsi\b", r"\bdora\b"),
    "Audit": (r"\baudits?\b", r"auditeur"),
    "AD": (r"active directory", r"\bentra\b", r"azure ad", r"\biam\b", r"gestion des identites", r"\bgpo\b",
           r"windows server", r"habilitation", r"annuaire"),
    "Reseau": (r"\breseaux?\b", r"firewall", r"pare-feu", r"\bvpn\b", r"switch", r"routage", r"cisco",
               r"fortinet", r"palo alto", r"\blan\b", r"\bwan\b", r"telecom"),
    "DevOps": (r"devops", r"devsecops", r"ci ?/ ?cd", r"kubernetes", r"docker", r"ansible", r"terraform",
               r"pipeline", r"gitlab", r"jenkins"),
    "Cloud": (r"\bcloud\b", r"\baws\b", r"\bazure\b", r"\bgcp\b"),
    "IA": (r"intelligence artificielle", r"machine learning", r"\bllm\b", r"\bia\b", r"data science",
           r"generative", r"multi.?agents?", r"\bagents?\b", r"langchain", r"langsmith"),
}


def branch_scores(offer: dict) -> dict[str, int]:
    """Poids de chaque branche dans la description de l'offre (chaque mot-clé
    compte au plus 3 fois, pour qu'un seul terme répété ne décide pas de tout)."""
    import unicodedata

    raw = " ".join(str(offer.get(k) or "") for k in ("full_description", "description")).lower()
    text = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    return {branch: sum(min(3, len(re.findall(rx, text))) for rx in patterns)
            for branch, patterns in _BRANCH_KEYWORDS.items()}


def _plain(text: str) -> str:
    """Minuscules, sans accents, apostrophes droites : pour comparer des textes."""
    import unicodedata

    text = str(text or "").replace("’", "'").lower()
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _mentions(term: str, raw_text: str, plain_text: str) -> bool:
    """Vrai si ``term`` figure en mot entier dans le texte. Les sigles courts en
    majuscules (SPL, GPO, VPN…) sont cherchés en respectant la casse, pour ne pas
    les confondre avec un mot courant."""
    if term.isupper() and len(term) <= 4:
        return re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", raw_text) is not None
    return re.search(rf"(?<![a-z0-9]){re.escape(_plain(term))}(?![a-z0-9])", plain_text) is not None


def catalog_terms(exp_data: dict) -> list[str]:
    """Tout ce que le candidat peut légitimement afficher : ses compétences et les
    technologies de ses expériences. Les termes d'une ou deux lettres (« C »,
    « OU ») sont écartés : ils se confondraient avec des mots courants."""
    seen: dict[str, str] = {}
    for items in exp_data.get("skills", {}).values():
        for t in items:
            seen.setdefault(_plain(t), str(t))
    for exp in exp_data.get("experiences", []):
        for t in exp.get("tech", []):
            seen.setdefault(_plain(t), str(t))
    return [t for key, t in seen.items() if len(key) >= 3 and key not in _AMBIGUOUS_TERMS]


# Technologies du catalogue qui sont aussi des mots courants d'une offre (ou un
# doublon d'un autre terme) : les chercher déclencherait de fausses alertes.
_AMBIGUOUS_TERMS = {"requests", "express", "python 3"}


def _with_required_experiences(selection: dict, gaps: list[str], exp_data: dict) -> dict:
    """Garantit que chaque élément exigé peut apparaître sur le CV : les
    compétences sont remontées par le rendu (clé ``must_have``) ; un élément qui
    n'existe que dans les technologies d'une expérience fait entrer cette
    expérience dans la sélection, juste après la première."""
    selection = {**selection, "must_have": list(gaps),
                 "selected_experience_ids": list(selection.get("selected_experience_ids", []))}
    in_skills = {_plain(t) for items in exp_data.get("skills", {}).values() for t in items}
    ids = selection["selected_experience_ids"]
    by_id = {e["id"]: e for e in exp_data["experiences"]}
    for term in gaps:
        key = _plain(term)
        if key in in_skills:
            continue
        carriers = [e["id"] for e in exp_data["experiences"] if key in {_plain(t) for t in e.get("tech", [])}]
        shown = [i for i in ids if i not in FIXED_PRO_EXP_IDS][:4] + [i for i in FIXED_PRO_EXP_IDS if i in by_id]
        if carriers and not any(c in shown for c in carriers):
            ids.insert(min(1, len(ids)), carriers[0])
    return selection


def offer_gaps(offer: dict, cv_text: str, exp_data: dict) -> list[str]:
    """Éléments que l'offre demande, que le candidat possède (ils sont dans son
    catalogue) mais que le CV ne montre pas. C'est la liste de ce qu'il faut
    absolument ajouter pour coller à l'offre — et rien d'inventé ne peut y entrer.
    """
    raw_offer = " ".join(str(offer.get(k) or "") for k in ("title", "full_description", "description"))
    plain_offer, plain_cv = _plain(raw_offer), _plain(cv_text)
    gaps = []
    for term in catalog_terms(exp_data):
        if _mentions(term, raw_offer, plain_offer) and not _mentions(term, cv_text, plain_cv):
            gaps.append(term)
    # Dans l'ordre où l'offre les cite : les premiers cités sont les plus importants.
    return sorted(gaps, key=lambda t: plain_offer.find(_plain(t)))


def _pdf_text(pdf_path: Path) -> str:
    try:
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf_path)).pages)
    except Exception as exc:
        log.warning("Lecture du PDF %s impossible : %s", pdf_path, exc)
        return ""


def offer_branch(offer: dict) -> str:
    """Branche cyber que demande l'offre : celle du CV de réserve à lui envoyer.

    L'intitulé décide quand il est explicite (« Analyste SOC », « Pentester »…).
    Sinon c'est la description : la branche dont le vocabulaire y domine
    nettement. Sans signal clair, la branche générique « Cyber ».
    """
    # L'intitulé demandé par le candidat (consigne ✏️) d'abord, puis celui de l'offre.
    for norm_title in (_norm_offer_title(offer), _norm_offer_title({"title": offer.get("title")})):
        tag = _title_tag(norm_title)
        if tag in RESERVE_BRANCHES and tag != _GENERIC_BRANCH:
            # Vérification de cohérence avec la description
            bscores = branch_scores(offer)
            if tag == "AD" and bscores.get("AD", 0) == 0:
                ranked = sorted(bscores.items(), key=lambda kv: kv[1], reverse=True)
                if ranked and ranked[0][1] >= 3:
                    return ranked[0][0]
            return tag
    ranked = sorted(branch_scores(offer).items(), key=lambda kv: kv[1], reverse=True)
    (best, top), (_, second) = ranked[0], ranked[1]
    # Net : au moins 4 occurrences, et une avance franche sur la branche suivante.
    if top >= 4 and top >= 1.5 * second:
        return best
    return _GENERIC_BRANCH


def reserve_dir() -> Path:
    return CV_LATEX_DIR / "reserve"


def reserve_pdf(branch: str) -> Path | None:
    """PDF de réserve de la branche, à défaut celui de la branche générique."""
    for tag in dict.fromkeys((branch, _GENERIC_BRANCH)):
        pdf = reserve_dir() / f"{cv_prefix()}_{tag}.pdf"
        if pdf.exists():
            return pdf
    return None


def load_reserve(branch: str, exp_data: dict, fallback: bool = True) -> dict | None:
    """Sélection de réserve (accroche + expériences + ordre des compétences) de la
    branche, à défaut (si ``fallback``) celle de la branche générique. None si
    rien n'est prêt.

    Les identifiants d'expériences disparus du catalogue depuis sont écartés.
    """
    valid_ids = {e["id"] for e in exp_data["experiences"]}
    for tag in dict.fromkeys((branch, _GENERIC_BRANCH) if fallback else (branch,)):
        try:
            data = json.loads((reserve_dir() / f"{tag}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        ids = [i for i in data.get("selected_experience_ids", []) if i in valid_ids]
        profil = (data.get("profil") or "").strip()
        if not ids or not profil:
            continue
        order = [c for c in data.get("skills_order", []) if c in exp_data["skills"]]
        order += [c for c in exp_data["skills"] if c not in order]
        return {"tag": tag, "profil": profil, "selected_experience_ids": ids, "skills_order": order}
    return None


def build_reserve(branches: list[str] | None = None, from_saved: bool = False) -> dict[str, dict]:
    """(Re)constitue la réserve : pour chaque branche, fait rédiger l'accroche par
    le LLM sur une offre type, compile le CV et range le tout dans reserve/.

    À lancer quand le LLM répond (et après chaque ajout d'expérience). Une
    branche dont la génération échoue garde sa réserve précédente.

    ``from_saved`` : ne sollicite pas le LLM et recompile le PDF à partir du
    fichier reserve/<Branche>.json — à utiliser après avoir corrigé une accroche
    à la main dans ce fichier.
    Renvoie {branche: {"ok", "pdf", "pages", "profil", "error"}}.
    """
    import time as _t

    unknown = [b for b in (branches or []) if b not in RESERVE_BRANCHES]
    if unknown:
        raise ValueError(f"Branche inconnue : {', '.join(unknown)} (choix : {', '.join(RESERVE_BRANCHES)})")

    exp_data = load_experiences()
    rdir = reserve_dir()
    rdir.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict] = {}
    for tag in branches or list(RESERVE_BRANCHES):
        offer = {**RESERVE_BRANCHES[tag], "company": _NO_COMPANY, "_reserve": True}
        if from_saved:
            selection = load_reserve(tag, exp_data, fallback=False)
            error = f"aucune réserve enregistrée ({tag}.json absent ou vide)"
        else:
            selection = _llm_selection_with_retries(offer, exp_data)
            error = "LLM indisponible"
        if not selection:
            own = rdir / f"{cv_prefix(exp_data)}_{tag}.pdf"
            results[tag] = {"ok": False, "error": error, "pdf": own if own.exists() else None}
            continue
        tex_path = CV_LATEX_DIR / f"_reserve_{tag}.tex"
        try:
            built = _compile_one_page(exp_data, selection, offer, tex_path)
        except Exception as exc:
            results[tag] = {"ok": False, "error": f"compilation : {exc}", "pdf": reserve_pdf(tag)}
            continue
        pages = _page_count(built)
        pdf = rdir / f"{cv_prefix(exp_data)}_{tag}.pdf"
        try:
            shutil.copyfile(built, pdf)
        except OSError as exc:  # PDF de réserve ouvert dans un lecteur
            results[tag] = {"ok": False, "error": f"écriture : {exc}", "pdf": reserve_pdf(tag)}
            continue
        (rdir / f"{tag}.json").write_text(json.dumps({
            "tag": tag,
            "generated_at": _t.strftime("%Y-%m-%d %H:%M:%S"),
            "profil": selection["profil"],
            "selected_experience_ids": selection["selected_experience_ids"],
            "skills_order": selection["skills_order"],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        for stale in (tex_path.with_suffix(ext) for ext in (".pdf", ".aux", ".out", ".log", ".tex")):
            try:
                stale.unlink()
            except OSError:
                pass
        results[tag] = {"ok": True, "pdf": pdf, "pages": pages, "profil": selection["profil"]}
    return results
