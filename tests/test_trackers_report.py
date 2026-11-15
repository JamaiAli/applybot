"""Commande /trackers : vue par offre, état réel du suivi, liens de test masqués."""
import fitz  # pymupdf
import pytest

import applypilot.cv_latex as cv_mod
from applypilot import config, database
from applypilot.database import create_cv_tracker, get_tracker_overview, record_tracker_hit
from applypilot.telegram_bot import format_trackers_report

OFFER_A = "https://example.com/offre/a"
OFFER_B = "https://example.com/offre/b"


@pytest.fixture
def trackers_db(tmp_db, seed_job):
    conn = tmp_db()
    seed_job(conn, url=OFFER_A, title="Stage - Analyste SOC", company="Thales")
    seed_job(conn, url=OFFER_B, title="Stage - Pentester", company="Orange")
    for url, prefix in ((OFFER_A, "a"), (OFFER_B, "b")):
        for ltype in ("portfolio", "linkedin", "github"):
            create_cv_tracker(token=f"{prefix}{ltype[:3]}", job_url=url, company="x", title="x",
                              target_url="https://example.org", link_type=ltype)
    # Liens orphelins : un test sans URL d'offre, et une offre absente de la base.
    create_cv_tracker(token="orph1", job_url="", company="Sopra Steria", title="Test", target_url="https://e.org")
    create_cv_tracker(token="orph2", job_url="https://example.com/disparue", company="X", title="Y",
                      target_url="https://e.org")
    create_cv_tracker(token="linkedin", job_url="profil_permanent", company="Lien Direct", title="Profil",
                      target_url="https://e.org", link_type="linkedin")
    return conn


def test_overview_groups_by_offer_and_hides_orphans(trackers_db):
    record_tracker_hit("alin", ip="88.12.34.56", user_agent="Mozilla/5.0 (Windows NT 10.0)")
    record_tracker_hit("alin", ip="88.12.34.56", user_agent="Mozilla/5.0 (Windows NT 10.0)")
    record_tracker_hit("agit", ip="88.12.34.56", user_agent="Mozilla/5.0 (Windows NT 10.0)")
    record_tracker_hit("linkedin", ip="1.2.3.4", user_agent="x")

    ov = get_tracker_overview()
    assert [o["company"] for o in ov["offers"]] == ["Thales", "Orange"]      # la consultée en tête
    thales = ov["offers"][0]
    assert thales["title"] == "Stage - Analyste SOC"                          # titre lu dans l'offre
    assert thales["opens"] == 3 and thales["links"] == {"portfolio": 0, "linkedin": 2, "github": 1}
    assert thales["local_only"] is False
    assert ov["offers"][1]["opens"] == 0
    assert ov["orphans"] == 2
    assert ov["permanent"] == {"linkedin": 1}


def test_local_click_is_flagged_as_a_test(trackers_db):
    record_tracker_hit("bpor", ip="192.168.1.55", user_agent="Mozilla/5.0 (iPhone)")
    orange = next(o for o in get_tracker_overview()["offers"] if o["company"] == "Orange")
    assert orange["opens"] == 1 and orange["local_only"] is True


def test_report_shows_one_line_per_offer_and_real_tracking_state(trackers_db, monkeypatch):
    record_tracker_hit("alin", ip="88.12.34.56", user_agent="Mozilla/5.0 (Windows NT 10.0)")
    monkeypatch.setattr(config, "TRACER_BASE_URL", "http://localhost:8765")
    report = format_trackers_report(get_tracker_overview())

    assert "Suivi inactif" in report and "deploy_tracer_worker" in report    # dit pourquoi, et comment l'activer
    assert "Offres suivies : <b>2</b> · consultées : <b>1</b> · clics : <b>1</b>" in report
    assert report.count("Thales") == 1 and report.count("Orange") == 1       # une ligne par offre, pas trois
    assert "💼 1 · 🐙 0 · 🌐 0" in report
    assert "Sopra Steria" not in report and "2 liens sans offre" in report   # orphelins masqués mais comptés
    assert len(report) < 4000                                                 # limite d'un message Telegram

    monkeypatch.setattr(config, "TRACER_BASE_URL", "https://suivi.exemple.fr")
    active = format_trackers_report(get_tracker_overview())
    assert "Suivi actif" in active and "Suivi inactif" not in active
    assert "deploy_tracer_worker" not in active                              # l'aide n'apparaît qu'inactif


