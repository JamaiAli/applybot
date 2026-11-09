"""Adaptateur de découverte Indeed (via le scraper local omkarcloud/indeed-scraper).

Deuxième source, au même niveau que HelloWork. On réutilise le package `indeed`
déjà installé hors du repo (``C:\\Users\\jamai\\indeed-scraper``) et la logique
métier de son script ``cyber_stages.py`` : requêtes cyber par domaine, filtre
« stage uniquement », filtre « bien cyber », et tagging par domaine.

Import du package : **aucun chemin en dur**. Le dossier du scraper vient de la
config (``searches.yaml → indeed.scraper_path``) ou de la variable d'environnement
``INDEED_SCRAPER_PATH``, et on l'ajoute à ``sys.path`` à la volée. Si le package
ou le réseau échoue, l'adaptateur **log et devient un no-op** : il ne casse jamais
HelloWork.

Format de sortie : identique à HelloWork (voir ``insert_normalized_jobs``), plus
les champs ``source="indeed"`` et ``domaines=[principal, …]``.

Config (bloc ``indeed`` de searches.yaml, tout surchargeable) :

    indeed:
      enabled: true
      scraper_path: ""            # vide → variable d'env INDEED_SCRAPER_PATH
      location: "France"
      pages: 1
      include_alternance: false
      delay_min: 1.5
      delay_max: 3.5
      queries:                    # dict {domaine: [requêtes]} OU liste plate
        "Généraliste cyber": ["stage cybersécurité", ...]
"""

from __future__ import annotations

import html
import inspect
import logging
import os
import random
import re
import sys
import time
import unicodedata

from applypilot import config
from applypilot.database import get_connection, init_db
from applypilot.discovery.ats_common import insert_normalized_jobs
from applypilot.discovery.dates import age_days, job_date, to_dt

log = logging.getLogger(__name__)

STRATEGY = "indeed_scrape"
DEFAULT_SITE = "Indeed"
SOURCE = "indeed"


# --------------------------------------------------------------------------- #
# Requêtes par domaine (défaut = cyber_stages.py ; surchargé par searches.yaml)
# --------------------------------------------------------------------------- #
QUERIES: dict[str, list[str]] = {
    "Généraliste cyber": [
        "stage cybersécurité",
        "stagiaire cybersécurité",
        "stage sécurité informatique",
        "stage ingénieur sécurité",
        "cybersecurity internship",
    ],
    "GRC": [
        "stage GRC cybersécurité",
        "stage gouvernance risques conformité sécurité",
        "stage ISO 27001",
        "stage analyste risques cyber",
        "stage assistant RSSI",
    ],
    "Pentest / Offensive": [
        "stage pentest",
        "stage test d'intrusion",
        "stage auditeur sécurité",
        "stage red team",
    ],
    "SOC / Blue team": [
        "stage SOC",
        "stage analyste SOC",
        "stage CERT CSIRT",
        "stage SIEM",
        "stage threat intelligence",
    ],
    "AD / IAM": [
        "stage Active Directory",
        "stage IAM",
        "stage Entra ID",
        "stage gestion des identités et des accès",
    ],
    "DevSecOps / Cloud": [
        "stage DevSecOps",
        "stage DevOps",
        "stage sécurité cloud",
        "stage Kubernetes",
    ],
    "AppSec": [
        "stage sécurité applicative",
        "stage AppSec",
    ],
    "Forensic / Malware": [
        "stage forensic",
        "stage analyse malware",
        "stage reverse engineering",
    ],
    "Réseau / Infra sécu": [
        "stage sécurité réseau",
        "stage administrateur systèmes sécurité",
    ],
    "Projet / Intégration": [
        "stage chef de projet cybersécurité",
        "stage chef de projet intégration IT",
    ],
}

# Mots-clés pour taguer chaque offre (sans accents, en minuscules).
DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "GRC": ["grc", "gouvernance", "iso 27001", "27001", "27002", "ebios", "nis2", "nis 2",
            "dora", "pssi", "rssi", "pca", "pra", "analyse de risques", "gestion des risques",
            "conformite"],
    "Pentest / Offensive": ["pentest", "pentester", "test d'intrusion", "tests d'intrusion",
                            "red team", "offensive", "auditeur securite", "audit de securite",
                            "bug bounty", "oscp"],
    "SOC / Blue team": ["soc", "siem", "cert", "csirt", "blue team", "edr", "xdr", "splunk",
                        "qradar", "sentinel", "threat intelligence", "reponse a incident",
                        "gestion des incidents de securite"],
    "AD / IAM": ["active directory", "entra id", "entra", "azure ad", "iam", "gestion des identites",
                 "identity", "pam", "ldap", "intune"],
    "DevSecOps / Cloud": ["devsecops", "devops", "ci/cd", "kubernetes", "docker", "terraform",
                          "ansible", "cloud", "aws", "azure", "gcp"],
    "AppSec": ["appsec", "securite applicative", "owasp", "sast", "dast", "secure code"],
    "Forensic / Malware": ["forensic", "forensique", "malware", "reverse engineering",
                           "investigation numerique", "dfir"],
    "Réseau / Infra sécu": ["securite reseau", "firewall", "pare-feu", "fortinet", "palo alto",
                            "vpn", "ids", "ips", "proxy", "nac"],
    "Projet / Intégration": ["chef de projet", "integration", "pmo", "amoa"],
}

