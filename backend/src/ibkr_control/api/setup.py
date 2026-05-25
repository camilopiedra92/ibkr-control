"""Wizard endpoints — auto-detect cuentas via Flex Web Service.

Per spec docs/specs/2026-05-24-wizard-redesign-design.md.

Endpoints:
    POST /api/setup/step1/save              save creds (no IBKR call)
    POST /api/setup/step2/detect            fetch YTD + parse + persist
    POST /api/setup/step2/detect_from_xml   parse uploaded XML (no persist)
    POST /api/setup/step2/save              persist accounts + participations
    POST /api/setup/step3/upload            stash uploaded XML
    POST /api/setup/step3/save_new_accounts persist accounts for new IDs
    POST /api/setup/step3/commit            persist all stashed XMLs
    POST /api/setup/finish                  mark setup_completed_at
    GET  /api/setup/state                   derived + stored state
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import date, datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import attributes

from ibkr_control.api._schemas import (
    DetectedAccount,
    FlexCredentialsValidate,
    Step2DetectFromXmlResponse,
    Step2DetectResponse,
    Step2SaveRequest,
    Step2SaveResponse,
    Step3CommitRequest,
    Step3CommitResponse,
    Step3SaveNewAccountsRequest,
    Step3UploadResponse,
    WizardStateResponse,
)
from ibkr_control.api._step3_stash import get_stash
from ibkr_control.auth.backend import current_active_user
from ibkr_control.auth.models import User
from ibkr_control.config import get_settings
from ibkr_control.db.models.accounts import Account
from ibkr_control.db.models.flex_credentials import FlexCredentials
from ibkr_control.db.models.flex_raw import FlexImport
from ibkr_control.db.models.participations import Participation
from ibkr_control.db.session import get_async_session, get_engine
from ibkr_control.ingest.flex import client as flex_client_mod
from ibkr_control.ingest.flex import crypto as flex_crypto_mod
from ibkr_control.ingest.flex import parser as flex_parser_mod
from ibkr_control.ingest.flex import persister as flex_persister_mod
from ibkr_control.ingest.flex._models import ParsedAccount
from ibkr_control.ingest.job_tracker import get_tracker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/setup", tags=["setup"])


# ===== Helpers =====


def _set_progress(user: User, key: str, value) -> None:
    """Mutate setup_progress dict y flagear dirty para SQLAlchemy."""
    progress = dict(user.setup_progress or {})
    progress[key] = value
    user.setup_progress = progress
    attributes.flag_modified(user, "setup_progress")


def _to_detected_account(account: ParsedAccount) -> DetectedAccount:
    return DetectedAccount(
        ibkr_account_id=account.ibkr_account_id,
        suggested_alias=account.account_alias,
        account_type=account.account_type,
        account_holder=account.name,
    )


def _is_shadow(ibkr_account_id: str) -> bool:
    """F-suffix accounts (IB-UK Limited shadow). Filtered everywhere user-facing."""
    return ibkr_account_id.endswith("F")


def _detected_from_parsed(parsed) -> list[DetectedAccount]:
    return [
        _to_detected_account(a)
        for a in parsed.accounts
        if not _is_shadow(a.ibkr_account_id)
    ]


def _ingest_summary_from_parsed(parsed) -> dict:
    """Opaque shape — frontend only renders counts; backend tests assert keys."""
    return {
        "n_trades": len(parsed.trades),
        "n_closed_lots": len(parsed.closed_lots),
        "n_open_position_lots": len(parsed.open_position_lots),
        "n_cash_transactions": len(parsed.cash_transactions),
        "n_transfers": len(parsed.transfers),
        "n_change_in_dividend_accruals": len(parsed.change_in_dividend_accruals),
        "n_open_dividend_accruals": len(parsed.open_dividend_accruals),
    }


async def _trm_backfill_background(user_id: int, job_id: int) -> None:
    """TRM full backfill post-step2/save with SSE progress reporting.

    Emits tracker events so the wizard's TrmBackfillBanner can render running /
    ok / failed states. Errors are still logged (the original safety net) but
    no longer silent — they surface as status='failed' on the SSE stream.
    """
    from ibkr_control.ingest.trm import job as trm_job_mod

    engine = get_engine()
    session_local = async_sessionmaker(engine, expire_on_commit=False)
    tracker = get_tracker()
    try:
        tracker.emit(job_id, {"step": "trm_backfill", "status": "running"})
        result = await trm_job_mod.run(
            session_local, trigger="wizard", full_backfill=True
        )
        tracker.emit(
            job_id,
            {"step": "trm_backfill", "status": "ok", "n_days": result["n_days"]},
        )
        tracker.emit(job_id, {"step": "done"})
    except Exception as e:
        # Must stay broad: this is the background-task catch-all. Any unhandled
        # exception (network, Socrata 5xx, persister DB error) must surface to
        # the wizard banner via the tracker so the user sees the failure
        # instead of a silent "Setup completado" with no TRM data.
        logger.exception("TRM backfill background for user_id=%s failed", user_id)
        tracker.emit(
            job_id,
            {"step": "trm_backfill", "status": "failed", "error": str(e)[:500]},
        )
    finally:
        tracker.mark_done(job_id)


# ===== STATE =====


@router.get("/state", response_model=WizardStateResponse)
async def get_state(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> WizardStateResponse:
    has_creds = (
        await session.scalar(
            select(func.count(FlexCredentials.user_id)).where(
                FlexCredentials.user_id == user.id
            )
        )
    ) > 0
    has_parts = (
        await session.scalar(
            select(func.count(Participation.user_id)).where(
                Participation.user_id == user.id
            )
        )
    ) > 0
    n_xmls = (
        await session.scalar(
            select(func.count(FlexImport.id)).where(
                FlexImport.user_id == user.id,
                FlexImport.source == "manual_upload",
            )
        )
    ) or 0

    p = user.setup_progress or {}
    stash = get_stash()
    pending = [e.temp_id for e in stash.list_for_user(user_id=user.id)]

    return WizardStateResponse(
        step1_credentials=has_creds,
        step2_accounts=has_parts,
        step3_xmls=p.get("step3_xmls", False),
        step3_n_xmls_uploaded=n_xmls,
        setup_completed_at=user.setup_completed_at,
        detected_accounts=None,
        pending_stash_temp_ids=pending,
    )


# ===== STEP 1 =====


@router.post("/step1/save")
async def step1_save(
    payload: FlexCredentialsValidate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Save creds (encrypt token). Does NOT call IBKR per spec D5."""
    creds = await session.scalar(
        select(FlexCredentials).where(FlexCredentials.user_id == user.id)
    )
    encrypted = flex_crypto_mod.encrypt_token(payload.token)
    if creds is None:
        creds = FlexCredentials(
            user_id=user.id,
            token_encrypted=encrypted,
            ytd_query_id=payload.query_id,
        )
        session.add(creds)
    else:
        creds.token_encrypted = encrypted
        creds.ytd_query_id = payload.query_id
        creds.last_rotated_at = datetime.now(timezone.utc)
    await session.commit()
    return {"ok": True}


