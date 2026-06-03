"""Smoke test del script poison_reset (R2 recovery)."""

import pytest
from datetime import date
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.db.models.flex_raw import FlexImport
from scripts.poison_reset import reset_poison


@pytest.mark.asyncio
async def test_reset_deletes_poison_row(db_session: AsyncSession, sample_org):
    h = "abc123" * 10  # 60 chars (not 64, but fine for the test)
    db_session.add(
        FlexImport(
            organization_id=sample_org.id,
            xml_hash=h,
            xml_bytes=b"x",
            xml_size_bytes=1,
            anyo=2026,
            source="web_service",
            year_status="rolling",
            period_covered_from=date(2026, 1, 1),
            period_covered_to=date(2026, 12, 31),
            status="poison",
            poison_reason="test",
        )
    )
    await db_session.commit()

    n_deleted = await reset_poison(db_session, organization_id=sample_org.id, xml_hash=h)

    assert n_deleted == 1
    row = await db_session.scalar(select(FlexImport).where(FlexImport.xml_hash == h))
    assert row is None


@pytest.mark.asyncio
async def test_reset_does_not_delete_ok_rows(db_session: AsyncSession, sample_org):
    """Safety: never delete a status='ok' row."""
    h = "def456" * 10
    db_session.add(
        FlexImport(
            organization_id=sample_org.id,
            xml_hash=h,
            xml_bytes=b"x",
            xml_size_bytes=1,
            anyo=2026,
            source="web_service",
            year_status="rolling",
            period_covered_from=date(2026, 1, 1),
            period_covered_to=date(2026, 12, 31),
            status="ok",
        )
    )
    await db_session.commit()

    n_deleted = await reset_poison(db_session, organization_id=sample_org.id, xml_hash=h)

    assert n_deleted == 0
    row = await db_session.scalar(select(FlexImport).where(FlexImport.xml_hash == h))
    assert row is not None
