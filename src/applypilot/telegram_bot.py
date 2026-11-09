"""Pilotage Telegram d'ApplyPilot — « depuis le téléphone », mains libres.

Alternative à Discord (compte perso flaggé) : Telegram est permissif pour les
bots personnels. Mêmes fonctions, mêmes règles de sécurité.

Le bot est la *surface de contrôle* du pipeline : il cherche, score, présente
les meilleures offres sous forme de messages avec boutons, et n'agit QUE sur
validation explicite.

Règles de sécurité câblées ici (non négociables) :
  * Accès restreint au SEUL user ID Telegram du propriétaire (gate sur chaque
    commande et chaque clic de bouton).
  * Aucune soumission sans ✅ explicite. Le ✅ enregistre le consentement et
    place l'offre en `ready_to_apply` ; la soumission réelle (Playwright,
    Phase 4b) reste une étape séparée. Dry-run par défaut.
  * Aucun secret en clair : token et user ID viennent de ~/.applypilot/.env
    (TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID).
  * Rythme limité : recherche quotidienne espacée, scoring via Groq (rate-limité).

Lancement :  applypilot telegram-bot   (ou  python -m applypilot.telegram_bot)
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import html
import logging
import os
import re
import subprocess
import sys

from applypilot import config

# load_env() DOIT précéder tout import de llm (vars lues à l'import du module).
config.load_env()

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from applypilot import bot_core
from applypilot.database import get_stats
from applypilot.discovery.dates import format_age_fr

log = logging.getLogger("applypilot.telegram")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
try:
    OWNER_ID = int(os.environ.get("TELEGRAM_USER_ID", "0").strip() or "0")
except ValueError:
    OWNER_ID = 0

MIN_SCORE = bot_core.MIN_SCORE
MAX_CARDS = bot_core.MAX_CARDS
DAILY_HOUR = int(os.environ.get("BOT_DAILY_HOUR", "9"))
# Intervalle de la veille automatique (minutes). Défaut 10.
CYCLE_MINUTES = int(os.environ.get("BOT_CYCLE_MINUTES", "10"))
# Cadence dédiée au scraping Indeed (heures). Indeed est lourd (beaucoup de
# requêtes) : on l'espace davantage que HelloWork pour éviter les blocages.
# HelloWork reste à CYCLE_MINUTES ; Indeed est scrapé toutes les N heures.
INDEED_CYCLE_HOURS = float(os.environ.get("BOT_INDEED_CYCLE_HOURS", "2"))
# Nombre de cycles de veille entre deux scrapes Indeed (>= 1).
_INDEED_EVERY_TICKS = (max(1, round(INDEED_CYCLE_HOURS * 60 / CYCLE_MINUTES))
                       if CYCLE_MINUTES > 0 else 1)

# CV fixe pour les jobs étudiants / intérim (déposé par Ali).
JOB_CV = config.APP_DIR / "cv_job_etudiant.pdf"


# ---------------------------------------------------------------------------
# Gate d'accès : seul le propriétaire peut parler au bot.
# ---------------------------------------------------------------------------
def _authorized(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id == OWNER_ID)


async def _deny(update: Update) -> None:
    if update.callback_query:
        await update.callback_query.answer("⛔ Accès réservé.", show_alert=True)
    elif update.effective_message:
        await update.effective_message.reply_text("⛔ Accès réservé.")


# ---------------------------------------------------------------------------
# Rendu d'une offre
# ---------------------------------------------------------------------------
def _format_posted(posted_at: str | None) -> str | None:
    """Date de publication en français : « aujourd'hui », « hier »,
    « il y a N jours (JJ/MM) »."""
    if not posted_at:
        return None
    try:
        s = posted_at.replace("Z", "+00:00")
        d = _dt.datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        now = _dt.datetime.now(_dt.timezone.utc)
        days = (now.date() - d.date()).days
        date_str = d.strftime("%d/%m/%Y")
        if days <= 0:
            return f"aujourd'hui ({date_str})"
        if days == 1:
            return f"hier ({date_str})"
        return f"il y a {days} jours ({date_str})"
    except (ValueError, TypeError):
        return None


def _offer_text(row: dict) -> str:
    score = row.get("fit_score")
    title = html.escape((row.get("title") or "Offre")[:250])
    company = html.escape(bot_core.display_company(row)[:100])
    lines = [f"<b>{title}</b>", f"🏢 {company}   ·   ⭐ <b>{score}/10</b>"]
    if row.get("location"):
        lines.append(f"📍 {html.escape(str(row['location'])[:100])}")
    age = format_age_fr(row.get("posted_at"))
    if age:
        if "aujourd'hui" in age:
            lines.append(f"🔥 <b>Parue {age}</b>")
        else:
            lines.append(f"📅 {age}")
    source = bot_core.display_source(row)
    domaine = bot_core.display_domaines(row)
    meta = []
    if source:
        meta.append(f"🔎 {html.escape(source)}")
    if domaine:
        meta.append(f"🏷 {html.escape(domaine)}")
    if meta:
        lines.append("   ·   ".join(meta))
    reason = (row.get("score_reasoning") or "").strip()
    if reason:
        lines.append("")
        lines.append(f"<i>{html.escape(reason[:700])}</i>")
    link = row.get("application_url") or row.get("url")
    if link:
        lines.append("")
        lines.append(f'<a href="{html.escape(link)}">Voir l\'offre</a>')
    has_cv = bool(row.get("tailored_resume_path"))
    has_cl = bool(row.get("cover_letter_path"))
    lines.append("")
    lines.append(f"CV adapté : {'oui' if has_cv else 'à générer'} · "
                 f"Lettre : {'oui' if has_cl else 'à générer'}")
    return "\n".join(lines)


def _offer_keyboard(h: str, done: bool = False) -> InlineKeyboardMarkup:
    if done:
        return InlineKeyboardMarkup([[InlineKeyboardButton("— décision enregistrée —",
                                                            callback_data="noop")]])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📨 Préparer ma candidature", callback_data=f"prep:{h}")],
        [
            InlineKeyboardButton("✅ J'ai postulé", callback_data=f"ok:{h}"),
            InlineKeyboardButton("✏️ Modifier le CV", callback_data=f"edit:{h}"),
            InlineKeyboardButton("❌ Ignorer", callback_data=f"no:{h}"),
        ],
    ])


async def _send_offer(bot, chat_id: int, row: dict) -> None:
    h = bot_core.remember(row["url"])
    msg = await bot.send_message(chat_id, _offer_text(row),
                                 parse_mode=ParseMode.HTML,
                                 disable_web_page_preview=True,
                                 reply_markup=_offer_keyboard(h))
    bot_core.set_card_message(row["url"], msg.message_id)  # pour suppression auto
    for path in bot_core.attachment_paths(row):
        try:
            with open(path, "rb") as fh:
                await bot.send_document(chat_id, InputFile(fh, filename=path.name))
        except OSError as exc:
            log.warning("Pièce jointe %s illisible : %s", path, exc)


# ---------------------------------------------------------------------------
# Logique partagée recherche + post
# ---------------------------------------------------------------------------
KNOWN_CITIES = {
    "paris", "bourges", "saint-doulchard", "saint doulchard", "lyon", "marseille",
    "bordeaux", "toulouse", "lille", "nantes", "rennes", "strasbourg",
    "montpellier", "nice", "orleans", "tours", "vierzon", "ile-de-france", "cher",
}


def _parse_search_args(args: list[str] | None, default_limit: int = MAX_CARDS) -> tuple[str | None, str | None, int]:
    """Parse les arguments pour /search ou /offres.
    Supporte:
      /search soc
      /search pentest Paris
      /search ad 10
      /search soc à Lyon 5
      /search Paris
      /offres grc
    Retourne (metier_query, city, limit).
    """
    if not args:
        return None, None, default_limit
    tokens = [a.strip() for a in args if a.strip()]
    limit = default_limit
    city: str | None = None
    metier: str | None = None

    if tokens and tokens[-1].isdigit():
        limit = min(20, max(1, int(tokens.pop())))

    if not tokens:
        return None, None, limit

    # Vérifier si le dernier mot est une ville connue
    if tokens and tokens[-1].lower() in KNOWN_CITIES:
        city = tokens.pop().lower()

    # Retirer les prépositions finales éventuelles ("à", "sur", "dans", "in")
    if tokens and tokens[-1].lower() in ("à", "a", "dans", "sur", "in", "ville", "de"):
        tokens.pop()

    joined = " ".join(tokens).strip()
    m_loc = re.search(r'\b(?:ville|a|à|dans|sur|in)\s+([A-Za-zÀ-ÿ\- ]+)', joined, re.I)
    if m_loc and not city:
        city = m_loc.group(1).strip()
        joined = (joined[:m_loc.start()] + " " + joined[m_loc.end():]).strip()
        tokens = joined.split()

    remaining = " ".join(tokens).strip().lower() if tokens else ""
    if remaining:
        if remaining in KNOWN_CITIES and not city:
            city = remaining
        else:
            metier = remaining

    return metier, city, limit


def _parse_offres_args(args: list[str] | None, default_limit: int = MAX_CARDS) -> tuple[str | None, int]:
    metier, city, limit = _parse_search_args(args, default_limit)
    return (city or metier), limit


async def _run_search_and_post(bot, chat_id: int, limit: int, do_search: bool = True,
                               city: str | None = None, metier: str | None = None) -> int:
    if do_search:
        from applypilot import pipeline
        ville_txt = f" à {city}" if city else ""
        metier_txt = f" ciblée sur <b>{html.escape(metier.upper())}</b>" if metier else ""
        await bot.send_message(chat_id, f"🔎 Recherche Indeed{metier_txt} en cours{ville_txt}…", parse_mode=ParseMode.HTML)
        try:
            queries_override = None
            if metier:
                from applypilot.discovery import indeed as _ind
                m_lower = metier.lower()
                for dom_name, q_list in _ind.QUERIES.items():
                    if m_lower in dom_name.lower() or any(m_lower in q.lower() for q in q_list):
                        queries_override = {dom_name: q_list}
                        break
                if not queries_override:
                    queries_override = {f"Custom {metier}": [f"stage {metier}", f"alternance {metier}", metier]}

            await asyncio.to_thread(pipeline._run_discover, 1, ["indeed"], queries=queries_override, location=city)
            await asyncio.to_thread(pipeline._run_score)
        except Exception as exc:
            log.exception("Pipeline échoué : %s", exc)
            await bot.send_message(chat_id, f"⚠️ Recherche partielle : {exc}")

    offers = await asyncio.to_thread(
        bot_core.fetch_pending_offers, MIN_SCORE, limit, ("indeed_scrape",), None, city, metier)
    if not offers and metier:
        offers = await asyncio.to_thread(
            bot_core.fetch_pending_offers, 7, limit, ("indeed_scrape",), None, city, metier)

    metier_label = f" <b>{html.escape(metier.upper())}</b>" if metier else ""
    ville_label = f" à <b>{html.escape(city)}</b>" if city else ""

    if not offers:
        aide_txt = (
            f"\n\n💡 <i>Astuce : tu peux élargir avec <code>/search {html.escape(metier)}</code> "
            f"ou tester <code>/offres</code> sans filtre.</i>" if metier else ""
        )
        await bot.send_message(
            chat_id,
            f"Aucune offre Indeed{metier_label}{ville_label} récente (< 7j) à valider pour l'instant.{aide_txt}",
            parse_mode=ParseMode.HTML)
        return 0

    await bot.send_message(
        chat_id, f"📋 {len(offers)} offre(s) Indeed{metier_label}{ville_label} à examiner :",
        parse_mode=ParseMode.HTML)
    for row in offers:
        await _send_offer(bot, chat_id, row)
    return len(offers)


# ---------------------------------------------------------------------------
# Commandes
# ---------------------------------------------------------------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)
    await update.message.reply_text(
        "👋 <b>ApplyPilot est prêt.</b>\n\n"
        "Tape <b>/commandes</b> pour voir la liste complète et détaillée de toutes les commandes !\n\n"
        "• <code>/scan [métier] [ville]</code> — scanner en temps réel les sites pour dénicher les offres inédites\n"
        "• <code>/search [métier] [ville]</code> — chercher sur Indeed (ex: <code>/search soc</code>, <code>/search pentest Paris</code>)\n"
        "• <code>/offres [métier] [ville]</code> — voir les offres en base (ex: <code>/offres soc</code>, <code>/offres ad</code>)\n"
        "• <code>/job</code> — jobs étudiants Bourges & Saint-Doulchard\n"
        "• <code>/track</code> — scanner les réponses recruteurs sur Gmail\n"
        "• <code>/status</code> — état du pipeline et de la veille auto\n\n"
        "Sur chaque offre : ✅ Valider · ✏️ Modifier · ❌ Ignorer.\n"
        "Je ne soumets jamais rien sans ton ✅.",
        parse_mode=ParseMode.HTML,
    )


async def commandes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Affiche la liste complète et détaillée de toutes les commandes disponibles."""
    if not _authorized(update):
        return await _deny(update)

    msg = (
        "🤖 <b>Toutes les commandes d'ApplyPilot</b>\n\n"
        "🎯 <b>Recherche & Offres Cybersécurité</b>\n"
        "• <code>/scan [métier] [ville] [limite]</code> — ⚡ Scan en temps réel des sites (HelloWork, Indeed) : ignore tout ce qui est déjà en base et diffuse les toutes dernières offres en direct !\n"
        "• <code>/search [métier] [ville] [limite]</code> — Recherche ciblée en direct sur Indeed + scoring IA (ex : <code>/search soc</code>, <code>/search pentest Paris</code>, <code>/search ad 10</code>)\n"
        "• <code>/offres [métier] [ville] [limite]</code> — Affiche les offres fraîches (≤ 7 jours, aujourd'hui en tête) prêtes en base (ex : <code>/offres soc</code>, <code>/offres Paris</code>)\n\n"
        "💼 <b>Jobs Étudiants & Intérim</b>\n"
        "• <code>/job [ville]</code> — Recherche live de jobs étudiants, temps partiel et intérim (par défaut Bourges & Saint-Doulchard)\n\n"
        "📬 <b>Suivi Recruteurs & Boîte Gmail</b>\n"
        "• <code>/track [jours]</code> — Analyse ta boîte Gmail pour détecter et classifier les réponses recruteurs (défaut : 7 jours)\n"
        "• <code>/confirmees</code> — Affiche le récapitulatif détaillé des candidatures confirmées et en cours\n"
        "• <code>/refus</code> — Liste les refus reçus avec entreprises, postes et dates\n"
        "• <code>/trackers</code> — Suivi des ouvertures de tes CV par les recruteurs (liens traceurs cliqués)\n\n"
        "⚙️ <b>Gestion & Contrôle du Bot</b>\n"
        "• <code>/status</code> — Bilan global du pipeline (offres en base, scores, veille)\n"
        "• <code>/pause</code> — Met en pause la veille automatique (toutes les 10 min)\n"
        "• <code>/resume</code> — Réactive la veille automatique\n"
        "• <code>/clean</code> — Supprime les messages récents du bot (moins de 48h) dans cette discussion\n"
        "• <code>/done</code> — Marque la dernière offre préparée comme postulée\n"
        "• <code>/commandes</code> — Affiche ce menu d'aide complet\n\n"
        "💡 <i>Sur chaque offre présentée, clique sur ✅ Valider, ✏️ Modifier ou ❌ Ignorer. Rien n'est envoyé sans ton accord !</i>"
    )
    try:
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    except Exception:
        # Fallback sans HTML si erreur de parsing Telegram
        import re as _re
        plain_msg = _re.sub(r'<[^>]+>', '', msg)
        await update.message.reply_text(plain_msg)


