"""Tests del advisory lock por (source, scope_id)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.ingest.lock import advisory_lock, LockHeldError


def test_advisory_lock_key_is_deterministic_across_processes():
    """The lock key must be the same for the same (source, scope_id) regardless of process.

    Python's built-in hash() uses PYTHONHASHSEED randomization which would produce
    different keys per process. This test pins the value to confirm we don't regress.
    """
    from ibkr_control.ingest.lock import _lock_key

    # Same inputs always give same output (deterministic)
    assert _lock_key("flex", 1) == _lock_key("flex", 1)
    # Pin specific values: zlib.crc32 of "flex:1" and "trm:None"
    import zlib

    assert _lock_key("flex", 1) == zlib.crc32(b"flex:1")
    assert _lock_key("trm", None) == zlib.crc32(b"trm:None")
    # Different inputs give different output
    assert _lock_key("flex", 1) != _lock_key("flex", 2)
    assert _lock_key("flex", 1) != _lock_key("trm", 1)


@pytest.mark.asyncio
async def test_advisory_lock_acquires_and_releases(db_session: AsyncSession):
    async with advisory_lock(db_session, scope_id=1, source="flex"):
        pass  # se libera en __aexit__


@pytest.mark.asyncio
async def test_advisory_lock_releases_on_exception(db_session: AsyncSession):
    """Si el block lanza, el lock se libera y se puede re-acquirir."""
    with pytest.raises(ValueError):
        async with advisory_lock(db_session, scope_id=2, source="flex"):
            raise ValueError("boom")

    # Re-acquire en la misma session debe funcionar
    async with advisory_lock(db_session, scope_id=2, source="flex"):
        pass


@pytest.mark.asyncio
async def test_advisory_lock_blocks_concurrent(db_engine):
    """Dos sessions distintas pidiendo el mismo lock — la segunda recibe LockHeldError."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    SessionLocal = async_sessionmaker(db_engine, expire_on_commit=False)

    async with SessionLocal() as s1, SessionLocal() as s2:
        async with advisory_lock(s1, scope_id=99, source="flex"):
            with pytest.raises(LockHeldError) as exc_info:
                async with advisory_lock(s2, scope_id=99, source="flex"):
                    pass
            assert exc_info.value.source == "flex"
            assert exc_info.value.scope_id == 99


@pytest.mark.asyncio
async def test_advisory_lock_different_sources_independent(db_engine):
    """flex y trm tienen locks distintos para el mismo user."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    SessionLocal = async_sessionmaker(db_engine, expire_on_commit=False)

    async with SessionLocal() as s1, SessionLocal() as s2:
        async with advisory_lock(s1, scope_id=100, source="flex"):
            async with advisory_lock(s2, scope_id=100, source="trm"):
                pass  # ambos OK
