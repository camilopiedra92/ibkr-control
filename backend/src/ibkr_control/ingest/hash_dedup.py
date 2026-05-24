"""SHA-256 dedup helper para flex_imports."""
import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def xml_hash(xml_bytes: bytes) -> str:
    """Hexdigest SHA-256 de un blob de bytes."""
    return hashlib.sha256(xml_bytes).hexdigest()


async def is_known_hash(session: AsyncSession, hash_hex: str) -> bool:
    """True si ya existe un flex_imports.xml_hash con este valor."""
    from ibkr_control.db.models.flex_raw import FlexImport
    result = await session.scalar(
        select(FlexImport.id).where(FlexImport.xml_hash == hash_hex)
    )
    return result is not None
