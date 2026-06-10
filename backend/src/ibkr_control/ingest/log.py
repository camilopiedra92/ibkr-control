"""Context manager para crear + actualizar rows en ingest_log."""

import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@asynccontextmanager
async def ingest_log_entry(
    session: AsyncSession,
    job_kind: str,
    organization_id: int,
    trigger: str,
    connection_id: int | None = None,
):
    """Crea un row en ingest_log con status='running' y lo cierra al salir.

    Yields:
        log_id: int del row recien creado (el caller puede usarlo para
                modificar items_processed antes del exit).

    Args:
        job_kind: 'flex' | 'trm' | 'manual_refresh' | 'manual_upload' | 'setup_initial'
        organization_id: tenant dueño del run (NOT NULL + RLS en ingest_log).
                 El org es la unidad de tenancy/operación — sin user_id (D-CONV-3).
        trigger: 'cron' | 'manual' | 'wizard'
        connection_id: conexión que produjo el run (flex); None para
                 manual_upload/TRM (no hay conexión asociada).
    """
    from ibkr_control.db.models.ingest_log import IngestLog

    row = IngestLog(
        job_kind=job_kind,
        organization_id=organization_id,
        trigger=trigger,
        status="running",
        connection_id=connection_id,
    )
    session.add(row)
    await session.flush()
    log_id = row.id
    try:
        yield log_id
        row.status = "ok"
        row.finished_at = _utcnow()
    except Exception as exc:
        row.status = "failed"
        tb_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        row.error_message = tb_text[:8000]
        row.finished_at = _utcnow()
        raise
    finally:
        await session.commit()
