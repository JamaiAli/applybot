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


def _qa_knowledge() -> dict:
    """Réponses d'Ali aux questions de formulaire (permis, mobilité, dispo…)."""
    try:
        d = json.loads((config.APP_DIR / "qa_knowledge.json").read_text(encoding="utf-8"))
        return {k: v for k, v in d.items() if not k.startswith("_")}
    except Exception:
        return {}


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
        r"(t[ée]l[ée]phone|phone|mobile|portable|num[ée]ro|joindre)": pers.get("phone", ""),
        r"(ville|habitez|city|localit[ée])": pers.get("city", ""),
        r"(code[\s_-]*postal|postal[\s_-]*code|zip)": pers.get("postal_code", ""),
        r"(linkedin)": pers.get("linkedin_url", ""),
        r"(github)": pers.get("github_url", ""),
        r"(portfolio|site[\s_-]*web|website|site perso)": pers.get("portfolio_url", ""),
    }
    return [(re.compile(k, re.I), v) for k, v in pairs.items() if v]


# ---------------------------------------------------------------------------
_ACCOUNT_PROMPT = (
    "créez votre compte", "creez votre compte", "créer un compte",
    "creer un compte", "connectez-vous", "identifiez-vous",
    "se connecter pour", "connexion pour postuler",
)


def _looks_like_login(url: str, page) -> bool:
    if _LOGIN_RE.search(url or ""):
        return True
    try:
        has_pw = page.locator("input[type='password']").count() > 0
        has_file = page.locator("input[type='file']").count() > 0
        if has_pw and not has_file:
            return True
        # Prompt « Créez votre compte / Connectez-vous » (ex. HelloWork avant
        # connexion) sans formulaire de candidature -> connexion requise.
        if not has_file:
            txt = ""
            try:
                sec = page.locator("#postuler")
                if sec.count() > 0:
                    txt = (sec.first.inner_text(timeout=1500) or "").lower()
            except Exception:
                txt = ""
            if any(k in txt for k in _ACCOUNT_PROMPT):
                return True
    except Exception:
        pass
    return False


def _has_captcha(page) -> bool:
    """Détecte uniquement un CAPTCHA VISIBLE à résoudre (case reCAPTCHA v2,
    hCaptcha, Turnstile). Les scripts reCAPTCHA v3 INVISIBLES chargés en fond
    (ex. SAP SuccessFactors) ne comptent PAS : ils ne bloquent pas le
    remplissage et ne se déclenchent qu'à l'envoi."""
    selectors = (
        "iframe[src*='recaptcha/api2/anchor']",   # case « je ne suis pas un robot »
        "iframe[src*='recaptcha/api2/bframe']",    # défi image
        "iframe[src*='hcaptcha.com']",
        "iframe[src*='challenges.cloudflare.com']",
        "iframe[title*='captcha' i]",
    )
    try:
        for sel in selectors:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 3)):
                try:
                    if loc.nth(i).is_visible():
                        return True
                except Exception:
                    continue
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


# Boutons qui mènent au formulaire de candidature, selon les ATS.
_APPLY_SELECTOR = (
    "a:has-text('Postuler'), button:has-text('Postuler'), "
    "a:has-text('Je postule'), button:has-text('Je postule'), "
    "a:has-text('Candidater'), button:has-text('Candidater'), "
    "a:has-text('Déposer'), button:has-text('Déposer'), "
    "button:has-text('candidature'), a:has-text('candidature'), "
    "a:has-text('Apply'), button:has-text('Apply'), "
    "[data-cy='applyButtonHeader'], [data-cy='applyButton']"  # HelloWork
)


def _form_present(page) -> bool:
    """Un formulaire de candidature semble présent (champ fichier, ou >=2 champs
    texte éditables)."""
    try:
        if page.locator("input[type='file']").count() > 0:
            return True
        return page.locator(
            "input[type='text'], input[type='email'], input[type='tel'], "
            "textarea").count() >= 2
    except Exception:
        return False


