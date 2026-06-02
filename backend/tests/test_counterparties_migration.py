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


async def _seed_orphan_scenario(conn):
    """Seed own account U99999001 (with a participation, so it is NOT an orphan),
    orphan CS-999999-99 (no participation/facts), and a FOP IN transfer
    (src=orphan, dst=own). Returns (u1_id, csx_id)."""
    uid = await conn.scalar(
        text(
            "INSERT INTO users (name, email, hashed_password, is_active, "
            "is_superuser, is_verified) "
            "VALUES ('t','t@example.com','x', true, false, false) RETURNING id"
        )
    )
    u1 = await conn.scalar(
        text(
            "INSERT INTO accounts (ibkr_account_id, currency) "
            "VALUES ('U99999001','USD') RETURNING id"
        )
    )
    # participation marks U1 as an owned account -> excluded from orphan sweep
    await conn.execute(
        text(
            "INSERT INTO participations (user_id, account_id, pct, valid_from) "
            "VALUES (:uid, :u1, 1.0, '2026-01-01')"
        ),
        {"uid": uid, "u1": u1},
    )
    csx = await conn.scalar(
        text(
            "INSERT INTO accounts (ibkr_account_id, currency) "
            "VALUES ('CS-999999-99','USD') RETURNING id"
        )
    )
    await conn.execute(
        text(
            "INSERT INTO transfers (transaction_id, transfer_date, direction, "
            "src_account_id, dst_account_id, symbol, qty, transfer_type) "
            "VALUES ('T-FOP-1','2026-04-30','IN', :csx, :u1, 'GLOB', 94, 'FOP')"
        ),
        {"csx": csx, "u1": u1},
    )
    return u1, csx


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
            u1, _csx = await _seed_orphan_scenario(conn)

        # (3) upgrade head (aplica la revision nueva)
        await asyncio.to_thread(command.upgrade, cfg, "head")

        async with eng.begin() as conn:
            cps = [r[0] for r in await conn.execute(text("SELECT external_id FROM counterparties"))]
            assert "CS-999999-99" in cps, f"orphan not in counterparties: {cps}"
            row = (
                await conn.execute(
                    text(
                        "SELECT src_account_id, src_counterparty_id, "
                        "dst_account_id, dst_counterparty_id FROM transfers "
                        "WHERE transaction_id='T-FOP-1'"
                    )
                )
            ).first()
            assert row[0] is None, "src_account_id should be NULL after re-pointing to counterparty"
            assert row[1] is not None, "src_counterparty_id should be set after re-pointing"
            # dst is the own account U1: must stay an account FK, not moved to counterparty.
            assert row[2] == u1, "dst_account_id should remain the own account (U1)"
            assert row[3] is None, "dst_counterparty_id should remain NULL (own account, not moved)"
            n_orphan = await conn.scalar(
                text("SELECT count(*) FROM accounts WHERE ibkr_account_id='CS-999999-99'")
            )
            assert n_orphan == 0, "orphan account should be deleted from accounts table"
            assert await conn.scalar(text("SELECT to_regclass('transfer_lots')")) is None, (
                "transfer_lots table should be dropped"
            )

        await eng.dispose()


@pytest.mark.asyncio
async def test_downgrade_reverts_cleanly():
    with PostgresContainer("postgres:16-alpine", driver="psycopg2") as pg:
        async_url = pg.get_connection_url().replace("+psycopg2", "+asyncpg")
        cfg = _alembic_cfg(async_url)

        # (1) subir hasta down_revision (schema previo a este cambio)
        await asyncio.to_thread(command.upgrade, cfg, DOWN_REV)

        # (2) seed mismo escenario huerfano que el upgrade test, para que el
        #     loop de reversion de data corra >0 iteraciones al hacer downgrade.
        eng = create_async_engine(async_url)
        async with eng.begin() as conn:
            await _seed_orphan_scenario(conn)

        # (3) upgrade head (mueve el orphan a counterparties), luego downgrade
        await asyncio.to_thread(command.upgrade, cfg, "head")
        await asyncio.to_thread(command.downgrade, cfg, DOWN_REV)

        async with eng.begin() as conn:
            assert await conn.scalar(text("SELECT to_regclass('transfer_lots')")) is not None, (
                "transfer_lots should be restored after downgrade"
            )
            assert await conn.scalar(text("SELECT to_regclass('counterparties')")) is None, (
                "counterparties should be dropped after downgrade"
            )
            # orphan restaurado en accounts
            n_orphan = await conn.scalar(
                text("SELECT count(*) FROM accounts WHERE ibkr_account_id='CS-999999-99'")
            )
            assert n_orphan == 1, "orphan account should be restored to accounts table"
            # la columna src_counterparty_id ya no existe; el transfer vuelve a
            # apuntar via src_account_id (non-null, hacia la cuenta restaurada).
            src_acct = await conn.scalar(
                text("SELECT src_account_id FROM transfers WHERE transaction_id='T-FOP-1'")
            )
            assert src_acct is not None, "src_account_id should be non-null after downgrade"
            restored = await conn.scalar(
                text("SELECT ibkr_account_id FROM accounts WHERE id=:a"), {"a": src_acct}
            )
            assert restored == "CS-999999-99", (
                "transfer src should point to the restored orphan account"
            )
        await eng.dispose()
