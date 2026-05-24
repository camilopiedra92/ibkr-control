"""Tests del persister TRM — bulk upsert con ON CONFLICT."""
from datetime import date
from decimal import Decimal
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.ingest.trm.persister import bulk_upsert_days


@pytest.mark.asyncio
async def test_insert_new_days(db_session: AsyncSession):
    expanded = [
        {"date": date(2026, 1, 1), "value_cop": Decimal("4100"),
         "vigencia_desde": date(2026, 1, 1), "vigencia_hasta": date(2026, 1, 1)},
        {"date": date(2026, 1, 2), "value_cop": Decimal("4110"),
         "vigencia_desde": date(2026, 1, 2), "vigencia_hasta": date(2026, 1, 2)},
    ]
    n = await bulk_upsert_days(db_session, expanded)
    assert n == 2

    from ibkr_control.db.models.trm import TrmDay
    count = await db_session.scalar(select(func.count(TrmDay.date)))
    assert count == 2


@pytest.mark.asyncio
async def test_upsert_updates_existing_value(db_session: AsyncSession):
    from ibkr_control.db.models.trm import TrmDay

    db_session.add(TrmDay(
        date=date(2026, 1, 1), value_cop=Decimal("4000"),
        vigencia_desde=date(2026, 1, 1), vigencia_hasta=date(2026, 1, 1),
    ))
    await db_session.commit()

    expanded = [{
        "date": date(2026, 1, 1), "value_cop": Decimal("4250"),
        "vigencia_desde": date(2026, 1, 1), "vigencia_hasta": date(2026, 1, 1),
    }]
    await bulk_upsert_days(db_session, expanded)

    # Expire so SQLAlchemy re-fetches from DB (not identity map)
    await db_session.commit()
    updated = await db_session.scalar(select(TrmDay).where(TrmDay.date == date(2026, 1, 1)))
    assert updated.value_cop == Decimal("4250.0000")


@pytest.mark.asyncio
async def test_bulk_handles_empty_list(db_session: AsyncSession):
    n = await bulk_upsert_days(db_session, [])
    assert n == 0
