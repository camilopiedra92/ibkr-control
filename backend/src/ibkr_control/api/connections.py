"""Router /api/connections -- CRUD + rotate + enable/disable (W1+W4, T1-D15).

Reemplaza /api/credentials/flex sin alias (pre-deploy, sin consumidores
externos). El token nunca sale en responses. Las transiciones de status pasan
por ingest/connection_state.py -- este router nunca asigna status directo.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.api._schemas import (
    ConnectionCreate,
    ConnectionRead,
    ConnectionRotate,
    ConnectionUpdate,
)
from ibkr_control.authz import AuthzContext, require_scope
from ibkr_control.db.models.connections import Connection, ConnectionIbkrFlex
from ibkr_control.db.models.institutions import Institution
from ibkr_control.db.session import get_async_session
from ibkr_control.ingest import connection_state
from ibkr_control.ingest.flex import client as flex_client_mod
from ibkr_control.ingest.flex import crypto as flex_crypto_mod

router = APIRouter(prefix="/connections", tags=["connections"])


async def _get_or_404(session: AsyncSession, connection_id: int) -> Connection:
    # RLS ya scopea por org; 404 cubre inexistente y cross-org por igual
    # (no filtra existencia -- mismo criterio D1/SSE).
    conn = await session.get(Connection, connection_id)
    if conn is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return conn


async def _detail_or_500(session: AsyncSession, connection_id: int) -> ConnectionIbkrFlex:
    detail = await session.get(ConnectionIbkrFlex, connection_id)
    if detail is None:
        # Invariante de subtipo roto -- fail-loud, no degradar en silencio.
        raise HTTPException(status_code=500, detail="Connection detail missing")
    return detail


async def _validate_token_against_ibkr(token: str, query_id: str) -> None:
    try:
        client = flex_client_mod.FlexClient(token=token)
        await client.send_request(query_id=query_id)
    except flex_client_mod.FlexAuthError as e:
        raise HTTPException(status_code=401, detail=f"Token invalido: {e.error_message}") from e
    except flex_client_mod.FlexQueryNotFoundError as e:
        raise HTTPException(status_code=400, detail=f"Query ID invalido: {e.error_message}") from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"No pude alcanzar IBKR: {e}") from e


async def _serialize(session: AsyncSession, conn: Connection) -> ConnectionRead:
    detail = await _detail_or_500(session, conn.id)
    code = await session.scalar(
        select(Institution.code).where(Institution.id == conn.institution_id)
    )
    return ConnectionRead(
        id=conn.id,
        institution_code=code,
        provider_type=conn.provider_type,
        display_name=conn.display_name,
        status=conn.status,
        status_reason=conn.status_reason,
        last_sync_at=conn.last_sync_at,
        last_sync_status=conn.last_sync_status,
        consecutive_failures=conn.consecutive_failures,
        query_id=detail.query_id,
        last_rotated_at=detail.last_rotated_at,
        created_at=conn.created_at,
    )


@router.get("", response_model=list[ConnectionRead])
async def list_connections(
    ctx: AuthzContext = Depends(require_scope("ops:read")),
    session: AsyncSession = Depends(get_async_session),
) -> list[ConnectionRead]:
    conns = (await session.scalars(select(Connection).order_by(Connection.id))).all()
    return [await _serialize(session, c) for c in conns]


@router.post("", response_model=ConnectionRead, status_code=201)
async def create_connection(
    payload: ConnectionCreate,
    ctx: AuthzContext = Depends(require_scope("connections:write")),
    session: AsyncSession = Depends(get_async_session),
) -> ConnectionRead:
    await _validate_token_against_ibkr(payload.token, payload.query_id)
    inst_id = await session.scalar(select(Institution.id).where(Institution.code == "ibkr"))
    if inst_id is None:
        # Seed roto (la migracion baseline inserta 'ibkr') -- fail-loud, no
        # dejar que el FK NOT NULL explote en un IntegrityError opaco.
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
            token_encrypted=flex_crypto_mod.encrypt_token(payload.token),
            query_id=payload.query_id,
        )
    )
    await session.commit()
    return await _serialize(session, conn)


@router.patch("/{connection_id}", response_model=ConnectionRead)
async def update_connection(
    connection_id: int,
    payload: ConnectionUpdate,
    ctx: AuthzContext = Depends(require_scope("connections:write")),
    session: AsyncSession = Depends(get_async_session),
) -> ConnectionRead:
    conn = await _get_or_404(session, connection_id)
    if payload.display_name is not None:
        conn.display_name = payload.display_name
    await session.commit()
    return await _serialize(session, conn)


@router.post("/{connection_id}/rotate-token", response_model=ConnectionRead)
async def rotate_token(
    connection_id: int,
    payload: ConnectionRotate,
    ctx: AuthzContext = Depends(require_scope("connections:write")),
    session: AsyncSession = Depends(get_async_session),
) -> ConnectionRead:
    conn = await _get_or_404(session, connection_id)
    detail = await _detail_or_500(session, connection_id)
    query_id = payload.query_id or detail.query_id
    await _validate_token_against_ibkr(payload.token, query_id)
    detail.token_encrypted = flex_crypto_mod.encrypt_token(payload.token)
    detail.query_id = query_id
    detail.last_rotated_at = datetime.now(timezone.utc)
    connection_state.mark_rotated(conn)
    await session.commit()
    return await _serialize(session, conn)


@router.post("/{connection_id}/disable", response_model=ConnectionRead)
async def disable_connection(
    connection_id: int,
    ctx: AuthzContext = Depends(require_scope("connections:write")),
    session: AsyncSession = Depends(get_async_session),
) -> ConnectionRead:
    conn = await _get_or_404(session, connection_id)
    connection_state.set_enabled(conn, enabled=False)
    await session.commit()
    return await _serialize(session, conn)


@router.post("/{connection_id}/enable", response_model=ConnectionRead)
async def enable_connection(
    connection_id: int,
    ctx: AuthzContext = Depends(require_scope("connections:write")),
    session: AsyncSession = Depends(get_async_session),
) -> ConnectionRead:
    conn = await _get_or_404(session, connection_id)
    connection_state.set_enabled(conn, enabled=True)
    await session.commit()
    return await _serialize(session, conn)


@router.delete("/{connection_id}", status_code=204)
async def delete_connection(
    connection_id: int,
    ctx: AuthzContext = Depends(require_scope("connections:write")),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    conn = await _get_or_404(session, connection_id)
    await session.delete(conn)  # detail cae por CASCADE; imports/logs SET NULL
    await session.commit()
    return Response(status_code=204)
