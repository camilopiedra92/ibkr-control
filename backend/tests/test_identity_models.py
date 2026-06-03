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


@pytest.mark.asyncio
async def test_party_optionally_links_to_user(db_session):
    from ibkr_control.db.models.organizations import Organization
    from ibkr_control.db.models.parties import Party

    org = Organization(type="personal", name="H")
    db_session.add(org)
    await db_session.flush()
    p = Party(organization_id=org.id, display_name="Cónyuge", tax_id="999", user_id=None)
    db_session.add(p)
    await db_session.flush()
    got = await db_session.scalar(select(Party).where(Party.id == p.id))
    assert got.user_id is None
    assert got.display_name == "Cónyuge"
    assert got.organization_id == org.id


@pytest.mark.asyncio
async def test_access_grant_exclusive_grantee_arc(db_session):
    from datetime import date

    from sqlalchemy.exc import IntegrityError

    from ibkr_control.db.models.access_grants import AccessGrant
    from ibkr_control.db.models.organizations import Organization
    from ibkr_control.db.models.parties import Party

    org = Organization(type="personal", name="H")
    firm = Organization(type="firm", name="Estudio")
    db_session.add_all([org, firm])
    await db_session.flush()
    p = Party(organization_id=org.id, display_name="Owner")
    db_session.add(p)
    await db_session.flush()
    db_session.add(
        AccessGrant(
            grantor_party_id=p.id,
            grantee_organization_id=None,
            grantee_user_id=None,
            organization_id=org.id,
            role="read_only",
            valid_from=date(2026, 1, 1),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_participation_is_party_anchored(db_session):
    from datetime import date
    from decimal import Decimal
    from ibkr_control.db.models.accounts import Account
    from ibkr_control.db.models.organizations import Organization
    from ibkr_control.db.models.parties import Party
    from ibkr_control.db.models.participations import Participation

    org = Organization(type="personal", name="H")
    db_session.add(org)
    await db_session.flush()
    party = Party(organization_id=org.id, display_name="Owner")
    acc = Account(ibkr_account_id="U99999001", organization_id=org.id, currency="USD")
    db_session.add_all([party, acc])
    await db_session.flush()
    db_session.add(
        Participation(
            party_id=party.id,
            account_id=acc.id,
            organization_id=org.id,
            pct=Decimal("0.5000"),
            valid_from=date(2026, 1, 1),
            valid_to=None,
        )
    )
    await db_session.flush()
    got = await db_session.scalar(select(Participation).where(Participation.party_id == party.id))
    assert got.pct == Decimal("0.5000")
    assert not hasattr(got, "user_id")


@pytest.mark.asyncio
async def test_flex_credentials_are_org_scoped(db_session):
    from ibkr_control.db.models.flex_credentials import FlexCredentials
    from ibkr_control.db.models.organizations import Organization

    org = Organization(type="personal", name="H")
    db_session.add(org)
    await db_session.flush()
    c = FlexCredentials(organization_id=org.id, token_encrypted=b"x", ytd_query_id="999")
    db_session.add(c)
    await db_session.flush()
    got = await db_session.scalar(select(FlexCredentials).where(FlexCredentials.id == c.id))
    assert got.organization_id == org.id
    assert not hasattr(got, "user_id")
