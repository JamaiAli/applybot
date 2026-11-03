"""Auto-apply (Phase 4b) — pré-remplissage de candidature piloté, sans triche.

Respecte les règles d'Ali :
  * Les connexions aux sites sont faites par LUI, une seule fois, dans un
    **profil navigateur persistant** (~/.applypilot/browser_profile). L'agent ne
    crée jamais de compte et ne saisit jamais de mot de passe.
  * **Aucun contournement de CAPTCHA** : dès qu'un CAPTCHA ou une page de
    connexion apparaît, l'agent S'ARRÊTE et renvoie le lien.
  * **Aucune soumission sans ✅** : par défaut l'agent remplit et téléverse le
    CV puis S'ARRÊTE avant l'envoi (submit=False) ; l'utilisateur vérifie et
    clique Envoyer. submit=True n'est utilisé que sur son feu vert explicite.

Flux typique des offres Adzuna FR : page Adzuna -> redirection (ex. Wizbii) ->
formulaire de l'employeur. Le moteur suit ces sauts, repère le formulaire,
remplit les champs connus depuis profile.json et joint le CV.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from applypilot import config

log = logging.getLogger("applypilot.autoapply")

BROWSER_PROFILE_DIR = config.APP_DIR / "browser_profile"
SHOTS_DIR = config.APP_DIR / "apply_shots"

# Indices d'une page de connexion / création de compte (on s'arrête).
_LOGIN_RE = re.compile(
    r"(account\.|/sign-?in|/sign-?up|/login|/connexion|/inscription|/register)",
    re.I)
# Indices de CAPTCHA (on s'arrête).
_CAPTCHA_HOST_RE = re.compile(r"(recaptcha|hcaptcha|turnstile|captcha)", re.I)


def _profile() -> dict:
    return json.loads((config.APP_DIR / "profile.json").read_text(encoding="utf-8"))


def _field_values(p: dict) -> list[tuple[re.Pattern, str]]:
    """Table (motif de champ -> valeur) à partir de profile.json."""
    pers = p.get("personal", {})
    full = pers.get("full_name", "")
    first = pers.get("preferred_name") or (full.split()[0] if full else "")
    last = " ".join(full.split()[1:]) if len(full.split()) > 1 else ""
    pairs = {
        r"(pr[ée]nom|first[\s_-]*name|given)": first,
        r"(nom(?![\s_-]*utilisateur)|last[\s_-]*name|surname|family)": last,
        r"(e[\s_-]?mail|courriel)": pers.get("email", ""),
        r"(t[ée]l[ée]phone|phone|mobile|portable)": pers.get("phone", ""),
        r"(ville|city|localit[ée])": pers.get("city", ""),
        r"(code[\s_-]*postal|postal[\s_-]*code|zip)": pers.get("postal_code", ""),
        r"(linkedin)": pers.get("linkedin_url", ""),
        r"(github)": pers.get("github_url", ""),
        r"(portfolio|site[\s_-]*web|website|site perso)": pers.get("portfolio_url", ""),
    }
    return [(re.compile(k, re.I), v) for k, v in pairs.items() if v]


# ---------------------------------------------------------------------------
def _looks_like_login(url: str, page) -> bool:
    if _LOGIN_RE.search(url or ""):
        return True
    try:
        # champ mot de passe présent et aucun champ fichier -> probablement login
        has_pw = page.locator("input[type='password']").count() > 0
        has_file = page.locator("input[type='file']").count() > 0
        return has_pw and not has_file
    except Exception:
        return False


def _has_captcha(page) -> bool:
    try:
        html = page.content()
    except Exception:
        return False
    if _CAPTCHA_HOST_RE.search(html):
        return True
    try:
        for fr in page.frames:
            if _CAPTCHA_HOST_RE.search(fr.url or ""):
                return True
    except Exception:
        pass
    return False


def _follow_to_employer(page, offer: dict) -> str:
    """Depuis la page Adzuna, suit « Postuler » jusqu'au site employeur.
    Renvoie l'URL finale."""
    start = offer.get("application_url") or offer.get("url")
    page.goto(start, wait_until="domcontentloaded", timeout=45000)
    # Lien « Postuler » d'Adzuna -> page /land/ -> redirection JS employeur.
    try:
        link = page.locator("a:has-text('Postuler')").first
        href = link.get_attribute("href", timeout=5000)
    except Exception:
        href = None
    if href:
        page.goto(href, wait_until="domcontentloaded", timeout=45000)
        # la page /land/ redirige en ~5 s vers l'employeur
        try:
            page.wait_for_url(lambda u: "adzuna." not in u, timeout=15000)
        except Exception:
            time.sleep(5)
    return page.url


