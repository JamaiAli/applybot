"""Module de gestion des liens traceurs (JobNavigator Tracer Links) pour ApplyPilot.

Permet de suivre en temps réel la lecture des CV par les recruteurs :
1. Chaque CV personnalisé reçoit un lien traceur unique (ex: http://localhost:8765/t/<token> ou domaine public).
2. Lorsque le recruteur clique sur le lien (portfolio/profil) depuis le PDF :
   - Son User-Agent et son IP sont enregistrés.
   - Une alerte Telegram instantanée est envoyée au candidat.
   - Le recruteur est immédiatement redirigé (HTTP 302) vers le portfolio réel (jamaiali.github.io).
"""

from __future__ import annotations

import asyncio
import datetime
import html
import json
import logging
import os
import re
import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import web

from applypilot import config
from applypilot.database import (
    create_cv_tracker,
    get_recent_trackers,
    get_tracker,
    get_tracker_by_job_url,
    get_tracker_stats,
    record_tracker_hit,
)

if TYPE_CHECKING:
    from telegram import Bot

log = logging.getLogger("applypilot.tracer")

# Singleton runner pour le serveur web asynchrone
_runner: web.AppRunner | None = None
_site: web.TCPSite | None = None
BOT_KEY = web.AppKey("bot", Any)


# ---------------------------------------------------------------------------
# Détection de l'appareil & navigateur (User-Agent parsing léger)
# ---------------------------------------------------------------------------
def parse_user_agent(ua: str | None) -> str:
    """Analyse succincte du User-Agent pour identifier l'appareil et le navigateur."""
    if not ua:
        return "Inconnu"

    # Détection des bots / crawlers de prévisualisation
    bots = {
        "slackbot": "Slack (Aperçu)",
        "linkedinbot": "LinkedIn (Crawler)",
        "discordbot": "Discord (Aperçu)",
        "twitterbot": "Twitter / X (Aperçu)",
        "telegrambot": "Telegram (Aperçu)",
        "whatsapp": "WhatsApp (Aperçu)",
        "googlebot": "Googlebot",
        "bingbot": "Bingbot",
        "facebookexternalhit": "Meta / Facebook (Aperçu)",
    }
    ua_lower = ua.lower()
    for pattern, name in bots.items():
        if pattern in ua_lower:
            return f"🤖 {name}"

    # Système d'exploitation
    os_name = "Appareil inconnu"
    if "iphone" in ua_lower:
        os_name = "iPhone"
    elif "ipad" in ua_lower:
        os_name = "iPad"
    elif "android" in ua_lower:
        os_name = "Android"
    elif "macintosh" in ua_lower or "mac os x" in ua_lower:
        os_name = "Mac"
    elif "windows nt 10" in ua_lower or "windows nt 11" in ua_lower:
        os_name = "Windows"
    elif "windows" in ua_lower:
        os_name = "Windows"
    elif "linux" in ua_lower:
        os_name = "Linux"

    # Navigateur
    browser = ""
    if "edg/" in ua_lower:
        browser = "Edge"
    elif "chrome/" in ua_lower and "chromium/" not in ua_lower and "edg/" not in ua_lower:
        browser = "Chrome"
    elif "safari/" in ua_lower and "chrome/" not in ua_lower:
        browser = "Safari"
    elif "firefox/" in ua_lower:
        browser = "Firefox"

    if browser:
        return f"{os_name} ({browser})"
    return os_name


# ---------------------------------------------------------------------------
# Cibles officielles d'Ali Jamai (Portfolio, LinkedIn, GitHub)
# ---------------------------------------------------------------------------
def get_candidate_links() -> dict[str, str]:
    """Récupère les URLs officielles (portfolio, linkedin, github) d'Ali Jamai."""
    exp_path = config.APP_DIR / "experiences.json"
    cand = {}
    if exp_path.exists():
        try:
            data = json.loads(exp_path.read_text(encoding="utf-8"))
            cand = data.get("candidate", {})
        except Exception:
            pass
    return {
        "portfolio": cand.get("portfolio") or "https://github.com",
        "linkedin": cand.get("linkedin") or "https://www.linkedin.com",
        "github": cand.get("github") or "https://github.com",
    }


