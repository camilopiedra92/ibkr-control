"""Tests del schema dividend accruals (Migration E)."""

from datetime import date
from decimal import Decimal
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_dividend_accrual_tables_exist(db_session: AsyncSession):
    for t in ("change_in_dividend_accruals", "open_dividend_accruals"):
        result = await db_session.execute(text(f"SELECT to_regclass('{t}')"))
        assert result.scalar() == t, f"Table {t} missing"


@pytest.mark.asyncio
async def test_change_in_dividend_accruals_indexes_exist(db_session: AsyncSession):
    for idx in (
        "change_in_dividend_accruals_report_date_idx",
        "change_in_dividend_accruals_account_symbol_idx",
    ):
        result = await db_session.execute(text(f"SELECT to_regclass('{idx}')"))
        assert result.scalar() == idx, f"Index {idx} missing"


@pytest.mark.asyncio
async def test_open_dividend_accruals_indexes_exist(db_session: AsyncSession):
    for idx in (
        "open_dividend_accruals_report_date_idx",
        "open_dividend_accruals_account_symbol_idx",
    ):
        result = await db_session.execute(text(f"SELECT to_regclass('{idx}')"))
        assert result.scalar() == idx, f"Index {idx} missing"


@pytest.mark.asyncio
async def test_change_in_dividend_accruals_cascade_on_flex_import(
    db_session: AsyncSession, sample_user, sample_account
):
    """Delete de flex_import borra los change_in_dividend_accruals hijos."""
    from ibkr_control.db.models.flex_raw import FlexImport, ChangeInDividendAccrual

    fi = FlexImport(
        user_id=sample_user.id,
        anyo=2025,
        xml_hash="hash-cascade-div-chg",
        xml_size_bytes=100,
        xml_bytes=b"<test/>",
        source="manual_upload",
        period_covered_from=date(2025, 1, 1),
        period_covered_to=date(2025, 12, 31),
        status="ok",
    )
    db_session.add(fi)
    await db_session.commit()
    fi_id = fi.id

    db_session.add(
        ChangeInDividendAccrual(
            flex_import_id=fi_id,
            account_id=sample_account.id,
            symbol="AAPL",
            currency="USD",
            report_date=date(2025, 3, 15),
            accrual_date=date(2025, 3, 14),
            quantity=Decimal("10"),
            gross_amount_usd=Decimal("5.00"),
            tax_usd=Decimal("0.75"),
            net_amount_usd=Decimal("4.25"),
        )
    )
    await db_session.commit()

    await db_session.delete(fi)
    await db_session.commit()

    result = await db_session.execute(
        text(f"SELECT COUNT(*) FROM change_in_dividend_accruals WHERE flex_import_id = {fi_id}")
    )
    assert result.scalar() == 0


@pytest.mark.asyncio
async def test_open_dividend_accruals_cascade_on_flex_import(
    db_session: AsyncSession, sample_user, sample_account
):
    """Delete de flex_import borra los open_dividend_accruals hijos."""
    from ibkr_control.db.models.flex_raw import FlexImport, OpenDividendAccrual

    fi = FlexImport(
        user_id=sample_user.id,
        anyo=2025,
        xml_hash="hash-cascade-div-open",
        xml_size_bytes=100,
        xml_bytes=b"<test/>",
        source="manual_upload",
        period_covered_from=date(2025, 1, 1),
        period_covered_to=date(2025, 12, 31),
        status="ok",
    )
    db_session.add(fi)
    await db_session.commit()
    fi_id = fi.id

    db_session.add(
        OpenDividendAccrual(
            flex_import_id=fi_id,
            account_id=sample_account.id,
            symbol="NKE",
            currency="USD",
            report_date=date(2025, 12, 31),
            quantity=Decimal("31.013"),
            gross_amount_usd=Decimal("12.72"),
            tax_usd=Decimal("3.82"),
            net_amount_usd=Decimal("8.90"),
        )
    )
    await db_session.commit()

    await db_session.delete(fi)
    await db_session.commit()

    result = await db_session.execute(
        text(f"SELECT COUNT(*) FROM open_dividend_accruals WHERE flex_import_id = {fi_id}")
    )
    assert result.scalar() == 0


@pytest.mark.asyncio
async def test_change_in_dividend_accruals_report_date_not_null(
    db_session: AsyncSession, sample_user, sample_account
):
    """report_date NOT NULL is enforced at DB level."""
    from ibkr_control.db.models.flex_raw import FlexImport, ChangeInDividendAccrual

    fi = FlexImport(
        user_id=sample_user.id,
        anyo=2025,
        xml_hash="hash-div-nn-chg",
        xml_size_bytes=100,
        xml_bytes=b"<test/>",
        source="manual_upload",
        period_covered_from=date(2025, 1, 1),
        period_covered_to=date(2025, 12, 31),
        status="ok",
    )
    db_session.add(fi)
    await db_session.commit()

    db_session.add(
        ChangeInDividendAccrual(
            flex_import_id=fi.id,
            account_id=sample_account.id,
            symbol="AAPL",
            currency="USD",
            report_date=None,  # violates NOT NULL
            quantity=Decimal("10"),
            gross_amount_usd=Decimal("5.00"),
            tax_usd=Decimal("0.75"),
            net_amount_usd=Decimal("4.25"),
        )
    )
    with pytest.raises((IntegrityError, Exception)):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_open_dividend_accruals_report_date_not_null(
    db_session: AsyncSession, sample_user, sample_account
):
    """report_date NOT NULL is enforced at DB level."""
    from ibkr_control.db.models.flex_raw import FlexImport, OpenDividendAccrual

    fi = FlexImport(
        user_id=sample_user.id,
        anyo=2025,
        xml_hash="hash-div-nn-open",
        xml_size_bytes=100,
        xml_bytes=b"<test/>",
        source="manual_upload",
        period_covered_from=date(2025, 1, 1),
        period_covered_to=date(2025, 12, 31),
        status="ok",
    )
    db_session.add(fi)
    await db_session.commit()

    db_session.add(
        OpenDividendAccrual(
            flex_import_id=fi.id,
            account_id=sample_account.id,
            symbol="NKE",
            currency="USD",
            report_date=None,  # violates NOT NULL
            quantity=Decimal("31.013"),
            gross_amount_usd=Decimal("12.72"),
            tax_usd=Decimal("3.82"),
            net_amount_usd=Decimal("8.90"),
        )
    )
    with pytest.raises((IntegrityError, Exception)):
        await db_session.commit()
    await db_session.rollback()
