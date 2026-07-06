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
    IngestCounters,
    SetupConnectionPayload,
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
from ibkr_control.authz import AuthzContext, require_scope
from ibkr_control.config import get_settings
from ibkr_control.db.models.accounts import Account
from ibkr_control.db.models.connections import Connection, ConnectionIbkrFlex
from ibkr_control.db.models.flex_raw import FlexImport, FlexImportAccount
from ibkr_control.db.models.institutions import Institution
from ibkr_control.db.models.organizations import Organization
from ibkr_control.db.models.parties import Party
from ibkr_control.db.models.participations import Participation
from ibkr_control.db.participations import upsert_participation
from ibkr_control.db.session import get_async_session, get_engine
from ibkr_control.ingest import connection_state
from ibkr_control.ingest.flex import client as flex_client_mod
from ibkr_control.ingest.flex import crypto as flex_crypto_mod
from ibkr_control.ingest.flex import parser as flex_parser_mod
from ibkr_control.ingest.flex import persister as flex_persister_mod
from ibkr_control.ingest.flex._models import ParsedAccount
from ibkr_control.ingest.job_tracker import get_tracker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/setup", tags=["setup"])


# ===== Helpers =====


def _set_progress(org: Organization, key: str, value) -> None:
    """Mutate the org's setup_progress dict y flagear dirty para SQLAlchemy.

    Setup state is per-org (Task 5 moved it off User onto Organization)."""
    progress = dict(org.setup_progress or {})
    progress[key] = value
    org.setup_progress = progress
    attributes.flag_modified(org, "setup_progress")


async def _founding_party_id(session: AsyncSession, org_id: int, user_id: int) -> int:
    """The org's founding party for this user (SP1 minimal: one party per
    founding user). Every participation the wizard writes is anchored to it;
    multi-party selection UI is SP3. It always exists post-provision, so a
    missing row is a server-side invariant break, not a client error."""
    party_id = await session.scalar(
        select(Party.id).where(Party.organization_id == org_id, Party.user_id == user_id)
    )
    if party_id is None:
        raise HTTPException(status_code=500, detail="NO_FOUNDING_PARTY")
    return party_id


async def _ibkr_flex_connections(session: AsyncSession) -> list[Connection]:
    """The org's ibkr_flex connections, ordered by id (RLS scopes by org).

    Wizard treats the FIRST (lowest id) as the canonical setup connection
    (step1 rotates it). detect iterates all that are not disabled."""
    return list(
        (
            await session.scalars(
                select(Connection)
                .where(Connection.provider_type == "ibkr_flex")
                .order_by(Connection.id)
            )
        ).all()
    )


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


async def _org_imported_account_ids(session: AsyncSession, org_id: int) -> set[str]:
    """ibkr_account_ids that appear in this org's Flex imports.

    Provenance gate: an account is claimable only if it appears in a FlexImport
    belonging to THIS org — recorded in the flex_import_accounts provenance
    table (includes AccountInformation-only accounts with no facts). Cross-org
    isolation is enforced structurally by org-scoping here (and RLS, Task 14);
    this query keeps the wizard from claiming an account the org never imported.
    """
    rows = await session.scalars(
        select(Account.ibkr_account_id)
        .join(FlexImportAccount, FlexImportAccount.account_id == Account.id)
        .join(FlexImport, FlexImport.id == FlexImportAccount.flex_import_id)
        .where(FlexImport.organization_id == org_id)
    )
    return set(rows.all())


async def _org_configured_account_ids(session: AsyncSession, org_id: int) -> set[str]:
    """ibkr_account_ids this org has an open participation on ('configured').

    Used by step3/commit: an XML may only be committed once every non-shadow
    account it references has been resolved (alias + pct via step2/save or
    step3/save_new_accounts). Scopes the completeness gate to the org's own
    participations rather than the shared `accounts` table, where an account
    another org created would pass falsely."""
    rows = await session.scalars(
        select(Account.ibkr_account_id)
        .join(Participation, Participation.account_id == Account.id)
        .where(Participation.organization_id == org_id, Participation.valid_to.is_(None))
    )
    return set(rows.all())


