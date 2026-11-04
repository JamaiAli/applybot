"""Job fit scoring: LLM-powered evaluation of candidate-job match quality.

Scores jobs on a 1-10 scale by comparing the user's resume against each
job description. All personal data is loaded at runtime from the user's
profile and resume file.
"""

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from applypilot.config import RESUME_PATH, load_profile
from applypilot.database import get_connection, get_jobs_by_stage, write_with_retry
from applypilot.llm import get_client

log = logging.getLogger(__name__)


# ── Scoring Prompt ────────────────────────────────────────────────────────

SCORE_PROMPT_TEMPLATE = """Tu es un évaluateur d'adéquation entre un candidat et une offre, spécialisé dans les **stages**. Tu compares le CV du candidat à une offre et tu notes l'adéquation de 1 à 10.

LE CANDIDAT : {candidate_summary}

⚠️ VÉRIFICATION GÉOGRAPHIQUE — À FAIRE EN PREMIER :
Le candidat cherche un stage **en France** : sur site en France, ou en télétravail/hybride ouvert à la France (ou à l'Europe / EMEA, qui incluent la France). Une offre restreinte à une zone géographique qui EXCLUT la France est hors zone, même en télétravail.
Lis TOUTE la description pour repérer des restrictions enfouies (« must be based in the US », « right to work in the UK », « Remote — APAC/LATAM/India »).

Renseigne ELIGIBILITY: hors_zone quand le poste est limité à un pays/une région hors de portée depuis la France (US-only, UK-only, Canada-only, APAC, LATAM, Inde, etc.), même à distance. Sinon ELIGIBILITY: eligible (France, Europe/EMEA, francophone, télétravail ouvert à la France, ou aucune restriction).
Si hors_zone, tu PEUX quand même donner un SCORE sur l'adéquation technique (pour l'audit), mais c'est le tag d'éligibilité qui décide si la candidature continue.

CRITÈRES DE SCORE (adéquation avec un stage en intégration IT & cybersécurité, idéalement autour d'Active Directory / Entra ID) :
- 10 : stage (ou alternance) quasi parfait — cybersécurité, intégration IT, administration systèmes, Active Directory / Entra ID, cloud/infrastructure, ou gestion de projet IT. Niveau adapté à un étudiant ingénieur (Bac+4/5). Durée compatible avec un stage de ~6 mois.
- 9 : excellent stage IT/cyber, très aligné, 1-2 écarts de techno secondaire.
- 7-8 : bon stage IT/cyber. Le candidat a la plupart des compétences attendues, écarts mineurs faciles à combler.
- 5-6 : stage technique mais domaine éloigné (dev pur front, data science) OU niveau mal adapté.
- 3-4 : poste technique mais PAS un stage (CDI/CDD junior ou confirmé), ou stage peu lié à l'IT/cyber.
- 1-2 : hors cible (commercial, avant-vente, marketing, RH, design, poste senior/management sans composante technique junior), poste non technique, OU hors zone géographique.

RÈGLES COMPLÉMENTAIRES :
- Le candidat cherche un STAGE (~6 mois). Un poste senior/confirmé ou de management sans dimension junior/stage → 3 max.
- Les postes non techniques (commercial, avant-vente, marketing, RH, design, recrutement) → 1-2 quel que soit le reste.
- Valorise : Active Directory, Entra ID, Azure/M365, intégration IT, infrastructure, réseau, sécurité, PowerShell, scripting, virtualisation, gestion de projet.
- Distingue compétences REQUISES et SOUHAITÉES ; ne pénalise que l'absence de compétences requises.
- Valorise l'expérience transférable (projets, administration système, sécurité, cloud) même acquise en formation ou en projet étudiant.

Tu DOIS produire les quatre lignes ci-dessous. Ne saute pas REASONING.

ELIGIBILITY: [eligible|hors_zone]
SCORE: [1-10]
KEYWORDS: [mots-clés ATS de l'offre, séparés par des virgules, qui correspondent ou pourraient correspondre au candidat]
REASONING: [2-3 phrases expliquant la note, ce qui correspond et les écarts. Si hors_zone, nomme le pays/la région.]"""


