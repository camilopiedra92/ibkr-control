"""SHA-256 dedup helper para flex_imports (per-org scope, R6 + SP1).

Exposes two public symbols:
- xml_hash(bytes) -> str  — deterministic SHA-256 hexdigest
- check_hash_status(session, organization_id, hash_hex) -> 'absent' | 'ok' | 'poison'
  Scoped to (organization_id, xml_hash) matching the UNIQUE constraint on
  flex_imports (uq_flex_imports_org_xml_hash). Dedup is per-ORG: the same XML
  ingested under two different orgs is two distinct imports.
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
    organization_id: int,
    hash_hex: str,
) -> Literal["absent", "ok", "poison"]:
    """Status del XML hash para un org dado.

    Returns:
        'absent': no existe row para (organization_id, hash_hex).
        'ok': existe con status='ok' (procesado previamente exitoso).
        'poison': existe con status='poison' (parse/persist falló antes).
    """
    from ibkr_control.db.models.flex_raw import FlexImport

    result = await session.scalar(
        select(FlexImport.status).where(
            FlexImport.organization_id == organization_id,
            FlexImport.xml_hash == hash_hex,
        )
    )
    if result is None:
        return "absent"
    return result  # type: ignore[return-value]
