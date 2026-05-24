from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.auth.backend import current_active_user
from ibkr_control.auth.models import User
from ibkr_control.db.session import get_async_session
from ibkr_control.settings.models import UserSettings
from ibkr_control.settings.schemas import UserSettingsRead, UserSettingsUpdate

router = APIRouter(prefix="/settings", tags=["settings"])


async def _load(user_id: int, session: AsyncSession) -> UserSettings:
    # Recovery path: la fila puede faltar si on_after_register falló durante
    # el register (User ya está committed por fastapi-users antes del hook, así
    # que un fallo posterior deja al usuario huérfano sin compensación posible
    # en el manager). Creamos la fila on-demand usando los defaults del DB
    # (marginal_rate 0.3900, timezone America/Bogota). Esto hace el endpoint
    # idempotente y tolerante a fallos transitorios del hook de registro.
    result = await session.execute(select(UserSettings).where(UserSettings.user_id == user_id))
    row = result.scalar_one_or_none()
    if row is None:
        row = UserSettings(user_id=user_id)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


@router.get("", response_model=UserSettingsRead)
async def get_settings(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> UserSettings:
    return await _load(user.id, session)


@router.patch("", response_model=UserSettingsRead)
async def update_settings(
    payload: UserSettingsUpdate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> UserSettings:
    row = await _load(user.id, session)
    if payload.marginal_rate is not None:
        row.marginal_rate = payload.marginal_rate
    if payload.timezone is not None:
        row.timezone = payload.timezone
    await session.commit()
    await session.refresh(row)
    return row
