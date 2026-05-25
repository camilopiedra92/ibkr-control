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
        xml_size_bytes=100, xml_bytes=b"sample content",
        source='manual_upload',
        period_covered_from=date(2025, 1, 1), period_covered_to=date(2025, 12, 31),
        status='ok',
    ))
    await db_session.commit()
    assert await is_known_hash(db_session, h) is True


def test_xml_hash_empty_bytes_pinned():
    """SHA-256 of empty bytes is the well-known constant. Pin it to prevent
    accidental hash function changes."""
    assert xml_hash(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


# ---------------------------------------------------------------------------
# check_hash_status — new per-user API (R2 fast-path + R6 isolation)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_hash_status_absent_when_hash_not_in_db(db_session: AsyncSession, sample_user):
    from ibkr_control.ingest.hash_dedup import check_hash_status
    status = await check_hash_status(db_session, sample_user.id, "abc123")
    assert status == "absent"


@pytest.mark.asyncio
async def test_check_hash_status_ok_when_hash_exists_for_user(db_session: AsyncSession, sample_user):
    from ibkr_control.db.models.flex_raw import FlexImport
    from ibkr_control.ingest.hash_dedup import check_hash_status, xml_hash

    h = xml_hash(b"<xml/>")
    db_session.add(FlexImport(
        user_id=sample_user.id, anyo=2026, xml_hash=h,
        xml_size_bytes=6, xml_bytes=b"<xml/>",
        source='web_service', year_status='rolling',
        period_covered_from=date(2026, 1, 1),
        period_covered_to=date(2026, 12, 31),
        status='ok',
    ))
    await db_session.commit()

    status = await check_hash_status(db_session, sample_user.id, h)
    assert status == "ok"


@pytest.mark.asyncio
async def test_check_hash_status_poison_when_hash_marked_poison(db_session: AsyncSession, sample_user):
    from ibkr_control.db.models.flex_raw import FlexImport
    from ibkr_control.ingest.hash_dedup import check_hash_status, xml_hash

    h = xml_hash(b"<xml-poison/>")
    db_session.add(FlexImport(
        user_id=sample_user.id, anyo=2026, xml_hash=h,
        xml_size_bytes=13, xml_bytes=b"<xml-poison/>",
        source='web_service', year_status='rolling',
        period_covered_from=date(2026, 1, 1),
        period_covered_to=date(2026, 12, 31),
        status='poison', poison_reason='parser crash',
    ))
    await db_session.commit()

    status = await check_hash_status(db_session, sample_user.id, h)
    assert status == "poison"


@pytest.mark.asyncio
async def test_check_hash_status_absent_when_hash_exists_for_different_user(
    db_session: AsyncSession, sample_user, second_sample_user,
):
    """R6: hash visible para user A no debe ser visible para user B."""
    from ibkr_control.db.models.flex_raw import FlexImport
    from ibkr_control.ingest.hash_dedup import check_hash_status, xml_hash

    h = xml_hash(b"<xml-user-a/>")
    db_session.add(FlexImport(
        user_id=sample_user.id, anyo=2026, xml_hash=h,
        xml_size_bytes=13, xml_bytes=b"<xml-user-a/>",
        source='web_service', year_status='rolling',
        period_covered_from=date(2026, 1, 1),
        period_covered_to=date(2026, 12, 31),
        status='ok',
    ))
    await db_session.commit()

    # User B no debe ver el hash de User A
    status_b = await check_hash_status(db_session, second_sample_user.id, h)
    assert status_b == "absent"

    # User A sí lo ve
    status_a = await check_hash_status(db_session, sample_user.id, h)
    assert status_a == "ok"
