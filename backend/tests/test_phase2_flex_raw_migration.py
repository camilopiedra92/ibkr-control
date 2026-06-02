"""Tests del schema flex_raw (migration C)."""

from datetime import date
from decimal import Decimal
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_flex_imports_tables_exist(db_session: AsyncSession):
    expected = {
        "flex_imports",
        "trades",
        "closed_lots",
        "open_position_lots",
        "transfers",
        "cash_transactions",
    }
    for t in expected:
        result = await db_session.execute(text(f"SELECT to_regclass('{t}')"))
        assert result.scalar() == t, f"Table {t} missing"


@pytest.mark.asyncio
async def test_flex_imports_xml_hash_unique(db_session: AsyncSession, sample_user, sample_account):
    from ibkr_control.db.models.flex_raw import FlexImport

    fi1 = FlexImport(
        user_id=sample_user.id,
        anyo=2025,
        xml_hash="deadbeef",
        xml_size_bytes=1000,
        xml_bytes=b"<xml/>",
        source="manual_upload",
        period_covered_from=date(2025, 1, 1),
        period_covered_to=date(2025, 12, 31),
        status="ok",
    )
    db_session.add(fi1)
    await db_session.commit()
    db_session.add(
        FlexImport(
            user_id=sample_user.id,
            anyo=2025,
            xml_hash="deadbeef",  # dup
            xml_size_bytes=2000,
            xml_bytes=b"<xml/>",
            source="web_service",
            period_covered_from=date(2025, 1, 1),
            period_covered_to=date(2025, 12, 31),
            status="ok",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_trades_transaction_id_unique(db_session: AsyncSession, sample_user, sample_account):
    from ibkr_control.db.models.flex_raw import FlexImport, Trade

    fi = FlexImport(
        user_id=sample_user.id,
        anyo=2025,
        xml_hash="hash-trades-test",
        xml_size_bytes=100,
        xml_bytes=b"<xml/>",
        source="manual_upload",
        period_covered_from=date(2025, 1, 1),
        period_covered_to=date(2025, 12, 31),
        status="ok",
    )
    db_session.add(fi)
    await db_session.commit()
    t1 = Trade(
        flex_import_id=fi.id,
        transaction_id="TXN-001",
        account_id=sample_account.id,
        symbol="AAPL",
        asset_class="STK",
        trade_date=date(2025, 1, 1),
        qty=Decimal("10"),
        price_usd=Decimal("150.00"),
        proceeds_usd=Decimal("-1500.00"),
        commission_usd=Decimal("1.00"),
        buy_sell="BUY",
    )
    db_session.add(t1)
    await db_session.commit()
    db_session.add(
        Trade(
            flex_import_id=fi.id,
            transaction_id="TXN-001",  # dup
            account_id=sample_account.id,
            symbol="AAPL",
            asset_class="STK",
            trade_date=date(2025, 1, 2),
            qty=Decimal("5"),
            price_usd=Decimal("160.00"),
            proceeds_usd=Decimal("-800.00"),
            commission_usd=Decimal("1.00"),
            buy_sell="BUY",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_trades_buy_sell_check(db_session: AsyncSession, sample_user, sample_account):
    from ibkr_control.db.models.flex_raw import FlexImport, Trade

    fi = FlexImport(
        user_id=sample_user.id,
        anyo=2025,
        xml_hash="hash-bs-test",
        xml_size_bytes=100,
        xml_bytes=b"<xml/>",
        source="manual_upload",
        period_covered_from=date(2025, 1, 1),
        period_covered_to=date(2025, 12, 31),
        status="ok",
    )
    db_session.add(fi)
    await db_session.commit()
    db_session.add(
        Trade(
            flex_import_id=fi.id,
            transaction_id="TXN-BAD",
            account_id=sample_account.id,
            symbol="AAPL",
            asset_class="STK",
            trade_date=date(2025, 1, 1),
            qty=Decimal("10"),
            price_usd=Decimal("150.00"),
            proceeds_usd=Decimal("-1500.00"),
            commission_usd=Decimal("1.00"),
            buy_sell="INVALID",  # check constraint fails
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_delete_flex_import_sets_children_null(
    db_session: AsyncSession, sample_user, sample_account
):
    """Phase 2.5: delete del flex_import nulea flex_import_id en hijos (SET NULL, no CASCADE).

    Rationale: bajo el persister idempotente, los rows hijos viven independientemente
    del flex_import que los introdujo — re-imports preservan la data fiscal aunque
    el flex_import original sea borrado para limpieza.
    """
    from ibkr_control.db.models.flex_raw import FlexImport, Trade

    fi = FlexImport(
        user_id=sample_user.id,
        anyo=2025,
        xml_hash="hash-set-null-test",
        xml_size_bytes=100,
        xml_bytes=b"<xml/>",
        source="manual_upload",
        period_covered_from=date(2025, 1, 1),
        period_covered_to=date(2025, 12, 31),
        status="ok",
    )
    db_session.add(fi)
    await db_session.commit()
    fi_id = fi.id

    db_session.add(
        Trade(
            flex_import_id=fi_id,
            transaction_id="TXN-SET-NULL",
            account_id=sample_account.id,
            symbol="AAPL",
            asset_class="STK",
            trade_date=date(2025, 1, 1),
            qty=Decimal("10"),
            price_usd=Decimal("150.00"),
            proceeds_usd=Decimal("-1500.00"),
            commission_usd=Decimal("1.00"),
            buy_sell="BUY",
        )
    )
    await db_session.commit()

    await db_session.delete(fi)
    await db_session.commit()

    # Trade row sigue viviendo, con flex_import_id=NULL
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM trades WHERE transaction_id = 'TXN-SET-NULL'")
    )
    assert result.scalar() == 1
    result = await db_session.execute(
        text("SELECT flex_import_id FROM trades WHERE transaction_id = 'TXN-SET-NULL'")
    )
    assert result.scalar() is None
