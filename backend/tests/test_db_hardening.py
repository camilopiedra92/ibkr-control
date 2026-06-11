"""Behavior locks de sp1-db-hardening.

D4: borrar una cuenta con ledger colgando debe fallar (RESTRICT — antes era
NO ACTION implícito; mismo efecto en deletes directos, ahora declarado y
lockeado).
D4-bis: borrar la ORGANIZACIÓN entera (tenant wipe) SÍ funciona — la cascada
multi-path (org->accounts CASCADE + org->trades CASCADE) resuelve limpio aun
con RESTRICT en trades.account_id. Verificado empíricamente 2026-06-10 contra
la dev DB; depende del orden de triggers de Postgres (detalle de
implementación), por eso se lockea: si una migración futura reordena los OIDs
de constraints y rompe el offboarding de tenant, este test lo detecta.
D5: created_at es first-seen — el ON CONFLICT DO UPDATE de snapshots NUNCA
lo pisa (no está en update_cols del persister).
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from ibkr_control.db.models.accounts import Account
from ibkr_control.db.models.flex_raw import OpenPositionLot, Trade
from ibkr_control.db.models.instruments import Instrument
from ibkr_control.db.models.organizations import Organization
from ibkr_control.ingest.flex._upsert_helpers import _upsert_snapshot


async def _make_instrument(db_session) -> int:
    """Seed a control-plane Instrument (W2 NOT NULL FK on facts). Returns its id."""
    inst = Instrument(symbol="VOO", asset_class="STK")
    db_session.add(inst)
    await db_session.flush()
    return inst.id


# Mismas listas que usa el persister para OpenPositionLot (persister.py
# ~línea 466): si el persister algún día agrega created_at a update_cols,
# este test deja de representar la realidad — actualizar AMBOS o ninguno.
_OPL_KEY = ["account_id", "symbol", "open_date", "snapshot_date", "originating_transaction_id"]
_OPL_UPDATE = [
    "flex_import_id",
    "asset_class",
    "qty",
    "cost_basis_usd",
    "mark_price_usd",
    "mark_value_usd",
]


def _trade_row(org_id: int, account_id: int, txn_id: str, instrument_id: int) -> Trade:
    return Trade(
        organization_id=org_id,
        transaction_id=txn_id,
        account_id=account_id,
        instrument_id=instrument_id,
        symbol="VOO",
        asset_class="STK",
        trade_date=date(2026, 1, 5),
        qty=Decimal("1"),
        price_usd=Decimal("500"),
        proceeds_usd=Decimal("-500"),
        commission_usd=Decimal("-1"),
        buy_sell="BUY",
    )


async def test_delete_account_with_trades_is_restricted(db_session, sample_org, sample_account):
    """D4: la FK trades.account_id -> accounts es RESTRICT."""
    iid = await _make_instrument(db_session)
    db_session.add(_trade_row(sample_org.id, sample_account.id, "T-RESTRICT-1", iid))
    await db_session.flush()

    with pytest.raises(IntegrityError):
        await db_session.execute(delete(Account).where(Account.id == sample_account.id))
    await db_session.rollback()


async def test_delete_organization_cascades_full_tenant(db_session, sample_org, sample_account):
    """D4-bis: tenant wipe via DELETE org funciona pese al RESTRICT (multi-path)."""
    iid = await _make_instrument(db_session)
    db_session.add(_trade_row(sample_org.id, sample_account.id, "T-CASCADE-1", iid))
    await db_session.flush()

    await db_session.execute(delete(Organization).where(Organization.id == sample_org.id))

    remaining_accounts = (
        (await db_session.execute(select(Account).where(Account.organization_id == sample_org.id)))
        .scalars()
        .all()
    )
    remaining_trades = (
        (await db_session.execute(select(Trade).where(Trade.organization_id == sample_org.id)))
        .scalars()
        .all()
    )
    assert remaining_accounts == []
    assert remaining_trades == []


async def test_created_at_populated_and_first_seen_on_reupsert(
    db_session, sample_org, sample_account
):
    """D5: created_at se puebla en INSERT y sobrevive re-upserts snapshot."""
    iid = await _make_instrument(db_session)
    base_row = {
        "organization_id": sample_org.id,
        "account_id": sample_account.id,
        "instrument_id": iid,
        "symbol": "VOO",
        "asset_class": "STK",
        "open_date": date(2026, 1, 5),
        "snapshot_date": date(2026, 6, 1),
        "originating_transaction_id": "OTID-1",
        "qty": Decimal("10"),
        "cost_basis_usd": Decimal("5000.0000"),
        "flex_import_id": None,
        "mark_price_usd": None,
        "mark_value_usd": None,
    }
    await _upsert_snapshot(
        db_session, OpenPositionLot.__table__, [dict(base_row)], _OPL_KEY, _OPL_UPDATE
    )
    row = (
        await db_session.execute(
            select(OpenPositionLot).where(OpenPositionLot.originating_transaction_id == "OTID-1")
        )
    ).scalar_one()
    assert row.created_at is not None  # server_default pobló en INSERT

    # Sentinel: NOW() dentro de una misma transacción es constante, así que
    # para probar que el DO UPDATE no pisa created_at lo movemos a un valor
    # imposible de reproducir y re-upserteamos.
    sentinel = datetime(2020, 1, 1, tzinfo=UTC)
    await db_session.execute(update(OpenPositionLot).values(created_at=sentinel))

    changed = dict(base_row)
    changed["qty"] = Decimal("12")
    await _upsert_snapshot(db_session, OpenPositionLot.__table__, [changed], _OPL_KEY, _OPL_UPDATE)
    db_session.expire_all()
    row2 = (
        await db_session.execute(
            select(OpenPositionLot).where(OpenPositionLot.originating_transaction_id == "OTID-1")
        )
    ).scalar_one()
    assert row2.qty == Decimal("12")  # el snapshot SÍ se actualizó
    assert row2.created_at == sentinel  # created_at NO se pisó (first-seen)