def _fill_known_fields(page, p: dict) -> int:
    """Remplit les champs texte connus restés vides. Renvoie le nb rempli."""
    pairs = _field_values(p)
    filled = 0
    try:
        inputs = page.locator(
            "input[type='text'], input[type='email'], input[type='tel'], "
            "input:not([type]), textarea")
        n = min(inputs.count(), 60)
    except Exception:
        return 0
    for i in range(n):
        el = inputs.nth(i)
        try:
            if not el.is_visible() or not el.is_editable():
                continue
            if (el.input_value() or "").strip():
                continue  # ne pas écraser
            meta = " ".join(filter(None, [
                el.get_attribute("name"), el.get_attribute("id"),
                el.get_attribute("placeholder"), el.get_attribute("aria-label")]))
            meta = (meta or "").lower()
            for pat, val in pairs:
                if pat.search(meta):
                    el.fill(val, timeout=3000)
                    filled += 1
                    break
        except Exception:
            continue
    return filled


def _upload_cv(page, cv_path: str) -> bool:
    try:
        files = page.locator("input[type='file']")
        if files.count() == 0:
            return False
        files.first.set_input_files(cv_path, timeout=5000)
        return True
    except Exception as exc:
        log.debug("Upload CV échoué : %s", exc)
        return False


def _screenshot(page, offer: dict) -> str | None:
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    import hashlib
    h = hashlib.md5((offer.get("url") or "x").encode()).hexdigest()[:8]
    path = SHOTS_DIR / f"apply_{h}.png"
    try:
        page.screenshot(path=str(path), full_page=False)
        return str(path)
    except Exception:
        return None


def _fill_and_report(page, offer: dict, cv_path: str | None) -> dict:
    """Remplit les champs connus + joint le CV, renvoie un résultat 'filled'/'no_form'."""
    filled = _fill_known_fields(page, _profile())
    uploaded = bool(cv_path) and _upload_cv(page, cv_path)
    if filled == 0 and not uploaded:
        return {"status": "no_form", "url": page.url,
                "message": "Pas de formulaire reconnu ici — fais-le à la main.",
                "screenshot": _screenshot(page, offer)}
    return {"status": "filled", "url": page.url,
            "message": f"Formulaire pré-rempli ({filled} champs), CV "
                       f"{'joint ✅' if uploaded else 'à téléverser toi-même'}. "
                       "Vérifie, complète si besoin, puis clique Postuler.",
            "screenshot": _screenshot(page, offer)}


def _wait_login_then_fill(page, offer: dict, cv_path: str | None, timeout: int) -> dict:
    """Après détection d'une connexion : attend qu'Ali se connecte DANS la fenêtre,
    puis ouvre le formulaire et le remplit automatiquement (sans re-cliquer)."""
    waited = 0
    while waited < timeout:
        try:
            if page.is_closed():
                break
            time.sleep(3)
            waited += 3
            if _looks_like_login(page.url, page):
                continue  # toujours sur la page de connexion
            # Connecté : tenter d'ouvrir le formulaire puis remplir.
            try:
                btn = page.locator(
                    "a:has-text('Postuler'), button:has-text('Postuler')").first
                if btn.count() > 0:
                    btn.click(timeout=4000)
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            if _looks_like_login(page.url, page):
                continue
            res = _fill_and_report(page, offer, cv_path)
            if res["status"] == "filled":
                return res
        except Exception:
            continue
    return {"status": "needs_login", "url": page.url if not page.is_closed() else None,
            "message": "En attente de connexion dans la fenêtre…", "screenshot": None}


def _run_flow(page, offer: dict, cv_path: str | None) -> dict:
    """Atteint le formulaire, détecte connexion/CAPTCHA, remplit + joint le CV.
    Ne soumet JAMAIS (l'envoi reste manuel). Renvoie le dict de résultat."""
    result = {"status": "error", "url": None, "message": "", "screenshot": None}
    final = _follow_to_employer(page, offer)

    # Sur la page employeur (ex. Wizbii) : déclencher « Postuler » pour ouvrir
    # le formulaire (si un simple lien/bouton le fait).
    try:
        btn = page.locator(
            "a:has-text('Postuler'), button:has-text('Postuler')").first
        if btn.count() > 0:
            btn.click(timeout=5000)
            page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    final = page.url

    if _has_captcha(page):
        result.update(status="needs_captcha", url=final,
                      message="CAPTCHA détecté — à toi de le résoudre, puis de finir.",
                      screenshot=_screenshot(page, offer))
        return result

    if _looks_like_login(final, page):
        result.update(status="needs_login", url=final,
                      message="Connexion requise. Connecte-toi dans cette fenêtre, "
                              "reviens sur l'offre, puis relance « Préparer ».",
                      screenshot=_screenshot(page, offer))
        return result

    return _fill_and_report(page, offer, cv_path)


