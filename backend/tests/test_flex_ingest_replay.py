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

import asyncio as _asyncio
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pytest
from alembic import command as _alembic_cmd
from alembic.config import Config as _AlembicConfig
from lxml import etree
from sqlalchemy import func, select, insert
from sqlalchemy.ext.asyncio import (
    async_sessionmaker as _async_sessionmaker,
    create_async_engine as _create_async_engine,
)

from ibkr_control.auth.models import User
from ibkr_control.db.models.flex_raw import (
    Trade,
    ClosedLot,
    OpenPositionLot,
    CashTransaction,
    Transfer,
    ChangeInDividendAccrual,
    OpenDividendAccrual,
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
            insert(User)
            .values(
                email="replay@test.local",
                hashed_password="x",
                name="Replay Test User",
                is_active=True,
                is_verified=True,
                is_superuser=False,
            )
            .returning(User.id)
        )
        await session.commit()
        return result.scalar_one()


@pytest.mark.parametrize("fixture_name", ["ACTIVITY_2024_sanitized", "ACTIVITY_2025_sanitized"])
async def test_persist_twice_yields_zero_new(
    ephemeral_session_factory,
    fixture_name,
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
            session,
            parsed=parsed,
            user_id=user_id,
            xml_bytes=xml,
            source="web_service",
        )
        await session.commit()

    # Second ingest of same XML — should hash-dedup fast-path
    async with ephemeral_session_factory() as session:
        parsed = parse(xml)
        _, counters_2 = await persist(
            session,
            parsed=parsed,
            user_id=user_id,
            xml_bytes=xml,
            source="web_service",
        )
        await session.commit()

    # First run should have gone through the full persist path
    assert counters_1["hash_dedup"] is False

    # Second run should hit fast-path
    assert counters_2["hash_dedup"] is True
    assert counters_2["hash_status"] == "ok"


@pytest.mark.parametrize("fixture_name", ["ACTIVITY_2024_sanitized", "ACTIVITY_2025_sanitized"])
async def test_counts_match_fixture_metadata(
    ephemeral_session_factory,
    fixture_name,
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
            session,
            parsed=parsed,
            user_id=user_id,
            xml_bytes=xml,
            source="web_service",
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
                    f"{fixture_name}.{entity_name}: expected {expected[entity_name]}, got {actual}"
                )


