from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from testcontainers.postgres import PostgresContainer

import ibkr_control.db  # noqa: F401  (carga modelos en Base.metadata)
from ibkr_control.db.base import Base


@pytest.fixture
def fresh_postgres():
    # Container dedicado al test de migrations: garantizamos DB vacia.
    # No reusar el session-scoped del conftest porque este test necesita una DB
    # vacia y no-migrada para correr `alembic upgrade head` desde cero.
    with PostgresContainer("postgres:16-alpine", driver="psycopg2") as pg:
        yield pg


def test_migrations_apply_cleanly_and_match_metadata(fresh_postgres, monkeypatch):
    sync_url = fresh_postgres.get_connection_url()
    async_url = sync_url.replace("+psycopg2", "+asyncpg")

    # env.py lee get_settings().database_url y se lo pasa a async_engine_from_config.
    monkeypatch.setenv("DATABASE_URL", async_url)
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")
    from ibkr_control.config import get_settings

    get_settings.cache_clear()

    backend_root = Path(__file__).resolve().parents[1]
    alembic_ini = backend_root / "alembic.ini"
    assert alembic_ini.exists(), f"alembic.ini missing at {alembic_ini}"

    cfg = Config(str(alembic_ini))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))

    command.upgrade(cfg, "head")

    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    engine = create_engine(sync_url)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn,
            opts={
                "compare_type": True,
                "compare_server_default": True,
                "target_metadata": Base.metadata,
            },
        )
        diffs = compare_metadata(ctx, Base.metadata)
    engine.dispose()

    def _is_ignorable(diff):
        flat = diff if isinstance(diff, tuple) else (diff,)
        text = repr(flat)
        return "apscheduler_jobs" in text or "alembic_version" in text

    real = [d for d in diffs if not _is_ignorable(d)]
    assert not real, (
        "DRIFT entre migraciones y Base.metadata (columnas/tipos/índices/uniques/"
        "FKs/server_defaults):\n" + "\n".join(repr(d) for d in real)
    )
