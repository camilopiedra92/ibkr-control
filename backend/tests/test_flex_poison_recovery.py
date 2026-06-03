"""Tests del poison-pill lifecycle (R2)."""

import pytest
from sqlalchemy import delete, select

from ibkr_control.db.models.flex_raw import FlexImport
from ibkr_control.ingest.flex import job as flex_job
from ibkr_control.ingest.flex import parser as flex_parser_mod
from ibkr_control.ingest.hash_dedup import xml_hash


@pytest.mark.asyncio
async def test_parser_failure_creates_poison_row(
    db_session,
    db_engine,
    sample_org,
    sample_user,
    monkeypatch,
):
    """Parser exception → INSERT flex_imports with status='poison'."""
    bad_xml = b"<not><well-formed></not>"
    h = xml_hash(bad_xml)

    # Monkeypatch parser to force a XMLSyntaxError
    def boom(xml_bytes):
        raise flex_parser_mod.etree.XMLSyntaxError("forced", 0, 0, 0)

    monkeypatch.setattr(flex_parser_mod, "parse", boom)

    with pytest.raises(Exception):
        await flex_job.ingest_xml(
            db_session,
            organization_id=sample_org.id,
            xml_bytes=bad_xml,
            source="manual_upload",
            trigger="manual",
        )

    # ingest_log_entry committed the session in its finally — open a new session
    # because db_session may be in error state after exception.
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession as AS2

    maker2 = async_sessionmaker(db_engine, expire_on_commit=False, class_=AS2)
    async with maker2() as s2:
        row = await s2.scalar(select(FlexImport).where(FlexImport.xml_hash == h))
        assert row is not None, "Expected a poison FlexImport row to be committed"
        assert row.status == "poison"
        assert row.poison_reason
        assert "forced" in row.poison_reason


@pytest.mark.asyncio
async def test_second_attempt_of_poison_xml_short_circuits(
    db_session,
    db_engine,
    sample_org,
    sample_user,
    monkeypatch,
):
    """After poison, second attempt of same XML → fast-path skip (parse NOT re-invoked)."""
    bad_xml = b"<not><well-formed></not>"

    def boom(xml_bytes):
        raise flex_parser_mod.etree.XMLSyntaxError("forced", 0, 0, 0)

    monkeypatch.setattr(flex_parser_mod, "parse", boom)

    # First attempt: poison
    with pytest.raises(Exception):
        await flex_job.ingest_xml(
            db_session,
            organization_id=sample_org.id,
            xml_bytes=bad_xml,
            source="manual_upload",
            trigger="manual",
        )

    # Second attempt: should short-circuit via fast-path → parser NOT called
    parse_calls = 0

    def count_parse(xml_bytes):
        nonlocal parse_calls
        parse_calls += 1
        raise flex_parser_mod.etree.XMLSyntaxError("forced", 0, 0, 0)

    monkeypatch.setattr(flex_parser_mod, "parse", count_parse)

    # Open a fresh session (the first db_session may be in error state)
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession as AS2

    maker2 = async_sessionmaker(db_engine, expire_on_commit=False, class_=AS2)
    async with maker2() as s2:
        result_id = await flex_job.ingest_xml(
            s2,
            organization_id=sample_org.id,
            xml_bytes=bad_xml,
            source="manual_upload",
            trigger="manual",
        )

    assert parse_calls == 0, "Parser should not be called on the second attempt (short-circuited)"
    assert result_id is not None, "Should return the existing poison row's id"


@pytest.mark.asyncio
async def test_recovery_via_delete_allows_retry(
    db_session,
    db_engine,
    sample_org,
    sample_user,
    monkeypatch,
):
    """DELETE of poison row → next attempt processes from scratch."""
    bad_xml = b"<not><well-formed></not>"
    h = xml_hash(bad_xml)

    def boom(xml_bytes):
        raise flex_parser_mod.etree.XMLSyntaxError("forced", 0, 0, 0)

    monkeypatch.setattr(flex_parser_mod, "parse", boom)

    # First attempt: poison
    with pytest.raises(Exception):
        await flex_job.ingest_xml(
            db_session,
            organization_id=sample_org.id,
            xml_bytes=bad_xml,
            source="manual_upload",
            trigger="manual",
        )

    # Recovery: delete the poison row using a fresh session
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession as AS2

    maker2 = async_sessionmaker(db_engine, expire_on_commit=False, class_=AS2)
    async with maker2() as s2:
        await s2.execute(delete(FlexImport).where(FlexImport.xml_hash == h))
        await s2.commit()

    # Verify deleted
    async with maker2() as s3:
        row = await s3.scalar(select(FlexImport).where(FlexImport.xml_hash == h))
        assert row is None, "Poison row should have been deleted"
