"""require_scope (PEP, SP2-D2/D5): scope check + header + contexto RLS."""

import pytest

from ibkr_control.authz.scopes import ROLE_SCOPES, SCOPES, require_scope


def test_role_scope_map_matches_spec_table():
    """SP2-D5: la tabla del spec, lockeada como data."""
    assert ROLE_SCOPES["owner"] == SCOPES  # owner: todo
    assert "grants:write" not in ROLE_SCOPES["admin"]
    assert "connections:write" in ROLE_SCOPES["admin"]
    assert ROLE_SCOPES["member"] == frozenset({"ops:read", "data:read", "grants:read"})
    assert ROLE_SCOPES["read_only"] == frozenset({"data:read", "grants:read"})


def test_unknown_scope_fails_loud_at_factory():
    """Un typo en el scope rompe al IMPORT del módulo del endpoint, no en runtime."""
    with pytest.raises(ValueError):
        require_scope("connectons:write")  # typo


async def test_insufficient_scope_403(client, auth_headers_with_org, owner_engine):
    """Un member (no admin) rebota un endpoint connections:write."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ibkr_control.auth.models import User
    from ibkr_control.db.models.memberships import Membership
    from ibkr_control.db.models.organizations import Organization

    # Registrar un segundo user y meterlo como MEMBER en el org del owner.
    await client.post(
        "/api/auth/register",
        json={"email": "plain_member@test.com", "password": "supersecret123", "name": "Member"},
    )
    maker = async_sessionmaker(owner_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        member = await s.scalar(select(User).where(User.email == "plain_member@test.com"))
        org = await s.scalar(select(Organization).where(Organization.name == "Org Owner Household"))
        s.add(Membership(user_id=member.id, organization_id=org.id, role="member"))
        await s.commit()
    login = await client.post(
        "/api/auth/jwt/login",
        data={"username": "plain_member@test.com", "password": "supersecret123"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    r = await client.post(
        "/api/connections",
        headers=headers,
        json={"display_name": "x", "query_id": "1", "token": "tok-abcdefghij"},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "INSUFFICIENT_SCOPE"

    # Pero el mismo member SÍ lee ops:read.
    r = await client.get("/api/connections", headers=headers)
    assert r.status_code == 200


async def test_malformed_org_header_422(client, auth_headers_with_org):
    r = await client.get(
        "/api/connections",
        headers={**auth_headers_with_org, "X-Organization-Id": "abc"},
    )
    assert r.status_code == 422


async def test_org_header_for_foreign_org_403(client, auth_headers_with_org):
    r = await client.get(
        "/api/connections",
        headers={**auth_headers_with_org, "X-Organization-Id": "999999"},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "NO_ORG_ACCESS"