# ── Pré-filtre basé sur des règles (évite un appel LLM pour les cas évidents) ─
#
# Marché français, recherche de STAGE. On ne rejette que les cas clairs :
#   - rôle manifestement hors cible (non technique : commercial, RH, design…),
#   - offre restreinte à une zone géographique qui EXCLUT la France.
# L'Europe / EMEA incluent la France → NON rejetés. Le LLM tranche le reste.
# Contrairement à la version US d'origine, les titres « stage / junior /
# alternance / graduate » ne sont PLUS rejetés : ce sont les cibles du candidat.

# Rôles clairement hors cible pour un stage en intégration IT & cybersécurité.
# Vérifié sur le TITRE uniquement.
_OFF_TARGET_TITLE = re.compile(
    r'\bSales Engineer\b|\bSolutions? Engineer\b|\bPre[- ]?[Ss]ales\b'
    r'|\bCustomer Success\b|\bAccount Manager\b|\bAccount Executive\b'
    r'|\bRecruiter\b|\bTalent Acquisition\b|\bTalent Sourcer\b'
    r'|\bUX Designer\b|\bUI Designer\b|\bProduct Designer\b|\bGraphic Designer\b'
    r'|\bCommerciale?\b|\bBusiness Developer\b|\bVendeur\b|\bVendeuse\b'
    r'|\bCaissi[èe]re?\b|\bCashier\b|\bBaker\b|\bButcher\b'
    r'|\bStocker\b|\bWarehouse Associate\b|\bChargé\(?e?\)? de recrutement\b',
    re.IGNORECASE,
)

# Marqueurs de région, dans le TITRE, qui excluent la France.
# (EMEA / Europe NON listés : ils incluent la France.)
_OUT_OF_ZONE_TITLE = re.compile(
    r'\bAPAC\b|\bLATAM\b|\bMENA\b|\bANZ\b|\bTOLA\b|\bNordics?\b'
    r'|\bUS[- ]only\b|\bUSA?[- ]only\b|\bUK[- ]only\b|\bU\.S\.[- ]only\b'
    r'|\bOnly hiring in\b',
    re.IGNORECASE,
)

# Indices « France / zone atteignable » qui ANNULENT un rejet géographique.
# Pas de \bEU\b / \bUE\b (collision avec « eu » / « ue »), ni « remote » seul.
_FRANCE_HINT = re.compile(
    r'\bFrance\b|\bfran[cç]\w*\b'                       # France, français, francophone
    r'|\bParis\b|\bLyon\b|\bMarseille\b|\bToulouse\b|\bLille\b|\bBordeaux\b'
    r'|\bNantes\b|\bStrasbourg\b|\bRennes\b|\bGrenoble\b|\bMontpellier\b'
    r'|\bOrl[ée]ans\b|\bBourges\b|\bBlois\b|\bCh[âa]teauroux\b'
    r'|\bEurope\b|\bEMEA\b|\bt[ée]l[ée]travail\b',
    re.IGNORECASE,
)

# Codes d'État américains en fin de localisation (« Seattle, WA »).
# SENSIBLE À LA CASSE : on ne veut PAS matcher « or », « in », « me »… en minuscules.
_US_STATE_CODE = re.compile(
    r',\s?(?:A[LKZR]|C[AOT]|DE|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]'
    r'|N[CDEHJMVY]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[AT]|W[AIVY]|DC)\b'
)

# Pays / villes clairement hors zone, par nom (localisation).
_OUT_OF_ZONE_LOCATION = re.compile(
    r'\bUnited States\b|\bUSA\b|\bU\.S\.A?\.?\b'
    r'|\bUnited Kingdom\b|\bLondon\b'
    r'|\bIndia\b|\bBangalore\b|\bBengaluru\b|\bHyderabad\b|\bPune\b'
    r'|\bSingapore\b|\bCanada\b|\bToronto\b|\bAustralia\b|\bNew Zealand\b'
    r'|\bBrazil\b|\bBrasil\b|\bMexico\b|\bArgentina\b'
    r'|\bJapan\b|\bChina\b|\bKorea\b|\bPhilippines\b|\bIndonesia\b|\bVietnam\b'
    r'|\bUAE\b|\bDubai\b|\bSaudi\b|\bIsrael\b|\bTel Aviv\b'
    r'|\bGermany\b|\bBerlin\b|\bMunich\b|\bSpain\b|\bMadrid\b|\bBarcelona\b'
    r'|\bItaly\b|\bMilan\b|\bNetherlands\b|\bAmsterdam\b'
    r'|\bSeattle\b|\bNew York\b|\bSan Francisco\b|\bAustin\b|\bBoston\b'
    r'|\bChicago\b|\bAtlanta\b|\bDenver\b|\bDallas\b',
    re.IGNORECASE,
)

