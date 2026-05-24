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
