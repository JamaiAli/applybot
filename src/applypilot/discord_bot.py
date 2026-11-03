"""Pilotage Discord d'ApplyPilot — « depuis le téléphone », mains libres.

Brique 2 du plan Phase 4. Le bot est la *surface de contrôle* du pipeline :
il cherche, score, présente les meilleures offres sous forme de cartes avec
boutons, et n'agit QUE sur validation explicite de l'utilisateur.

Règles de sécurité câblées ici (non négociables) :
  * Accès restreint au SEUL user ID Discord du propriétaire (gate sur chaque
    commande et chaque clic de bouton).
  * Aucune soumission sans ✅ explicite. Le ✅ enregistre le consentement et
    place l'offre en `ready_to_apply` ; la soumission réelle (Playwright,
    Phase 4b) reste une étape séparée. Dry-run par défaut.
  * Aucun secret en clair : token et user ID viennent de ~/.applypilot/.env
    (DISCORD_BOT_TOKEN, DISCORD_USER_ID).
  * Rythme limité : la recherche quotidienne est espacée, le scoring passe par
    Groq (déjà rate-limité).

Lancement :  python -m applypilot.discord_bot
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path

from applypilot import config

# load_env() DOIT précéder tout import de llm (vars lues à l'import du module).
config.load_env()

import discord
from discord import app_commands
from discord.ext import commands, tasks

from applypilot.database import get_connection, get_stats, transition_state

log = logging.getLogger("applypilot.discord")

# --- Configuration (depuis l'environnement, jamais en dur) ----------------
TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
try:
    OWNER_ID = int(os.environ.get("DISCORD_USER_ID", "0").strip() or "0")
except ValueError:
    OWNER_ID = 0

# Score minimal pour qu'une offre soit présentée (le funnel du projet = 8).
MIN_SCORE = int(os.environ.get("BOT_MIN_SCORE", str(config.DEFAULTS.get("min_score", 8))))
# Nombre max de cartes postées par recherche (évite le flood + le rate-limit).
MAX_CARDS = int(os.environ.get("BOT_MAX_CARDS", "6"))
# Heure de la recherche quotidienne automatique (format HH, heure locale).
DAILY_HOUR = int(os.environ.get("BOT_DAILY_HOUR", "9"))

# Mapping hash -> url des offres postées (persisté pour survivre aux redémarrages).
_OFFERS_PATH = config.APP_DIR / "bot_offers.json"


# ---------------------------------------------------------------------------
# Persistance légère du mapping hash<->url (les custom_id de boutons sont
# limités à 100 car. ; on y met un hash court, et on résout via ce fichier).
# ---------------------------------------------------------------------------
def _load_offers() -> dict[str, str]:
    try:
        return json.loads(_OFFERS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_offers(data: dict[str, str]) -> None:
    try:
        _OFFERS_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log.warning("Impossible d'écrire %s : %s", _OFFERS_PATH, exc)


def _url_hash(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:16]


def _remember(url: str) -> str:
    h = _url_hash(url)
    offers = _load_offers()
    if offers.get(h) != url:
        offers[h] = url
        _save_offers(offers)
    return h


def _resolve(h: str) -> str | None:
    return _load_offers().get(h)


# ---------------------------------------------------------------------------
# Accès aux offres (SQL direct, lecture seule)
# ---------------------------------------------------------------------------
def _display_company(row: dict) -> str:
    return (row.get("company") or "").strip() or (row.get("site") or "—")


def _fetch_pending_offers(min_score: int, limit: int) -> list[dict]:
    """Offres scorées, éligibles, non encore décidées (ni validées, ni ignorées,
    ni déjà candidatées), triées par score décroissant."""
    conn = get_connection()
    conn.row_factory = __import__("sqlite3").Row
    rows = conn.execute(
        """
        SELECT url, title, company, site, location, fit_score, score_reasoning,
               application_url, tailored_resume_path, cover_letter_path
        FROM jobs
        WHERE fit_score IS NOT NULL
          AND fit_score >= ?
          AND (eligibility IS NULL OR eligibility = 'eligible')
          AND COALESCE(apply_status, '') NOT IN
              ('applied', 'validated', 'ignored', 'skip_handicap')
        ORDER BY fit_score DESC, scored_at DESC
        LIMIT ?
        """,
        (min_score, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def _fetch_one(url: str) -> dict | None:
    conn = get_connection()
    conn.row_factory = __import__("sqlite3").Row
    r = conn.execute(
        """SELECT url, title, company, site, location, fit_score, score_reasoning,
                  application_url, tailored_resume_path, cover_letter_path, apply_status
           FROM jobs WHERE url = ?""",
        (url,),
    ).fetchone()
    return dict(r) if r else None


def _set_apply_status(url: str, status: str, note: str | None = None) -> None:
    conn = get_connection()
    conn.execute("UPDATE jobs SET apply_status = ? WHERE url = ?", (status, url))
    conn.commit()
    # Reflète la décision dans la machine à états (force=True : l'état d'origine
    # ne permet pas toujours la transition, mais la décision utilisateur prime).
    to_state = {"validated": "ready_to_apply", "ignored": "archived"}.get(status)
    if to_state:
        try:
            transition_state(conn, url, to_state,
                             reason=f"discord:{status}" + (f" ({note})" if note else ""),
                             force=True)
        except Exception as exc:  # transition non bloquante
            log.debug("transition_state ignorée pour %s : %s", url, exc)


# ---------------------------------------------------------------------------
# Rendu d'une carte d'offre
# ---------------------------------------------------------------------------
def _offer_embed(row: dict) -> discord.Embed:
    score = row.get("fit_score")
    colour = (discord.Color.green() if score and score >= 9
              else discord.Color.gold() if score and score >= 8
              else discord.Color.light_grey())
    title = (row.get("title") or "Offre")[:250]
    emb = discord.Embed(title=title, colour=colour,
                        url=row.get("application_url") or row.get("url") or None)
    emb.add_field(name="Entreprise", value=_display_company(row)[:100], inline=True)
    emb.add_field(name="Score", value=f"**{score}/10**", inline=True)
    if row.get("location"):
        emb.add_field(name="Lieu", value=str(row["location"])[:100], inline=True)
    reason = (row.get("score_reasoning") or "").strip()
    if reason:
        emb.add_field(name="Pourquoi", value=reason[:1000], inline=False)
    has_cv = bool(row.get("tailored_resume_path"))
    has_cl = bool(row.get("cover_letter_path"))
    emb.set_footer(text=f"CV adapté : {'oui' if has_cv else 'à générer'} · "
                        f"Lettre : {'oui' if has_cl else 'à générer'}")
    return emb


def _attachments(row: dict) -> list[discord.File]:
    files: list[discord.File] = []
    for key in ("tailored_resume_path", "cover_letter_path"):
        p = row.get(key)
        if not p:
            continue
        # Préfère un .pdf à côté du .txt si disponible.
        path = Path(p)
        pdf = path.with_suffix(".pdf")
        chosen = pdf if pdf.exists() else (path if path.exists() else None)
        if chosen:
            files.append(discord.File(str(chosen), filename=chosen.name))
    return files


# ---------------------------------------------------------------------------
# Vue à boutons (✅ / ✏️ / ❌)
# ---------------------------------------------------------------------------
class OfferView(discord.ui.View):
    """Boutons de décision pour une offre. `timeout=None` → vue persistante."""

    def __init__(self, url_hash: str):
        super().__init__(timeout=None)
        self.url_hash = url_hash
        # custom_id stable (préfixe + hash) pour la persistance inter-redémarrage.
        self.valider.custom_id = f"ap:ok:{url_hash}"
        self.modifier.custom_id = f"ap:edit:{url_hash}"
        self.ignorer.custom_id = f"ap:no:{url_hash}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Gate d'accès : seul le propriétaire peut cliquer."""
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "⛔ Accès réservé.", ephemeral=True)
            return False
        return True

    def _url(self) -> str | None:
        return _resolve(self.url_hash)

    @discord.ui.button(label="Valider", emoji="✅",
                       style=discord.ButtonStyle.success, row=0)
    async def valider(self, interaction: discord.Interaction, button: discord.ui.Button):
        url = self._url()
        if not url:
            await interaction.response.send_message(
                "Offre introuvable (redémarrage ?).", ephemeral=True)
            return
        _set_apply_status(url, "validated")
        row = _fetch_one(url) or {}
        for child in self.children:  # fige la décision
            child.disabled = True
        await interaction.response.edit_message(view=self)
        files = _attachments(row)
        msg = ("✅ **Validée.** Elle passe en *prête à postuler*. "
               "La soumission réelle reste une étape séparée (je ne soumets "
               "jamais sans ce ✅).")
        if files:
            msg += "\n📎 CV + lettre joints."
        await interaction.followup.send(msg, files=files, ephemeral=False)

    @discord.ui.button(label="Modifier", emoji="✏️",
                       style=discord.ButtonStyle.primary, row=0)
    async def modifier(self, interaction: discord.Interaction, button: discord.ui.Button):
        url = self._url()
        if not url:
            await interaction.response.send_message(
                "Offre introuvable (redémarrage ?).", ephemeral=True)
            return
        await interaction.response.send_modal(EditModal(url))

    @discord.ui.button(label="Ignorer", emoji="❌",
                       style=discord.ButtonStyle.secondary, row=0)
    async def ignorer(self, interaction: discord.Interaction, button: discord.ui.Button):
        url = self._url()
        if not url:
            await interaction.response.send_message(
                "Offre introuvable (redémarrage ?).", ephemeral=True)
            return
        _set_apply_status(url, "ignored")
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("❌ Offre ignorée.", ephemeral=True)