# Restrictions explicites dans la DESCRIPTION (autorisation de travail hors France).
_OUT_OF_ZONE_DESC = re.compile(
    r'right\s+to\s+work\s+in\s+(the\s+)?(United\s+States|US|USA|U\.S\.|UK|United\s+Kingdom|Canada|India|Australia|Ireland)'
    r'|authori[sz]ed\s+to\s+work\s+in\s+the\s+(US|United\s+States|UK|United\s+Kingdom)'
    r'|must\s+be\s+(located|based|residing)\s+in\s+(the\s+)?(United\s+States|US|USA|UK|United\s+Kingdom|Canada|India|Australia)'
    r'|US[- ]based\s+(only|position|role|candidates)'
    r'|Remote\s*[\(\-—–]\s*(APAC|LATAM|MENA|India|US|USA|United\s+States|Canada|UK|United\s+Kingdom)',
    re.IGNORECASE,
)

# Fenêtre scannée pour les motifs au niveau de la description.
_DESC_SCAN_CHARS = 6000


def _check_ineligible(job: dict) -> str | None:
    """Renvoie une raison d'inéligibilité si l'offre est clairement hors cible
    ou hors zone (géographie excluant la France), sinon None.

    Appelé avant le LLM pour économiser des tokens. Ne rejette jamais une offre
    qui mentionne la France / l'Europe / le télétravail, ni un titre de stage.
    """
    title = job.get("title") or ""
    location = job.get("location") or ""
    desc_head = (job.get("full_description") or "")[:_DESC_SCAN_CHARS]

    if _OFF_TARGET_TITLE.search(title):
        return f"hors cible (rôle non technique) : {title[:80]}"

    if _OUT_OF_ZONE_TITLE.search(title) and not _FRANCE_HINT.search(title):
        return f"hors zone (titre) : {title[:80]}"

    if location and not _FRANCE_HINT.search(location) and (
        _US_STATE_CODE.search(location) or _OUT_OF_ZONE_LOCATION.search(location)
    ):
        return f"hors zone (localisation) : {location[:80]}"

    if not _FRANCE_HINT.search(desc_head):
        m = _OUT_OF_ZONE_DESC.search(desc_head)
        if m:
            return f"hors zone (description) : {m.group(0)[:80]}"

    return None


def _parse_score_response(response: str) -> dict:
    """Parse the LLM's score response into structured data.

    Args:
        response: Raw LLM response text.

    Returns:
        {"score": int, "keywords": str, "reasoning": str, "eligibility": str}
        eligibility is one of: 'eligible' | 'hors_zone'.
        Older models that omit ELIGIBILITY default to 'eligible' (preserves
        prior behavior; new prompt requires the line so absence is rare).
    """
    score = 0
    keywords = ""
    reasoning = response
    eligibility = "eligible"

    for line in response.split("\n"):
        line = line.strip()
        if line.startswith("ELIGIBILITY:"):
            value = line.replace("ELIGIBILITY:", "").strip().lower()
            value = re.sub(r"[\[\]\s]+", "_", value).strip("_")
            if "hors" in value or "zone" in value or ("non" in value and "us" in value):
                eligibility = "hors_zone"
            else:
                eligibility = "eligible"
        elif line.startswith("SCORE:"):
            try:
                score = int(re.search(r"\d+", line).group())
                score = max(1, min(10, score))
            except (AttributeError, ValueError):
                score = 0
        elif line.startswith("KEYWORDS:"):
            keywords = line.replace("KEYWORDS:", "").strip()
        elif line.startswith("REASONING:"):
            reasoning = line.replace("REASONING:", "").strip()

    return {"score": score, "keywords": keywords, "reasoning": reasoning,
            "eligibility": eligibility}