def _trigger_apply(page) -> bool:
    """Cherche dans TOUTE la page un bouton « Postuler/Candidater… » et le clique
    (en le faisant défiler à l'écran). Renvoie True si un clic a eu lieu."""
    try:
        btns = page.locator(_APPLY_SELECTOR)
        for i in range(min(btns.count(), 5)):
            b = btns.nth(i)
            try:
                if not b.is_visible():
                    continue
                b.scroll_into_view_if_needed(timeout=2000)
                b.click(timeout=4000)
                page.wait_for_load_state("domcontentloaded", timeout=12000)
                return True
            except Exception:
                continue
    except Exception:
        pass
    return False


# JS : pour chaque input/textarea, renvoie son LIBELLÉ (aria-label, <label for>,
# label englobant, ou le texte le plus proche au-dessus — ex. une question),
# plus name/placeholder. Indispensable pour les formulaires type Wizbii où le
# libellé est une question séparée, pas un attribut du champ.
_JS_LABELS = r"""
() => {
  const els = [...document.querySelectorAll('input, textarea')];
  return els.map((el, idx) => {
    let label = el.getAttribute('aria-label') || '';
    if (!label && el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l) label = l.innerText || '';
    }
    if (!label) { const l = el.closest('label'); if (l) label = l.innerText || ''; }
    if (!label) {
      let node = el;
      for (let i = 0; i < 4 && node; i++) {
        node = node.parentElement;
        if (node) {
          const t = (node.innerText || '').trim();
          if (t) { label = t.split('\n')[0]; break; }
        }
      }
    }
    label = (label || '') + ' ' + (el.name || '') + ' ' + (el.placeholder || '');
    const r = el.getBoundingClientRect();
    return {idx, label: label.slice(0, 160),
            type: (el.type || el.tagName || '').toLowerCase(),
            visible: !!(r.width && r.height)};
  });
}
"""


def _fill_known_fields(page, p: dict) -> int:
    """Remplit les champs connus restés vides en s'appuyant sur leur LIBELLÉ
    (question affichée) et non seulement sur les attributs. Renvoie le nb rempli."""
    pairs = _field_values(p)
    try:
        metas = page.evaluate(_JS_LABELS)
    except Exception:
        metas = []
    inputs = page.locator("input, textarea")
    filled = 0
    for m in metas:
        if not m.get("visible") or m.get("type") in ("hidden", "password", "file",
                                                      "checkbox", "radio", "submit"):
            continue
        label = (m.get("label") or "").lower()
        if not label:
            continue
        el = inputs.nth(m["idx"])
        try:
            if not el.is_visible() or not el.is_editable():
                continue
            if (el.input_value() or "").strip():
                continue  # ne pas écraser
            for pat, val in pairs:
                if pat.search(label):
                    el.fill(val, timeout=3000)
                    filled += 1
                    break
        except Exception:
            continue
    return filled


# JS : extrait les <select> (avec leurs options) et les <textarea> vides visibles,
# avec leur libellé — pour que Groq choisisse l'option / rédige la lettre.
_JS_SMART_FIELDS = r"""
() => {
  const labelOf = (el) => {
    let label = el.getAttribute('aria-label') || '';
    if (!label && el.id) { const l=document.querySelector('label[for="'+CSS.escape(el.id)+'"]'); if(l) label=l.innerText||''; }
    if (!label) { const l=el.closest('label'); if(l) label=l.innerText||''; }
    if (!label) { let n=el; for(let i=0;i<4&&n;i++){ n=n.parentElement; if(n){const t=(n.innerText||'').trim(); if(t){label=t.split('\n')[0];break;}}}}
    return (label||'').slice(0,160);
  };
  const out = [];
  const selects = [...document.querySelectorAll('select')];
  selects.forEach((el) => {
    const r = el.getBoundingClientRect(); if (!(r.width && r.height)) return;
    if (el.value && el.value.trim() && el.selectedIndex > 0) return;
    const opts = [...el.options].map(o => (o.text||'').trim()).filter(Boolean);
    out.push({kind:'select', nth: selects.indexOf(el), label: labelOf(el), options: opts.slice(0,30)});
  });
  const tas = [...document.querySelectorAll('textarea')];
  tas.forEach((el) => {
    const r = el.getBoundingClientRect(); if (!(r.width && r.height)) return;
    if ((el.value||'').trim()) return;
    out.push({kind:'textarea', nth: tas.indexOf(el), label: labelOf(el), options: []});
  });
  // Groupes de boutons radio (ex. « Permis de conduire ? » Oui / Non)
  const names = new Set();
  document.querySelectorAll('input[type=radio]').forEach(e => { if (e.name) names.add(e.name); });
  names.forEach(name => {
    const els = [...document.querySelectorAll('input[type=radio][name="' + CSS.escape(name) + '"]')];
    const vis = els.filter(e => { const r = e.getBoundingClientRect(); return r.width && r.height; });
    if (!vis.length || els.some(e => e.checked)) return;  // vide seulement
    // question = texte au-dessus du groupe ; options = libellé de chaque radio
    let q = labelOf(vis[0]);
    const values = vis.map(e => e.value);
    const labels = vis.map(e => labelOf(e) || e.value);
    out.push({kind:'radio', name:name, label:q, options:labels, values:values});
  });
  return out;
}
"""


