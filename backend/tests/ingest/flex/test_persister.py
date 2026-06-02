"""Tests del persister Flex: parsed → DB con dedup + TX."""

from datetime import date
from decimal import Decimal
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.ingest.flex.persister import persist
from ibkr_control.ingest.flex._models import (
    ParsedAccount,
    ParsedTrade,
    ParsedTransfer,
    ParsedXML,
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
        db_session,
        parsed=parsed,
        user_id=sample_user.id,
        xml_bytes=xml_bytes,
        source="manual_upload",
    )
    assert counters_1["hash_dedup"] is False
    fi_id_2, counters_2 = await persist(
        db_session,
        parsed=parsed,
        user_id=sample_user.id,
        xml_bytes=xml_bytes,
        source="manual_upload",
    )
    assert fi_id_1 == fi_id_2
    assert counters_2["hash_dedup"] is True
    assert counters_2["hash_status"] == "ok"

    from ibkr_control.db.models.flex_raw import Trade

    n = await db_session.scalar(select(func.count(Trade.id)).where(Trade.flex_import_id == fi_id_1))
    assert n == 2  # NO duplicó los trades


@pytest.mark.asyncio
async def test_persist_creates_missing_accounts_on_the_fly(db_session: AsyncSession, sample_user):
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

    acc = await db_session.scalar(select(Account).where(Account.ibkr_account_id == "U99999777"))
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
async def test_persist_links_closed_lots_to_source_trades(db_session: AsyncSession, sample_user):
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
async def test_persist_dividend_accruals_from_2025_fixture(db_session: AsyncSession, sample_user):
    """After persisting the 2025 fixture:
    - change_in_dividend_accruals: 51 rows (DETAIL-level rows; 46 SUMMARY rows skipped)
    - open_dividend_accruals: 1 row (NKE Q4 2025, account U99999001)

    Note: The sanitized fixture preserves DETAIL rows with anonymized account IDs,
    so 51 real rows are persisted (not 0 as the raw fixture would yield).
    """
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    from ibkr_control.ingest.flex.parser import parse

    parsed = parse(xml)
    assert len(parsed.change_in_dividend_accruals) == 51, (
        f"Expected 51 DETAIL ChangeInDividendAccrual rows, got {len(parsed.change_in_dividend_accruals)}"
    )
    assert len(parsed.open_dividend_accruals) == 1, (
        "Expected exactly 1 OpenDividendAccrual row in 2025 fixture"
    )

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


