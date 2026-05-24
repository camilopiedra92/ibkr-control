"""Router /api/credentials/flex -- GET (metadata) y PUT (rotate)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.api._schemas import FlexCredentialsRead, FlexCredentialsUpdate
from ibkr_control.auth.backend import current_active_user
from ibkr_control.auth.models import User
from ibkr_control.db.models.flex_credentials import FlexCredentials
from ibkr_control.db.session import get_async_session
from ibkr_control.ingest.flex import client as flex_client_mod
from ibkr_control.ingest.flex import crypto as flex_crypto_mod

router = APIRouter(prefix="/credentials", tags=["credentials"])


@router.get("/flex", response_model=FlexCredentialsRead)
async def get_flex_credentials(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> FlexCredentialsRead:
    creds = await session.scalar(
        select(FlexCredentials).where(FlexCredentials.user_id == user.id)
    )
    if creds is None:
        raise HTTPException(status_code=404, detail="No Flex credentials configured")
    return FlexCredentialsRead(
        configured_at=creds.last_rotated_at,
        query_id=creds.ytd_query_id,
        last_rotated_at=creds.last_rotated_at,
    )


@router.put("/flex")
async def update_flex_credentials(
    payload: FlexCredentialsUpdate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Si pasa token, lo valida contra IBKR antes de guardar."""
    creds = await session.scalar(
        select(FlexCredentials).where(FlexCredentials.user_id == user.id)
    )

    new_token = payload.token
    new_query_id = payload.query_id

    # Ping IBKR si hay token nuevo
    if new_token is not None:
        test_query_id = new_query_id or (creds.ytd_query_id if creds else None)
        if not test_query_id:
            raise HTTPException(status_code=400, detail="Falta query_id para validar el token")
        try:
            client = flex_client_mod.FlexClient(token=new_token)
            await client.send_request(query_id=test_query_id)
        except flex_client_mod.FlexAuthError as e:
            raise HTTPException(status_code=401, detail=f"Token invalido: {e.error_message}")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"No pude alcanzar IBKR: {e}")

    # Persistir
    if creds is None:
        if not new_token or not new_query_id:
            raise HTTPException(
                status_code=400, detail="Primera vez requiere token + query_id"
            )
        creds = FlexCredentials(
            user_id=user.id,
            token_encrypted=flex_crypto_mod.encrypt_token(new_token),
            ytd_query_id=new_query_id,
        )
        session.add(creds)
    else:
        if new_token is not None:
            creds.token_encrypted = flex_crypto_mod.encrypt_token(new_token)
        if new_query_id is not None:
            creds.ytd_query_id = new_query_id
        creds.last_rotated_at = datetime.now(timezone.utc)

    await session.commit()
    return {"ok": True}