# Termes qui prouvent qu'une offre est bien « cyber ».
CORE_KEYWORDS = [
    "cyber", "cybersecurite", "cybersecurity", "infosec", "ssi", "rssi", "securite informatique",
    "securite des si", "securite du si", "securite des systemes", "securite reseau",
    "securite cloud", "securite applicative", "securite operationnelle", "ingenieur securite",
    "analyste securite", "security", "soc", "pentest", "siem", "iam", "grc", "devsecops",
    "forensic", "active directory",
]
# Domaines trop génériques pour valider une offre à eux seuls.
WEAK_DOMAINS = {"Projet / Intégration"}


# --------------------------------------------------------------------------- #
# Outils de normalisation / regex (portés de cyber_stages.py)
# --------------------------------------------------------------------------- #
def norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return s.lower().replace("’", "'")


def _kw_regex(words) -> re.Pattern:
    alt = "|".join(re.escape(norm(w)) for w in words)
    return re.compile(r"(?<![a-z0-9])(" + alt + r")(?![a-z0-9])")


DOMAIN_RES = {d: _kw_regex(w) for d, w in DOMAIN_KEYWORDS.items()}
CORE_RE = _kw_regex(CORE_KEYWORDS)
STAGE_TITLE_RE = re.compile(r"(?<![a-z])(stage|stagiaire|internship|intern|pfe)(?![a-z])")
STAGE_TEXT_RE = re.compile(
    r"(convention de stage|stage de fin d.etudes|stage de \d|stage d.une duree|offre de stage"
    r"|internship)"
)
ALT_RE = re.compile(r"(?<![a-z])(alternance|alternant|apprentissage|apprenti)(?![a-z])")
TAG_RE = re.compile(r"<[^>]+>")


def _strings(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)
    elif isinstance(obj, str):
        yield obj


def job_text(job: dict) -> str:
    """Tout le texte d'une offre, nettoyé et normalisé (pour le matching)."""
    return norm(TAG_RE.sub(" ", " ".join(_strings(job))))


def is_stage(title: str, text: str, keep_alt: bool) -> bool:
    """title/text déjà normalisés (norm())."""
    if STAGE_TITLE_RE.search(title):
        return True
    if ALT_RE.search(title):
        return keep_alt
    if STAGE_TEXT_RE.search(text):
        return True
    return keep_alt and bool(ALT_RE.search(text))


def classify_domains(text: str) -> list[str]:
    """Domaines cyber détectés dans le texte normalisé."""
    return [d for d, rx in DOMAIN_RES.items() if rx.search(text)]


def _clean_html(raw: str | None) -> str | None:
    if not raw:
        return None
    txt = html.unescape(TAG_RE.sub(" ", raw))
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt or None


# --------------------------------------------------------------------------- #
# Logique pure : filtrage + tagging + normalisation (testable sans réseau)
# --------------------------------------------------------------------------- #
def filter_and_tag(records: list[dict], keep_alt: bool = False) -> list[dict]:
    """Garde les offres « stage » ET « bien cyber », et calcule leurs domaines.

    `records` : liste de ``{"job": <dict Indeed>, "domaines_req": [domaine, …]}``
    où ``domaines_req`` liste les domaines dont les requêtes ont fait remonter
    l'offre (fallback pour le domaine principal). Retourne une liste de
    ``{"domaine_principal", "domaines", "job"}``.
    """
    kept: list[dict] = []
    for rec in records:
        job = rec["job"]
        domaines_req = rec.get("domaines_req") or []
        title = norm(job.get("title", ""))
        text = job_text(job)

        if not is_stage(title, text, keep_alt):
            continue

        title_domains = [d for d, rx in DOMAIN_RES.items() if rx.search(title)]
        strong_title_domains = [d for d in title_domains if d not in WEAK_DOMAINS]
        if not CORE_RE.search(text) and not strong_title_domains:
            continue  # hors cyber

        domaines = classify_domains(text)
        principal = (strong_title_domains[0] if strong_title_domains
                     else (domaines_req[0] if domaines_req
                           else (domaines[0] if domaines else "Généraliste cyber")))
        kept.append({"domaine_principal": principal, "domaines": domaines, "job": job})
    return kept


