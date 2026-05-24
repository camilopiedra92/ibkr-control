"""POST /api/imports/upload — multipart XML upload reusado por wizard step 3 + Settings."""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.auth.backend import current_active_user
from ibkr_control.auth.models import User
from ibkr_control.db.models.flex_raw import FlexImport
from ibkr_control.db.session import get_async_session
from ibkr_control.ingest.flex import job as flex_job_mod
from ibkr_control.ingest.flex import parser as flex_parser_mod
from ibkr_control.ingest.flex._models import UnknownFlexTagError
from ibkr_control.ingest.hash_dedup import xml_hash

router = APIRouter(prefix="/imports", tags=["imports"])

MAX_XML_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB


@router.post("/upload")
async def upload_xml(
    file: UploadFile = File(...),
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    xml_bytes = await file.read()
    if len(xml_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(xml_bytes) > MAX_XML_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large: {len(xml_bytes)} bytes (max {MAX_XML_SIZE_BYTES})",
        )

    # Pre-check: hash duplicado -> 409 sin parsear
    h = xml_hash(xml_bytes)
    existing = await session.scalar(
        select(FlexImport).where(FlexImport.xml_hash == h)
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
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"XML invalido: {e}")

    # Ingest real
    flex_import_id = await flex_job_mod.ingest_xml(
        session,
        user_id=user.id,
        xml_bytes=xml_bytes,
        source="manual_upload",
        trigger="wizard",
    )

    # Pull counts del registro persistido
    fi = await session.scalar(select(FlexImport).where(FlexImport.id == flex_import_id))
    return {
        "flex_import_id": flex_import_id,
        "n_trades": fi.n_trades,
        "n_lots_closed": fi.n_lots_closed,
        "n_cash_tx": fi.n_cash_tx,
        "anyo": fi.anyo,
    }
