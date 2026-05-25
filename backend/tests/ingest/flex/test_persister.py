"""Tests del persister Flex: parsed → DB con dedup + TX."""
from datetime import date
from decimal import Decimal
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.ingest.flex.persister import persist
from ibkr_control.ingest.flex._models import (
    ParsedAccount, ParsedTrade, ParsedClosedLot, ParsedOpenPositionLot,
    ParsedCashTransaction, ParsedTransfer, ParsedTransferLot, ParsedXML,
    ParsedDividendAccrual, ParsedOpenDividendAccrual,
)

FIXTURE_DIR = __import__("pathlib").Path(__file__).parent.parent.parent / "fixtures" / "xml"


def _make_parsed(account_id: str = "U99999001", n_trades: int = 2) -> ParsedXML:
    return ParsedXML(
        anyo=2025,
        period_from=date(2025, 1, 1),
        period_to=date(2025, 12, 31),
        accounts=[ParsedAccount(ibkr_account_id=account_id, currency="USD")],
        trades=[
            ParsedTrade(
                transaction_id=f"TXN-{i}",
                ibkr_account_id=account_id,
                symbol="AAPL",
                asset_class="STK",
                trade_date=date(2025, 1, 15 + i),
                settle_date=date(2025, 1, 17 + i),
                qty=Decimal("10"),
                price_usd=Decimal("150.00"),
                proceeds_usd=Decimal("-1500.00"),
                commission_usd=Decimal("1.00"),
                open_close="O",
                buy_sell="BUY",
                raw_attrs={"orderType": "LMT"},
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
async def test_persist_creates_flex_import_and_trades(
    db_session: AsyncSession, sample_user, sample_account
):
    parsed = _make_parsed(account_id=sample_account.ibkr_account_id, n_trades=3)
    flex_import_id, counters = await persist(
        db_session,
        parsed=parsed,
        user_id=sample_user.id,
        xml_bytes=b"<xml>fake</xml>",
        source="manual_upload",
    )
    assert flex_import_id is not None
    assert counters["hash_dedup"] is False
    assert counters["n_observed_trades"] == 3
    assert counters["n_new_trades"] == 3

    from ibkr_control.db.models.flex_raw import FlexImport, Trade

    fi = await db_session.scalar(select(FlexImport).where(FlexImport.id == flex_import_id))
    assert fi.user_id == sample_user.id
    assert fi.anyo == 2025
    assert fi.source == "manual_upload"
    assert fi.status == "ok"
    assert fi.n_observed_trades == 3
    assert fi.n_new_trades == 3

    n = await db_session.scalar(
        select(func.count(Trade.id)).where(Trade.flex_import_id == flex_import_id)
    )
    assert n == 3


@pytest.mark.asyncio
async def test_persist_year_status_sealed_when_period_to_is_dec31(
    db_session: AsyncSession, sample_user, sample_account
):
    parsed = _make_parsed(account_id=sample_account.ibkr_account_id, n_trades=1)
    parsed.period_to = date(2025, 12, 31)
    fi_id, _ = await persist(
        db_session,
        parsed=parsed,
        user_id=sample_user.id,
        xml_bytes=b"<xml>1</xml>",
        source="manual_upload",
    )
    from ibkr_control.db.models.flex_raw import FlexImport

    fi = await db_session.scalar(select(FlexImport).where(FlexImport.id == fi_id))
    assert fi.year_status == "sealed"


@pytest.mark.asyncio
async def test_persist_year_status_rolling_when_period_to_before_dec31(
    db_session: AsyncSession, sample_user, sample_account
):
    parsed = _make_parsed(account_id=sample_account.ibkr_account_id, n_trades=1)
    parsed.period_to = date(2025, 5, 24)
    fi_id, _ = await persist(
        db_session,
        parsed=parsed,
        user_id=sample_user.id,
        xml_bytes=b"<xml>2</xml>",
        source="manual_upload",
    )
    from ibkr_control.db.models.flex_raw import FlexImport

    fi = await db_session.scalar(select(FlexImport).where(FlexImport.id == fi_id))
    assert fi.year_status == "rolling"


@pytest.mark.asyncio
async def test_persist_duplicate_hash_returns_existing_id(
    db_session: AsyncSession, sample_user, sample_account
):
    """Si el mismo XML (mismo hash) se persiste dos veces, devuelve el id existente sin re-insertar."""
    parsed = _make_parsed(account_id=sample_account.ibkr_account_id, n_trades=2)
    xml_bytes = b"<xml>identical</xml>"

    fi_id_1, counters_1 = await persist(
        db_session, parsed=parsed, user_id=sample_user.id,
        xml_bytes=xml_bytes, source="manual_upload",
    )
    assert counters_1["hash_dedup"] is False
    fi_id_2, counters_2 = await persist(
        db_session, parsed=parsed, user_id=sample_user.id,
        xml_bytes=xml_bytes, source="manual_upload",
    )
    assert fi_id_1 == fi_id_2
    assert counters_2 == {"hash_dedup": True}

    from ibkr_control.db.models.flex_raw import Trade

    n = await db_session.scalar(
        select(func.count(Trade.id)).where(Trade.flex_import_id == fi_id_1)
    )
    assert n == 2  # NO duplicó los trades


@pytest.mark.asyncio
async def test_persist_creates_missing_accounts_on_the_fly(
    db_session: AsyncSession, sample_user
):
    """Si el XML referencia un account que no existe en DB, se crea automaticamente."""
    parsed = _make_parsed(account_id="U99999777", n_trades=1)
    fi_id, _ = await persist(
        db_session,
        parsed=parsed,
        user_id=sample_user.id,
        xml_bytes=b"<xml>new-acc</xml>",
        source="manual_upload",
    )
    assert fi_id is not None

    from ibkr_control.db.models.accounts import Account

    acc = await db_session.scalar(
        select(Account).where(Account.ibkr_account_id == "U99999777")
    )
    assert acc is not None


@pytest.mark.asyncio
async def test_persist_intra_batch_dup_is_absorbed_by_upsert(
    db_session: AsyncSession, sample_user, sample_account
):
    """Spec phase 2.5: UPSERTs ON CONFLICT DO NOTHING absorben duplicados
    intra-batch sin lanzar IntegrityError.

    Antes del rewrite, dos trades con el mismo transaction_id en el mismo
    parsed levantaban IntegrityError (UNIQUE constraint en INSERT plain).
    Ahora el segundo se trata como NO-OP igual que un re-ingest cross-XML,
    y solo 1 trade queda persistido. Esto es por diseño: la mismidad por
    transaction_id es la natural key del spec A1 y manda en todo nivel
    (intra-batch, inter-batch, inter-import).
    """
    parsed = _make_parsed(account_id=sample_account.ibkr_account_id, n_trades=2)
    parsed.trades[1].transaction_id = parsed.trades[0].transaction_id  # forzar dup

    from ibkr_control.db.models.flex_raw import FlexImport, Trade

    fi_id, counters = await persist(
        db_session,
        parsed=parsed,
        user_id=sample_user.id,
        xml_bytes=b"<xml>intra-dup</xml>",
        source="manual_upload",
    )
    assert fi_id is not None
    # n_observed cuenta lo que llegó (2); n_new cuenta lo que efectivamente
    # se insertó (1 — el segundo fue absorbido por ON CONFLICT DO NOTHING).
    assert counters["n_observed_trades"] == 2
    assert counters["n_new_trades"] == 1

    n_trades = await db_session.scalar(
        select(func.count(Trade.id)).where(Trade.flex_import_id == fi_id)
    )
    assert n_trades == 1

    # FlexImport persiste — no se rolled back porque no hubo error.
    fi = await db_session.scalar(select(FlexImport).where(FlexImport.id == fi_id))
    assert fi is not None
    assert fi.n_observed_trades == 2
    assert fi.n_new_trades == 1


@pytest.mark.asyncio
async def test_persist_links_closed_lots_to_source_trades(
    db_session: AsyncSession, sample_user
):
    """ClosedLot.source_trade_id is populated from matching Trade.transaction_id."""
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    from ibkr_control.ingest.flex.parser import parse

    parsed = parse(xml)
    fi_id, _ = await persist(
        db_session,
        parsed=parsed,
        user_id=sample_user.id,
        xml_bytes=xml,
        source="manual_upload",
    )

    from ibkr_control.db.models.flex_raw import ClosedLot

    n_linked = await db_session.scalar(
        select(func.count(ClosedLot.id)).where(
            ClosedLot.flex_import_id == fi_id,
            ClosedLot.source_trade_id.is_not(None),
        )
    )
    n_total = await db_session.scalar(
        select(func.count(ClosedLot.id)).where(ClosedLot.flex_import_id == fi_id)
    )
    assert n_total > 0, "Expected at least some closed lots in the fixture"
    # Many closed lots reference trades from prior years and won't match trades in this
    # XML — that's expected. We verify that lots whose transaction_id IS present in this
    # XML's trades DID get linked (i.e., the linking mechanism works at all).
    # The 2025 fixture has 194 trades and 146 closed lots; ~69 lots have their
    # transaction_id in this XML's trade set (the rest reference prior-year trades).
    assert n_linked > 0, (
        f"Expected at least some closed lots to have source_trade_id populated, "
        f"got 0 out of {n_total}"
    )


@pytest.mark.asyncio
async def test_persist_dividend_accruals_from_2025_fixture(
    db_session: AsyncSession, sample_user
):
    """After persisting the 2025 fixture:
    - change_in_dividend_accruals: 51 rows (DETAIL-level rows; 46 SUMMARY rows skipped)
    - open_dividend_accruals: 1 row (NKE Q4 2025, account U99999001)

    Note: The sanitized fixture preserves DETAIL rows with anonymized account IDs,
    so 51 real rows are persisted (not 0 as the raw fixture would yield).
    """
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    from ibkr_control.ingest.flex.parser import parse

    parsed = parse(xml)
    assert len(parsed.change_in_dividend_accruals) == 51, \
        f"Expected 51 DETAIL ChangeInDividendAccrual rows, got {len(parsed.change_in_dividend_accruals)}"
    assert len(parsed.open_dividend_accruals) == 1, \
        "Expected exactly 1 OpenDividendAccrual row in 2025 fixture"

    fi_id, _ = await persist(
        db_session,
        parsed=parsed,
        user_id=sample_user.id,
        xml_bytes=xml,
        source="manual_upload",
    )

    from ibkr_control.db.models.flex_raw import ChangeInDividendAccrual, OpenDividendAccrual

    n_chg = await db_session.scalar(
        select(func.count(ChangeInDividendAccrual.id)).where(
            ChangeInDividendAccrual.flex_import_id == fi_id
        )
    )
    assert n_chg == 51, f"Expected 51 change_in_dividend_accruals, got {n_chg}"

    n_open = await db_session.scalar(
        select(func.count(OpenDividendAccrual.id)).where(
            OpenDividendAccrual.flex_import_id == fi_id
        )
    )
    assert n_open == 1, f"Expected 1 open_dividend_accruals, got {n_open}"

    # Verify the NKE row's data
    from sqlalchemy.orm import selectinload
    row = await db_session.scalar(
        select(OpenDividendAccrual).where(OpenDividendAccrual.flex_import_id == fi_id)
    )
    assert row is not None
    assert row.symbol == "NKE"
    assert row.report_date == date(2025, 12, 31)
    assert row.quantity == Decimal("31.013")
    assert row.gross_amount_usd == Decimal("12.72")
    assert row.tax_usd == Decimal("3.82")
    assert row.net_amount_usd == Decimal("8.90")
    assert row.account_id is not None  # resolves to the joint account
