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
    assert row[0] == 'NO'  # Rev2 promovio a NOT NULL post-wipe


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
    assert row[0] == 'NO'  # Rev2 promovio a NOT NULL post-wipe


@pytest.mark.asyncio
async def test_phase25_rev2_promotes_transaction_id_not_null(alembic_db_session):
    for table in ('closed_lots', 'cash_transactions', 'transfers'):
        result = await alembic_db_session.execute(text(
            f"SELECT is_nullable FROM information_schema.columns "
            f"WHERE table_name = '{table}' AND column_name = 'transaction_id'"
        ))
        row = result.first()
        assert row is not None, f"transaction_id missing from {table}"
        assert row[0] == 'NO', f"{table}.transaction_id should be NOT NULL"


@pytest.mark.asyncio
async def test_phase25_rev2_adds_unique_constraints(alembic_db_session):
    """All 6 UNIQUE constraints from spec A3 + transaction_id must exist.

    NOTE: closed_lots_transaction_id_key (single-column UNIQUE created in
    Rev2) is dropped by Rev5 (A3 amendment #3) and replaced with the
    composite closed_lots_natural_key in Rev6 — verified by
    test_phase25_rev6_closed_lots_natural_key_has_close_datetime below.
    """
    expected_constraints = {
        'closed_lots_natural_key',
        'cash_transactions_transaction_id_key',
        'transfers_transaction_id_key',
        'open_position_lots_natural_key',
        'change_in_dividend_accruals_natural_key',
        'open_dividend_accruals_natural_key',
    }
    result = await alembic_db_session.execute(text(
        "SELECT conname FROM pg_constraint WHERE conname = ANY(:names)"
    ).bindparams(names=list(expected_constraints)))
    found = {row[0] for row in result.all()}
    assert found == expected_constraints, f"Missing: {expected_constraints - found}"


@pytest.mark.asyncio
async def test_phase25_rev6_closed_lots_natural_key_has_close_datetime(alembic_db_session):
    """Verify the natural key includes close_datetime + qty + fifo_pnl_usd per A3 #3."""
    result = await alembic_db_session.execute(text("""
        SELECT a.attname FROM pg_attribute a
        JOIN pg_constraint c ON a.attnum = ANY(c.conkey)
        WHERE c.conname = 'closed_lots_natural_key'
          AND a.attrelid = c.conrelid
        ORDER BY array_position(c.conkey, a.attnum)
    """))
    cols = [row[0] for row in result.all()]
    assert cols == ['transaction_id', 'close_datetime', 'qty', 'fifo_pnl_usd']


@pytest.mark.asyncio
async def test_phase25_rev6_close_datetime_not_null(alembic_db_session):
    result = await alembic_db_session.execute(text(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = 'closed_lots' AND column_name = 'close_datetime'"
    ))
    row = result.first()
    assert row is not None
    assert row[0] == 'NO'


@pytest.mark.asyncio
async def test_phase25_rev2_promotes_xml_bytes_not_null(alembic_db_session):
    result = await alembic_db_session.execute(text(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = 'flex_imports' AND column_name = 'xml_bytes'"
    ))
    row = result.first()
    assert row is not None
    assert row[0] == 'NO'


@pytest.mark.asyncio
async def test_phase25_rev2_open_position_lots_natural_key_enforced(alembic_db_session):
    """Insertar duplicate natural key (incluyendo otid post-Rev3) debe fallar.

    Updated for A3 amendment (2026-05-25 Rev3): natural key ahora incluye
    originating_transaction_id. Insertar dos rows con todas las columnas del
    key iguales (incluido el otid) debe fallar; insertar dos rows con mismo
    (account, symbol, open_date, snapshot_date) pero distinto otid debe pasar
    (caso multi-fill order — el escenario que motivó la amendment).
    """
    from sqlalchemy.exc import IntegrityError

    # Setup minimo: insertar account + flex_import + open_position_lots
    await alembic_db_session.execute(text("""
        INSERT INTO accounts (ibkr_account_id, currency)
        VALUES ('U99999099', 'USD') ON CONFLICT DO NOTHING
    """))
    await alembic_db_session.execute(text("""
        INSERT INTO users (id, name, email, hashed_password, is_active, is_superuser, is_verified)
        VALUES (9999, 'rev2test', 'rev2test@example.com', 'x', true, false, false)
        ON CONFLICT (id) DO NOTHING
    """))
    await alembic_db_session.execute(text("""
        INSERT INTO flex_imports (id, user_id, anyo, xml_hash, xml_size_bytes, xml_bytes,
                                  source, period_covered_from, period_covered_to,
                                  year_status, status, fetched_at)
        VALUES (9999, 9999, 2026, 'test-rev2-hash', 10, '\\x00010203'::bytea,
                'manual_upload', '2026-01-01', '2026-12-31', 'rolling', 'ok', now())
        ON CONFLICT DO NOTHING
    """))
    await alembic_db_session.commit()

    account_id = (await alembic_db_session.execute(
        text("SELECT id FROM accounts WHERE ibkr_account_id = 'U99999099'")
    )).scalar()

    # Primer insert OK (otid = 'OTID-001')
    await alembic_db_session.execute(text("""
        INSERT INTO open_position_lots
          (flex_import_id, account_id, symbol, open_date, qty, cost_basis_usd, snapshot_date, originating_transaction_id)
        VALUES (9999, :acc, 'AAPL', '2026-01-15', 10, 1500, '2026-05-25', 'OTID-001')
    """).bindparams(acc=account_id))
    await alembic_db_session.commit()

    # Insert con mismo (account, symbol, open_date, snapshot_date) pero distinto otid
    # DEBE pasar — este es el caso multi-fill que la amendment habilita.
    await alembic_db_session.execute(text("""
        INSERT INTO open_position_lots
          (flex_import_id, account_id, symbol, open_date, qty, cost_basis_usd, snapshot_date, originating_transaction_id)
        VALUES (9999, :acc, 'AAPL', '2026-01-15', 20, 3000, '2026-05-25', 'OTID-002')
    """).bindparams(acc=account_id))
    await alembic_db_session.commit()

    # Insert con TODA la natural key igual (incluido otid='OTID-001') DEBE fallar.
    with pytest.raises(IntegrityError):
        await alembic_db_session.execute(text("""
            INSERT INTO open_position_lots
              (flex_import_id, account_id, symbol, open_date, qty, cost_basis_usd, snapshot_date, originating_transaction_id)
            VALUES (9999, :acc, 'AAPL', '2026-01-15', 5, 750, '2026-05-25', 'OTID-001')
        """).bindparams(acc=account_id))
        await alembic_db_session.commit()
    await alembic_db_session.rollback()


