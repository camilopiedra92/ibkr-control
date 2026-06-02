"""G1: data_access_grants — CHECKs de no-self-grant, rango válido, rol."""

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from ibkr_control.db.models.grants import DataAccessGrant


async def _two_users(db_session):
    from ibkr_control.auth.models import User

    a = User(email="grantor@t.com", hashed_password="x", is_active=True, name="Grantor")
    b = User(email="grantee@t.com", hashed_password="x", is_active=True, name="Grantee")
    db_session.add_all([a, b])
    await db_session.commit()
    await db_session.refresh(a)
    await db_session.refresh(b)
    return a, b


async def test_valid_grant_persists(db_session):
    a, b = await _two_users(db_session)
    g = DataAccessGrant(grantor_user_id=a.id, grantee_user_id=b.id, valid_from=date(2026, 1, 1))
    db_session.add(g)
    await db_session.commit()
    assert g.role == "read_only"


async def test_self_grant_rejected(db_session):
    a, _ = await _two_users(db_session)
    db_session.add(
        DataAccessGrant(grantor_user_id=a.id, grantee_user_id=a.id, valid_from=date(2026, 1, 1))
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_invalid_range_rejected(db_session):
    a, b = await _two_users(db_session)
    db_session.add(
        DataAccessGrant(
            grantor_user_id=a.id,
            grantee_user_id=b.id,
            valid_from=date(2026, 6, 1),
            valid_to=date(2026, 1, 1),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
