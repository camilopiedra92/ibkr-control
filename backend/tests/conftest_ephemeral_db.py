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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer


def swap_dsn_credentials(async_dsn: str, user: str, password: str) -> str:
    """Return ``async_dsn`` with its user:password swapped for ``user:password``.

    Splits on ``://`` to keep the scheme, then on ``@`` to drop the existing
    creds and re-attach the host+db part. Shared by ``rls_session_factory`` and
    (Task 14) ``app_with_db`` to connect as the non-bypass ``app_rls`` role.

    Caveat: assumes the credentials contain no ``@`` (it splits on the FIRST
    ``@``). Current callers (``test:test``, ``app_rls:app_rls_pw``) are fine.
    """
    scheme, after_scheme = async_dsn.split("://", 1)
    _creds, hostpart = after_scheme.split("@", 1)
    return f"{scheme}://{user}:{password}@{hostpart}"


def build_alembic_config() -> Config:
    """Build an Alembic ``Config`` pointing at this backend's alembic dir.

    Shared by ``ephemeral_session_factory`` and (Task 14) ``app_with_db`` so the
    ``alembic upgrade head`` path is defined in one place.
    """
    backend_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    return cfg


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

    cfg = build_alembic_config()

    # alembic command.upgrade is sync — run in thread to not block event loop
    await asyncio.to_thread(command.upgrade, cfg, "head")

    engine = create_async_engine(ephemeral_db_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def rls_session_factory(ephemeral_session_factory, ephemeral_db_url):
    """(app_factory, seed) for RLS isolation tests.

    - ``app_factory`` opens sessions connected as the non-superuser, non-bypass
      ``app_rls`` login role, so RLS policies (created FORCE in the baseline
      migration) actually apply to every query.
    - ``seed(org_name, ibkr_account_id) -> org_id`` inserts an Organization plus
      one Account for it. It connects as the container OWNER (the role that ran
      the migrations). Because RLS is FORCEd, the owner is ALSO subject to the
      WITH CHECK on org-scoped tables — so the seed sets ``app.current_org`` to
      the freshly-created org id (organizations has NO RLS, so it inserts freely)
      before inserting the Account, ensuring the WITH CHECK passes.

    ``set_config(..., true)`` is SET LOCAL: it lives only for the current
    transaction. The async session's autobegin opens the transaction on the
    first statement, so the set_config + insert + commit all share one tx.
    """
    from ibkr_control.db.models.accounts import Account
    from ibkr_control.db.models.organizations import Organization

    owner_factory = ephemeral_session_factory

    async def seed(org_name: str, ibkr_account_id: str) -> int:
        async with owner_factory() as s:
            org = Organization(type="personal", name=org_name)
            s.add(org)
            await s.flush()
            # FORCE RLS applies to the owner too -> set context so the Account
            # WITH CHECK (organization_id = current_org) passes.
            await s.execute(
                text("SELECT set_config('app.current_org', :o, true)").bindparams(o=str(org.id))
            )
            s.add(
                Account(
                    ibkr_account_id=ibkr_account_id,
                    organization_id=org.id,
                    currency="USD",
                )
            )
            await s.commit()
            return org.id

    # Connect as app_rls by swapping the creds in the asyncpg DSN.
    app_dsn = swap_dsn_credentials(ephemeral_db_url, "app_rls", "app_rls_pw")
    app_engine = create_async_engine(app_dsn, echo=False)
    app_factory = async_sessionmaker(app_engine, expire_on_commit=False)
    try:
        yield app_factory, seed
    finally:
        await app_engine.dispose()