@pytest.mark.asyncio
async def test_phase25_rev3_natural_key_has_otid_column(alembic_db_session):
    """A3 amendment Rev3 (2026-05-25): natural key on open_position_lots includes
    originating_transaction_id as 5th column."""
    result = await alembic_db_session.execute(text("""
        SELECT a.attname
        FROM pg_constraint c
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
        WHERE c.conname = 'open_position_lots_natural_key'
        ORDER BY array_position(c.conkey, a.attnum)
    """))
    cols = [row[0] for row in result.all()]
    assert cols == [
        'account_id', 'symbol', 'open_date', 'snapshot_date', 'originating_transaction_id'
    ], f"Natural key columns wrong order/contents: {cols}"


@pytest.mark.asyncio
async def test_phase25_rev3_originating_transaction_id_not_null(alembic_db_session):
    """A3 amendment: originating_transaction_id must be NOT NULL post-Rev3."""
    result = await alembic_db_session.execute(text(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = 'open_position_lots' "
        "AND column_name = 'originating_transaction_id'"
    ))
    row = result.first()
    assert row is not None, "originating_transaction_id column missing"
    assert row[0] == 'NO', "originating_transaction_id should be NOT NULL post-Rev3"


@pytest.mark.asyncio
async def test_phase25_rev4_change_in_accruals_natural_key_has_code(alembic_db_session):
    """A3 amendment #2 Rev4 (2026-05-25): natural key on
    change_in_dividend_accruals extended with (report_date, action_id, code)."""
    result = await alembic_db_session.execute(text("""
        SELECT a.attname FROM pg_attribute a
        JOIN pg_constraint c ON a.attnum = ANY(c.conkey)
        WHERE c.conname = 'change_in_dividend_accruals_natural_key'
          AND a.attrelid = c.conrelid
        ORDER BY array_position(c.conkey, a.attnum)
    """))
    cols = [row[0] for row in result.all()]
    assert cols == [
        'account_id', 'conid', 'ex_date', 'pay_date', 'accrual_date',
        'report_date', 'action_id', 'code',
    ], f"change_in_dividend_accruals_natural_key columns wrong: {cols}"


@pytest.mark.asyncio
async def test_phase25_rev4_open_accruals_natural_key_has_code(alembic_db_session):
    """A3 amendment #2 preemptive mirror: open_dividend_accruals natural key
    extended with (action_id, code)."""
    result = await alembic_db_session.execute(text("""
        SELECT a.attname FROM pg_attribute a
        JOIN pg_constraint c ON a.attnum = ANY(c.conkey)
        WHERE c.conname = 'open_dividend_accruals_natural_key'
          AND a.attrelid = c.conrelid
        ORDER BY array_position(c.conkey, a.attnum)
    """))
    cols = [row[0] for row in result.all()]
    assert cols == [
        'account_id', 'conid', 'ex_date', 'pay_date', 'report_date',
        'action_id', 'code',
    ], f"open_dividend_accruals_natural_key columns wrong: {cols}"


@pytest.mark.asyncio
async def test_phase25_rev4_accruals_code_columns_not_null(alembic_db_session):
    """Both accrual tables must have `code` as NOT NULL post-Rev4."""
    for table in ('change_in_dividend_accruals', 'open_dividend_accruals'):
        result = await alembic_db_session.execute(text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = 'code'"
        ).bindparams(t=table))
        row = result.first()
        assert row is not None, f"`code` column missing from {table}"
        assert row[0] == 'NO', f"{table}.code should be NOT NULL post-Rev4"