class EditModal(discord.ui.Modal, title="Modifier la candidature"):
    note = discord.ui.TextInput(
        label="Que changer dans le CV / la lettre ?",
        style=discord.TextStyle.paragraph,
        placeholder="Ex : insister sur l'Active Directory, retirer tel projet…",
        required=True, max_length=500,
    )

    def __init__(self, url: str):
        super().__init__()
        self._url = url

    async def on_submit(self, interaction: discord.Interaction):
        # On enregistre la consigne comme statut + note ; la régénération du CV
        # est déclenchée au prochain passage tailor/cover (Phase 3/4b).
        _set_apply_status(self._url, "edit_requested", note=str(self.note))
        await interaction.response.send_message(
            f"✏️ Noté : *{self.note}*\nLe CV/lettre sera régénéré avec cette "
            "consigne au prochain passage.", ephemeral=True)


# ---------------------------------------------------------------------------
# Le bot
# ---------------------------------------------------------------------------
class ApplyPilotBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        # Pas besoin du message_content intent : tout passe par slash + boutons.
        super().__init__(command_prefix="!", intents=intents)
        self.auto_search_enabled = True

    async def setup_hook(self):
        # Enregistre les vues persistantes connues (boutons cliquables après
        # redémarrage) + synchronise les slash commands.
        for h in _load_offers():
            self.add_view(OfferView(h))
        await self.tree.sync()
        if not self.daily_search.is_running():
            self.daily_search.start()

    async def on_ready(self):
        log.info("Connecté comme %s (owner=%s)", self.user, OWNER_ID)

    @tasks.loop(hours=24)
    async def daily_search(self):
        if not self.auto_search_enabled:
            return
        owner = self.get_user(OWNER_ID) or await self.fetch_user(OWNER_ID)
        if owner is None:
            return
        try:
            dm = owner.dm_channel or await owner.create_dm()
            await _run_search_and_post(dm, limit=MAX_CARDS)
        except Exception as exc:
            log.exception("Recherche quotidienne échouée : %s", exc)

    @daily_search.before_loop
    async def _before_daily(self):
        await self.wait_until_ready()


