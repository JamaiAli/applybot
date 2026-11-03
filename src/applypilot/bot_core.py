"""Logique commune aux bots de pilotage (Discord, Telegram).

Tout ce qui ne dépend pas d'une plateforme précise vit ici : lecture des
offres scorées, enregistrement des décisions, mapping hash<->url, résolution
des pièces jointes CV/lettre. Les modules `telegram_bot` et `discord_bot`
n'ajoutent que la couche d'interface (messages, boutons, fichiers).

Règles de sécurité partagées :
  * Aucune soumission auto : ✅ Valider place l'offre en `ready_to_apply`
    (consentement explicite), jamais d'envoi réel ici.
  * Lecture des offres filtrée (score, éligibilité, pas déjà décidées).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from pathlib import Path

from applypilot import config
from applypilot.database import get_connection, transition_state

log = logging.getLogger("applypilot.bot")

# Score minimal pour qu'une offre soit présentée (funnel du projet = 8).
MIN_SCORE = int(config.DEFAULTS.get("min_score", 8))
# Nombre max de cartes postées par recherche (évite le flood + le rate-limit).
MAX_CARDS = 6

# Mapping hash -> url des offres postées (persisté pour survivre aux redémarrages).
_OFFERS_PATH = config.APP_DIR / "bot_offers.json"


# ---------------------------------------------------------------------------
# Mapping hash<->url (les identifiants de boutons sont courts ; on y met un
# hash, résolu via ce fichier, pour que les boutons restent valides après
# redémarrage).
# ---------------------------------------------------------------------------
def load_offers() -> dict[str, str]:
    try:
        return json.loads(_OFFERS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_offers(data: dict[str, str]) -> None:
    try:
        _OFFERS_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log.warning("Impossible d'écrire %s : %s", _OFFERS_PATH, exc)


def url_hash(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:16]


def remember(url: str) -> str:
    h = url_hash(url)
    offers = load_offers()
    if offers.get(h) != url:
        offers[h] = url
        _save_offers(offers)
    return h


def resolve(h: str) -> str | None:
    return load_offers().get(h)


# ---------------------------------------------------------------------------
# Accès aux offres (SQL direct, lecture seule)
# ---------------------------------------------------------------------------
def display_company(row: dict) -> str:
    return (row.get("company") or "").strip() or (row.get("site") or "—")


def fetch_pending_offers(min_score: int = MIN_SCORE, limit: int = MAX_CARDS) -> list[dict]:
    """Offres scorées, éligibles, non encore décidées (ni validées, ni ignorées,
    ni déjà candidatées), triées par score décroissant."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
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


def fetch_one(url: str) -> dict | None:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    r = conn.execute(
        """SELECT url, title, company, site, location, fit_score, score_reasoning,
                  application_url, tailored_resume_path, cover_letter_path, apply_status
           FROM jobs WHERE url = ?""",
        (url,),
    ).fetchone()
    return dict(r) if r else None


def set_apply_status(url: str, status: str, note: str | None = None) -> None:
    """Enregistre une décision utilisateur et reflète l'état dans la machine à
    états. `status` ∈ {validated, ignored, edit_requested}."""
    conn = get_connection()
    conn.execute("UPDATE jobs SET apply_status = ? WHERE url = ?", (status, url))
    conn.commit()
    to_state = {"validated": "ready_to_apply", "ignored": "archived"}.get(status)
    if to_state:
        try:
            transition_state(conn, url, to_state,
                             reason=f"bot:{status}" + (f" ({note})" if note else ""),
                             force=True)
        except Exception as exc:  # transition non bloquante
            log.debug("transition_state ignorée pour %s : %s", url, exc)


def set_cv_path(url: str, pdf_path: str) -> None:
    """Enregistre le CV généré comme CV adapté de l'offre."""
    conn = get_connection()
    conn.execute("UPDATE jobs SET tailored_resume_path = ? WHERE url = ?",
                 (pdf_path, url))
    conn.commit()


def attachment_paths(row: dict) -> list[Path]:
    """Chemins des pièces jointes CV/lettre existantes (préfère le .pdf)."""
    out: list[Path] = []
    for key in ("tailored_resume_path", "cover_letter_path"):
        p = row.get(key)
        if not p:
            continue
        path = Path(p)
        pdf = path.with_suffix(".pdf")
        chosen = pdf if pdf.exists() else (path if path.exists() else None)
        if chosen:
            out.append(chosen)
    return out
