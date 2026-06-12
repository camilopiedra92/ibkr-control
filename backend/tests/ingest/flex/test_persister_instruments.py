"""W2 — securities master: _ensure_instruments + instrument_id threading.

Cubre la cadena conid -> identifier -> instrument -> FK en los hechos, la
idempotencia (re-persist no crea instruments nuevos), el ticker change (mismo
conid, symbol last-seen), la semántica resolver-only de cash (conid ausente o
sin instrument => instrument_id NULL, NUNCA crea) y el rol creator de los
transfers de securities (TL-D1, spec 2026-06-11: assetCategory != 'CASH' trae
spec completo de instrumento y crea el instrument; CASH => NULL por diseño).
"""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.db.models.flex_raw import CashTransaction, Trade
from ibkr_control.db.models.instruments import Instrument, InstrumentIdentifier
from ibkr_control.ingest.flex._models import (
    ParsedAccount,
    ParsedCashTransaction,
    ParsedClosedLot,
    ParsedTrade,
    ParsedTransfer,
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
    """A cash transaction whose conid has NO instrument in the batch NOR in the
    securities master (TL-D2: lookup is DB-wide) -> instrument_id NULL (resolver
    never creates instruments)."""
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
async def test_fop_transfer_creates_instrument_and_resolves(db_session: AsyncSession, sample_org):
    """TL-D1 end-to-end contra el fixture FOP real: el transfer de security ES
    creator — crea el instrument GLOB (conid 160756766 + isin) y el transfer
    queda con instrument_id non-NULL. Flip del test pre-spec-2026-06-11 que
    lockeaba el NULL (supersede T1-D8/CR-1)."""
    xml = (FIXTURE_DIR / "ACTIVITY_2026_FOP_sanitized.xml").read_bytes()
    parsed = parse(xml)
    await persist(
        db_session,
        parsed=parsed,
        organization_id=sample_org.id,
        xml_bytes=xml,
        source="manual_upload",
    )
    ident = await db_session.scalar(
        select(InstrumentIdentifier).where(InstrumentIdentifier.id_value == "160756766")
    )
    assert ident is not None
    isin_ident = await db_session.scalar(
        select(InstrumentIdentifier).where(InstrumentIdentifier.id_value == "LU0974299876")
    )
    assert isin_ident is not None
    assert isin_ident.instrument_id == ident.instrument_id
    # El transfer es el ÚNICO creator de GLOB en este fixture: su spec
    # (_merge con name=tr.description) debe poblar la fila Instrument completa.
    inst = await db_session.scalar(select(Instrument).where(Instrument.id == ident.instrument_id))
    assert inst.symbol == "GLOB"
    assert inst.asset_class == "STK"
    assert inst.name == "GLOBANT SA"
    rows = (
        await db_session.execute(
            text("SELECT instrument_id, asset_class, conid FROM transfers ORDER BY transaction_id")
        )
    ).all()
    assert len(rows) == 2  # FOP IN + INTERNAL OUT, ambos GLOB
    for instrument_id, asset_class, conid in rows:
        assert instrument_id == ident.instrument_id
        assert asset_class == "STK"
        assert conid == "160756766"


@pytest.mark.asyncio
async def test_cash_transfer_no_instrument_no_creation(db_session: AsyncSession, sample_org):
    """TL-D1: CASH interno -> instrument_id NULL, conid NULL, cero instruments."""
    transfer = ParsedTransfer(
        transaction_id="XFER-CASH-1",
        transfer_date=date(2026, 5, 1),
        direction="OUT",
        src_ibkr_account_id="U99999001",
        dst_ibkr_account_id="U99999002",
        symbol="--",
        qty=Decimal("0"),
        transfer_type="INTERNAL",
        asset_class="CASH",
        conid=None,
    )
    p = _xml("U99999001", [])
    p.accounts.append(ParsedAccount(ibkr_account_id="U99999002", currency="USD"))
    p.transfers = [transfer]
    await persist(
        db_session,
        parsed=p,
        organization_id=sample_org.id,
        xml_bytes=b"<cash-xfer/>",
        source="manual_upload",
    )
    n_instruments = await db_session.scalar(select(func.count()).select_from(Instrument))
    assert n_instruments == 0
    row = (
        await db_session.execute(
            text("SELECT instrument_id, conid FROM transfers WHERE transaction_id = 'XFER-CASH-1'")
        )
    ).one()
    assert row.instrument_id is None
    assert row.conid is None


@pytest.mark.asyncio
async def test_resolver_resolves_against_master_cross_batch(db_session: AsyncSession, sample_org):
    """TL-D2: el lookup del resolver es contra el MASTER (DB), no solo el batch.
    Batch 1 crea el instrument (trade creator); batch 2 trae SOLO un cash con el
    mismo conid -> resuelve aunque no haya creator en ese batch."""
    trade = _trade("265598", "AAPL", txn="TX-AAPL-1")
    await persist(
        db_session,
        parsed=_xml("U99999001", [trade]),
        organization_id=sample_org.id,
        xml_bytes=b"<batch1/>",
        source="manual_upload",
    )
    cash = ParsedCashTransaction(
        transaction_id="CASH-CROSS-BATCH",
        ibkr_account_id="U99999001",
        type="Dividends",
        currency="USD",
        amount_usd=Decimal("5.00"),
        description="AAPL dividend, posicion ya cerrada",
        date=date(2026, 2, 1),
        symbol="AAPL",
        conid="265598",
    )
    await persist(
        db_session,
        parsed=_xml("U99999001", [], cash_transactions=[cash]),
        organization_id=sample_org.id,
        xml_bytes=b"<batch2/>",
        source="manual_upload",
    )
    ct = await db_session.scalar(
        select(CashTransaction).where(CashTransaction.transaction_id == "CASH-CROSS-BATCH")
    )
    assert ct.instrument_id is not None


@pytest.mark.asyncio
async def test_cash_instrument_id_converges_monotonically(db_session: AsyncSession, sample_org):
    """TL-D3: una fila cash congelada con NULL converge cuando el re-ingest trae
    la resolucion; la convergencia NO cuenta como fila nueva (n_new honesto) y
    NO genera restatements (enriquecimiento != restatement economico)."""
    cash = ParsedCashTransaction(
        transaction_id="CASH-CONV",
        ibkr_account_id="U99999001",
        type="Dividends",
        currency="USD",
        amount_usd=Decimal("7.00"),
        description="dividendo de instrument aun no visto",
        date=date(2026, 3, 1),
        symbol="AAPL",
        conid="265598",
    )
    # Batch 1: conid irresoluble (sin creator en batch ni master) -> NULL.
    await persist(
        db_session,
        parsed=_xml("U99999001", [], cash_transactions=[cash]),
        organization_id=sample_org.id,
        xml_bytes=b"<conv1/>",
        source="manual_upload",
    )
    ct = await db_session.scalar(
        select(CashTransaction).where(CashTransaction.transaction_id == "CASH-CONV")
    )
    assert ct.instrument_id is None
    # Batch 2 (re-ingest YTD): creator + LA MISMA fila cash -> converge.
    trade = _trade("265598", "AAPL", txn="TX-AAPL-2")
    _, counters = await persist(
        db_session,
        parsed=_xml("U99999001", [trade], cash_transactions=[cash]),
        organization_id=sample_org.id,
        xml_bytes=b"<conv2/>",
        source="manual_upload",
    )
    assert counters["n_new_cash_tx"] == 0  # convergencia != fila nueva
    await db_session.refresh(ct)
    assert ct.instrument_id is not None
    assert ct.amount_usd == Decimal("7.00")  # columnas de hecho intactas
    n_restatements = (
        await db_session.execute(text("SELECT count(*) FROM restatement_log"))
    ).scalar_one()
    assert n_restatements == 0
    n_rows = await db_session.scalar(
        select(func.count())
        .select_from(CashTransaction)
        .where(CashTransaction.transaction_id == "CASH-CONV")
    )
    assert n_rows == 1


@pytest.mark.asyncio
async def test_resolved_cash_row_untouched_on_reingest(db_session: AsyncSession, sample_org):
    """TL-D3 guard monotono: fila ya resuelta re-ingerida -> cero cambios, cero new."""
    trade = _trade("265598", "AAPL", txn="TX-AAPL-3")
    cash = ParsedCashTransaction(
        transaction_id="CASH-STABLE",
        ibkr_account_id="U99999001",
        type="Dividends",
        currency="USD",
        amount_usd=Decimal("3.00"),
        description="ya resuelto",
        date=date(2026, 4, 1),
        symbol="AAPL",
        conid="265598",
    )
    await persist(
        db_session,
        parsed=_xml("U99999001", [trade], cash_transactions=[cash]),
        organization_id=sample_org.id,
        xml_bytes=b"<stable1/>",
        source="manual_upload",
    )
    ct = await db_session.scalar(
        select(CashTransaction).where(CashTransaction.transaction_id == "CASH-STABLE")
    )
    original_iid = ct.instrument_id
    assert original_iid is not None
    _, counters = await persist(
        db_session,
        parsed=_xml("U99999001", [], cash_transactions=[cash]),
        organization_id=sample_org.id,
        xml_bytes=b"<stable2/>",
        source="manual_upload",
    )
    assert counters["n_new_cash_tx"] == 0
    await db_session.refresh(ct)
    assert ct.instrument_id == original_iid


@pytest.mark.asyncio
async def test_accrual_only_creator_without_asset_category_fails_loud(
    db_session: AsyncSession, sample_org
):
    """Spec review W2: si el ÚNICO creator de un conid es un accrual SIN
    assetCategory, _ensure_instruments falla loud con un ValueError accionable
    en vez de un IntegrityError opaco del NOT NULL de instruments.asset_class.
    (CR-1 verificó assetCategory 100% presente en los accruals reales — esto
    atrapa drift futuro.)"""
    from ibkr_control.ingest.flex._models import ParsedDividendAccrual

    accrual = ParsedDividendAccrual(
        ibkr_account_id="U99999001",
        symbol="ZZZZ",
        conid="424242",
        isin=None,
        issuer_country=None,
        currency="USD",
        ex_date=date(2026, 2, 10),
        pay_date=date(2026, 3, 1),
        report_date=date(2026, 2, 15),
        accrual_date=date(2026, 2, 14),
        quantity=Decimal("10"),
        gross_rate_per_share=None,
        gross_amount_usd=Decimal("5.00"),
        tax_usd=Decimal("0.75"),
        fee_usd=None,
        net_amount_usd=Decimal("4.25"),
        action_id=None,
        asset_category=None,  # the drift case the guard catches
        sub_category=None,
        level_of_detail=None,
        code="Po",
        raw_attrs={},
    )
    p = _xml("U99999001", [])
    p.change_in_dividend_accruals = [accrual]
    with pytest.raises(ValueError, match="424242.*assetCategory"):
        await persist(
            db_session,
            parsed=p,
            organization_id=sample_org.id,
            xml_bytes=b"<accrual-no-ac/>",
            source="manual_upload",
        )
