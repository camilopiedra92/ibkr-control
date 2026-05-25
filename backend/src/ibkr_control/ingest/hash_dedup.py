"""SHA-256 dedup helper para flex_imports (per-user scope, R6).

NOTE: is_known_hash() is deprecated (kept temporarily for backwards compat
with flex/job.py until Task 6 swaps callers). New code should use
check_hash_status() which returns Literal['absent', 'ok', 'poison'] and is
scoped per-user (matching the UNIQUE(user_id, xml_hash) constraint).
"""
import hashlib
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def xml_hash(xml_bytes: bytes) -> str:
    """Hexdigest SHA-256 de un blob de bytes."""
    return hashlib.sha256(xml_bytes).hexdigest()


async def check_hash_status(
    session: AsyncSession,
    user_id: int,
    hash_hex: str,
) -> Literal["absent", "ok", "poison"]:
    """Status del XML hash para un user dado.

    Returns:
        'absent': no existe row para (user_id, hash_hex).
        'ok': existe con status='ok' (procesado previamente exitoso).
        'poison': existe con status='poison' (parse/persist falló antes).
    """
    from ibkr_control.db.models.flex_raw import FlexImport

    result = await session.scalar(
        select(FlexImport.status).where(
            FlexImport.user_id == user_id,
            FlexImport.xml_hash == hash_hex,
        )
    )
    if result is None:
        return "absent"
    return result  # type: ignore[return-value]


async def is_known_hash(session: AsyncSession, hash_hex: str) -> bool:
    """DEPRECATED: removed in Task 6. Use check_hash_status() instead."""
    from ibkr_control.db.models.flex_raw import FlexImport

    result = await session.scalar(
        select(FlexImport.id).where(FlexImport.xml_hash == hash_hex)
    )
    return result is not None