async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scanne en temps réel les sites d'offres (HelloWork, Indeed), filtre tout ce qui est
    déjà présent en base, et diffuse immédiatement les toutes dernières offres inédites."""
    if not _authorized(update):
        return await _deny(update)

    metier, city, limit = _parse_search_args(context.args, MAX_CARDS)
    chat_id = update.effective_chat.id

    metier_label = f" ciblée sur <b>{html.escape(metier.upper())}</b>" if metier else ""
    ville_label = f" à <b>{html.escape(city)}</b>" if city else ""

    status_msg = await update.message.reply_text(
        f"⚡ <b>Scan en temps réel des sites d'offres{metier_label}{ville_label}…</b>\n\n"
        f"📡 <i>Interrogation en direct de HelloWork et Indeed pour dénicher les offres les plus fraîches qui ne sont <b>pas encore</b> dans ta base…</i>",
        parse_mode=ParseMode.HTML,
    )

    async def _keep_typing():
        while True:
            try:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            except Exception:
                pass
            await asyncio.sleep(4)

    typing_task = asyncio.create_task(_keep_typing())

    try:
        existing_urls = await asyncio.to_thread(bot_core.get_all_job_urls)

        from applypilot import pipeline
        queries_indeed = None
        queries_hw = None
        if metier:
            from applypilot.discovery import indeed as _ind
            m_lower = metier.lower()
            for dom_name, q_list in _ind.QUERIES.items():
                if m_lower in dom_name.lower() or any(m_lower in q.lower() for q in q_list):
                    queries_indeed = {dom_name: q_list}
                    break
            if not queries_indeed:
                queries_indeed = {f"Scan {metier}": [f"stage {metier}", f"alternance {metier}", metier]}
            queries_hw = [f"stage {metier}", f"alternance {metier}", metier]

        def _do_live_scan():
            pipeline._run_discover(
                workers=1,
                sources=["hellowork", "indeed"],
                queries=queries_indeed or queries_hw,
                location=city,
            )
            all_urls = bot_core.get_all_job_urls()
            new_urls = list(all_urls - existing_urls)
            if new_urls:
                pipeline._run_score()
            return new_urls

        newly_found_urls = await asyncio.to_thread(_do_live_scan)

        if not newly_found_urls:
            metier_hint = f" {html.escape(metier)}" if metier else ""
            await status_msg.edit_text(
                f"✅ <b>Scan en direct terminé</b> : Aucune nouvelle offre détectée sur les sites (toutes les offres en ligne font déjà partie de ta base de données).\n\n"
                f"💡 <i>Pour voir les offres fraîches déjà qualifiées en base, tape <code>/offres{metier_hint}</code> !</i>",
                parse_mode=ParseMode.HTML,
            )
            return

        offers = await asyncio.to_thread(
            bot_core.fetch_new_scan_offers,
            newly_found_urls,
            MIN_SCORE,
            limit,
            city,
            metier,
        )
        if not offers and metier:
            offers = await asyncio.to_thread(
                bot_core.fetch_new_scan_offers,
                newly_found_urls,
                4,
                limit,
                city,
                metier,
            )

        if not offers:
            await status_msg.edit_text(
                f"📊 <b>Scan en direct terminé</b> : <b>{len(newly_found_urls)}</b> nouvelle(s) offre(s) scannée(s) sur le web, "
                f"mais aucune ne correspondait suffisamment à tes critères de stage (score trop bas ou hors-sujet).\n\n"
                f"💡 <i>Toutes ces offres ont été enregistrées en base pour l'historique.</i>",
                parse_mode=ParseMode.HTML,
            )
            return

        await status_msg.edit_text(
            f"🆕 <b>{len(offers)} nouvelle(s) offre(s) inédite(s) trouvée(s) en direct !</b> Envoi en cours…",
            parse_mode=ParseMode.HTML,
        )

        for row in offers:
            await _send_offer(context.bot, chat_id, row)

        await asyncio.to_thread(bot_core.mark_notified, [o["url"] for o in offers])

        await context.bot.send_message(
            chat_id,
            f"🎯 <b>Scan terminé</b> : {len(offers)} nouvelle(s) offre(s) fraîche(s) envoyée(s) en direct !",
            parse_mode=ParseMode.HTML,
        )

    except Exception as exc:
        log.exception("Erreur lors du scan en temps réel : %s", exc)
        await status_msg.edit_text(f"⚠️ Erreur lors du scan : {html.escape(str(exc))}", parse_mode=ParseMode.HTML)
    finally:
        typing_task.cancel()


async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)
    metier, city, limit = _parse_search_args(context.args, MAX_CARDS)
    n = await _run_search_and_post(context.bot, update.effective_chat.id, limit, do_search=True, city=city, metier=metier)
    cible_txt = f" ({metier.upper()})" if metier else ""
    ville_txt = f" à {city}" if city else ""
    await context.bot.send_message(update.effective_chat.id,
                                   f"Terminé — {n} offre(s) présentée(s){cible_txt}{ville_txt}.")


async def offres_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)
    metier, city, limit = _parse_search_args(context.args, MAX_CARDS)
    n = await _run_search_and_post(context.bot, update.effective_chat.id, limit, do_search=False, city=city, metier=metier)
    cible_txt = f" ({metier.upper()})" if metier else ""
    ville_txt = f" à {city}" if city else ""
    await context.bot.send_message(update.effective_chat.id, f"{n} offre(s) affichée(s){cible_txt}{ville_txt}.")


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)
    stats = await asyncio.to_thread(get_stats)
    pending = await asyncio.to_thread(bot_core.fetch_pending_offers, MIN_SCORE, 9999)
    auto = context.application.bot_data.get("auto_search", True)
    dist = stats.get("score_distribution") or []
    dist_txt = "\n".join(f"  {s}/10 : {c}" for s, c in dist[:6]) or "  (aucune)"
    msg = (
        "📊 <b>ApplyPilot — état</b>\n"
        f"Offres en base : <b>{stats.get('total', 0)}</b>\n"
        f"Scorées : <b>{stats.get('scored', 0)}</b>\n"
        f"Candidatées : <b>{stats.get('applied', 0)}</b>\n"
        f"À valider (≥{MIN_SCORE}) : <b>{len(pending)}</b>\n"
        f"Recherche auto : <b>{'activée ✅' if auto else 'en pause ⏸'}</b>\n\n"
        f"<b>Distribution des scores</b>\n{dist_txt}"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


async def pause_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)
    context.application.bot_data["auto_search"] = False
    await update.message.reply_text("⏸ Recherche auto en pause.")


async def resume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)
    context.application.bot_data["auto_search"] = True
    await update.message.reply_text("▶️ Recherche auto réactivée.")


async def clean_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Supprime les messages récents de la discussion (le bot peut effacer ses
    propres messages ET les tiens en chat privé, ceux de moins de 48 h).
    Usage : /clean (200 derniers) ou /clean 500."""
    if not _authorized(update):
        return await _deny(update)
    chat_id = update.effective_chat.id
    last = update.message.message_id
    n = 200
    if context.args and context.args[0].isdigit():
        n = min(int(context.args[0]), 1000)
    deleted = 0
    for mid in range(last, max(last - n, 1), -1):
        try:
            await context.bot.delete_message(chat_id, mid)
            deleted += 1
        except Exception:
            pass  # message trop vieux (>48 h), déjà supprimé, ou inexistant
        await asyncio.sleep(0.03)  # évite le flood-limit Telegram
    try:
        await context.bot.send_message(
            chat_id, f"🧹 Discussion nettoyée — {deleted} message(s) supprimé(s). "
                     "(Telegram empêche de supprimer les messages de plus de 48 h.)")
    except Exception:
        pass


