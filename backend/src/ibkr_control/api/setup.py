"""Wizard endpoints -- state machine en users.setup_progress JSONB."""
from datetime import date, datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import attributes

from ibkr_control.api._schemas import (
    FlexCredentialsValidate,
    SetupJobStarted,
    SetupState,
    SetupStep2Save,
)
from ibkr_control.auth.backend import current_active_user
from ibkr_control.auth.models import User
from ibkr_control.db.models.accounts import Account
from ibkr_control.db.models.flex_credentials import FlexCredentials
from ibkr_control.db.models.flex_raw import FlexImport
from ibkr_control.db.models.participations import Participation
from ibkr_control.db.session import get_async_session, get_engine
from ibkr_control.ingest.flex import client as flex_client_mod
from ibkr_control.ingest.flex import crypto as flex_crypto_mod
from ibkr_control.ingest.job_tracker import get_tracker

router = APIRouter(prefix="/setup", tags=["setup"])


def _set_progress(user: User, key: str, value) -> None:
    """Mutate setup_progress dict y flagear dirty para SQLAlchemy."""
    progress = dict(user.setup_progress or {})
    progress[key] = value
    user.setup_progress = progress
    attributes.flag_modified(user, "setup_progress")


@router.get("/state", response_model=SetupState)
async def get_state(user: User = Depends(current_active_user)) -> SetupState:
    p = user.setup_progress or {}
    return SetupState(
        step1_credentials=p.get("step1_credentials", False),
        step2_accounts=p.get("step2_accounts", False),
        step3_xmls=p.get("step3_xmls", False),
        step3_n_xmls_uploaded=p.get("step3_n_xmls_uploaded", 0),
        step4_started_at=p.get("step4_started_at"),
        step4_job_id=p.get("step4_job_id"),
        step4_substeps=p.get("step4_substeps", {}),
        setup_completed_at=user.setup_completed_at,
    )


