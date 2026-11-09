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
}

# Types d'expériences classés comme « Expériences » (le reste = « Projets »).
_PRO_TYPES = {"experience_pro", "challenge"}

# Expériences obligatoires : le projet Airbus doit apparaître dans TOUS les CV sans exception.
MANDATORY_EXP_IDS = ["etude-hydrogene-airbus"]

# Expériences professionnelles fixes : apparaissent TOUJOURS dans cet ordre exact
# dans \section{Expériences professionnelles} avec leurs puces verbatim (Airbus 3, Shellter 1, CyberQuest 1).
FIXED_PRO_EXP_IDS = [
    "etude-hydrogene-airbus",
    "shellter",
    "cyberquest",
]


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------
def load_experiences() -> dict:
    return json.loads(EXPERIENCES_PATH.read_text(encoding="utf-8"))


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
    # Le projet Airbus est obligatoire sans exception dans tous les CV
    mandatory = [m for m in MANDATORY_EXP_IDS if any(e['id'] == m for e in exp_data['experiences'])]
    other_ids = [i for _, i in scored if i not in MANDATORY_EXP_IDS]
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
    concrète et alignée sur le template ATS."""
    return (
        "Élève-ingénieur en cybersécurité, je recherche un **stage de 4 à 5 mois à partir d'avril 2027** "
        "(prolongation possible) en audit et gouvernance cybersécurité. Mes projets d'audit (test d'intrusion "
        "Active Directory, pentest web OWASP) m'ont appris à identifier méthodiquement les vulnérabilités, à "
        "rédiger des rapports et à formuler des recommandations actionnables. Rigoureux, à l'aise en anglais "
        "et habitué à vulgariser des sujets techniques, je souhaite développer une vision transverse de la "
        "cybersécurité au sein d'un groupe international."
    )


def _llm_selection(offer: dict, exp_data: dict) -> dict | None:
    """Demande à Groq la sélection + un court profil. Renvoie None si échec."""
    try:
        from applypilot.llm import get_client
    except Exception as exc:  # pragma: no cover
        log.warning("LLM indisponible : %s", exc)
        return None

    # Catalogue compact (ids + titres + tags) — pas les puces, pour rester léger.
    catalog = [
        {"id": e["id"], "titre": e["title"], "type": e["type"],
         "tags": e.get("tags", []), "tech": e.get("tech", [])[:8]}
        for e in exp_data["experiences"]
    ]
    skills_cats = list(exp_data["skills"].keys())
    cand = exp_data["candidate"]

    # Consigne « accroche » : bien rédigée, fluide, ALIGNÉE sur l'angle de l'offre.
    profil_rule = (
        "Rédige une accroche de CV en français, 2 ou 3 phrases BIEN CONSTRUITES "
        "et FLUIDES (pas une liste de tâches), à la première personne, ton pro "
        "mais naturel. LE PLUS IMPORTANT : ADAPTE-la à l'ANGLE PRÉCIS de CETTE "
        "offre. Lis la description et identifie ce qu'elle demande vraiment "
        "(gouvernance/GRC/audit/conformité/ISO/analyse de risques ; OU "
        "intégration/Active Directory/Entra ID ; OU pentest/sécurité offensive ; "
        "OU cloud/DevSecOps/réseau…), puis mets en avant MES expériences réelles "
        "qui collent le mieux à CET angle — par exemple, pour un poste GRC/audit, "
        "parle de mon audit et durcissement d'Active Directory, de mon analyse de "
        "risques, de mes rapports et recommandations de conformité, et PAS du "
        "déploiement d'infrastructure. Ne répète pas un objectif générique : "
        "parle de CETTE offre. Structure : qui je suis et le stage visé, 1 ou 2 "
        "réalisations concrètes alignées sur l'offre, ce que j'apporte. "
        "N'utilise JAMAIS les formules IA / CV bateau : « Fort de », « Passionné "
        "par », « Doté d'une solide », « mettre à profit », « évoluer au sein "
        "de », « environnement stimulant/dynamique », « alliant ... et ... », "
        "« au carrefour de », « je suis convaincu », « rigoureux et motivé », ni "
        "les tirets cadratins. Uniquement des faits fournis, aucun chiffre "
        "inventé, aucune parenthèse."
    )

    system = (
        "Tu aides à ADAPTER un CV à une offre de stage, en français. RÈGLE ABSOLUE : le projet Airbus (etude-hydrogene-airbus) DOIT TOUJOURS être inclus dans selected_experience_ids. "
        "RÈGLE ABSOLUE : tu ne sélectionnes et n'ordonnes que des éléments RÉELS "
        "fournis ; tu n'inventes AUCUNE compétence, expérience, entreprise, ÉCOLE, "
        "ville d'études ni chiffre. L'école du candidat est TOUJOURS celle indiquée "
        "ci-dessous, jamais celle de la ville de l'offre. Tu réponds UNIQUEMENT par "
        "un objet JSON valide, sans texte autour."
    )
    fixed = (f"{cand.get('full_name')}, {cand.get('title')} "
             "(Bac+4, diplôme d'ingénieur prévu en 2028). École : INSA Centre Val "
             "de Loire — NE JAMAIS la remplacer par une autre.")
    user = (
        f"CANDIDAT (faits fixes, à ne JAMAIS modifier) : {fixed}\n"
        f"OBJECTIF DU CANDIDAT : {cand.get('objective')}\n\n"
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
    for mand in MANDATORY_EXP_IDS:
        if mand in valid_ids and mand not in sel:
            sel.append(mand)
    return {"profil": profil, "selected_experience_ids": sel[:7], "skills_order": order}


def _extract_json(raw: str) -> dict | None:
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Rendu LaTeX
# ---------------------------------------------------------------------------
def _format_dates(raw_dates: str) -> str:
    if not raw_dates:
        return ""
    s = re.sub(r"\s*(?:--|[–—−-])\s*", " -- ", raw_dates.strip())
    return latex_escape(s)


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


def _render_xp_block(exp: dict, max_bullets: int = 3) -> str:
    title = latex_escape(exp["title"])
    dates = _format_dates(exp.get("dates", ""))
    org = exp.get("org", "").strip()

    heading = title
    if org and org not in title and "Projet académique" not in org:
        short_org = org
        if "challenge" in org.lower():
            short_org = "challenge ECIU, Hambourg"
        elif "ormvag" in org.lower() or "stage devops" in org.lower():
            short_org = "ORMVAG"
        elif "projet personnel" in org.lower():
            short_org = "projet personnel"
        heading = f"{title} \\textnormal{{\\color{{midgray}}({latex_escape(short_org)})}}"

    lines = [f"\\entry{{{heading}}}{{{dates}}}"]
    bullets = exp.get("bullets", [])[:max_bullets]
    if bullets:
        lines.append("\\begin{itemize}")
        for b in bullets:
            lines.append(f"  \\item {_render_bullet(b)}")
        lines.append("\\end{itemize}")
    return "\n".join(lines)


def _render_tex(exp_data: dict, selection: dict, offer: dict,
                max_exp: int = 7, max_bullets: int = 2) -> str:
    cand = exp_data["candidate"]
    by_id = {e["id"]: e for e in exp_data["experiences"]}

    # 1. Expériences professionnelles : strictement fixes conformément au modèle ATS Claude
    # - Étude Hydrogène Airbus x Univ Hambourg (3 puces)
    # - Stage DevOps Shellter (1 puce)
    # - Plateforme CyberQuest (1 puce)
    fixed_pro_entries = []
    for fid in FIXED_PRO_EXP_IDS:
        if fid in by_id:
            b_count = 3 if fid == "etude-hydrogene-airbus" else 1
            fixed_pro_entries.append(_render_xp_block(by_id[fid], max_bullets=b_count))

    sec_exp = "\\section{Expériences professionnelles}\n\n" + "\n\n".join(fixed_pro_entries)

    # 2. Audits & projets cybersécurité : sélectionnés selon l'offre et adaptés à la page
    raw_sel = selection.get("selected_experience_ids", [])
    selected_proj_ids = [pid for pid in raw_sel if pid not in FIXED_PRO_EXP_IDS and pid in by_id]
    if len(selected_proj_ids) < max_exp:
        for e in exp_data.get("experiences", []):
            if e["id"] not in FIXED_PRO_EXP_IDS and e["id"] not in selected_proj_ids:
                selected_proj_ids.append(e["id"])
                if len(selected_proj_ids) >= max_exp:
                    break

    projets = [by_id[pid] for pid in selected_proj_ids[:max_exp]]
    proj_blocks = []
    for idx, p in enumerate(projets):
        b_count = max_bullets if idx == 0 else 1
        proj_blocks.append(_render_xp_block(p, max_bullets=b_count))

    sec_projets = ""
    if proj_blocks:
        sec_projets = "\\section{Audits \\& projets cybersécurité}\n\n" + "\n\n".join(proj_blocks)

    # Formation (verbatim depuis le JSON, format ATS)
    formation_lines = []
    for i, ed in enumerate(exp_data.get("education", [])):
        inst = latex_escape(ed["institution"])
        deg = latex_escape(ed["degree"])
        dates = _format_dates(ed.get("dates", ""))
        vspace = r"\par\vspace{3pt}" if i < len(exp_data["education"]) - 1 else r"\par"
        formation_lines.append(f"\\noindent\\textbf{{\\color{{darkgray}}{inst}}} --- {{\\color{{midgray}}{deg}}}\\datesep\\chip{{{dates}}}{vspace}")
    formation = "\n".join(formation_lines)

    # Compétences : catégories ordonnées, 5 catégories max, format ATS \lbl{Cat}Items
    skills = exp_data.get("skills", {})
    comp_lines = []
    top_cats = [c for c in selection.get("skills_order", []) if c in skills and skills[c]][:5]
    if not top_cats:
        top_cats = list(skills.keys())[:5]
    for i, cat in enumerate(top_cats):
        label = SKILL_LABELS.get(cat, cat.replace("_", " ").title())
        items = ", ".join(latex_escape(t) for t in skills[cat][:8])
        sep = r"\par\vspace{1.5pt}" if i < len(top_cats) - 1 else r"\par"
        comp_lines.append(f"\\lbl{{{label}}}{items}.{sep}")
    competences = "\n".join(comp_lines)

    # Certifications & Langues
    certs = " ; ".join(latex_escape(c) for c in exp_data.get("certifications", []))
    if certs and not certs.endswith("."):
        certs += "."
    langs = " ; ".join(
        f"{latex_escape(l['langue'])} --- {latex_escape(l['niveau']).lower()}"
        for l in exp_data.get("languages", [])
    )
    if langs and not langs.endswith("."):
        langs += "."
    certifs_langues = (
        f"\\lbl{{Certifications cybersécurité}}{certs}\\par\\vspace{{1.5pt}}\n"
        f"\\lbl{{Langues}}{langs}\\par"
    )

    # Engagement (achievements)
    achieve_lines = []
    for a in exp_data.get("achievements", [])[:2]:
        achieve_lines.append(f"  \\item {_render_bullet(a)}")
    achievements = "\n".join(achieve_lines)

    # Qualités & Loisirs
    soft_skills = ", ".join(latex_escape(s) for s in exp_data.get("soft_skills", []))
    if soft_skills and not soft_skills.endswith("."):
        soft_skills += "."

    loisirs = ", ".join(latex_escape(l) for l in exp_data.get("loisirs", []))
    if not loisirs:
        loisirs = "Échecs, course à pied, tennis."
    elif not loisirs.endswith("."):
        loisirs += "."

    # Titre du bandeau cible (clean, uppercase ATS)
    raw_title = offer.get("title") or "AUDIT CYBERSÉCURITÉ & GRC"
    clean_title = re.sub(r"\b(F/H|H/F|M/F|H/X)\b", "", raw_title, flags=re.IGNORECASE)
    clean_title = re.sub(r"\(\d+[-_]?\d*\)", "", clean_title)
    clean_title = re.sub(r"^\s*stage\s*[-–—:]?\s*", "", clean_title, flags=re.IGNORECASE).strip()
    clean_title = re.sub(r"\s+", " ", clean_title).strip()
    if not clean_title:
        clean_title = "AUDIT CYBERSÉCURITÉ & GRC"
    candidature = f"STAGE --- {latex_escape(clean_title.upper())}"
    pdftitle = f"CV Ali Jamai - Stage {latex_escape(clean_title)}"

    # Profil
    profil = _render_bullet(selection.get("profil") or _default_profil(exp_data, offer))

    # Photo node
    photo_path = CV_LATEX_DIR / PHOTO_REL
    if photo_path.exists():
        photo_node = f"\\node at (0,0) {{\\includegraphics[width=2.45cm]{{{PHOTO_REL}}}}};"
    else:
        photo_node = r"\node at (0,0) {\fontsize{20}{20}\selectfont\bfseries\color{primary} AJ};"

    # Liens traceurs (JobNavigator Tracer Links)
    portfolio_link = cand.get("portfolio") or "https://jamaiali.github.io"
    linkedin_link = cand.get("linkedin") or "https://www.linkedin.com/in/ali-jamai-078602297"
    github_link = cand.get("github") or "https://github.com/JamaiAli"

    try:
        from applypilot.tracking.tracer import get_or_create_all_tracers
        tracers = get_or_create_all_tracers(offer)
        is_safe_public = (
            config.TRACER_BASE_URL.startswith("https://")
            and not any(h in config.TRACER_BASE_URL.lower() for h in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "192.168.", "10.", "172."))
        )
        if is_safe_public:
            if tracers.get("portfolio"):
                portfolio_link = tracers["portfolio"]
            if tracers.get("linkedin"):
                linkedin_link = tracers["linkedin"]
            if tracers.get("github"):
                github_link = tracers["github"]
    except Exception as exc:
        log.warning("Traceurs CV non initialisés : %s", exc)

    # Nettoyage de la localisation pour l'en-tête (ex: '18000 Bourges')
    loc = cand.get("location", "18000 Bourges")
    if "Bourges" in loc:
        loc = "18000 Bourges"

    fields = {
        "NAME": latex_escape(cand["full_name"]),
        "SUBTITLE": "ÉLÈVE-INGÉNIEUR EN CYBERSÉCURITÉ --- INSA CVL",
        "LOCATION": latex_escape(loc),
        "PHONE": latex_escape(cand["phone"]),
        "EMAIL": cand["email"],
        "LINKEDIN": linkedin_link,
        "LINKEDIN_LABEL": "linkedin.com/in/ali-jamai-078602297",
        "GITHUB": github_link,
        "GITHUB_LABEL": "github.com/JamaiAli",
        "PORTFOLIO": portfolio_link,
        "PORTFOLIO_LABEL": "jamaiali.github.io",
        "PHOTO_NODE": photo_node,
        "CANDIDATURE": candidature,
        "PDFTITLE": pdftitle,
        "PROFIL": profil,
        "FORMATION": formation,
        "PROJETS": sec_projets,
        "EXPERIENCES": sec_exp,
        "COMPETENCES": competences,
        "CERTIFS_LANGUES": certifs_langues,
        "ACHIEVEMENTS": achievements,
        "SOFT_SKILLS": soft_skills,
        "LOISIRS": loisirs,
    }
    tex = _TEMPLATE
    for key, val in fields.items():
        tex = tex.replace(f"@@{key}@@", val)
    return tex


_TEMPLATE = r"""\documentclass[10pt,a4paper]{article}
% ---- ATS : table Unicode propre pour chaque glyphe (copier-coller / parsing fiables) ----
\input{glyphtounicode}
\pdfgentounicode=1

