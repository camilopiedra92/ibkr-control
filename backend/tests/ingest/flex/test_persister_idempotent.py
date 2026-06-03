"""Integration tests: persister idempotente bajo re-ingest scenarios cross-XML.

Cubren los happy paths donde el MISMO contenido logico se ve en XMLs
con bytes distintos (caso central del cron diario YTD: cada XML cambia
byte-a-byte por mark prices/timestamp, pero la mayoria de los trades
son los mismos del dia anterior).

Spec: docs/specs/2026-05-25-flex-persister-idempotent-design.md
Plan: docs/plans/2026-05-25-flex-persister-rewrite.md Task 11.

Scenario #1 (same xml_bytes -> hash dedup) ya cubierto en
test_persister.py::test_persist_duplicate_hash_returns_existing_id.
Aqui van los 6 restantes que test_persister.py NO cubre.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.db.models.flex_raw import (
    FlexImport,
    OpenPositionLot,
    Trade,
    Transfer,
)
from ibkr_control.ingest.flex._models import (
    ParsedAccount,
    ParsedOpenPositionLot,
    ParsedTrade,
    ParsedTransfer,
    ParsedXML,
)
from ibkr_control.ingest.flex.persister import persist


def _minimal_parsed(
    n_trades: int = 1,
    account_id: str = "U99999001",
    anyo: int = 2026,
) -> ParsedXML:
    return ParsedXML(
        anyo=anyo,
        period_from=date(anyo, 1, 1),
        period_to=date(anyo, 5, 25) if anyo == 2026 else date(anyo, 12, 31),
        accounts=[ParsedAccount(ibkr_account_id=account_id, currency="USD")],
        trades=[
            ParsedTrade(
                transaction_id=f"TX-IDEMP-{i}",
                ibkr_account_id=account_id,
                symbol="AAPL",
                asset_class="STK",
                trade_date=date(anyo, 1, 15 + i),
                settle_date=date(anyo, 1, 17 + i),
                qty=Decimal("10"),
                price_usd=Decimal("150"),
                proceeds_usd=Decimal("-1500"),
                commission_usd=Decimal("1"),
                open_close="O",
                buy_sell="BUY",
                raw_attrs={},
            )
            for i in range(n_trades)
        ],
        closed_lots=[],
        open_position_lots=[],
        cash_transactions=[],
        transfers=[],
        change_in_dividend_accruals=[],
        open_dividend_accruals=[],
    )


@pytest.mark.asyncio
async def test_persist_modified_xml_same_trades_yields_zero_n_new(
    db_session: AsyncSession, sample_user, sample_account
):
    """XML modificado byte-a-byte pero trades identicos -> n_new_trades=0,
    no duplicates. Este es el caso central del cron diario YTD."""
    parsed = _minimal_parsed(n_trades=2, account_id=sample_account.ibkr_account_id)
    _, c1 = await persist(
        db_session,
        parsed=parsed,
        user_id=sample_user.id,
        xml_bytes=b"<v1/>",
        source="web_service",
    )
    await db_session.commit()
    assert c1["n_new_trades"] == 2

    # v2: diff hash, mismos trades
    _, c2 = await persist(
        db_session,
        parsed=parsed,
        user_id=sample_user.id,
        xml_bytes=b"<v2 changed/>",
        source="web_service",
    )
    assert c2["hash_dedup"] is False
    assert c2["n_new_trades"] == 0
    assert c2["n_observed_trades"] == 2

    total = await db_session.scalar(select(func.count()).select_from(Trade))
    assert total == 2


@pytest.mark.asyncio
async def test_persist_modified_xml_plus_one_new_trade(
    db_session: AsyncSession, sample_user, sample_account
):
    """v2 trae 1 trade adicional -> n_new=1, total en DB = 3."""
    parsed_v1 = _minimal_parsed(n_trades=2, account_id=sample_account.ibkr_account_id)
    await persist(
        db_session,
        parsed=parsed_v1,
        user_id=sample_user.id,
        xml_bytes=b"<v1/>",
        source="web_service",
    )
    await db_session.commit()

    parsed_v2 = _minimal_parsed(n_trades=3, account_id=sample_account.ibkr_account_id)
    _, c2 = await persist(
        db_session,
        parsed=parsed_v2,
        user_id=sample_user.id,
        xml_bytes=b"<v2/>",
        source="web_service",
    )
    assert c2["n_new_trades"] == 1
    total = await db_session.scalar(select(func.count()).select_from(Trade))
    assert total == 3


@pytest.mark.asyncio
async def test_snapshot_lot_mark_price_updates_in_place(
    db_session: AsyncSession, sample_user, sample_account
):
    """Mismo (account, symbol, open_date, snapshot_date, otid) con mark_price
    distinto -> UPDATE en place, no duplicate row. A1-bis last-updated-by."""
    base_args = dict(
        ibkr_account_id=sample_account.ibkr_account_id,
        symbol="MSFT",
        asset_class="STK",
        open_date=date(2026, 1, 15),
        qty=Decimal("50"),
        cost_basis_usd=Decimal("15000"),
        snapshot_date=date(2026, 5, 25),
        originating_transaction_id="OTID-LOT-1",
    )
    p1 = _minimal_parsed(n_trades=0, account_id=sample_account.ibkr_account_id)
    p1.open_position_lots = [
        ParsedOpenPositionLot(
            **base_args,
            mark_price_usd=Decimal("400"),
            mark_value_usd=Decimal("20000"),
        )
    ]
    await persist(
        db_session,
        parsed=p1,
        user_id=sample_user.id,
        xml_bytes=b"<lot v1/>",
        source="web_service",
    )
    await db_session.commit()

    # v2: misma natural key (incluyendo mismo otid), mark_price subio
    p2 = _minimal_parsed(n_trades=0, account_id=sample_account.ibkr_account_id)
    p2.open_position_lots = [
        ParsedOpenPositionLot(
            **base_args,
            mark_price_usd=Decimal("420"),
            mark_value_usd=Decimal("21000"),
        )
    ]
    await persist(
        db_session,
        parsed=p2,
        user_id=sample_user.id,
        xml_bytes=b"<lot v2/>",
        source="web_service",
    )

    total_lots = await db_session.scalar(select(func.count()).select_from(OpenPositionLot))
    assert total_lots == 1  # NO duplicate

    current_mark = await db_session.scalar(
        select(OpenPositionLot.mark_price_usd).where(OpenPositionLot.symbol == "MSFT")
    )
    assert current_mark == Decimal("420")


@pytest.mark.asyncio
async def test_snapshot_lot_different_snapshot_date_creates_new_row(
    db_session: AsyncSession, sample_user, sample_account
):
    """snapshot_date distinto -> fila nueva (serie temporal preservada).
    Permite Patrimonio Dec 31 + analitica historica de mark prices."""
    base_args = dict(
        ibkr_account_id=sample_account.ibkr_account_id,
        symbol="MSFT",
        asset_class="STK",
        open_date=date(2026, 1, 15),
        qty=Decimal("50"),
        cost_basis_usd=Decimal("15000"),
        mark_price_usd=Decimal("400"),
        mark_value_usd=Decimal("20000"),
        originating_transaction_id="OTID-LOT-A",
    )
    p1 = _minimal_parsed(n_trades=0, account_id=sample_account.ibkr_account_id)
    p1.open_position_lots = [ParsedOpenPositionLot(**base_args, snapshot_date=date(2026, 5, 25))]
    p2 = _minimal_parsed(n_trades=0, account_id=sample_account.ibkr_account_id)
    p2.open_position_lots = [ParsedOpenPositionLot(**base_args, snapshot_date=date(2026, 5, 26))]
    await persist(
        db_session,
        parsed=p1,
        user_id=sample_user.id,
        xml_bytes=b"<day1/>",
        source="web_service",
    )
    await db_session.commit()
    await persist(
        db_session,
        parsed=p2,
        user_id=sample_user.id,
        xml_bytes=b"<day2/>",
        source="web_service",
    )

    total = await db_session.scalar(select(func.count()).select_from(OpenPositionLot))
    assert total == 2


@pytest.mark.asyncio
async def test_transfer_not_duplicated_on_reingest(
    db_session: AsyncSession, sample_user, sample_account
):
    """Re-ingest del mismo transfer NO lo duplica (ON CONFLICT transaction_id
    DO NOTHING). transfer_lots fue eliminado (impoblable desde Activity Flex,
    spec 2026-06-02)."""
    # src_ibkr_account_id uses the same account as dst so the persister can
    # resolve it via accounts_map. The test only checks idempotency (count=1),
    # not the business semantics of who sent the transfer.
    # (src_account_id=None would violate ck_transfers_src_arc added in Task 2;
    # the persister's counterparty mapping is a later task.)
    transfer = ParsedTransfer(
        transaction_id="XFER-IDEMP-1",
        transfer_date=date(2026, 4, 30),
        direction="IN",
        src_ibkr_account_id=sample_account.ibkr_account_id,
        dst_ibkr_account_id=sample_account.ibkr_account_id,
        symbol="GLOB",
        qty=Decimal("94"),
        transfer_type="FOP",
    )
    p1 = _minimal_parsed(n_trades=0, account_id=sample_account.ibkr_account_id)
    p1.transfers = [transfer]
    await persist(
        db_session,
        parsed=p1,
        user_id=sample_user.id,
        xml_bytes=b"<xfer v1/>",
        source="web_service",
    )
    await db_session.commit()

    p2 = _minimal_parsed(n_trades=0, account_id=sample_account.ibkr_account_id)
    p2.transfers = [transfer]
    await persist(
        db_session,
        parsed=p2,
        user_id=sample_user.id,
        xml_bytes=b"<xfer v2/>",
        source="web_service",
    )

    n_transfers = await db_session.scalar(select(func.count()).select_from(Transfer))
    assert n_transfers == 1


@pytest.mark.asyncio
async def test_xml_bytes_persisted(db_session: AsyncSession, sample_user):
    """A0: xml_bytes guardado en DB y recuperable post-fetch."""
    parsed = _minimal_parsed(n_trades=1)
    test_bytes = b"<replayable_xml>...payload...</replayable_xml>"
    fi_id, _ = await persist(
        db_session,
        parsed=parsed,
        user_id=sample_user.id,
        xml_bytes=test_bytes,
        source="manual_upload",
    )
    await db_session.commit()
    retrieved = await db_session.scalar(select(FlexImport.xml_bytes).where(FlexImport.id == fi_id))
    assert retrieved == test_bytes
