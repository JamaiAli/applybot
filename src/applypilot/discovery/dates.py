"""Parsing des dates de publication des offres (source-agnostique).

Porté de ``cyber_stages.py`` (scraper Indeed) pour être réutilisé par tous les
adaptateurs du bot : ``to_dt`` convertit n'importe quel format de date Indeed
(ISO, timestamp epoch s/ms, relatif FR/EN « il y a 3 jours », « 30+ days ago »,
« hier », « Just posted »…) en ``datetime`` naïf (heure locale) ; ``job_date``
cherche la date dans les champs connus puis dans tout champ qui ressemble à une
date.

Module autonome (stdlib uniquement) pour éviter tout couplage/cycle d'import.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta


def _norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return s.lower().replace("’", "'")


# Champs Indeed susceptibles de porter la date de publication, par ordre de préférence.
DATE_KEYS = ("date_posted", "posted_at", "published_at", "publication_date", "pub_date",
             "date", "created_at", "first_seen", "posted", "age", "formatted_relative_time")

REL_RE = re.compile(r"(\d+)\s*\+?\s*(minute|min|heure|hour|h|jour|day|semaine|week|mois|month)")
UNIT = {"minute": "minutes", "min": "minutes", "heure": "hours", "hour": "hours", "h": "hours",
        "jour": "days", "day": "days", "semaine": "weeks", "week": "weeks"}


def to_dt(v):
    """Convertit une date Indeed (ISO, timestamp, '3 days ago', 'il y a 3 jours'…)
    en ``datetime`` naïf (heure locale), ou ``None`` si indéchiffrable."""
    now = datetime.now()
    if v is None or v == "" or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        try:
            return datetime.fromtimestamp(v / 1000 if v > 1e11 else v)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(v).strip()
    if s.isdigit():
        return to_dt(int(s))
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt
    except ValueError:
        pass
    n = _norm(s)
    if any(w in n for w in ("instant", "just posted", "aujourd", "today", "nouveau", "new")):
        return now
    if "hier" in n or "yesterday" in n:
        return now - timedelta(days=1)
    m = REL_RE.search(n)
    if m:
        k, unit = int(m.group(1)), m.group(2)
        if unit in ("mois", "month"):
            return now - timedelta(days=30 * k)
        return now - timedelta(**{UNIT[unit]: k})
    return None


def job_date(job: dict):
    """Date de publication d'une offre : champs connus d'abord, puis filet de
    sécurité sur tout champ dont le nom contient date/posted/publi."""
    for k in DATE_KEYS:
        dt = to_dt(job.get(k))
        if dt:
            return dt
    for k, val in job.items():
        if any(t in str(k).lower() for t in ("date", "posted", "publi")):
            dt = to_dt(val)
            if dt:
                return dt
    return None


def age_days(dt: datetime | None) -> int | None:
    """Ancienneté en jours d'une ``datetime``, ou ``None``."""
    if dt is None:
        return None
    return (datetime.now() - dt).days


def format_age_fr(value) -> str:
    """Libellé court FR de l'ancienneté : « aujourd'hui », « hier »,
    « il y a N j ». Accepte un ``datetime`` ou une chaîne (parsée via ``to_dt``).
    Renvoie '' si la date est absente/indéchiffrable."""
    dt = value if isinstance(value, datetime) else to_dt(value)
    d = age_days(dt)
    if d is None:
        return ""
    if d <= 0:
        return "aujourd'hui"
    if d == 1:
        return "hier"
    return f"il y a {d} j"
