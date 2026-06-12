"""Matriz de resolve_authz (SP2-D3): member/grantee/none × org × vigencia."""

from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from ibkr_control.authz.resolver import resolve_authz
from ibkr_control.db.models.access_grants import AccessGrant


async def _mk_user(owner_session, email, *, org=None, role="member"):
    from ibkr_control.auth.models import User
    from ibkr_control.db.models.memberships import Membership

    u = User(email=email, hashed_password="x", is_active=True, name=email)
    owner_session.add(u)
    await owner_session.flush()
    if org is not None:
        owner_session.add(Membership(user_id=u.id, organization_id=org.id, role=role))
    await owner_session.commit()
    await owner_session.refresh(u)
    return u


async def _mk_org(owner_session, name, type_="firm"):
    from ibkr_control.db.models.organizations import Organization

    org = Organization(type=type_, name=name)
    owner_session.add(org)
    await owner_session.commit()
    await owner_session.refresh(org)
    return org


async def test_single_membership_autoresolves(db_session, sample_org, sample_user):
    ctx = await resolve_authz(db_session, user_id=sample_user.id, requested_org_id=None)
    assert (ctx.org_id, ctx.actor, ctx.role, ctx.party_ids) == (
        sample_org.id,
        "member",
        "owner",
        None,
    )


async def test_multi_membership_without_header_requires_selection(
    db_session, sample_org, sample_user, owner_session
):
    org_b = await _mk_org(owner_session, "Org B", "personal")
    from ibkr_control.db.models.memberships import Membership

    owner_session.add(Membership(user_id=sample_user.id, organization_id=org_b.id, role="member"))
    await owner_session.commit()
    with pytest.raises(HTTPException) as exc:
        await resolve_authz(db_session, user_id=sample_user.id, requested_org_id=None)
    assert (exc.value.status_code, exc.value.detail) == (400, "ORG_SELECTION_REQUIRED")


async def test_no_memberships_no_header_403(db_session, owner_session):
    u = await _mk_user(owner_session, "nobody@t.com")
    with pytest.raises(HTTPException) as exc:
        await resolve_authz(db_session, user_id=u.id, requested_org_id=None)
    assert (exc.value.status_code, exc.value.detail) == (403, "NO_ORG_MEMBERSHIP")


async def test_requested_own_org_resolves_member_role(db_session, sample_org, sample_user):
    ctx = await resolve_authz(db_session, user_id=sample_user.id, requested_org_id=sample_org.id)
    assert (ctx.actor, ctx.role) == ("member", "owner")


async def test_requested_org_without_access_403(db_session, sample_user, owner_session):
    other = await _mk_org(owner_session, "Ajena", "personal")
    with pytest.raises(HTTPException) as exc:
        await resolve_authz(db_session, user_id=sample_user.id, requested_org_id=other.id)
    assert (exc.value.status_code, exc.value.detail) == (403, "NO_ORG_ACCESS")


async def test_valid_grant_resolves_grantee_context(
    db_session, sample_org, sample_party, owner_session
):
    cpa = await _mk_user(owner_session, "cpa@t.com")
    db_session.add(
        AccessGrant(
            grantor_party_id=sample_party.id,
            grantee_user_id=cpa.id,
            organization_id=sample_org.id,
            valid_from=date.today() - timedelta(days=1),
        )
    )
    await db_session.commit()
    ctx = await resolve_authz(db_session, user_id=cpa.id, requested_org_id=sample_org.id)
    assert (ctx.actor, ctx.role) == ("grantee", "read_only")
    assert ctx.party_ids == frozenset({sample_party.id})


async def test_grant_not_yet_valid_is_403(db_session, sample_org, sample_party, owner_session):
    cpa = await _mk_user(owner_session, "cpa2@t.com")
    db_session.add(
        AccessGrant(
            grantor_party_id=sample_party.id,
            grantee_user_id=cpa.id,
            organization_id=sample_org.id,
            valid_from=date.today() + timedelta(days=1),  # futuro
        )
    )
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        await resolve_authz(db_session, user_id=cpa.id, requested_org_id=sample_org.id)
    assert exc.value.detail == "NO_ORG_ACCESS"


async def test_grant_never_requires_explicit_org(
    db_session, sample_org, sample_party, owner_session
):
    """User sin memberships + un grant: SIN header -> NO_ORG_MEMBERSHIP (el
    contexto cross-org jamas se adivina, SP2-D3 punto 3)."""
    cpa = await _mk_user(owner_session, "cpa3@t.com")
    db_session.add(
        AccessGrant(
            grantor_party_id=sample_party.id,
            grantee_user_id=cpa.id,
            organization_id=sample_org.id,
            valid_from=date.today() - timedelta(days=1),
        )
    )
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        await resolve_authz(db_session, user_id=cpa.id, requested_org_id=None)
    assert exc.value.detail == "NO_ORG_MEMBERSHIP"


async def test_membership_wins_over_grant_in_same_org(
    db_session, sample_org, sample_party, sample_user
):
    """Si sos member del org solicitado, sos member (el grant no degrada)."""
    db_session.add(
        AccessGrant(
            grantor_party_id=sample_party.id,
            grantee_user_id=sample_user.id,
            organization_id=sample_org.id,
            valid_from=date.today() - timedelta(days=1),
        )
    )
    await db_session.commit()
    ctx = await resolve_authz(db_session, user_id=sample_user.id, requested_org_id=sample_org.id)
    assert ctx.actor == "member"
