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
        {
            "date": date(2026, 1, 1),
            "value_cop": Decimal("4100"),
            "vigencia_desde": date(2026, 1, 1),
            "vigencia_hasta": date(2026, 1, 1),
        },
        {
            "date": date(2026, 1, 2),
            "value_cop": Decimal("4110"),
            "vigencia_desde": date(2026, 1, 2),
            "vigencia_hasta": date(2026, 1, 2),
        },
    ]
    n = await bulk_upsert_days(db_session, expanded)
    assert n == 2

    from ibkr_control.db.models.trm import TrmDay

    count = await db_session.scalar(select(func.count(TrmDay.date)))
    assert count == 2


@pytest.mark.asyncio
async def test_upsert_updates_existing_value(db_session: AsyncSession):
    from ibkr_control.db.models.trm import TrmDay

    db_session.add(
        TrmDay(
            date=date(2026, 1, 1),
            value_cop=Decimal("4000"),
            vigencia_desde=date(2026, 1, 1),
            vigencia_hasta=date(2026, 1, 1),
        )
    )
    await db_session.commit()

    expanded = [
        {
            "date": date(2026, 1, 1),
            "value_cop": Decimal("4250"),
            "vigencia_desde": date(2026, 1, 1),
            "vigencia_hasta": date(2026, 1, 1),
        }
    ]
    await bulk_upsert_days(db_session, expanded)

    # Expire so SQLAlchemy re-fetches from DB (not identity map)
    await db_session.commit()
    updated = await db_session.scalar(select(TrmDay).where(TrmDay.date == date(2026, 1, 1)))
    assert updated.value_cop == Decimal("4250.0000")


@pytest.mark.asyncio
async def test_bulk_handles_empty_list(db_session: AsyncSession):
    n = await bulk_upsert_days(db_session, [])
    assert n == 0


@pytest.mark.asyncio
async def test_bulk_chunks_large_batches_under_pg_param_limit(
    db_session: AsyncSession, monkeypatch
):
    """Full backfill from Socrata expands to ~12.5k days; with 4 cols/row that
    is ~50k bind params, exceeding asyncpg's 32767 limit. Persister must chunk.

    We force a tiny batch size so the test is fast yet exercises the >1-chunk
    path, then assert every row landed in DB.
    """
    from datetime import timedelta

    from ibkr_control.db.models.trm import TrmDay
    from ibkr_control.ingest.trm import persister as persister_mod

    monkeypatch.setattr(persister_mod, "_BATCH_SIZE", 4)

    n_rows = 15  # 15 / 4 = 4 chunks (4 + 4 + 4 + 3)
    base = date(2026, 1, 1)
    expanded = [
        {
            "date": base + timedelta(days=i),
            "value_cop": Decimal("4000") + Decimal(i),
            "vigencia_desde": base + timedelta(days=i),
            "vigencia_hasta": base + timedelta(days=i),
        }
        for i in range(n_rows)
    ]

    n = await bulk_upsert_days(db_session, expanded)
    await db_session.commit()

    assert n == n_rows
    persisted = await db_session.scalar(select(func.count(TrmDay.date)))
    assert persisted == n_rows
