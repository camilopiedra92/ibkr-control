"""E2E del contador (SP2): switch cross-org explícito + 3 barreras + revocación.

Seeding como OWNER (cross-tenant); todo el ejercicio va por HTTP con el client
real (app conectada como app_rls — la suite hereda la parity RLS)."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest.fixture
async def grantee_world(client, auth_headers_with_org, owner_engine):
    """Household (auth_headers_with_org) + contador con org firm + grant vigente
    + un restatement en una cuenta del party y otro en una cuenta ajena.

    Devuelve dict: cpa_headers, client_org_id, party_account_id, other_account_id,
    grant_id (creado via API por el owner — ejercita el CRUD real)."""
    from ibkr_control.auth.models import User
    from ibkr_control.db.models.accounts import Account
    from ibkr_control.db.models.flex_raw import FlexImport
    from ibkr_control.db.models.memberships import Membership
    from ibkr_control.db.models.organizations import Organization
    from ibkr_control.db.models.participations import Participation
    from ibkr_control.db.models.parties import Party
    from ibkr_control.db.models.restatements import RestatementLog

    # Contador: user + org firm propia.
    await client.post(
        "/api/auth/register",
        json={"email": "cpa@firm.com", "password": "supersecret123", "name": "CPA"},
    )
    maker = async_sessionmaker(owner_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        cpa = await s.scalar(select(User).where(User.email == "cpa@firm.com"))
        firm = Organization(type="firm", name="CPA Firm")
        s.add(firm)
        await s.flush()
        s.add(Membership(user_id=cpa.id, organization_id=firm.id, role="owner"))
        org = await s.scalar(select(Organization).where(Organization.name == "Org Owner Household"))
        party = await s.scalar(select(Party).where(Party.organization_id == org.id))
        # Cuentas + participación del party SOLO en la primera + restatements.
        # set_config es inerte aquí (owner_engine = superuser, bypassa RLS aun
        # bajo FORCE) — se deja solo como documentación de intención de scope.
        await s.execute(text("SELECT set_config('app.current_org', :o, true)"), {"o": str(org.id)})
        acc1 = Account(
            ibkr_account_id="U10000001", organization_id=org.id, alias="a1", currency="USD"
        )
        acc2 = Account(
            ibkr_account_id="U10000002", organization_id=org.id, alias="a2", currency="USD"
        )
        s.add_all([acc1, acc2])
        await s.flush()
        s.add(
            Participation(
                party_id=party.id,
                account_id=acc1.id,
                organization_id=org.id,
                pct=Decimal("1.0"),
                valid_from=date(2024, 1, 1),
            )
        )
        fi = FlexImport(
            organization_id=org.id,
            anyo=2025,
            xml_hash="e2e-hash",
            xml_size_bytes=1,
            xml_bytes=b"<x/>",
            source="manual_upload",
            period_covered_from=date(2025, 1, 1),
            period_covered_to=date(2025, 12, 31),
            year_status="sealed",
            status="ok",
        )
        s.add(fi)
        await s.flush()
        for acc in (acc1, acc2):
            s.add(
                RestatementLog(
                    organization_id=org.id,
                    flex_import_id=fi.id,
                    account_id=acc.id,
                    table_name="closed_lots",
                    natural_key={"account_id": acc.id},
                    column_name="*",
                    old_value="1",
                    new_value="2",
                    kind="sibling_row",
                )
            )
        await s.commit()
        org_id, party_id, a1, a2 = org.id, party.id, acc1.id, acc2.id

    # El OWNER crea el grant via API (write-path real).
    r = await client.post(
        "/api/grants",
        headers=auth_headers_with_org,
        json={"grantor_party_id": party_id, "grantee_user_id": cpa.id},
    )
    assert r.status_code == 201, r.text

    login = await client.post(
        "/api/auth/jwt/login",
        data={"username": "cpa@firm.com", "password": "supersecret123"},
    )
    cpa_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    return {
        "cpa_headers": cpa_headers,
        "client_org_id": org_id,
        "party_account_id": a1,
        "other_account_id": a2,
        "grant_id": r.json()["id"],
        "owner_headers": auth_headers_with_org,
    }


async def test_grantee_reads_party_scoped_restatements(client, grantee_world):
    w = grantee_world
    headers = {**w["cpa_headers"], "X-Organization-Id": str(w["client_org_id"])}
    r = await client.get("/api/ingest/restatements", headers=headers)
    assert r.status_code == 200, r.text
    accounts = {row["account_id"] for row in r.json()}
    assert accounts == {w["party_account_id"]}  # la cuenta ajena al party NO


async def test_grantee_without_header_stays_in_own_org(client, grantee_world):
    """Sin header, el contador resuelve SU org (firm) — no el del cliente."""
    r = await client.get("/api/ingest/restatements", headers=grantee_world["cpa_headers"])
    assert r.status_code == 200
    assert r.json() == []  # su firm no tiene restatements


async def test_grantee_denied_admin_plane_and_writes(client, grantee_world):
    w = grantee_world
    headers = {**w["cpa_headers"], "X-Organization-Id": str(w["client_org_id"])}
    r = await client.get("/api/connections", headers=headers)  # ops:read
    assert r.status_code == 403
    assert r.json()["detail"] == "INSUFFICIENT_SCOPE"
    r = await client.post(  # connections:write
        "/api/connections",
        headers=headers,
        json={"display_name": "x", "query_id": "1", "token": "tok-abcdefghij"},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "INSUFFICIENT_SCOPE"


async def test_revocation_is_immediate(client, grantee_world):
    w = grantee_world
    r = await client.post(f"/api/grants/{w['grant_id']}/revoke", headers=w["owner_headers"])
    assert r.status_code == 200
    headers = {**w["cpa_headers"], "X-Organization-Id": str(w["client_org_id"])}
    r = await client.get("/api/ingest/restatements", headers=headers)
    assert r.status_code == 403
    assert r.json()["detail"] == "NO_ORG_ACCESS"


async def test_grantee_lists_received_grants_from_own_org(client, grantee_world):
    r = await client.get("/api/grants", headers=grantee_world["cpa_headers"])
    assert r.status_code == 200
    [g] = r.json()
    assert g["direction"] == "received"


async def test_grantee_cannot_enumerate_other_grants_of_client_org(
    client, grantee_world, owner_engine
):
    """Leak (review holístico): CPA1 entrando al org del cliente con el header
    que legítimamente usa para restatements NO debe ver los grants que el
    cliente otorgó a OTROS contadores — solo los dirigidos a él (SP2-D5:
    'el grantee lista sus grants desde su propio org')."""
    from ibkr_control.auth.models import User
    from ibkr_control.db.models.parties import Party

    w = grantee_world
    # Segundo contador independiente, mismo party como grantor.
    await client.post(
        "/api/auth/register",
        json={"email": "cpa2@firm.com", "password": "supersecret123", "name": "CPA 2"},
    )
    maker = async_sessionmaker(owner_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        cpa2 = await s.scalar(select(User).where(User.email == "cpa2@firm.com"))
        party = await s.scalar(select(Party).where(Party.organization_id == w["client_org_id"]))
        cpa2_id, party_id = cpa2.id, party.id
    r = await client.post(
        "/api/grants",
        headers=w["owner_headers"],
        json={"grantor_party_id": party_id, "grantee_user_id": cpa2_id},
    )
    assert r.status_code == 201, r.text

    headers = {**w["cpa_headers"], "X-Organization-Id": str(w["client_org_id"])}
    r = await client.get("/api/grants", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert {g["id"] for g in body} == {w["grant_id"]}  # SOLO el suyo
    assert all(g["grantee_user_id"] != cpa2_id for g in body)  # cero leak de cpa2
