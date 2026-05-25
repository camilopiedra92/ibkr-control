"""Tests del schema phase25 (Revision 1: idempotent_schema).

Estos tests corren contra una DB nueva con `alembic upgrade head` aplicado
(no contra `Base.metadata.create_all`), porque la Revision 1 introduce
columnas/constraints que todavia no estan en los modelos SQLAlchemy
(el sync de modelos viene en Task 6 del plan).
"""
import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer


def _run_alembic_upgrade(async_url: str) -> None:
    """Corre `alembic upgrade head` en un proceso/thread sin loop activo.

    El env.py de alembic invoca `asyncio.run()` adentro, que no puede
    correr dentro de un event loop ya activo (pytest-asyncio). Por eso
    esta funcion se invoca via `asyncio.to_thread` desde el fixture async.
    """
    import os
    os.environ["DATABASE_URL"] = async_url
    os.environ["JWT_SECRET"] = "test-secret-32-chars-minimum-please-ok"
    from ibkr_control.config import get_settings
    get_settings.cache_clear()

    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    command.upgrade(cfg, "head")


@pytest.fixture
async def alembic_db_session(monkeypatch):
    """Sesion contra una DB nueva con `alembic upgrade head` aplicado.

    Usa container dedicado (no el session-scoped de conftest) porque ese
    crea tablas via `Base.metadata.create_all` y los modelos aun no
    reflejan los cambios de Revision 1 (`n_observed_*`, `xml_bytes`,
    `transaction_id`, FK SET NULL). Recien Task 6 actualiza los modelos.
    """
    with PostgresContainer("postgres:16-alpine", driver="psycopg2") as pg:
        sync_url = pg.get_connection_url()
        async_url = sync_url.replace("+psycopg2", "+asyncpg")

        monkeypatch.setenv("DATABASE_URL", async_url)
        monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")
        from ibkr_control.config import get_settings
        get_settings.cache_clear()

        # Alembic env.py llama asyncio.run() — incompatible con el event loop
        # activo de pytest-asyncio. Lo corremos en un thread separado.
        await asyncio.to_thread(_run_alembic_upgrade, async_url)

        engine = create_async_engine(async_url)
        session_maker = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        async with session_maker() as session:
            yield session
        await engine.dispose()


@pytest.mark.asyncio
async def test_phase25_rev1_renames_observed_counters(alembic_db_session: AsyncSession):
    """Revision 1 debe renombrar n_trades -> n_observed_trades, etc."""
    result = await alembic_db_session.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'flex_imports' AND column_name LIKE 'n_observed_%'"
    ))
    names = {row[0] for row in result.all()}
    expected = {
        'n_observed_trades', 'n_observed_lots_closed', 'n_observed_open_lots',
        'n_observed_cash_tx', 'n_observed_dividends', 'n_observed_transfers',
    }
    assert expected.issubset(names), f"Missing renamed counters: {expected - names}"


@pytest.mark.asyncio
async def test_phase25_rev1_adds_n_new_counters(alembic_db_session: AsyncSession):
    result = await alembic_db_session.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'flex_imports' AND column_name LIKE 'n_new_%'"
    ))
    names = {row[0] for row in result.all()}
    expected = {
        'n_new_trades', 'n_new_lots_closed', 'n_new_open_lots',
        'n_new_cash_tx', 'n_new_dividends', 'n_new_transfers',
    }
    assert expected == names


@pytest.mark.asyncio
async def test_phase25_rev1_adds_xml_bytes_nullable(alembic_db_session: AsyncSession):
    result = await alembic_db_session.execute(text(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = 'flex_imports' AND column_name = 'xml_bytes'"
    ))
    row = result.first()
    assert row is not None
    assert row[0] == 'YES'  # nullable in Revision 1 (NOT NULL post-wipe in Rev2)


@pytest.mark.asyncio
async def test_phase25_rev1_makes_flex_import_id_nullable_with_set_null(
    alembic_db_session: AsyncSession,
):
    """FK debe estar como SET NULL en trades."""
    # pg_constraint.confdeltype es de tipo "char" (1-byte) — asyncpg lo entrega
    # como bytes. Casteamos a text para comparar como string.
    result = await alembic_db_session.execute(text("""
        SELECT confdeltype::text FROM pg_constraint
        WHERE conname = 'trades_flex_import_id_fkey'
    """))
    row = result.first()
    assert row is not None
    assert row[0] == 'n', f"Expected SET NULL (confdeltype='n'), got {row[0]!r}"


@pytest.mark.asyncio
async def test_phase25_rev1_adds_transaction_id_to_closed_lots_nullable(
    alembic_db_session: AsyncSession,
):
    result = await alembic_db_session.execute(text(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = 'closed_lots' AND column_name = 'transaction_id'"
    ))
    row = result.first()
    assert row is not None
    assert row[0] == 'YES'  # nullable en Rev1; NOT NULL en Rev2
