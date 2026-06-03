"""Endpoints /api/ingest/* — manual trigger, SSE stream, logs viewer."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sse_starlette.sse import EventSourceResponse

from ibkr_control.api._context import org_context
from ibkr_control.api._schemas import IngestJobStarted, IngestLogRead, IngestTrigger
from ibkr_control.auth.backend import current_active_user
from ibkr_control.auth.models import User
from ibkr_control.config import get_settings
from ibkr_control.db.models.ingest_log import IngestLog
from ibkr_control.db.session import get_async_session, get_engine
from ibkr_control.ingest.job_tracker import get_tracker

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("/trigger", response_model=IngestJobStarted)
async def trigger_manual_refresh(
    payload: IngestTrigger,
    background: BackgroundTasks,
    user: User = Depends(current_active_user),
    org_id: int = Depends(org_context),
    session: AsyncSession = Depends(get_async_session),
) -> IngestJobStarted:
    """Trigger manual del ingest. Rate-limited via UPDATE atomico condicional.

    El throttle es PER-ORG (D-CONV-3): el ingest es la unidad de operación del
    tenant, no del usuario. El UPDATE solo afecta una fila si el cooldown ya
    pasó; rowcount=0 indica rate-limited y devolvemos 429 con el tiempo
    restante. Esto elimina el race condition TOCTOU del patron check-then-set y
    persiste el estado en DB (sobrevive container restart, multi-replica safe).
    """
    from sqlalchemy import or_, select, update

    from ibkr_control.db.models.organizations import Organization

    settings = get_settings()
    cooldown = timedelta(seconds=settings.ingest_trigger_cooldown_seconds)
    now = datetime.now(timezone.utc)
    cutoff = now - cooldown

    result = await session.execute(
        update(Organization)
        .where(Organization.id == org_id)
        .where(
            or_(
                Organization.last_ingest_trigger_at.is_(None),
                Organization.last_ingest_trigger_at < cutoff,
            )
        )
        .values(last_ingest_trigger_at=now)
    )
    await session.commit()

    if result.rowcount == 0:
        current = await session.scalar(
            select(Organization.last_ingest_trigger_at).where(Organization.id == org_id)
        )
        wait_seconds = (
            int((cooldown - (now - current)).total_seconds())
            if current
            else int(cooldown.total_seconds())
        )
        raise HTTPException(
            status_code=429,
            detail=f"Espera {wait_seconds}s antes de reintentar",
        )

    job_id = await _launch_manual_job(payload.kind, user.id, org_id, background)
    return IngestJobStarted(job_id=job_id)


async def _launch_manual_job(
    kind: str, user_id: int, org_id: int, background: BackgroundTasks
) -> int:
    """Lanza el job real en background. Devuelve job_id del tracker.

    org_id es el scope del ingest (flex run es per-org). El job_id (ownership)
    sigue siendo per-user (D1): lo dueña quien lo dispara."""
    tracker = get_tracker()
    job_id = tracker.create_job(user_id=user_id)
    background.add_task(_run_manual, kind=kind, org_id=org_id, job_id=job_id)
    return job_id


async def _run_manual(kind: str, org_id: int, job_id: int) -> None:
    from ibkr_control.ingest.flex import job as flex_job_mod
    from ibkr_control.ingest.trm import job as trm_job_mod

    engine = get_engine()
    session_local = async_sessionmaker(engine, expire_on_commit=False)
    tracker = get_tracker()

    # Tracks the substep that is currently running so a crash mid-substep
    # surfaces with status="failed" on the right row in the UI.
    current_step: str | None = None
    try:
        if kind in ("trm", "both"):
            current_step = "trm_backfill"
            tracker.emit(job_id, {"step": current_step, "status": "running"})
            r = await trm_job_mod.run(session_local, trigger="manual")
            tracker.emit(
                job_id,
                {"step": current_step, "status": "ok", "n_days": r["n_days"]},
            )

        if kind in ("flex", "both"):
            current_step = "flex_ytd"
            tracker.emit(job_id, {"step": current_step, "status": "running"})
            await flex_job_mod.run(session_local, organization_id=org_id, trigger="manual")
            tracker.emit(job_id, {"step": current_step, "status": "ok"})

        current_step = None
        tracker.emit(job_id, {"step": "done"})
    except Exception as e:
        # Must stay broad: this is the SSE background task catch-all. Any
        # unhandled exception (network, parse, DB, lock) must surface to the
        # client via the tracker event so the UI can display the error message.
        # When the crash happened inside a substep we tag the failure with that
        # substep so the frontend (ManualRefreshButton) can red-flag the right
        # row; otherwise we fall back to a synthetic "error" step.
        payload: dict = {"status": "failed", "error": str(e)[:500]}
        payload["step"] = current_step if current_step is not None else "error"
        tracker.emit(job_id, payload)
    finally:
        tracker.mark_done(job_id)


@router.get("/stream/{job_id}")
async def stream_progress(
    job_id: int,
    user: User = Depends(current_active_user),
):
    tracker = get_tracker()
    # Ownership scope (D1): a job belongs to the user who triggered it. A job
    # owned by someone else is reported as 404 — identical to a non-existent
    # job — so we never leak the existence of another user's job (404, not 403).
    if tracker.owner_id(job_id) != user.id:
        raise HTTPException(status_code=404, detail="Unknown job_id")

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
    org_id: int = Depends(org_context),
    session: AsyncSession = Depends(get_async_session),
) -> list[IngestLogRead]:
    # Per-org ingest activity. The old `| user_id IS NULL` clause existed to
    # surface TRM global rows; TRM no longer writes ingest_log (D-CONV-1) — its
    # observability is the health endpoint — so this is now strictly the org's
    # own flex/manual ingest log.
    result = await session.scalars(
        select(IngestLog)
        .where(IngestLog.organization_id == org_id)
        .order_by(IngestLog.started_at.desc())
        .limit(limit)
    )
    return [IngestLogRead.model_validate(r, from_attributes=True) for r in result.all()]
