"""Tests de SHA-256 dedup helper."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date

from ibkr_control.ingest.hash_dedup import xml_hash


def test_xml_hash_deterministic():
    assert xml_hash(b"hello") == xml_hash(b"hello")


def test_xml_hash_different_inputs():
    assert xml_hash(b"hello") != xml_hash(b"world")


def test_xml_hash_is_64_hex_chars():
    h = xml_hash(b"hello")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_xml_hash_empty_bytes_pinned():
    """SHA-256 of empty bytes is the well-known constant. Pin it to prevent
    accidental hash function changes."""
    assert xml_hash(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


# ---------------------------------------------------------------------------
# check_hash_status — per-ORG API (R2 fast-path + SP1 tenant isolation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_hash_status_absent_when_hash_not_in_db(db_session: AsyncSession, sample_org):
    from ibkr_control.ingest.hash_dedup import check_hash_status

    status = await check_hash_status(db_session, sample_org.id, "abc123")
    assert status == "absent"


@pytest.mark.asyncio
async def test_check_hash_status_ok_when_hash_exists_for_org(
    db_session: AsyncSession, sample_org, sample_user
):
    from ibkr_control.db.models.flex_raw import FlexImport
    from ibkr_control.ingest.hash_dedup import check_hash_status, xml_hash

    h = xml_hash(b"<xml/>")
    db_session.add(
        FlexImport(
            organization_id=sample_org.id,
            anyo=2026,
            xml_hash=h,
            xml_size_bytes=6,
            xml_bytes=b"<xml/>",
            source="web_service",
            year_status="rolling",
            period_covered_from=date(2026, 1, 1),
            period_covered_to=date(2026, 12, 31),
            status="ok",
        )
    )
    await db_session.commit()

    status = await check_hash_status(db_session, sample_org.id, h)
    assert status == "ok"


@pytest.mark.asyncio
async def test_check_hash_status_poison_when_hash_marked_poison(
    db_session: AsyncSession, sample_org, sample_user
):
    from ibkr_control.db.models.flex_raw import FlexImport
    from ibkr_control.ingest.hash_dedup import check_hash_status, xml_hash

    h = xml_hash(b"<xml-poison/>")
    db_session.add(
        FlexImport(
            organization_id=sample_org.id,
            anyo=2026,
            xml_hash=h,
            xml_size_bytes=13,
            xml_bytes=b"<xml-poison/>",
            source="web_service",
            year_status="rolling",
            period_covered_from=date(2026, 1, 1),
            period_covered_to=date(2026, 12, 31),
            status="poison",
            poison_reason="parser crash",
        )
    )
    await db_session.commit()

    status = await check_hash_status(db_session, sample_org.id, h)
    assert status == "poison"


@pytest.mark.asyncio
async def test_check_hash_status_absent_when_hash_exists_for_different_org(
    owner_session: AsyncSession,
    sample_org,
    sample_user,
    second_sample_user,
):
    """SP1 tenant isolation: hash de org A no debe ser visible para org B.

    second_sample_user founds its own org (Membership owner); we read its
    organization_id from the membership to scope the cross-tenant check.

    Inherentemente cross-tenant (escribe y consulta dos orgs) → usa
    ``owner_session`` (bypass RLS).
    """
    from sqlalchemy import select

    from ibkr_control.db.models.flex_raw import FlexImport
    from ibkr_control.db.models.memberships import Membership
    from ibkr_control.ingest.hash_dedup import check_hash_status, xml_hash

    org_b_id = await owner_session.scalar(
        select(Membership.organization_id).where(Membership.user_id == second_sample_user.id)
    )

    h = xml_hash(b"<xml-org-a/>")
    owner_session.add(
        FlexImport(
            organization_id=sample_org.id,
            anyo=2026,
            xml_hash=h,
            xml_size_bytes=13,
            xml_bytes=b"<xml-org-a/>",
            source="web_service",
            year_status="rolling",
            period_covered_from=date(2026, 1, 1),
            period_covered_to=date(2026, 12, 31),
            status="ok",
        )
    )
    await owner_session.commit()

    # Org B no debe ver el hash de Org A
    status_b = await check_hash_status(owner_session, org_b_id, h)
    assert status_b == "absent"

    # Org A sí lo ve
    status_a = await check_hash_status(owner_session, sample_org.id, h)
    assert status_a == "ok"