bot = ApplyPilotBot()


def _owner_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("⛔ Accès réservé.", ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)


# --- Logique partagée recherche + post ------------------------------------
async def _run_search_and_post(channel, limit: int, do_search: bool = True) -> int:
    """Lance (optionnellement) discover+score puis poste les meilleures offres.
    Renvoie le nombre de cartes postées."""
    if do_search:
        # Appels pipeline bloquants → thread séparé pour ne pas figer l'event loop.
        from applypilot import pipeline
        await channel.send("🔎 Recherche en cours (discover + score)…")
        try:
            await asyncio.to_thread(pipeline._run_discover)
            await asyncio.to_thread(pipeline._run_score)
        except Exception as exc:
            log.exception("Pipeline échoué : %s", exc)
            await channel.send(f"⚠️ Recherche partielle : {exc}")

    offers = await asyncio.to_thread(_fetch_pending_offers, MIN_SCORE, limit)
    if not offers:
        await channel.send(
            f"Aucune nouvelle offre ≥ {MIN_SCORE}/10 à valider pour l'instant.")
        return 0

    await channel.send(f"📋 {len(offers)} offre(s) à examiner :")
    for row in offers:
        h = _remember(row["url"])
        await channel.send(embed=_offer_embed(row),
                           files=_attachments(row),
                           view=OfferView(h))
    return len(offers)


