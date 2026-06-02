"""Dependency FastAPI que envuelve el resolver. Es el contrato que Phase 3
consume: cada endpoint de lectura de hechos resuelve su scope con esto."""

from fastapi import Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.auth.backend import current_active_user
from ibkr_control.auth.models import User
from ibkr_control.authz.scope import GrantRequiredError, visible_account_ids
from ibkr_control.db.session import get_async_session


async def require_account_scope(
    on_behalf_of: int | None = Query(default=None),
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> set[int]:
    try:
        return await visible_account_ids(session, user.id, on_behalf_of=on_behalf_of)
    except GrantRequiredError as e:
        raise HTTPException(
            status_code=403, detail="No tenés acceso a los datos de ese usuario"
        ) from e
