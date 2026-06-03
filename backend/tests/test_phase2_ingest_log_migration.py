"""Tests del schema ingest_log (migration D).

NOTE (SP1 / D-CONV-3): ingest_log is now purely org-scoped — the `user_id`
column + its SET NULL FK + the `ix_ingest_log_user_id_started_at` index were
dropped (org is the unit of tenancy/operation). The old user-deletion-SET-NULL
and user-index tests are removed; the new audit index is on
(organization_id, started_at DESC).
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_ingest_log_table_exists(db_session: AsyncSession):
    result = await db_session.execute(text("SELECT to_regclass('ingest_log')"))
    assert result.scalar() == "ingest_log"


@pytest.mark.asyncio
async def test_ingest_log_job_kind_check(db_session: AsyncSession, sample_org):
    from ibkr_control.db.models.ingest_log import IngestLog

    db_session.add(
        IngestLog(
            job_kind="INVALID",
            organization_id=sample_org.id,
            status="running",
            trigger="cron",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_ix_ingest_log_org_started_at(db_session: AsyncSession):
    result = await db_session.execute(
        text("""
        SELECT indexname FROM pg_indexes
        WHERE tablename = 'ingest_log'
          AND indexname = 'ix_ingest_log_org_started_at'
    """)
    )
    assert result.scalar() == "ix_ingest_log_org_started_at"


@pytest.mark.asyncio
async def test_ingest_log_index_is_desc_on_started_at(db_session: AsyncSession):
    result = await db_session.execute(
        text("""
        SELECT indexdef FROM pg_indexes
        WHERE indexname = 'ix_ingest_log_org_started_at'
    """)
    )
    indexdef = result.scalar()
    assert indexdef is not None
    assert "started_at DESC" in indexdef
    assert "organization_id" in indexdef