async def _send_job_list(bot, chat_id: int, titre: str, jobs: list[dict]) -> None:
    """Envoie chaque job (étudiant / intérim) en message séparé AVEC un bouton
    « 🚀 Postuler » (pré-remplit le formulaire + CV job étudiant sur le PC)."""
    if not jobs:
        await bot.send_message(chat_id, f"{titre} : aucune offre de moins d'une semaine trouvée.")
        return
    await bot.send_message(chat_id, f"<b>{html.escape(titre)}</b> — {len(jobs)} offre(s)",
                           parse_mode=ParseMode.HTML)
    for j in jobs:
        h = bot_core.remember(j["url"])
        t = html.escape((j.get("title") or "")[:90])
        comp = html.escape((j.get("employer_name") or "")[:40])
        loc = html.escape((j.get("location") or "")[:40])
        txt = f"<b>{t}</b>\n{comp}" + (f" · {loc}" if loc else "")
        extra = []
        age = format_age_fr(j.get("posted_at"))
        if age:
            if "aujourd'hui" in age:
                extra.append(f"🔥 <b>{age}</b>")
            else:
                extra.append(f"📅 {age}")
        if j.get("week_end"):
            extra.append("🗓️ week-end")
        if extra:
            txt += "\n" + " · ".join(extra)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🚀 Postuler", callback_data=f"jobapply:{h}"),
            InlineKeyboardButton("Voir l'offre", url=j["url"]),
        ]])
        await bot.send_message(chat_id, txt, parse_mode=ParseMode.HTML,
                               reply_markup=kb, disable_web_page_preview=True)
        await asyncio.sleep(0.25)