def _candidate_brief(p: dict) -> str:
    pers = p.get("personal", {})
    skills = p.get("skills_boundary", {})
    flat = []
    for v in (skills.values() if isinstance(skills, dict) else []):
        if isinstance(v, list):
            flat += v
    school = p.get("experience", {}).get("school") or "INSA Centre Val de Loire"
    objectif = (p.get("internship", {}).get("summary")
                or "stage de 6 mois a partir d'avril 2027 en integration IT et "
                   "cybersecurite, autour d'Active Directory / Entra ID")
    comp = ", ".join(flat[:25])
    return (f"Nom: {pers.get('full_name')}. Ecole: {school}. "
            f"Objectif: {objectif}. Competences cles: {comp}.")


def _smart_fill(page, offer: dict, p: dict, seen: set) -> int:
    """Via Groq : choisit l'option des menus déroulants et rédige la lettre de
    motivation, à partir des SEULES données du profil. Dé-doublonne par
    signature (un seul appel Groq par page/étape distincte)."""
    try:
        fields = page.evaluate(_JS_SMART_FIELDS)
    except Exception:
        fields = []
    if not fields:
        return 0
    sig = "|".join(f"{f['kind']}:{f['label']}" for f in fields)
    if sig in seen:
        return 0
    seen.add(sig)

    import json as _json
    cat = [{"i": i, "type": f["kind"], "libelle": f["label"], "options": f["options"]}
           for i, f in enumerate(fields)]
    qa = _qa_knowledge()
    system = (
        "Tu remplis un formulaire de candidature, en français. "
        "RÈGLE ABSOLUE : n'invente aucune compétence, expérience ni chiffre ; "
        "utilise uniquement le PROFIL et les INFOS fournis. Pour 'select' ou "
        "'radio', 'valeur' DOIT être EXACTEMENT l'une des options proposées (ou "
        "\"\" si rien ne convient). Pour un 'textarea' de motivation, rédige 2 à "
        "4 phrases sincères (sans parenthèses, sans formule bateau). Réponds "
        "UNIQUEMENT en JSON : {\"remplir\": [{\"i\": <int>, \"valeur\": \"...\"}]}"
    )
    user = (
        f"PROFIL: {_candidate_brief(p)}\n"
        f"INFOS (réponses du candidat) : {_json.dumps(qa, ensure_ascii=False)}\n"
        f"OFFRE: {offer.get('title')} — {offer.get('company') or offer.get('site')}\n"
        f"CHAMPS: {_json.dumps(cat, ensure_ascii=False)}"
    )
    try:
        from applypilot.llm import get_client
        raw = get_client().chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=900, temperature=0.3)
    except Exception as exc:
        log.debug("smart_fill LLM: %s", exc)
        return 0

    import re as _re
    m = _re.search(r"\{.*\}", raw or "", _re.DOTALL)
    if not m:
        return 0
    try:
        data = _json.loads(m.group(0))
    except ValueError:
        return 0

    selects = page.locator("select")
    textareas = page.locator("textarea")
    done = 0
    for item in data.get("remplir", []):
        try:
            i = int(item.get("i"))
            val = (item.get("valeur") or "").strip()
            if not val or i >= len(fields):
                continue
            f = fields[i]
            if f["kind"] == "select":
                try:
                    selects.nth(f["nth"]).select_option(label=val, timeout=3000)
                    done += 1
                except Exception:
                    # repli : option dont le texte contient la valeur
                    for opt in f["options"]:
                        if val.lower() in opt.lower():
                            selects.nth(f["nth"]).select_option(label=opt, timeout=3000)
                            done += 1
                            break
            elif f["kind"] == "textarea":
                ta = textareas.nth(f["nth"])
                if not (ta.input_value() or "").strip():
                    ta.fill(val, timeout=3000)
                    done += 1
            elif f["kind"] == "radio":
                # Trouve l'option (libellé) choisie -> clique le radio correspondant.
                labels = f.get("options", [])
                values = f.get("values", [])
                idx = None
                for k, lab in enumerate(labels):
                    if val.lower() == lab.lower() or val.lower() in lab.lower() \
                            or lab.lower() in val.lower():
                        idx = k
                        break
                if idx is not None and idx < len(values):
                    try:
                        page.locator(
                            f"input[type=radio][name=\"{f['name']}\"]"
                            f"[value=\"{values[idx]}\"]").first.check(timeout=3000)
                        done += 1
                    except Exception:
                        pass
        except Exception:
            continue
    return done