@pytest.mark.asyncio
async def test_persist_deletes_previous_rolling_for_same_user_anyo_source(
    db_session,
    sample_user,
):
    """R1 latest-1 retention: previous rolling row for same key gets deleted."""
    from pathlib import Path
    from datetime import date
    from lxml import etree
    from sqlalchemy import select
    from ibkr_control.db.models.flex_raw import FlexImport
    from ibkr_control.ingest.flex.parser import parse
    from ibkr_control.ingest.flex.persister import persist

    fixture = (
        Path(__file__).parent.parent.parent / "fixtures" / "xml" / "ACTIVITY_2025_sanitized.xml"
    )
    base = fixture.read_bytes()
    tree = etree.fromstring(base)
    xml_v1 = etree.tostring(tree, pretty_print=False)
    xml_v2 = etree.tostring(tree, pretty_print=True)  # different whitespace → different SHA-256

    # First persist — force year_status='rolling' via period_to monkeypatch
    parsed_v1 = parse(xml_v1)
    parsed_v1.period_to = date(2025, 5, 24)  # not Dec 31 → 'rolling'
    id_v1, _ = await persist(
        db_session,
        parsed=parsed_v1,
        user_id=sample_user.id,
        xml_bytes=xml_v1,
        source="web_service",
    )
    await db_session.commit()

    # Second persist (different hash, same key) — should DELETE v1
    parsed_v2 = parse(xml_v2)
    parsed_v2.period_to = date(2025, 5, 24)
    id_v2, _ = await persist(
        db_session,
        parsed=parsed_v2,
        user_id=sample_user.id,
        xml_bytes=xml_v2,
        source="web_service",
    )
    await db_session.commit()

    rows = (
        await db_session.scalars(
            select(FlexImport).where(
                FlexImport.user_id == sample_user.id,
                FlexImport.anyo == 2025,
                FlexImport.source == "web_service",
                FlexImport.year_status == "rolling",
            )
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].id == id_v2


@pytest.mark.asyncio
async def test_persist_does_not_delete_sealed_years(db_session, sample_user):
    """R1: sealed years are pinned regardless of latest-1 cleanup."""
    from pathlib import Path
    from datetime import date
    from lxml import etree
    from sqlalchemy import select
    from ibkr_control.db.models.flex_raw import FlexImport
    from ibkr_control.ingest.flex.parser import parse
    from ibkr_control.ingest.flex.persister import persist

    fixture = (
        Path(__file__).parent.parent.parent / "fixtures" / "xml" / "ACTIVITY_2025_sanitized.xml"
    )
    base = fixture.read_bytes()
    tree = etree.fromstring(base)
    xml_sealed = etree.tostring(tree, pretty_print=False)
    xml_rolling = etree.tostring(tree, pretty_print=True)

    # First: persist as 'sealed' (period_to = Dec 31)
    parsed_sealed = parse(xml_sealed)
    parsed_sealed.period_to = date(2025, 12, 31)
    id_sealed, _ = await persist(
        db_session,
        parsed=parsed_sealed,
        user_id=sample_user.id,
        xml_bytes=xml_sealed,
        source="web_service",
    )
    await db_session.commit()

    # Second: persist as 'rolling' (different bytes → different hash, same anyo)
    parsed_rolling = parse(xml_rolling)
    parsed_rolling.period_to = date(2025, 5, 24)
    id_rolling, _ = await persist(
        db_session,
        parsed=parsed_rolling,
        user_id=sample_user.id,
        xml_bytes=xml_rolling,
        source="web_service",
    )
    await db_session.commit()

    # Sealed row must still exist
    sealed_row = await db_session.scalar(select(FlexImport).where(FlexImport.id == id_sealed))
    assert sealed_row is not None
    assert sealed_row.year_status == "sealed"
    # Rolling row also exists
    rolling_row = await db_session.scalar(select(FlexImport).where(FlexImport.id == id_rolling))
    assert rolling_row is not None
    assert rolling_row.year_status == "rolling"


@pytest.mark.asyncio
async def test_persist_does_not_delete_poison_rows(db_session, sample_user):
    """R1: poison rows are forensic evidence — never auto-deleted."""
    from pathlib import Path
    from datetime import date
    from sqlalchemy import select
    from ibkr_control.db.models.flex_raw import FlexImport
    from ibkr_control.ingest.flex.parser import parse
    from ibkr_control.ingest.flex.persister import persist

    # Seed a poison row first
    db_session.add(
        FlexImport(
            user_id=sample_user.id,
            xml_hash="poison-row-hash",
            xml_bytes=b"x",
            xml_size_bytes=1,
            anyo=2025,
            source="web_service",
            year_status="rolling",
            status="poison",
            poison_reason="test",
            period_covered_from=date(2025, 1, 1),
            period_covered_to=date(2025, 12, 31),
        )
    )
    await db_session.commit()

    # Now persist a fresh rolling row
    fixture = (
        Path(__file__).parent.parent.parent / "fixtures" / "xml" / "ACTIVITY_2025_sanitized.xml"
    )
    base = fixture.read_bytes()
    parsed = parse(base)
    parsed.period_to = date(2025, 5, 24)
    await persist(
        db_session,
        parsed=parsed,
        user_id=sample_user.id,
        xml_bytes=base,
        source="web_service",
    )
    await db_session.commit()

    poison_row = await db_session.scalar(
        select(FlexImport).where(FlexImport.xml_hash == "poison-row-hash")
    )
    assert poison_row is not None
    assert poison_row.status == "poison"


def _minimal_parsed(
    n_trades: int = 1,
    account_id: str = "U99999001",
    anyo: int = 2026,
) -> ParsedXML:
    """Minimal ParsedXML with `account_id` in parsed.accounts (AccountInformation).

    OWN account always ends up in accounts_map because it comes from
    parsed.accounts (the authoritative source, per spec #6).
    """
    return ParsedXML(
        anyo=anyo,
        period_from=date(anyo, 1, 1),
        period_to=date(anyo, 5, 25) if anyo == 2026 else date(anyo, 12, 31),
        accounts=[ParsedAccount(ibkr_account_id=account_id, currency="USD")],
        trades=[
            ParsedTrade(
                transaction_id=f"TX-CP-{i}",
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
async def test_external_transfer_peer_becomes_counterparty_not_account(
    db_session: AsyncSession, sample_user
):
    """Un <Transfer> FOP IN desde un broker externo (CS-...) crea fila en
    counterparties, NO en accounts; el transfer queda con src_counterparty_id
    set y src_account_id NULL (exclusive arc)."""
    from ibkr_control.db.models.accounts import Account
    from ibkr_control.db.models.counterparties import Counterparty
    from sqlalchemy import func, select, text

    OWN = "U99999002"
    EXT = "CS-999999-99"
    transfer = ParsedTransfer(
        transaction_id="XFER-FOP-1",
        transfer_date=date(2026, 4, 30),
        direction="IN",
        src_ibkr_account_id=EXT,  # peer externo
        dst_ibkr_account_id=OWN,  # cuenta propia
        symbol="GLOB",
        qty=Decimal("94"),
        transfer_type="FOP",
    )
    p = _minimal_parsed(n_trades=0, account_id=OWN)  # OWN in <AccountInformation>
    p.transfers = [transfer]
    await persist(
        db_session,
        parsed=p,
        user_id=sample_user.id,
        xml_bytes=b"<fop/>",
        source="web_service",
    )
    await db_session.commit()

    # EXT NOT in accounts
    n_ext_acct = await db_session.scalar(
        select(func.count()).select_from(Account).where(Account.ibkr_account_id == EXT)
    )
    assert n_ext_acct == 0
    # EXT IS in counterparties
    cp = await db_session.scalar(select(Counterparty).where(Counterparty.external_id == EXT))
    assert cp is not None
    # transfer points src->counterparty, dst->own account
    row = (
        await db_session.execute(
            text(
                "SELECT src_account_id, src_counterparty_id, dst_account_id, dst_counterparty_id "
                "FROM transfers WHERE transaction_id='XFER-FOP-1'"
            )
        )
    ).first()
    assert row[0] is None and row[1] == cp.id  # src = counterparty
    assert row[2] is not None and row[3] is None  # dst = own account


@pytest.mark.asyncio
async def test_fop_fixture_creates_counterparty_no_orphan_account(
    db_session: AsyncSession, sample_user
):
    """End-to-end: parse(xml) + persist del fixture FOP sanitizado.

    Asserts:
    - CS-999999-99 (external FOP peer) -> counterparties row, NOT in accounts
    - U99999001 + U99999002 (own accounts, both in <AccountInformation>) -> accounts rows
    - INTERNAL transfer peer (U99999002) resolves to account FK, not counterparty
      because U99999002 appears in <AccountInformation> in its own FlexStatement

    U99999002 nuance (Option A chosen): the fixture includes a second <FlexStatement>
    with <AccountInformation accountId="U99999002">. This reflects the real multi-account
    consolidated Flex Query where every own account has its own FlexStatement.
    Without this, U99999002 would NOT appear in accounts_map (since transfer peers are
    deliberately excluded from all_account_ids in the persister) and would be routed to
    counterparties — incorrect behavior for an own account. Option A makes the fixture
    realistic and keeps the assertion `own == 2` meaningful.
    """
    from ibkr_control.ingest.flex.parser import parse
    from ibkr_control.db.models.accounts import Account
    from ibkr_control.db.models.counterparties import Counterparty
    from sqlalchemy import func, select, text

    xml = (FIXTURE_DIR / "ACTIVITY_2026_FOP_sanitized.xml").read_bytes()
    parsed = parse(xml)

    # Sanity-check: parser extracted both accounts and both transfers
    assert len(parsed.accounts) == 2
    account_ids = {a.ibkr_account_id for a in parsed.accounts}
    assert account_ids == {"U99999001", "U99999002"}
    assert len(parsed.transfers) == 2
    fop = next(t for t in parsed.transfers if t.transfer_type == "FOP")
    assert fop.src_ibkr_account_id == "CS-999999-99"
    assert fop.dst_ibkr_account_id == "U99999001"

    await persist(
        db_session,
        parsed=parsed,
        user_id=sample_user.id,
        xml_bytes=xml,
        source="manual_upload",
    )
    await db_session.commit()

    # CS-999999-99 must be in counterparties
    cp = await db_session.scalar(
        select(Counterparty).where(Counterparty.external_id == "CS-999999-99")
    )
    assert cp is not None

    # CS-999999-99 must NOT appear in accounts (no orphan account)
    n_cp_in_accounts = await db_session.scalar(
        select(func.count()).select_from(Account).where(Account.ibkr_account_id == "CS-999999-99")
    )
    assert n_cp_in_accounts == 0

    # Both own accounts must be in accounts
    own = await db_session.scalar(
        select(func.count())
        .select_from(Account)
        .where(Account.ibkr_account_id.in_(["U99999001", "U99999002"]))
    )
    assert own == 2

    # INTERNAL transfer (own -> own) must resolve to account FKs on both sides,
    # NOT counterparties (exclusive arc the other way).
    internal_row = (
        await db_session.execute(
            text(
                "SELECT src_account_id, src_counterparty_id, dst_account_id, dst_counterparty_id "
                "FROM transfers WHERE transaction_id='39601540411'"
            )
        )
    ).first()
    assert internal_row is not None
    assert internal_row[0] is not None and internal_row[1] is None  # src = own account
    assert internal_row[2] is not None and internal_row[3] is None  # dst = own account
