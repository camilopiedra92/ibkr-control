import pytest
from sqlalchemy import text
from ibkr_control.api._context import apply_org_context


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
