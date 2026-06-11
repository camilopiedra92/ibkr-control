"""W2 — securities master: _ensure_instruments + instrument_id threading.

Cubre la cadena conid -> identifier -> instrument -> FK en los hechos, la
idempotencia (re-persist no crea instruments nuevos), el ticker change (mismo
conid, symbol last-seen), y la semántica resolver-only de cash/transfers
(conid ausente o sin instrument => instrument_id NULL, NUNCA crea).
"""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.db.models.flex_raw import CashTransaction, Trade
from ibkr_control.db.models.instruments import Instrument, InstrumentIdentifier
from ibkr_control.ingest.flex._models import (
    ParsedAccount,
    ParsedCashTransaction,
    ParsedClosedLot,
    ParsedTrade,
    ParsedXML,
)
from ibkr_control.ingest.flex.parser import parse
from ibkr_control.ingest.flex.persister import persist

FIXTURE_DIR = Path(__file__).parent.parent.parent / "fixtures" / "xml"


def _trade(conid: str, symbol: str, account_id: str = "U99999001", *, txn: str) -> ParsedTrade:
    return ParsedTrade(
        transaction_id=txn,
        ibkr_account_id=account_id,
        symbol=symbol,
        asset_class="STK",
        conid=conid,
        isin=None,
        description="Acme Corp",
        currency="USD",
        multiplier=None,
        trade_date=date(2026, 1, 15),
        settle_date=date(2026, 1, 17),
        qty=Decimal("10"),
        price_usd=Decimal("150"),
        proceeds_usd=Decimal("-1500"),
        commission_usd=Decimal("-1"),
        open_close="O",
        buy_sell="BUY",
        raw_attrs={},
    )


def _xml(account_id: str, trades: list[ParsedTrade], **kw) -> ParsedXML:
    return ParsedXML(
        anyo=2026,
        period_from=date(2026, 1, 1),
        period_to=date(2026, 5, 25),
        accounts=[ParsedAccount(ibkr_account_id=account_id, currency="USD")],
        trades=trades,
        closed_lots=kw.get("closed_lots", []),
        open_position_lots=[],
        cash_transactions=kw.get("cash_transactions", []),
        transfers=[],
        change_in_dividend_accruals=[],
        open_dividend_accruals=[],
    )


@pytest.mark.asyncio
async def test_persist_populates_instruments_from_2025_fixture(
    db_session: AsyncSession, sample_org
):
    """Real fixture: instruments get created and every trade.instrument_id points
    at an instrument whose ('conid', ...) identifier matches the trade's conid."""
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    parsed = parse(xml)
    fi_id, _ = await persist(
        db_session,
        parsed=parsed,
        organization_id=sample_org.id,
        xml_bytes=xml,
        source="manual_upload",
    )

    n_instruments = await db_session.scalar(select(func.count()).select_from(Instrument))
    assert n_instruments > 0

    # Every trade has a non-null instrument_id whose conid identifier exists.
    n_trades = await db_session.scalar(
        select(func.count()).select_from(Trade).where(Trade.flex_import_id == fi_id)
    )
    n_null = await db_session.scalar(
        select(func.count())
        .select_from(Trade)
        .where(Trade.flex_import_id == fi_id, Trade.instrument_id.is_(None))
    )
    assert n_trades > 0
    assert n_null == 0

    # Spot-check AAL: its conid resolves to the instrument the trade points at.
    aal = await db_session.scalar(select(Trade).where(Trade.symbol == "AAL").limit(1))
    ident = await db_session.scalar(
        select(InstrumentIdentifier).where(
            InstrumentIdentifier.id_type == "conid",
            InstrumentIdentifier.id_value == "139673266",
        )
    )
    assert ident is not None
    assert aal.instrument_id == ident.instrument_id