def _merge_student_jobs(*lists: list[dict]) -> list[dict]:
    """Fusionne plusieurs listes de jobs (HelloWork + Indeed), dédupliquées par
    URL puis par (titre, entreprise, lieu) normalisés — l'ordre des listes
    décide du gagnant en cas de doublon."""
    seen_url: set[str] = set()
    seen_key: set[tuple] = set()
    out: list[dict] = []
    for lst in lists:
        for j in lst or []:
            u = j.get("url")
            if u and u in seen_url:
                continue
            key = (bot_core._norm_key(j.get("title")),
                   bot_core._norm_key(j.get("employer_name")),
                   bot_core._norm_key(j.get("location")))
            if key in seen_key:
                continue
            if u:
                seen_url.add(u)
            seen_key.add(key)
            out.append(j)
    return out


async def done_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Marque comme POSTULÉE la dernière offre pour laquelle tu as cliqué
    « 📨 Préparer ma candidature »."""
    if not _authorized(update):
        return await _deny(update)
    url = bot_core.get_last_prepared()
    if not url:
        await update.message.reply_text(
            "Aucune candidature récente à marquer. Clique d'abord "
            "« 📨 Préparer ma candidature » sur une offre.")
        return
    row = bot_core.fetch_one(url) or {}
    bot_core.set_apply_status(url, "applied")
    # Supprime la carte de l'offre déjà affichée dans le fil.
    mid = bot_core.get_card_message(url)
    if mid:
        try:
            await context.bot.delete_message(update.effective_chat.id, mid)
        except Exception:
            pass
    titre = (row.get("title") or "l'offre")[:80]
    await update.message.reply_text(
        f"✅ Marquée « postulée » : {titre}. Bravo 🎉")


async def trackers_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Affiche les statistiques et dernières consultations de CV et profils (Portfolio, LinkedIn, GitHub)."""
    if not _authorized(update):
        return await _deny(update)
    from applypilot.database import get_recent_trackers, get_tracker_stats
    from applypilot.tracking.tracer import parse_user_agent
    stats = get_tracker_stats()
    by_type = stats.get("by_type", {})
    recent = get_recent_trackers(15)
    lines = [
        "📊 <b>Suivi des consultations (Tracer Links)</b>",
        f"• Total liens tracés : <b>{stats['total_trackers']}</b>",
        f"• Liens ouverts par un recruteur : <b>{stats['opened_trackers']}</b>",
        f"• Total clics / consultations : <b>{stats['total_opens']}</b>",
        "",
        "🎯 <b>Détail des ouvertures :</b>",
        f"  🌐 Portfolio : <b>{by_type.get('portfolio', 0)}</b> consultation(s)",
        f"  💼 LinkedIn : <b>{by_type.get('linkedin', 0)}</b> consultation(s)",
        f"  🐙 GitHub : <b>{by_type.get('github', 0)}</b> consultation(s)",
        "",
        f"• Serveur traceur : <code>{config.TRACER_BASE_URL}</code>",
        "",
    ]
    if not recent:
        lines.append("<i>Aucun traceur enregistré pour le moment.</i>")
    else:
        lines.append("<b>Dernières consultations :</b>")
        for t in recent:
            comp = html.escape(t.get("company") or "Entreprise")
            tit = html.escape((t.get("title") or "Offre")[:32])
            ltype = t.get("link_type", "portfolio")
            badge = {
                "linkedin": "💼 LinkedIn",
                "github": "🐙 GitHub",
                "portfolio": "🌐 Portfolio",
            }.get(ltype, f"🔗 {ltype.capitalize()}")
            cnt = t.get("open_count") or 0
            if cnt > 0:
                dev = html.escape(parse_user_agent(t.get("user_agent")))
                dt = t.get("last_opened_at") or t.get("opened_at") or ""
                dt_str = dt[:16].replace("T", " ") if dt else ""
                lines.append(
                    f"🟢 <b>{comp}</b> — {tit} [{badge}]\n"
                    f"   ↳ <b>{cnt} clic(s)</b> (Dernier : {dt_str} via {dev})"
                )
            else:
                lines.append(f"⚪ <b>{comp}</b> — {tit} [{badge}]\n   ↳ <i>Pas encore ouvert</i>")

    lines.append("")
    lines.append("🔗 <b>Tes liens permanents traqués :</b>")
    lines.append(f"• 💼 LinkedIn : <code>{config.TRACER_BASE_URL}/t/linkedin</code>")
    lines.append(f"• 🐙 GitHub : <code>{config.TRACER_BASE_URL}/t/github</code>")
    lines.append(f"• 🌐 Portfolio : <code>{config.TRACER_BASE_URL}/t/portfolio</code>")

    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