def test_report_caps_the_list_of_unopened_offers(trackers_db, seed_job):
    for i in range(9):
        url = f"https://example.com/offre/extra-{i}"
        seed_job(trackers_db, url=url, title=f"Stage {i}", company=f"Entreprise {i}")
        create_cv_tracker(token=f"x{i}", job_url=url, company="x", title="x", target_url="https://e.org")
    report = format_trackers_report(get_tracker_overview())
    assert "et 6 autres offres pas encore consultées" in report                # 11 offres, 5 listées




# --- Traceur hébergé (Cloudflare Worker) ---------------------------------------

@pytest.fixture
def remote_on(monkeypatch, tmp_path):
    from applypilot.tracking import remote
    monkeypatch.setattr(config, "TRACER_BASE_URL", "https://suivi.exemple.workers.dev")
    monkeypatch.setattr(config, "TRACER_API_KEY", "cle-de-test")
    monkeypatch.setattr(remote, "_cursor_path", lambda: tmp_path / "curseur.txt")
    return remote


def test_tracers_are_registered_with_the_worker_before_use(trackers_db, remote_on, monkeypatch):
    from applypilot.tracking import tracer
    sent = []
    monkeypatch.setattr(remote_on, "_request",
                        lambda method, path, body=None: sent.append((method, path, body)) or
                        {"ok": True, "registered": len(body["trackers"])})
    urls = tracer.publish_tracers({"url": OFFER_A, "title": "Stage - Analyste SOC", "company": "Thales"})

    assert set(urls) == {"portfolio", "linkedin", "github"}
    assert all(u.startswith("https://suivi.exemple.workers.dev/t/") for u in urls.values())
    (method, path, body), = sent
    assert (method, path) == ("POST", "/api/register")
    assert {t["token"] for t in body["trackers"]} == {"apor", "alin", "agit"}       # les liens déjà créés pour l'offre
    assert all(t["job_url"] == OFFER_A and t["target"].startswith("https://") for t in body["trackers"])


def test_unreachable_worker_means_direct_links_stay_in_the_cv(trackers_db, remote_on, monkeypatch):
    from applypilot.tracking import tracer

    def down(method, path, body=None):
        raise OSError("réseau coupé")
    monkeypatch.setattr(remote_on, "_request", down)
    assert tracer.publish_tracers({"url": OFFER_A, "title": "x"}) is None
    # Déclaration partielle : même prudence.
    monkeypatch.setattr(remote_on, "_request", lambda *a, **k: {"ok": False, "registered": 2})
    assert tracer.publish_tracers({"url": OFFER_A, "title": "x"}) is None


def test_sync_brings_remote_clicks_into_the_local_base_once(trackers_db, remote_on, monkeypatch):
    hits = [
        {"key": "h:000001-a", "token": "alin", "ip": "88.1.2.3", "ua": "Mozilla/5.0 (Windows NT 10.0)",
         "ts": "2026-11-10T09:00:00+00:00", "bot": False},
        {"key": "h:000002-b", "token": "agit", "ip": "88.1.2.3", "ua": "Mozilla/5.0 (Windows NT 10.0)",
         "ts": "2026-11-10T09:05:00+00:00", "bot": False},
        {"key": "h:000003-c", "token": "alin", "ip": "40.1.1.1", "ua": "Proofpoint scanner",
         "ts": "2026-11-10T09:06:00+00:00", "bot": True},
        {"key": "h:000004-d", "token": "github", "ip": "90.1.1.1", "ua": "Mozilla/5.0 (iPhone)",
         "ts": "2026-11-10T09:07:00+00:00", "bot": False},
    ]
    asked = []

    def fake(method, path, body=None):
        asked.append(path)
        after = path.split("after=", 1)[1]
        from urllib.parse import unquote
        return {"ok": True, "hits": [h for h in hits if h["key"] > unquote(after)]}
    monkeypatch.setattr(remote_on, "_request", fake)

    assert remote_on.sync_hits() == {"new": 3, "bots": 1, "error": None}
    ov = get_tracker_overview()
    thales = next(o for o in ov["offers"] if o["company"] == "Thales")
    assert thales["opens"] == 2 and thales["links"]["linkedin"] == 1          # le robot n'est pas compté
    assert thales["last_opened_at"] == "2026-11-10T09:05:00+00:00"            # date réelle du clic, pas celle de la synchro
    assert ov["permanent"].get("github") == 1                                 # lien permanent créé à la volée

    # Deuxième synchronisation : rien de neuf, rien n'est compté deux fois.
    assert remote_on.sync_hits() == {"new": 0, "bots": 0, "error": None}
    assert asked[-1].endswith("h%3A000004-d")
    assert next(o for o in get_tracker_overview()["offers"] if o["company"] == "Thales")["opens"] == 2


