"""GET /api/health/ingest — per-org ingest health summary (R4 backend + R6 API).

Two planes, two sources (D-CONV-1):
- Flex health reads ingest_log scoped by organization_id (org-scoped data plane).
- TRM health reads trm_imports (GLOBAL system job; TRM no longer writes
  ingest_log). TRM only records successful imports, so failure observability is
  staleness of max(fetched_at) here; rich failure tracking is SP8.

The IngestSourceHealth / IngestHealthResponse shapes stay identical (frontend
contract): both sources surface last_success_at / last_failure_at /
consecutive_failures / last_error.
"""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.api._context import org_context
from ibkr_control.db.models.ingest_log import IngestLog
from ibkr_control.db.models.trm import TrmImport
from ibkr_control.db.session import get_async_session


router = APIRouter(prefix="/health", tags=["health"])


class IngestSourceHealth(BaseModel):
    source: Literal["flex", "trm"]
    last_success_at: datetime | None
    last_failure_at: datetime | None
    consecutive_failures: int
    last_error: str | None


class IngestHealthResponse(BaseModel):
    sources: list[IngestSourceHealth]
    checked_at: datetime


_ERROR_TRUNCATE_LEN = 500


async def _flex_health(session: AsyncSession, *, organization_id: int) -> IngestSourceHealth:
    """Flex health from ingest_log, scoped to this org's runs (job_kind='flex')."""
    last_success = await session.scalar(
        select(IngestLog.started_at)
        .where(
            IngestLog.organization_id == organization_id,
            IngestLog.job_kind == "flex",
            IngestLog.status == "ok",
        )
        .order_by(IngestLog.started_at.desc())
        .limit(1)
    )

    last_failure_row = await session.execute(
        select(IngestLog.started_at, IngestLog.error_message)
        .where(
            IngestLog.organization_id == organization_id,
            IngestLog.job_kind == "flex",
            IngestLog.status == "failed",
        )
        .order_by(IngestLog.started_at.desc())
        .limit(1)
    )
    last_failure_tuple = last_failure_row.first()
    last_failure_at = last_failure_tuple[0] if last_failure_tuple else None
    last_error = last_failure_tuple[1] if last_failure_tuple else None
    if last_error and len(last_error) > _ERROR_TRUNCATE_LEN:
        last_error = last_error[:_ERROR_TRUNCATE_LEN] + "..."

    consec_q = select(func.count(IngestLog.id)).where(
        IngestLog.organization_id == organization_id,
        IngestLog.job_kind == "flex",
        IngestLog.status == "failed",
    )
    if last_success is not None:
        consec_q = consec_q.where(IngestLog.started_at > last_success)
    consecutive_failures = await session.scalar(consec_q) or 0

    return IngestSourceHealth(
        source="flex",
        last_success_at=last_success,
        last_failure_at=last_failure_at,
        consecutive_failures=consecutive_failures,
        last_error=last_error,
    )


async def _trm_health(session: AsyncSession) -> IngestSourceHealth:
    """TRM health from trm_imports (GLOBAL system job). trm_imports records only
    successful imports, so last_success_at = max(fetched_at); there is no
    failure record to read (last_failure_at / last_error None, consec 0). TRM
    staleness is judged on last_success_at; rich failure observability is SP8.
    """
    last_success = await session.scalar(select(func.max(TrmImport.fetched_at)))
    return IngestSourceHealth(
        source="trm",
        last_success_at=last_success,
        last_failure_at=None,
        consecutive_failures=0,
        last_error=None,
    )


@router.get("/ingest", response_model=IngestHealthResponse)
async def get_ingest_health(
    org_id: int = Depends(org_context),
    session: AsyncSession = Depends(get_async_session),
) -> IngestHealthResponse:
    flex = await _flex_health(session, organization_id=org_id)
    trm = await _trm_health(session)
    return IngestHealthResponse(
        sources=[flex, trm],
        checked_at=datetime.now(timezone.utc),
    )