def _format_tracking_event(ev: dict) -> str:
    """Formate un événement de réponse recruteur pour Telegram."""
    etype = ev.get("type")
    comp = html.escape(ev.get("company") or "Entreprise")
    tit = html.escape(ev.get("title") or "Candidature")
    subj = html.escape(ev.get("subject") or "")
    summary = html.escape(ev.get("summary") or "")

    if etype == "interview":
        lines = [
            "🎉 <b>INVITATION EN ENTRETIEN !</b>",
            "",
            f"🏢 <b>Entreprise :</b> {comp}",
            f"💼 <b>Poste :</b> {tit}",
            f"📬 <b>Objet :</b> {subj}",
        ]
        contact = ev.get("sender_name") or ev.get("sender")
        if contact:
            lines.append(f"👤 <b>Contact :</b> {html.escape(contact)}")
        dates = ev.get("dates") or []
        if dates:
            d_parts = [f"{d.get('date', '')} ({d.get('description', '')})".strip() for d in dates if d.get("date")]
            if d_parts:
                lines.append(f"📅 <b>Date(s) :</b> {html.escape(', '.join(d_parts))}")
        actions = ev.get("action_items") or []
        if actions:
            a_parts = [a.get("task", "") for a in actions if a.get("task")]
            if a_parts:
                lines.append(f"⚡ <b>Action requise :</b> {html.escape('; '.join(a_parts))}")
        if summary:
            lines.append(f"\n💬 <i>{summary}</i>")
        return "\n".join(lines)

    elif etype == "offer":
        lines = [
            "🥳 <b>OFFRE DE STAGE OU D'EMBAUCHE !</b>",
            "",
            f"🏢 <b>Entreprise :</b> {comp}",
            f"💼 <b>Poste :</b> {tit}",
            f"📬 <b>Objet :</b> {subj}",
        ]
        if summary:
            lines.append(f"\n💬 <i>{summary}</i>")
        return "\n".join(lines)

    elif etype == "rejection":
        lines = [
            "❌ <b>Réponse négative reçue</b>",
            "",
            f"🏢 <b>Entreprise :</b> {comp}",
            f"💼 <b>Poste :</b> {tit}",
        ]
        if summary:
            lines.append(f"💬 <i>{summary}</i>")
        return "\n".join(lines)

    elif etype == "confirmation":
        return (
            f"📨 <b>Candidature bien enregistrée</b>\n\n"
            f"🏢 <b>{comp}</b> — {tit}\n"
            f"📬 <i>{subj}</i>"
        )

    elif etype == "ghosted":
        return (
            f"👻 <b>Candidature sans réponse (Ghosting)</b>\n\n"
            f"🏢 <b>{comp}</b> — {tit}\n"
            f"⏳ <i>Plus de 7 jours sans retour depuis l'envoi.</i>"
        )

    return ""


