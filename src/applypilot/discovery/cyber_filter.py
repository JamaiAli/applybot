"""Filtre partagé de détection de stages en cybersécurité (FR/EN/DE/SV/IT)
et wrapper rapide multi-pays pour la découverte d'offres internationales.

Garantit que les commandes internationales (/suisse, /luxembourg, /suede)
retournent exclusivement de vrais stages / traineeships / working student /
mémoires de fin d'études dans la cybersécurité (SOC, Pentest, GRC, IAM,
Active Directory / Entra ID, Sécurité Réseau & Cloud, DevSecOps, Audit IT)
— exactement comme /offres en France.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime

log = logging.getLogger(__name__)


def norm_text(s: str | None) -> str:
    """Normalise une chaîne (sans accents, minuscules, apostrophe standard)."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
    return s.lower().replace("’", "'").strip()


# ---------------------------------------------------------------------------
# 1. Détection STAGE / INTERNSHIP (multilingue : FR, EN, DE, SV, IT)
# ---------------------------------------------------------------------------
# Utilise des frontières de mots (?<![a-z]) / (?![a-z]) pour éviter les faux
# positifs suédois/anglais comme "internrevisor" (auditeur interne),
# "internkonsult" (consultant interne), "internal", "international", "internet".
_STAGE_TITLE_RE = re.compile(
    r"(?<![a-z])("
    r"stage|stagiaire|stagiaires|pfe|"
    r"intern|internship|internships|"
    r"trainee|traineeship|traineeprogram|"
    r"working student|werkstudent|werkstudentin|studentische|"
    r"praktik|praktikum|praktikant|praktikantin|praktikplats|hochschulpraktikant|"
    r"praticante|tirocinio|"
    r"exjobb|master thesis|bachelor thesis|thesis|lia|"
    r"graduate program|graduate"
    r")(?![a-z])"
)

_NON_STAGE_EXCLUDE_RE = re.compile(
    r"(?<![a-z])("
    r"apprenti|apprentie|apprentissage|cfc|lehrstelle|lehrling|"
    r"internrevisor|internkonsult|internal"
    r")(?![a-z])"
)


def is_stage_offer(title: str, contract: str = "") -> bool:
    """Vrai uniquement si l'offre est un vrai stage / internship / trainee / mémoire."""
    t = norm_text(title)
    c = norm_text(contract)

    # Rejeter les faux amis (ex: apprentissage CFC, internrevisor suédois, internal auditor)
    # sauf si "stage" ou "internship" est aussi explicitement dans le titre.
    has_explicit_stage = bool(re.search(r"(?<![a-z])(stage|stagiaire|internship|praktikplats|hochschulpraktikant)(?![a-z])", t))
    if _NON_STAGE_EXCLUDE_RE.search(t) and not has_explicit_stage:
        return False

    if _STAGE_TITLE_RE.search(t):
        return True

    if c and _STAGE_TITLE_RE.search(c) and not _NON_STAGE_EXCLUDE_RE.search(c):
        return True

    return False


# ---------------------------------------------------------------------------
# 2. Détection CYBERSÉCURITÉ (alignée sur indeed.py + multilingue CH/LU/SE)
# ---------------------------------------------------------------------------
_CYBER_RE = re.compile(
    r"(?<![a-z0-9])("
    # Cœur cybersécurité (FR / EN / DE / SV / IT)
    r"cyber|cybersecurite|cybersecurity|cyber security|infosec|ssi|rssi|ciso|ncsc|"
    r"securite informatique|securite des si|securite du si|securite des systemes|"
    r"securite reseau|securite cloud|securite applicative|securite operationnelle|"
    r"information security|it security|it-security|network security|cloud security|"
    r"application security|ot security|ot/it|"
    r"informationssicherheit|it-sicherheit|cybersicherheit|"
    r"informationssakerhet|it-sakerhet|cybersakerhet|sakerhet och resiliens|"
    r"sicurezza informatica|gestione delle vulnerabilita|"
    # Spécialités cyber (identiques à /offres)
    r"security|securing|"
    r"soc|siem|csirt|cert|blue team|edr|xdr|splunk|qradar|sentinel|threat|dfir|"
    r"pentest|pentester|red team|offensive|test d'intrusion|tests d'intrusion|"
    r"vulnerabilit|vulnerabilite|vulnerabilites|vulnerability|schwachstellen|bug bounty|oscp|appsec|owasp|"
    r"grc|iso 27001|27001|27002|ebios|nis2|nis 2|dora|pssi|"
    r"technology risk|it risk|cyber risk|it governance|it audit|audit it|auditor it|it resilience|"
    r"iam|active directory|entra|entra id|azure ad|pam|ldap|intune|cisco ise|access control|zero trust|identity|"
    r"devsecops|netsec|firewall|pare-feu|fortinet|palo alto|f5|vpn|ids|ips|nac|"
    r"forensic|forensique|malware|reverse engineering|cryptography|cryptographie|privacy-preserving"
    r")(?![a-z0-9])"
)

