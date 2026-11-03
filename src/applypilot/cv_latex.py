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
    "active_directory_identite": "Active Directory \\& Identité",
    "systemes_reseaux": "Systèmes \\& Réseau",
    "devops_devsecops": "Infrastructure \\& DevSecOps",
    "securite_offensive_pentest": "Sécurité offensive \\& Pentest",
    "securite_audits": "Sécurité, audit \\& conformité",
    "developpement": "Développement",
    "ia_data": "IA \\& Data",
}

# Types d'expériences classés comme « Expériences » (le reste = « Projets »).
_PRO_TYPES = {"experience_pro", "challenge"}


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
    "→": " vers ", "×": "x", "…": "...", "\u00a0": "~", "‰": "pour mille",
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
    for uni, repl in _UNICODE_FIX.items():
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
    return "".join(out)


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
    ids = [i for _, i in scored[:5]]

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
    cand = exp_data["candidate"]
    return (cand.get("objective") or
            "Élève-ingénieur en cybersécurité recherchant un stage de 6 mois "
            "à partir d'avril 2027.")


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

    # Consigne « accroche humanisée » : éviter que le paragraphe sonne IA.
    profil_rule = (
        "Accroche de CV en français, 2 ou 3 phrases courtes, à la première "
        "personne, écrite comme le ferait un vrai étudiant : naturelle, directe, "
        "concrete et sincère. Ancre 1 ou 2 éléments REELS et précis en lien avec "
        "CETTE offre, par exemple une techno ou un projet que j'ai fait. "
        "N'utilise JAMAIS les formules qui sonnent « IA » ou « CV bateau » : "
        "« Fort de », « Passionné par », « Doté d'une solide », « mettre à profit », "
        "« évoluer au sein de », « environnement stimulant », « environnement "
        "dynamique », « alliant ... et ... », « au carrefour de », « je suis "
        "convaincu », « rigoureux et motivé », « dynamique et motivé », ni les "
        "tirets cadratins. Varie la tournure d'une offre à l'autre. Uniquement "
        "les faits fournis, aucun chiffre inventé, aucune parenthèse."
    )

    system = (
        "Tu aides à ADAPTER un CV à une offre de stage, en français. "
        "RÈGLE ABSOLUE : tu ne sélectionnes et n'ordonnes que des éléments RÉELS "
        "fournis ; tu n'inventes AUCUNE compétence, expérience, entreprise ni "
        "chiffre. Tu réponds UNIQUEMENT par un objet JSON valide, sans texte autour."
    )
    user = (
        f"OBJECTIF DU CANDIDAT : {cand.get('objective')}\n\n"
        f"OFFRE :\nTitre : {offer.get('title')}\nEntreprise : "
        f"{offer.get('company') or offer.get('site')}\nLieu : {offer.get('location')}\n"
        f"Description : {(offer.get('full_description') or offer.get('description') or offer.get('score_reasoning') or '')[:1500]}\n\n"
        f"EXPÉRIENCES DISPONIBLES (identifiants) :\n{json.dumps(catalog, ensure_ascii=False)}\n\n"
        f"CATÉGORIES DE COMPÉTENCES : {skills_cats}\n\n"
        "Renvoie un JSON avec EXACTEMENT ces clés :\n"
        '{\n'
        '  "profil": ' + json.dumps(profil_rule, ensure_ascii=False) + ',\n'
        '  "selected_experience_ids": ["<3 à 4 ids les plus pertinents, ordonnés>"],\n'
        '  "skills_order": ["<catégories de compétences, pertinentes en premier>"]\n'
        "}"
    )
    try:
        client = get_client()
        raw = client.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            max_tokens=1200, temperature=0.5)
    except Exception as exc:
        log.warning("Appel LLM échoué : %s", exc)
        return None

    data = _extract_json(raw)
    if not data:
        return None

    valid_ids = {e["id"] for e in exp_data["experiences"]}
    sel = [i for i in data.get("selected_experience_ids", []) if i in valid_ids]
    if not sel:
        return None
    order = [c for c in data.get("skills_order", []) if c in exp_data["skills"]]
    # Complète avec les catégories manquantes pour ne rien perdre.
    order += [c for c in exp_data["skills"] if c not in order]
    profil = (data.get("profil") or "").strip() or _default_profil(exp_data, offer)
    return {"profil": profil, "selected_experience_ids": sel[:4], "skills_order": order}


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
def _render_xp_block(exp: dict, max_bullets: int = 3) -> str:
    title = latex_escape(exp["title"])
    org = latex_escape(exp.get("org", ""))
    dates = latex_escape(exp.get("dates", ""))
    lines = [f"\\xp{{{title}}}{{{org}}}{{{dates}}}"]
    bullets = exp.get("bullets", [])[:max_bullets]
    if bullets:
        lines.append("\\begin{itemize}")
        for b in bullets:
            lines.append(f"  \\item {latex_escape(b)}")
        lines.append("\\end{itemize}")
    return "\n".join(lines)


