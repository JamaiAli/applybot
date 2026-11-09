"""Dry-run de la source Indeed : scrape, filtre et AFFICHE les offres.

N'écrit RIEN en base et n'envoie RIEN sur Telegram. Sert à vérifier de bout en
bout que le scraper local est bien branché (chemin, dépendances, filtres,
normalisation) avant de lancer le bot.

Usage (PowerShell, Windows) :

    $env:INDEED_SCRAPER_PATH = "C:\\Users\\jamai\\indeed-scraper"
    .\\.venv\\Scripts\\python.exe scripts\\indeed_dryrun.py --pages 1
    .\\.venv\\Scripts\\python.exe scripts\\indeed_dryrun.py --location "Île-de-France" --pages 2 --alternance

Le chemin du scraper vient de INDEED_SCRAPER_PATH ou de searches.yaml
(indeed.scraper_path). Aucun chemin en dur ici.
"""

from __future__ import annotations

import argparse
import logging
import sys

# Windows : la console est souvent en cp1252 et plante sur les emoji/accents.
# On force l'UTF-8 sur stdout/stderr (no-op ailleurs).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Dry-run de la découverte Indeed (affichage seul).")
    ap.add_argument("--location", default=None, help='ex: "Paris", "Île-de-France"')
    ap.add_argument("--pages", type=int, default=None, help="pages max par requête")
    ap.add_argument("--alternance", action="store_true", help="garder aussi les alternances")
    ap.add_argument("--limit", type=int, default=50, help="offres max à afficher")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    # Import tardif : laisse load_env() s'exécuter via la config de l'adaptateur.
    from applypilot import config
    from applypilot.discovery import indeed

    # Surcharges CLI par-dessus la config (sans toucher aux fichiers).
    cfg = indeed._load_indeed_config()
    overrides = dict(cfg)
    if args.location is not None:
        overrides["location"] = args.location
    if args.pages is not None:
        overrides["pages"] = args.pages
    if args.alternance:
        overrides["include_alternance"] = True
    overrides.setdefault("enabled", True)

    # Patch léger et local de la lecture de config pour ce run.
    indeed._load_indeed_config = lambda: overrides  # type: ignore[assignment]

    print(f"\n[dry-run] Indeed — location={overrides.get('location', 'France')} "
          f"pages={overrides.get('pages', 1)} alternance={overrides.get('include_alternance', False)}")
    print(f"[dry-run] scraper_path={indeed._resolve_scraper_path(overrides) or '(INDEED_SCRAPER_PATH non défini)'}\n")

    result = indeed.run_indeed_discovery(dry_run=True)

    if result.get("skipped"):
        print(f"[dry-run] Source ignorée ({result['skipped']}).")
        if result.get("errors"):
            print(f"[dry-run] Détails : {result['errors']}")
        return

    from applypilot.discovery.dates import format_age_fr

    offers = result.get("offers", [])[: args.limit]
    print(f"[dry-run] {result.get('found', 0)} stage(s) cyber retenu(s) — "
          f"affichage des {len(offers)} premier(s) :\n")
    for i, o in enumerate(offers, 1):
        dom = ", ".join(o.get("domaines") or [])
        age = format_age_fr(o.get("posted_at")) or "date inconnue"
        print(f"{i:>3}. {o.get('title', '')[:90]}")
        print(f"     🏢 {o.get('employer_name', '')}   📍 {o.get('location', '')}")
        print(f"     🔎 {o.get('source', '')}   🏷 {dom}   📅 {age}")
        print(f"     🔗 {o.get('url', '')}\n")

    if not offers:
        print("[dry-run] Aucune offre (Indeed a peut-être bloqué, ou 0 résultat pour ces requêtes).")


if __name__ == "__main__":
    main()
