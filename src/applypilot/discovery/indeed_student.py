"""Recherche de JOBS ÉTUDIANTS sur Indeed (porté de jobs_bourges.py).

Recherche **live** : pas d'insertion en base, pas de scoring LLM. Sert la
commande Telegram ``/job`` (jobs étudiants / week-end, par défaut Bourges),
à côté de la source HelloWork déjà interrogée par cette commande.

Réutilise les outils du scraper Indeed (``discovery.indeed`` : norm, job_text,
import du package, pagination, pauses) et le parseur de dates partagé
(``discovery.dates.job_date``). Le chemin du scraper vient de la même config/env
que la source Indeed principale (bloc ``indeed`` / INDEED_SCRAPER_PATH).

Config (bloc ``indeed_student`` de searches.yaml, tout surchargeable) :

    indeed_student:
      enabled: true
      location: "Bourges (18)"
      pages: 1
      weekend_only: false
      cher_only: false
      sort: "date"            # date | pertinence
      queries: {catégorie: [requêtes]}   # défaut = QUERIES ci-dessous
"""

from __future__ import annotations

import logging
import re

from applypilot import config
from applypilot.discovery import indeed as _indeed
from applypilot.discovery.dates import age_days, job_date, to_dt

log = logging.getLogger(__name__)

SOURCE = "indeed"


# Requêtes rapides par défaut pour la commande live /job (1-2s au lieu de 70s)
DEFAULT_FAST_STUDENT_QUERIES = [
    "job étudiant",
    "étudiant temps partiel",
    "temps partiel",
    "équipier polyvalent",
    "employé polyvalent",
]

DEFAULT_INTERIM_QUERIES = [
    "intérim",
    "mission intérim",
    "manutentionnaire",
    "préparateur de commandes",
    "magasinier",
]


# --------------------------------------------------------------------------- #
# Requêtes par catégorie (défaut = jobs_bourges.py ; surchargé par searches.yaml)
# --------------------------------------------------------------------------- #
QUERIES: dict[str, list[str]] = {
    "Général": [
        "job étudiant",
        "étudiant temps partiel",
        "week-end",
        "samedi dimanche",
        "extra",
    ],
    "Vente / Commerce": [
        "vendeur temps partiel",
        "hôte de caisse",
        "employé libre-service",
        "employé commercial week-end",
    ],
    "Restauration": [
        "équipier polyvalent",
        "serveur",
        "plongeur",
        "employé restauration rapide",
        "commis de cuisine extra",
    ],
    "Hôtellerie / Accueil": [
        "réceptionniste week-end",
        "agent d'accueil",
        "veilleur de nuit",
    ],
    "Logistique": [
        "préparateur de commandes",
        "manutentionnaire",
        "inventaire",
    ],
    "Livraison": [
        "livreur",
    ],
    "Services": [
        "baby-sitting",
        "garde d'enfants",
        "soutien scolaire",
        "agent d'entretien",
        "animateur",
    ],
    "Événementiel": [
        "staff événementiel",
        "hôte événementiel",
    ],
}

# Détection du type de poste (sur texte normalisé : minuscules, sans accents).
WEEKEND_RE = re.compile(r"(week[- ]?end|samedis?|dimanches?|fin de semaine)")
ETUDIANT_RE = re.compile(r"(etudiant|compatible avec (vos|tes|les) etudes|student)")
PARTIEL_RE = re.compile(r"(temps partiel|part[- ]time|mi[- ]temps|extras?\b|missions? ponctuelles?"
                        r"|vacations?|quelques heures)")
PLEIN_RE = re.compile(r"(temps plein|full[- ]time|35 ?h|39 ?h)")
CDI_RE = re.compile(r"(?<![a-z])cdi(?![a-z])")
EXCLU_TITRE_RE = re.compile(r"(?<![a-z])(stage|stagiaire|alternance|alternant|apprenti|apprentissage)"
                            r"(?![a-z])")


def departement(job: dict) -> str:
    """Numéro de département depuis le code postal ou le libellé de lieu."""
    loc = job.get("location") or {}
    if not isinstance(loc, dict):
        return ""
    cp = str(loc.get("postal_code") or "")
    if len(cp) >= 2:
        return cp[:2]
    m = re.search(r"\((\d{2})\)", str(loc.get("display_name") or ""))
    return m.group(1) if m else ""


