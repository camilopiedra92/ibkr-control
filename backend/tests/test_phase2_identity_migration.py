"""Tests del schema agregado en migration A (phase2_identity)."""

import pytest
from decimal import Decimal
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_accounts_table_exists(db_session: AsyncSession):
    result = await db_session.execute(text("SELECT to_regclass('accounts')"))
    assert result.scalar() == "accounts"


@pytest.mark.asyncio
async def test_accounts_columns(db_session: AsyncSession):
    result = await db_session.execute(
        text("""
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'accounts'
        ORDER BY ordinal_position
    """)
    )
    cols = {row[0]: (row[1], row[2]) for row in result.all()}
    assert "id" in cols
    assert "ibkr_account_id" in cols and cols["ibkr_account_id"][1] == "NO"
    assert "alias" in cols and cols["alias"][1] == "YES"
    assert "currency" in cols
    assert "created_at" in cols


@pytest.mark.asyncio
async def test_accounts_ibkr_account_id_unique(db_session: AsyncSession):
    from ibkr_control.db.models.accounts import Account

    db_session.add(Account(ibkr_account_id="U99999001", alias="test1"))
    await db_session.commit()
    db_session.add(Account(ibkr_account_id="U99999001", alias="dup"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_participations_pct_check_constraint(db_session: AsyncSession):
    from ibkr_control.db.models.accounts import Account
    from ibkr_control.db.models.participations import Participation
    from ibkr_control.auth.models import User
    from datetime import date

    user = User(
        email="t@t.com",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=False,
        name="Test",
    )
    db_session.add(user)
    acc = Account(ibkr_account_id="U99999002", alias="test2")
    db_session.add(acc)
    await db_session.commit()

    db_session.add(
        Participation(
            user_id=user.id,
            account_id=acc.id,
            pct=Decimal("1.5"),  # > 1, debe fallar
            valid_from=date(2026, 1, 1),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_flex_credentials_one_per_user(db_session: AsyncSession):
    from ibkr_control.db.models.flex_credentials import FlexCredentials
    from ibkr_control.auth.models import User

    user = User(
        email="t2@t.com",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=False,
        name="Test2",
    )
    db_session.add(user)
    await db_session.commit()

    db_session.add(FlexCredentials(user_id=user.id, token_encrypted=b"fake", ytd_query_id="123"))
    await db_session.commit()
    db_session.add(FlexCredentials(user_id=user.id, token_encrypted=b"fake2", ytd_query_id="456"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_users_setup_columns_exist(db_session: AsyncSession):
    result = await db_session.execute(
        text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'users' AND column_name IN ('setup_completed_at', 'setup_progress')
    """)
    )
    cols = {row[0] for row in result.all()}
    assert cols == {"setup_completed_at", "setup_progress"}


@pytest.mark.asyncio
async def test_participations_valid_range_check_constraint(db_session: AsyncSession):
    """valid_to must be strictly greater than valid_from when not NULL."""
    from ibkr_control.db.models.accounts import Account
    from ibkr_control.db.models.participations import Participation
    from ibkr_control.auth.models import User
    from datetime import date

    user = User(
        email="range@t.com",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=False,
        name="RangeTest",
    )
    db_session.add(user)
    acc = Account(ibkr_account_id="U99999003", alias="range-test")
    db_session.add(acc)
    await db_session.commit()

    # valid_to BEFORE valid_from -> CHECK should fail
    db_session.add(
        Participation(
            user_id=user.id,
            account_id=acc.id,
            pct=Decimal("0.5"),
            valid_from=date(2026, 1, 1),
            valid_to=date(2025, 12, 31),  # earlier than valid_from
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()
