"""Tests de l'adaptateur Adzuna (API France). HTTP entièrement mocké."""

from applypilot.discovery import adzuna as az


def _res(**over):
    base = {
        "id": "1",
        "title": "Stage Cybersécurité (H/F)",
        "description": "Mission de stage en cybersécurité. " * 20,  # > 200 chars
        "redirect_url": "https://www.adzuna.fr/details/1",
        "created": "2026-10-15T09:00:00Z",
        "company": {"display_name": "ACME"},
        "location": {"display_name": "Paris, Île-de-France"},
        "category": {"label": "IT Jobs"},
        "contract_time": "full_time",
    }
    base.update(over)
    return base


# ── _is_stage ─────────────────────────────────────────────────────────────

def test_is_stage_vrai():
    assert az._is_stage(_res(title="Stage SOC"))


def test_is_stage_alternance():
    assert az._is_stage(_res(title="Alternance Administrateur Systèmes"))


def test_is_stage_faux_cdi():
    assert not az._is_stage(_res(title="Ingénieur Sécurité (CDI)", category={"label": "IT Jobs"}))


def test_is_stage_strict_exclut_alternance():
    assert not az._is_stage(_res(title="Alternance Réseau"), include_alternance=False)


# ── _normalize_result ─────────────────────────────────────────────────────

def test_normalize_mappe_les_champs():
    norm = az._normalize_result(_res())
    assert norm["url"] == "https://www.adzuna.fr/details/1"
    assert norm["title"] == "Stage Cybersécurité (H/F)"
    assert norm["location"] == "Paris, Île-de-France"
    assert norm["employer_name"] == "ACME"
    assert norm["posted_at"] == "2026-10-15T09:00:00Z"
    assert norm["full_description"] and len(norm["full_description"]) > 200


# ── _insert_jobs : contrat état + transition ──────────────────────────────

def test_insert_cree_transition_initiale(tmp_db):
    conn = tmp_db()
    jobs = [az._normalize_result(_res())]
    new, existing = az._insert_jobs(conn, jobs)
    assert (new, existing) == (1, 0)

    url = jobs[0]["url"]
    state = conn.execute("SELECT state FROM jobs WHERE url = ?", (url,)).fetchone()["state"]
    assert state == "enriched"
    rows = conn.execute(
        "SELECT from_state, to_state FROM job_state_transitions WHERE job_url = ?", (url,)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["from_state"] is None
    assert rows[0]["to_state"] == "enriched"


# ── run_adzuna_discovery ──────────────────────────────────────────────────

def test_run_no_op_sans_identifiants(monkeypatch):
    monkeypatch.setattr(az.config, "load_env", lambda: None)
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    result = az.run_adzuna_discovery()
    assert result["skipped"] == "missing credentials"
    assert result["new"] == 0


def test_run_ne_garde_que_les_stages(tmp_db, monkeypatch):
    tmp_db()
    monkeypatch.setattr(az.config, "load_env", lambda: None)
    monkeypatch.setenv("ADZUNA_APP_ID", "id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "key")
    monkeypatch.setattr(az.config, "load_search_config",
                        lambda: {"adzuna": {"queries": ["stage"], "max_pages": 1}})

    results = [
        _res(id="1", title="Stage Cybersécurité", redirect_url="https://az/1"),
        _res(id="2", title="Ingénieur Sécurité (CDI)", redirect_url="https://az/2"),
    ]
    monkeypatch.setattr(az, "_search",
                        lambda app_id, app_key, country, query, page, cfg, timeout=20.0: (results, 2))

    result = az.run_adzuna_discovery()
    assert result["new"] == 1

    conn = az.get_connection()
    rows = conn.execute("SELECT url FROM jobs").fetchall()
    assert len(rows) == 1
    assert rows[0]["url"] == "https://az/1"
