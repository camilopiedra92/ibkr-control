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
    user_id: int | None,
    trigger: str,
):
    """Crea un row en ingest_log con status='running' y lo cierra al salir.

    Yields:
        log_id: int del row recien creado (el caller puede usarlo para
                modificar items_processed antes del exit).

    Args:
        job_kind: 'flex' | 'trm' | 'manual_refresh' | 'manual_upload' | 'setup_initial'
        user_id: None para jobs globales (e.g. TRM cron sin user especifico)
        trigger: 'cron' | 'manual' | 'wizard'
    """
    from ibkr_control.db.models.ingest_log import IngestLog

    row = IngestLog(job_kind=job_kind, user_id=user_id, trigger=trigger, status="running")
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
