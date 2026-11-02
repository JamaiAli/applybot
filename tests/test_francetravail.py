"""Tests de l'adaptateur France Travail (API « Offres d'emploi v2 »).

HTTP entièrement mocké — aucun appel réseau. Couvre : filtre stage, normalisation,
contrat d'insertion (état + transition), no-op gracieux sans identifiants, et
l'orchestration (ne garde que les offres de stage).
"""

from applypilot.discovery import francetravail as ft


def _offre(**over):
    base = {
        "id": "123",
        "intitule": "Stage Cybersécurité",
        "typeContratLibelle": "Stage",
        "description": "Mission de stage. " * 30,  # > 200 chars
        "lieuTravail": {"libelle": "Paris (75)"},
        "entreprise": {"nom": "ACME"},
        "dateCreation": "2026-10-01T09:00:00.000Z",
        "origineOffre": {"urlOrigine": "https://candidat.francetravail.fr/offres/recherche/detail/123"},
    }
    base.update(over)
    return base


# ── _is_stage ───────────────────────────────────────────────────────────

def test_is_stage_vrai_pour_stage():
    assert ft._is_stage(_offre(intitule="Stage SOC", typeContratLibelle="Stage"))


def test_is_stage_vrai_pour_alternance():
    assert ft._is_stage(_offre(intitule="Alternance Admin Sys", typeContratLibelle="Contrat d'apprentissage"))


def test_is_stage_faux_pour_cdi():
    assert not ft._is_stage(_offre(intitule="Ingénieur Cyber", typeContratLibelle="Contrat à durée indéterminée"))


def test_is_stage_strict_exclut_alternance():
    offre = _offre(intitule="Alternance Réseau", typeContratLibelle="Contrat d'apprentissage")
    assert not ft._is_stage(offre, include_alternance=False)


# ── _normalize_offre ──────────────────────────────────────────────────────

def test_normalize_mappe_les_champs():
    norm = ft._normalize_offre(_offre())
    assert norm["url"].endswith("/detail/123")
    assert norm["title"] == "Stage Cybersécurité"
    assert norm["location"] == "Paris (75)"
    assert norm["employer_name"] == "ACME"
    assert norm["posted_at"] == "2026-10-01T09:00:00.000Z"
    assert norm["full_description"] and len(norm["full_description"]) > 200


def test_normalize_url_de_repli_sans_origine():
    norm = ft._normalize_offre(_offre(origineOffre={}, id="999"))
    assert norm["url"] == "https://candidat.francetravail.fr/offres/recherche/detail/999"


# ── _insert_jobs : contrat état + transition ──────────────────────────────

def test_insert_cree_transition_initiale(tmp_db):
    conn = tmp_db()
    jobs = [ft._normalize_offre(_offre())]
    new, existing = ft._insert_jobs(conn, jobs)
    assert (new, existing) == (1, 0)

    url = jobs[0]["url"]
    state = conn.execute("SELECT state FROM jobs WHERE url = ?", (url,)).fetchone()["state"]
    assert state == "enriched"  # description complète → enriched

    rows = conn.execute(
        "SELECT from_state, to_state FROM job_state_transitions WHERE job_url = ?", (url,)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["from_state"] is None
    assert rows[0]["to_state"] == "enriched"


# ── run_francetravail_discovery ───────────────────────────────────────────

def test_run_no_op_sans_identifiants(monkeypatch):
    monkeypatch.setattr(ft.config, "load_env", lambda: None)
    monkeypatch.delenv("FRANCE_TRAVAIL_CLIENT_ID", raising=False)
    monkeypatch.delenv("FRANCE_TRAVAIL_CLIENT_SECRET", raising=False)
    result = ft.run_francetravail_discovery()
    assert result["skipped"] == "missing credentials"
    assert result["new"] == 0


def test_run_ne_garde_que_les_stages(tmp_db, monkeypatch):
    tmp_db()  # schéma + DB temporaire
    ft._token_cache.update({"token": None, "expires_at": 0.0})

    monkeypatch.setattr(ft.config, "load_env", lambda: None)
    monkeypatch.setenv("FRANCE_TRAVAIL_CLIENT_ID", "id")
    monkeypatch.setenv("FRANCE_TRAVAIL_CLIENT_SECRET", "secret")
    monkeypatch.setattr(ft.config, "load_search_config",
                        lambda: {"france_travail": {"queries": ["stage"], "max_pages": 1}})
    monkeypatch.setattr(ft, "_request_token", lambda cid, cs, timeout=20.0: ("tok", 1500))

    offres = [
        _offre(id="1", intitule="Stage Cybersécurité", typeContratLibelle="Stage",
               origineOffre={"urlOrigine": "https://ft/1"}),
        _offre(id="2", intitule="Ingénieur CDI", typeContratLibelle="Contrat à durée indéterminée",
               origineOffre={"urlOrigine": "https://ft/2"}),
    ]
    monkeypatch.setattr(ft, "_search_offres", lambda token, params, timeout=20.0: (offres, False))

    result = ft.run_francetravail_discovery()
    assert result["new"] == 1  # seul le stage est inséré

    conn = ft.get_connection()
    rows = conn.execute("SELECT url, title FROM jobs").fetchall()
    assert len(rows) == 1
    assert rows[0]["url"] == "https://ft/1"