def normalize_offer(record: dict) -> dict:
    """Transforme une offre Indeed retenue en dict au format HelloWork + extras.

    `record` : un élément retourné par ``filter_and_tag``.
    """
    job = record["job"]
    principal = record["domaine_principal"]
    domaines = record.get("domaines") or []
    # domaines ordonnés : principal en tête (utile pour l'affichage / dédup).
    ordered = [principal] + [d for d in domaines if d != principal]

    comp = job.get("company") or {}
    company_name = comp.get("name") if isinstance(comp, dict) else comp
    loc = job.get("location") or {}
    if isinstance(loc, dict):
        location = loc.get("display_name") or loc.get("city")
    else:
        location = loc
    url = job.get("link") or job.get("id")
    full_desc = _clean_html(job.get("description"))
    # Date de publication via le parseur robuste partagé (ISO/timestamp/relatif).
    dt = job_date(job)
    posted = dt.isoformat(timespec="minutes") if dt else None

    return {
        "url": url,
        "title": (job.get("title") or "").strip(),
        "location": location,
        "description": (full_desc[:500] if full_desc else None),
        "full_description": full_desc,
        "application_url": job.get("apply_link") or url,
        "employer_name": (company_name or DEFAULT_SITE).strip() or DEFAULT_SITE,
        "posted_at": posted,
        "date_publication": posted,   # alias : champ de date commun aux sources
        "source": SOURCE,
        "domaines": ordered,
    }


# --------------------------------------------------------------------------- #
# Chargement config + import du package `indeed`
# --------------------------------------------------------------------------- #
def _load_indeed_config() -> dict:
    config.load_env()
    return (config.load_search_config() or {}).get("indeed", {}) or {}


def _normalize_queries(q) -> dict[str, list[str]]:
    """Accepte un dict {domaine: [requêtes]} ou une liste plate ; défaut = QUERIES."""
    if isinstance(q, dict) and q:
        return {str(k): list(v or []) for k, v in q.items()}
    if isinstance(q, list) and q:
        return {"Généraliste cyber": [str(x) for x in q]}
    return {k: list(v) for k, v in QUERIES.items()}


def _resolve_scraper_path(cfg: dict) -> str | None:
    path = (cfg.get("scraper_path") or "").strip() or os.environ.get("INDEED_SCRAPER_PATH", "").strip()
    return path or None


def _resolve_max_age(cfg: dict) -> int:
    """Âge max (jours) des offres à garder. `indeed.max_age_days`, sinon le
    `max_age_days` global, sinon 30. 0 (ou négatif) = pas de filtre."""
    if "max_age_days" in cfg:
        return int(cfg.get("max_age_days") or 0)
    top = config.load_search_config() or {}
    return int(top.get("max_age_days", 30) or 0)


def _within_max_age(posted_at: str | None, max_age: int) -> bool:
    """True si l'offre doit être gardée. Les offres sans date sont conservées
    (elles seront triées en fin de liste à la présentation)."""
    if not posted_at:
        return True
    d = age_days(to_dt(posted_at))
    return d is None or d <= max_age


def _import_jobs(cfg: dict):
    """Importe `indeed.jobs` depuis le dossier configuré. Lève si introuvable."""
    path = _resolve_scraper_path(cfg)
    if not path:
        raise RuntimeError(
            "chemin du scraper Indeed non configuré "
            "(searches.yaml → indeed.scraper_path OU variable d'env INDEED_SCRAPER_PATH)")
    if not os.path.isdir(path):
        raise RuntimeError(f"dossier du scraper Indeed introuvable : {path}")
    if path not in sys.path:
        sys.path.insert(0, path)
    from indeed import jobs  # noqa: PLC0415 (import tardif : chemin dynamique)
    return jobs


def _page_kw(jobs_mod) -> str | None:
    params = inspect.signature(jobs_mod.search).parameters
    return next((k for k in ("page", "page_number", "page_num") if k in params), None)


def _pause(delay_min: float, delay_max: float) -> None:
    lo, hi = sorted((float(delay_min), float(delay_max)))
    time.sleep(random.uniform(lo, hi))


def _search_all(jobs_mod, query: str, location: str, pages: int,
                page_kw: str | None, delay_min: float, delay_max: float) -> list[dict]:
    """Toutes les offres d'une requête, sur `pages` pages (rythme lent)."""
    out: list[dict] = []
    seen: set = set()
    for page in range(1, pages + 1):
        kwargs = {"query": query, "location": location, "country": "FR"}
        if page_kw:
            kwargs[page_kw] = page
        elif page > 1:
            break
        try:
            res = jobs_mod.search(**kwargs)
        except Exception as e:
            log.debug("Indeed '%s' page %d : %s", query, page, e)
            break
        batch = (res or {}).get("jobs") or []
        new = [j for j in batch if (j.get("id") or j.get("link")) not in seen]
        if not new:
            break
        seen.update(j.get("id") or j.get("link") for j in new)
        out.extend(new)
        pag = (res or {}).get("pagination") or {}
        if any(k in pag and not pag[k] for k in ("has_next", "next", "next_page")):
            break
        if page < pages:
            _pause(delay_min, delay_max)
    return out


