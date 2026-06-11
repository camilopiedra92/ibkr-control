"""Tests del TRM job orchestrator.

TRM es control plane: NO escribe en ingest_log (org-scoped + RLS). Su fuente
de verdad / observabilidad es trm_imports (global). Los tests verifican que
trm_imports se escribe en exito, que ingest_log NUNCA recibe un row TRM, y que
un fallo no deja datos parciales (ni trm_days ni trm_imports).
"""

from datetime import date

import pytest
import respx
from httpx import HTTPStatusError, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ibkr_control.ingest.trm import job as trm_job


@pytest.mark.asyncio
async def test_run_with_no_new_rows_returns_ok_zero(db_engine):
    # The migrated ``test_db`` clone (via db_engine) provides the schema; this
    # engine yields the sessions the job runs against.
    SessionLocal = async_sessionmaker(db_engine, expire_on_commit=False)
    with respx.mock(base_url="https://www.datos.gov.co") as router:
        router.get("/resource/ceyp-9c7c.json").mock(return_value=Response(200, json=[]))
        result = await trm_job.run(SessionLocal, trigger="cron")

    assert result["n_days"] == 0
    assert result["status"] == "ok"

    # TRM is control plane: no ingest_log row, no trm_imports row (nothing fetched).
    from ibkr_control.db.models.ingest_log import IngestLog
    from ibkr_control.db.models.trm import TrmImport

    async with SessionLocal() as s:
        assert (await s.scalar(select(func.count()).select_from(IngestLog))) == 0
        assert (await s.scalar(select(func.count()).select_from(TrmImport))) == 0


@pytest.mark.asyncio
async def test_run_inserts_new_days_and_records_import(db_engine):
    # The migrated ``test_db`` clone (via db_engine) provides the schema; this
    # engine yields the sessions the job runs against.
    SessionLocal = async_sessionmaker(db_engine, expire_on_commit=False)
    payload = [
        {
            "vigenciadesde": "2027-01-02T00:00:00.000",
            "vigenciahasta": "2027-01-02T00:00:00.000",
            "valor": "4200",
        },
        {
            "vigenciadesde": "2027-01-03T00:00:00.000",
            "vigenciahasta": "2027-01-05T00:00:00.000",
            "valor": "4220",
        },
    ]
    with respx.mock(base_url="https://www.datos.gov.co") as router:
        router.get("/resource/ceyp-9c7c.json").mock(return_value=Response(200, json=payload))
        result = await trm_job.run(SessionLocal, trigger="cron")

    assert result["n_days"] == 4  # 1 + 3 dias expandidos
    assert result["status"] == "ok"

    from ibkr_control.db.models.ingest_log import IngestLog
    from ibkr_control.db.models.trm import TrmDay, TrmImport

    async with SessionLocal() as s:
        count = await s.scalar(
            select(func.count(TrmDay.date)).where(
                TrmDay.date >= date(2027, 1, 2),
                TrmDay.date <= date(2027, 1, 5),
            )
        )
        assert count == 4

        # TRM observability lives in trm_imports, NOT ingest_log.
        assert (await s.scalar(select(func.count()).select_from(IngestLog))) == 0
        imp = await s.scalar(select(TrmImport).order_by(TrmImport.id.desc()).limit(1))
        assert imp is not None
        assert imp.n_rows_api == 2
        assert imp.n_days_expanded == 4
        assert imp.date_range_from == date(2027, 1, 2)
        assert imp.date_range_to == date(2027, 1, 5)


@pytest.mark.asyncio
async def test_run_raises_on_http_error_and_writes_no_log(db_engine):
    # The migrated ``test_db`` clone (via db_engine) provides the schema; this
    # engine yields the sessions the job runs against.
    SessionLocal = async_sessionmaker(db_engine, expire_on_commit=False)
    with respx.mock(base_url="https://www.datos.gov.co") as router:
        router.get("/resource/ceyp-9c7c.json").mock(
            return_value=Response(500, json={"error": "boom"})
        )
        with pytest.raises(HTTPStatusError):
            await trm_job.run(SessionLocal, trigger="cron")

    # TRM never writes ingest_log; an HTTP failure leaves nothing behind.
    from ibkr_control.db.models.ingest_log import IngestLog
    from ibkr_control.db.models.trm import TrmImport

    async with SessionLocal() as s:
        assert (await s.scalar(select(func.count()).select_from(IngestLog))) == 0
        assert (await s.scalar(select(func.count()).select_from(TrmImport))) == 0


@pytest.mark.asyncio
async def test_run_persist_failure_leaves_no_partial_data(db_engine):
    """A failure during persist must roll back the whole tx — no partial trm_days,
    no trm_imports, no ingest_log. The plain transaction (no SAVEPOINT) relies on
    the session context manager rolling back when the exception propagates."""
    SessionLocal = async_sessionmaker(db_engine, expire_on_commit=False)
    payload = [
        {
            "vigenciadesde": "2027-02-01T00:00:00.000",
            "vigenciahasta": "2027-02-03T00:00:00.000",
            "valor": "4300",
        },
    ]

    async def _boom(*args, **kwargs):
        raise RuntimeError("persist exploded")

    import ibkr_control.ingest.trm.job as job_mod

    with respx.mock(base_url="https://www.datos.gov.co") as router:
        router.get("/resource/ceyp-9c7c.json").mock(return_value=Response(200, json=payload))
        # Force the persist step to blow up AFTER fetch succeeded.
        original = job_mod.bulk_upsert_days
        job_mod.bulk_upsert_days = _boom
        try:
            with pytest.raises(RuntimeError, match="persist exploded"):
                await trm_job.run(SessionLocal, trigger="cron")
        finally:
            job_mod.bulk_upsert_days = original

    from ibkr_control.db.models.ingest_log import IngestLog
    from ibkr_control.db.models.trm import TrmDay, TrmImport

    async with SessionLocal() as s:
        assert (await s.scalar(select(func.count()).select_from(TrmDay))) == 0
        assert (await s.scalar(select(func.count()).select_from(TrmImport))) == 0
        assert (await s.scalar(select(func.count()).select_from(IngestLog))) == 0
