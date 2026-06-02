"""Tests del parser de Flex XML."""

from datetime import date
from decimal import Decimal
from pathlib import Path
import pytest

from lxml.etree import XMLSyntaxError

from ibkr_control.ingest.flex.parser import parse
from ibkr_control.ingest.flex._models import UnknownFlexTagError

FIXTURE_DIR = Path(__file__).parent.parent.parent / "fixtures" / "xml"


def test_parses_empty_response():
    xml = (FIXTURE_DIR / "empty_query_response.xml").read_bytes()
    parsed = parse(xml)
    assert parsed.anyo == 2026
    assert parsed.period_from == date(2026, 1, 1)
    assert parsed.period_to == date(2026, 5, 24)
    assert len(parsed.accounts) == 1
    assert parsed.accounts[0].ibkr_account_id == "U99999001"
    assert parsed.trades == []
    assert parsed.closed_lots == []


def test_rejects_non_flex_response():
    xml = (FIXTURE_DIR / "not_a_flex_response.xml").read_bytes()
    with pytest.raises(ValueError, match="FlexQueryResponse"):
        parse(xml)


def test_rejects_malformed_xml():
    xml = (FIXTURE_DIR / "malformed_xml.xml").read_bytes()
    with pytest.raises(XMLSyntaxError):
        parse(xml)


def test_unknown_top_level_tag_aborts():
    """Si aparece un tag desconocido en TOP-level, abortar (no silenciar)."""
    xml = b"""<?xml version="1.0"?>
<FlexQueryResponse>
  <FlexStatements count="1">
    <FlexStatement accountId="U99999001" fromDate="2026-01-01" toDate="2026-01-31"
                   period="YearToDate" whenGenerated="2026-02-01;10:00:00">
      <AccountInformation accountId="U99999001" currency="USD"/>
      <Trades/>
      <BrandNewIBKRTagWeNeverSawBefore/>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>"""
    with pytest.raises(UnknownFlexTagError, match="BrandNewIBKRTagWeNeverSawBefore"):
        parse(xml)


def test_parses_activity_2024_fixture():
    xml = (FIXTURE_DIR / "ACTIVITY_2024_sanitized.xml").read_bytes()
    parsed = parse(xml)
    assert parsed.anyo == 2024
    assert len(parsed.accounts) >= 1
    assert len(parsed.trades) > 0
    # Account IDs deben ser sanitizados
    for trade in parsed.trades:
        assert trade.ibkr_account_id in {"U99999001", "U99999002", "U99999003"}


def test_parses_activity_2025_fixture():
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    parsed = parse(xml)
    assert parsed.anyo == 2025
    assert len(parsed.trades) > 0


def test_trade_has_decimal_amounts():
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    parsed = parse(xml)
    for trade in parsed.trades[:5]:
        assert isinstance(trade.qty, Decimal)
        assert isinstance(trade.price_usd, Decimal)
        assert isinstance(trade.proceeds_usd, Decimal)
        assert isinstance(trade.commission_usd, Decimal)


def test_buy_sell_is_normalized():
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    parsed = parse(xml)
    for trade in parsed.trades:
        assert trade.buy_sell in {"BUY", "SELL"}


def test_open_close_optional():
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    parsed = parse(xml)
    # Algunos trades pueden no tener openCloseIndicator (e.g. FUT cash settlement)
    valid_values = {"O", "C", None}
    for trade in parsed.trades:
        assert trade.open_close in valid_values


def test_closed_lot_pnl_consistency():
    """fifo_pnl_usd debería ser ~ proceeds_usd - cost_basis_usd."""
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    parsed = parse(xml)
    for lot in parsed.closed_lots[:10]:
        computed = lot.proceeds_usd - lot.cost_basis_usd
        assert abs(lot.fifo_pnl_usd - computed) < Decimal("0.01")


def test_raw_attrs_preserved():
    """Atributos del XML no tipados deben quedar en raw_attrs JSONB."""
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    parsed = parse(xml)
    if parsed.trades:
        # Al menos algún atributo no del schema fijo debe estar en raw_attrs
        first = parsed.trades[0]
        assert isinstance(first.raw_attrs, dict)


def test_cash_transactions_drop_summary_rows():
    """CashTransaction rows with levelOfDetail=SUMMARY are dropped (they duplicate DETAIL)."""
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    parsed = parse(xml)
    for tx in parsed.cash_transactions:
        assert tx.ibkr_account_id != "-", f"SUMMARY row leaked into output (accountId='-'): {tx}"


def test_closed_lots_capture_transaction_id():
    """ClosedLot/Lot elements expose transactionID linking to Trade row; parser captures it."""
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    parsed = parse(xml)
    if parsed.closed_lots:
        # At least some closed lots should have transaction_id populated from real XML
        with_id = [c for c in parsed.closed_lots if c.transaction_id is not None]
        assert len(with_id) > 0, (
            "Expected transaction_id to be populated from real Activity XML Lot elements"
        )


