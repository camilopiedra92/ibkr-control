"""Tests del aislamiento multi-tenant (R6 + SP1).

El dedup de flex_imports es per-ORG: UNIQUE(organization_id, xml_hash). El
advisory lock de flex es per-org (scope_id = organization_id). El mismo XML
ingerido por dos orgs distintos produce dos FlexImport rows separados.
"""

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ibkr_control.db.models.flex_raw import FlexImport
from ibkr_control.db.models.memberships import Membership
from ibkr_control.ingest.flex import persister as flex_persister_mod
from ibkr_control.ingest.flex.parser import parse
from ibkr_control.ingest.lock import LockHeldError, advisory_lock


FIXTURE_XML = Path(__file__).parent / "fixtures" / "xml" / "ACTIVITY_2025_sanitized.xml"


async def _org_id_of(session, user) -> int:
    """Read the founding org id of a user from its owner membership."""
    return await session.scalar(
        select(Membership.organization_id).where(Membership.user_id == user.id)
    )


@pytest.mark.asyncio
async def test_persister_isolation_two_orgs_sequential(
    db_engine,
    db_session,
    sample_org,
    second_sample_user,
):
    """SP1: persisting the same XML for 2 orgs creates 2 distinct FlexImport rows.

    Note: runs sequentially (not asyncio.gather) because accounts.ibkr_account_id
    has a global UNIQUE constraint (shared broker identity). The semantically
    important assertion is that the same xml_hash produces two separate
    FlexImport rows scoped to different organization_ids, which is what the
    UNIQUE(organization_id, xml_hash) constraint enables.
    """
    xml_bytes = FIXTURE_XML.read_bytes()
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)

    org_b_id = await _org_id_of(db_session, second_sample_user)

    async def persist_for(organization_id: int) -> int:
        async with session_factory() as session:
            parsed = parse(xml_bytes)
            flex_import_id, _ = await flex_persister_mod.persist(
                session,
                parsed=parsed,
                organization_id=organization_id,
                xml_bytes=xml_bytes,
                source="web_service",
            )
            await session.commit()
            return flex_import_id

    # Sequential: accounts table has global UNIQUE on ibkr_account_id;
    # parallel inserts on the same XML race before either commit and collide.
    id_a = await persist_for(sample_org.id)
    id_b = await persist_for(org_b_id)

    assert id_a != id_b

    # Verify both rows exist with the right organization_ids
    async with session_factory() as session:
        rows = (await session.scalars(select(FlexImport))).all()
        per_org_ids = {r.organization_id for r in rows}
        assert sample_org.id in per_org_ids
        assert org_b_id in per_org_ids


@pytest.mark.asyncio
async def test_advisory_lock_is_per_org(
    db_engine,
    db_session,
    sample_org,
    second_sample_user,
):
    """SP1: Org A holds (source='flex', scope_id=A) lock -> Org B acquires
    (source='flex', scope_id=B) without conflict."""
    org_b_id = await _org_id_of(db_session, second_sample_user)
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session_a, session_factory() as session_b:
        async with advisory_lock(session_a, scope_id=sample_org.id, source="flex"):
            # Org B should be able to acquire ITS OWN lock
            try:
                async with advisory_lock(session_b, scope_id=org_b_id, source="flex"):
                    pass  # success
            except LockHeldError:
                pytest.fail("Advisory lock leaked across orgs")


@pytest.mark.asyncio
async def test_advisory_lock_same_org_two_sessions_conflict(
    db_engine,
    sample_org,
):
    """SP1 sanity: Org A holds lock -> Org A second session cannot acquire."""
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session_a, session_factory() as session_b:
        async with advisory_lock(session_a, scope_id=sample_org.id, source="flex"):
            with pytest.raises(LockHeldError):
                async with advisory_lock(session_b, scope_id=sample_org.id, source="flex"):
                    pass


@pytest.mark.asyncio
async def test_same_xml_hash_two_orgs_no_unique_collision(
    db_engine,
    db_session,
    sample_org,
    second_sample_user,
):
    """SP1: UNIQUE(organization_id, xml_hash) allows the same hash for two orgs."""
    xml_bytes = FIXTURE_XML.read_bytes()
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)

    org_b_id = await _org_id_of(db_session, second_sample_user)

    # Org A persist
    async with session_factory() as session:
        parsed = parse(xml_bytes)
        await flex_persister_mod.persist(
            session,
            parsed=parsed,
            organization_id=sample_org.id,
            xml_bytes=xml_bytes,
            source="web_service",
        )
        await session.commit()

    # Org B persist same XML -- should NOT raise UniqueViolation
    async with session_factory() as session:
        parsed = parse(xml_bytes)
        await flex_persister_mod.persist(
            session,
            parsed=parsed,
            organization_id=org_b_id,
            xml_bytes=xml_bytes,
            source="web_service",
        )
        await session.commit()

    # Verify both rows present
    async with session_factory() as session:
        rows = (await session.scalars(select(FlexImport.organization_id))).all()
        assert set(rows) >= {sample_org.id, org_b_id}
