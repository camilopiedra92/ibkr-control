"""Tests del TRM job orchestrator."""
from datetime import date
from decimal import Decimal
import pytest
import respx
from httpx import HTTPStatusError, Response
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ibkr_control.ingest.trm import job as trm_job


@pytest.mark.asyncio
async def test_run_with_no_new_rows_logs_ok_zero(db_session: AsyncSession, db_engine):
    # db_session creates the schema via Base.metadata.create_all;
    # db_engine provides additional sessions for the job (same container).
    SessionLocal = async_sessionmaker(db_engine, expire_on_commit=False)
    with respx.mock(base_url="https://www.datos.gov.co") as router:
        router.get("/resource/ceyp-9c7c.json").mock(return_value=Response(200, json=[]))
        result = await trm_job.run(SessionLocal, trigger="cron")

    assert result["n_days"] == 0
    assert result["status"] == "ok"

    from ibkr_control.db.models.ingest_log import IngestLog
    async with SessionLocal() as s:
        log = await s.scalar(
            select(IngestLog)
            .where(IngestLog.job_kind == "trm")
            .order_by(IngestLog.id.desc())
            .limit(1)
        )
        assert log is not None
        assert log.status == "ok"
        assert log.items_processed == 0


@pytest.mark.asyncio
async def test_run_inserts_new_days(db_session: AsyncSession, db_engine):
    # db_session creates the schema; db_engine provides job sessions.
    SessionLocal = async_sessionmaker(db_engine, expire_on_commit=False)
    payload = [
        {"vigenciadesde": "2027-01-02T00:00:00.000", "vigenciahasta": "2027-01-02T00:00:00.000", "valor": "4200"},
        {"vigenciadesde": "2027-01-03T00:00:00.000", "vigenciahasta": "2027-01-05T00:00:00.000", "valor": "4220"},
    ]
    with respx.mock(base_url="https://www.datos.gov.co") as router:
        router.get("/resource/ceyp-9c7c.json").mock(return_value=Response(200, json=payload))
        result = await trm_job.run(SessionLocal, trigger="cron")

    assert result["n_days"] == 4  # 1 + 3 dias expandidos
    assert result["status"] == "ok"

    from ibkr_control.db.models.trm import TrmDay
    async with SessionLocal() as s:
        count = await s.scalar(select(func.count(TrmDay.date)).where(
            TrmDay.date >= date(2027, 1, 2),
            TrmDay.date <= date(2027, 1, 5),
        ))
        assert count == 4


@pytest.mark.asyncio
async def test_run_logs_failure_on_http_error(db_session: AsyncSession, db_engine):
    # db_session creates the schema; db_engine provides job sessions.
    SessionLocal = async_sessionmaker(db_engine, expire_on_commit=False)
    with respx.mock(base_url="https://www.datos.gov.co") as router:
        router.get("/resource/ceyp-9c7c.json").mock(return_value=Response(500, json={"error": "boom"}))
        with pytest.raises(HTTPStatusError):
            await trm_job.run(SessionLocal, trigger="cron")

    from ibkr_control.db.models.ingest_log import IngestLog
    async with SessionLocal() as s:
        log = await s.scalar(
            select(IngestLog)
            .where(IngestLog.job_kind == "trm")
            .order_by(IngestLog.id.desc())
            .limit(1)
        )
        assert log.status == "failed"
        assert log.error_message is not None
