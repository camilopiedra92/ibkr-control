import pytest
from sqlalchemy import select
from ibkr_control.db.models.organizations import Organization


@pytest.mark.asyncio
async def test_organization_persists_with_type(db_session):
    org = Organization(type="personal", name="Hogar Test")
    db_session.add(org)
    await db_session.flush()
    got = await db_session.scalar(select(Organization).where(Organization.id == org.id))
    assert got.type == "personal"
    assert got.name == "Hogar Test"


@pytest.mark.asyncio
async def test_membership_links_user_to_org(db_session):
    from ibkr_control.auth.models import User
    from ibkr_control.db.models.memberships import Membership
    from ibkr_control.db.models.organizations import Organization

    u = User(email="m@t.com", hashed_password="x", is_active=True, name="M")
    org = Organization(type="personal", name="H")
    db_session.add_all([u, org])
    await db_session.flush()
    db_session.add(Membership(user_id=u.id, organization_id=org.id, role="owner"))
    await db_session.flush()
    got = await db_session.scalar(
        select(Membership).where(Membership.user_id == u.id, Membership.organization_id == org.id)
    )
    assert got.role == "owner"
