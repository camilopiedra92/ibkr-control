"""Endpoints /api/ingest/* — manual trigger, SSE stream, logs viewer."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ibkr_control.api._schemas import IngestJobStarted, IngestLogRead, IngestTrigger
from ibkr_control.auth.backend import current_active_user
from ibkr_control.auth.models import User
from ibkr_control.config import get_settings
from ibkr_control.db.models.ingest_log import IngestLog
from ibkr_control.db.session import get_async_session, get_engine
from ibkr_control.ingest.job_tracker import get_tracker

try:
    from sse_starlette.sse import EventSourceResponse
except ImportError:
    EventSourceResponse = None  # type: ignore[assignment,misc]

router = APIRouter(prefix="/ingest", tags=["ingest"])

# Rate limit: module-level dict tracks last trigger time per user.
# V1: in-process, single replica. Container restart resets cooldown.
# Cooldown duration is config-driven via settings.ingest_trigger_cooldown_seconds.
_LAST_TRIGGER: dict[int, datetime] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@router.post("/trigger", response_model=IngestJobStarted)
async def trigger_manual_refresh(
    payload: IngestTrigger,
    background: BackgroundTasks,
    user: User = Depends(current_active_user),
) -> IngestJobStarted:
    settings = get_settings()
    cooldown = timedelta(seconds=settings.ingest_trigger_cooldown_seconds)
    last = _LAST_TRIGGER.get(user.id)
    if last and (_utcnow() - last) < cooldown:
        wait = cooldown - (_utcnow() - last)
        raise HTTPException(
            status_code=429,
            detail=f"Espera {int(wait.total_seconds())}s antes de reintentar",
        )
    _LAST_TRIGGER[user.id] = _utcnow()

    job_id = await _launch_manual_job(payload.kind, user.id, background)
    return IngestJobStarted(job_id=job_id)


async def _launch_manual_job(kind: str, user_id: int, background: BackgroundTasks) -> int:
    """Lanza el job real en background. Devuelve job_id del tracker."""
    tracker = get_tracker()
    job_id = tracker.create_job()
    background.add_task(_run_manual, kind=kind, user_id=user_id, job_id=job_id)
    return job_id


async def _run_manual(kind: str, user_id: int, job_id: int) -> None:
    from ibkr_control.ingest.flex import job as flex_job_mod
    from ibkr_control.ingest.trm import job as trm_job_mod

    engine = get_engine()
    session_local = async_sessionmaker(engine, expire_on_commit=False)
    tracker = get_tracker()

    try:
        if kind in ("trm", "both"):
            tracker.emit(job_id, {"step": "trm", "status": "running"})
            r = await trm_job_mod.run(session_local, trigger="manual")
            tracker.emit(job_id, {"step": "trm", "status": "ok", "n_days": r["n_days"]})

        if kind in ("flex", "both"):
            tracker.emit(job_id, {"step": "flex", "status": "running"})
            await flex_job_mod.run(session_local, user_id=user_id, trigger="manual")
            tracker.emit(job_id, {"step": "flex", "status": "ok"})

        tracker.emit(job_id, {"step": "done"})
    except Exception as e:
        # Must stay broad: this is the SSE background task catch-all. Any
        # unhandled exception (network, parse, DB, lock) must surface to the
        # client via the tracker event so the UI can display the error message.
        tracker.emit(job_id, {"step": "error", "error": str(e)[:500]})
    finally:
        tracker.mark_done(job_id)


@router.get("/stream/{job_id}")
async def stream_progress(
    job_id: int,
    user: User = Depends(current_active_user),
):
    # NOTE: V1 no verifica que job_id pertenezca al usuario — single-user.
    tracker = get_tracker()
    if not tracker.has_job(job_id):
        raise HTTPException(status_code=404, detail="Unknown job_id")

    if EventSourceResponse is None:
        raise HTTPException(status_code=501, detail="SSE no disponible")

    async def event_generator():
        # events_since es EXCLUSIVO: last_id=-1 devuelve todos (id > -1 = id >= 0).
        # Despues de cada batch, last_id = ev.id del ultimo evento procesado.
        # La proxima llamada usa last_id directamente (no +1) por semantica exclusiva.
        last_id = -1
        while True:
            events = tracker.events_since(job_id, after_id=last_id)
            for ev in events:
                event_type = "done" if ev.payload.get("step") == "done" else "progress"
                yield {
                    "id": str(ev.id),
                    "event": event_type,
                    "data": json.dumps(ev.payload),
                }
                last_id = ev.id
            if tracker.is_done(job_id):
                # Si el job termino sin emitir un step="done", emitir evento de cierre.
                if not events or events[-1].payload.get("step") != "done":
                    yield {"event": "done", "data": "{}"}
                break
            await asyncio.sleep(0.5)

    return EventSourceResponse(
        event_generator(),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/logs", response_model=list[IngestLogRead])
async def list_logs(
    limit: int = Query(10, ge=1, le=100),
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> list[IngestLogRead]:
    result = await session.scalars(
        select(IngestLog)
        .where((IngestLog.user_id == user.id) | (IngestLog.user_id.is_(None)))
        .order_by(IngestLog.started_at.desc())
        .limit(limit)
    )
    return [IngestLogRead.model_validate(r, from_attributes=True) for r in result.all()]
