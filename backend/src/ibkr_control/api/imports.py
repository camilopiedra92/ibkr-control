"""POST /api/imports/upload — multipart XML upload reusado por wizard step 3 + Settings."""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from lxml.etree import XMLSyntaxError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.api._context import org_context
from ibkr_control.auth.backend import current_active_user
from ibkr_control.auth.models import User
from ibkr_control.config import get_settings
from ibkr_control.db.models.flex_raw import FlexImport
from ibkr_control.db.session import get_async_session
from ibkr_control.ingest.flex import job as flex_job_mod
from ibkr_control.ingest.flex import parser as flex_parser_mod
from ibkr_control.ingest.flex._models import UnknownFlexTagError
from ibkr_control.ingest.hash_dedup import xml_hash

router = APIRouter(prefix="/imports", tags=["imports"])

_CHUNK_SIZE = 64 * 1024  # 64 KB streaming chunks


@router.post("/upload")
async def upload_xml(
    file: UploadFile = File(...),
    user: User = Depends(current_active_user),
    org_id: int = Depends(org_context),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    settings = get_settings()
    max_size = settings.max_xml_size_bytes

    # Check declared size first (cheap, before any I/O).
    # UploadFile.size is populated from Content-Length by Starlette.
    if file.size is not None:
        if file.size == 0:
            raise HTTPException(status_code=400, detail="Empty file")
        if file.size > max_size:
            raise HTTPException(
                status_code=413,
                detail=f"File too large: {file.size} bytes (max {max_size})",
            )

    # Stream read with cumulative size cap — defense in depth: client may lie
    # about Content-Length (or omit it entirely, leaving file.size as None).
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_size:
            raise HTTPException(
                status_code=413,
                detail=f"File too large during upload (>{max_size} bytes)",
            )
        chunks.append(chunk)
    xml_bytes = b"".join(chunks)

    if len(xml_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty file")

    # Pre-check: hash duplicado -> 409 sin parsear. Dedup es per-org: que otro
    # org haya subido el mismo XML NO lo hace duplicado para este org.
    h = xml_hash(xml_bytes)
    existing = await session.scalar(
        select(FlexImport).where(FlexImport.organization_id == org_id, FlexImport.xml_hash == h)
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "flex_import_id": existing.id,
                "fetched_at": existing.fetched_at.isoformat(),
                "message": "Este XML ya fue importado",
            },
        )

    # Pre-check: parser sintactico + audit de tags conocidos
    try:
        flex_parser_mod.parse(xml_bytes)
    except UnknownFlexTagError as e:
        raise HTTPException(status_code=400, detail=f"XML tiene tag desconocido: {e.tag}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except XMLSyntaxError as e:
        raise HTTPException(status_code=400, detail=f"XML invalido: {e}")

    # Ingest real. job.ingest_xml internally destructures the persister tuple
    # and returns only the flex_import_id; we re-pull the FlexImport row to
    # expose both n_observed_* (what the XML carried) and n_new_* (what
    # actually hit DB — 0s on dedup'd re-uploads) per spec A5.
    flex_import_id = await flex_job_mod.ingest_xml(
        session,
        organization_id=org_id,
        xml_bytes=xml_bytes,
        source="manual_upload",
        trigger="wizard",
    )

    # ingest_xml commits internally (via ingest_log_entry), ending the transaction.
    # The follow-up read below autobegins a fresh transaction; the after_begin RLS
    # listener (db/rls.py) re-applies app.current_org from the session's stashed
    # context, so the just-written FlexImport is visible (org-scoped) without a
    # manual re-apply.
    fi = await session.scalar(select(FlexImport).where(FlexImport.id == flex_import_id))
    return {
        "flex_import_id": flex_import_id,
        "anyo": fi.anyo,
        "n_observed_trades": fi.n_observed_trades or 0,
        "n_observed_lots_closed": fi.n_observed_lots_closed or 0,
        "n_observed_open_lots": fi.n_observed_open_lots or 0,
        "n_observed_cash_tx": fi.n_observed_cash_tx or 0,
        "n_observed_dividends": fi.n_observed_dividends or 0,
        "n_observed_transfers": fi.n_observed_transfers or 0,
        "n_new_trades": fi.n_new_trades or 0,
        "n_new_lots_closed": fi.n_new_lots_closed or 0,
        "n_new_open_lots": fi.n_new_open_lots or 0,
        "n_new_cash_tx": fi.n_new_cash_tx or 0,
        "n_new_dividends": fi.n_new_dividends or 0,
        "n_new_transfers": fi.n_new_transfers or 0,
    }
