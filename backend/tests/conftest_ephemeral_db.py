"""Ephemeral postgres fixtures for cross-schema replay tests (R3).

Boots a fresh postgres container PER test (function scope), monkeypatches
DATABASE_URL, runs `alembic upgrade head`, and yields an async session
factory. Used by test_flex_ingest_replay.py + similar tests.

Pattern is the same as test_migrations.py::fresh_postgres but wrapped as
reusable fixtures with the alembic upgrade pre-applied.
"""
import asyncio
from pathlib import Path

import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer


@pytest_asyncio.fixture(scope="function")
async def ephemeral_postgres():
    """Boot a fresh postgres container for one test."""
    with PostgresContainer("postgres:16-alpine", driver="psycopg2") as pg:
        yield pg


@pytest_asyncio.fixture(scope="function")
async def ephemeral_db_url(ephemeral_postgres):
    """Async DSN of the ephemeral postgres (asyncpg driver for the app)."""
    sync_url = ephemeral_postgres.get_connection_url()
    return sync_url.replace("+psycopg2", "+asyncpg")


@pytest_asyncio.fixture(scope="function")
async def ephemeral_session_factory(ephemeral_postgres, ephemeral_db_url, monkeypatch):
    """Async sessionmaker bound to ephemeral postgres at HEAD revision.

    Side-effects:
    - Sets DATABASE_URL env var to the ephemeral DSN.
    - Clears get_settings() lru_cache so app code re-reads the env.
    - Runs alembic upgrade head against the container.
    """
    monkeypatch.setenv("DATABASE_URL", ephemeral_db_url)
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")

    from ibkr_control.config import get_settings
    get_settings.cache_clear()

    backend_root = Path(__file__).resolve().parent.parent
    alembic_ini = backend_root / "alembic.ini"
    cfg = Config(str(alembic_ini))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))

    # alembic command.upgrade is sync — run in thread to not block event loop
    await asyncio.to_thread(command.upgrade, cfg, "head")

    engine = create_async_engine(ephemeral_db_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()
