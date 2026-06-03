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
async def test_accounts_ibkr_account_id_unique(db_session: AsyncSession, sample_org):
    """ibkr_account_id is UNIQUE global (shared identity — see Account.comment)."""
    from ibkr_control.db.models.accounts import Account

    db_session.add(
        Account(ibkr_account_id="U99999001", organization_id=sample_org.id, alias="test1")
    )
    await db_session.commit()
    db_session.add(Account(ibkr_account_id="U99999001", organization_id=sample_org.id, alias="dup"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_participations_pct_check_constraint(
    db_session: AsyncSession, sample_org, sample_party
):
    """pct_range CHECK (0 <= pct <= 1). Party-anchored (SP1)."""
    from ibkr_control.db.models.accounts import Account
    from ibkr_control.db.models.participations import Participation
    from datetime import date

    acc = Account(ibkr_account_id="U99999002", organization_id=sample_org.id, alias="test2")
    db_session.add(acc)
    await db_session.commit()

    db_session.add(
        Participation(
            party_id=sample_party.id,
            account_id=acc.id,
            organization_id=sample_org.id,
            pct=Decimal("1.5"),  # > 1, debe fallar
            valid_from=date(2026, 1, 1),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_participations_valid_range_check_constraint(
    db_session: AsyncSession, sample_org, sample_party
):
    """valid_range CHECK: valid_to must be strictly greater than valid_from when
    not NULL. Party-anchored (SP1)."""
    from ibkr_control.db.models.accounts import Account
    from ibkr_control.db.models.participations import Participation
    from datetime import date

    acc = Account(ibkr_account_id="U99999003", organization_id=sample_org.id, alias="range-test")
    db_session.add(acc)
    await db_session.commit()

    # valid_to BEFORE valid_from -> CHECK should fail
    db_session.add(
        Participation(
            party_id=sample_party.id,
            account_id=acc.id,
            organization_id=sample_org.id,
            pct=Decimal("0.5"),
            valid_from=date(2026, 1, 1),
            valid_to=date(2025, 12, 31),  # earlier than valid_from
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()
