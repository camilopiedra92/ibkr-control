"""Tier-1 re-baseline guardrails (W1 Task 1).

The 5-revision SP1 chain (baseline -> apscheduler -> db-hardening -> system
function -> multihome) was squashed into ONE pristine baseline (T1-D14: the
baseline is mutable until the first deploy, no deployment exists yet, dev DB is
disposable). These tests pin the invariants the squash must preserve:

  * exactly one revision file, ``down_revision is None``;
  * ``apscheduler_jobs`` exists after upgrade — it is NOT in ``Base.metadata``
    so the drift test (test_migrations.py) ignores it; THIS assert is its only
    net (the table is the APScheduler 3.x SQLAlchemyJobStore shape, pre-created
    by the migration because app_rls has no CREATE);
  * ``system_credentialed_org_ids()`` is SECURITY DEFINER and still reads
    ``flex_credentials`` (a later W1 task points it at the new connections
    tables — today it must read flex_credentials).

Behavioral RLS coverage lives in test_rls.py; structural drift in
test_migrations.py. These three only guard the squash mechanics.
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from testcontainers.postgres import PostgresContainer

_VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _revision_files() -> list[Path]:
    return sorted(p for p in _VERSIONS_DIR.glob("*.py") if p.name != "__init__.py")


def test_baseline_is_single_revision():
    """Exactly one revision file, and it is a true baseline (down_revision None)."""
    from alembic.script import ScriptDirectory

    files = _revision_files()
    assert len(files) == 1, f"expected exactly 1 revision file, found: {[f.name for f in files]}"

    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    revs = list(script.walk_revisions())
    assert len(revs) == 1, f"expected 1 alembic revision in the graph, found {len(revs)}"
    assert revs[0].down_revision is None, (
        "the single revision must be a baseline (down_revision None)"
    )


@pytest.fixture
def fresh_postgres():
    with PostgresContainer("postgres:16-alpine", driver="psycopg2") as pg:
        yield pg


def _upgrade_head(fresh_postgres, monkeypatch):
    sync_url = fresh_postgres.get_connection_url()
    async_url = sync_url.replace("+psycopg2", "+asyncpg")
    monkeypatch.setenv("DATABASE_URL", async_url)
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")
    monkeypatch.setenv("APP_RLS_PASSWORD", "app_rls_pw")
    from ibkr_control.config import get_settings

    get_settings.cache_clear()

    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    command.upgrade(cfg, "head")
    return sync_url


def test_apscheduler_jobs_exists_after_upgrade(fresh_postgres, monkeypatch):
    """apscheduler_jobs (runtime table, not in Base.metadata) is created by the
    baseline so the non-CREATE app_rls role finds it ready."""
    sync_url = _upgrade_head(fresh_postgres, monkeypatch)
    engine = create_engine(sync_url)
    with engine.connect() as conn:
        regclass = conn.execute(text("SELECT to_regclass('public.apscheduler_jobs')")).scalar_one()
        assert regclass == "apscheduler_jobs"
    engine.dispose()


def test_system_function_exists_and_is_security_definer(fresh_postgres, monkeypatch):
    """system_credentialed_org_ids() is SECURITY DEFINER and reads flex_credentials
    (a later W1 task repoints it; today it must still read flex_credentials)."""
    sync_url = _upgrade_head(fresh_postgres, monkeypatch)
    engine = create_engine(sync_url)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT prosecdef, prosrc FROM pg_proc WHERE proname='system_credentialed_org_ids'"
            )
        ).one()
    engine.dispose()
    prosecdef, prosrc = row
    assert prosecdef is True, "system_credentialed_org_ids() must be SECURITY DEFINER"
    assert "FROM flex_credentials" in prosrc, (
        "today the function body must still read flex_credentials (repointed in a later W1 task)"
    )