def _user_stashed_account_ids(user_id: int) -> set[str]:
    """Non-shadow ibkr_account_ids across the user's own Step 3 stash uploads.

    Provenance scope for step3/save_new_accounts: only accounts that appear in
    an XML the uploading user actually staged are claimable — never an arbitrary
    id they POST. The stash is an in-memory, per-user session concept
    (list_for_user), so it stays keyed by the uploading user, not the org."""
    ids: set[str] = set()
    for entry in get_stash().list_for_user(user_id=user_id):
        for a in entry.data["parsed"].accounts:
            if not _is_shadow(a.ibkr_account_id):
                ids.add(a.ibkr_account_id)
    return ids


def _detected_from_parsed(parsed) -> list[DetectedAccount]:
    return [_to_detected_account(a) for a in parsed.accounts if not _is_shadow(a.ibkr_account_id)]


def _counters_to_ingest(counters: dict) -> IngestCounters:
    """Build IngestCounters from the persister's counters dict.

    The persister returns a dict with `hash_dedup` and (when not deduped)
    n_observed_* + n_new_* keys. On hash_dedup fast-path, only `hash_dedup`
    is present; all counter fields default to 0 (per spec A4: nothing was
    written, so n_new are all 0; observed counts are also 0 because we
    didn't re-parse).
    """
    return IngestCounters(**counters)


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
        result = await trm_job_mod.run(session_local, trigger="wizard", full_backfill=True)
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
    ctx: AuthzContext = Depends(require_scope("setup:write")),
    session: AsyncSession = Depends(get_async_session),
) -> WizardStateResponse:
    has_creds = (
        await session.scalar(
            select(func.count(Connection.id)).where(Connection.provider_type == "ibkr_flex")
        )
    ) > 0
    has_parts = (
        await session.scalar(
            select(func.count())
            .select_from(Participation)
            .where(Participation.organization_id == ctx.org_id)
        )
    ) > 0
    n_xmls = (
        await session.scalar(
            select(func.count(FlexImport.id)).where(
                FlexImport.organization_id == ctx.org_id,
                FlexImport.source == "manual_upload",
            )
        )
    ) or 0

    org = await session.get(Organization, ctx.org_id)
    p = org.setup_progress or {}
    stash = get_stash()
    pending = [e.temp_id for e in stash.list_for_user(user_id=ctx.user_id)]

    return WizardStateResponse(
        step1_credentials=has_creds,
        step2_accounts=has_parts,
        step3_xmls=p.get("step3_xmls", False),
        step3_n_xmls_uploaded=n_xmls,
        setup_completed_at=org.setup_completed_at,
        detected_accounts=None,
        pending_stash_temp_ids=pending,
    )


# ===== STEP 1 =====


