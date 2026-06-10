from collections.abc import AsyncGenerator
from functools import lru_cache
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ibkr_control.config import get_settings


def engine_kwargs() -> dict[str, Any]:
    """Pool config explícito (D2 sp1-db-hardening) — único punto de verdad.

    Consumido por get_engine() y por los engines efímeros de los crons
    (scheduler/jobs.py) para que el presupuesto de conexiones sea un solo
    concepto. pool_pre_ping=True fijo: detecta conexiones muertas post
    restart de Postgres en vez de fallar el primer request.
    """
    s = get_settings()
    return {
        "pool_size": s.db_pool_size,
        "max_overflow": s.db_max_overflow,
        "pool_recycle": s.db_pool_recycle,
        "pool_pre_ping": True,
    }


@lru_cache
def get_engine() -> AsyncEngine:
    """Engine singleton de la app (lru_cache — se crea una sola vez).

    El pool config se hornea en la PRIMERA llamada vía engine_kwargs().
    Cambiar DB_POOL_* después no tiene efecto hasta reiniciar el proceso
    (o get_engine.cache_clear() + get_session_maker.cache_clear() en tests).
    Intencional: el pool de un engine vivo no se puede redimensionar.
    """
    return create_async_engine(
        get_settings().database_url, echo=False, future=True, **engine_kwargs()
    )


@lru_cache
def get_session_maker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with get_session_maker()() as session:
        yield session
