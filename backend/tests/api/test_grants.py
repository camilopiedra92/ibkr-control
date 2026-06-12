"""CRUD /api/grants (SP2-D7): owner-only write, revoke=valid_to, direcciones."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def _seed_ids(owner_engine):
    """Devuelve (org_id, party_id) del org del fixture auth_headers_with_org."""
    from ibkr_control.db.models.organizations import Organization
    from ibkr_control.db.models.parties import Party

    maker = async_sessionmaker(owner_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        org = await s.scalar(select(Organization).where(Organization.name == "Org Owner Household"))
        party = await s.scalar(select(Party).where(Party.organization_id == org.id))
        return org.id, party.id


async def _mk_firm(owner_engine, name="Estudio X"):
    from ibkr_control.db.models.organizations import Organization

    maker = async_sessionmaker(owner_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        firm = Organization(type="firm", name=name)
        s.add(firm)
        await s.commit()
        await s.refresh(firm)
        return firm.id


async def test_owner_creates_and_lists_grant(client, auth_headers_with_org, owner_engine):
    org_id, party_id = await _seed_ids(owner_engine)
    firm_id = await _mk_firm(owner_engine)
    r = await client.post(
        "/api/grants",
        headers=auth_headers_with_org,
        json={"grantor_party_id": party_id, "grantee_organization_id": firm_id},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["role"] == "read_only"
    assert body["valid_to"] is None

    r = await client.get("/api/grants", headers=auth_headers_with_org)
    assert r.status_code == 200
    [g] = r.json()
    assert g["direction"] == "granted"


async def test_revoke_same_day_is_legal_and_immediate(client, auth_headers_with_org, owner_engine):
    """SP2-D7/D8: revoke = valid_to hoy (half-open => inactivo ya), mismo dia OK."""
    _, party_id = await _seed_ids(owner_engine)
    firm_id = await _mk_firm(owner_engine, "Estudio Y")
    r = await client.post(
        "/api/grants",
        headers=auth_headers_with_org,
        json={"grantor_party_id": party_id, "grantee_organization_id": firm_id},
    )
    gid = r.json()["id"]
    r = await client.post(f"/api/grants/{gid}/revoke", headers=auth_headers_with_org)
    assert r.status_code == 200
    assert r.json()["valid_to"] is not None
    # Doble revoke => 409 (ya inactivo).
    r = await client.post(f"/api/grants/{gid}/revoke", headers=auth_headers_with_org)
    assert r.status_code == 409


async def test_foreign_party_404(
    client, auth_headers_with_org, second_auth_headers_with_org, owner_engine
):
    """El grantor party de OTRO org no se encuentra (RLS) => 404 sin leak."""
    from ibkr_control.db.models.organizations import Organization
    from ibkr_control.db.models.parties import Party

    maker = async_sessionmaker(owner_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        other_org = await s.scalar(
            select(Organization).where(Organization.name == "Org Owner 2 Household")
        )
        other_party = await s.scalar(select(Party).where(Party.organization_id == other_org.id))
    firm_id = await _mk_firm(owner_engine, "Estudio Z")
    r = await client.post(
        "/api/grants",
        headers=auth_headers_with_org,
        json={"grantor_party_id": other_party.id, "grantee_organization_id": firm_id},
    )
    assert r.status_code == 404


async def test_self_grant_422(client, auth_headers_with_org, owner_engine):
    org_id, party_id = await _seed_ids(owner_engine)
    r = await client.post(
        "/api/grants",
        headers=auth_headers_with_org,
        json={"grantor_party_id": party_id, "grantee_organization_id": org_id},
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "SELF_GRANT"