# --------------------------------------------------------------------------- #
# Logique pure : filtrage + tags + normalisation (testable sans réseau)
# --------------------------------------------------------------------------- #
def filter_and_tag(records: list[dict], weekend_only: bool = False,
                   cher_only: bool = False) -> list[dict]:
    """Garde les jobs étudiants/week-end et les tague.

    `records` : liste de ``{"job": <dict Indeed>, "categories": [catégorie, …]}``.
    Écarte stages/alternances, et les temps-plein qui ne sont ni week-end ni
    étudiant ni temps-partiel. Retourne des dicts enrichis (tags + job).
    """
    kept: list[dict] = []
    for rec in records:
        job = rec["job"]
        categories = rec.get("categories") or []
        title = _indeed.norm(job.get("title", ""))
        text = _indeed.job_text(job)

        if EXCLU_TITRE_RE.search(title):
            continue  # stage / alternance : hors sujet ici

        we = bool(WEEKEND_RE.search(text))
        etu = bool(ETUDIANT_RE.search(text))
        partiel = bool(PARTIEL_RE.search(text))
        plein = bool(PLEIN_RE.search(text))
        cdi = bool(CDI_RE.search(text))

        if plein and not (we or etu or partiel):
            continue
        if weekend_only and not we:
            continue
        dep = departement(job)
        if cher_only and dep and dep != "18":
            continue

        kept.append({
            "job": job,
            "categorie": categories[0] if categories else "Général",
            "week_end": we,
            "etudiant": etu,
            "temps_partiel": partiel,
            "temps_plein": plein,
            "cdi": cdi,
            "departement": dep,
            "pertinence": 3 * we + 2 * etu + 2 * partiel - cdi,
            "date_dt": job_date(job),
        })
    return kept


def normalize_offer(record: dict) -> dict:
    """Transforme un job étudiant retenu en dict compatible `/job`
    (mêmes clés que ``hellowork.search_live``) + tags + date."""
    job = record["job"]
    comp = job.get("company") or {}
    company_name = comp.get("name") if isinstance(comp, dict) else comp
    loc = job.get("location") or {}
    location = loc.get("display_name") if isinstance(loc, dict) else loc
    url = job.get("link") or job.get("id")
    dt = record.get("date_dt")
    posted = dt.isoformat(timespec="minutes") if dt else None

    return {
        "url": url,
        "title": (job.get("title") or "").strip(),
        "location": location,
        "employer_name": (company_name or "Indeed").strip() or "Indeed",
        "application_url": job.get("apply_link") or url,
        "posted_at": posted,
        "date_publication": posted,
        "anciennete_jours": age_days(dt),
        "source": SOURCE,
        "week_end": record["week_end"],
        "etudiant": record["etudiant"],
        "temps_partiel": record["temps_partiel"],
        "temps_plein": record["temps_plein"],
        "cdi": record["cdi"],
        "departement": record["departement"],
        "categorie": record["categorie"],
        "pertinence": record["pertinence"],
    }


# --------------------------------------------------------------------------- #
# Config + recherche live
# --------------------------------------------------------------------------- #
def _student_config() -> dict:
    config.load_env()
    return (config.load_search_config() or {}).get("indeed_student", {}) or {}


def _normalize_queries(q) -> dict[str, list[str]]:
    if isinstance(q, dict) and q:
        return {str(k): list(v or []) for k, v in q.items()}
    if isinstance(q, list) and q:
        return {"Général": [str(x) for x in q]}
    # Défaut rapide pour commande live : 3 requêtes ciblées au lieu de 28
    return {"Général": list(DEFAULT_FAST_STUDENT_QUERIES)}


def search_student_jobs(location: str | None = None, limit: int = 12,
                        pages: int | None = None, weekend_only: bool | None = None,
                        cher_only: bool | None = None, sort: str | None = None,
                        queries=None, max_age_days: int = 7) -> list[dict]:
    """Recherche live de jobs étudiants sur Indeed. No-op gracieux (retourne [])
    si désactivé, si le scraper manque, ou si Indeed bloque — rapide pour Telegram.
    Filtre par défaut sur les offres de moins d'une semaine (max_age_days <= 7)."""
    cfg = _student_config()
    if not cfg.get("enabled", True):
        return []

    location = location or str(cfg.get("location", "Bourges (18)"))
    pages = int(cfg.get("pages", 1)) if pages is None else int(pages)
    weekend_only = bool(cfg.get("weekend_only", False)) if weekend_only is None else weekend_only
    cher_only = bool(cfg.get("cher_only", False)) if cher_only is None else cher_only
    sort = sort or str(cfg.get("sort", "date"))
    queries_by_cat = _normalize_queries(queries if queries is not None else cfg.get("queries"))

    try:
        jobs_mod = _indeed._import_jobs(_indeed._load_indeed_config())
    except Exception as e:
        log.warning("Jobs étudiants Indeed indisponibles (%s).", e)
        return []

    delay_min = float(cfg.get("delay_min", 0.3))
    delay_max = float(cfg.get("delay_max", 0.6))

    raw: dict = {}
    try:
        for cat, qs in queries_by_cat.items():
            for q in qs:
                res = jobs_mod.search(query=q, location=location, country="FR", sort=sort, page=1)
                for job in (res or {}).get("jobs") or []:
                    jid = job.get("id") or job.get("link")
                    if not jid:
                        continue
                    e = raw.setdefault(jid, {"job": job, "categories": []})
                    if cat not in e["categories"]:
                        e["categories"].append(cat)
                _indeed._pause(delay_min, delay_max)
    except Exception as e:
        log.warning("Jobs étudiants Indeed : scraping partiel (%s).", e)

    kept = filter_and_tag(list(raw.values()), weekend_only=weekend_only, cher_only=cher_only)
    offers = sort_offers([normalize_offer(r) for r in kept], sort=sort, max_age_days=max_age_days)
    return offers[:limit] if limit and limit > 0 else offers


