"""Replay tests del Flex ingest (R3 — idempotency + counts × N fixtures).

Tests parametrizados sobre los 2 fixtures Flex XML reales disponibles:
  - ACTIVITY_2024_sanitized.xml  (year sealed, sin accruals)
  - ACTIVITY_2025_sanitized.xml  (year sealed, con 51 change_accruals + 1 open)

(a) test_persist_twice_yields_zero_new — R3 idempotency: 2× ingest del mismo
    XML activa el fast-path A4 hash-dedup (counters_2.hash_dedup=True, hash_status="ok").

(b) test_counts_match_fixture_metadata — per-entity counts bloqueados contra
    EXPECTED_COUNTS (llenados manualmente la primera corrida, al estilo
    renta/_invariants.py). Falla ruidosamente si el persister introduce
    regresión en cualquier entity type.

Nota: el plan original pedía 3 fixtures (2024 + 2025 + 2026_ytd), pero solo hay
2 sanitizados en el repo. La intención del spec R3 (idempotency + count parity
× múltiples fixtures reales) está cubierta con los 2 disponibles.
"""
from pathlib import Path

import pytest
from sqlalchemy import func, select, insert

from ibkr_control.auth.models import User
from ibkr_control.db.models.flex_raw import (
    FlexImport, Trade, ClosedLot, OpenPositionLot, CashTransaction,
    Transfer, ChangeInDividendAccrual, OpenDividendAccrual,
)
from ibkr_control.ingest.flex.parser import parse
from ibkr_control.ingest.flex.persister import persist


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "xml"

# Counts locked manually after running the discovery pass against each fixture.
# Pattern: renta/_invariants.py — assert exact counts, fail loudly on regression.
#
# 2024 fixture: year 2024 sealed (period_to 2024-12-31), no accruals table data.
# 2025 fixture: year 2025 sealed, 51 change_in_dividend_accruals + 1 open_accrual.
#
# NOTE: these differ from CLAUDE.md smoke-test totals (302 trades / 154 closed_lots /
# 327 open_lots) because those combined 3 XML imports (2024 + 2025 + 2026 YTD).
# These counts are per-fixture, against a fresh DB with a single import.
EXPECTED_COUNTS: dict[str, dict[str, int]] = {
    "ACTIVITY_2024_sanitized": {
        "trades": 95,
        "closed_lots": 5,
        "open_lots": 86,
        "cash_tx": 13,
        "transfers": 3,
        "change_accruals": 0,
        "open_accruals": 0,
    },
    "ACTIVITY_2025_sanitized": {
        "trades": 194,
        "closed_lots": 146,
        "open_lots": 115,
        "cash_tx": 58,
        "transfers": 11,
        "change_accruals": 51,
        "open_accruals": 1,
    },
}


async def _create_user(session_factory) -> int:
    """Insert a minimal User row; return its id."""
    async with session_factory() as session:
        result = await session.execute(
            insert(User).values(
                email="replay@test.local",
                hashed_password="x",
                name="Replay Test User",
                is_active=True,
                is_verified=True,
                is_superuser=False,
            ).returning(User.id)
        )
        await session.commit()
        return result.scalar_one()


@pytest.mark.parametrize(
    "fixture_name", ["ACTIVITY_2024_sanitized", "ACTIVITY_2025_sanitized"]
)
async def test_persist_twice_yields_zero_new(
    ephemeral_session_factory, fixture_name,
):
    """R3 idempotency: persist 2× consecutive → counters_2 reports hash_dedup=True.

    First run processes the XML normally (hash_dedup=False).
    Second run with the same xml_bytes hits the A4 fast-path (hash_dedup=True)
    and returns hash_status="ok" (the first import landed cleanly).
    """
    xml = (FIXTURES_DIR / f"{fixture_name}.xml").read_bytes()
    user_id = await _create_user(ephemeral_session_factory)

    async with ephemeral_session_factory() as session:
        parsed = parse(xml)
        _, counters_1 = await persist(
            session, parsed=parsed, user_id=user_id,
            xml_bytes=xml, source="web_service",
        )
        await session.commit()

    # Second ingest of same XML — should hash-dedup fast-path
    async with ephemeral_session_factory() as session:
        parsed = parse(xml)
        _, counters_2 = await persist(
            session, parsed=parsed, user_id=user_id,
            xml_bytes=xml, source="web_service",
        )
        await session.commit()

    # First run should have gone through the full persist path
    assert counters_1["hash_dedup"] is False

    # Second run should hit fast-path
    assert counters_2["hash_dedup"] is True
    assert counters_2["hash_status"] == "ok"


@pytest.mark.parametrize(
    "fixture_name", ["ACTIVITY_2024_sanitized", "ACTIVITY_2025_sanitized"]
)
async def test_counts_match_fixture_metadata(
    ephemeral_session_factory, fixture_name,
):
    """Counts per entity match EXPECTED_COUNTS (locked from real fixture data).

    Regressions in the parser/persister will surface here as count mismatches.
    Checks all entity types individually to produce clear failure messages.
    """
    expected = EXPECTED_COUNTS[fixture_name]
    if not expected:
        pytest.skip(f"EXPECTED_COUNTS[{fixture_name}] not filled yet")

    xml = (FIXTURES_DIR / f"{fixture_name}.xml").read_bytes()
    user_id = await _create_user(ephemeral_session_factory)

    async with ephemeral_session_factory() as session:
        parsed = parse(xml)
        await persist(
            session, parsed=parsed, user_id=user_id,
            xml_bytes=xml, source="web_service",
        )
        await session.commit()

    async with ephemeral_session_factory() as session:
        async def _count(model) -> int:
            return await session.scalar(select(func.count()).select_from(model))

        for entity_name, model in [
            ("trades", Trade),
            ("closed_lots", ClosedLot),
            ("open_lots", OpenPositionLot),
            ("cash_tx", CashTransaction),
            ("transfers", Transfer),
            ("change_accruals", ChangeInDividendAccrual),
            ("open_accruals", OpenDividendAccrual),
        ]:
            if entity_name in expected:
                actual = await _count(model)
                assert actual == expected[entity_name], (
                    f"{fixture_name}.{entity_name}: "
                    f"expected {expected[entity_name]}, got {actual}"
                )
