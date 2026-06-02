"""Tests del schema TRM (migration B)."""

from datetime import date
from decimal import Decimal
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_trm_days_pk_is_date(db_session: AsyncSession):
    from ibkr_control.db.models.trm import TrmDay

    db_session.add(
        TrmDay(
            date=date(2026, 1, 15),
            value_cop=Decimal("4123.45"),
            vigencia_desde=date(2026, 1, 15),
            vigencia_hasta=date(2026, 1, 15),
        )
    )
    await db_session.commit()
    db_session.add(
        TrmDay(
            date=date(2026, 1, 15),  # mismo PK
            value_cop=Decimal("9999.99"),
            vigencia_desde=date(2026, 1, 15),
            vigencia_hasta=date(2026, 1, 15),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_trm_days_index_on_date(db_session: AsyncSession):
    result = await db_session.execute(
        text("""
        SELECT indexname FROM pg_indexes
        WHERE tablename = 'trm_days' AND indexname = 'trm_days_date_idx'
    """)
    )
    assert result.scalar() == "trm_days_date_idx"


@pytest.mark.asyncio
async def test_trm_imports_table_exists(db_session: AsyncSession):
    result = await db_session.execute(text("SELECT to_regclass('trm_imports')"))
    assert result.scalar() == "trm_imports"


@pytest.mark.asyncio
async def test_trm_days_value_precision(db_session: AsyncSession):
    """TRM puede llegar a 6 enteros + 4 decimales (e.g. 999999.1234). NUMERIC(12,4) lo banca."""
    from ibkr_control.db.models.trm import TrmDay

    db_session.add(
        TrmDay(
            date=date(2026, 2, 1),
            value_cop=Decimal("12345.6789"),
            vigencia_desde=date(2026, 2, 1),
            vigencia_hasta=date(2026, 2, 1),
        )
    )
    await db_session.commit()
