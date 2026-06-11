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
  * ``system_credentialed_org_ids()`` is SECURITY DEFINER and enumerates the
    ``connections`` tables (repointed off the legacy flex_credentials in W1
    Task 4; the legacy table itself was dropped in W1 Task 7).

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


def test_org_scoped_snapshot_matches_live_ssot():
    """The baseline's frozen _ORG_SCOPED_TABLES snapshot stays in lockstep with the
    live SSOT (db/rls.py::ORG_SCOPED_TABLES).

    Fast unit guard (no container): the baseline freezes the org-scoped table list
    inline (T1-D14: no live builder imports), so a future amendment could update
    db/rls.py but forget the snapshot. The RLS policies aren't in Base.metadata, so
    the drift test cannot catch this. Same ORDER too (the migration iterates the
    snapshot to CREATE POLICY)."""
    import importlib.util

    import ibkr_control.db.rls as rls

    baseline_path = _VERSIONS_DIR / "a9977ac077e5_tier1_baseline.py"
    # Safe to exec_module: the baseline has no import-time side effects (env is
    # only read inside _app_rls_password(), called from upgrade(), never at load).
    spec = importlib.util.spec_from_file_location("_tier1_baseline_snapshot", baseline_path)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    assert baseline._ORG_SCOPED_TABLES == list(rls.ORG_SCOPED_TABLES)


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
    """system_credentialed_org_ids() is SECURITY DEFINER and enumerates orgs with
    a non-disabled ibkr_flex connection (repointed from flex_credentials in W1
    Task 4: the cron now iterates connections, not legacy credentials)."""
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
    assert "FROM connections" in prosrc, "the function body must enumerate connections"
    assert "status <> 'disabled'" in prosrc, (
        "the function must skip disabled connections (only credentialed/active orgs)"
    )
    assert "flex_credentials" not in prosrc, (
        "the function must no longer reference legacy flex_credentials"
    )


def test_institutions_seeded(fresh_postgres, monkeypatch):
    """The baseline seeds the global institutions catalog with the ibkr row
    (control-plane data, not user input — W1 T1-D3)."""
    sync_url = _upgrade_head(fresh_postgres, monkeypatch)
    engine = create_engine(sync_url)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT code, name FROM institutions ORDER BY code")).all()
    engine.dispose()
    assert rows == [("ibkr", "Interactive Brokers")]


def test_connections_subtype_integrity(fresh_postgres, monkeypatch):
    """Subtype integrity (T1-D2): a valid ibkr_flex parent+detail pair inserts, and
    the detail's CHECK rejects any provider_type literal other than 'ibkr_flex'.

    Only the CHECK leg is verified here. The composite-FK leg (detail must point at
    a parent with the SAME provider_type) is unreachable today: the parent's own
    CHECK only allows 'ibkr_flex', so no mismatched parent can exist to test
    against. It becomes testable when a second provider is added."""
    sync_url = _upgrade_head(fresh_postgres, monkeypatch)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        # Seed an org + institution to satisfy the connection's FKs.
        org_id = conn.execute(
            text("INSERT INTO organizations (type, name) VALUES ('personal', 'Org') RETURNING id")
        ).scalar_one()
        inst_id = conn.execute(text("SELECT id FROM institutions WHERE code = 'ibkr'")).scalar_one()
        # A valid ibkr_flex connection.
        conn_id = conn.execute(
            text(
                "INSERT INTO connections "
                "(organization_id, institution_id, provider_type) "
                "VALUES (:o, :i, 'ibkr_flex') RETURNING id"
            ).bindparams(o=org_id, i=inst_id)
        ).scalar_one()
        # The matching detail inserts fine.
        conn.execute(
            text(
                "INSERT INTO connection_ibkr_flex "
                "(connection_id, provider_type, organization_id, token_encrypted, query_id) "
                "VALUES (:c, 'ibkr_flex', :o, :tok, 'q123')"
            ).bindparams(c=conn_id, o=org_id, tok=b"x")
        )

    # The CHECK rejects a detail with any other provider_type literal.
    from sqlalchemy.exc import IntegrityError

    with engine.begin() as conn:
        org_id = conn.execute(text("SELECT id FROM organizations LIMIT 1")).scalar_one()
        conn_id = conn.execute(text("SELECT id FROM connections LIMIT 1")).scalar_one()
    with pytest.raises(IntegrityError, match="ck_connection_ibkr_flex_provider_type"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO connection_ibkr_flex "
                    "(connection_id, provider_type, organization_id, token_encrypted, query_id) "
                    "VALUES (:c, 'other_provider', :o, :tok, 'q')"
                ).bindparams(c=conn_id, o=org_id, tok=b"x")
            )
    engine.dispose()


def test_instruments_tables_exist_no_rls(fresh_postgres, monkeypatch):
    """W2 (T1-D7/D9): instruments + instrument_identifiers exist after upgrade and
    are CONTROL PLANE — RLS is OFF (relrowsecurity = false), mirror of the
    institutions catalog. They must NOT be in _ORG_SCOPED_TABLES (no
    organization_id, AAPL is AAPL for every tenant)."""
    sync_url = _upgrade_head(fresh_postgres, monkeypatch)
    engine = create_engine(sync_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT relname, relrowsecurity FROM pg_class "
                "WHERE relname IN ('instruments', 'instrument_identifiers') ORDER BY relname"
            )
        ).all()
    engine.dispose()
    assert rows == [
        ("instrument_identifiers", False),
        ("instruments", False),
    ], rows


def test_instrument_identifiers_unique(fresh_postgres, monkeypatch):
    """W2 (T1-D7): UNIQUE(id_type, id_value) — the same conid cannot map to two
    instruments. A second ('conid','265598') row violates
    uq_instrument_identifiers_id_type_id_value."""
    from sqlalchemy.exc import IntegrityError

    sync_url = _upgrade_head(fresh_postgres, monkeypatch)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        iid1 = conn.execute(
            text(
                "INSERT INTO instruments (symbol, asset_class) VALUES ('AAPL', 'STK') RETURNING id"
            )
        ).scalar_one()
        iid2 = conn.execute(
            text(
                "INSERT INTO instruments (symbol, asset_class) VALUES ('AAPL2', 'STK') RETURNING id"
            )
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO instrument_identifiers (instrument_id, id_type, id_value) "
                "VALUES (:i, 'conid', '265598')"
            ).bindparams(i=iid1)
        )
    with pytest.raises(IntegrityError, match="uq_instrument_identifiers_id_type_id_value"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO instrument_identifiers (instrument_id, id_type, id_value) "
                    "VALUES (:i, 'conid', '265598')"
                ).bindparams(i=iid2)
            )
    engine.dispose()