def test_sync_failure_is_reported_not_raised(trackers_db, remote_on, monkeypatch):
    def down(method, path, body=None):
        raise OSError("réseau coupé")
    monkeypatch.setattr(remote_on, "_request", down)
    result = remote_on.sync_hits()
    assert result["new"] == 0 and "réseau coupé" in result["error"]
    report = format_trackers_report(get_tracker_overview(), sync=result)
    assert "Suivi actif" in report and "non récupérés" in report


def test_report_announces_new_remote_clicks(trackers_db, remote_on):
    report = format_trackers_report(get_tracker_overview(), sync={"new": 2, "bots": 1, "error": None})
    assert "PC éteint" in report and "<b>2</b> nouveau(x) clic(s)" in report and "robot" in report


def test_env_file_values_reach_the_tracer_settings(monkeypatch):
    # Les réglages sont lus à l'import de config, avant le chargement du .env :
    # la relecture doit prendre en compte ce que le fichier a défini.
    monkeypatch.setenv("TRACER_BASE_URL", "https://suivi.exemple.workers.dev/")
    monkeypatch.setenv("TRACER_API_KEY", " abc ")
    config._refresh_tracer_settings()
    assert config.TRACER_BASE_URL == "https://suivi.exemple.workers.dev"
    assert config.TRACER_API_KEY == "abc"


# --- Liens traceurs sur les CV de réserve -------------------------------------

def _links(pdf) -> list[str]:
    return [l["uri"] for l in fitz.open(str(pdf))[0].get_links() if l.get("uri")]


def test_reserve_copy_gets_tracker_links_only_when_tracking_is_public(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)

    def fake_llm(offer, exp_data):
        return cv_mod._default_selection(exp_data, offer)
    monkeypatch.setattr(cv_mod, "_llm_selection", fake_llm)
    cv_mod.build_reserve(["Pentest"])
    stock = cv_mod.reserve_dir() / "CV_Ali_Jamai_Pentest.pdf"
    stock_bytes, stock_links = stock.read_bytes(), _links(stock)
    assert any("linkedin.com" in u for u in stock_links)

    tracers = {k: f"https://suivi.exemple.fr/t/{k}123" for k in ("portfolio", "linkedin", "github")}
    monkeypatch.setattr("applypilot.tracking.tracer.get_or_create_all_tracers", lambda offer: tracers)
    offer = {"title": "Stage - Pentester", "url": "https://example.com/offre/suivie", "description": "Pentest."}

    # Adresse locale : la copie reste identique à la réserve (jamais de lien local dans un CV).
    monkeypatch.setattr(config, "TRACER_BASE_URL", "http://localhost:8765")
    assert cv_mod.generate_cv(offer).read_bytes() == stock_bytes

    # Adresse publique : les trois liens de la copie passent par le traceur.
    monkeypatch.setattr(config, "TRACER_BASE_URL", "https://suivi.exemple.fr")
    pdf = cv_mod.generate_cv(offer)
    links = _links(pdf)
    assert set(tracers.values()) <= set(links)
    assert not any("linkedin.com" in u or "github.com" in u for u in links)
    assert any(u.startswith("mailto:") for u in links)                         # l'e-mail n'est pas touché
    assert fitz.open(str(pdf))[0].get_text() == fitz.open(str(stock))[0].get_text()   # texte inchangé
    assert stock.read_bytes() == stock_bytes                                   # la réserve n'a pas bougé