# ---------------------------------------------------------------------------
def apply_to_offer(offer: dict, cv_path: str | None = None,
                   headless: bool = False, keep_open_seconds: int = 900) -> dict:
    """Ouvre l'offre dans un navigateur VISIBLE (profil persistant), pré-remplit
    le formulaire et joint le CV, puis GARDE la fenêtre ouverte pour que tu
    vérifies, téléverses si besoin et cliques Postuler TOI-MÊME.

    Ne soumet jamais et s'arrête sur connexion/CAPTCHA (règles d'Ali).
    Renvoie {status, url, message, screenshot}.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover
        return {"status": "error", "message": f"Playwright indisponible : {exc}"}

    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE_DIR),
            headless=headless,
            locale="fr-FR",
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            result = _run_flow(page, offer, cv_path)
        except Exception as exc:
            log.exception("apply_to_offer a échoué : %s", exc)
            result = {"status": "error", "url": page.url if page else None,
                      "message": str(exc), "screenshot": None}
        # Si connexion requise : attendre qu'Ali se connecte DANS la fenêtre,
        # puis remplir automatiquement (il n'a pas à re-cliquer « Préparer »).
        if not headless and result.get("status") == "needs_login":
            try:
                result = _wait_login_then_fill(page, offer, cv_path, keep_open_seconds)
            except Exception as exc:
                log.debug("attente connexion : %s", exc)

        # Laisse la fenêtre ouverte pour que l'utilisateur finisse (sauf headless).
        if not headless and keep_open_seconds > 0:
            waited = 0
            try:
                while waited < keep_open_seconds and not page.is_closed():
                    time.sleep(2)
                    waited += 2
            except Exception:
                pass
        try:
            ctx.close()
        except Exception:
            pass
        return result


# ---------------------------------------------------------------------------
def _load_offer(url: str | None, rank: int | None) -> dict | None:
    """Charge une offre depuis la base par URL ou par rang (1 = meilleur score)."""
    import sqlite3
    conn = sqlite3.connect(str(config.DB_PATH)); conn.row_factory = sqlite3.Row
    if url:
        r = conn.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone()
    else:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE fit_score IS NOT NULL "
            "AND (eligibility IS NULL OR eligibility='eligible') "
            "AND COALESCE(apply_status,'') NOT IN ('applied','ignored','skip_handicap') "
            "ORDER BY fit_score DESC, scored_at DESC").fetchall()
        r = rows[(rank or 1) - 1] if rows and (rank or 1) <= len(rows) else None
    return dict(r) if r else None


def main() -> None:
    """CLI à lancer SUR TON PC : ouvre le formulaire pré-rempli, tu finis.

    Exemples :
      python -m applypilot.autoapply --rank 1
      python -m applypilot.autoapply --url "https://www.adzuna.fr/details/..."
    """
    import argparse
    config.load_env()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s - %(levelname)s - %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description="Candidature assistée (navigateur visible).")
    ap.add_argument("--url", help="URL de l'offre (jobs.url).")
    ap.add_argument("--rank", type=int, default=1, help="Rang par score (1 = meilleur).")
    ap.add_argument("--cv", help="Chemin du CV PDF (sinon celui de l'offre, sinon généré).")
    ap.add_argument("--headless", action="store_true", help="Sans fenêtre (déconseillé).")
    args = ap.parse_args()

    offer = _load_offer(args.url, args.rank)
    if not offer:
        raise SystemExit("Offre introuvable (vérifie --url ou --rank).")

    cv = args.cv or offer.get("tailored_resume_path")
    if not cv or not Path(cv).exists():
        from applypilot import cv_latex
        log.info("Génération du CV adapté…")
        cv = str(cv_latex.generate_cv(offer))
    log.info("Offre : %s", (offer.get("title") or "")[:60])
    log.info("CV    : %s", cv)

    res = apply_to_offer(offer, cv_path=cv, headless=args.headless)
    log.info("STATUT : %s", res["status"])
    log.info("%s", res.get("message"))


if __name__ == "__main__":
    main()
