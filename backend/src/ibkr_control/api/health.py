"""GET /api/health/ingest — per-org ingest health summary (R4 backend + R6 API).

Two planes, two sources (D-CONV-1):
- Flex health reads ingest_log scoped by organization_id (org-scoped data plane).
- TRM health reads trm_imports (GLOBAL system job; TRM no longer writes
  ingest_log). TRM only records successful imports, so failure observability is
  staleness of max(fetched_at) here; rich failure tracking is SP8.

The IngestSourceHealth shape stays identical (frontend contract): both sources
surface last_success_at / last_failure_at / consecutive_failures / last_error.

The connections plane (additive): per-connection state from the W4 state machine
(connection.status / status_reason / last_sync_at). This is the durable signal of
a partial failure — when an org has multiple connections and one fails while
another succeeds, the manual-refresh SSE collapses to "ok"; the per-connection
status here is what makes that partial failure VISIBLE to the frontend.
"""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.api._context import org_context
from ibkr_control.api._schemas import ConnectionStatus
from ibkr_control.db.models.connections import Connection
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


class ConnectionHealth(BaseModel):
    id: int
    display_name: str | None
    # ConnectionStatus Literal (no str): orval genera un union de strings en el
    # cliente TS, habilitando switch exhaustivo sobre status en el frontend.
    status: ConnectionStatus
    status_reason: str | None
    last_sync_at: datetime | None


class IngestHealthResponse(BaseModel):
    sources: list[IngestSourceHealth]
    connections: list[ConnectionHealth]
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


async def _connection_health(session: AsyncSession) -> list[ConnectionHealth]:
    """Per-connection state from the W4 state machine (RLS scopes to the org).

    Ordered by id for a deterministic response. Surfaces the durable
    partial-failure signal (status / status_reason / last_sync_at).
    """
    rows = await session.scalars(select(Connection).order_by(Connection.id))
    return [
        ConnectionHealth(
            id=c.id,
            display_name=c.display_name,
            status=c.status,
            status_reason=c.status_reason,
            last_sync_at=c.last_sync_at,
        )
        for c in rows
    ]


@router.get("/ingest", response_model=IngestHealthResponse)
async def get_ingest_health(
    org_id: int = Depends(org_context),
    session: AsyncSession = Depends(get_async_session),
) -> IngestHealthResponse:
    flex = await _flex_health(session, organization_id=org_id)
    trm = await _trm_health(session)
    connections = await _connection_health(session)
    return IngestHealthResponse(
        sources=[flex, trm],
        connections=connections,
        checked_at=datetime.now(timezone.utc),
    )