def _upload_cv(page, cv_path: str) -> bool:
    """Joint le CV au PREMIER champ fichier, UNE SEULE FOIS (idempotent) : si un
    fichier y est déjà, on ne retéléverse pas (sinon « Vous ne pouvez plus
    télécharger de fichiers »). On ne touche pas aux champs « fichiers
    supplémentaires »."""
    try:
        files = page.locator("input[type='file']")
        if files.count() == 0:
            return False
        first = files.first
        try:
            already = first.evaluate("e => (e.files && e.files.length) ? e.files.length : 0")
        except Exception:
            already = 0
        if already and already > 0:
            return True  # CV déjà joint — ne rien faire
        first.set_input_files(cv_path, timeout=5000)
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
            # Connecté : atteindre le formulaire (bouton Postuler) si besoin.
            if not _form_present(page):
                _trigger_apply(page)
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
    """Ouvre l'offre, essaie d'atteindre le formulaire et injecte le bouton
    « Compléter ». NE REMPLIT PAS automatiquement : le remplissage se fait quand
    Ali clique le bouton (géré par la boucle de apply_to_offer)."""
    result = {"status": "ready", "url": None, "message": "", "screenshot": None}
    final = _follow_to_employer(page, offer)

    # Si pas déjà sur un formulaire, tenter d'ouvrir la candidature.
    if not _form_present(page):
        _trigger_apply(page)
    final = page.url

    # Injecte le bouton « Compléter le formulaire ».
    try:
        page.evaluate(_FILL_BUTTON_JS)
    except Exception:
        pass

    if _has_captcha(page):
        result.update(status="needs_captcha", url=final,
                      message="CAPTCHA détecté — résous-le, puis clique « Compléter ».")
    elif _looks_like_login(final, page):
        result.update(status="needs_login", url=final,
                      message="Connexion requise. Connecte-toi dans la fenêtre, "
                              "puis clique « Compléter le formulaire » en haut.")
    else:
        result.update(status="ready", url=final,
                      message="Formulaire ouvert — clique « Compléter le "
                              "formulaire » en haut de la page.")
    return result