@pytest.mark.asyncio
async def test_repersist_creates_zero_new_instruments(db_session: AsyncSession, sample_org):
    """Idempotency: re-running _ensure_instruments for the same conids (different
    xml_bytes so the hash fast-path does NOT short-circuit) creates no new rows."""
    p1 = _xml("U99999001", [_trade("265598", "AAPL", txn="TX-1")])
    await persist(
        db_session,
        parsed=p1,
        organization_id=sample_org.id,
        xml_bytes=b"<v1/>",
        source="manual_upload",
    )
    n1 = await db_session.scalar(select(func.count()).select_from(Instrument))

    p2 = _xml("U99999001", [_trade("265598", "AAPL", txn="TX-2")])
    await persist(
        db_session,
        parsed=p2,
        organization_id=sample_org.id,
        xml_bytes=b"<v2/>",
        source="manual_upload",
    )
    n2 = await db_session.scalar(select(func.count()).select_from(Instrument))
    assert n1 == n2 == 1


@pytest.mark.asyncio
async def test_ticker_change_updates_symbol_last_seen(db_session: AsyncSession, sample_org):
    """Same conid, symbol FB -> META: ONE instrument, symbol last-seen, updated_at
    advanced past created_at."""
    p1 = _xml("U99999001", [_trade("107113386", "FB", txn="TX-FB")])
    await persist(
        db_session,
        parsed=p1,
        organization_id=sample_org.id,
        xml_bytes=b"<fb/>",
        source="manual_upload",
    )
    inst = await db_session.scalar(select(Instrument))
    assert inst.symbol == "FB"
    # Force updated_at to be strictly older so a real bump is observable.
    from sqlalchemy import update

    await db_session.execute(
        update(Instrument)
        .where(Instrument.id == inst.id)
        .values(updated_at=datetime(2020, 1, 1), created_at=datetime(2020, 1, 1))
    )
    await db_session.flush()

    p2 = _xml("U99999001", [_trade("107113386", "META", txn="TX-META")])
    await persist(
        db_session,
        parsed=p2,
        organization_id=sample_org.id,
        xml_bytes=b"<meta/>",
        source="manual_upload",
    )
    n = await db_session.scalar(select(func.count()).select_from(Instrument))
    assert n == 1
    await db_session.refresh(inst)
    assert inst.symbol == "META"
    assert inst.updated_at > inst.created_at


@pytest.mark.asyncio
async def test_cash_without_conid_leaves_instrument_id_null(db_session: AsyncSession, sample_org):
    """A cash transaction with no conid (fee/interest) -> instrument_id NULL, no
    spurious instrument created."""
    cash = ParsedCashTransaction(
        transaction_id="CASH-1",
        ibkr_account_id="U99999001",
        type="Commissions",
        currency="USD",
        amount_usd=Decimal("-1.50"),
        description="commission",
        date=date(2026, 1, 15),
        symbol=None,
        conid=None,
    )
    p = _xml("U99999001", [], cash_transactions=[cash])
    await persist(
        db_session,
        parsed=p,
        organization_id=sample_org.id,
        xml_bytes=b"<cash/>",
        source="manual_upload",
    )
    n_instruments = await db_session.scalar(select(func.count()).select_from(Instrument))
    assert n_instruments == 0
    ct = await db_session.scalar(
        select(CashTransaction).where(CashTransaction.transaction_id == "CASH-1")
    )
    assert ct.instrument_id is None


@pytest.mark.asyncio
async def test_resolver_with_unknown_conid_does_not_create(db_session: AsyncSession, sample_org):
    """A cash transaction whose conid has NO instrument in the batch -> instrument_id
    NULL (resolver never creates instruments)."""
    cash = ParsedCashTransaction(
        transaction_id="CASH-DIV",
        ibkr_account_id="U99999001",
        type="Dividends",
        currency="USD",
        amount_usd=Decimal("10.00"),
        description="dividend",
        date=date(2026, 2, 1),
        symbol="ZZZZ",
        conid="999999999",  # no creator brought this conid
    )
    p = _xml("U99999001", [], cash_transactions=[cash])
    await persist(
        db_session,
        parsed=p,
        organization_id=sample_org.id,
        xml_bytes=b"<div/>",
        source="manual_upload",
    )
    n_instruments = await db_session.scalar(select(func.count()).select_from(Instrument))
    assert n_instruments == 0
    ct = await db_session.scalar(
        select(CashTransaction).where(CashTransaction.transaction_id == "CASH-DIV")
    )
    assert ct.instrument_id is None