def get_default_target_url(link_type: str = "portfolio") -> str:
    """Récupère l'URL de destination selon le type de lien."""
    links = get_candidate_links()
    return links.get(link_type) or links["portfolio"]


# ---------------------------------------------------------------------------
# Création & résolution des traceurs (Portfolio, LinkedIn, GitHub)
# ---------------------------------------------------------------------------
def get_or_create_tracer(
    offer: dict,
    target_url: str | None = None,
    link_type: str = "portfolio",
) -> tuple[str, str]:
    """Récupère ou crée un lien traceur pour l'offre spécifiée et le type de lien (portfolio, linkedin, github).

    Renvoie (token, tracer_url).
    """
    job_url = offer.get("url") or ""
    company = offer.get("company") or offer.get("site") or "Entreprise"
    title = offer.get("title") or "Candidature"
    cand_links = get_candidate_links()
    target = target_url or cand_links.get(link_type) or cand_links["portfolio"]

    # Si un traceur existe déjà pour cette offre et ce type, on le réutilise
    if job_url:
        existing = get_tracker_by_job_url(job_url, link_type=link_type)
        if existing:
            token = existing["token"]
            return token, f"{config.TRACER_BASE_URL}/t/{token}"

    token = secrets.token_hex(4)  # 8 caractères hexadécimaux
    create_cv_tracker(
        token=token,
        job_url=job_url,
        company=company,
        title=title,
        target_url=target,
        link_type=link_type,
    )
    tracer_url = f"{config.TRACER_BASE_URL}/t/{token}"
    log.info("Lien traceur créé pour '%s' (%s, %s) : %s", title, company, link_type, tracer_url)
    return token, tracer_url


def get_or_create_all_tracers(offer: dict) -> dict[str, str]:
    """Crée ou récupère les 3 traceurs (portfolio, linkedin, github) pour cette offre.
    Renvoie un dictionnaire {"portfolio": url, "linkedin": url, "github": url}.
    """
    res = {}
    for ltype in ("portfolio", "linkedin", "github"):
        _, turl = get_or_create_tracer(offer, link_type=ltype)
        res[ltype] = turl
    return res


def publish_tracers(offer: dict) -> dict[str, str] | None:
    """Liens traceurs prêts à être écrits dans un CV pour cette offre, ou None
    s'il faut y laisser les liens directs.

    Avec un traceur hébergé (TRACER_API_KEY défini), les liens sont d'abord
    déclarés au Worker : tant qu'il ne les connaît pas, il ne saurait pas où
    rediriger le recruteur. Si la déclaration échoue, on renvoie None — un CV
    avec des liens directs vaut mieux qu'un CV avec des liens qui mènent ailleurs.
    """
    urls = get_or_create_all_tracers(offer)
    if not config.TRACER_API_KEY:
        return urls  # serveur auto-hébergé, joignable directement
    from applypilot.tracking import remote

    job_url = offer.get("url") or ""
    payload = []
    for ltype in ("portfolio", "linkedin", "github"):
        row = get_tracker_by_job_url(job_url, link_type=ltype) if job_url else None
        if not row:
            return None
        payload.append({"token": row["token"], "target": row["target_url"], "link_type": ltype,
                        "company": row.get("company") or "", "title": row.get("title") or "",
                        "job_url": job_url})
    return urls if remote.register(payload) else None


# ---------------------------------------------------------------------------
# Notification Telegram
# ---------------------------------------------------------------------------
def format_hit_notification(tracker: dict) -> str:
    """Mise en forme HTML de l'alerte pour Telegram."""
    company = html.escape(tracker.get("company") or "Entreprise")
    title = html.escape(tracker.get("title") or "Offre")
    ip = html.escape(tracker.get("ip") or "Inconnue")
    device = html.escape(parse_user_agent(tracker.get("user_agent")))
    open_count = tracker.get("open_count") or 1
    job_url = tracker.get("job_url") or ""
    link_type = tracker.get("link_type", "portfolio")

    type_meta = {
        "linkedin": ("💼", "Profil LinkedIn"),
        "github": ("🐙", "Projet GitHub"),
        "portfolio": ("🌐", "Portfolio"),
    }.get(link_type, ("🔗", link_type.capitalize()))
    icon, type_label = type_meta

    import zoneinfo
    paris_tz = zoneinfo.ZoneInfo("Europe/Paris")
    now_fr = datetime.datetime.now(paris_tz).strftime("%d/%m/%Y à %H:%M:%S")

    lines = [
        f"🔔 <b>Consultation de ton {type_label} détectée !</b>",
        "",
        f"🎯 <b>Élément consulté :</b> {icon} {type_label}",
        f"🏢 <b>Entreprise :</b> {company}",
        f"💼 <b>Contexte / Offre :</b> {title}",
        f"📱 <b>Appareil :</b> {device}",
        f"🌐 <b>IP :</b> <code>{ip}</code>",
        f"🔢 <b>Consultation n° :</b> {open_count}",
        f"⏰ <b>Date :</b> {now_fr}",
    ]
    if job_url and job_url != "profil_permanent":
        lines.append(f'🔗 <a href="{html.escape(job_url)}">Voir l\'offre associée</a>')

    return "\n".join(lines)


