import pytest
from sqlalchemy import select
from ibkr_control.db.models.organizations import Organization

from tests.conftest import scope_session_to_org


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
    await scope_session_to_org(db_session, org.id)
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
    await scope_session_to_org(db_session, org.id)
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
    await scope_session_to_org(db_session, org.id)
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


def test_all_tenant_tables_have_organization_id():
    # Aserción pura sobre Base.metadata (registro de modelos en memoria) — no toca
    # la DB, así que no pide db_session ni es async (no provisiona un clon).
    from ibkr_control.db.base import Base
    from ibkr_control.db.rls import ORG_SCOPED_TABLES

    for table_name in ORG_SCOPED_TABLES:
        table = Base.metadata.tables[table_name]
        assert "organization_id" in table.columns, f"{table_name} missing organization_id"
        assert not table.columns["organization_id"].nullable, (
            f"{table_name}.organization_id nullable"
        )


def test_every_org_scoped_table_is_rls_covered():
    """Guard inverso (HD-1): TODA tabla con organization_id debe tener régimen RLS
    declarado — en ORG_SCOPED_TABLES (loop org_isolation), en el set policied-aparte
    (access_grants con grant_visibility), o marcada info={'rls_exempt': ...} en el
    modelo. Una tabla org-scoped nueva (p.ej. lot_classifications de Phase 3) que se
    olvide de todo esto rompe acá — fail-closed, análogo tabla-nivel del boot guard
    de roles (PR #7). Aserción pura sobre Base.metadata, no toca DB.
    """
    from ibkr_control.db.base import Base
    from ibkr_control.db.rls import ORG_SCOPED_TABLES

    policied = set(ORG_SCOPED_TABLES) | {"access_grants"}
    uncovered = []
    for table in Base.metadata.tables.values():
        if "organization_id" not in table.columns:
            continue
        if table.name in policied:
            continue
        if table.info.get("rls_exempt"):
            continue
        uncovered.append(table.name)

    assert not uncovered, (
        "Tablas con organization_id SIN cobertura RLS declarada — agregalas a "
        "ORG_SCOPED_TABLES, o marcá __table_args__ = (..., {'info': {'rls_exempt': "
        f"'razón'}}) en el modelo: {uncovered}"
    )


@pytest.mark.asyncio
async def test_org_scoped_tables_actually_force_rls(owner_engine):
    """HD-1 capa 2: cada tabla policied (ORG_SCOPED_TABLES + access_grants) tiene
    ENABLE + FORCE RLS + >=1 policy en la DB real. Cierra el caso 'está en la lista
    pero el baseline no le aplicó FORCE'. owner_engine (no app_rls) para leer
    pg_class/pg_policies sin RLS de por medio.
    """
    from sqlalchemy import text
    from ibkr_control.db.rls import ORG_SCOPED_TABLES

    policied = list(ORG_SCOPED_TABLES) + ["access_grants"]
    async with owner_engine.connect() as conn:
        for t in policied:
            flags = (
                await conn.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity "
                        "FROM pg_class WHERE relname = :t"
                    ),
                    {"t": t},
                )
            ).one()
            assert flags.relrowsecurity and flags.relforcerowsecurity, (
                f"{t}: RLS no está ENABLE+FORCE (relrowsecurity={flags.relrowsecurity}, "
                f"relforcerowsecurity={flags.relforcerowsecurity})"
            )
            npol = (
                await conn.execute(
                    text("SELECT count(*) FROM pg_policies WHERE tablename = :t"),
                    {"t": t},
                )
            ).scalar()
            assert npol >= 1, f"{t}: sin ninguna RLS policy"