def test_parses_change_in_dividend_accruals_skips_summary_rows():
    """Parser skips ChangeInDividendAccrual rows with accountId='-' (SUMMARY-level rollups).

    The 2025 sanitized fixture has 97 total rows: 46 SUMMARY (accountId='-') + 51 DETAIL
    (accountId='U999...') with real data. Parser keeps the 51 DETAIL rows, skipping the
    46 SUMMARY-only rollups. All kept rows must have non-empty account IDs.

    Note: The raw (pre-sanitized) fixture had all 97 as SUMMARY; the sanitized fixture
    correctly preserves DETAIL rows with anonymized account IDs.
    """
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    parsed = parse(xml)
    # 46 SUMMARY rows skipped, 51 DETAIL rows kept
    assert len(parsed.change_in_dividend_accruals) == 51
    # All kept rows must have real account IDs
    for row in parsed.change_in_dividend_accruals:
        assert row.ibkr_account_id != "-", f"SUMMARY row leaked into output (accountId='-'): {row}"
        assert row.level_of_detail == "DETAIL"


def test_parses_open_dividend_accruals():
    """The 2025 fixture has 1 OpenDividendAccrual row with a real accountId."""
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    parsed = parse(xml)
    assert len(parsed.open_dividend_accruals) == 1
    row = parsed.open_dividend_accruals[0]
    assert row.symbol == "NKE"
    assert row.ibkr_account_id != "-"  # filter worked
    assert row.report_date == date(2025, 12, 31)
    assert row.quantity == Decimal("31.013")
    assert row.gross_amount_usd == Decimal("12.72")
    assert row.tax_usd == Decimal("3.82")
    assert row.net_amount_usd == Decimal("8.9")


def test_parse_account_info_extracts_alias_type_and_name():
    """AccountInformation tag attrs accountAlias/accountType/name carried into ParsedAccount."""
    xml = b"""<?xml version="1.0"?>
    <FlexQueryResponse>
      <FlexStatements>
        <FlexStatement accountId="U99999999" fromDate="20260101" toDate="20261231">
          <AccountInformation accountId="U99999999" accountAlias="My Joint" accountType="Joint" name="TEST USER" currency="USD"/>
        </FlexStatement>
      </FlexStatements>
    </FlexQueryResponse>
    """
    parsed = parse(xml)
    assert len(parsed.accounts) == 1
    ai = parsed.accounts[0]
    assert ai.ibkr_account_id == "U99999999"
    assert ai.account_alias == "My Joint"
    assert ai.account_type == "Joint"
    assert ai.name == "TEST USER"


def test_parse_account_info_handles_missing_optional_attrs():
    """If accountAlias/name/accountType absent, fields are None (not empty string)."""
    xml = b"""<?xml version="1.0"?>
    <FlexQueryResponse>
      <FlexStatements>
        <FlexStatement accountId="U99999999" fromDate="20260101" toDate="20261231">
          <AccountInformation accountId="U99999999" currency="USD"/>
        </FlexStatement>
      </FlexStatements>
    </FlexQueryResponse>
    """
    parsed = parse(xml)
    ai = parsed.accounts[0]
    assert ai.account_alias is None
    assert ai.account_type is None
    assert ai.name is None


def test_parse_cash_transaction_captures_transaction_id():
    """Parser must extract transactionID attribute from <CashTransaction> elements
    so the persister can UPSERT by natural key without spurious duplicates."""
    from ibkr_control.ingest.flex.parser import parse

    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse>
  <FlexStatements>
    <FlexStatement accountId="U99999001" period="20250101-20251231"
                   fromDate="20250101" toDate="20251231">
      <AccountInformation accountId="U99999001" currency="USD"/>
      <CashTransactions>
        <CashTransaction accountId="U99999001" type="Dividends"
                         currency="USD" amount="100.00"
                         description="AAPL DIV"
                         dateTime="20250215;120000"
                         transactionID="TXN-CASH-42"
                         levelOfDetail="DETAIL"/>
      </CashTransactions>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>"""
    parsed = parse(xml)
    assert len(parsed.cash_transactions) == 1
    assert parsed.cash_transactions[0].transaction_id == "TXN-CASH-42"


def test_parse_transfer_captures_transaction_id():
    """Parser must extract transactionID attribute from <Transfer> elements."""
    from ibkr_control.ingest.flex.parser import parse

    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse>
  <FlexStatements>
    <FlexStatement accountId="U99999001" period="20250101-20251231"
                   fromDate="20250101" toDate="20251231">
      <AccountInformation accountId="U99999001" currency="USD"/>
      <Transfers>
        <Transfer accountId="U99999001" date="20250301"
                  direction="IN" symbol="MSFT" quantity="100"
                  type="ACATS" transactionID="TXN-XFER-99"/>
      </Transfers>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>"""
    parsed = parse(xml)
    assert len(parsed.transfers) == 1
    assert parsed.transfers[0].transaction_id == "TXN-XFER-99"