def _render_tex(exp_data: dict, selection: dict, offer: dict,
                max_exp: int = 4, max_bullets: int = 3) -> str:
    cand = exp_data["candidate"]
    by_id = {e["id"]: e for e in exp_data["experiences"]}
    selected = [by_id[i] for i in selection["selected_experience_ids"] if i in by_id][:max_exp]

    projets = [e for e in selected if e["type"] not in _PRO_TYPES]
    experiences = [e for e in selected if e["type"] in _PRO_TYPES]

    # Formation (verbatim depuis le JSON)
    formation = "\n".join(
        f"\\xp{{{latex_escape(ed['degree'])}}}{{{latex_escape(ed['institution'])}}}"
        f"{{{latex_escape(ed.get('dates',''))}}}"
        for ed in exp_data["education"]
    )

    # Compétences : catégories ordonnées, contenu verbatim
    skills = exp_data["skills"]
    comp_lines = []
    for cat in selection["skills_order"]:
        if cat not in skills or not skills[cat]:
            continue
        label = SKILL_LABELS.get(cat, cat.replace("_", " ").title())
        items = ", ".join(latex_escape(t) for t in skills[cat])
        comp_lines.append(f"\\textbf{{{label} :}} {items}.\\\\")
    competences = "\n".join(comp_lines)

    # Langues / certif / centres d'intérêt (verbatim)
    langs = " ; ".join(f"{latex_escape(l['langue'])} {latex_escape(l['niveau'])}"
                       for l in exp_data.get("languages", []))
    certs = ", ".join(latex_escape(c) for c in exp_data.get("certifications", []))
    achievements = " ; ".join(latex_escape(a) for a in exp_data.get("achievements", [])[:3])

    title = latex_escape(offer.get("title") or "Stage")
    company = latex_escape(offer.get("company") or offer.get("site") or "")
    candidature = f"Candidature : {title}" + (f" --- {company}" if company else "")
    profil = latex_escape(selection["profil"])

    # Sections Projets / Expériences (omises si vides)
    sec_projets = ""
    if projets:
        sec_projets = "\\cvsection{Projets}\n" + "\n\n".join(
            _render_xp_block(e, max_bullets) for e in projets)
    sec_exp = ""
    if experiences:
        sec_exp = "\\cvsection{Expériences}\n" + "\n\n".join(
            _render_xp_block(e, max_bullets) for e in experiences)

    fields = {
        "NAME": latex_escape(cand["full_name"]),
        "SUBTITLE": latex_escape(cand["title"]),
        "LOCATION": latex_escape(cand["location"]),
        "PHONE": latex_escape(cand["phone"]),
        "EMAIL": cand["email"],
        "LINKEDIN": cand["linkedin"], "GITHUB": cand["github"], "PORTFOLIO": cand["portfolio"],
        "PHOTO": PHOTO_REL,
        "CANDIDATURE": candidature,
        "PROFIL": profil,
        "FORMATION": formation,
        "PROJETS": sec_projets,
        "EXPERIENCES": sec_exp,
        "COMPETENCES": competences,
        "LANGUES": langs, "CERTIFS": certs, "ACHIEVEMENTS": achievements,
    }
    tex = _TEMPLATE
    for key, val in fields.items():
        tex = tex.replace(f"@@{key}@@", val)
    return tex