async def _notify_owner(bot: Any, tracker: dict) -> None:
    """Envoie la notification Telegram au propriétaire en tâche de fond."""
    owner_id_raw = os.environ.get("TELEGRAM_USER_ID", "0").strip()
    try:
        owner_id = int(owner_id_raw)
    except ValueError:
        owner_id = 0

    if not owner_id or not bot:
        return

    msg = format_hit_notification(tracker)
    try:
        from telegram.constants import ParseMode
        await bot.send_message(
            chat_id=owner_id,
            text=msg,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        log.info("Alerte de consultation envoyée à Telegram (owner=%s)", owner_id)
    except Exception as exc:
        log.error("Échec de l'envoi de l'alerte Telegram : %s", exc)


# ---------------------------------------------------------------------------
# Serveur HTTP aiohttp
# ---------------------------------------------------------------------------
async def handle_tracer(request: web.Request) -> web.StreamResponse:
    """Route GET /t/{token} : enregistre le clic et redirige vers la cible (Portfolio, LinkedIn, GitHub)."""
    raw_token = request.match_info.get("token", "").strip()
    token = raw_token.lower()
    # Récupération de la vraie IP (en tenant compte d'un proxy éventuel comme Cloudflare ou ngrok)
    ip = (
        request.headers.get("CF-Connecting-IP")
        or request.headers.get("X-Forwarded-For", request.remote or "Inconnu").split(",")[0].strip()
    )
    user_agent = request.headers.get("User-Agent", "Inconnu")

    cand_links = get_candidate_links()

    # Support des liens permanents directs : /t/portfolio, /t/linkedin, /t/github
    if token in ("portfolio", "linkedin", "github"):
        if not get_tracker(token):
            create_cv_tracker(
                token=token,
                job_url="profil_permanent",
                company="Lien Direct / Profil Public",
                title=f"Profil {token.capitalize()}",
                target_url=cand_links.get(token) or cand_links["portfolio"],
                link_type=token,
            )

    tracker = record_tracker_hit(token, ip=ip, user_agent=user_agent)
    if tracker:
        log.info(
            "Hit traceur '%s' (%s) pour '%s' (%s) depuis %s (%s)",
            token,
            tracker.get("link_type"),
            tracker.get("title"),
            tracker.get("company"),
            ip,
            user_agent,
        )
        bot = request.app.get(BOT_KEY)
        if bot:
            asyncio.create_task(_notify_owner(bot, tracker))
        target = tracker.get("target_url") or cand_links.get(tracker.get("link_type")) or cand_links["portfolio"]
        raise web.HTTPFound(location=target)

    # Si token inconnu, redirection par défaut vers le portfolio
    log.warning("Hit traceur avec token inconnu : '%s'", raw_token)
    raise web.HTTPFound(location=cand_links["portfolio"])


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "applypilot-tracer"})


async def handle_stats(request: web.Request) -> web.Response:
    return web.json_response(get_tracker_stats())