# ===== STEP 2 DETECT =====


_DETECT_RETRY_DELAYS = [5, 15, 30]  # seconds; total max wait ~50s plus the calls themselves


@router.post("/step2/detect", response_model=Step2DetectResponse)
async def step2_detect(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> Step2DetectResponse:
    """Fetch + parse + persist YTD. Returns detected accounts (sin F).

    Retry policy per spec D10: server-side retry on 1001 with backoff [5, 15, 30]s.
    """
    creds = await session.scalar(
        select(FlexCredentials).where(FlexCredentials.user_id == user.id)
    )
    if creds is None:
        raise HTTPException(status_code=400, detail="MISSING_CREDENTIALS")

    token = flex_crypto_mod.decrypt_token(creds.token_encrypted)
    query_id = creds.ytd_query_id

    client = flex_client_mod.FlexClient(token=token)

    xml_bytes: bytes | None = None
    # _DETECT_RETRY_DELAYS produces 4 attempts: try, sleep 5, try, sleep 15,
    # try, sleep 30, try. After the 4th attempt we surface IBKR_BUSY.
    for attempt_idx, delay in enumerate(_DETECT_RETRY_DELAYS + [None]):
        try:
            ref = await client.send_request(query_id=query_id)
            xml_bytes = await client.get_statement(reference_code=ref)
            break
        except flex_client_mod.FlexBusyError:
            if delay is None:
                raise HTTPException(
                    status_code=503,
                    detail={"code": "IBKR_BUSY", "attempts": attempt_idx + 1},
                )
            await asyncio.sleep(delay)
        except flex_client_mod.FlexAuthError as e:
            raise HTTPException(status_code=401, detail="INVALID_TOKEN") from e
        except flex_client_mod.FlexQueryNotFoundError as e:
            raise HTTPException(status_code=400, detail="QUERY_NOT_FOUND") from e
        except flex_client_mod.FlexClientError as e:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "IBKR_ERROR",
                    "ibkr_code": getattr(e, "code", None),
                    "message": str(e)[:500],
                },
            ) from e
        except flex_client_mod.FlexPollTimeoutError as e:
            raise HTTPException(status_code=504, detail="IBKR_TIMEOUT") from e

    assert xml_bytes is not None
    try:
        parsed = flex_parser_mod.parse(xml_bytes)
    except Exception as e:  # noqa: BLE001 — surface as 422 for diagnosis
        raise HTTPException(
            status_code=422,
            detail={"code": "PARSE_ERROR", "message": str(e)[:500]},
        ) from e

    # Persist via the orchestrator-grade persister: it builds FlexImport
    # internally, dedups by xml_hash, and skips F-shadow accounts. Returns
    # the FlexImport id (existing if hash duplicate).
    flex_import_id = await flex_persister_mod.persist(
        session,
        parsed=parsed,
        user_id=user.id,
        xml_bytes=xml_bytes,
        source="web_service",
    )
    await session.commit()

    detected = _detected_from_parsed(parsed)

    return Step2DetectResponse(
        detected_accounts=detected,
        flex_import_id=flex_import_id,
        ingest_summary=_ingest_summary_from_parsed(parsed),
    )


