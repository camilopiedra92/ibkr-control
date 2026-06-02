"""Tests del cliente Socrata DIAN para TRM."""

from datetime import date
import pytest

from ibkr_control.ingest.trm.client import TrmClient


@pytest.mark.vcr(cassette_library_dir="tests/fixtures/cassettes/trm")
@pytest.mark.asyncio
async def test_fetch_empty_returns_no_rows():
    """Happy path via cassette: sin rows (since reciente → sin datos nuevos)."""
    client = TrmClient()
    rows = await client.fetch(since=date(2026, 5, 24))
    assert rows == []


@pytest.mark.vcr(cassette_library_dir="tests/fixtures/cassettes/trm")
@pytest.mark.asyncio
async def test_fetch_three_rows():
    """Cassette con 3 rows → devuelve lista con 3 dicts de Socrata."""
    client = TrmClient()
    rows = await client.fetch(since=date(2026, 1, 1))
    assert len(rows) == 3
    assert rows[0]["vigenciadesde"].startswith("2026-01-02")