def _build_candidate_summary(profile: dict) -> str:
    """Construit un résumé candidat (FR) à partir du profil, pour le scoring.

    Lit le profil avec des valeurs de repli pour un étudiant en recherche de
    stage ; les champs dédiés au stage seront formalisés dans le schéma de profil.
    """
    exp = profile.get("experience", {}) or {}
    boundary = profile.get("skills_boundary", {}) or {}
    stage = profile.get("internship", {}) or profile.get("stage", {}) or {}
    personal = profile.get("personal", {}) or {}

    level = (exp.get("current_job_title") or exp.get("education_level")
             or "Étudiant ingénieur en cybersécurité (Bac+4/5)")
    school = exp.get("school") or personal.get("school") or ""
    target = exp.get("target_role") or "stage en intégration IT & cybersécurité"
    all_skills: list[str] = []
    for items in boundary.values():
        if isinstance(items, list):
            all_skills.extend(s for s in items if s)

    head = level + (f" à {school}" if school else "")
    parts = [f"{head}, recherche un {target}."]

    duration = stage.get("duration") or "6 mois"
    start = stage.get("start_date") or ""
    parts.append(f"Stage de {duration}{(' à partir de ' + start) if start else ''}.")

    if all_skills:
        parts.append(f"Compétences : {', '.join(all_skills[:14])}.")
    return " ".join(parts)


def score_job(resume_text: str, job: dict, profile: dict | None = None) -> dict:
    """Score a single job against the resume.

    Args:
        resume_text: The candidate's full resume text.
        job: Job dict with keys: title, site, location, full_description.

    Returns:
        {"score": int, "keywords": str, "reasoning": str}
    """
    if profile is None:
        profile = load_profile()

    # Pré-filtre : écarte les offres clairement hors cible / hors zone avant
    # l'appel LLM. Tag eligibility=hors_zone → les étapes suivantes l'ignorent ;
    # le score est tout de même enregistré pour l'audit.
    ineligible_reason = _check_ineligible(job)
    if ineligible_reason:
        log.info("Pré-filtre HORS ZONE/CIBLE : %s — %s", (job.get("title") or "?")[:60], ineligible_reason)
        return {
            "score": 2,
            "keywords": "",
            "reasoning": f"Inéligible : {ineligible_reason}.",
            "eligibility": "hors_zone",
        }

    try:
        candidate_summary = _build_candidate_summary(profile)
        score_prompt = SCORE_PROMPT_TEMPLATE.format(candidate_summary=candidate_summary)

        job_text = (
            f"TITLE: {job['title']}\n"
            f"COMPANY: {job['site']}\n"
            f"LOCATION: {job.get('location', 'N/A')}\n\n"
            f"DESCRIPTION:\n{(job.get('full_description') or '')[:2500]}"
        )

        messages = [
            {"role": "system", "content": score_prompt},
            {"role": "user", "content": f"RESUME:\n{resume_text}\n\n---\n\nJOB POSTING:\n{job_text}"},
        ]

        client = get_client()
        # Scoring output is ~4 short lines : un petit budget suffit et reste
        # compatible avec les plafonds de jetons/minute des paliers gratuits.
        response = client.chat(messages, max_tokens=2048, temperature=0.2)
        return _parse_score_response(response)
    except Exception as e:
        log.error("LLM error scoring job '%s': %s", (job or {}).get("title") or "?", e)
        return {"score": None, "keywords": "", "reasoning": "", "eligibility": None,
                "error": f"LLM error: {e}"}


MAX_SCORE_RETRIES = 5


def _score_backoff_minutes(retry_count: int) -> int:
    """Exponential backoff for scoring retries: 5, 20, 80, ~5h, ~21h."""
    return min(5 * (4 ** retry_count), 24 * 60)