_TEMPLATE = r"""\documentclass[11pt,a4paper]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage[french]{babel}
\usepackage[a4paper,top=0.8cm,bottom=0.8cm,left=1.4cm,right=1.4cm]{geometry}
\usepackage{lmodern}
\usepackage{xcolor}
\usepackage{enumitem}
\usepackage{graphicx}
\usepackage{tikz}
\usepackage[hidelinks]{hyperref}

\definecolor{accent}{HTML}{1F3A5F}
\definecolor{grey}{HTML}{555555}

\setlist[itemize]{leftmargin=1.2em,itemsep=0pt,topsep=1pt,parsep=0pt}
\pagestyle{empty}
\setlength{\parindent}{0pt}
\renewcommand{\baselinestretch}{0.90}

\newcommand{\roundphoto}[1]{%
  \begin{tikzpicture}
    \begin{scope}
      \clip (0,0) circle (1.35cm);
      \node at (0,-0.18cm) {\includegraphics[width=3.0cm]{#1}};
    \end{scope}
    \draw[accent, line width=0.9pt] (0,0) circle (1.35cm);
  \end{tikzpicture}}

\newcommand{\cvsection}[1]{%
  \vspace{3pt}%
  {\normalsize\bfseries\color{accent}\MakeUppercase{#1}}\\[-4pt]%
  {\color{accent}\rule{\linewidth}{0.8pt}}\\[1pt]%
}
\newcommand{\xp}[3]{\textbf{#1} \hfill {\color{grey}\small #3}\\{\color{accent}\small #2}\par\vspace{2pt}}

\begin{document}

\begin{minipage}[c]{0.73\linewidth}
  {\Huge\bfseries\color{accent} @@NAME@@}\\[2pt]
  {\large @@SUBTITLE@@}\\[4pt]
  {\color{grey}\small @@LOCATION@@ \quad|\quad @@PHONE@@ \quad|\quad \href{mailto:@@EMAIL@@}{@@EMAIL@@}}\\
  {\color{grey}\small \href{@@LINKEDIN@@}{LinkedIn} \quad|\quad \href{@@GITHUB@@}{GitHub} \quad|\quad \href{@@PORTFOLIO@@}{Portfolio}}
\end{minipage}\hfill
\begin{minipage}[c]{0.24\linewidth}
  \raggedleft\roundphoto{@@PHOTO@@}
\end{minipage}

\vspace{4pt}
{\color{accent}\small\itshape @@CANDIDATURE@@}

\cvsection{Profil}
@@PROFIL@@

\cvsection{Formation}
@@FORMATION@@

@@PROJETS@@

@@EXPERIENCES@@

\cvsection{Compétences clés}
@@COMPETENCES@@

\cvsection{Langues, certification \& centres d'intérêt}
\textbf{Langues :} @@LANGUES@@.\quad \textbf{Certification :} @@CERTIFS@@.\\
\textbf{Centres d'intérêt :} @@ACHIEVEMENTS@@.

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


def generate_cv(offer: dict, out_name: str | None = None) -> Path:
    """Génère et compile un CV adapté à l'offre. Renvoie le chemin du PDF.

    Lève RuntimeError/FileNotFoundError en cas d'échec (données ou compilation).
    """
    exp_data = load_experiences()
    # On tente d'abord la sélection Groq ; en cas d'échec de compilation, on
    # retombe sur la sélection déterministe (profil « propre », sans texte LLM).
    selections = []
    llm_sel = _llm_selection(offer, exp_data)
    if llm_sel:
        selections.append(llm_sel)
    selections.append(_default_selection(exp_data, offer))

    CV_LATEX_DIR.mkdir(parents=True, exist_ok=True)
    if not (CV_LATEX_DIR / PHOTO_REL).exists():
        log.warning("Photo %s absente — le CV compilera sans photo si le fichier manque.",
                    CV_LATEX_DIR / PHOTO_REL)

    if not out_name:
        import hashlib
        import time as _t
        h = hashlib.md5((offer.get("url") or offer.get("title") or "cv").encode()).hexdigest()[:8]
        # Suffixe horaire -> nom unique à chaque génération : évite tout conflit
        # avec un PDF du même nom déjà OUVERT (Windows verrouille le fichier,
        # ce qui ferait échouer la compilation LaTeX).
        out_name = f"CV_Ali_Jamai_{h}_{_t.strftime('%H%M%S')}"
    tex_path = CV_LATEX_DIR / f"{out_name}.tex"

    # Mise en page adaptative : on réduit puces puis expériences jusqu'à tenir
    # sur une seule page (CV de stage = 1 page impérative). Si une sélection ne
    # compile pas, on essaie la suivante.
    last_err = None
    for selection in selections:
        pdf = None
        for max_exp, max_bullets in [(4, 3), (4, 2), (3, 2), (3, 1)]:
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
