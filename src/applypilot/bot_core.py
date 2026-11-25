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
from datetime import datetime, timezone
from pathlib import Path

from applypilot import config
from applypilot.database import execute_write, get_connection, transition_state
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
PENDING_STRATEGIES: tuple[str, ...] = ("hellowork_scrape", "indeed_scrape", "moovijob_scrape")

# Libellés d'affichage par source (colonne `source`).
SOURCE_LABELS: dict[str, str] = {"hellowork": "HelloWork", "indeed": "Indeed", "moovijob": "Moovijob"}


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


def get_decided_job_keys(conn: sqlite3.Connection | None = None) -> set[tuple[str, str]]:
    """Retourne l'ensemble des paires (_norm_key(titre), _norm_key(entreprise))
    pour toutes les offres déjà décidées (applied, validated, ignored, skip_handicap)."""
    if conn is None:
        conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT title, company, site
            FROM jobs
            WHERE COALESCE(apply_status, '') IN ('applied', 'validated', 'ignored', 'skip_handicap')
            """
        ).fetchall()
        keys: set[tuple[str, str]] = set()
        for r in rows:
            t = _norm_key(r[0])
            c = _norm_key(r[1] or r[2])
            if t:
                keys.add((t, c))
                if len(t) >= 25:
                    keys.add((t, ""))
        return keys
    except Exception as exc:
        log.warning("Impossible de récupérer les offres décidées : %s", exc)
        return set()


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


def _user_note_col(conn: sqlite3.Connection) -> str:
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        return "user_note" if "user_note" in cols else "NULL AS user_note"
    except Exception:
        return "NULL AS user_note"


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
    unote = _user_note_col(conn)
    rows = conn.execute(
        f"""
        SELECT url, title, company, site, source, domaines, strategy, location,
               fit_score, score_reasoning, application_url,
               tailored_resume_path, cover_letter_path, posted_at, {unote}
        FROM jobs
        WHERE fit_score IS NOT NULL
          AND fit_score >= ?
          AND (eligibility IS NULL OR eligibility = 'eligible')
          AND COALESCE(apply_status, '') NOT IN
              ('applied', 'validated', 'ignored', 'skip_handicap')
          AND LOWER(COALESCE(company, '')) NOT LIKE '%handicap%'
          AND LOWER(COALESCE(site, '')) NOT LIKE '%handicap%'
          AND LOWER(COALESCE(title, '')) NOT LIKE '%handicap%'
          AND LOWER(COALESCE(title, '')) NOT LIKE '%rqth%'
          {strat_clause}
        ORDER BY (COALESCE(strategy, '') <> 'indeed_scrape'),
                 (posted_at IS NULL), posted_at DESC, fit_score DESC
        """,
        params,
    ).fetchall()
    dict_rows = [dict(r) for r in rows]
    try:
        from applypilot.discovery.cyber_filter import is_handicap_targeted
        dict_rows = [
            r for r in dict_rows
            if not is_handicap_targeted(r.get("title") or "", r.get("company") or "", r.get("score_reasoning") or "", r.get("site") or "")
        ]
    except Exception:
        pass
    decided_keys = get_decided_job_keys(conn)
    if decided_keys:
        dict_rows = [
            r for r in dict_rows
            if (_norm_key(r.get("title")), _norm_key(display_company(r))) not in decided_keys
            and (_norm_key(r.get("title")), "") not in decided_keys
        ]
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
    unote = _user_note_col(conn)
    rows = conn.execute(
        f"""
        SELECT url, title, company, site, source, domaines, strategy, location,
               fit_score, score_reasoning, application_url,
               tailored_resume_path, cover_letter_path, posted_at, {unote}
        FROM jobs
        WHERE fit_score IS NOT NULL
          AND fit_score >= ?
          AND (eligibility IS NULL OR eligibility = 'eligible')
          AND COALESCE(apply_status, '') NOT IN
              ('applied', 'validated', 'ignored', 'skip_handicap')
          AND LOWER(COALESCE(company, '')) NOT LIKE '%handicap%'
          AND LOWER(COALESCE(site, '')) NOT LIKE '%handicap%'
          AND LOWER(COALESCE(title, '')) NOT LIKE '%handicap%'
          AND LOWER(COALESCE(title, '')) NOT LIKE '%rqth%'
          AND url IN ({placeholders})
        ORDER BY (posted_at IS NULL), posted_at DESC, fit_score DESC
        """,
        params,
    ).fetchall()
    dict_rows = [dict(r) for r in rows]
    try:
        from applypilot.discovery.cyber_filter import is_handicap_targeted
        dict_rows = [
            r for r in dict_rows
            if not is_handicap_targeted(r.get("title") or "", r.get("company") or "", r.get("score_reasoning") or "", r.get("site") or "")
        ]
    except Exception:
        pass
    decided_keys = get_decided_job_keys(conn)
    if decided_keys:
        dict_rows = [
            r for r in dict_rows
            if (_norm_key(r.get("title")), _norm_key(display_company(r))) not in decided_keys
            and (_norm_key(r.get("title")), "") not in decided_keys
        ]
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
    unote = _user_note_col(conn)
    r = conn.execute(
        # description / full_description : indispensables au choix du CV de réserve
        # (branche de l'offre) et à la détection des éléments à y ajouter. Sans
        # elles, le bot ne voyait que l'intitulé et envoyait le CV générique tel quel.
        f"""SELECT url, title, company, site, location, fit_score, score_reasoning,
                  description, full_description,
                  application_url, tailored_resume_path, cover_letter_path, apply_status, {unote}
           FROM jobs WHERE url = ?""",
        (url,),
    ).fetchone()
    return dict(r) if r else None


def set_apply_status(url: str, status: str, note: str | None = None) -> None:
    """Enregistre une décision utilisateur et reflète l'état dans la machine à
    états. `status` ∈ {validated, ignored, edit_requested, applied, prepared}."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    now_iso = datetime.now(timezone.utc).isoformat()

    # Si l'offre n'est pas encore dans la table jobs (ex: job étudiant ouvert directement), la créer
    existing_row = fetch_one(url)
    if not existing_row:
        st_job = _load_student_jobs().get(url) or {}
        st_title = st_job.get("title") or "Job étudiant / intérim"
        st_emp = st_job.get("employer") or ""
        mark_student_job(url, title=st_title, employer=st_emp)

    # Mettre à jour student_jobs.json si présent
    try:
        sd = _load_student_jobs()
        jk = extract_indeed_jk(url)
        keys_to_update = [k for k in [url, f"jk:{jk}" if jk else None] if k and k in sd]
        if keys_to_update:
            for k in keys_to_update:
                sd[k]["apply_status"] = status
                if status == "applied":
                    sd[k]["applied_at"] = now_iso
            _STUDENT_JOBS_PATH.write_text(json.dumps(sd, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    if status == "applied":
        if note is not None and _user_note_col(conn) == "user_note":
            execute_write(conn, "UPDATE jobs SET apply_status = ?, applied_at = ?, user_note = ? WHERE url = ?", (status, now_iso, note, url))
        else:
            execute_write(conn, "UPDATE jobs SET apply_status = ?, applied_at = ? WHERE url = ?", (status, now_iso, url))
    elif note is not None:
        if _user_note_col(conn) == "user_note":
            execute_write(conn, "UPDATE jobs SET apply_status = ?, user_note = ? WHERE url = ?", (status, note, url))
        else:
            execute_write(conn, "UPDATE jobs SET apply_status = ? WHERE url = ?", (status, url))
    else:
        execute_write(conn, "UPDATE jobs SET apply_status = ? WHERE url = ?", (status, url))
    to_state = {"validated": "ready_to_apply", "ignored": "archived",
                "applied": "applied"}.get(status)
    if to_state:
        try:
            transition_state(conn, url, to_state,
                             reason=f"bot:{status}" + (f" ({note})" if note else ""),
                             force=True, commit=True)
        except Exception as exc:  # transition non bloquante
            log.debug("transition_state ignorée pour %s : %s", url, exc)

    # Propagation automatique aux doublons (même titre et entreprise)
    if status in ("applied", "ignored"):
        try:
            cur_job = fetch_one(url)
            if cur_job and cur_job.get("title"):
                norm_t = _norm_key(cur_job.get("title"))
                norm_c = _norm_key(display_company(cur_job))
                all_sim = conn.execute(
                    "SELECT url, title, company, site FROM jobs WHERE url <> ? AND COALESCE(apply_status, '') <> ?",
                    (url, status),
                ).fetchall()
                for r in all_sim:
                    if _norm_key(r["title"]) == norm_t:
                        other_c = _norm_key(display_company(dict(r)))
                        if not norm_c or not other_c or norm_c == other_c:
                            other_url = r["url"]
                            if status == "applied":
                                execute_write(conn, "UPDATE jobs SET apply_status = ?, applied_at = ? WHERE url = ?", (status, now_iso, other_url))
                            else:
                                execute_write(conn, "UPDATE jobs SET apply_status = ? WHERE url = ?", (status, other_url))
                            if to_state:
                                try:
                                    transition_state(conn, other_url, to_state,
                                                     reason=f"bot:duplicate_{status}_of:{url}",
                                                     force=True, commit=True)
                                except Exception:
                                    pass
        except Exception as exc:
            log.debug("Propagation doublon échouée pour %s : %s", url, exc)


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
    """Associe l'offre (par hash et par jk Indeed) au message Telegram qui l'affiche, pour
    pouvoir le supprimer du fil après décision."""
    d = _load_cards()
    d[url_hash(url)] = int(message_id)
    jk = extract_indeed_jk(url)
    if jk:
        d[f"jk:{jk}"] = int(message_id)
    try:
        _CARDS_PATH.write_text(json.dumps(d), encoding="utf-8")
    except OSError as exc:
        log.warning("Impossible d'écrire %s : %s", _CARDS_PATH, exc)


def get_card_message(url: str) -> int | None:
    d = _load_cards()
    res = d.get(url_hash(url))
    if not res:
        jk = extract_indeed_jk(url)
        if jk:
            res = d.get(f"jk:{jk}")
    return res


def is_job_decided(url: str, title: str = "", employer: str = "") -> bool:
    """Vrai si l'offre a déjà été postulée ou ignorée."""
    if not url:
        return False

    # 1. Vérification dans student_jobs.json
    try:
        sd = _load_student_jobs()
        jk = extract_indeed_jk(url)
        keys_to_check = [url]
        if jk:
            keys_to_check.append(f"jk:{jk}")
        for k in keys_to_check:
            if k in sd and sd[k].get("apply_status") in ("applied", "ignored"):
                return True
    except Exception:
        pass

    # 2. Vérification dans la table jobs
    try:
        row = fetch_one(url)
        if row and (row.get("apply_status") in ("applied", "ignored") or row.get("state") in ("applied", "archived")):
            return True
    except Exception:
        pass

    # 3. Vérification par (titre, entreprise) si fournis
    if title:
        try:
            conn = get_connection()
            decided_keys = get_decided_job_keys(conn)
            tk = _norm_key(title)
            ck = _norm_key(employer)
            if (tk, ck) in decided_keys or (len(tk) >= 25 and (tk, "") in decided_keys):
                return True
        except Exception:
            pass

    return False


_LAST_PREPARED = config.APP_DIR / "last_prepared.txt"
_LAST_PREPARED_CV = config.APP_DIR / "last_prepared_cv.txt"
_LAST_PREPARED_TYPE = config.APP_DIR / "last_prepared_type.txt"
_LAST_PREPARED_TIME = config.APP_DIR / "last_prepared_time.txt"
_STUDENT_JOBS_PATH = config.APP_DIR / "student_jobs.json"

STUDENT_KEYWORDS = (
    "equipier", "équipier", "caissier", "caissière", "vendeur", "vendeuse",
    "manutentionnaire", "magasinier", "conditionnement", "restauration",
    "fast food", "fast-food", "hote de caisse", "hôte de caisse", "plongeur",
    "inventoriste", "preparateur de commande", "préparateur de commande",
    "livreur", "serveur", "serveuse", "job etudiant", "job étudiant",
    "etudiant", "étudiant", "interim", "intérim", "aide cuisine", "commis",
    "employe polyvalent", "employé polyvalent", "mcdo", "mcdonald", "burger king",
    "kfc", "leclerc", "carrefour", "lidl", "auchan", "intermarche", "intermarché",
    "rayon", "mise en rayon", "reassort", "réassort"
)


def extract_indeed_jk(url: str | None) -> str | None:
    """Extrait le job key Indeed (jk) d'une URL."""
    if not url:
        return None
    m = re.search(r"[?&]jk=([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1).lower()
    m = re.search(r"/(?:setup|form|viewjob|clk)/([a-f0-9]{16})", url, re.I)
    if m:
        return m.group(1).lower()
    m = re.search(r"([a-f0-9]{16})", url)
    if m:
        return m.group(1).lower()
    return None


def matches_student_keywords(text: str | None) -> bool:
    """Vrai si le texte contient des termes typiques d'un job étudiant / intérim."""
    if not text:
        return False
    t = text.lower()
    return any(k in t for k in STUDENT_KEYWORDS)


def _load_student_jobs() -> dict:
    try:
        return json.loads(_STUDENT_JOBS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def mark_student_job(url: str, title: str = "", employer: str = "") -> None:
    """Enregistre une offre comme job étudiant / intérim pour l'extension et le traceur."""
    if not url:
        return
    try:
        from applypilot.discovery.cyber_filter import is_handicap_targeted
        if is_handicap_targeted(title, employer, ""):
            return
    except Exception:
        pass
    import time as _time
    d = _load_student_jobs()
    jk = extract_indeed_jk(url)
    cv_path = str(config.APP_DIR / "cv_job_etudiant.pdf")

    # Préserver le titre et l'employeur existants si les arguments sont vides
    existing = d.get(url) or (d.get(f"jk:{jk}") if jk else {}) or {}
    title = title or existing.get("title", "")
    employer = employer or existing.get("employer", "")

    entry = {
        "url": url,
        "title": title,
        "employer": employer,
        "jk": jk,
        "time": _time.time(),
        "cv": cv_path,
    }
    d[url] = entry
    if jk:
        d[f"jk:{jk}"] = entry
    try:
        _STUDENT_JOBS_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        log.warning("Impossible d'écrire %s : %s", _STUDENT_JOBS_PATH, exc)

    # Enregistrement dans la table jobs (upsert)
    try:
        conn = get_connection()
        now_iso = datetime.now(timezone.utc).isoformat()
        execute_write(
            conn,
            """
            INSERT INTO jobs (url, title, company, site, tailored_resume_path, apply_status, category, discovered_at)
            VALUES (?, ?, ?, 'indeed_student', ?, 'ready_to_apply', 'job_etudiant', ?)
            ON CONFLICT(url) DO UPDATE SET
                title = CASE WHEN excluded.title != '' AND excluded.title != 'Job étudiant / intérim' THEN excluded.title ELSE jobs.title END,
                company = CASE WHEN excluded.company != '' THEN excluded.company ELSE jobs.company END,
                tailored_resume_path = excluded.tailored_resume_path,
                category = 'job_etudiant'
            """,
            (url, title or "Job étudiant / intérim", employer, cv_path, now_iso),
        )
    except Exception as exc:
        log.warning("Enregistrement job étudiant dans jobs.db : %s", exc)


def is_student_job(url: str | None) -> bool:
    """Vrai si l'URL ou l'offre correspond à un job étudiant / intérim."""
    if not url:
        return get_last_prepared_type() == "student"

    d = _load_student_jobs()
    if url in d:
        return True

    jk = extract_indeed_jk(url)
    if jk and f"jk:{jk}" in d:
        return True

    if matches_student_keywords(url):
        return True

    # Vérification en base jobs
    try:
        row = fetch_one(url)
        if row:
            if row.get("category") == "job_etudiant" or row.get("site") in ("indeed_student", "indeed_interim"):
                return True
            if "cv_job_etudiant" in str(row.get("tailored_resume_path") or ""):
                return True
            if matches_student_keywords(row.get("title")):
                return True
    except Exception:
        pass

    # Si la dernière offre préparée il y a moins de 45 min était un job étudiant
    if get_last_prepared_type() == "student":
        import time as _time
        if _time.time() - get_last_prepared_time() < 2700:
            return True

    return False


def set_last_prepared(url: str, cv_path: str | None = None, job_type: str = "stage") -> None:
    """Mémorise la dernière offre pour laquelle on a cliqué « Préparer » ou « Postuler »."""
    import time as _time
    try:
        _LAST_PREPARED.write_text(url, encoding="utf-8")
        _LAST_PREPARED_TYPE.write_text(job_type, encoding="utf-8")
        _LAST_PREPARED_TIME.write_text(str(_time.time()), encoding="utf-8")
        if cv_path:
            _LAST_PREPARED_CV.write_text(str(cv_path), encoding="utf-8")
    except OSError as exc:
        log.warning("Impossible d'écrire l'état de préparation : %s", exc)


def get_last_prepared() -> str | None:
    try:
        return (_LAST_PREPARED.read_text(encoding="utf-8").strip() or None)
    except OSError:
        return None


def get_last_prepared_cv() -> Path | None:
    try:
        txt = (_LAST_PREPARED_CV.read_text(encoding="utf-8").strip() or None)
        if txt:
            p = Path(txt)
            if p.exists():
                return p
    except OSError:
        pass
    return None


def get_last_prepared_type() -> str:
    try:
        return (_LAST_PREPARED_TYPE.read_text(encoding="utf-8").strip() or "stage")
    except OSError:
        return "stage"


def get_last_prepared_time() -> float:
    try:
        return float(_LAST_PREPARED_TIME.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0.0


def set_cv_path(url: str, pdf_path: str) -> None:
    """Enregistre le CV généré comme CV adapté de l'offre."""
    conn = get_connection()
    execute_write(conn, "UPDATE jobs SET tailored_resume_path = ? WHERE url = ?",
                  (pdf_path, url))


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


def parse_date_to_paris(raw: str | None) -> datetime | None:
    """Convertit une date brute (ISO, RFC 2822, email) en datetime dans le fuseau Europe/Paris."""
    if not raw:
        return None
    import email.utils
    import zoneinfo
    dt = None
    try:
        dt = email.utils.parsedate_to_datetime(raw)
    except Exception:
        pass
    if dt is None:
        try:
            clean_raw = str(raw).replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_raw)
        except Exception:
            pass
    if dt is not None:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        paris_tz = zoneinfo.ZoneInfo("Europe/Paris")
        return dt.astimezone(paris_tz)
    return None


def get_application_counts() -> dict:
    """Calcule le nombre de candidatures envoyées aujourd'hui et au total
    (fuseau Europe/Paris), avec le détail des offres du jour et l'historique récent."""
    import zoneinfo
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    paris_tz = zoneinfo.ZoneInfo("Europe/Paris")
    today = datetime.now(paris_tz).date()

    # Dates de transition pour les offres dont applied_at était NULL
    trans_dates = {}
    try:
        for tr in conn.execute(
            "SELECT job_url, at FROM job_state_transitions WHERE to_state = 'applied' ORDER BY at ASC"
        ).fetchall():
            trans_dates[tr["job_url"]] = tr["at"]
    except Exception:
        pass

    rows = conn.execute("""
        SELECT url, title, company, site, apply_status, applied_at, state, discovered_at
        FROM jobs
        WHERE applied_at IS NOT NULL OR apply_status = 'applied' OR state = 'applied'
    """).fetchall()

    total_count = len(rows)
    today_jobs = []
    daily_counts: dict[str, int] = {}

    for r in rows:
        raw_dt = r["applied_at"] or trans_dates.get(r["url"]) or r["discovered_at"]
        dt = parse_date_to_paris(raw_dt)
        if dt:
            d = dt.date()
            d_str = d.isoformat()
            daily_counts[d_str] = daily_counts.get(d_str, 0) + 1
            if d == today:
                row_dict = dict(r)
                comp = display_company(row_dict)
                if not comp or comp.lower() == "manual":
                    comp = (r["company"] or "").strip()
                if not comp or comp.lower() == "manual":
                    comp = (r["site"] or "").strip()
                if not comp or comp.lower() == "manual":
                    comp = "Candidature"
                today_jobs.append({
                    "title": (r["title"] or "Poste non spécifié").strip(),
                    "company": comp,
                    "time": dt.strftime("%H:%M"),
                    "dt": dt,
                })

    today_jobs.sort(key=lambda x: x["dt"], reverse=True)
    return {
        "today_count": len(today_jobs),
        "total_count": total_count,
        "today_jobs": today_jobs,
        "daily_counts": daily_counts,
    }


def get_all_applied_jobs() -> list[dict]:
    """Renvoie la liste complète de toutes les candidatures envoyées,
    triées de la plus récente à la plus ancienne."""
    import zoneinfo
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    paris_tz = zoneinfo.ZoneInfo("Europe/Paris")
    today = datetime.now(paris_tz).date()

    trans_dates = {}
    try:
        for tr in conn.execute(
            "SELECT job_url, at FROM job_state_transitions WHERE to_state = 'applied' ORDER BY at ASC"
        ).fetchall():
            trans_dates[tr["job_url"]] = tr["at"]
    except Exception:
        pass

    rows = conn.execute("""
        SELECT url, title, company, site, apply_status, applied_at, state, discovered_at
        FROM jobs
        WHERE applied_at IS NOT NULL OR apply_status = 'applied' OR state = 'applied'
    """).fetchall()

    all_jobs = []
    for r in rows:
        raw_dt = r["applied_at"] or trans_dates.get(r["url"]) or r["discovered_at"]
        dt = parse_date_to_paris(raw_dt)
        row_dict = dict(r)
        comp = display_company(row_dict)
        if not comp or comp.lower() == "manual":
            comp = (r["company"] or "").strip()
        if not comp or comp.lower() == "manual":
            comp = (r["site"] or "").strip()
        if not comp or comp.lower() == "manual":
            comp = "Entreprise"

        is_today = (dt.date() == today) if dt else False
        all_jobs.append({
            "url": r["url"] or "",
            "title": (r["title"] or "Poste non spécifié").strip(),
            "company": comp,
            "dt": dt,
            "time": dt.strftime("%H:%M") if dt else "--:--",
            "date": dt.strftime("%d/%m/%Y") if dt else "",
            "date_short": dt.strftime("%d/%m") if dt else "",
            "is_today": is_today,
        })

    all_jobs.sort(key=lambda x: (x["dt"] is not None, x["dt"]), reverse=True)
    return all_jobs