def _flush_score_batch(conn, batch: list[dict], now: str) -> None:
    """Write a batch of scoring results to the DB.

    On success (score is not None): writes fit_score, clears score_error.
    On failure (score is None): leaves fit_score NULL, writes score_error + backoff.
    Jobs that have already hit MAX_SCORE_RETRIES stay unscored indefinitely (manual rescue needed).
    """
    from applypilot.database import transition_state
    from applypilot.config import DEFAULTS as _cfg_DEFAULTS
    _min_score = _cfg_DEFAULTS["min_score"]

    for r in batch:
        if r["score"] is not None:
            eligibility = r.get("eligibility") or "eligible"
            conn.execute(
                "UPDATE jobs SET fit_score = ?, score_reasoning = ?, scored_at = ?, "
                "eligibility = ?, "
                "score_error = NULL, score_attempts = 0, score_next_retry_at = NULL "
                "WHERE url = ?",
                (r["score"], f"{r['keywords']}\n{r['reasoning']}", now, eligibility,
                 r["url"]),
            )
            # Transition pilotée par l'éligibilité. Les offres hors zone/cible
            # vont directement en `archived` (terminal) : tailor/cover/apply ne
            # les reprennent jamais, court-circuitant la chaîne habituelle.
            if eligibility == "hors_zone":
                transition_state(conn, r["url"], "archived",
                                 reason="offre hors zone / hors cible",
                                 metadata={"score": r["score"],
                                           "eligibility": eligibility},
                                 force=True)
            else:
                to_state = "scored" if r["score"] >= _min_score else "low_score"
                transition_state(conn, r["url"], to_state,
                                 reason=f"scored {r['score']}/10",
                                 metadata={"score": r["score"],
                                           "eligibility": eligibility})
        else:
            # LLM failure — keep fit_score NULL so it stays in pending_score
            row = conn.execute(
                "SELECT COALESCE(score_attempts, 0) FROM jobs WHERE url = ?", (r["url"],)
            ).fetchone()
            retry_count = row[0] if row else 0
            if retry_count >= MAX_SCORE_RETRIES:
                # Give up — write score_error but don't schedule another retry
                conn.execute(
                    "UPDATE jobs SET score_error = ?, score_attempts = ?, "
                    "score_next_retry_at = NULL, scored_at = ? WHERE url = ?",
                    (r["error"], retry_count + 1, now, r["url"]),
                )
                transition_state(conn, r["url"], "score_failed",
                                 reason=f"LLM failed after {retry_count+1} attempts",
                                 metadata={"error": r["error"]})
            else:
                delay = _score_backoff_minutes(retry_count)
                next_retry = (
                    datetime.now(timezone.utc) + timedelta(minutes=delay)
                ).isoformat()
                conn.execute(
                    "UPDATE jobs SET score_error = ?, score_attempts = ?, "
                    "score_next_retry_at = ?, scored_at = ? WHERE url = ?",
                    (r["error"], retry_count + 1, next_retry, now, r["url"]),
                )
                log.info("  score retry %d/%d scheduled in %d min for %s",
                         retry_count + 1, MAX_SCORE_RETRIES, delay, r["url"][:60])