@router.post("/step1/save")
async def step1_save(
    payload: SetupConnectionPayload,
    ctx: AuthzContext = Depends(require_scope("setup:write")),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Save the org's ibkr_flex connection (encrypt token). Does NOT call IBKR
    per spec D5 — the wizard validates the token later in step2/detect.

    No connection yet → create Connection (status='active') + ConnectionIbkrFlex
    detail. Else → update the FIRST connection's detail (token/query_id/
    last_rotated_at) and run mark_rotated (clears reauth_required back to active).
    Idempotent: a re-save updates, never duplicates.
    """
    encrypted = flex_crypto_mod.encrypt_token(payload.token)
    conns = await _ibkr_flex_connections(session)
    if not conns:
        inst_id = await session.scalar(select(Institution.id).where(Institution.code == "ibkr"))
        if inst_id is None:
            # Seed roto (la migracion baseline inserta 'ibkr') -- fail-loud.
            raise HTTPException(status_code=500, detail="Institution 'ibkr' seed missing")
        conn = Connection(
            organization_id=ctx.org_id,
            institution_id=inst_id,
            provider_type="ibkr_flex",
            display_name=payload.display_name,
        )
        session.add(conn)
        await session.flush()
        session.add(
            ConnectionIbkrFlex(
                connection_id=conn.id,
                organization_id=ctx.org_id,
                token_encrypted=encrypted,
                query_id=payload.query_id,
            )
        )
    else:
        conn = conns[0]
        detail = await session.get(ConnectionIbkrFlex, conn.id)
        if detail is None:
            # Invariante de subtipo roto -- fail-loud, no degradar en silencio.
            raise HTTPException(status_code=500, detail="Connection detail missing")
        detail.token_encrypted = encrypted
        detail.query_id = payload.query_id
        detail.last_rotated_at = datetime.now(timezone.utc)
        if payload.display_name is not None:
            conn.display_name = payload.display_name
        connection_state.mark_rotated(conn)
    await session.commit()
    return {"ok": True}


# ===== STEP 2 DETECT =====


_DETECT_RETRY_DELAYS = [5, 15, 30]  # seconds; total max wait ~50s plus the calls themselves


async def _fetch_statement_with_retry(client, query_id: str) -> bytes:
    """SendRequest + GetStatement for one connection, retrying 1001 BUSY per
    spec D10 (backoff [5,15,30]s). Raises the typed flex_client exception so the
    detect loop can classify it (auth-class vs transient). On retry exhaustion
    re-raises FlexBusyError (transient)."""
    # _DETECT_RETRY_DELAYS produces 4 attempts: try, sleep 5, try, sleep 15,
    # try, sleep 30, try. After the 4th attempt the BUSY propagates.
    for delay in _DETECT_RETRY_DELAYS + [None]:
        try:
            ref = await client.send_request(query_id=query_id)
            return await client.get_statement(reference_code=ref)
        except flex_client_mod.FlexBusyError:
            if delay is None:
                raise
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def _classify_detect_failure(exc: Exception):
    """Mapea una excepción del fetch/parse/persist de UNA connection a
    (transition_fn, failure_class). Espejo de la clasificación del job loop de
    Task 4 (ingest/flex/job.py::run, CR-3): auth-class (token inválido o
    query_id mal configurado) requiere acción del usuario → mark_auth_failed
    (reauth_required); todo lo demás es transitorio → mark_sync_failed.

    failure_class alimenta la precedencia del error agregado cuando CERO
    connections produjeron cuentas: 'auth' → 401, 'data' (parse/persist —
    comparten el mismo catch, igual que en el job loop) → 422 PARSE_ERROR,
    'transient' (busy/transporte) → 503 IBKR_BUSY.
    """
    if isinstance(exc, (flex_client_mod.FlexAuthError, flex_client_mod.FlexQueryNotFoundError)):
        return connection_state.mark_auth_failed, "auth"
    if isinstance(exc, (flex_client_mod.FlexClientError, flex_client_mod.FlexPollTimeoutError)):
        # Incluye FlexBusyError (subclase de FlexClientError, retries agotados).
        return connection_state.mark_sync_failed, "transient"
    # No es una excepción del cliente Flex → vino del parse o del persist.
    return connection_state.mark_sync_failed, "data"


@router.post("/step2/detect", response_model=Step2DetectResponse)
async def step2_detect(
    ctx: AuthzContext = Depends(require_scope("setup:write")),
    session: AsyncSession = Depends(get_async_session),
) -> Step2DetectResponse:
    """Fetch + parse + persist YTD across ALL active connections (W1).

    Iterates the org's non-disabled ibkr_flex connections, accumulating detected
    accounts deduped by ibkr_account_id. detect es partial-tolerant (espejo del
    job loop de Task 4): CUALQUIER fallo per-connection (auth, busy, transporte,
    parse, persist) transiciona SU estado vía connection_state + commit y el
    loop continúa; el error agregado solo se lanza si CERO connections
    produjeron cuentas. Precedencia del agregado: any auth-class → 401
    INVALID_TOKEN; elif any parse/persist → 422 PARSE_ERROR; else → 503
    IBKR_BUSY (misma shape que antes).

    Retry policy per spec D10: server-side retry on 1001 with backoff [5,15,30]s
    per connection.
    """
    conns = await _ibkr_flex_connections(session)
    # Capturar los ids ANTES del loop: un rollback per-connection (fallo de
    # persist) expira TODOS los objetos ORM de la sesión; iterar por id y
    # re-cargar con session.get evita accesos a instancias expiradas
    # (MissingGreenlet bajo asyncio).
    conn_ids = [c.id for c in conns if c.status != "disabled"]
    if not conn_ids:
        raise HTTPException(status_code=400, detail="MISSING_CREDENTIALS")

    detected_by_id: dict[str, DetectedAccount] = {}
    last_import_id: int | None = None
    last_counters: dict | None = None
    failure_classes: set[str] = set()
    data_error_message: str | None = None
    any_success = False

    for conn_id in conn_ids:
        conn = await session.get(Connection, conn_id)
        detail = await session.get(ConnectionIbkrFlex, conn_id)
        if detail is None:
            # Invariante de subtipo roto -- fail-loud, no degradar en silencio.
            raise HTTPException(status_code=500, detail="Connection detail missing")
        token = flex_crypto_mod.decrypt_token(detail.token_encrypted)
        client = flex_client_mod.FlexClient(token=token)
        try:
            xml_bytes = await _fetch_statement_with_retry(client, detail.query_id)
            parsed = flex_parser_mod.parse(xml_bytes)
            # Persist via the orchestrator-grade persister: builds FlexImport
            # internally, dedups by xml_hash, and skips F-shadow accounts.
            # Returns (flex_import_id, counters_dict) per spec A5.
            flex_import_id, counters = await flex_persister_mod.persist(
                session,
                parsed=parsed,
                organization_id=ctx.org_id,
                xml_bytes=xml_bytes,
                source="web_service",
                connection_id=conn.id,
            )
        except Exception as e:  # noqa: BLE001 — espejo del broad catch del job loop
            # Un persist fallido puede dejar la transacción abortada: rollback
            # ANTES de la transición de estado para que el commit de abajo
            # corra en una transacción limpia (el GUC RLS se re-aplica vía el
            # listener after_begin, PR #7). El rollback expira el objeto conn;
            # refresh explícito (async) antes de mutarlo — el acceso implícito
            # lanzaría MissingGreenlet.
            await session.rollback()
            await session.refresh(conn)
            transition, failure_class = _classify_detect_failure(e)
            transition(conn, reason=str(e)[:500], now=datetime.now(timezone.utc))
            await session.commit()
            failure_classes.add(failure_class)
            if failure_class == "data" and data_error_message is None:
                data_error_message = str(e)[:500]
            continue

        connection_state.mark_sync_ok(conn, now=datetime.now(timezone.utc))
        await session.commit()

        any_success = True
        last_import_id = flex_import_id
        last_counters = counters
        for da in _detected_from_parsed(parsed):
            detected_by_id.setdefault(da.ibkr_account_id, da)

    if not any_success:
        if "auth" in failure_classes:
            raise HTTPException(status_code=401, detail="INVALID_TOKEN")
        if "data" in failure_classes:
            raise HTTPException(
                status_code=422,
                detail={"code": "PARSE_ERROR", "message": data_error_message or ""},
            )
        # All transient/busy.
        raise HTTPException(
            status_code=503,
            detail={"code": "IBKR_BUSY", "attempts": len(_DETECT_RETRY_DELAYS) + 1},
        )

    assert last_import_id is not None and last_counters is not None
    return Step2DetectResponse(
        detected_accounts=list(detected_by_id.values()),
        flex_import_id=last_import_id,
        ingest_summary=_counters_to_ingest(last_counters),
    )


@router.post("/step2/detect_from_xml", response_model=Step2DetectFromXmlResponse)
async def step2_detect_from_xml(
    file: UploadFile = File(...),
    ctx: AuthzContext = Depends(require_scope("setup:write")),
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

    # Persister returns (flex_import_id, counters_dict); this endpoint doesn't
    # surface either to the response shape, but we still need to consume the
    # tuple cleanly so future linter doesn't flag the discard.
    _flex_import_id, _counters = await flex_persister_mod.persist(
        session,
        parsed=parsed,
        organization_id=ctx.org_id,
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
    ctx: AuthzContext = Depends(require_scope("setup:write")),
    session: AsyncSession = Depends(get_async_session),
) -> Step2SaveResponse:
    """Persist accounts + participations. Dispatches TRM backfill in background (per D6)."""
    # Validation pass: must have detected at least one flex_import for this org
    # (re-entry after partial setup also OK). Then every incoming
    # ibkr_account_id must already exist in `accounts` (it gets there via
    # step2/detect's persist() call, which skips F-shadow accounts).
    flex_imports_count = await session.scalar(
        select(func.count(FlexImport.id)).where(FlexImport.organization_id == ctx.org_id)
    )
    if not flex_imports_count:
        raise HTTPException(status_code=400, detail="NO_DETECT_YET")

    # Provenance gate: only accounts that appear in THIS org's imports are
    # claimable — not the whole shared `accounts` table.
    existing_account_ids = await _org_imported_account_ids(session, ctx.org_id)
    party_id = await _founding_party_id(session, ctx.org_id, ctx.user_id)

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
            select(Account).where(
                Account.organization_id == ctx.org_id,
                Account.ibkr_account_id == item.ibkr_account_id,
            )
        )
        # acc must exist because validation above verified detected_ids
        if item.alias is not None:
            acc.alias = item.alias

        await upsert_participation(
            session,
            party_id=party_id,
            account_id=acc.id,
            organization_id=ctx.org_id,
            pct=item.pct,
            at=today,
        )

    await session.commit()
    # Register the job BEFORE add_task so the wizard banner can subscribe to
    # /api/ingest/stream/{job_id} the moment it receives this response without
    # racing the background task start (D12 fix — was fire-and-forget).
    job_id = get_tracker().create_job(user_id=ctx.user_id)
    background.add_task(_trm_backfill_background, user_id=ctx.user_id, job_id=job_id)
    return Step2SaveResponse(ok=True, trm_backfill_job_id=job_id)


# ===== STEP 3 =====


@router.post("/step3/upload", response_model=Step3UploadResponse)
async def step3_upload(
    file: UploadFile = File(...),
    ctx: AuthzContext = Depends(require_scope("setup:write")),
    session: AsyncSession = Depends(get_async_session),
) -> Step3UploadResponse:
    """Stash one XML in memory. Detect new accounts vs already-configured."""
    content = await file.read()
    settings = get_settings()
    if len(content) > settings.max_xml_size_bytes:
        raise HTTPException(status_code=413, detail="FILE_TOO_LARGE")

    sha = hashlib.sha256(content).hexdigest()

    existing_import = await session.scalar(
        select(FlexImport).where(
            FlexImport.organization_id == ctx.org_id, FlexImport.xml_hash == sha
        )
    )
    if existing_import is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "DUPLICATE_XML", "flex_import_id": existing_import.id},
        )

    stash = get_stash()
    if stash.find_by_sha256(user_id=ctx.user_id, sha256=sha) is not None:
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
        a.ibkr_account_id
        for a in (
            await session.scalars(select(Account).where(Account.organization_id == ctx.org_id))
        ).all()
    }

    detected = _detected_from_parsed(parsed)
    new_accounts = [da for da in detected if da.ibkr_account_id not in existing_accounts]

    period_from = parsed.period_from
    period_to = parsed.period_to
    anyo = parsed.anyo

    temp_id = stash.put(
        user_id=ctx.user_id,
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
    ctx: AuthzContext = Depends(require_scope("setup:write")),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Persist accounts + participations for newly-detected IDs from Step 3 uploads."""
    # Provenance gate: only ids the user actually uploaded in Step 3 are
    # claimable — guard before any create/mutate so a guessed id can neither
    # fabricate an account nor grant participation.
    stashed_ids = _user_stashed_account_ids(ctx.user_id)
    party_id = await _founding_party_id(session, ctx.org_id, ctx.user_id)
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
        if item.ibkr_account_id not in stashed_ids:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "ACCOUNT_NOT_DETECTED",
                    "ibkr_account_id": item.ibkr_account_id,
                },
            )
        acc = await session.scalar(
            select(Account).where(
                Account.organization_id == ctx.org_id,
                Account.ibkr_account_id == item.ibkr_account_id,
            )
        )
        if acc is None:
            acc = Account(
                organization_id=ctx.org_id,
                ibkr_account_id=item.ibkr_account_id,
                alias=item.alias,
                currency="USD",
            )
            session.add(acc)
            await session.flush()
        elif item.alias is not None:
            acc.alias = item.alias

        await upsert_participation(
            session,
            party_id=party_id,
            account_id=acc.id,
            organization_id=ctx.org_id,
            pct=item.pct,
            at=today,
        )
    await session.commit()
    return {"ok": True}