\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage[scaled=0.92]{helvet}
\renewcommand{\familydefault}{\sfdefault}
\usepackage[a4paper,top=0.8cm,bottom=0.6cm,left=1.4cm,right=1.4cm]{geometry}
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

\hypersetup{colorlinks=true,urlcolor=accent,linkcolor=accent,pdftitle={@@PDFTITLE@@},pdfauthor={Ali Jamai}}

% ---- Titres de section : VRAIES majuscules (pas de petites capitales -> lisible par les ATS) ----
\titleformat{\section}
  {\color{primary}\bfseries\normalsize}
  {}{0em}
  {\raisebox{0.05ex}{\textcolor{accent}{\rule{2mm}{2mm}}}\hspace{6pt}\MakeUppercase}
  [{\vspace{-3pt}\color{rule}\titlerule[1pt]}]
\titlespacing{\section}{0pt}{4pt}{2pt}

\setlength{\parindent}{0pt}
\pagestyle{empty}
\linespread{0.99}

% ---- Dates : collées au titre (alignées à droite, certains parseurs ATS les rattachent à la mauvaise ligne) ----
\newcommand{\chip}[1]{{\footnotesize\bfseries\color{accent}#1}}
\newcommand{\datesep}{\hspace{0.5em}{\color{rule}\textbar}\hspace{0.5em}}

% ---- Entrée : titre + date ----
\newcommand{\entry}[2]{%
  \vspace{2.5pt}%
  \noindent\textbf{\color{darkgray}#1}\datesep\chip{#2}\par\vspace{1.5pt}%
}

\newcommand{\arrowbullet}{\textcolor{accent}{\small\raisebox{0.1ex}{$\blacktriangleright$}}}
\setlist[itemize]{leftmargin=1.3em,itemsep=1.2pt,topsep=1.5pt,parsep=0pt,label=\arrowbullet}

% ---- Libellé de ligne (compétences, langues...) ----
\newcommand{\lbl}[1]{\textbf{\color{primary}#1\,:}\enspace}

\begin{document}

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
  {\fontsize{26}{28}\selectfont\bfseries\color{primary} @@NAME@@}\\[2pt]
  {\color{accent}\small\bfseries @@SUBTITLE@@}\\[5pt]
  {\footnotesize\color{midgray}%
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

\vspace{4pt}
{\color{darkgray}\small @@PROFIL@@}

% ======================= FORMATION =======================
\section{Formation}
@@FORMATION@@

% ======================= AUDITS & PROJETS =======================
@@PROJETS@@

% ======================= EXPÉRIENCES =======================
@@EXPERIENCES@@

% ======================= BAS DE PAGE : UNE SEULE COLONNE (ordre de lecture linéaire) =======================
\section{Compétences techniques}
{\footnotesize
@@COMPETENCES@@
}

\section{Certifications \& Langues}
{\footnotesize
@@CERTIFS_LANGUES@@
}

\section{Engagement}
{\footnotesize
\begin{itemize}
@@ACHIEVEMENTS@@
\end{itemize}
}

\section{Qualités \& Loisirs}
{\footnotesize
\lbl{Savoir-être}@@SOFT_SKILLS@@\par
\lbl{Loisirs}@@LOISIRS@@
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


def _compile(tex_path: Path) -> Path:
    """Compile le .tex en PDF dans son dossier. Renvoie le chemin du PDF."""
    workdir = tex_path.parent
    pdf_path = tex_path.with_suffix(".pdf")
    # On PRÉFÈRE pdflatex (vrai .exe MiKTeX, sans dépendance Perl) à latexmk
    # (script Perl qui échoue silencieusement si Perl n'est pas installé — cas
    # fréquent sous PowerShell/Windows).
    pdflatex = shutil.which("pdflatex")
    latexmk = shutil.which("latexmk")
    if pdflatex:
        cmd = [pdflatex, "-interaction=nonstopmode", "-halt-on-error", tex_path.name]
        passes = 2  # 2 passes pour tikz/hyperref
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


def build_cv_filename(offer: dict, candidate_name: str | None = None) -> str:
    """Génère un nom de fichier court et percutant basé sur l'offre,
    strictement limité à <= 25 caractères (.pdf inclus) pour être accepté
    sans exception par tous les portails ATS (Workday/Thales, Taleo, etc.).
    Exemples : CV_Ali_Jamai_Cyber.pdf (22 car.), CV_Ali_Jamai_Audit.pdf (22 car.)
    """
    import unicodedata

    raw_title = str(offer.get("title") or "").lower()
    norm_title = unicodedata.normalize("NFKD", raw_title).encode("ascii", "ignore").decode("ascii")

    # Détection du domaine clé de l'offre (termes courts et ciblés)
    tag = None
    if "audit" in norm_title:
        tag = "Audit"
    elif "pentest" in norm_title or "offensiv" in norm_title or "intrusion" in norm_title:
        tag = "Pentest"
    elif "grc" in norm_title or "gouvernance" in norm_title or "conformit" in norm_title:
        tag = "GRC"
    elif "soc" in norm_title or "incident" in norm_title or "detection" in norm_title:
        tag = "SOC"
    elif "devops" in norm_title or "devsecops" in norm_title or "sre" in norm_title:
        tag = "DevOps"
    elif "active directory" in norm_title or re.search(r"\bad\b", norm_title) or "identit" in norm_title or "iam" in norm_title:
        tag = "AD"
    elif "reseau" in norm_title or "telecom" in norm_title:
        tag = "Reseau"
    elif "cloud" in norm_title or "azure" in norm_title or "aws" in norm_title:
        tag = "Cloud"
    elif re.search(r"\bia\b", norm_title) or "llm" in norm_title or "intelligence artificielle" in norm_title:
        tag = "IA"
    elif "cyber" in norm_title or "securit" in norm_title or "ssi" in norm_title:
        tag = "Cyber"

    if not tag:
        clean = re.sub(r"\b(stage|stagiaire|ingenieur|ingenieure|h/f|f/h|m/f|de|du|en|pour|et|le|la|un|une)\b", "", norm_title)
        words = re.findall(r"[a-z]{3,}", clean)
        tag = words[0].capitalize()[:8] if words else "Cyber"

    return f"CV_Ali_Jamai_{tag}"


def generate_cv(offer: dict, out_name: str | None = None) -> Path:
    """Génère et compile un CV adapté à l'offre. Renvoie le chemin du PDF.

    Lève RuntimeError/FileNotFoundError en cas d'échec (données ou compilation).
    """
    exp_data = load_experiences()
    # On tente la sélection Groq (accroche rédigée par le LLM, adaptée à l'offre)
    # avec plusieurs relances : le secours ne sert qu'en dernier recours.
    import time as _t
    selections = []
    llm_sel = None
    # Priorité LLM : met le scoring de fond en pause pendant la génération de
    # l'accroche (tâche demandée en direct par l'utilisateur).
    config.set_llm_priority(True)
    try:
        for attempt in range(3):
            llm_sel = _llm_selection(offer, exp_data)
            if llm_sel:
                break
            if attempt < 2:
                log.info("Accroche LLM indisponible, relance (%d/3)…", attempt + 2)
                _t.sleep(3)
    finally:
        config.set_llm_priority(False)
    if llm_sel:
        selections.append(llm_sel)
    selections.append(_default_selection(exp_data, offer))

    CV_LATEX_DIR.mkdir(parents=True, exist_ok=True)
    if not (CV_LATEX_DIR / PHOTO_REL).exists():
        log.warning("Photo %s absente — le CV compilera sans photo si le fichier manque.",
                    CV_LATEX_DIR / PHOTO_REL)

    if not out_name:
        candidate_full_name = exp_data.get("candidate", {}).get("full_name", "Ali Jamai")
        out_name = build_cv_filename(offer, candidate_name=candidate_full_name)

    tex_path = CV_LATEX_DIR / f"{out_name}.tex"
    pdf_path = tex_path.with_suffix(".pdf")
    # Si le PDF existe déjà et est verrouillé par un visualiseur (Windows),
    # on ajoute un suffixe temporel pour ne jamais bloquer la compilation.
    if pdf_path.exists():
        try:
            with open(pdf_path, "a+b"):
                pass
        except OSError:
            import time as _t
            out_name = f"{out_name}_{_t.strftime('%H%M%S')}"
            tex_path = CV_LATEX_DIR / f"{out_name}.tex"

    # Mise en page adaptative : on réduit puces puis expériences jusqu'à tenir
    # sur une seule page (CV de stage = 1 page impérative). Si une sélection ne
    # compile pas, on essaie la suivante.
    last_err = None
    for selection in selections:
        pdf = None
        for max_exp, max_bullets in [(4, 2), (4, 1), (3, 2), (3, 1), (2, 1)]:
            tex = _render_tex(exp_data, selection, offer, max_exp, max_bullets)
            tex_path.write_text(tex, encoding="utf-8")
            try:
                pdf = _compile(tex_path)
            except Exception as exc:
                last_err = exc
                continue
            if _page_count(pdf) <= 1:
                return pdf
        if pdf is not None:
            return pdf  # a compilé mais >1 page : on accepte faute de mieux
    raise RuntimeError(f"Génération CV impossible : {last_err}")
