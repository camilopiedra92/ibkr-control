from collections.abc import AsyncGenerator

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, IntegerIDMixin
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

# Import order intencional: db.session debe importarse antes que auth.models
# para que db/__init__.py (que re-exporta User) corra completo y deje
# auth.models registrado. Si alphabetizamos, se rompe con un partial-init
# ImportError (ver historial git, commit del polish backlog Task 4).
from ibkr_control.config import get_settings
from ibkr_control.db.session import get_async_session
from ibkr_control.auth.models import User
from ibkr_control.settings.models import UserSettings


async def get_user_db(
    session: AsyncSession = Depends(get_async_session),
) -> AsyncGenerator[SQLAlchemyUserDatabase, None]:
    yield SQLAlchemyUserDatabase(session, User)


class UserManager(IntegerIDMixin, BaseUserManager[User, int]):
    # Properties (no class attributes): leer get_settings() en cada acceso para
    # que monkeypatch en tests no caiga sobre referencias capturadas a import-time.
    @property
    def reset_password_token_secret(self) -> str:  # type: ignore[override]
        return get_settings().jwt_secret

    @property
    def verification_token_secret(self) -> str:  # type: ignore[override]
        return get_settings().jwt_secret

    async def on_after_register(self, user: User, request: Request | None = None) -> None:
        session: AsyncSession = self.user_db.session  # type: ignore[attr-defined]
        session.add(UserSettings(user_id=user.id))
        await session.commit()


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
) -> AsyncGenerator[UserManager, None]:
    yield UserManager(user_db)
