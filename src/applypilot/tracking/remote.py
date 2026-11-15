"""Client du traceur hébergé (Cloudflare Worker, voir deploy/tracer-worker/).

Le Worker reçoit les clics des recruteurs 24 h/24, envoie lui-même l'alerte
Telegram et redirige vers la vraie page. Le bot n'a que deux choses à lui dire :

- ``register`` : « voici les liens de cette offre », avant d'envoyer le CV ;
- ``sync_hits`` : « quels clics depuis la dernière fois ? », pour /trackers.

Actif seulement si TRACER_BASE_URL est une adresse HTTPS publique ET que
TRACER_API_KEY est défini. Sinon rien ici n'est appelé.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from applypilot import config

log = logging.getLogger(__name__)

PERMANENT_TOKENS = ("portfolio", "linkedin", "github")
_TIMEOUT = 10


def is_enabled() -> bool:
    base = config.TRACER_BASE_URL.lower()
    return bool(config.TRACER_API_KEY) and base.startswith("https://") and "localhost" not in base


def _cursor_path():
    return config.APP_DIR / "tracer_remote_cursor.txt"


def _request(method: str, path: str, body: dict | None = None) -> dict:
    """Appel HTTP authentifié au Worker. Lève une exception en cas d'échec."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{config.TRACER_BASE_URL}{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {config.TRACER_API_KEY}",
                 "Content-Type": "application/json", "User-Agent": "applypilot-bot"},
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def register(trackers: list[dict]) -> bool:
    """Déclare des liens au Worker. Vrai seulement si TOUS sont enregistrés :
    un lien non déclaré redirigerait le recruteur vers la mauvaise page, donc en
    cas de doute l'appelant garde les liens directs dans le CV."""
    if not trackers:
        return False
    try:
        res = _request("POST", "/api/register", {"trackers": trackers})
    except Exception as exc:
        log.warning("Traceur distant injoignable, liens directs conservés : %s", exc)
        return False
    ok = bool(res.get("ok")) and res.get("registered") == len(trackers)
    if not ok:
        log.warning("Traceur distant : déclaration incomplète (%s)", res)
    return ok


def fetch_hits(after: str = "") -> list[dict]:
    res = _request("GET", "/api/hits?after=" + urllib.parse.quote(after, safe=""))
    return list(res.get("hits") or [])


def sync_hits() -> dict:
    """Rapatrie dans la base locale les clics reçus par le Worker depuis la
    dernière synchronisation. Les ouvertures par un robot (antivirus de
    messagerie, aperçu de lien…) ne sont pas comptées comme des consultations.

    Renvoie {"new": clics comptés, "bots": robots écartés, "error": message ou None}.
    """
    from applypilot.database import create_cv_tracker, get_tracker, record_tracker_hit
    from applypilot.tracking.tracer import get_candidate_links

    result = {"new": 0, "bots": 0, "error": None}
    if not is_enabled():
        return result
    try:
        after = _cursor_path().read_text(encoding="utf-8").strip()
    except OSError:
        after = ""
    try:
        hits = fetch_hits(after)
    except Exception as exc:
        result["error"] = str(exc)
        log.warning("Synchronisation du traceur distant impossible : %s", exc)
        return result

    for hit in hits:
        token = str(hit.get("token") or "").lower()
        if hit.get("bot"):
            result["bots"] += 1
        elif token:
            if token in PERMANENT_TOKENS and not get_tracker(token):
                links = get_candidate_links()
                create_cv_tracker(token=token, job_url="profil_permanent",
                                  company="Lien Direct / Profil Public", title=f"Profil {token.capitalize()}",
                                  target_url=links.get(token) or links["portfolio"], link_type=token)
            if record_tracker_hit(token, ip=str(hit.get("ip") or ""), user_agent=str(hit.get("ua") or ""),
                                  opened_at=hit.get("ts")):
                result["new"] += 1
        after = max(after, str(hit.get("key") or ""))
    if hits:
        try:
            _cursor_path().write_text(after, encoding="utf-8")
        except OSError as exc:
            log.warning("Curseur du traceur distant non enregistré : %s", exc)
    return result