# Exclusions de sécurité physique / sécurité sociale / métiers hors-IT
_CYBER_FALSE_POSITIVE_RE = re.compile(
    r"(?<![a-z])("
    r"agent de securite|agents de securite|securite publique|securite commerciale|"
    r"securite incendie|sante et securite|stps|gardiennage|patrouille|"
    r"assurances sociales|social security|food security|humanitarian|"
    r"basketball|players|vapor|procurement|human resources|ressources humaines|"
    r"legal counsel|marketing|sales|accounting|psychologie|educateur|"
    r"assistant medical|medecin|infirmier|platrier|horloger|couvreur|paysagiste|"
    r"conciergerie|sporttherapie|arbeitsagogik|physique medicale|"
    r"supply chain|depositary|mobility consulting|tax|content management"
    r")(?![a-z])"
)


def is_cyber_offer(title: str, employer: str = "", extra_text: str = "") -> bool:
    """Vrai si l'offre appartient bien au domaine cybersécurité / sécurité IT / infra-sécu."""
    t = norm_text(title)
    emp = norm_text(employer)
    extra = norm_text(extra_text)

    if _CYBER_FALSE_POSITIVE_RE.search(t):
        return False

    # Match dans le titre ou dans le nom d'un organisme explicitement cyber (ex: NCSC, Kommando Cyber)
    if _CYBER_RE.search(t):
        return True

    if any(k in emp for k in ("cyber", "ncsc", "infosec", "cert ", "csirt")):
        return True

    # Pour "security" seul dans un texte long, on évite les faux positifs : on exige un mot-clé cyber fort
    if extra and re.search(
        r"(?<![a-z0-9])(cybersecurity|cybersecurite|cyber security|infosec|pentest|siem|soc analyst|devsecops|entra id|active directory|cisco ise|iso 27001|information security)(?![a-z0-9])",
        extra[:1200],
    ):
        return True

    return False


def _dedup_key(title: str, employer: str) -> tuple[str, str]:
    """Clé normalisée pour dédupliquer les doublons inter-sites (ex: 'On Ag' vs 'On', 'Full Time', 'H/F')."""
    t = norm_text(title)
    t = re.sub(r"\b(full\s*time|part\s*time|h/f/x|h/f|f/h|m/f/x|m/f/d|m/w/d|m/f|w/m|100%)\b", "", t)
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()

    e = norm_text(employer)
    e = re.sub(r"\b(ag|sa|sarl|sas|gmbh|inc|ltd|group|groupe|switzerland|suisse|luxembourg|ab)\b", "", e)
    e = re.sub(r"[^a-z0-9]+", " ", e).strip()
    return (t[:50], e[:25])