async def track_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Vérifie la boîte Gmail et classifie les réponses des recruteurs."""
    if not _authorized(update):
        return await _deny(update)

    from applypilot.tracking.gmail_client import check_gmail_setup
    ok, msg = check_gmail_setup()
    if not ok:
        await update.effective_message.reply_text(
            f"⚠️ <b>Suivi Gmail non configuré</b>\n\n"
            f"{html.escape(msg)}",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return

    days = 7
    if context.args and context.args[0].isdigit():
        days = min(60, max(1, int(context.args[0])))

    chat_id = update.effective_chat.id
    status_msg = await context.bot.send_message(
        chat_id, f"📬 <i>Analyse des {days} derniers jours sur ta boîte Gmail…</i>",
        parse_mode=ParseMode.HTML,
    )

    from applypilot.tracking import run_tracking
    from applypilot.database import get_tracking_stats

    # Feedback visuel continu pendant l'analyse Gmail
    async def _keep_typing():
        while True:
            try:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            except Exception:
                pass
            await asyncio.sleep(4)

    typing_task = asyncio.create_task(_keep_typing())

    try:
        res = await asyncio.to_thread(run_tracking, days=days, limit=30, ghosted_days=7, dry_run=False)
    except Exception as exc:
        log.exception("Erreur lors du suivi Gmail : %s", exc)
        try:
            await status_msg.edit_text(f"❌ Erreur lors de l'analyse Gmail : {html.escape(str(exc))}")
        except Exception:
            await context.bot.send_message(chat_id, f"❌ Erreur lors de l'analyse Gmail : {html.escape(str(exc))}")
        return
    finally:
        typing_task.cancel()

    fetched = res.get("fetched", 0)
    matched = res.get("matched", 0)
    stubs = res.get("stubs", 0)
    classified = res.get("classified", 0)
    savings = int(res.get("triage_savings_pct", 0))
    new_events = res.get("new_events", [])

    stats = get_tracking_stats()
    lines = [
        "📬 <b>Rapport du suivi des candidatures (Gmail)</b>",
        "",
        f"• Emails analysés : <b>{fetched}</b>",
        f"• Nouvelles réponses classifiées : <b>{classified}</b>",
        f"• Rapprochées à tes offres : <b>{matched}</b>" + (f" (+{stubs} hors pipeline)" if stubs else ""),
        f"• Économie LLM (triage instantané) : <b>{savings}%</b>",
        "",
        "📊 <b>Bilan global de tes candidatures :</b>",
        f"🎉 Entretiens : <b>{stats.get('interview', 0)}</b>",
        f"📨 Confirmées / En cours : <b>{stats.get('confirmation', 0)}</b>",
        f"❌ Refus : <b>{stats.get('rejection', 0)}</b>",
        f"👻 Sans réponse (Ghosting) : <b>{stats.get('ghosted', 0)}</b>",
    ]
    if stats.get('offer', 0):
        lines.append(f"🥳 Offres reçues : <b>{stats.get('offer', 0)}</b>")

    report_text = "\n".join(lines)
    try:
        await status_msg.edit_text(report_text, parse_mode=ParseMode.HTML)
    except Exception:
        await context.bot.send_message(chat_id, report_text, parse_mode=ParseMode.HTML)

    # Notification sonore/vibration sur mobile
    if not new_events:
        await context.bot.send_message(
            chat_id,
            f"✅ <b>Suivi Gmail terminé</b> : {fetched} email(s) analysés (économies triage : {savings}%), tout est à jour !",
            parse_mode=ParseMode.HTML,
        )

    for ev in new_events:
        card = _format_tracking_event(ev)
        if card:
            await context.bot.send_message(chat_id, card, parse_mode=ParseMode.HTML)


def _format_date_fr(raw: str | None) -> str:
    """Formate une date RFC 2822 ou ISO en format lisible en français."""
    if not raw:
        return "Date inconnue"
    import email.utils
    try:
        dt = email.utils.parsedate_to_datetime(raw)
        return dt.strftime("%d/%m/%Y à %H:%M")
    except Exception:
        pass
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.strftime("%d/%m/%Y à %H:%M")
    except Exception:
        return str(raw)[:16]


async def _send_classified_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    classification: str,
    title_emoji: str,
    title_text: str,
) -> None:
    """Envoie la liste détaillée des emails classifiés (confirmations ou refus)."""
    if not _authorized(update):
        return await _deny(update)

    import json
    import re
    from applypilot.database import get_classified_emails
    from applypilot.tracking.matcher import extract_company_from_subject

    emails = get_classified_emails(classification, limit=20)
    chat_id = update.effective_chat.id

    if not emails:
        empty_msg = {
            "confirmation": "📬 <i>Aucune candidature confirmée trouvée dans tes emails récents.</i>",
            "rejection": "✨ <i>Aucun refus trouvé dans tes emails récents !</i>",
        }.get(classification, "<i>Aucun email trouvé.</i>")
        await update.effective_message.reply_text(empty_msg, parse_mode=ParseMode.HTML)
        return

    cards = [f"{title_emoji} <b>{title_text} ({len(emails)})</b>\n"]

    for i, e in enumerate(emails, 1):
        subj = e.get("subject") or "Sans objet"
        comp = (
            e.get("company")
            or extract_company_from_subject(subj)
            or e.get("sender_name")
            or e.get("sender")
            or "Entreprise"
        )
        job_title = e.get("title") or ""
        date_str = _format_date_fr(e.get("received_at"))

        summary = ""
        if e.get("extracted_data"):
            try:
                data = json.loads(e["extracted_data"])
                summary = data.get("summary", "")
            except Exception:
                pass
        if not summary and e.get("snippet"):
            summary = e["snippet"]

        clean_comp = html.escape(comp)
        clean_subj = html.escape(subj)
        clean_title = html.escape(job_title)

        entry_lines = [
            f"<b>{i}. {clean_comp}</b>" + (f" — {clean_title}" if clean_title and clean_title != clean_subj else ""),
            f"📅 <b>Date :</b> {date_str}",
            f"📬 <b>Objet :</b> {clean_subj}",
        ]
        if e.get("sender_name") or e.get("sender"):
            contact = html.escape(e.get("sender_name") or e.get("sender"))
            entry_lines.append(f"👤 <b>Expéditeur :</b> {contact}")
        if summary:
            clean_summary = html.escape(re.sub(r"\s+", " ", summary).strip()[:200])
            entry_lines.append(f"💬 <i>{clean_summary}</i>")

        cards.append("\n".join(entry_lines))

    full_text = "\n\n───────────────\n\n".join(cards)
    if len(full_text) < 4000:
        await update.effective_message.reply_text(
            full_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
    else:
        header = cards[0]
        current_chunk = [header]
        current_len = len(header)
        for item in cards[1:]:
            if current_len + len(item) + 20 > 3800:
                await context.bot.send_message(
                    chat_id, "\n\n───────────────\n\n".join(current_chunk),
                    parse_mode=ParseMode.HTML, disable_web_page_preview=True
                )
                current_chunk = []
                current_len = 0
            current_chunk.append(item)
            current_len += len(item) + 20
        if current_chunk:
            await context.bot.send_message(
                chat_id, "\n\n───────────────\n\n".join(current_chunk),
                parse_mode=ParseMode.HTML, disable_web_page_preview=True
            )


async def confirmee_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Affiche le détail des candidatures confirmées / accusés de réception."""
    await _send_classified_report(
        update, context, "confirmation", "📨", "Candidatures confirmées / En cours"
    )


async def refus_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Affiche le détail et dates des réponses négatives reçues."""
    await _send_classified_report(
        update, context, "rejection", "❌", "Refus reçus"
    )


DEFAULT_JOB_LOCATIONS = ["Bourges", "Saint-Doulchard"]
DEFAULT_JOB_DISPLAY = "Bourges & Saint-Doulchard"


async def job_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Jobs étudiants + missions intérim en direct depuis Indeed.
    Défaut : Bourges et Saint-Doulchard.
    Usage : /job  ou  /job Orléans"""
    if not _authorized(update):
        return await _deny(update)

    raw_arg = " ".join(context.args).strip() if context.args else ""
    norm_arg = raw_arg.lower()

    if not raw_arg or any(k in norm_arg for k in ("bourges", "saint-doulchard", "saint doulchard", "doulchard")):
        villes = list(DEFAULT_JOB_LOCATIONS)
        display_ville = DEFAULT_JOB_DISPLAY
    else:
        villes = [raw_arg]
        display_ville = raw_arg

    chat_id = update.effective_chat.id
    try:
        await context.bot.send_message(
            chat_id, f"🔎 Recherche jobs étudiants + missions intérim Indeed à {html.escape(display_ville)}…")
    except Exception as exc:
        log.warning("Notification initiale job_cmd : %s", exc)

    from applypilot.discovery import indeed_student

    # Recherche Indeed sur chaque secteur (Bourges et Saint-Doulchard par défaut)
    etu_lists = []
    inter_lists = []
    for v in villes:
        try:
            res_etu = await asyncio.to_thread(indeed_student.search_student_jobs, v, 10)
            if res_etu:
                etu_lists.append(res_etu)
        except Exception as exc:
            log.warning("Jobs étudiants Indeed échoués pour %s : %s", v, exc)

        try:
            res_inter = await asyncio.to_thread(indeed_student.search_interim_jobs, v, 10)
            if res_inter:
                inter_lists.append(res_inter)
        except Exception as exc:
            log.warning("Missions intérim Indeed échouées pour %s : %s", v, exc)

    # Fusion, déduplication et tri strict (<= 7 jours, sorties le jour même en tête)
    etu = indeed_student.sort_offers(_merge_student_jobs(*etu_lists), sort="date", max_age_days=7)
    inter = indeed_student.sort_offers(_merge_student_jobs(*inter_lists), sort="date", max_age_days=7)

    try:
        await _send_job_list(context.bot, chat_id, f"🎓 Jobs étudiants (Indeed) — {display_ville}", etu[:10])
        await _send_job_list(context.bot, chat_id, f"🛠️ Missions intérim (Indeed) — {display_ville}", inter[:10])
    except Exception as exc:
        log.warning("Envoi job_list échoué : %s", exc)

    if not etu and not inter:
        try:
            await context.bot.send_message(
                chat_id,
                f"⚠️ Aucune offre récente (< 7 jours) trouvée sur Indeed pour <b>{html.escape(display_ville)}</b>.\n"
                "💡 <i>Astuce : tu peux chercher sur une plus grande ville à proximité, par exemple : "
                "<code>/job Orléans</code>, <code>/job Tours</code> ou <code>/job Paris</code>.</i>",
                parse_mode=ParseMode.HTML)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Boutons (✅ / ✏️ / ❌)