def _collect(jobs_mod, queries_by_domain: dict[str, list[str]], location: str,
             pages: int, page_kw: str | None,
             delay_min: float, delay_max: float) -> list[dict]:
    """Scrape toutes les requêtes et regroupe les offres uniques (par id/link)."""
    raw: dict = {}
    for domain, queries in queries_by_domain.items():
        for q in queries:
            for job in _search_all(jobs_mod, q, location, pages, page_kw, delay_min, delay_max):
                jid = job.get("id") or job.get("link")
                if not jid:
                    continue
                e = raw.setdefault(jid, {"job": job, "domaines_req": []})
                if domain not in e["domaines_req"]:
                    e["domaines_req"].append(domain)
            _pause(delay_min, delay_max)
    return list(raw.values())


# --------------------------------------------------------------------------- #
# Point d'entrée
# --------------------------------------------------------------------------- #
def run_indeed_discovery(workers: int = 1, dry_run: bool = False,
                         queries: dict[str, list[str]] | list[str] | None = None,
                         location: str | None = None) -> dict:
    """Découvre des offres de STAGE cyber sur Indeed via le scraper local.

    No-op gracieux si Indeed est désactivé, le chemin manque, le package est
    absent ou le réseau échoue : on log et on continue (HelloWork intact).

    En `dry_run` : scrape + filtre + normalise, retourne les offres dans
    ``result["offers"]`` SANS insertion en base ni envoi Telegram.
    """
    cfg = _load_indeed_config()
    if not cfg.get("enabled", True):
        log.info("Indeed désactivé (indeed.enabled=false) — ignoré.")
        return {"found": 0, "new": 0, "existing": 0, "offers": [],
                "errors": [], "skipped": "disabled"}

    location = str(location or cfg.get("location", "France"))
    pages = int(cfg.get("pages", 1))
    keep_alt = bool(cfg.get("include_alternance", False))
    delay_min = float(cfg.get("delay_min", 1.5))
    delay_max = float(cfg.get("delay_max", 3.5))
    queries_by_domain = _normalize_queries(queries if queries is not None else cfg.get("queries"))

    try:
        jobs_mod = _import_jobs(cfg)
    except Exception as e:
        log.warning("Indeed indisponible (%s) — source ignorée, HelloWork continue.", e)
        return {"found": 0, "new": 0, "existing": 0, "offers": [],
                "errors": [str(e)], "skipped": "import_failed"}

    page_kw = _page_kw(jobs_mod)
    n_queries = sum(len(v) for v in queries_by_domain.values())
    log.info("Indeed : %d requête(s), %d page(s)/requête, lieu=%s (rythme lent)",
             n_queries, pages, location)

    try:
        records = _collect(jobs_mod, queries_by_domain, location, pages, page_kw,
                           delay_min, delay_max)
    except Exception as e:
        log.warning("Indeed : scraping échoué (%s) — le site bloque peut-être.", e)
        return {"found": 0, "new": 0, "existing": 0, "offers": [],
                "errors": [str(e)], "skipped": "scrape_failed"}

    kept = filter_and_tag(records, keep_alt=keep_alt)
    offers = [normalize_offer(r) for r in kept]

    # Filtre d'ancienneté (offres trop vieilles ignorées ; sans date = gardées).
    max_age = _resolve_max_age(cfg)
    if max_age and max_age > 0:
        before = len(offers)
        offers = [o for o in offers if _within_max_age(o.get("posted_at"), max_age)]
        dropped = before - len(offers)
        if dropped:
            log.info("Indeed : %d offre(s) écartée(s) (> %d j).", dropped, max_age)

    log.info("Indeed : %d offre(s) brute(s) -> %d stage(s) cyber retenu(s)",
             len(records), len(offers))

    if dry_run:
        return {"found": len(offers), "new": 0, "existing": 0,
                "offers": offers, "errors": [], "dry_run": True}

    conn = get_connection()
    init_db()
    new, existing = insert_normalized_jobs(conn, offers, DEFAULT_SITE, STRATEGY)
    log.info("Indeed terminé : %d retenu(s) (%d new, %d existing)",
             len(offers), new, existing)
    return {"found": len(offers), "new": new, "existing": existing,
            "offers": offers, "errors": []}