@pytest.mark.asyncio
async def test_resolver_with_known_conid_resolves_to_instrument(
    db_session: AsyncSession, sample_org
):
    """If a creator (trade) brought conid X, a cash transaction with the same conid
    resolves to that instrument (resolver lookup hits)."""
    trade = _trade("265598", "AAPL", txn="TX-AAPL")
    cash = ParsedCashTransaction(
        transaction_id="CASH-AAPL-DIV",
        ibkr_account_id="U99999001",
        type="Dividends",
        currency="USD",
        amount_usd=Decimal("5.00"),
        description="AAPL dividend",
        date=date(2026, 2, 1),
        symbol="AAPL",
        conid="265598",
    )
    p = _xml("U99999001", [trade], cash_transactions=[cash])
    await persist(
        db_session,
        parsed=p,
        organization_id=sample_org.id,
        xml_bytes=b"<aapl-div/>",
        source="manual_upload",
    )
    ident = await db_session.scalar(
        select(InstrumentIdentifier).where(
            InstrumentIdentifier.id_type == "conid",
            InstrumentIdentifier.id_value == "265598",
        )
    )
    ct = await db_session.scalar(
        select(CashTransaction).where(CashTransaction.transaction_id == "CASH-AAPL-DIV")
    )
    assert ct.instrument_id == ident.instrument_id


@pytest.mark.asyncio
async def test_fop_closed_lot_creates_instrument(db_session: AsyncSession, sample_org):
    """A FOP-acquired closed lot (no opening trade) is still a CREATOR: its conid
    creates an instrument and the lot's instrument_id is non-null."""
    lot = ParsedClosedLot(
        ibkr_account_id="U99999002",
        symbol="GLOB",
        asset_class="STK",
        conid="GLOB-CONID",
        isin=None,
        description="Globant SA",
        currency="USD",
        multiplier=None,
        open_date=date(2026, 4, 28),
        close_date=date(2026, 5, 1),
        close_datetime=datetime(2026, 5, 1, 10, 0, 0),
        qty=Decimal("74"),
        cost_basis_usd=Decimal("3137.60"),
        proceeds_usd=Decimal("3141.0924"),
        fifo_pnl_usd=Decimal("3.4924"),
        transaction_id=None,
    )
    p = _xml("U99999002", [], closed_lots=[lot])
    await persist(
        db_session,
        parsed=p,
        organization_id=sample_org.id,
        xml_bytes=b"<fop/>",
        source="manual_upload",
    )
    from ibkr_control.db.models.flex_raw import ClosedLot

    row = await db_session.scalar(select(ClosedLot).where(ClosedLot.symbol == "GLOB"))
    assert row.instrument_id is not None
    ident = await db_session.scalar(
        select(InstrumentIdentifier).where(InstrumentIdentifier.id_value == "GLOB-CONID")
    )
    assert ident is not None
    assert row.instrument_id == ident.instrument_id


@pytest.mark.asyncio
async def test_fop_transfer_instrument_id_null_resolver_no_creator(
    db_session: AsyncSession, sample_org
):
    """End-to-end on the real FOP fixture: the FOP transfer carries a conid
    (160756766) that appears ONLY on <Transfer> (no creator). The resolver does
    NOT create an instrument for it, so the transfer's instrument_id stays NULL."""
    from sqlalchemy import text

    xml = (FIXTURE_DIR / "ACTIVITY_2026_FOP_sanitized.xml").read_bytes()
    parsed = parse(xml)
    await persist(
        db_session,
        parsed=parsed,
        organization_id=sample_org.id,
        xml_bytes=xml,
        source="manual_upload",
    )
    # No instrument was created for the transfer-only conid.
    ident = await db_session.scalar(
        select(InstrumentIdentifier).where(InstrumentIdentifier.id_value == "160756766")
    )
    assert ident is None
    # The FOP transfer row has a NULL instrument_id.
    fop_iid = (
        await db_session.execute(
            text("SELECT instrument_id FROM transfers WHERE transaction_id = '39584831194'")
        )
    ).scalar_one()
    assert fop_iid is None
