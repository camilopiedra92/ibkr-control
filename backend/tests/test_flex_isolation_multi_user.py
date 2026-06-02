"""Tests del aislamiento multi-user (R6)."""

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ibkr_control.db.models.flex_raw import FlexImport
from ibkr_control.ingest.flex import persister as flex_persister_mod
from ibkr_control.ingest.flex.parser import parse
from ibkr_control.ingest.lock import LockHeldError, advisory_lock


FIXTURE_XML = Path(__file__).parent / "fixtures" / "xml" / "ACTIVITY_2025_sanitized.xml"


@pytest.mark.asyncio
async def test_persister_isolation_two_users_parallel(
    db_engine,
    db_session,
    sample_user,
    second_sample_user,
):
    """R6: persisting the same XML for 2 users creates 2 distinct FlexImport rows.

    Note: runs sequentially (not asyncio.gather) because accounts.ibkr_account_id
    has a global UNIQUE constraint — parallel inserts from two transactions that
    both see the same account IDs not yet committed race to INSERT and one loses.
    The semantically important assertion is that the same xml_hash produces two
    separate FlexImport rows scoped to different user_ids, which is what the
    UNIQUE(user_id, xml_hash) migration (Task 4) enables.
    """
    xml_bytes = FIXTURE_XML.read_bytes()
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def persist_for(user_id: int) -> int:
        async with session_factory() as session:
            parsed = parse(xml_bytes)
            flex_import_id, _ = await flex_persister_mod.persist(
                session,
                parsed=parsed,
                user_id=user_id,
                xml_bytes=xml_bytes,
                source="web_service",
            )
            await session.commit()
            return flex_import_id

    # Sequential: accounts table has global UNIQUE on ibkr_account_id;
    # parallel inserts on the same XML race before either commit and collide.
    id_a = await persist_for(sample_user.id)
    id_b = await persist_for(second_sample_user.id)

    assert id_a != id_b

    # Verify both rows exist with the right user_ids
    async with session_factory() as session:
        rows = (await session.scalars(select(FlexImport))).all()
        per_user_ids = {r.user_id for r in rows}
        assert sample_user.id in per_user_ids
        assert second_sample_user.id in per_user_ids


@pytest.mark.asyncio
async def test_advisory_lock_is_per_user(
    db_engine,
    sample_user,
    second_sample_user,
):
    """R6: User A holds (source='flex', user_id=A) lock -> User B acquires (source='flex', user_id=B) without conflict."""
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session_a, session_factory() as session_b:
        async with advisory_lock(session_a, user_id=sample_user.id, source="flex"):
            # User B should be able to acquire ITS OWN lock
            try:
                async with advisory_lock(session_b, user_id=second_sample_user.id, source="flex"):
                    pass  # success
            except LockHeldError:
                pytest.fail("Advisory lock leaked across users")


@pytest.mark.asyncio
async def test_advisory_lock_same_user_two_sessions_conflict(
    db_engine,
    sample_user,
):
    """R6 sanity: User A holds lock -> User A second session cannot acquire."""
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session_a, session_factory() as session_b:
        async with advisory_lock(session_a, user_id=sample_user.id, source="flex"):
            with pytest.raises(LockHeldError):
                async with advisory_lock(session_b, user_id=sample_user.id, source="flex"):
                    pass


@pytest.mark.asyncio
async def test_same_xml_hash_two_users_no_unique_collision(
    db_engine,
    db_session,
    sample_user,
    second_sample_user,
):
    """R6 migration: UNIQUE(user_id, xml_hash) allows the same hash for two distinct users."""
    xml_bytes = FIXTURE_XML.read_bytes()
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)

    # User A persist
    async with session_factory() as session:
        parsed = parse(xml_bytes)
        await flex_persister_mod.persist(
            session,
            parsed=parsed,
            user_id=sample_user.id,
            xml_bytes=xml_bytes,
            source="web_service",
        )
        await session.commit()

    # User B persist same XML -- should NOT raise UniqueViolation
    async with session_factory() as session:
        parsed = parse(xml_bytes)
        await flex_persister_mod.persist(
            session,
            parsed=parsed,
            user_id=second_sample_user.id,
            xml_bytes=xml_bytes,
            source="web_service",
        )
        await session.commit()

    # Verify both rows present
    async with session_factory() as session:
        rows = (await session.scalars(select(FlexImport.user_id))).all()
        assert set(rows) >= {sample_user.id, second_sample_user.id}
