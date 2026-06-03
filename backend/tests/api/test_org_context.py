import pytest
from fastapi import HTTPException
from sqlalchemy import text
from ibkr_control.db.rls import apply_org_context


@pytest.mark.asyncio
async def test_apply_org_context_sets_local_guc(db_session):
    await apply_org_context(db_session, org_id=42, user_id=7)
    org = await db_session.scalar(text("SELECT current_setting('app.current_org', true)"))
    usr = await db_session.scalar(text("SELECT current_setting('app.current_user', true)"))
    assert org == "42"
    assert usr == "7"


@pytest.mark.asyncio
async def test_current_org_resolves_single_membership(db_session):
    from ibkr_control.api._context import resolve_current_org_id
    from ibkr_control.auth.models import User
    from ibkr_control.db.models.memberships import Membership
    from ibkr_control.db.models.organizations import Organization

    u = User(email="r@t.com", hashed_password="x", is_active=True, name="R")
    org = Organization(type="personal", name="H")
    db_session.add_all([u, org])
    await db_session.flush()
    db_session.add(Membership(user_id=u.id, organization_id=org.id, role="owner"))
    await db_session.flush()
    got = await resolve_current_org_id(db_session, user_id=u.id, requested_org_id=None)
    assert got == org.id


@pytest.mark.asyncio
async def test_multi_membership_without_requested_org_requires_selection(db_session):
    """D-CONV-3: no silent first-pick. >1 membership + no requested org → 400."""
    from ibkr_control.api._context import resolve_current_org_id
    from ibkr_control.auth.models import User
    from ibkr_control.db.models.memberships import Membership
    from ibkr_control.db.models.organizations import Organization

    u = User(email="multi@t.com", hashed_password="x", is_active=True, name="M")
    org_a = Organization(type="personal", name="A")
    org_b = Organization(type="firm", name="B")
    db_session.add_all([u, org_a, org_b])
    await db_session.flush()
    db_session.add_all(
        [
            Membership(user_id=u.id, organization_id=org_a.id, role="owner"),
            Membership(user_id=u.id, organization_id=org_b.id, role="member"),
        ]
    )
    await db_session.flush()

    with pytest.raises(HTTPException) as exc:
        await resolve_current_org_id(db_session, user_id=u.id, requested_org_id=None)
    assert exc.value.status_code == 400
    assert exc.value.detail == "ORG_SELECTION_REQUIRED"


@pytest.mark.asyncio
async def test_multi_membership_honors_requested_org(db_session):
    """With multiple memberships, an explicit (member-of) requested org resolves."""
    from ibkr_control.api._context import resolve_current_org_id
    from ibkr_control.auth.models import User
    from ibkr_control.db.models.memberships import Membership
    from ibkr_control.db.models.organizations import Organization

    u = User(email="multi2@t.com", hashed_password="x", is_active=True, name="M2")
    org_a = Organization(type="personal", name="A2")
    org_b = Organization(type="firm", name="B2")
    db_session.add_all([u, org_a, org_b])
    await db_session.flush()
    db_session.add_all(
        [
            Membership(user_id=u.id, organization_id=org_a.id, role="owner"),
            Membership(user_id=u.id, organization_id=org_b.id, role="member"),
        ]
    )
    await db_session.flush()

    got = await resolve_current_org_id(db_session, user_id=u.id, requested_org_id=org_b.id)
    assert got == org_b.id


@pytest.mark.asyncio
async def test_no_membership_raises_403(db_session):
    from ibkr_control.api._context import resolve_current_org_id
    from ibkr_control.auth.models import User

    u = User(email="lonely@t.com", hashed_password="x", is_active=True, name="L")
    db_session.add(u)
    await db_session.flush()

    with pytest.raises(HTTPException) as exc:
        await resolve_current_org_id(db_session, user_id=u.id, requested_org_id=None)
    assert exc.value.status_code == 403
    assert exc.value.detail == "NO_ORG_MEMBERSHIP"


@pytest.mark.asyncio
async def test_requested_org_not_a_member_raises_403(db_session):
    """Public surface (SP2): a requested org the user is NOT a member of → 403
    NOT_A_MEMBER. Unreachable via org_context today (it passes None), but the
    resolver's contract must hold."""
    from ibkr_control.api._context import resolve_current_org_id
    from ibkr_control.auth.models import User
    from ibkr_control.db.models.memberships import Membership
    from ibkr_control.db.models.organizations import Organization

    u = User(email="outsider@t.com", hashed_password="x", is_active=True, name="O")
    own = Organization(type="personal", name="Own")
    other = Organization(type="firm", name="Other")
    db_session.add_all([u, own, other])
    await db_session.flush()
    db_session.add(Membership(user_id=u.id, organization_id=own.id, role="owner"))
    await db_session.flush()

    with pytest.raises(HTTPException) as exc:
        await resolve_current_org_id(db_session, user_id=u.id, requested_org_id=other.id)
    assert exc.value.status_code == 403
    assert exc.value.detail == "NOT_A_MEMBER"