# ---------------------------------------------------------------------------
# API pour l'Extension Chrome (Autofill 1-Clic)
# ---------------------------------------------------------------------------
def resolve_cv_path(job_url: str | None = None) -> Path | None:
    """Résout le chemin du CV PDF le plus pertinent pour la candidature."""
    from applypilot import bot_core
    student_cv = config.APP_DIR / "cv_job_etudiant.pdf"

    # 1. Vérification si c'est un job étudiant / intérim
    if job_url and bot_core.is_student_job(job_url):
        if student_cv.exists():
            return student_cv

    # 2. Vérification par URL dans la base jobs
    if job_url:
        row = bot_core.fetch_one(job_url)
        if row and row.get("tailored_resume_path"):
            p = Path(row["tailored_resume_path"])
            if p.exists():
                return p

    # 3. Vérification de la dernière offre préparée
    if bot_core.get_last_prepared_type() == "student" and student_cv.exists():
        return student_cv

    last_prepared_cv = bot_core.get_last_prepared_cv()
    if last_prepared_cv and last_prepared_cv.exists():
        if last_prepared_cv == student_cv:
            return student_cv

    last = bot_core.get_last_prepared()
    if last:
        if bot_core.is_student_job(last) and student_cv.exists():
            return student_cv
        row = bot_core.fetch_one(last)
        if row and row.get("tailored_resume_path"):
            p = Path(row["tailored_resume_path"])
            if p.exists():
                return p

    # 4. CV adapté le plus récent généré (pour stage / cybersécurité)
    cv_dir = config.APP_DIR / "cv_latex"
    if cv_dir.exists():
        from applypilot.cv_latex import cv_prefix
        pfx = cv_prefix()
        pdfs = sorted(cv_dir.glob(f"{pfx}_*.pdf"), key=lambda f: f.stat().st_mtime, reverse=True)
        if not pdfs:
            pdfs = sorted(cv_dir.glob("*.pdf"), key=lambda f: f.stat().st_mtime, reverse=True)
        if pdfs:
            return pdfs[0]

    # 5. CV de secours
    for fallback in [student_cv, config.RESUME_PDF_PATH]:
        if fallback and Path(fallback).exists():
            return Path(fallback)
    return None


def get_candidate_data(job_url: str | None = None) -> dict:
    """Rassemble toutes les informations personnelles et réponses Q&A d'Ali."""
    from applypilot import autoapply
    p = autoapply._profile()
    pers = p.get("personal", {})
    qa = autoapply._qa_knowledge()

    # Lien Portfolio ou Traceur pour cette offre
    portfolio_url = pers.get("portfolio_url") or ""
    if job_url:
        try:
            _, turl = get_or_create_tracer({"url": job_url})
            if (turl
                    and turl.startswith("https://")
                    and not any(h in turl.lower() for h in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "192.168.", "10.", "172."))):
                portfolio_url = turl
        except Exception:
            pass

    full = pers.get("full_name", "Candidat")
    first = pers.get("preferred_name") or (full.split()[0] if full else "Ali")
    last = " ".join(full.split()[1:]) if len(full.split()) > 1 else ""

    cv_p = resolve_cv_path(job_url)
    from applypilot import bot_core
    is_student = (cv_p and "cv_job_etudiant" in cv_p.name) or bot_core.is_student_job(job_url)
    from applypilot.cv_latex import cv_prefix
    pfx = cv_prefix()
    cv_name = f"{pfx}_Job_Etudiant.pdf" if is_student else (cv_p.name if (cv_p and cv_p.exists()) else f"{pfx}.pdf")

    city = pers.get("city", "")
    postal = pers.get("postal_code", "")
    address = f"{city} ({postal}), France" if city and postal else (city or "France")

    edu = p.get("education", {})
    qa_data = dict(qa)
    if is_student:
        qa_data["disponibilite"] = "Immédiate, disponible en semaine, en soirée et le week-end."
        qa_data["disponibilite_stage"] = "Immédiate, disponible en semaine, en soirée et le week-end."
        qa_data["type_contrat"] = "Job étudiant / Temps partiel / Intérim"
        qa_data["mobilite_geographique"] = "Bourges et agglomération (titulaire du permis B)."
        qa_data["teletravail"] = "Disponible sur site"

    return {
        "status": "ok",
        "personal": {
            "full_name": full,
            "first_name": first,
            "last_name": last,
            "email": pers.get("email", ""),
            "phone": pers.get("phone", ""),
            "address": address,
            "city": city,
            "postal_code": postal,
            "country": pers.get("country", "France"),
            "nationality": pers.get("nationality", ""),
            "citizenship": pers.get("citizenship", ""),
            "linkedin": pers.get("linkedin_url", ""),
            "github": pers.get("github_url", ""),
            "portfolio": portfolio_url,
        },
        "education": {
            "school": edu.get("school", ""),
            "degree": edu.get("degree", ""),
            "specialite": edu.get("specialite", ""),
            "start_date": edu.get("start_date", ""),
            "end_date": edu.get("end_date", ""),
            "is_current": edu.get("is_current", "Yes"),
            "city": edu.get("city", ""),
        },
        "qa": qa_data,
        "has_cv": bool(cv_p and cv_p.exists()),
        "cv_name": cv_name,
    }


