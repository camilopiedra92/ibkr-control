"""GET /api/health/ingest — per-user ingest health summary (R4 backend + R6 API)."""
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.auth.models import User
from ibkr_control.auth.backend import current_active_user
from ibkr_control.db.models.ingest_log import IngestLog
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


async def _source_health(
    session: AsyncSession, *, user_id: int, source: Literal["flex", "trm"]
) -> IngestSourceHealth:
    job_kind = "flex" if source == "flex" else "trm"

    last_success = await session.scalar(
        select(IngestLog.started_at)
        .where(IngestLog.user_id == user_id, IngestLog.job_kind == job_kind, IngestLog.status == "ok")
        .order_by(IngestLog.started_at.desc())
        .limit(1)
    )

    last_failure_row = await session.execute(
        select(IngestLog.started_at, IngestLog.error_message)
        .where(IngestLog.user_id == user_id, IngestLog.job_kind == job_kind, IngestLog.status == "failed")
        .order_by(IngestLog.started_at.desc())
        .limit(1)
    )
    last_failure_tuple = last_failure_row.first()
    last_failure_at = last_failure_tuple[0] if last_failure_tuple else None
    last_error = last_failure_tuple[1] if last_failure_tuple else None
    if last_error and len(last_error) > _ERROR_TRUNCATE_LEN:
        last_error = last_error[:_ERROR_TRUNCATE_LEN] + "..."

    consec_q = select(func.count(IngestLog.id)).where(
        IngestLog.user_id == user_id,
        IngestLog.job_kind == job_kind,
        IngestLog.status == "failed",
    )
    if last_success is not None:
        consec_q = consec_q.where(IngestLog.started_at > last_success)
    consecutive_failures = await session.scalar(consec_q) or 0

    return IngestSourceHealth(
        source=source,
        last_success_at=last_success,
        last_failure_at=last_failure_at,
        consecutive_failures=consecutive_failures,
        last_error=last_error,
    )


@router.get("/ingest", response_model=IngestHealthResponse)
async def get_ingest_health(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> IngestHealthResponse:
    flex = await _source_health(session, user_id=user.id, source="flex")
    trm = await _source_health(session, user_id=user.id, source="trm")
    return IngestHealthResponse(
        sources=[flex, trm],
        checked_at=datetime.now(timezone.utc),
    )
