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

# Exclusions spécifiques aux offres destinées / réservées aux travailleurs handicapés (RQTH)
_HANDICAP_TITLE_RE = re.compile(
    r"(?<![a-z0-9])("
    r"rqth|boeth|handi[- ]?stage|handi[- ]?alternance|handi[- ]?job|"
    r"handicap|handicape|handicapee|handicapes|handicapees|"
    r"tremplin[- ]?handicap|disability|disabled"
    r")(?![a-z0-9])"
)

_HANDICAP_EMPLOYER_RE = re.compile(
    r"(?<![a-z0-9])("
    r"handicap|handicape|handicapee|handicapes|handicapees|handi|"
    r"talents handicap|groupe talents handicap|tremplin|arpejeh|agefiph|fiphfp|"
    r"club etre|talenteo|l adapt|ladapt|handi[- ]?sup|handi[- ]?partage|"
    r"cap emploi|entreprise adaptee|esat|milieu protege|rqth|boeth"
    r")(?![a-z0-9])"
)

_HANDICAP_DESC_RE = re.compile(
    r"("
    r"reservee?\s+(exclusivement\s+)?(aux?\s+)?(personnes?\s+(en\s+situation\s+de\s+)?handicap|travailleurs?\s+handicapes?|titulaires?\s+d['’ ]?une?\s+rqth|rqth)|"
    r"(dediee?|destinee?)\s+(exclusivement\s+)?(aux?\s+)?(personnes?\s+(en\s+situation\s+de\s+)?handicap|travailleurs?\s+handicapes?|rqth)|"
    r"(exigence|exigee?|obligatoire|justifier|titulaire|reconnaissance)\s*:?\s*(de\s+la\s+|d['’ ]?une?\s+)?rqth|"
    r"beneficiaire\s+de\s+l['’ ]?obligation\s+d['’ ]?emploi|"
    r"contrat\s+tremplin|"
    r"uniquement\s+pour\s+(les\s+)?personnes?\s+(en\s+situation\s+de\s+)?handicap"
    r")"
)


def is_handicap_targeted(title: str = "", employer: str = "", extra_text: str = "", site: str = "") -> bool:
    """Détecte si une offre est spécifiquement réservée ou destinée aux personnes en situation de handicap (RQTH).

    Renvoie True pour :
    - Les offres avec RQTH, handicap, handi-stage, tremplin dans le titre.
    - Les organismes spécialisés et entreprises adaptées (Groupe Talents Handicap, Tremplin, Agefiph, Arpejeh, ESAT, etc.).
    - Tout employeur ou site portant la mention 'handicap' ou 'handi'.
    - Les offres dont la description stipule expressément un poste réservé/exclusif RQTH ou BOETH.

    Renvoie False pour les offres normales avec simple mention légale inclusive
    ('à compétences égales, poste ouvert aux personnes en situation de handicap').
    """
    t = norm_text(title)
    emp = f"{norm_text(employer)} {norm_text(site)}".strip()
    extra = norm_text(extra_text)

    if _HANDICAP_TITLE_RE.search(t):
        return True
    if _HANDICAP_EMPLOYER_RE.search(emp):
        return True
    if extra and _HANDICAP_DESC_RE.search(extra):
        return True
    return False


def is_cyber_offer(title: str, employer: str = "", extra_text: str = "") -> bool:
    """Vrai si l'offre appartient bien au domaine cybersécurité / sécurité IT / infra-sécu."""
    t = norm_text(title)
    emp = norm_text(employer)
    extra = norm_text(extra_text)

    if is_handicap_targeted(title, employer, extra_text):
        return False

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


# ---------------------------------------------------------------------------
# Scoring rapide /10 aligné sur le profil candidat (AD/Entra ID, Cyber, Infra)
# ---------------------------------------------------------------------------
_SCORE_DOMAINS: list[tuple[str, str]] = [
    ("AD / IAM", r"active directory|entra|entra id|azure ad|microsoft 365|m365|iam|pam|ldap|intune|cisco ise|access control|zero trust|identity"),
    ("SOC / Blue team", r"soc|siem|csirt|cert|blue team|edr|xdr|splunk|qradar|sentinel|threat|incident|dfir|detection"),
    ("Pentest / Offensive", r"pentest|pentester|red team|offensive|intrusion|vulnerabilit|schwachstellen|bug bounty|oscp|appsec|owasp"),
    ("Réseau / Infra sécu", r"network|reseau|firewall|pare-feu|fortinet|palo alto|f5|vpn|ids|ips|nac|infra|system|observability"),
    ("DevSecOps / Cloud", r"devsecops|devops|cloud|aws|azure|gcp|kubernetes|docker|terraform|ansible|automation|securing"),
    ("GRC / Audit IT", r"grc|gouvernance|governance|iso 27001|27001|ebios|nis2|dora|risk|risque|audit|compliance|conformite|ciso|resilience|resiliens"),
    ("Forensic / Malware", r"forensic|forensique|malware|reverse engineering|cryptograph|privacy"),
]


