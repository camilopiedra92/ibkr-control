"""Advisory lock por (source, scope_id) sobre Postgres.

Usa pg_try_advisory_lock — non-blocking, falla rapido si esta tomado.
El lock se libera explicitamente en el finally del context manager.

El scope del lock es per-source:
- flex corre per-ORG → scope_id es el organization_id.
- trm es global → scope_id es None.
"""

import zlib
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class LockHeldError(RuntimeError):
    """Lanzada cuando un advisory lock esta tomado por otra session."""

    def __init__(self, source: str, scope_id: int | None):
        self.source = source
        self.scope_id = scope_id
        super().__init__(f"Lock held: source={source}, scope_id={scope_id}")


def _lock_key(source: str, scope_id: int | None) -> int:
    """Mapea (source, scope_id) a un bigint positivo deterministico across processes.

    Usa zlib.crc32 en lugar de hash() porque Python aplica PYTHONHASHSEED
    a strings por defecto — cada proceso produciria claves distintas para el
    mismo input, lo que romperia la coordinacion entre contenedores/procesos.
    zlib.crc32 es deterministico en todos los procesos y plataformas.
    """
    key_str = f"{source}:{scope_id}"
    # zlib.crc32 retorna uint32 (0..2^32-1), cabe en bigint de pg_advisory_lock
    return zlib.crc32(key_str.encode("utf-8"))


@asynccontextmanager
async def advisory_lock(session: AsyncSession, scope_id: int | None, source: str):
    """Adquiere lock para (source, scope_id). Lanza LockHeldError si tomado.

    Args:
        session: sesion SQLAlchemy. El lock vive en esta sesion (libera al commit/close).
        scope_id: id del scope del lock — organization_id para flex (per-org),
                  None para locks globales como TRM.
        source: 'flex' | 'trm' | 'manual_upload' | ...
    """
    key = _lock_key(source, scope_id)
    acquired = await session.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})
    if not acquired:
        raise LockHeldError(source=source, scope_id=scope_id)
    try:
        yield
    finally:
        await session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