# --- Slash commands --------------------------------------------------------
@bot.tree.command(name="search", description="Chercher + scorer, puis présenter les meilleures offres")
@_owner_only()
async def search_cmd(interaction: discord.Interaction, limit: int = MAX_CARDS):
    await interaction.response.defer(thinking=True)
    n = await _run_search_and_post(interaction.channel, limit=limit)
    await interaction.followup.send(f"Terminé — {n} offre(s) présentée(s).")


@bot.tree.command(name="offres", description="Afficher les offres à valider (sans relancer la recherche)")
@_owner_only()
async def offres_cmd(interaction: discord.Interaction, limit: int = MAX_CARDS):
    await interaction.response.defer(thinking=True)
    n = await _run_search_and_post(interaction.channel, limit=limit, do_search=False)
    await interaction.followup.send(f"{n} offre(s) affichée(s).")


@bot.tree.command(name="status", description="État du pipeline (entonnoir)")
@_owner_only()
async def status_cmd(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    stats = await asyncio.to_thread(get_stats)
    pending = await asyncio.to_thread(_fetch_pending_offers, MIN_SCORE, 9999)
    emb = discord.Embed(title="📊 ApplyPilot — état", colour=discord.Color.blurple())
    emb.add_field(name="Offres en base", value=str(stats.get("total", 0)), inline=True)
    emb.add_field(name="Scorées", value=str(stats.get("scored", 0)), inline=True)
    emb.add_field(name="Candidatées", value=str(stats.get("applied", 0)), inline=True)
    emb.add_field(name=f"À valider (≥{MIN_SCORE})", value=str(len(pending)), inline=True)
    dist = stats.get("score_distribution") or []
    if dist:
        emb.add_field(name="Distribution des scores",
                      value="\n".join(f"{s}/10 : {c}" for s, c in dist[:6]),
                      inline=False)
    emb.add_field(name="Recherche auto",
                  value="activée ✅" if bot.auto_search_enabled else "en pause ⏸",
                  inline=False)
    await interaction.followup.send(embed=emb)


@bot.tree.command(name="pause", description="Mettre en pause la recherche quotidienne auto")
@_owner_only()
async def pause_cmd(interaction: discord.Interaction):
    bot.auto_search_enabled = False
    await interaction.response.send_message("⏸ Recherche auto en pause.", ephemeral=True)


@bot.tree.command(name="resume", description="Reprendre la recherche quotidienne auto")
@_owner_only()
async def resume_cmd(interaction: discord.Interaction):
    bot.auto_search_enabled = True
    await interaction.response.send_message("▶️ Recherche auto réactivée.", ephemeral=True)


# ---------------------------------------------------------------------------
def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s - %(levelname)s - %(message)s",
                        datefmt="%H:%M:%S")
    if not TOKEN:
        raise SystemExit(
            "DISCORD_BOT_TOKEN manquant dans ~/.applypilot/.env "
            "(crée un bot sur https://discord.com/developers/applications).")
    if not OWNER_ID:
        raise SystemExit(
            "DISCORD_USER_ID manquant dans ~/.applypilot/.env "
            "(active le mode développeur Discord, clic droit sur ton profil → "
            "« Copier l'identifiant »).")
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