def score_cyber_stage(offer: dict) -> tuple[int, list[str], str]:
    """Calcule instantanément (fit_score /10, domaines, reasoning) sans blocage LLM.

    Barème aligné sur scorer.py (profil ingénieur INSA : Cybersécurité, AD/Entra ID,
    Intégration IT, Réseau/Infra sécu, SOC, Pentest, GRC, DevSecOps).
    """
    title = offer.get("title") or ""
    employer = offer.get("employer_name") or ""
    desc = (offer.get("description") or "")[:1500]
    t = norm_text(title)
    full = f"{t} {norm_text(employer)} {norm_text(desc)}"

    domains: list[str] = []
    for label, pattern in _SCORE_DOMAINS:
        if re.search(rf"(?<![a-z0-9])({pattern})(?![a-z0-9])", full):
            domains.append(label)

    has_core_cyber = bool(re.search(
        r"(?<![a-z0-9])(cyber|cybersecurite|cybersecurity|infosec|information security|it security|ncsc|ciso|sakerhet|sicherheit)(?![a-z0-9])",
        f"{t} {norm_text(employer)}",
    ))
    has_ad_iam = "AD / IAM" in domains
    has_tech_sec = any(d in domains for d in ("SOC / Blue team", "Pentest / Offensive", "Réseau / Infra sécu", "DevSecOps / Cloud"))
    has_grc_only = domains == ["GRC / Audit IT"]

    # Base de score selon adéquation technique au profil
    score = 7
    if has_ad_iam:
        score = 10
    elif has_core_cyber and has_tech_sec:
        score = 9
    elif has_core_cyber:
        score = 9
    elif has_tech_sec:
        score = 8
    elif has_grc_only:
        score = 8
    elif domains:
        score = 7

    # Ajustement léger selon le format (stage direct vs working student / graduate)
    is_direct_stage = bool(re.search(
        r"(?<![a-z])(stage|stagiaire|intern|internship|praktik|praktikum|hochschulpraktikant|praticante|exjobb|thesis)(?![a-z])",
        t,
    ))
    is_working_student = bool(re.search(r"(?<![a-z])(working student|werkstudent|studentische)(?![a-z])", t))
    is_graduate = bool(re.search(r"(?<![a-z])(graduate|trainee)(?![a-z])", t)) and not is_direct_stage

    notes: list[str] = []
    if is_working_student:
        score = max(6, score - 1)
        notes.append("format Working Student (temps partiel à vérifier avec ta convention)")
    elif is_graduate:
        score = max(7, score - 1)
        notes.append("format Graduate / Trainee (vérifier l'éligibilité stage PFE / pré-embauche)")

    if not domains and has_core_cyber:
        domains = ["Cybersécurité"]
    elif not domains:
        domains = ["Cybersécurité / IT"]

    score = max(1, min(10, score))

    # Génération d'une explication claire en français
    dom_str = ", ".join(domains[:3])
    if has_ad_iam:
        reason = f"Très forte adéquation : stage ciblant directement {dom_str}, cœur de ton profil cybersécurité & intégration IT."
    elif score >= 9:
        reason = f"Excellent stage en cybersécurité ({dom_str}), parfaitement aligné avec ton profil ingénieur."
    elif score == 8:
        reason = f"Bon stage orienté {dom_str}, pertinent pour ton parcours cybersécurité."
    else:
        reason = f"Stage technique en lien avec {dom_str}."

    if notes:
        reason += " " + notes[0].capitalize() + "."

    return score, domains, reason


def filter_cyber_internships(
    offers: list[dict],
    custom_query: str | None = None,
    limit: int = 12,
) -> list[dict]:
    """Filtre, déduplique, note (/10) et trie une liste d'offres pour ne garder que les stages cybersécurité.

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

        if is_handicap_targeted(title, employer, desc):
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

        score, domains, reason = score_cyber_stage(o)
        enriched = dict(o)
        enriched["fit_score"] = score
        enriched["domaines"] = domains
        enriched["score_reasoning"] = reason
        kept.append(enriched)

    # Trier les meilleurs scores /10 en tête
    kept.sort(key=lambda x: x.get("fit_score", 0), reverse=True)
    return kept[:limit]


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

                if is_handicap_targeted(title, comp, desc):
                    continue

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
