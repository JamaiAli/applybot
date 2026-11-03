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

from applypilot import config

# load_env() DOIT précéder tout import de llm (vars lues à l'import du module).
config.load_env()

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    Update,
)
from telegram.constants import ParseMode
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

log = logging.getLogger("applypilot.telegram")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
try:
    OWNER_ID = int(os.environ.get("TELEGRAM_USER_ID", "0").strip() or "0")
except ValueError:
    OWNER_ID = 0

MIN_SCORE = bot_core.MIN_SCORE
MAX_CARDS = bot_core.MAX_CARDS
DAILY_HOUR = int(os.environ.get("BOT_DAILY_HOUR", "9"))


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
def _offer_text(row: dict) -> str:
    score = row.get("fit_score")
    title = html.escape((row.get("title") or "Offre")[:250])
    company = html.escape(bot_core.display_company(row)[:100])
    lines = [f"<b>{title}</b>", f"🏢 {company}   ·   ⭐ <b>{score}/10</b>"]
    if row.get("location"):
        lines.append(f"📍 {html.escape(str(row['location'])[:100])}")
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
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Valider", callback_data=f"ok:{h}"),
        InlineKeyboardButton("✏️ Modifier", callback_data=f"edit:{h}"),
        InlineKeyboardButton("❌ Ignorer", callback_data=f"no:{h}"),
    ]])


async def _send_offer(bot, chat_id: int, row: dict) -> None:
    h = bot_core.remember(row["url"])
    await bot.send_message(chat_id, _offer_text(row),
                           parse_mode=ParseMode.HTML,
                           disable_web_page_preview=True,
                           reply_markup=_offer_keyboard(h))
    for path in bot_core.attachment_paths(row):
        try:
            with open(path, "rb") as fh:
                await bot.send_document(chat_id, InputFile(fh, filename=path.name))
        except OSError as exc:
            log.warning("Pièce jointe %s illisible : %s", path, exc)


# ---------------------------------------------------------------------------
# Logique partagée recherche + post
# ---------------------------------------------------------------------------
async def _run_search_and_post(bot, chat_id: int, limit: int, do_search: bool = True) -> int:
    if do_search:
        from applypilot import pipeline
        await bot.send_message(chat_id, "🔎 Recherche en cours (discover + score)…")
        try:
            await asyncio.to_thread(pipeline._run_discover)
            await asyncio.to_thread(pipeline._run_score)
        except Exception as exc:
            log.exception("Pipeline échoué : %s", exc)
            await bot.send_message(chat_id, f"⚠️ Recherche partielle : {exc}")

    offers = await asyncio.to_thread(bot_core.fetch_pending_offers, MIN_SCORE, limit)
    if not offers:
        await bot.send_message(
            chat_id, f"Aucune nouvelle offre ≥ {MIN_SCORE}/10 à valider pour l'instant.")
        return 0

    await bot.send_message(chat_id, f"📋 {len(offers)} offre(s) à examiner :")
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
        "👋 ApplyPilot est prêt.\n\n"
        "/search — chercher + scorer, puis présenter les meilleures offres\n"
        "/offres — afficher les offres à valider (sans relancer la recherche)\n"
        "/status — état du pipeline\n"
        "/pause — mettre en pause la recherche quotidienne auto\n"
        "/resume — la réactiver\n\n"
        "Sur chaque offre : ✅ Valider · ✏️ Modifier · ❌ Ignorer.\n"
        "Je ne soumets jamais rien sans ton ✅.")


async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)
    limit = MAX_CARDS
    if context.args and context.args[0].isdigit():
        limit = int(context.args[0])
    n = await _run_search_and_post(context.bot, update.effective_chat.id, limit)
    await context.bot.send_message(update.effective_chat.id,
                                   f"Terminé — {n} offre(s) présentée(s).")


async def offres_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)
    limit = MAX_CARDS
    if context.args and context.args[0].isdigit():
        limit = int(context.args[0])
    n = await _run_search_and_post(context.bot, update.effective_chat.id, limit, do_search=False)
    await context.bot.send_message(update.effective_chat.id, f"{n} offre(s) affichée(s).")


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

    if action == "ok":
        bot_core.set_apply_status(url, "validated")
        row = bot_core.fetch_one(url) or {}
        await query.answer("✅ Validée")
        await query.edit_message_reply_markup(reply_markup=_offer_keyboard(h, done=True))
        paths = bot_core.attachment_paths(row)
        txt = ("✅ <b>Validée.</b> Elle passe en <i>prête à postuler</i>. "
               "La soumission réelle reste une étape séparée (je ne soumets "
               "jamais sans ce ✅).")
        if paths:
            txt += "\n📎 CV + lettre envoyés."
        await context.bot.send_message(query.message.chat_id, txt, parse_mode=ParseMode.HTML)
        for path in paths:
            try:
                with open(path, "rb") as fh:
                    await context.bot.send_document(query.message.chat_id,
                                                    InputFile(fh, filename=path.name))
            except OSError:
                pass

    elif action == "no":
        bot_core.set_apply_status(url, "ignored")
        await query.answer("❌ Ignorée")
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
# Recherche quotidienne auto
# ---------------------------------------------------------------------------
async def _daily_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.application.bot_data.get("auto_search", True):
        return
    try:
        await _run_search_and_post(context.bot, OWNER_ID, limit=MAX_CARDS)
    except Exception as exc:
        log.exception("Recherche quotidienne échouée : %s", exc)


# ---------------------------------------------------------------------------
def build_application() -> Application:
    app = Application.builder().token(TOKEN).build()
    app.bot_data["auto_search"] = True
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("offres", offres_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("pause", pause_cmd))
    app.add_handler(CommandHandler("resume", resume_cmd))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    if app.job_queue is not None:
        app.job_queue.run_daily(_daily_job, time=_dt.time(hour=DAILY_HOUR))
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
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
