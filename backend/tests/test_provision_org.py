"""Tests for the provision_org bootstrap script.

Uses ``owner_session`` (bypass RLS): provisioning the FIRST tenant writes a
``parties`` row (an RLS table) for an org that did not exist before the same
transaction — a chicken-and-egg bootstrap with no pre-existing ``app.current_org``
context to set. Tenant bootstrap is a control-plane action, so it routes to the
named owner exception, like the other cross-tenant/control-plane seeding.
"""

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_provision_org_creates_org_user_party_membership(owner_session):
    from ibkr_control.scripts.provision_org import provision_org
    from ibkr_control.db.models.organizations import Organization
    from ibkr_control.db.models.memberships import Membership
    from ibkr_control.db.models.parties import Party

    result = await provision_org(
        owner_session,
        org_name="Hogar",
        org_type="personal",
        user_email="owner@t.com",
        user_password="supersecret123",
        party_name="Owner",
    )
    org = await owner_session.scalar(select(Organization).where(Organization.id == result.org_id))
    assert org.name == "Hogar"
    m = await owner_session.scalar(select(Membership).where(Membership.organization_id == org.id))
    assert m.role == "owner"
    p = await owner_session.scalar(select(Party).where(Party.organization_id == org.id))
    assert p.user_id == result.user_id


@pytest.mark.asyncio
async def test_provision_org_hashes_password(owner_session):
    """User.hashed_password must not equal the plaintext password."""
    from ibkr_control.scripts.provision_org import provision_org
    from ibkr_control.auth.models import User

    result = await provision_org(
        owner_session,
        org_name="Test Org",
        org_type="personal",
        user_email="hash_check@t.com",
        user_password="plaintext_pass",
        party_name="Tester",
    )
    user = await owner_session.scalar(select(User).where(User.id == result.user_id))
    assert user is not None
    assert user.hashed_password != "plaintext_pass"
    assert len(user.hashed_password) > 20  # bcrypt hash is long


@pytest.mark.asyncio
async def test_provision_org_returns_correct_ids(owner_session):
    """ProvisionResult ids must point to real rows."""
    from ibkr_control.scripts.provision_org import provision_org
    from ibkr_control.auth.models import User
    from ibkr_control.db.models.organizations import Organization
    from ibkr_control.db.models.parties import Party

    result = await provision_org(
        owner_session,
        org_name="Ids Check",
        org_type="personal",
        user_email="ids@t.com",
        user_password="supersecret123",
        party_name="IdsOwner",
    )
    assert result.org_id is not None
    assert result.user_id is not None
    assert result.party_id is not None

    org = await owner_session.scalar(select(Organization).where(Organization.id == result.org_id))
    assert org is not None

    user = await owner_session.scalar(select(User).where(User.id == result.user_id))
    assert user is not None
    assert user.is_active is True

    party = await owner_session.scalar(select(Party).where(Party.id == result.party_id))
    assert party is not None
    assert party.display_name == "IdsOwner"