def run_scoring(limit: int = 0, rescore: bool = False, workers: int = 1,
                max_age_days: int | None = None) -> dict:
    """Score unscored jobs that have full descriptions.

    Args:
        limit: Maximum number of jobs to score in this run.
        rescore: If True, re-score all jobs (not just unscored ones).
        workers: Parallel LLM threads (default 1 = sequential).

    Returns:
        {"scored": int, "errors": int, "elapsed": float, "distribution": list}
    """
    resume_text = RESUME_PATH.read_text(encoding="utf-8")
    conn = get_connection()

    if rescore:
        query = "SELECT * FROM jobs WHERE full_description IS NOT NULL"
        if limit > 0:
            query += f" LIMIT {limit}"
        jobs = conn.execute(query).fetchall()
    else:
        # Note: get_jobs_by_stage now applies a 14-day discovered_at filter by
        # default (config.DEFAULTS["max_job_age_days"]). Pass max_age_days=0
        # to disable.
        jobs = get_jobs_by_stage(conn=conn, stage="pending_score",
                                 max_age_days=max_age_days, limit=limit)

    if not jobs:
        log.info("No unscored jobs with descriptions found.")
        return {"scored": 0, "errors": 0, "elapsed": 0.0, "distribution": []}

    # Convert sqlite3.Row to dicts if needed
    if jobs and not isinstance(jobs[0], dict):
        columns = jobs[0].keys()
        jobs = [dict(zip(columns, row)) for row in jobs]

    log.info("Scoring %d jobs (workers=%d)...", len(jobs), workers)
    # Pause entre appels (séquentiel) pour rester sous la limite jetons/min de
    # Groq (palier gratuit) et éviter de tomber sur Gemini (503). Réglable via
    # SCORE_DELAY_SECONDS (0 = pas de pause).
    import os as _os
    _delay = float(_os.environ.get("SCORE_DELAY_SECONDS", "4"))
    t0 = time.time()
    completed = 0
    errors = 0
    batch_size = 25  # Commit every N jobs so downstream stages see results sooner
    batch: list[dict] = []

    def _score_one(job: dict) -> dict:
        try:
            result = score_job(resume_text, job)
            result["url"] = job["url"]
        except Exception as e:
            log.error("Unexpected error scoring '%s': %s", (job or {}).get("title") or "?", e)
            result = {
                "score": None, "keywords": "", "reasoning": "",
                "error": f"Unexpected: {e}", "url": (job or {}).get("url", ""),
            }
        return result

    def _flush_and_log(batch: list[dict], completed: int) -> list[dict]:
        now = datetime.now(timezone.utc).isoformat()
        try:
            write_with_retry(conn, _flush_score_batch, conn, batch, now)
        except Exception as flush_err:
            log.exception("Batch flush failed (batch of %d): %s", len(batch), flush_err)
        log.info("Committed batch of %d scores to DB (%d/%d total)", len(batch), completed, len(jobs))
        return []

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_score_one, job): job for job in jobs}
            for future in as_completed(futures):
                job = futures[future]
                result = future.result()
                completed += 1
                if result["score"] is None:
                    errors += 1
                batch.append(result)
                log.info(
                    "[%d/%d] score=%s  %s",
                    completed, len(jobs),
                    result["score"] if result["score"] is not None else "ERR",
                    (job.get("title") or "")[:60],
                )
                if len(batch) >= batch_size:
                    batch = _flush_and_log(batch, completed)
    else:
        from applypilot import config as _cfg
        for job in jobs:
            # Priorité LLM : si une tâche prioritaire (ex. accroche de CV demandée
            # en direct) utilise le LLM, on met le scoring en pause le temps qu'il
            # passe (auto-expiration du verrou -> jamais bloqué indéfiniment).
            _waited_prio = 0
            while _cfg.llm_priority_active() and _waited_prio < 130:
                time.sleep(2)
                _waited_prio += 2
            result = _score_one(job)
            completed += 1
            if result["score"] is None:
                errors += 1
            batch.append(result)
            log.info(
                "[%d/%d] score=%s  %s",
                completed, len(jobs),
                result["score"] if result["score"] is not None else "ERR",
                (job.get("title") or "")[:60],
            )
            if len(batch) >= batch_size:
                batch = _flush_and_log(batch, completed)
            if _delay > 0:
                time.sleep(_delay)

    # Flush remaining
    if batch:
        now = datetime.now(timezone.utc).isoformat()
        try:
            write_with_retry(conn, _flush_score_batch, conn, batch, now)
        except Exception as flush_err:
            log.exception("Final batch flush failed (batch of %d): %s", len(batch), flush_err)

    elapsed = time.time() - t0
    log.info("Done: %d scored in %.1fs (%.1f jobs/sec)", completed, elapsed, completed / elapsed if elapsed > 0 else 0)

    # Score distribution
    try:
        dist = conn.execute("""
            SELECT fit_score, COUNT(*) FROM jobs
            WHERE fit_score IS NOT NULL
            GROUP BY fit_score ORDER BY fit_score DESC
        """).fetchall()
        distribution = [(row[0], row[1]) for row in dist]
    except Exception as dist_err:
        log.exception("Distribution query failed: %s", dist_err)
        distribution = []

    return {
        "scored": completed,
        "errors": errors,
        "elapsed": elapsed,
        "distribution": distribution,
    }
