"""Tests del schema ingest_log (migration D)."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_ingest_log_table_exists(db_session: AsyncSession):
    result = await db_session.execute(text("SELECT to_regclass('ingest_log')"))
    assert result.scalar() == "ingest_log"


@pytest.mark.asyncio
async def test_ingest_log_job_kind_check(db_session: AsyncSession, sample_user):
    from ibkr_control.db.models.ingest_log import IngestLog

    db_session.add(
        IngestLog(
            job_kind="INVALID",
            user_id=sample_user.id,
            status="running",
            trigger="cron",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_ingest_log_user_deleted_sets_null(db_session: AsyncSession, sample_user):
    from ibkr_control.db.models.ingest_log import IngestLog
    from ibkr_control.auth.models import User

    log = IngestLog(
        job_kind="flex",
        user_id=sample_user.id,
        status="ok",
        trigger="cron",
        items_processed=42,
    )
    db_session.add(log)
    await db_session.commit()
    await db_session.refresh(log)
    log_id = log.id

    # Delete the user — should SET NULL on user_id, not cascade-delete the log
    user = await db_session.get(User, sample_user.id)
    await db_session.delete(user)
    await db_session.commit()

    result = await db_session.execute(
        text("SELECT user_id FROM ingest_log WHERE id = :id"),
        {"id": log_id},
    )
    assert result.scalar() is None


@pytest.mark.asyncio
async def test_ix_ingest_log_user_id_started_at(db_session: AsyncSession):
    result = await db_session.execute(
        text("""
        SELECT indexname FROM pg_indexes
        WHERE tablename = 'ingest_log'
          AND indexname = 'ix_ingest_log_user_id_started_at'
    """)
    )
    assert result.scalar() == "ix_ingest_log_user_id_started_at"


@pytest.mark.asyncio
async def test_ingest_log_index_is_desc_on_started_at(db_session: AsyncSession):
    result = await db_session.execute(
        text("""
        SELECT indexdef FROM pg_indexes
        WHERE indexname = 'ix_ingest_log_user_id_started_at'
    """)
    )
    indexdef = result.scalar()
    assert indexdef is not None
    assert "started_at DESC" in indexdef