@router.post("/step2/detect_from_xml", response_model=Step2DetectFromXmlResponse)
async def step2_detect_from_xml(
    file: UploadFile = File(...),
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> Step2DetectFromXmlResponse:
    """Fallback when IBKR is unreachable: parse uploaded XML and persist it as
    a manual upload so step2/save can validate detected accounts. The persister
    dedups by SHA-256, so re-uploads of the same XML are idempotent.
    """
    content = await file.read()
    settings = get_settings()
    if len(content) > settings.max_xml_size_bytes:
        raise HTTPException(status_code=413, detail="FILE_TOO_LARGE")
    try:
        parsed = flex_parser_mod.parse(content)
    except Exception as e:  # noqa: BLE001 — surface to user as 422
        raise HTTPException(
            status_code=422,
            detail={"code": "PARSE_ERROR", "message": str(e)[:500]},
        ) from e

    await flex_persister_mod.persist(
        session,
        parsed=parsed,
        user_id=user.id,
        xml_bytes=content,
        source="manual_upload",
    )
    await session.commit()

    return Step2DetectFromXmlResponse(
        detected_accounts=_detected_from_parsed(parsed),
        parsed_only=False,
    )


# ===== STEP 2 SAVE =====


@router.post("/step2/save", response_model=Step2SaveResponse)
async def step2_save(
    payload: Step2SaveRequest,
    background: BackgroundTasks,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> Step2SaveResponse:
    """Persist accounts + participations. Dispatches TRM backfill in background (per D6)."""
    # Validation pass: must have detected at least one flex_import OR have
    # accounts already in DB (re-entry after partial setup). Then every
    # incoming ibkr_account_id must already exist in `accounts` (it gets there
    # via step2/detect's persist() call, which skips F-shadow accounts).
    flex_imports_count = await session.scalar(
        select(func.count(FlexImport.id)).where(FlexImport.user_id == user.id)
    )
    if not flex_imports_count:
        raise HTTPException(status_code=400, detail="NO_DETECT_YET")

    existing_account_ids = {
        a.ibkr_account_id for a in (await session.scalars(select(Account))).all()
    }

    for item in payload.accounts:
        if _is_shadow(item.ibkr_account_id):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "SHADOW_ACCOUNT_REJECTED",
                    "ibkr_account_id": item.ibkr_account_id,
                },
            )
        if item.ibkr_account_id not in existing_account_ids:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "ACCOUNT_NOT_DETECTED",
                    "ibkr_account_id": item.ibkr_account_id,
                },
            )

    today = date.today()
    for item in payload.accounts:
        acc = await session.scalar(
            select(Account).where(Account.ibkr_account_id == item.ibkr_account_id)
        )
        # acc must exist because validation above verified detected_ids
        if item.alias is not None:
            acc.alias = item.alias

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

    await session.commit()
    # Register the job BEFORE add_task so the wizard banner can subscribe to
    # /api/ingest/stream/{job_id} the moment it receives this response without
    # racing the background task start (D12 fix — was fire-and-forget).
    job_id = get_tracker().create_job()
    background.add_task(_trm_backfill_background, user_id=user.id, job_id=job_id)
    return Step2SaveResponse(ok=True, trm_backfill_job_id=job_id)


# ===== STEP 3 =====


