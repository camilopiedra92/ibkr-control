"""Tests de los Core UPSERT helpers (spec A8)."""

from decimal import Decimal
from datetime import date
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.db.models.flex_raw import Trade, OpenPositionLot, Transfer
from ibkr_control.ingest.flex._upsert_helpers import (
    _upsert_immutable,
    _upsert_snapshot,
    _upsert_immutable_returning_inserted,
    _chunks,
)


def test_chunks_splits_iterable():
    assert list(_chunks([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]
    assert list(_chunks([], 10)) == []
    assert list(_chunks([1], 10)) == [[1]]


@pytest.mark.asyncio
async def test_upsert_immutable_inserts_then_noop(
    db_session: AsyncSession, sample_user, sample_account, sample_flex_import
):
    """Primera ronda inserta N rows, segunda devuelve 0 (DO NOTHING)."""
    base_row = dict(
        flex_import_id=sample_flex_import.id,
        account_id=sample_account.id,
        symbol="AAPL",
        asset_class="STK",
        trade_date=date(2025, 1, 15),
        settle_date=None,
        qty=Decimal("10"),
        price_usd=Decimal("150"),
        proceeds_usd=Decimal("-1500"),
        commission_usd=Decimal("1"),
        open_close="O",
        buy_sell="BUY",
        raw_attrs={},
    )
    rows = [dict(base_row, transaction_id=f"TX-{i}") for i in range(3)]

    n_new_first = await _upsert_immutable(db_session, Trade.__table__, rows, ["transaction_id"])
    assert n_new_first == 3

    n_new_second = await _upsert_immutable(db_session, Trade.__table__, rows, ["transaction_id"])
    assert n_new_second == 0


@pytest.mark.asyncio
async def test_upsert_snapshot_inserts_then_updates(
    db_session: AsyncSession, sample_account, sample_flex_import
):
    """Snapshot UPSERT: insert nuevo, update si conflicto, mantiene n_touched."""
    base_row = dict(
        flex_import_id=sample_flex_import.id,
        account_id=sample_account.id,
        symbol="AAPL",
        asset_class="STK",
        open_date=date(2025, 1, 15),
        snapshot_date=date(2025, 5, 25),
        qty=Decimal("10"),
        cost_basis_usd=Decimal("1500"),
        mark_price_usd=Decimal("170"),
        mark_value_usd=Decimal("1700"),
        originating_transaction_id="OTID-TEST-001",
    )
    n_first = await _upsert_snapshot(
        db_session,
        OpenPositionLot.__table__,
        [base_row],
        ["account_id", "symbol", "open_date", "snapshot_date", "originating_transaction_id"],
        [
            "asset_class",
            "qty",
            "cost_basis_usd",
            "mark_price_usd",
            "mark_value_usd",
            "flex_import_id",
        ],
    )
    assert n_first == 1

    updated = dict(base_row, mark_price_usd=Decimal("180"), mark_value_usd=Decimal("1800"))
    n_second = await _upsert_snapshot(
        db_session,
        OpenPositionLot.__table__,
        [updated],
        ["account_id", "symbol", "open_date", "snapshot_date", "originating_transaction_id"],
        [
            "asset_class",
            "qty",
            "cost_basis_usd",
            "mark_price_usd",
            "mark_value_usd",
            "flex_import_id",
        ],
    )
    assert n_second == 1  # touched (UPDATE path)

    # Verify update actually applied
    from sqlalchemy import select

    result = await db_session.execute(
        select(OpenPositionLot.mark_price_usd).where(
            OpenPositionLot.account_id == sample_account.id,
            OpenPositionLot.symbol == "AAPL",
            OpenPositionLot.snapshot_date == date(2025, 5, 25),
        )
    )
    assert result.scalar() == Decimal("180")


@pytest.mark.asyncio
async def test_upsert_immutable_returning_inserted_filters_noop(
    db_session: AsyncSession, sample_account, sample_flex_import
):
    """Returning helper para Transfers: solo rows insertadas, no NO-OP."""
    # src_account_id must be non-NULL (exclusive arc: exactly one of
    # src_account_id / src_counterparty_id required per ck_transfers_src_arc).
    base = dict(
        flex_import_id=sample_flex_import.id,
        transfer_date=date(2025, 3, 1),
        direction="IN",
        src_account_id=sample_account.id,
        dst_account_id=sample_account.id,
        symbol="MSFT",
        qty=Decimal("100"),
        transfer_type="ACATS",
    )
    rows_round1 = [dict(base, transaction_id="XFER-1"), dict(base, transaction_id="XFER-2")]
    inserted1 = await _upsert_immutable_returning_inserted(
        db_session,
        Transfer.__table__,
        rows_round1,
        ["transaction_id"],
        ["id", "transaction_id"],
    )
    assert {row.transaction_id for row in inserted1} == {"XFER-1", "XFER-2"}

    rows_round2 = [dict(base, transaction_id="XFER-2"), dict(base, transaction_id="XFER-3")]
    inserted2 = await _upsert_immutable_returning_inserted(
        db_session,
        Transfer.__table__,
        rows_round2,
        ["transaction_id"],
        ["id", "transaction_id"],
    )
    assert {row.transaction_id for row in inserted2} == {"XFER-3"}


@pytest.mark.asyncio
async def test_upsert_immutable_batches_large_input(
    db_session: AsyncSession,
    sample_user,
    sample_account,
    sample_flex_import,
    monkeypatch,
):
    """Con _BATCH_SIZE=4 y 15 rows → 4 batches sin exceder param limit."""
    from ibkr_control.ingest.flex import _upsert_helpers as mod

    monkeypatch.setattr(mod, "_BATCH_SIZE", 4)
    base_row = dict(
        flex_import_id=sample_flex_import.id,
        account_id=sample_account.id,
        symbol="AAPL",
        asset_class="STK",
        trade_date=date(2025, 1, 15),
        settle_date=None,
        qty=Decimal("10"),
        price_usd=Decimal("150"),
        proceeds_usd=Decimal("-1500"),
        commission_usd=Decimal("1"),
        open_close="O",
        buy_sell="BUY",
        raw_attrs={},
    )
    rows = [dict(base_row, transaction_id=f"TX-BATCH-{i}") for i in range(15)]
    n_new = await _upsert_immutable(db_session, Trade.__table__, rows, ["transaction_id"])
    assert n_new == 15