def filter_cyber_internships(
    offers: list[dict],
    custom_query: str | None = None,
    limit: int = 12,
) -> list[dict]:
    """Filtre et déduplique une liste d'offres pour ne garder que les stages cybersécurité.

    Si custom_query est fourni (ex: 'pentest', 'grc', 'cloud', 'entra'),
    conserve les stages correspondant à ce mot-clé (en vérifiant qu'il s'agit bien d'un stage).
    Sinon (défaut), impose à la fois is_stage_offer ET is_cyber_offer.
    """
    q_norm = norm_text(custom_query) if custom_query else ""
    # Si l'utilisateur a juste tapé "cyber" ou "cybersécurité", on applique le filtre cyber complet
    is_default_cyber = (not q_norm) or q_norm in ("cyber", "cybersecurite", "cybersecurity", "securite", "security", "stage")

    kept: list[dict] = []
    seen_urls: set[str] = set()
    seen_keys: set[tuple[str, str]] = set()

    for o in offers:
        url = (o.get("url") or "").strip()
        title = (o.get("title") or "").strip()
        employer = (o.get("employer_name") or "").strip()
        contract = (o.get("contract") or "").strip()
        desc = (o.get("description") or "").strip()
        loc = norm_text(o.get("location") or "")

        if not url or not title:
            continue

        # Ignorer les annonces génériques de salons / événements sans offre de stage directe
        if "evenement" in loc and not is_stage_offer(title):
            continue

        if not is_stage_offer(title, contract):
            continue

        if is_default_cyber:
            if not is_cyber_offer(title, employer, desc):
                continue
        else:
            haystack = f"{norm_text(title)} {norm_text(employer)} {norm_text(desc[:600])}"
            if q_norm not in haystack:
                continue

        # Déduplication par URL et par (titre, entreprise) normalisés
        norm_key = _dedup_key(title, employer)
        if url in seen_urls or norm_key in seen_keys:
            continue
        seen_urls.add(url)
        seen_keys.add(norm_key)

        kept.append(o)
        if len(kept) >= limit:
            break

    return kept


# ---------------------------------------------------------------------------
# 3. Wrapper rapide JobSpy (LinkedIn / Indeed) pour les pays internationaux
# ---------------------------------------------------------------------------
def scrape_jobspy_cyber_internships(
    country: str,
    country_fr: str,
    queries: list[str],
    sites: list[str] | None = None,
    limit: int = 12,
    hours_old: int = 720,
) -> list[dict]:
    """Récupère les stages cybersécurité récents sur un pays via JobSpy (LinkedIn / Indeed)."""
    try:
        from jobspy import scrape_jobs
    except Exception as exc:
        log.warning("JobSpy indisponible : %s", exc)
        return []

    target_sites = sites or ["linkedin"]
    raw_offers: list[dict] = []

    for q in queries:
        try:
            kwargs: dict = {
                "site_name": target_sites,
                "search_term": q,
                "location": country,
                "results_wanted": 12,
                "hours_old": hours_old,
            }
            if "indeed" in target_sites:
                kwargs["country_indeed"] = country

            df = scrape_jobs(**kwargs)
            if df is None or df.empty:
                continue

            for _, row in df.iterrows():
                url = str(row.get("job_url") or "").strip()
                title = str(row.get("title") or "").strip()
                if not url or not title or title.lower() == "nan":
                    continue

                comp = str(row.get("company") or "").strip()
                if not comp or comp.lower() == "nan":
                    comp = f"Entreprise ({country_fr})"

                loc = str(row.get("location") or "").strip()
                if not loc or loc.lower() == "nan":
                    loc = country_fr
                elif country_fr.lower() not in loc.lower() and country.lower() not in loc.lower():
                    loc = f"{loc}, {country_fr}"

                site = str(row.get("site") or "web").strip().lower()
                date_posted = row.get("date_posted")
                if date_posted is not None and str(date_posted).lower() not in ("nan", "nat", "none", ""):
                    posted_at = str(date_posted)
                else:
                    posted_at = datetime.now().isoformat()

                desc = str(row.get("description") or "")
                if desc.lower() == "nan":
                    desc = ""

                raw_offers.append({
                    "title": title,
                    "employer_name": comp,
                    "location": loc,
                    "url": url,
                    "posted_at": posted_at,
                    "source": site,
                    "description": desc,
                })
        except Exception as exc:
            log.warning("JobSpy (%s / %s) indisponible : %s", country, q, exc)

    return raw_offers
