"""Tests del modelo Counterparty + exclusive arc en transfers (spec 2026-06-02)."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_counterparties_table_exists(db_session: AsyncSession):
    result = await db_session.execute(text("SELECT to_regclass('counterparties')"))
    assert result.scalar() == "counterparties"


@pytest.mark.asyncio
async def test_counterparty_external_id_unique(db_session: AsyncSession, sample_org):
    from ibkr_control.db.models.counterparties import Counterparty

    db_session.add(Counterparty(organization_id=sample_org.id, external_id="CS-999999-99"))
    await db_session.commit()
    db_session.add(Counterparty(organization_id=sample_org.id, external_id="CS-999999-99"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def _seed_account_and_cp(db_session, org_id):
    from ibkr_control.db.models.accounts import Account
    from ibkr_control.db.models.counterparties import Counterparty

    a = Account(organization_id=org_id, ibkr_account_id="U99999001", currency="USD")
    cp = Counterparty(organization_id=org_id, external_id="CS-999999-99")
    db_session.add_all([a, cp])
    await db_session.commit()
    await db_session.refresh(a)
    await db_session.refresh(cp)
    return a, cp


async def _insert_transfer(db_session, org_id, **overrides):
    cols = {
        "organization_id": org_id,
        "transaction_id": "T-ARC-1",
        "transfer_date": date(2026, 4, 30),
        "direction": "IN",
        "src_account_id": None,
        "src_counterparty_id": None,
        "dst_account_id": None,
        "dst_counterparty_id": None,
        "symbol": "GLOB",
        "qty": Decimal("94"),
        "transfer_type": "FOP",
    }
    cols.update(overrides)
    await db_session.execute(
        text(
            """INSERT INTO transfers
           (organization_id, transaction_id, transfer_date, direction, src_account_id,
            src_counterparty_id, dst_account_id, dst_counterparty_id, symbol, qty, transfer_type)
           VALUES (:organization_id, :transaction_id, :transfer_date, :direction, :src_account_id,
            :src_counterparty_id, :dst_account_id, :dst_counterparty_id, :symbol, :qty, :transfer_type)"""
        ),
        cols,
    )


@pytest.mark.asyncio
async def test_exclusive_arc_accepts_exactly_one_per_side(db_session: AsyncSession, sample_org):
    a, cp = await _seed_account_and_cp(db_session, sample_org.id)
    # src = counterparty (externo), dst = account propio -> valido
    await _insert_transfer(
        db_session, sample_org.id, src_counterparty_id=cp.id, dst_account_id=a.id
    )
    await db_session.commit()  # no debe rebotar


@pytest.mark.asyncio
async def test_exclusive_arc_rejects_both_null(db_session: AsyncSession, sample_org):
    a, cp = await _seed_account_and_cp(db_session, sample_org.id)
    # src vacio (both null) -> rebota; asyncpg fires CHECK at execute time, not commit
    with pytest.raises(IntegrityError):
        await _insert_transfer(
            db_session,
            sample_org.id,
            src_account_id=None,
            src_counterparty_id=None,
            dst_account_id=a.id,
        )
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_exclusive_arc_rejects_both_set(db_session: AsyncSession, sample_org):
    a, cp = await _seed_account_and_cp(db_session, sample_org.id)
    # src = account Y counterparty a la vez -> rebota; asyncpg fires CHECK at execute time
    with pytest.raises(IntegrityError):
        await _insert_transfer(
            db_session,
            sample_org.id,
            src_account_id=a.id,
            src_counterparty_id=cp.id,
            dst_account_id=a.id,
        )
        await db_session.commit()
    await db_session.rollback()
