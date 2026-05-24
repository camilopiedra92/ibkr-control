from fastapi_users.authentication import AuthenticationBackend, BearerTransport, JWTStrategy
from fastapi_users import FastAPIUsers
from fastapi import Depends

from ibkr_control.config import get_settings
from ibkr_control.auth.manager import get_user_manager
from ibkr_control.auth.models import User

settings = get_settings()

bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(secret=settings.jwt_secret, lifetime_seconds=settings.jwt_lifetime_seconds)


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, int](get_user_manager, [auth_backend])

current_active_user = fastapi_users.current_user(active=True)
