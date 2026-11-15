"""Shared pytest fixtures for applypilot tests.

`tmp_db` yields a factory returning a fresh, schema-initialized SQLite
connection rooted at a tmp path. Each call is isolated — tests that need
multiple DBs get multiple calls.

Adaptation notes vs. original plan:
- database uses `_local` (threading.local), not `_thread_local`
- connections are cached in `_local.connections` dict keyed by path string
- init_db(db_path) accepts a path arg and returns the connection directly
"""

import sqlite3
from pathlib import Path

import pytest

# Module-level counter so repeated seed_job calls get unique URLs by default.
_seed_counter = [0]


@pytest.fixture(autouse=True)
def _no_real_tracer(monkeypatch):
    """Aucun test ne doit parler au vrai traceur hébergé ni hériter de son adresse.

    Une fois le Worker déployé, ~/.applypilot/.env contient une adresse publique
    et une clé : un test qui chargerait ce fichier déclarerait de faux liens chez
    Cloudflare. Les variables sont donc figées (load_dotenv n'écrase pas une
    variable déjà définie) et tout appel réseau du client lève une erreur.
    """
    from applypilot import config
    from applypilot.tracking import remote

    monkeypatch.setenv("TRACER_BASE_URL", "http://localhost:8765")
    monkeypatch.setenv("TRACER_API_KEY", "")
    monkeypatch.setattr(config, "TRACER_BASE_URL", "http://localhost:8765")
    monkeypatch.setattr(config, "TRACER_API_KEY", "")

    def no_network(*args, **kwargs):
        raise AssertionError("appel réseau au traceur hébergé pendant un test")
    monkeypatch.setattr(remote, "_request", no_network)


@pytest.fixture(autouse=True)
def _isolated_cv_dir(tmp_path_factory, monkeypatch):
    """Redirige le dossier de compilation des CV vers un dossier temporaire.

    Sans cela, tout test qui appelle ``generate_cv`` écrase les vrais CV de
    ``~/.applypilot/cv_latex`` (ceux liés aux offres en base) par une version
    de test. La photo réelle est copiée pour que la mise en page — et donc la
    contrainte « 1 page » — reste représentative.
    """
    import shutil

    from applypilot import cv_latex

    real_dir = cv_latex.CV_LATEX_DIR
    tmp_dir = tmp_path_factory.mktemp("cv_latex")
    photo = real_dir / cv_latex.PHOTO_REL
    if photo.exists():
        (tmp_dir / cv_latex.PHOTO_REL).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(photo, tmp_dir / cv_latex.PHOTO_REL)
    monkeypatch.setattr(cv_latex, "CV_LATEX_DIR", tmp_dir)
    yield tmp_dir


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Yield a factory that returns a fresh sqlite3.Connection backed by a tmp file.

    Also monkeypatches applypilot.config.DB_PATH and APP_DIR so that
    `applypilot.database.get_connection()` returns the same connection.
    """
    from applypilot import config
    from applypilot import database

    db_file = tmp_path / "applypilot.db"
    monkeypatch.setattr(config, "DB_PATH", db_file)
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    # database.DB_PATH is imported by-name at module load, so patching only
    # config.DB_PATH leaves database.DB_PATH pointing at the real DB.
    monkeypatch.setattr(database, "DB_PATH", db_file)

    # Reset the module-level thread-local connection cache so get_connection
    # opens fresh against the new tmp path.  _local.connections is a dict
    # keyed by path string; clear it entirely to avoid any stale handle.
    if hasattr(database._local, "connections"):
        for conn in database._local.connections.values():
            try:
                conn.close()
            except Exception:
                pass
        database._local.connections.clear()

    def _factory() -> sqlite3.Connection:
        # init_db(db_path) creates the schema and returns the connection.
        return database.init_db(db_file)

    yield _factory

    # Cleanup: close the connection opened for the tmp db_file
    if hasattr(database._local, "connections"):
        path_key = str(db_file)
        conn = database._local.connections.pop(path_key, None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


@pytest.fixture
def seed_job():
    """Return a callable that inserts a minimally-valid job row into a connection.

    Returns the full row dict so callers can assert on any field.
    The URL is available at ``row["url"]``.

    ``url_suffix`` is an optional keyword-only override (stripped before INSERT)
    that customises the URL path segment.  When omitted an auto-incrementing
    suffix is used so that repeated calls never collide on the UNIQUE ``url``
    constraint.
    """
    from datetime import datetime, timezone

    def _seed(conn: sqlite3.Connection, **overrides) -> dict:
        default_suffix = f"auto-{_seed_counter[0]}"
        _seed_counter[0] += 1
        suffix = overrides.get("url_suffix", default_suffix)
        now = datetime.now(timezone.utc).isoformat()
        row = {
            "url": f"https://example.com/job/{suffix}",
            "title": "Senior Software Engineer",
            "description": "A job.",
            "full_description": "A full description.",
            "location": "Remote (US)",
            "site": "linkedin",
            "company": "acme",
            "application_url": "https://boards.greenhouse.io/acme/jobs/1",
            "fit_score": 9,
            "tailored_resume_path": "/tmp/resume.pdf",
            "cover_letter_path": "/tmp/cover.pdf",
            "discovered_at": now,
            "apply_status": None,
            "apply_attempts": 0,
        }
        row.update({k: v for k, v in overrides.items() if k != "url_suffix"})
        cols = ", ".join(row.keys())
        qs = ", ".join("?" * len(row))
        conn.execute(f"INSERT INTO jobs ({cols}) VALUES ({qs})", tuple(row.values()))
        conn.commit()
        return row

    return _seed
