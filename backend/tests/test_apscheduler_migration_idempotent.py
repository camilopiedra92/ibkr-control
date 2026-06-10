"""Dirty-volume regression: the apscheduler_jobs DDL (now in the tier1 baseline
a9977ac077e5, frozen from the old 7fdaf6528762) must converge even when the
table already exists.

APScheduler's SQLAlchemyJobStore lazily create_all()s apscheduler_jobs at
runtime. The pre-split app chained `alembic upgrade head` into the uvicorn CMD
and started the scheduler in the same process, so any volume that ran it already
has the table BEFORE the migration runs. The unconditional `op.create_table`
raised DuplicateTableError on those volumes, killing the migrate step (and thus
the backend, which depends_on it). This test pre-creates the table as the owner
— simulating that dirty volume — then runs `alembic upgrade head` and asserts it
completes, the table+index exist, and app_rls has the DML grant.

Sanity: this test PASSES against the idempotent migration and FAILS against the
old unconditional `op.create_table` (verified manually during the fix).
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from testcontainers.postgres import PostgresContainer


@pytest.fixture
def fresh_postgres():
    # Dedicated fresh container so we control the pre-migration state exactly.
    with PostgresContainer("postgres:16-alpine", driver="psycopg2") as pg:
        yield pg


def test_migration_idempotent_when_apscheduler_jobs_already_exists(fresh_postgres, monkeypatch):
    sync_url = fresh_postgres.get_connection_url()
    async_url = sync_url.replace("+psycopg2", "+asyncpg")

    monkeypatch.setenv("DATABASE_URL", async_url)
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")
    monkeypatch.setenv("APP_RLS_PASSWORD", "app_rls_pw")
    from ibkr_control.config import get_settings

    get_settings.cache_clear()

    engine = create_engine(sync_url)

    # Simulate the dirty volume: the pre-split app's APScheduler already lazily
    # created the table (as the owner) before any migration ran. Shape matches
    # APScheduler 3.x SQLAlchemyJobStore.
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE apscheduler_jobs ("
                "id VARCHAR(191) NOT NULL PRIMARY KEY, "
                "next_run_time DOUBLE PRECISION, "
                "job_state BYTEA NOT NULL)"
            )
        )

    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))

    # The real assertion: this must NOT raise DuplicateTableError. Against the old
    # unconditional op.create_table it raises here and the test fails.
    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        # Table still present after the migration converged.
        regclass = conn.execute(text("SELECT to_regclass('public.apscheduler_jobs')")).scalar_one()
        assert regclass == "apscheduler_jobs"

        # The index the migration creates is present.
        idx = conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'apscheduler_jobs' "
                "AND indexname = 'ix_apscheduler_jobs_next_run_time'"
            )
        ).scalar_one_or_none()
        assert idx == "ix_apscheduler_jobs_next_run_time"

        # app_rls got the DML grant despite the table pre-existing (GRANT is
        # outside the CREATE, so it runs regardless).
        from ibkr_control.db.rls import APP_ROLE

        privs = (
            conn.execute(
                text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE table_name = 'apscheduler_jobs' AND grantee = :role"
                ),
                {"role": APP_ROLE},
            )
            .scalars()
            .all()
        )
        assert {"SELECT", "INSERT", "UPDATE", "DELETE"}.issubset(set(privs))

    engine.dispose()
