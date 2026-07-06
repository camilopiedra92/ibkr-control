from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from ibkr_control.db.models.accounts import Account
from ibkr_control.db.models.parties import Party
from ibkr_control.db.models.participations import Participation
from ibkr_control.db.participations import upsert_participation
from tests.conftest import scope_session_to_org


@pytest.mark.asyncio
async def test_upsert_participation_scd2_close_and_insert(db_session, sample_org):
    """HD-5a: cambiar el pct cierra la fila abierta (valid_to=at) e inserta la nueva.
    Mismo pct = no-op (sin fila nueva ni cierre)."""
    org_id = sample_org.id
    await scope_session_to_org(db_session, org_id)
    party = Party(organization_id=org_id, display_name="Owner")
    acc = Account(ibkr_account_id="U99999001", organization_id=org_id, currency="USD")
    db_session.add_all([party, acc])
    await db_session.flush()

    at1 = date(2026, 1, 1)
    await upsert_participation(
        db_session,
        party_id=party.id,
        account_id=acc.id,
        organization_id=org_id,
        pct=Decimal("0.5000"),
        at=at1,
    )
    await db_session.flush()

    # Mismo pct → no-op
    await upsert_participation(
        db_session,
        party_id=party.id,
        account_id=acc.id,
        organization_id=org_id,
        pct=Decimal("0.5000"),
        at=date(2026, 2, 1),
    )
    await db_session.flush()
    rows = (
        await db_session.scalars(select(Participation).where(Participation.party_id == party.id))
    ).all()
    assert len(rows) == 1
    assert rows[0].valid_to is None

    # pct nuevo → cierra la vieja + inserta
    at2 = date(2026, 3, 1)
    await upsert_participation(
        db_session,
        party_id=party.id,
        account_id=acc.id,
        organization_id=org_id,
        pct=Decimal("1.0000"),
        at=at2,
    )
    await db_session.flush()
    rows = (
        await db_session.scalars(
            select(Participation)
            .where(Participation.party_id == party.id)
            .order_by(Participation.valid_from)
        )
    ).all()
    assert len(rows) == 2
    assert rows[0].valid_to == at2 and rows[0].pct == Decimal("0.5000")
    assert rows[1].valid_to is None and rows[1].pct == Decimal("1.0000")


@pytest.mark.asyncio
async def test_upsert_participation_same_day_pct_change_updates_in_place(db_session, sample_org):
    """Same-day correction: re-onboarding the same day with a changed pct must NOT
    close the open row on its own valid_from (a zero-width [at, at) interval violates
    ck_participations_valid_range and 500s). SCD-2 has no positive-duration history to
    preserve, so the open row's pct is updated in place — single open row, no crash."""
    org_id = sample_org.id
    await scope_session_to_org(db_session, org_id)
    party = Party(organization_id=org_id, display_name="Owner")
    acc = Account(ibkr_account_id="U99999002", organization_id=org_id, currency="USD")
    db_session.add_all([party, acc])
    await db_session.flush()

    today = date(2026, 6, 1)
    await upsert_participation(
        db_session,
        party_id=party.id,
        account_id=acc.id,
        organization_id=org_id,
        pct=Decimal("0.5000"),
        at=today,
    )
    await db_session.flush()

    # Same day, changed pct → in-place update (must not raise, must not open a 2nd row)
    await upsert_participation(
        db_session,
        party_id=party.id,
        account_id=acc.id,
        organization_id=org_id,
        pct=Decimal("1.0000"),
        at=today,
    )
    await db_session.flush()

    rows = (
        await db_session.scalars(select(Participation).where(Participation.party_id == party.id))
    ).all()
    assert len(rows) == 1
    assert rows[0].valid_from == today
    assert rows[0].valid_to is None
    assert rows[0].pct == Decimal("1.0000")
