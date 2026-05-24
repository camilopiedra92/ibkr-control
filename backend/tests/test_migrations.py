from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from testcontainers.postgres import PostgresContainer

import ibkr_control.db  # noqa: F401  (carga modelos en Base.metadata)
from ibkr_control.db.base import Base


@pytest.fixture
def fresh_postgres():
    # Container dedicado al test de migrations: garantizamos DB vacia.
    # No reusar el session-scoped del conftest porque ese crea tablas via
    # Base.metadata.create_all (otra ruta, no alembic).
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

    # Inspect via sync driver — mas simple que async run_sync para esta verificacion.
    engine = create_engine(sync_url)
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    engine.dispose()

    expected = set(Base.metadata.tables.keys())
    extra_alembic_tables = existing - expected - {"alembic_version"}
    missing = expected - existing

    assert not missing, f"tablas en Base.metadata faltantes tras upgrade head: {missing}"
    assert not extra_alembic_tables, (
        f"tablas creadas por migrations pero NO declaradas en Base.metadata "
        f"(drift): {extra_alembic_tables}"
    )
