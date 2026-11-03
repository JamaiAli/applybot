"""Outil d'INSPECTION JobTeaser — à lancer par Ali, connecté via son école.

JobTeaser est une SPA derrière une connexion SSO (INSA) : impossible d'y accéder
sans se connecter. Cet outil ouvre un navigateur VISIBLE (profil persistant),
laisse Ali se connecter et naviguer vers les offres, et CAPTURE au passage les
réponses JSON de l'API interne que la page utilise pour charger les offres.

Le résultat est écrit dans ~/.applypilot/jobteaser_capture.json. Claude le lit
ensuite (aucun secret : on ne garde que les URLs d'API et un extrait du corps
JSON des offres) pour écrire le vrai scraper.

⚠️ L'outil ne saisit JAMAIS de mot de passe et ne crée pas de compte : c'est Ali
qui se connecte lui-même dans la fenêtre.

Lancement (dans ta PowerShell) :
    .venv\\Scripts\\python -m applypilot.jobteaser_inspect
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from applypilot import config

log = logging.getLogger("applypilot.jobteaser")

PROFILE_DIR = config.APP_DIR / "browser_profile"
OUT_PATH = config.APP_DIR / "jobteaser_capture.json"
START_URL = "https://www.jobteaser.com/fr/users/sign_in"

# On ne retient que les réponses JSON dont l'URL ressemble à une API d'offres.
_KEYWORDS = ("job", "offer", "offre", "search", "recherche", "graphql",
             "annonce", "position", "listing", "/api/")
_MAX_BODY = 40000   # extrait de corps conservé par réponse


def main() -> None:
    config.load_env()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s - %(levelname)s - %(message)s",
                        datefmt="%H:%M:%S")
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"Playwright indisponible : {exc}")

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    captures: list[dict] = []
    seen: set[str] = set()

    print("\n" + "=" * 68)
    print(" INSPECTION JOBTEASER — étapes :")
    print("  1) Connecte-toi à JobTeaser via ton école (INSA) dans la fenêtre.")
    print("  2) Va sur la page des OFFRES / jobs et fais défiler quelques offres.")
    print("  3) Ouvre 1 ou 2 offres si possible.")
    print("  4) Quand c'est fait, FERME la fenêtre du navigateur.")
    print(" L'outil enregistre alors ce qu'il a capturé. Rien n'est soumis.")
    print("=" * 68 + "\n")

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR), headless=False, locale="fr-FR",
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        def _on_response(resp):
            try:
                url = resp.url
                ct = (resp.headers or {}).get("content-type", "")
                if "json" not in ct.lower():
                    return
                low = url.lower()
                if not any(k in low for k in _KEYWORDS):
                    return
                key = url.split("?")[0]
                if key in seen:
                    return
                seen.add(key)
                try:
                    body = resp.text()[:_MAX_BODY]
                except Exception:
                    body = ""
                captures.append({"url": url, "status": resp.status,
                                 "content_type": ct, "body": body})
                log.info("Capturé : %s (%s)", url[:90], resp.status)
            except Exception:
                pass

        # Capture sur toutes les pages/onglets du contexte.
        ctx.on("page", lambda p: p.on("response", _on_response))
        page.on("response", _on_response)

        try:
            page.goto(START_URL, wait_until="domcontentloaded", timeout=45000)
        except Exception as exc:
            log.warning("Navigation initiale : %s", exc)

        # Garde la fenêtre ouverte jusqu'à ce qu'Ali la ferme (max 20 min).
        waited = 0
        while waited < 1200:
            try:
                if all(p.is_closed() for p in ctx.pages) or not ctx.pages:
                    break
            except Exception:
                break
            time.sleep(2)
            waited += 2

        try:
            ctx.close()
        except Exception:
            pass

    OUT_PATH.write_text(json.dumps(captures, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"\n✅ {len(captures)} réponse(s) JSON capturée(s).")
    print(f"Fichier écrit : {OUT_PATH}")
    print("Dis à Claude : « capture JobTeaser faite » — il lira le fichier.\n")


if __name__ == "__main__":
    main()