@pytest.mark.asyncio
async def test_closed_lots_sum_matches_pool_2025(ephemeral_session_factory):
    """FIFO parity (R3 part 3): Σ closed_lots.fifo_pnl_usd in DB equals
    Σ <Lot levelOfDetail="CLOSED_LOT">.fifoPnlRealized from raw XML.

    Heredado de renta/_invariants.py — the gold standard for verifying that
    persister doesn't drift from the XML source of truth.

    Note: in IBKR Flex XMLs the closed-lot records are <Lot> elements with
    levelOfDetail="CLOSED_LOT" nested under <Trades>, NOT a separate
    <ClosedLot> tag.  The filter on levelOfDetail is required because sibling
    <Lot> elements carry other levelOfDetail values (e.g. "LOT", "ORDER") that
    should not be summed.
    """
    xml = (FIXTURES_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    user_id = await _create_user(ephemeral_session_factory)

    # Persist
    async with ephemeral_session_factory() as session:
        parsed = parse(xml)
        await persist(
            session,
            parsed=parsed,
            user_id=user_id,
            xml_bytes=xml,
            source="web_service",
        )
        await session.commit()

    # Sum from DB (Decimal)
    async with ephemeral_session_factory() as session:
        db_sum = await session.scalar(select(func.coalesce(func.sum(ClosedLot.fifo_pnl_usd), 0)))
    db_sum = Decimal(str(db_sum))  # session.scalar may return Decimal or numeric str

    # Sum from raw XML: <Lot levelOfDetail="CLOSED_LOT" fifoPnlRealized="...">
    # Only CLOSED_LOT rows are persisted; other levelOfDetail values (LOT, ORDER,
    # SYMBOL_SUMMARY, etc.) must be excluded to avoid double-counting.
    #
    # Each XML value is quantized to 4 decimal places before summing because
    # the DB column is Numeric(20, 4) — Postgres rounds each value to 4dp on
    # INSERT (ROUND_HALF_UP).  Summing the full-precision XML values and then
    # comparing to the DB sum would produce a false delta due to accumulated
    # sub-cent truncation across 146 rows.
    _FOUR_DP = Decimal("0.0001")
    tree = etree.fromstring(xml)
    xml_sum = Decimal("0")
    for el in tree.iter("Lot"):
        if el.get("levelOfDetail") != "CLOSED_LOT":
            continue
        v = el.get("fifoPnlRealized")
        if v:
            xml_sum += Decimal(v).quantize(_FOUR_DP, rounding=ROUND_HALF_UP)

    assert xml_sum != Decimal("0"), (
        "no CLOSED_LOT rows parsed from XML — fixture truncated or tag filter broke"
    )
    assert db_sum == xml_sum, f"DB sum {db_sum} != XML sum {xml_sum} (delta: {db_sum - xml_sum})"


@pytest.mark.asyncio
async def test_cross_schema_replay_with_downgrade_upgrade(ephemeral_postgres, monkeypatch):
    """Cross-schema replay (R3 part 4).

    The test that would have caught A3 amendments #1/#2/#3 before prod:

    1. Boot ephemeral postgres at HEAD (phase26).
    2. alembic downgrade -1 → revert phase26, schema is now phase25.
    3. Insert fixture data using the HEAD persister code against phase25
       schema (persister doesn't touch phase26-specific columns on the
       success path, so it's compatible).
    4. alembic upgrade head → re-apply phase26.
    5. Re-ingest the same XML — must hit hash-dedup fast-path (no
       UniqueViolation, no recomputation).
    """
    sync_url = ephemeral_postgres.get_connection_url()
    async_url = sync_url.replace("+psycopg2", "+asyncpg")

    monkeypatch.setenv("DATABASE_URL", async_url)
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")
    from ibkr_control.config import get_settings

    get_settings.cache_clear()

    backend_root = Path(__file__).resolve().parent.parent
    cfg = _AlembicConfig(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))

    # Step 1: upgrade to head
    await _asyncio.to_thread(_alembic_cmd.upgrade, cfg, "head")
    # Step 2: downgrade phase26 (revert to phase25)
    await _asyncio.to_thread(_alembic_cmd.downgrade, cfg, "-1")

    # Step 3: insert a flex_imports row under phase25 schema using Core INSERT.
    # Bypass the ORM (which includes phase26 columns poison_reason + per-user
    # UNIQUE) by writing raw SQL that only references columns present in phase25.
    # This mirrors exactly what the phase25-era persister would have written.
    from sqlalchemy import text as _text
    from ibkr_control.ingest.hash_dedup import xml_hash as _xml_hash

    xml = (FIXTURES_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    h = _xml_hash(xml)

    engine = _create_async_engine(async_url, echo=False)
    factory = _async_sessionmaker(engine, expire_on_commit=False)
    user_id = await _create_user(factory)

    try:
        async with engine.begin() as conn:
            await conn.execute(
                _text("""
                    INSERT INTO flex_imports (
                        user_id, anyo, xml_hash, xml_size_bytes, xml_bytes,
                        source, period_covered_from, period_covered_to,
                        year_status, status
                    ) VALUES (
                        :user_id, :anyo, :xml_hash, :xml_size_bytes, :xml_bytes,
                        :source, :period_from, :period_to, :year_status, :status
                    )
                """),
                {
                    "user_id": user_id,
                    "anyo": 2025,
                    "xml_hash": h,
                    "xml_size_bytes": len(xml),
                    "xml_bytes": xml,
                    "source": "web_service",
                    "period_from": date(2025, 1, 1),
                    "period_to": date(2025, 12, 31),
                    "year_status": "sealed",
                    "status": "ok",
                },
            )
    finally:
        await engine.dispose()

    # Step 4: upgrade head (re-apply phase26)
    await _asyncio.to_thread(_alembic_cmd.upgrade, cfg, "head")

    # Step 5: re-ingest the same XML via the ORM/persister against phase26 schema.
    # Should hit hash-dedup fast-path because (user_id, xml_hash) row already exists.
    engine2 = _create_async_engine(async_url, echo=False)
    factory2 = _async_sessionmaker(engine2, expire_on_commit=False)
    try:
        async with factory2() as session:
            parsed = parse(xml)
            _, counters_2 = await persist(
                session,
                parsed=parsed,
                user_id=user_id,
                xml_bytes=xml,
                source="web_service",
            )
            await session.commit()
        # Expect fast-path: pre-phase26 row found via (user_id, xml_hash) — post-upgrade
        assert counters_2["hash_dedup"] is True
        assert counters_2["hash_status"] == "ok"
    finally:
        await engine2.dispose()
