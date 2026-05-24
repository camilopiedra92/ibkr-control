"""Advisory lock por (source, user_id) sobre Postgres.

Usa pg_try_advisory_lock — non-blocking, falla rapido si esta tomado.
El lock se libera explicitamente en el finally del context manager.
"""
import zlib
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class LockHeldError(RuntimeError):
    """Lanzada cuando un advisory lock esta tomado por otra session."""

    def __init__(self, source: str, user_id: int | None):
        self.source = source
        self.user_id = user_id
        super().__init__(f"Lock held: source={source}, user_id={user_id}")


def _lock_key(source: str, user_id: int | None) -> int:
    """Mapea (source, user_id) a un bigint positivo deterministico across processes.

    Usa zlib.crc32 en lugar de hash() porque Python aplica PYTHONHASHSEED
    a strings por defecto — cada proceso produciria claves distintas para el
    mismo input, lo que romperia la coordinacion entre contenedores/procesos.
    zlib.crc32 es deterministico en todos los procesos y plataformas.
    """
    key_str = f"{source}:{user_id}"
    # zlib.crc32 retorna uint32 (0..2^32-1), cabe en bigint de pg_advisory_lock
    return zlib.crc32(key_str.encode("utf-8"))


@asynccontextmanager
async def advisory_lock(session: AsyncSession, user_id: int | None, source: str):
    """Adquiere lock para (source, user_id). Lanza LockHeldError si tomado.

    Args:
        session: sesion SQLAlchemy. El lock vive en esta sesion (libera al commit/close).
        user_id: id del usuario (None para locks globales como TRM).
        source: 'flex' | 'trm' | 'manual_upload' | ...
    """
    key = _lock_key(source, user_id)
    acquired = await session.scalar(
        text("SELECT pg_try_advisory_lock(:k)"), {"k": key}
    )
    if not acquired:
        raise LockHeldError(source=source, user_id=user_id)
    try:
        yield
    finally:
        await session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