# Bouton flottant « Compléter le formulaire » injecté dans la page : Ali clique
# quand il veut remplir (un clic -> window.__ap_fill_ts ; Python remplit puis met
# window.__ap_fill_done).
_FILL_BUTTON_JS = r"""
(() => {
  if (!document.body || document.getElementById('__ap_fill_btn')) return;
  const b = document.createElement('button');
  b.id = '__ap_fill_btn';
  b.type = 'button';
  b.textContent = '📝 Compléter le formulaire';
  b.style.cssText = 'position:fixed;top:10px;left:50%;transform:translateX(-50%);' +
    'z-index:2147483647;background:#1F3A5F;color:#fff;border:none;padding:11px 20px;' +
    'border-radius:10px;font-size:15px;font-weight:700;font-family:sans-serif;' +
    'cursor:pointer;box-shadow:0 3px 12px rgba(0,0,0,.35)';
  b.addEventListener('click', (e) => {
    e.preventDefault();
    window.__ap_fill_ts = Date.now();
    b.textContent = '⏳ Remplissage…';
  });
  document.body.appendChild(b);
})();
"""


def _set_button_label(page, text: str) -> None:
    try:
        page.evaluate(
            "(t)=>{const b=document.getElementById('__ap_fill_btn'); if(b) b.textContent=t;}",
            text)
    except Exception:
        pass


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
        # Fenêtre ouverte + bouton « Compléter » : Ali navigue/se connecte
        # librement, et REMPLIT AU CLIC du bouton (pas de remplissage auto en
        # boucle). Gère plusieurs onglets (HelloWork -> site recruteur).
        if not headless and keep_open_seconds > 0:
            waited = 0.0
            prof = _profile()
            smart_seen: set = set()  # signatures de pages déjà traitées par Groq
            while waited < keep_open_seconds:
                try:
                    time.sleep(1.5)
                    waited += 1.5
                    opened = [pg for pg in ctx.pages if not pg.is_closed()]
                    if not opened:
                        break
                    cur = opened[-1]
                    try:
                        cur.evaluate(_FILL_BUTTON_JS)  # (ré)injecte le bouton
                    except Exception:
                        continue
                    # A-t-il cliqué « Compléter » depuis la dernière fois ? (par page)
                    try:
                        ts = cur.evaluate(
                            "(window.__ap_fill_ts||0) > (window.__ap_fill_done||0)"
                            " ? window.__ap_fill_ts : 0")
                    except Exception:
                        ts = 0
                    if not ts:
                        continue
                    # Marque ce clic comme traité pour ne pas boucler.
                    try:
                        cur.evaluate("window.__ap_fill_done = window.__ap_fill_ts")
                    except Exception:
                        pass
                    if _has_captcha(cur):
                        _set_button_label(cur, "🧩 CAPTCHA : résous-le, puis reclique")
                        continue
                    if _looks_like_login(cur.url, cur):
                        _set_button_label(cur, "🔒 Connecte-toi, puis reclique")
                        continue
                    if not _form_present(cur):
                        _trigger_apply(cur)
                    n = _fill_known_fields(cur, prof)
                    n += _smart_fill(cur, offer, prof, smart_seen)
                    up = bool(cv_path) and _upload_cv(cur, cv_path)
                    _set_button_label(
                        cur,
                        f"✅ {n} champ(s) rempli(s){' + CV' if up else ''} — vérifie et envoie")
                except Exception:
                    continue
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
    if not offer and args.url:
        # Offre hors base (ex. job étudiant / intérim via /job) : offre minimale.
        offer = {"url": args.url, "application_url": args.url, "title": ""}
    if not offer:
        raise SystemExit("Offre introuvable (vérifie --url ou --rank).")

    cv = args.cv or offer.get("tailored_resume_path")
    if cv and not Path(cv).exists():
        cv = None
    if not cv:
        # Pas de CV fourni : on génère un CV adapté SEULEMENT pour une offre du
        # pipeline (qui a un titre). Sinon (--cv manquant), on refuse.
        if offer.get("title"):
            from applypilot import cv_latex
            log.info("Génération du CV adapté…")
            cv = str(cv_latex.generate_cv(offer))
        else:
            raise SystemExit("Aucun CV : passe --cv <chemin.pdf>.")
    log.info("Offre : %s", (offer.get("title") or args.url or "")[:60])
    log.info("CV    : %s", cv)

    res = apply_to_offer(offer, cv_path=cv, headless=args.headless)
    log.info("STATUT : %s", res["status"])
    log.info("%s", res.get("message"))


if __name__ == "__main__":
    main()
