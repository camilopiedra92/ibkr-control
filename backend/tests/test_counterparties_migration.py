"""Migracion counterparties + drop transfer_lots (spec 2026-06-02)."""
import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer

DOWN_REV = "2b0b2863c6e9"


def _alembic_cfg(async_url: str) -> Config:
    import os
    os.environ["DATABASE_URL"] = async_url
    os.environ["JWT_SECRET"] = "test-secret-32-chars-minimum-please-ok"
    from ibkr_control.config import get_settings
    get_settings.cache_clear()
    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    return cfg


@pytest.mark.asyncio
async def test_upgrade_reconciles_orphan_and_drops_transfer_lots():
    with PostgresContainer("postgres:16-alpine", driver="psycopg2") as pg:
        sync_url = pg.get_connection_url()
        async_url = sync_url.replace("+psycopg2", "+asyncpg")
        cfg = _alembic_cfg(async_url)

        # (1) subir hasta down_revision (schema previo a este cambio)
        await asyncio.to_thread(command.upgrade, cfg, DOWN_REV)

        # (2) seed: cuenta propia U1, account huerfano CSX, transfer FOP IN
        eng = create_async_engine(async_url)
        async with eng.begin() as conn:
            u1 = await conn.scalar(text(
                "INSERT INTO accounts (ibkr_account_id, currency) "
                "VALUES ('U99999001','USD') RETURNING id"))
            csx = await conn.scalar(text(
                "INSERT INTO accounts (ibkr_account_id, currency) "
                "VALUES ('CS-999999-99','USD') RETURNING id"))
            await conn.execute(text(
                "INSERT INTO transfers (transaction_id, transfer_date, direction, "
                "src_account_id, dst_account_id, symbol, qty, transfer_type) "
                "VALUES ('T-FOP-1','2026-04-30','IN', :csx, :u1, 'GLOB', 94, 'FOP')"
            ), {"csx": csx, "u1": u1})

        # (3) upgrade head (aplica la revision nueva)
        await asyncio.to_thread(command.upgrade, cfg, "head")

        async with eng.begin() as conn:
            cps = [r[0] for r in await conn.execute(text("SELECT external_id FROM counterparties"))]
            assert "CS-999999-99" in cps, f"orphan not in counterparties: {cps}"
            row = (await conn.execute(text(
                "SELECT src_account_id, src_counterparty_id FROM transfers "
                "WHERE transaction_id='T-FOP-1'"))).first()
            assert row[0] is None, "src_account_id should be NULL after re-pointing to counterparty"
            assert row[1] is not None, "src_counterparty_id should be set after re-pointing"
            n_orphan = await conn.scalar(text(
                "SELECT count(*) FROM accounts WHERE ibkr_account_id='CS-999999-99'"))
            assert n_orphan == 0, "orphan account should be deleted from accounts table"
            assert await conn.scalar(text("SELECT to_regclass('transfer_lots')")) is None, \
                "transfer_lots table should be dropped"

        await eng.dispose()


@pytest.mark.asyncio
async def test_downgrade_reverts_cleanly():
    with PostgresContainer("postgres:16-alpine", driver="psycopg2") as pg:
        async_url = pg.get_connection_url().replace("+psycopg2", "+asyncpg")
        cfg = _alembic_cfg(async_url)
        await asyncio.to_thread(command.upgrade, cfg, "head")
        await asyncio.to_thread(command.downgrade, cfg, DOWN_REV)
        eng = create_async_engine(async_url)
        async with eng.begin() as conn:
            assert await conn.scalar(text("SELECT to_regclass('transfer_lots')")) is not None, \
                "transfer_lots should be restored after downgrade"
            assert await conn.scalar(text("SELECT to_regclass('counterparties')")) is None, \
                "counterparties should be dropped after downgrade"
        await eng.dispose()
