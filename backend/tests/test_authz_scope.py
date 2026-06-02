"""G2/G3: resolver de visibilidad por participations + grants."""

from datetime import date

import pytest

from ibkr_control.authz.scope import GrantRequiredError, visible_account_ids


async def _seed(db_session):
    from ibkr_control.auth.models import User
    from ibkr_control.db.models.accounts import Account
    from ibkr_control.db.models.participations import Participation
    from ibkr_control.db.models.grants import DataAccessGrant

    owner = User(email="owner@t.com", hashed_password="x", is_active=True, name="Owner")
    cont = User(email="cont@t.com", hashed_password="x", is_active=True, name="Contador")
    other = User(email="other@t.com", hashed_password="x", is_active=True, name="Other")
    acc = Account(ibkr_account_id="U11111111", alias="own", currency="USD")
    db_session.add_all([owner, cont, other, acc])
    await db_session.commit()
    for u in (owner, cont, other):
        await db_session.refresh(u)
    await db_session.refresh(acc)

    db_session.add(
        Participation(user_id=owner.id, account_id=acc.id, pct=1, valid_from=date(2020, 1, 1))
    )
    db_session.add(
        DataAccessGrant(
            grantor_user_id=owner.id, grantee_user_id=cont.id, valid_from=date(2020, 1, 1)
        )
    )
    await db_session.commit()
    return owner, cont, other, acc


async def test_owner_sees_own_accounts(db_session):
    owner, _, _, acc = await _seed(db_session)
    assert await visible_account_ids(db_session, owner.id) == {acc.id}


async def test_contador_with_grant_sees_owner_accounts(db_session):
    owner, cont, _, acc = await _seed(db_session)
    got = await visible_account_ids(db_session, cont.id, on_behalf_of=owner.id)
    assert got == {acc.id}


async def test_contador_without_grant_is_403(db_session):
    owner, _, other, acc = await _seed(db_session)
    with pytest.raises(GrantRequiredError):
        await visible_account_ids(db_session, other.id, on_behalf_of=owner.id)


async def test_contador_omitting_context_sees_empty(db_session):
    _, cont, _, _ = await _seed(db_session)
    assert await visible_account_ids(db_session, cont.id) == set()


async def test_expired_grant_is_403(db_session):
    owner, cont, _, _ = await _seed(db_session)
    with pytest.raises(GrantRequiredError):
        await visible_account_ids(db_session, cont.id, on_behalf_of=owner.id, at=date(2019, 1, 1))


async def test_grant_expired_via_valid_to_is_403(db_session):
    from datetime import date

    from ibkr_control.db.models.grants import DataAccessGrant

    owner, cont, _, acc = await _seed(db_session)
    # Cerrar el grant existente en el pasado (valid_to < hoy)
    g = await db_session.get(DataAccessGrant, (owner.id, cont.id, date(2020, 1, 1)))
    g.valid_to = date(2021, 1, 1)
    await db_session.commit()
    with pytest.raises(GrantRequiredError):
        await visible_account_ids(db_session, cont.id, on_behalf_of=owner.id, at=date(2022, 6, 1))