def search_interim_jobs(location: str | None = None, limit: int = 12,
                        queries: list[str] | None = None,
                        max_age_days: int = 7) -> list[dict]:
    """Recherche live de missions INTÉRIM sur Indeed (rapide, sans HelloWork).

    Recherche ciblée sur les missions temporaires / logistique / manutention,
    filtrées à <= 7 jours, dédupliquée et triée avec le jour même en tête.
    """
    cfg = _student_config()
    if not cfg.get("enabled", True):
        return []

    location = location or str(cfg.get("location", "Bourges (18)"))
    target_queries = queries or DEFAULT_INTERIM_QUERIES

    try:
        jobs_mod = _indeed._import_jobs(_indeed._load_indeed_config())
    except Exception as e:
        log.warning("Missions intérim Indeed indisponibles (%s).", e)
        return []

    seen: set = set()
    offers: list[dict] = []
    try:
        for q in target_queries:
            res = jobs_mod.search(query=q, location=location, country="FR", sort="date", page=1)
            for j in (res or {}).get("jobs") or []:
                jid = j.get("id") or j.get("link")
                if not jid or jid in seen:
                    continue
                seen.add(jid)

                # Écarter les stages / alternances
                title = (j.get("title") or "").strip()
                if EXCLU_TITRE_RE.search(_indeed.norm(title)):
                    continue

                dt = job_date(j)
                age = age_days(dt)
                if max_age_days and max_age_days > 0:
                    if age is None or age > max_age_days:
                        continue

                comp = j.get("company") or {}
                comp_name = comp.get("name") if isinstance(comp, dict) else comp
                loc = j.get("location") or {}
                loc_name = loc.get("display_name") if isinstance(loc, dict) else loc
                posted = dt.isoformat(timespec="minutes") if dt else None

                offers.append({
                    "url": j.get("link") or jid,
                    "title": title,
                    "location": loc_name or location,
                    "employer_name": (comp_name or "Indeed").strip() or "Indeed",
                    "application_url": j.get("apply_link") or j.get("link") or jid,
                    "posted_at": posted,
                    "date_publication": posted,
                    "anciennete_jours": age,
                    "source": SOURCE,
                    "week_end": bool(WEEKEND_RE.search(_indeed.job_text(j))),
                })
            _indeed._pause(0.2, 0.5)
    except Exception as e:
        log.warning("Missions intérim Indeed : scraping partiel (%s).", e)

    # Tri : jour même d'abord (0j), puis hier (1j), etc., et plus récent en premier
    offers.sort(key=lambda x: (
        max(0, x.get("anciennete_jours") if x.get("anciennete_jours") is not None else 999),
        -(to_dt(x.get("posted_at")).timestamp() if to_dt(x.get("posted_at")) else 0),
    ))
    return offers[:limit] if limit and limit > 0 else offers


def _get_age_days(j: dict) -> int | None:
    if "anciennete_jours" in j and j["anciennete_jours"] is not None:
        return j["anciennete_jours"]
    posted = j.get("posted_at") or j.get("created_at") or j.get("date_publication")
    if posted:
        dt = to_dt(posted)
        return age_days(dt) if dt else None
    return None


def sort_offers(offers: list[dict], sort: str = "date", max_age_days: int | None = None) -> list[dict]:
    """Filtre (optionnellement si max_age_days > 0) et trie les offres :
    - 'date' : sorties le jour même (0j / aujourd'hui) en tout premier, puis hier (1j), etc.
      À date égale, départage par pertinence puis horodatage.
    - 'pertinence' : pertinence décroissante d'abord."""
    if max_age_days and max_age_days > 0:
        offers = [
            j for j in offers
            if _get_age_days(j) is not None and _get_age_days(j) <= max_age_days
        ]

    def _date_sort_key(j: dict):
        age = _get_age_days(j)
        effective_age = max(0, age) if age is not None else 999
        pert = -(j.get("pertinence") or 0)
        dt = to_dt(j.get("posted_at"))
        ts = -(dt.timestamp() if dt is not None else 0)
        return (effective_age, pert, ts)

    if sort == "pertinence":
        return sorted(
            offers,
            key=lambda j: (
                -(j.get("pertinence") or 0),
                _date_sort_key(j),
            )
        )
    return sorted(offers, key=_date_sort_key)
