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
import re
import sqlite3
import unicodedata
from pathlib import Path

from applypilot import config
from applypilot.database import get_connection, transition_state
from applypilot.discovery.dates import age_days, to_dt

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


# Sources présentées par le bot (HelloWork + Indeed, au même niveau).
PENDING_STRATEGIES: tuple[str, ...] = ("hellowork_scrape", "indeed_scrape")

# Libellés d'affichage par source (colonne `source`).
SOURCE_LABELS: dict[str, str] = {"hellowork": "HelloWork", "indeed": "Indeed"}


def _norm_key(s: str | None) -> str:
    """Minuscule, sans accents, espaces compactés — pour comparer titres/villes."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def display_source(row: dict) -> str:
    """Libellé lisible de la source (déduit de `source`, repli sur la stratégie)."""
    src = (row.get("source") or "").strip()
    if not src:
        strat = row.get("strategy") or ""
        src = {"hellowork_scrape": "hellowork", "indeed_scrape": "indeed"}.get(strat, "")
    return SOURCE_LABELS.get(src, src.capitalize() if src else "")


def display_domaines(row: dict) -> str:
    """Domaine cyber principal (1ᵉʳ élément de la liste JSON `domaines`), ou ''."""
    raw = row.get("domaines")
    if not raw:
        return ""
    try:
        doms = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return ""
    return str(doms[0]) if doms else ""


def _dedup_cross_source(rows: list[dict]) -> list[dict]:
    """Supprime les doublons inter-sources : même (titre, entreprise, ville)
    normalisés → on garde la 1ʳᵉ ligne (l'ordre de tri décide du gagnant).
    La dédup intra-source par URL est déjà assurée en base (clé primaire)."""
    seen: set[tuple[str, str, str]] = set()
    out: list[dict] = []
    for r in rows:
        key = (_norm_key(r.get("title")),
               _norm_key(display_company(r)),
               _norm_key(r.get("location")))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _resolve_max_age_days(max_age_days: int | None = None) -> int:
    """Âge max (jours) des offres présentées. Param explicite, sinon
    `max_age_days` global de searches.yaml, sinon 7 (<= 1 semaine).
    0 = pas de filtre d'âge."""
    if max_age_days is not None:
        return int(max_age_days)
    try:
        val = (config.load_search_config() or {}).get("max_age_days")
        if val is not None:
            return int(val)
        return 7
    except (ValueError, TypeError):
        return 7


def _filter_by_age(rows: list[dict], max_age_days: int = 7) -> list[dict]:
    """Retire les offres plus vieilles que `max_age_days` (toutes sources, via
    `posted_at`). Les offres de plus de `max_age_days` jours sont écartées.
    0 = pas de filtre d'âge (toutes conservées). Les offres sans date sont
    conservées en fin de liste."""
    if not max_age_days or max_age_days <= 0:
        return rows
    out: list[dict] = []
    for r in rows:
        posted = r.get("posted_at")
        if not posted:
            out.append(r)
            continue
        d = age_days(to_dt(posted))
        if d is None or d <= max_age_days:
            out.append(r)
    return out


def _sort_offers_by_freshness(rows: list[dict]) -> list[dict]:
    """Trie les offres selon :
    1. Source : Indeed en priorité absolue (indeed_scrape avant les autres sources).
    2. Présence de date : les offres avec date d'abord, sans date en fin.
    3. Fraîcheur : sorties le jour même (0j / aujourd'hui) en tout premier,
       puis hier (1j), 2j... jusqu'à 7j.
    4. Horodatage : plus récent en premier au sein du même jour.
    5. Pertinence : fit score décroissant.
    """
    def _key(r: dict):
        strat = (r.get("strategy") or "").lower()
        src = (r.get("source") or "").lower()
        is_indeed = 0 if (strat == "indeed_scrape" or src == "indeed") else 1

        posted = r.get("posted_at")
        if not posted:
            return (is_indeed, 1, 999, 0, -(r.get("fit_score") or 0))

        dt = to_dt(posted)
        age = age_days(dt) if dt is not None else 999
        effective_age = max(0, age) if age is not None else 999
        ts = -(dt.timestamp() if dt is not None else 0)
        score = -(r.get("fit_score") or 0)
        return (is_indeed, 0, effective_age, ts, score)

    return sorted(rows, key=_key)


def _matches_location(target_loc: str, job_loc: str | None) -> bool:
    """Vérifie si la localisation de l'offre correspond à la ville recherchée."""
    if not job_loc or not target_loc:
        return False
    target = _norm_key(target_loc)
    jloc = _norm_key(job_loc)
    if target in jloc:
        return True
    # Gestion Île-de-France (départements 75, 77, 78, 91, 92, 93, 94, 95)
    if target in ("idf", "ile de france", "region parisienne"):
        if re.search(r"\b(75|77|78|91|92|93|94|95)\d{3}\b|\b(75|77|78|91|92|93|94|95)\b|paris|ile-de-france", jloc):
            return True
    return False


METIER_ALIASES: dict[str, list[str]] = {
    "soc": ["soc", "siem", "cert", "csirt", "blue team", "edr", "xdr", "threat intelligence"],
    "pentest": ["pentest", "pentester", "intrusion", "red team", "offensive", "audit de securite"],
    "ad": ["active directory", "entra", "azure ad", "iam", "ldap", "ad"],
    "active directory": ["active directory", "entra id", "ad", "iam"],
    "cloud": ["cloud", "aws", "azure", "gcp", "kubernetes", "docker", "devsecops"],
    "devops": ["devops", "devsecops", "ci/cd", "kubernetes", "docker", "terraform"],
    "devsecops": ["devsecops", "devops", "ci/cd", "kubernetes", "securite cloud", "appsec"],
    "grc": ["grc", "gouvernance", "iso 27001", "27001", "conformite", "ebios", "dora", "nis2", "rssi"],
    "reseau": ["reseau", "reseaux", "network", "firewall", "infra", "cisco", "vpn", "systemes et reseaux"],
    "forensic": ["forensic", "malware", "reverse", "dfir"],
    "appsec": ["appsec", "applicative", "owasp"],
    "ia": ["ia", "ai", "intelligence artificielle", "machine learning"],
    "support": ["support", "helpdesk", "technicien", "support informatique"],
    "systeme": ["systeme", "systemes", "linux", "windows", "administrateur"],
    "cyber": ["cyber", "cybersecurite", "securite informatique"],
}


def _matches_query(query: str | None, row: dict) -> bool:
    """Vérifie si une offre correspond au métier ou mot-clé recherché."""
    if not query:
        return True
    q = query.lower().strip()
    patterns = METIER_ALIASES.get(q, [q])

    title = (row.get("title") or "").lower()
    domaines = (row.get("domaines") or "").lower()

    for pat in patterns:
        pat_clean = pat.strip()
        if not pat_clean:
            continue
        # Pour les tokens courts (<= 3 chars comme 'ad', 'ia', 'soc'), exiger frontière de mot
        if len(pat_clean) <= 3:
            rx = rf"\b{re.escape(pat_clean)}\b"
            if re.search(rx, title) or re.search(rx, domaines):
                return True
        else:
            if pat_clean in title or pat_clean in domaines:
                return True
    return False


def fetch_pending_offers(min_score: int = MIN_SCORE, limit: int = MAX_CARDS,
                         strategies: tuple[str, ...] = PENDING_STRATEGIES,
                         max_age_days: int | None = None,
                         location: str | None = None,
                         query: str | None = None) -> list[dict]:
    """Offres scorées, éligibles, non décidées, des sources `strategies`
    (HelloWork + Indeed par défaut).

    Règles strictes de tri et filtrage :
    - Moins d'une semaine obligatoire (max_age_days = 7 par défaut).
    - Les offres sorties le jour même (aujourd'hui) s'affichent en tout premier.
    - À date égale, Indeed est prioritaire sur HelloWork, puis par fit_score décroissant.
    - Déduplication inter-sources (garde la meilleure offre selon le tri ci-dessus).
    - Filtrage optionnel par localisation si `location` est spécifié.
    - Filtrage optionnel par métier/mot-clé si `query` est spécifié.
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    params: list = [min_score]
    strat_clause = ""
    strats = tuple(strategies or ())
    if strats:
        placeholders = ", ".join("?" for _ in strats)
        strat_clause = f"AND strategy IN ({placeholders})"
        params.extend(strats)
    rows = conn.execute(
        f"""
        SELECT url, title, company, site, source, domaines, strategy, location,
               fit_score, score_reasoning, application_url,
               tailored_resume_path, cover_letter_path, posted_at
        FROM jobs
        WHERE fit_score IS NOT NULL
          AND fit_score >= ?
          AND (eligibility IS NULL OR eligibility = 'eligible')
          AND COALESCE(apply_status, '') NOT IN
              ('applied', 'validated', 'ignored', 'skip_handicap')
          {strat_clause}
        ORDER BY (COALESCE(strategy, '') <> 'indeed_scrape'),
                 (posted_at IS NULL), posted_at DESC, fit_score DESC
        """,
        params,
    ).fetchall()
    dict_rows = [dict(r) for r in rows]
    # Filtrer par âge max (strictement <= 7 jours par défaut)
    max_days = _resolve_max_age_days(max_age_days)
    filtered = _filter_by_age(dict_rows, max_days)
    # Trier avec fraîcheur absolue en priorité (0j = aujourd'hui en tête)
    sorted_rows = _sort_offers_by_freshness(filtered)
    # Déduplication inter-sources (l'ordre de tri décide de l'offre conservée)
    deduped = _dedup_cross_source(sorted_rows)
    if location:
        deduped = [r for r in deduped if _matches_location(location, r.get("location"))]
    if query:
        deduped = [r for r in deduped if _matches_query(query, r)]
    return deduped[:limit] if limit and limit > 0 else deduped


def get_all_job_urls() -> set[str]:
    """Retourne l'ensemble des URLs de toutes les offres actuellement présentes en base."""
    conn = get_connection()
    rows = conn.execute("SELECT url FROM jobs").fetchall()
    return {r[0] for r in rows if r[0]}


def fetch_new_scan_offers(new_urls: list[str], min_score: int = MIN_SCORE,
                          limit: int = MAX_CARDS, location: str | None = None,
                          query: str | None = None) -> list[dict]:
    """Récupère, filtre et trie STRICTEMENT les offres issues d'un scan en direct.

    Seules les offres faisant partie de new_urls (non présentes en base avant le scan)
    sont retournées.
    """
    if not new_urls:
        return []
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    placeholders = ", ".join("?" for _ in new_urls)
    params: list = [min_score] + list(new_urls)
    rows = conn.execute(
        f"""
        SELECT url, title, company, site, source, domaines, strategy, location,
               fit_score, score_reasoning, application_url,
               tailored_resume_path, cover_letter_path, posted_at
        FROM jobs
        WHERE fit_score IS NOT NULL
          AND fit_score >= ?
          AND (eligibility IS NULL OR eligibility = 'eligible')
          AND COALESCE(apply_status, '') NOT IN
              ('applied', 'validated', 'ignored', 'skip_handicap')
          AND url IN ({placeholders})
        ORDER BY (posted_at IS NULL), posted_at DESC, fit_score DESC
        """,
        params,
    ).fetchall()
    dict_rows = [dict(r) for r in rows]
    filtered = _filter_by_age(dict_rows, 7)
    sorted_rows = _sort_offers_by_freshness(filtered)
    deduped = _dedup_cross_source(sorted_rows)
    if location:
        deduped = [r for r in deduped if _matches_location(location, r.get("location"))]
    if query:
        deduped = [r for r in deduped if _matches_query(query, r)]
    return deduped[:limit] if limit and limit > 0 else deduped


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
    to_state = {"validated": "ready_to_apply", "ignored": "archived",
                "applied": "applied"}.get(status)
    if to_state:
        try:
            transition_state(conn, url, to_state,
                             reason=f"bot:{status}" + (f" ({note})" if note else ""),
                             force=True)
        except Exception as exc:  # transition non bloquante
            log.debug("transition_state ignorée pour %s : %s", url, exc)


_NOTIFIED_PATH = config.APP_DIR / "notified_offers.json"


def load_notified() -> set[str]:
    try:
        return set(json.loads(_NOTIFIED_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def mark_notified(urls: list[str]) -> None:
    """Mémorise les offres déjà envoyées automatiquement (anti-spam)."""
    s = load_notified()
    s.update(urls)
    try:
        _NOTIFIED_PATH.write_text(json.dumps(sorted(s), ensure_ascii=False),
                                  encoding="utf-8")
    except OSError as exc:
        log.warning("Impossible d'écrire %s : %s", _NOTIFIED_PATH, exc)


_CARDS_PATH = config.APP_DIR / "card_messages.json"


def _load_cards() -> dict[str, int]:
    try:
        return json.loads(_CARDS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def set_card_message(url: str, message_id: int) -> None:
    """Associe l'offre (par hash) au message Telegram qui l'affiche, pour
    pouvoir le supprimer du fil après décision."""
    d = _load_cards()
    d[url_hash(url)] = int(message_id)
    try:
        _CARDS_PATH.write_text(json.dumps(d), encoding="utf-8")
    except OSError as exc:
        log.warning("Impossible d'écrire %s : %s", _CARDS_PATH, exc)


def get_card_message(url: str) -> int | None:
    return _load_cards().get(url_hash(url))


_LAST_PREPARED = config.APP_DIR / "last_prepared.txt"


def set_last_prepared(url: str) -> None:
    """Mémorise la dernière offre pour laquelle on a cliqué « Préparer »."""
    try:
        _LAST_PREPARED.write_text(url, encoding="utf-8")
    except OSError as exc:
        log.warning("Impossible d'écrire %s : %s", _LAST_PREPARED, exc)


def get_last_prepared() -> str | None:
    try:
        return (_LAST_PREPARED.read_text(encoding="utf-8").strip() or None)
    except OSError:
        return None


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