# ---------------------------------------------------------------------------
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)
    query = update.callback_query
    data = query.data or ""
    if data == "noop":
        return await query.answer()
    try:
        action, h = data.split(":", 1)
    except ValueError:
        return await query.answer()
    url = bot_core.resolve(h)
    if not url:
        return await query.answer("Offre introuvable (redémarrage ?).", show_alert=True)

    if action == "jobapply":
        # Job étudiant / intérim : CV fixe. Envoie le CV + le lien (téléphone) ET
        # ouvre le formulaire pré-rempli sur le PC si tu y es.
        await query.answer("C'est parti…")
        chat_id = query.message.chat_id
        if not JOB_CV.exists():
            await context.bot.send_message(
                chat_id, "⚠️ CV job étudiant introuvable (cv_job_etudiant.pdf). "
                         "Renvoie-le-moi et je le remets en place.")
            return
        # 1) Envoie le CV job étudiant (utile pour postuler depuis le téléphone).
        try:
            with open(JOB_CV, "rb") as fh:
                await context.bot.send_document(
                    chat_id, InputFile(fh, filename="CV_Ali_Jamai_Job_Etudiant.pdf"),
                    caption="📄 Ton CV job étudiant.")
        except Exception:
            pass
        # 2) Ouvre le formulaire pré-rempli sur le PC (si tu y es).
        opened = False
        try:
            subprocess.Popen(
                [sys.executable, "-m", "applypilot.autoapply", "--url", url,
                 "--cv", str(JOB_CV)], cwd=os.getcwd(),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            opened = True
        except Exception as exc:
            log.warning("jobapply lancement navigateur : %s", exc)
        parts = []
        if opened:
            _is_indeed_job = any(h in url for h in
                                 ("indeed.com", "indeed.fr", "fr.indeed.com"))
            if _is_indeed_job:
                parts.append(
                    "🖥️ <b>Indeed</b> : offre ouverte dans ton Chrome réel "
                    "(sans flag automation → pas de blocage Cloudflare). "
                    "Téléverse le CV ci-dessus dans le formulaire."
                )
            else:
                parts.append("🖥️ <b>Sur ton PC</b> : la fenêtre s'ouvre avec un bouton "
                             "<b>« 📝 Compléter le formulaire »</b> en haut — clique-le "
                             "pour remplir avec ton CV job étudiant.")
        parts.append(f'📱 <b>Sur ton téléphone</b> : ouvre l\'offre et postule à la '
                     f'main (CV ci-dessus) → <a href="{html.escape(url)}">lien de l\'offre</a>')
        parts.append("Connexion/CAPTCHA = à toi.")
        await context.bot.send_message(chat_id, "\n".join(parts),
                                       parse_mode=ParseMode.HTML,
                                       disable_web_page_preview=True)
        return

    if action == "prep":
        await query.answer("Préparation…")
        await context.bot.send_message(
            query.message.chat_id,
            "📨 Préparation de ta candidature : génération du CV adapté "
            "(tes expériences réelles, aucune invention)… ~10 s.")
        row = bot_core.fetch_one(url) or {}
        pdf = None
        try:
            from applypilot import cv_latex
            pdf = await asyncio.to_thread(cv_latex.generate_cv, row)
            bot_core.set_cv_path(url, str(pdf))
            with open(pdf, "rb") as fh:
                await context.bot.send_document(
                    query.message.chat_id, InputFile(fh, filename=pdf.name),
                    caption="📄 Ton CV adapté à cette offre (1 page).")
        except Exception as exc:
            log.exception("Génération CV échouée : %s", exc)
            await context.bot.send_message(
                query.message.chat_id, f"⚠️ Échec de la génération du CV : {exc}")
        link = row.get("application_url") or row.get("url") or ""
        bot_core.set_apply_status(url, "prepared")
        bot_core.set_last_prepared(url)  # pour /done

        # Ouvre le formulaire pré-rempli dans un navigateur VISIBLE sur le PC
        # (processus détaché : tu finis à la main — upload + clic Postuler).
        opened = False
        try:
            cmd = [sys.executable, "-m", "applypilot.autoapply", "--url", url]
            if pdf:
                cmd += ["--cv", str(pdf)]
            subprocess.Popen(cmd, cwd=os.getcwd(),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            opened = True
        except Exception as exc:
            log.warning("Lancement du navigateur échoué : %s", exc)

        msg = ["✅ <b>Candidature prête.</b>"]
        if opened:
            res_status = getattr(opened, "get", lambda k, d=None: None)
            # Adapter le message selon la source (Indeed → CDP = vrai Chrome + auto-complétion)
            _target = row.get("application_url") or row.get("url") or ""
            _is_indeed = any(h in _target for h in
                             ("indeed.com", "indeed.fr", "fr.indeed.com"))
            if _is_indeed:
                msg.append(
                    "🖥️ <b>Indeed (mode CDP)</b> : offre ouverte dans ton <b>vrai Chrome</b> "
                    "→ Cloudflare ne bloque pas. Clique le bouton "
                    "<b>« 📝 Compléter le formulaire »</b> en haut pour remplir "
                    "automatiquement les champs et téléverser le CV."
                )
            else:
                msg.append("🖥️ <b>Sur ton PC</b> : la fenêtre s'ouvre avec un bouton "
                           "<b>« 📝 Compléter le formulaire »</b> en haut. Connecte-toi "
                           "si besoin, clique-le pour tout remplir (champs + CV), "
                           "vérifie et clique Postuler.")
        msg.append(f'📱 <b>Sur ton téléphone</b> : ouvre l\'offre et postule à la '
                   f'main (le CV est juste au-dessus) → '
                   f'<a href="{html.escape(link)}">lien de l\'offre</a>')
        msg.append("Connexion / CAPTCHA = à toi. Ensuite tape /done.")
        await context.bot.send_message(
            query.message.chat_id, "\n".join(msg),
            parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        return

    if action == "ok":
        bot_core.set_apply_status(url, "applied")
        await query.answer("✅ Marquée postulée")
        try:
            await query.message.delete()  # retire la carte du fil
        except Exception:
            await query.edit_message_reply_markup(reply_markup=_offer_keyboard(h, done=True))

    elif action == "no":
        bot_core.set_apply_status(url, "ignored")
        await query.answer("❌ Ignorée")
        try:
            await query.message.delete()  # retire la carte du fil
        except Exception:
            await query.edit_message_reply_markup(reply_markup=_offer_keyboard(h, done=True))

    elif action == "edit":
        # Telegram n'a pas de modal : on attend le prochain message texte.
        context.user_data["awaiting_edit_for"] = url
        await query.answer()
        await context.bot.send_message(
            query.message.chat_id,
            "✏️ Réponds à ce message avec ta consigne "
            "(ex. « insister sur l'Active Directory, retirer tel projet »).")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Capture la consigne de modification saisie après le bouton ✏️."""
    if not _authorized(update):
        return
    url = context.user_data.pop("awaiting_edit_for", None)
    if not url:
        return  # texte libre hors contexte : on ignore.
    note = (update.message.text or "").strip()[:500]
    bot_core.set_apply_status(url, "edit_requested", note=note)
    await update.message.reply_text(
        f"✏️ Noté : « {note} »\nLe CV/lettre sera régénéré avec cette "
        "consigne au prochain passage.")


# ---------------------------------------------------------------------------
# Veille automatique : HelloWork à chaque cycle (CYCLE_MINUTES), Indeed toutes
# les INDEED_CYCLE_HOURS (plus lourd → cadence plus lente). Puis score, puis
# envoi des NOUVELLES offres seulement (sans re-spammer celles déjà envoyées).
# ---------------------------------------------------------------------------
def _sources_for_tick(tick: int, indeed_every: int) -> list[str]:
    """Sources à scraper pour ce cycle de veille. HelloWork à chaque cycle ;
    Indeed un cycle sur `indeed_every` (≈ toutes les 2 h par défaut)."""
    sources = ["hellowork"]
    if indeed_every <= 1 or tick % indeed_every == 0:
        sources.append("indeed")
    return sources


async def _auto_cycle(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.application.bot_data.get("auto_search", True):
        return
    if context.application.bot_data.get("_cycle_busy"):
        return  # un cycle précédent tourne encore
    context.application.bot_data["_cycle_busy"] = True
    # Compteur de cycles (persiste tant que le bot tourne) : décide quand Indeed
    # est inclus. Incrémenté tout de suite pour ne pas rester bloqué sur Indeed.
    tick = int(context.application.bot_data.get("_tick", 0))
    context.application.bot_data["_tick"] = tick + 1
    try:
        from applypilot import pipeline
        sources = _sources_for_tick(tick, _INDEED_EVERY_TICKS)
        try:
            # _run_discover isole chaque source dans son propre try/except :
            # une erreur Indeed ne casse pas HelloWork.
            await asyncio.to_thread(pipeline._run_discover, 1, sources)
            await asyncio.to_thread(pipeline._run_score)
        except Exception as exc:
            log.exception("Veille auto (discover/score) : %s", exc)
            return
        offers = await asyncio.to_thread(bot_core.fetch_pending_offers, MIN_SCORE, 50)
        notified = await asyncio.to_thread(bot_core.load_notified)
        new = [o for o in offers if o["url"] not in notified][:5]
        if not new:
            return
        await context.bot.send_message(
            OWNER_ID, f"🆕 {len(new)} nouvelle(s) offre(s) ≥ {MIN_SCORE}/10 détectée(s) :")
        for row in new:
            await _send_offer(context.bot, OWNER_ID, row)
        await asyncio.to_thread(bot_core.mark_notified, [o["url"] for o in new])

        # Surveillance automatique des réponses Gmail (toutes les ~30 min)
        if tick % 3 == 0:
            try:
                from applypilot.tracking.gmail_client import check_gmail_setup
                ok, _ = check_gmail_setup()
                if ok:
                    from applypilot.tracking import run_tracking
                    res = await asyncio.to_thread(run_tracking, days=7, ghosted_days=7, dry_run=False)
                    for ev in res.get("new_events", []):
                        if ev.get("type") in ("interview", "offer", "rejection"):
                            card = _format_tracking_event(ev)
                            if card:
                                await context.bot.send_message(OWNER_ID, card, parse_mode=ParseMode.HTML)
            except Exception as exc:
                log.warning("Veille Gmail tracking échouée : %s", exc)
    finally:
        context.application.bot_data["_cycle_busy"] = False


# ---------------------------------------------------------------------------
async def _post_init(app: Application) -> None:
    from applypilot.tracking import tracer
    await tracer.start_tracer_server(bot=app.bot)


async def _post_shutdown(app: Application) -> None:
    from applypilot.tracking import tracer
    await tracer.stop_tracer_server()


def build_application() -> Application:
    app = (
        Application.builder()
        .token(TOKEN)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .connect_timeout(30.0)
        .pool_timeout(30.0)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.bot_data["auto_search"] = True
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("commandes", commandes_cmd))
    app.add_handler(CommandHandler("commande", commandes_cmd))
    app.add_handler(CommandHandler("help", commandes_cmd))
    app.add_handler(CommandHandler("aide", commandes_cmd))
    app.add_handler(CommandHandler("scan", scan_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("offres", offres_cmd))
    app.add_handler(CommandHandler("offre", offres_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("pause", pause_cmd))
    app.add_handler(CommandHandler("resume", resume_cmd))
    app.add_handler(CommandHandler("clean", clean_cmd))
    app.add_handler(CommandHandler("job", job_cmd))
    app.add_handler(CommandHandler("jobs", job_cmd))
    app.add_handler(CommandHandler("trackers", trackers_cmd))
    app.add_handler(CommandHandler("tracker", trackers_cmd))
    app.add_handler(CommandHandler("track", track_cmd))
    app.add_handler(CommandHandler("confirmees", confirmee_cmd))
    app.add_handler(CommandHandler("confirmee", confirmee_cmd))
    app.add_handler(CommandHandler("refus", refus_cmd))
    app.add_handler(CommandHandler("done", done_cmd))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    if app.job_queue is not None:
        # Veille automatique toutes les N minutes (défaut 10) : scrape HelloWork,
        # score, et envoie les nouvelles bonnes offres.
        app.job_queue.run_repeating(_auto_cycle, interval=CYCLE_MINUTES * 60,
                                    first=30)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s - %(levelname)s - %(message)s",
                        datefmt="%H:%M:%S")
    if not TOKEN:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN manquant dans ~/.applypilot/.env "
            "(crée un bot via @BotFather sur Telegram).")
    if not OWNER_ID:
        raise SystemExit(
            "TELEGRAM_USER_ID manquant dans ~/.applypilot/.env "
            "(demande ton ID à @userinfobot sur Telegram).")
    app = build_application()
    log.info("ApplyPilot Telegram — démarrage (owner=%s)", OWNER_ID)
    app.run_polling(allowed_updates=Update.ALL_TYPES, bootstrap_retries=-1)


if __name__ == "__main__":
    main()