@router.post("/step3/upload", response_model=Step3UploadResponse)
async def step3_upload(
    file: UploadFile = File(...),
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> Step3UploadResponse:
    """Stash one XML in memory. Detect new accounts vs already-configured."""
    content = await file.read()
    settings = get_settings()
    if len(content) > settings.max_xml_size_bytes:
        raise HTTPException(status_code=413, detail="FILE_TOO_LARGE")

    sha = hashlib.sha256(content).hexdigest()

    existing_import = await session.scalar(
        select(FlexImport).where(FlexImport.xml_hash == sha)
    )
    if existing_import is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "DUPLICATE_XML", "flex_import_id": existing_import.id},
        )

    stash = get_stash()
    if stash.find_by_sha256(user_id=user.id, sha256=sha) is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "DUPLICATE_XML_STASHED"},
        )

    try:
        parsed = flex_parser_mod.parse(content)
    except Exception as e:  # noqa: BLE001 — surface to user as 422
        raise HTTPException(
            status_code=422,
            detail={"code": "PARSE_ERROR", "message": str(e)[:500]},
        ) from e

    existing_accounts = {
        a.ibkr_account_id for a in (await session.scalars(select(Account))).all()
    }

    detected = _detected_from_parsed(parsed)
    new_accounts = [
        da for da in detected if da.ibkr_account_id not in existing_accounts
    ]

    period_from = parsed.period_from
    period_to = parsed.period_to
    anyo = parsed.anyo

    temp_id = stash.put(
        user_id=user.id,
        data={
            "parsed": parsed,
            "xml_bytes": content,
            "size_bytes": len(content),
            "anyo": anyo,
            "period_from": period_from,
            "period_to": period_to,
        },
        sha256=sha,
    )

    return Step3UploadResponse(
        flex_import_temp_id=temp_id,
        detected_accounts=detected,
        new_accounts=new_accounts,
        period={"from": period_from.isoformat(), "to": period_to.isoformat()},
        anyo=anyo,
        sha256=sha,
    )


@router.post("/step3/save_new_accounts")
async def step3_save_new_accounts(
    payload: Step3SaveNewAccountsRequest,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Persist accounts + participations for newly-detected IDs from Step 3 uploads."""
    today = date.today()
    for item in payload.accounts:
        if _is_shadow(item.ibkr_account_id):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "SHADOW_ACCOUNT_REJECTED",
                    "ibkr_account_id": item.ibkr_account_id,
                },
            )
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
    await session.commit()
    return {"ok": True}


@router.post("/step3/commit", response_model=Step3CommitResponse)
async def step3_commit(
    payload: Step3CommitRequest,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> Step3CommitResponse:
    """Drain stash, persist all selected XMLs in one transaction.

    Validates first that every detected (non-shadow) account in each stashed
    XML is already configured (via step3/save_new_accounts or earlier steps).
    """
    stash = get_stash()
    flex_import_ids: list[int] = []
    total_rows = 0

    # Validate first pass: all temp_ids exist + no unresolved new accounts
    existing_accounts = {
        a.ibkr_account_id for a in (await session.scalars(select(Account))).all()
    }
    for temp_id in payload.temp_ids:
        entry = stash.get(user_id=user.id, temp_id=temp_id)
        if entry is None:
            raise HTTPException(
                status_code=410,
                detail={"code": "TEMP_ID_EXPIRED", "temp_id": temp_id},
            )
        for a in entry.data["parsed"].accounts:
            if _is_shadow(a.ibkr_account_id):
                continue
            if a.ibkr_account_id not in existing_accounts:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "UNRESOLVED_NEW_ACCOUNTS",
                        "ibkr_account_id": a.ibkr_account_id,
                    },
                )

    # Persist pass: delegate to the real persister which handles FlexImport
    # creation, dedup, and shadow-account filtering. We tally rows from the
    # ParsedXML (same shape the persister uses for n_* counts).
    for temp_id in payload.temp_ids:
        entry = stash.pop(user_id=user.id, temp_id=temp_id)
        if entry is None:
            continue  # belt-and-suspenders; validate pass already checked
        data = entry.data
        parsed = data["parsed"]
        flex_import_id = await flex_persister_mod.persist(
            session,
            parsed=parsed,
            user_id=user.id,
            xml_bytes=data["xml_bytes"],
            source="manual_upload",
        )
        flex_import_ids.append(flex_import_id)
        total_rows += sum(_ingest_summary_from_parsed(parsed).values())

    _set_progress(user, "step3_xmls", True)
    await session.commit()
    return Step3CommitResponse(
        flex_import_ids=flex_import_ids,
        total_rows_inserted=total_rows,
    )


# ===== FINISH =====


@router.post("/finish")
async def finish(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Mark setup_completed_at. Validates preconditions (creds + at least 1 participation + step3 marked)."""
    if user.setup_completed_at is not None:
        return {"ok": True, "already_completed": True}

    has_parts = (
        await session.scalar(
            select(func.count(Participation.user_id)).where(
                Participation.user_id == user.id
            )
        )
    ) > 0
    if not has_parts:
        raise HTTPException(status_code=400, detail="INCOMPLETE_SETUP")

    p = user.setup_progress or {}
    if not p.get("step3_xmls"):
        raise HTTPException(status_code=400, detail="INCOMPLETE_SETUP")

    user.setup_completed_at = datetime.now(timezone.utc)
    await session.commit()
    return {"ok": True}