@router.post("/step1/validate")
async def step1_validate(
    payload: FlexCredentialsValidate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    # Ping a IBKR para validar el token
    try:
        client = flex_client_mod.FlexClient(token=payload.token)
        await client.send_request(query_id=payload.query_id)
    except flex_client_mod.FlexAuthError as e:
        raise HTTPException(status_code=401, detail=f"Token invalido: {e.error_message}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"No pude alcanzar IBKR: {e}")

    # Guardar credenciales (upsert)
    creds = await session.scalar(
        select(FlexCredentials).where(FlexCredentials.user_id == user.id)
    )
    if creds is None:
        creds = FlexCredentials(
            user_id=user.id,
            token_encrypted=flex_crypto_mod.encrypt_token(payload.token),
            ytd_query_id=payload.query_id,
        )
        session.add(creds)
    else:
        creds.token_encrypted = flex_crypto_mod.encrypt_token(payload.token)
        creds.ytd_query_id = payload.query_id
        creds.last_rotated_at = datetime.now(timezone.utc)

    _set_progress(user, "step1_credentials", True)
    await session.commit()
    return {"ok": True}


@router.post("/step2/save")
async def step2_save(
    payload: SetupStep2Save,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    today = date.today()
    for item in payload.accounts:
        acc = await session.scalar(
            select(Account).where(Account.ibkr_account_id == item.ibkr_account_id)
        )
        if acc is None:
            acc = Account(
                ibkr_account_id=item.ibkr_account_id,
                alias=item.alias,
                currency="USD",
            )
            session.add(acc)
            await session.flush()
        elif item.alias is not None:
            acc.alias = item.alias

        # Upsert participation: cerrar la vigente y crear nueva con valid_from = today
        existing = await session.scalar(
            select(Participation).where(
                Participation.user_id == user.id,
                Participation.account_id == acc.id,
                Participation.valid_to.is_(None),
            )
        )
        if existing is not None:
            if existing.pct == item.pct:
                continue
            existing.valid_to = today
            await session.flush()

        session.add(
            Participation(
                user_id=user.id,
                account_id=acc.id,
                pct=item.pct,
                valid_from=today,
                valid_to=None,
            )
        )

    _set_progress(user, "step2_accounts", True)
    await session.commit()
    return {"ok": True}


@router.post("/step3/complete")
async def step3_complete(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    n_xmls = await session.scalar(
        select(func.count(FlexImport.id)).where(
            FlexImport.user_id == user.id,
            FlexImport.source == "manual_upload",
        )
    )
    _set_progress(user, "step3_xmls", True)
    _set_progress(user, "step3_n_xmls_uploaded", n_xmls or 0)
    await session.commit()
    return {"ok": True, "n_xmls_uploaded": n_xmls or 0}


@router.post("/step4/start", response_model=SetupJobStarted)
async def step4_start(
    background: BackgroundTasks,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> SetupJobStarted:
    tracker = get_tracker()
    job_id = tracker.create_job()

    _set_progress(user, "step4_started_at", datetime.now(timezone.utc).isoformat())
    _set_progress(user, "step4_job_id", job_id)
    if "step4_substeps" not in (user.setup_progress or {}):
        _set_progress(user, "step4_substeps", {})
    await session.commit()

    background.add_task(_run_setup_meta_job, user_id=user.id, job_id=job_id)
    return SetupJobStarted(job_id=job_id)


async def _run_setup_meta_job(user_id: int, job_id: int) -> None:
    """Meta-job idempotente que corre TRM backfill + Flex YTD + marca completed."""
    from ibkr_control.ingest.flex import job as flex_job_mod
    from ibkr_control.ingest.trm import job as trm_job_mod

    engine = get_engine()
    session_local = async_sessionmaker(engine, expire_on_commit=False)
    tracker = get_tracker()

    async def _read_substeps() -> dict[str, str]:
        async with session_local() as s:
            u = await s.scalar(select(User).where(User.id == user_id))
            return (u.setup_progress or {}).get("step4_substeps", {})

    async def _write_substep(name: str, status: str) -> None:
        async with session_local() as s:
            u = await s.scalar(select(User).where(User.id == user_id))
            p = dict(u.setup_progress or {})
            substeps = dict(p.get("step4_substeps", {}))
            substeps[name] = status
            p["step4_substeps"] = substeps
            u.setup_progress = p
            attributes.flag_modified(u, "setup_progress")
            await s.commit()

    substeps = await _read_substeps()

    # Substep 1: TRM backfill
    if substeps.get("trm_backfill") != "ok":
        tracker.emit(job_id, {"step": "trm_backfill", "status": "running"})
        try:
            result = await trm_job_mod.run(session_local, trigger="wizard", full_backfill=True)
            await _write_substep("trm_backfill", "ok")
            tracker.emit(
                job_id,
                {"step": "trm_backfill", "status": "ok", "n_days": result["n_days"]},
            )
        except Exception as e:
            await _write_substep("trm_backfill", "failed")
            tracker.emit(
                job_id, {"step": "trm_backfill", "status": "failed", "error": str(e)[:500]}
            )
            tracker.mark_done(job_id)
            return

    # Substep 2: Flex YTD
    substeps = await _read_substeps()
    if substeps.get("flex_ytd") != "ok":
        tracker.emit(job_id, {"step": "flex_ytd", "status": "running"})
        try:
            await flex_job_mod.run(session_local, user_id=user_id, trigger="wizard")
            await _write_substep("flex_ytd", "ok")
            tracker.emit(job_id, {"step": "flex_ytd", "status": "ok"})
        except Exception as e:
            await _write_substep("flex_ytd", "failed")
            tracker.emit(
                job_id, {"step": "flex_ytd", "status": "failed", "error": str(e)[:500]}
            )
            tracker.mark_done(job_id)
            return

    # Marcar setup completado
    async with session_local() as s:
        u = await s.scalar(select(User).where(User.id == user_id))
        u.setup_completed_at = datetime.now(timezone.utc)
        await s.commit()

    tracker.emit(job_id, {"step": "done", "redirect": "/dashboard"})
    tracker.mark_done(job_id)