@router.post("/step3/commit", response_model=Step3CommitResponse)
async def step3_commit(
    payload: Step3CommitRequest,
    ctx: AuthzContext = Depends(require_scope("setup:write")),
    session: AsyncSession = Depends(get_async_session),
) -> Step3CommitResponse:
    """Drain stash, persist all selected XMLs in one transaction.

    Validates first that every detected (non-shadow) account in each stashed
    XML is already configured (via step3/save_new_accounts or earlier steps).
    """
    stash = get_stash()
    flex_import_ids: list[int] = []
    total_rows = 0

    # Validate first pass: all temp_ids exist + every referenced account was
    # resolved by THIS ORG (has a participation) — provenance/isolation scope,
    # not mere global existence.
    configured_accounts = await _org_configured_account_ids(session, ctx.org_id)
    for temp_id in payload.temp_ids:
        entry = stash.get(user_id=ctx.user_id, temp_id=temp_id)
        if entry is None:
            raise HTTPException(
                status_code=410,
                detail={"code": "TEMP_ID_EXPIRED", "temp_id": temp_id},
            )
        for a in entry.data["parsed"].accounts:
            if _is_shadow(a.ibkr_account_id):
                continue
            if a.ibkr_account_id not in configured_accounts:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "UNRESOLVED_NEW_ACCOUNTS",
                        "ibkr_account_id": a.ibkr_account_id,
                    },
                )

    # Persist pass: delegate to the real persister which handles FlexImport
    # creation, dedup, and shadow-account filtering. Tally `n_new_*` from the
    # persister's counters (rows that actually hit DB — excludes hash_dedup
    # fast-paths and dedup'd children).
    _NEW_KEYS = (
        "n_new_trades",
        "n_new_lots_closed",
        "n_new_open_lots",
        "n_new_cash_tx",
        "n_new_dividends",
        "n_new_transfers",
    )
    for temp_id in payload.temp_ids:
        entry = stash.pop(user_id=ctx.user_id, temp_id=temp_id)
        if entry is None:
            continue  # belt-and-suspenders; validate pass already checked
        data = entry.data
        parsed = data["parsed"]
        flex_import_id, counters = await flex_persister_mod.persist(
            session,
            parsed=parsed,
            organization_id=ctx.org_id,
            xml_bytes=data["xml_bytes"],
            source="manual_upload",
        )
        flex_import_ids.append(flex_import_id)
        total_rows += sum(counters.get(k, 0) for k in _NEW_KEYS)

    org = await session.get(Organization, ctx.org_id)
    _set_progress(org, "step3_xmls", True)
    await session.commit()
    return Step3CommitResponse(
        flex_import_ids=flex_import_ids,
        total_rows_inserted=total_rows,
    )


# ===== FINISH =====


@router.post("/finish")
async def finish(
    ctx: AuthzContext = Depends(require_scope("setup:write")),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Mark setup_completed_at. Validates preconditions (creds + at least 1 participation + step3 marked)."""
    org = await session.get(Organization, ctx.org_id)
    if org.setup_completed_at is not None:
        return {"ok": True, "already_completed": True}

    has_parts = (
        await session.scalar(
            select(func.count())
            .select_from(Participation)
            .where(Participation.organization_id == ctx.org_id)
        )
    ) > 0
    if not has_parts:
        raise HTTPException(status_code=400, detail="INCOMPLETE_SETUP")

    p = org.setup_progress or {}
    if not p.get("step3_xmls"):
        raise HTTPException(status_code=400, detail="INCOMPLETE_SETUP")

    org.setup_completed_at = datetime.now(timezone.utc)
    await session.commit()
    return {"ok": True}
