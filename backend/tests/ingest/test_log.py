"""Tests del context manager ingest_log_entry."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.ingest.log import ingest_log_entry


@pytest.mark.asyncio
async def test_log_creates_running_row_on_enter(db_session: AsyncSession, sample_user):
    async with ingest_log_entry(db_session, "flex", sample_user.id, "cron") as log_id:
        result = await db_session.execute(
            text(f"SELECT status, job_kind, user_id, trigger FROM ingest_log WHERE id = {log_id}")
        )
        row = result.first()
        assert row.status == "running"
        assert row.job_kind == "flex"
        assert row.user_id == sample_user.id
        assert row.trigger == "cron"


@pytest.mark.asyncio
async def test_log_finishes_ok_on_normal_exit(db_session: AsyncSession, sample_user):
    async with ingest_log_entry(db_session, "trm", sample_user.id, "manual") as log_id:
        pass
    result = await db_session.execute(
        text(f"SELECT status, finished_at, error_message FROM ingest_log WHERE id = {log_id}")
    )
    row = result.first()
    assert row.status == "ok"
    assert row.finished_at is not None
    assert row.error_message is None


@pytest.mark.asyncio
async def test_log_finishes_failed_on_exception(db_session: AsyncSession, sample_user):
    with pytest.raises(ValueError, match="boom"):
        async with ingest_log_entry(db_session, "flex", sample_user.id, "wizard") as log_id:
            raise ValueError("boom")

    result = await db_session.execute(
        text(f"SELECT status, error_message, finished_at FROM ingest_log WHERE id = {log_id}")
    )
    row = result.first()
    assert row.status == "failed"
    assert "ValueError" in row.error_message
    assert "boom" in row.error_message
    assert row.finished_at is not None


@pytest.mark.asyncio
async def test_log_items_processed_settable(db_session: AsyncSession, sample_user):
    """El caller puede setear items_processed antes del exit."""
    from ibkr_control.db.models.ingest_log import IngestLog
    from sqlalchemy import select

    async with ingest_log_entry(db_session, "flex", sample_user.id, "cron") as log_id:
        row = await db_session.scalar(select(IngestLog).where(IngestLog.id == log_id))
        row.items_processed = 42

    result = await db_session.execute(
        text(f"SELECT items_processed FROM ingest_log WHERE id = {log_id}")
    )
    assert result.scalar() == 42
