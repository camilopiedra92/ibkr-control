"""Tests de SHA-256 dedup helper."""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date

from ibkr_control.ingest.hash_dedup import xml_hash, is_known_hash


def test_xml_hash_deterministic():
    assert xml_hash(b"hello") == xml_hash(b"hello")


def test_xml_hash_different_inputs():
    assert xml_hash(b"hello") != xml_hash(b"world")


def test_xml_hash_is_64_hex_chars():
    h = xml_hash(b"hello")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


@pytest.mark.asyncio
async def test_is_known_hash_false_when_empty(db_session: AsyncSession):
    assert await is_known_hash(db_session, "deadbeef" * 8) is False


@pytest.mark.asyncio
async def test_is_known_hash_true_when_exists(db_session: AsyncSession, sample_user):
    from ibkr_control.db.models.flex_raw import FlexImport
    h = xml_hash(b"sample content")
    db_session.add(FlexImport(
        user_id=sample_user.id, anyo=2025, xml_hash=h,
        xml_size_bytes=100, source='manual_upload',
        period_covered_from=date(2025, 1, 1), period_covered_to=date(2025, 12, 31),
        status='ok',
    ))
    await db_session.commit()
    assert await is_known_hash(db_session, h) is True