async def handle_candidate(request: web.Request) -> web.Response:
    """Route GET /api/candidate : données pour le pré-remplissage."""
    job_url = request.query.get("url")
    data = get_candidate_data(job_url)
    return web.json_response(data)


async def handle_cv(request: web.Request) -> web.Response:
    """Route GET /api/cv : téléchargement du PDF adapté pour attachement automatique."""
    job_url = request.query.get("url")
    cv_p = resolve_cv_path(job_url)
    if not cv_p or not cv_p.exists():
        return web.Response(status=404, text="CV non trouvé")
    from applypilot.cv_latex import cv_prefix
    pfx = cv_prefix()
    filename = f"{pfx}_Job_Etudiant.pdf" if "cv_job_etudiant" in cv_p.name else cv_p.name
    return web.Response(
        body=cv_p.read_bytes(),
        content_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


async def handle_smart_answer(request: web.Request) -> web.Response:
    """Route POST /api/smart-answer : réponse IA personnalisée aux questions ouvertes."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    question = (body.get("question") or "").strip()
    job_title = body.get("job_title", "")
    company = body.get("company", "")
    try:
        max_length = int(body.get("max_length") or 0)
    except (TypeError, ValueError):
        max_length = 0
    if not question:
        return web.json_response({"answer": "", "source": "none"})
    answer, source = await asyncio.to_thread(smart_answer, question, job_title, company, max_length)
    return web.json_response({"answer": answer, "source": source})


def _candidate_facts() -> str:
    """Faits réels du candidat, en clair, pour que le LLM réponde sans inventer."""
    exp_path = config.APP_DIR / "experiences.json"
    try:
        data = json.loads(exp_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    cand = data.get("candidate", {})
    lines = [f"Candidat : {cand.get('full_name', 'Candidat')}, {cand.get('title', '')}.",
             f"Objectif : {cand.get('objective', '')}",
             f"Lieu : {cand.get('location', '')}. Nationalité : {cand.get('nationality', '')}."]
    for ed in data.get("education", []):
        lines.append(f"Formation : {ed.get('institution', '')} — {ed.get('degree', '')} ({ed.get('dates', '')}).")
    for exp in data.get("experiences", []):
        first = str((exp.get("bullets") or [""])[0]).replace("**", "")
        lines.append(f"{exp.get('type', 'projet')} : {exp.get('title', '')} ({exp.get('org', '')}, "
                     f"{exp.get('dates', '')}). {first}")
    for cat, items in data.get("skills", {}).items():
        lines.append(f"Compétences {cat} : {', '.join(map(str, items))}.")
    lines.append("Certifications : " + " ; ".join(map(str, data.get("certifications", []))))
    lines.append("Langues : " + " ; ".join(f"{l.get('langue')} ({l.get('niveau')})" for l in data.get("languages", [])))
    lines.append("Distinctions : " + " ; ".join(str(a).replace("**", "") for a in data.get("achievements", [])))
    return "\n".join(lines)


def smart_answer(question: str, job_title: str = "", company: str = "", max_length: int = 0) -> tuple[str, str]:
    """Réponse à une question de formulaire de candidature : (réponse, source).

    D'abord la banque de réponses (faits fixes : disponibilité, diplôme, mobilité…),
    sinon le LLM, à partir des seuls faits réels du candidat. ``max_length`` :
    taille maximale du champ (0 = libre).
    """
    def fit(text: str) -> str:
        text = (text or "").strip()
        if max_length and len(text) > max_length:
            cut = text[:max_length]
            text = cut[:cut.rfind(" ")].rstrip(" ,;:") if " " in cut else cut
        return text

    from applypilot import autoapply
    qa = autoapply._qa_knowledge()
    q_lower = question.lower()

    # Table de correspondances avancées pour la banque Q&A
    qa_rules = [
        # Questions préliminaires fréquentes (SmartRecruiters…), à tester avant les règles larges.
        (re.compile(r"ann[ée]es?\s+d.exp[ée]rience|years?\s+of\s+(professional\s+)?experience", re.I), "annees_experience"),
        (re.compile(r"dipl[ôo]me\s+obtenu|plus\s+haut\s+(niveau|dipl)|highest\s+(degree|level)", re.I), "diplome_obtenu"),
        (re.compile(r"entendu\s+parler|heard\s+about|connu\s+notre|how\s+did\s+you\s+(hear|find)", re.I), "source_offre"),
        (re.compile(r"convention", re.I), "convention_stage"),
        (re.compile(r"permis|driving[\s_-]*licen", re.I), "permis_de_conduire"),
        (re.compile(r"v[ée]hicule|voiture|car[\s_-]*owner", re.I), "vehicule_personnel"),
        (re.compile(r"jusqu['\s]*[àa]\s*quand|limite.*d[ée]marrage|fin.*d[ée]marrage", re.I), "date_fin_demarrage_stage"),
        (re.compile(r"a\s*partir\s*de\s*quand.*disponible|date.*d[ée]but|start.*date|quand.*commencer", re.I), "date_debut_stage"),
        (re.compile(r"[ée]tablissement|[ée]cole|school|university|universit[ée]|institut", re.I), "etablissement"),
        (re.compile(r"type.*stage", re.I), "type_stage"),
        (re.compile(r"dur[ée]e|duration", re.I), "duree_stage"),
        (re.compile(r"disponibilit[ée]|availability|pr[ée]avis|notice[\s_-]*period", re.I), "disponibilite_stage"),
        (re.compile(r"pr[ée]tention|salaire|salary|compensation|remun[ée]ration|gratification", re.I), "pretentions_salariales"),
        (re.compile(r"mobilit[ée]|d[ée]placement|relocat", re.I), "mobilite_geographique"),
        (re.compile(r"t[ée]l[ée]travail|remote|hybride", re.I), "teletravail"),
        (re.compile(r"niveau.*(diplom|[ée]tud)|dernier dipl|education[\s_-]*level", re.I), "niveau_etudes"),
        (re.compile(r"dipl[ôo]me[\s_-]*pr[ée]par[ée]|cursus|degree", re.I), "diplome_prepare"),
        (re.compile(r"ann[ée]e[\s_-]*dipl[ôo]me|graduation[\s_-]*year", re.I), "annee_diplome"),
        (re.compile(r"anglais|english|toeic|langue", re.I), "langues"),
        (re.compile(r"source|comment.*connu|how.*hear", re.I), "source_offre"),
        (re.compile(r"handicap|rqth|disability", re.I), "handicap"),
    ]

    prof = autoapply._profile()
    pers = prof.get("personal", {})
    edu = prof.get("education", {})
    school_name = edu.get("school") or prof.get("experience", {}).get("school") or ""
    start_date_str = prof.get("internship", {}).get("start_date") or prof.get("availability", {}).get("earliest_start_date") or ""
    conv_school = f" par mon établissement ({school_name})" if school_name else " par mon établissement"

    sentence_answers = {
        "convention_stage": f"Oui, je disposerai d'une convention de stage délivrée{conv_school} pour toute la durée du stage.",
        "date_debut_stage": f"Je suis disponible pour débuter mon stage à partir de {start_date_str}." if start_date_str else "",
        "etablissement": school_name,
        "type_stage": prof.get("internship", {}).get("type", "Stage conventionné"),
        "duree_stage": prof.get("internship", {}).get("duration", "4 à 6 mois"),
        "disponibilite_stage": prof.get("internship", {}).get("availability") or start_date_str,
        "pretentions_salariales": "Selon la grille légale de rémunération des stagiaires en vigueur au sein de l'entreprise.",
        "mobilite_geographique": prof.get("internship", {}).get("remote_preference", "Mobile en France."),
        "permis_de_conduire": qa.get("permis_de_conduire", "Oui, je suis titulaire du permis de conduire."),
        "vehicule_personnel": qa.get("vehicule_personnel", "Non"),
        "nationalite": pers.get("nationality", ""),
        "teletravail": "Je suis disponible pour travailler sur site, en mode hybride ou en télétravail.",
        "niveau_etudes": prof.get("experience", {}).get("education_level", ""),
        "diplome_prepare": edu.get("degree") or prof.get("experience", {}).get("education_level", ""),
        "source_offre": qa.get("source_offre") or "Via une plateforme d'offres en ligne.",
        "handicap": "Non concerné",
        "annees_experience": "0 : je suis étudiant, mon expérience vient de mes stages et de mes projets.",
        "diplome_obtenu": qa.get("diplome_obtenu") or "Baccalauréat (cursus supérieur en cours)",
        "date_fin_demarrage_stage": qa.get("date_fin_demarrage_stage") or "Démarrage possible selon les dates de la convention.",
    }

    for pat, key in qa_rules:
        if pat.search(q_lower):
            ans = qa.get(key) or sentence_answers.get(key)
            if ans:
                return fit(ans), "qa_bank"

    # Recherche directe par nom de clé Q&A
    # Seulement si la question contient l'intitulé complet de la clé. Un seul mot en
    # commun ne suffit pas : « stage » ou « durée » figurent dans presque toutes les
    # questions, et une question de motivation recevait la réponse sur la disponibilité.
    for k, v in qa.items():
        k_clean = k.replace("_", " ").lower()
        if len(k_clean) > 5 and k_clean in q_lower:
            return fit(str(v)), "qa_bank"

    # Génération IA (Groq / Gemini) sans invention
    try:
        from applypilot.llm import get_client
        client = get_client()
        limit = (f"La réponse doit tenir en {max_length} caractères MAXIMUM. " if max_length else "")
        short = max_length and max_length <= 250
        system_prompt = (
            "Tu réponds, à la place du candidat et à la première personne, à une question d'un formulaire de "
            "candidature à un stage. Réponds en français, directement, sans formule d'introduction ni de politesse. "
            + ("Réponse très courte : quelques mots ou une phrase. " if short
               else "Réponse concise : 2 à 4 phrases claires, ton professionnel et naturel. ")
            + limit +
            "RÈGLE ABSOLUE : appuie-toi UNIQUEMENT sur les faits ci-dessous. N'invente aucune expérience, "
            "compétence, date, chiffre ni diplôme. Si les faits ne permettent pas de répondre, dis-le simplement "
            "et honnêtement en une phrase plutôt que d'inventer. N'affirme RIEN sur l'entreprise (son activité, "
            "sa taille, sa réputation) : tu ne la connais pas ; parle de ce que je cherche et de ce que je sais faire.\n\n"
            f"FAITS RÉELS DU CANDIDAT :\n{_candidate_facts()[:6000]}"
        )
        user_prompt = f"Entreprise : {company}\nPoste / page : {job_title}\nQuestion posée : {question}"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        reply = client.chat(messages=messages, max_tokens=600)
        return fit(reply.strip().strip('"«» ')), "ai"
    except Exception as exc:
        log.warning("Smart answer échec : %s", exc)
        return "", "error"


async def handle_smart_select(request: web.Request) -> web.Response:
    """Route POST /api/smart-select : sélection IA de la meilleure option dans un menu déroulant."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    question = (body.get("question") or "").strip()
    options = body.get("options", [])
    job_title = body.get("job_title", "")

    if not options:
        return web.json_response({"ok": False, "selected_value": None, "source": "none"})

    if len(options) == 1:
        return web.json_response({
            "ok": True,
            "selected_value": options[0].get("value"),
            "selected_text": options[0].get("text"),
            "source": "single_option",
        })

    try:
        from applypilot.llm import get_client
        client = get_client()

        opts_formatted = "\n".join(
            f"- [Valeur: {opt.get('value')}] : {opt.get('text')}"
            for opt in options[:40]
        )

        cand_facts = _candidate_facts()
        system_prompt = (
            "Tu es l'assistant de candidature pour postuler aux offres de stage et d'emploi.\n"
            f"FAITS RÉELS DU CANDIDAT :\n{cand_facts[:2000]}\n\n"
            "Tâche : Parmi la liste d'options ci-dessous pour ce champ de formulaire, choisis l'option qui correspond le mieux au profil réel du candidat.\n"
            "Réponds STRICTEMENT par un objet JSON au format exact suivant, sans aucun autre texte :\n"
            '{"selected_value": "<la valeur exacte choisie>"}'
        )

        user_prompt = (
            f"Champ / Question : {question}\n"
            f"Intitulé de poste : {job_title}\n"
            f"Options disponibles :\n{opts_formatted}\n\n"
            "Option choisie (JSON uniquement) :"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        reply = await asyncio.to_thread(client.chat, messages=messages, max_tokens=300)

        # Extraction JSON
        m = re.search(r'\"selected_value\"\s*:\s*\"([^\"]+)\"', reply)
        if m:
            val = m.group(1)
            matched_opt = next((o for o in options if str(o.get("value")) == val), None)
            return web.json_response({
                "ok": True,
                "selected_value": val,
                "selected_text": matched_opt.get("text") if matched_opt else "",
                "source": "ai",
            })

        import json as py_json
        try:
            parsed = py_json.loads(reply.strip())
            if "selected_value" in parsed:
                val = str(parsed["selected_value"])
                matched_opt = next((o for o in options if str(o.get("value")) == val), None)
                return web.json_response({
                    "ok": True,
                    "selected_value": val,
                    "selected_text": matched_opt.get("text") if matched_opt else "",
                    "source": "ai",
                })
        except Exception:
            pass

    except Exception as exc:
        log.warning("Smart select LLM échec : %s", exc)

    return web.json_response({"ok": False, "selected_value": None, "source": "error"})


@web.middleware
async def cors_middleware(request: web.Request, handler) -> web.StreamResponse:
    """Middleware pour autoriser les requêtes cross-origin et Private Network Access depuis l'extension Chrome."""
    if request.method == "OPTIONS":
        resp = web.Response(status=200)
    else:
        try:
            resp = await handler(request)
        except web.HTTPException as ex:
            ex.headers["Access-Control-Allow-Origin"] = "*"
            ex.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            ex.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With, Access-Control-Request-Private-Network"
            ex.headers["Access-Control-Allow-Private-Network"] = "true"
            ex.headers["Access-Control-Expose-Headers"] = "Content-Disposition"
            raise ex
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With, Access-Control-Request-Private-Network"
    resp.headers["Access-Control-Allow-Private-Network"] = "true"
    resp.headers["Access-Control-Expose-Headers"] = "Content-Disposition"
    return resp


def create_tracer_app(bot: Any = None) -> web.Application:
    """Crée l'application aiohttp avec les routes de tracking et d'autofill."""
    app = web.Application(middlewares=[cors_middleware])
    app[BOT_KEY] = bot
    app.router.add_get("/t/{token}", handle_tracer)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/stats", handle_stats)
    app.router.add_get("/api/candidate", handle_candidate)
    app.router.add_get("/api/cv", handle_cv)
    app.router.add_get("/api/get-cv", handle_cv)
    app.router.add_post("/api/smart-answer", handle_smart_answer)
    app.router.add_post("/api/smart-select", handle_smart_select)
    return app


async def start_tracer_server(
    bot: Any = None,
    host: str = config.TRACER_HOST,
    port: int = config.TRACER_PORT,
) -> None:
    """Démarre le serveur HTTP aiohttp dans la boucle asyncio courante."""
    global _runner, _site
    if _runner is not None:
        return  # déjà démarré

    app = create_tracer_app(bot=bot)
    _runner = web.AppRunner(app)
    await _runner.setup()
    try:
        _site = web.TCPSite(_runner, host=host, port=port)
        await _site.start()
        log.info("Serveur traceur démarré sur http://%s:%d (Base URL: %s)", host, port, config.TRACER_BASE_URL)
    except Exception as exc:
        log.error("Impossible de lier le serveur traceur sur %s:%d : %s", host, port, exc)
        await _runner.cleanup()
        _runner = None
        _site = None


async def stop_tracer_server() -> None:
    """Arrête proprement le serveur HTTP traceur."""
    global _runner, _site
    if _runner is not None:
        await _runner.cleanup()
        _runner = None
        _site = None
        log.info("Serveur traceur arrêté.")
