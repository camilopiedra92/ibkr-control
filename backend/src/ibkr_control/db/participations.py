"""Write-path compartido de participations (SCD-2) — HD-5a.

Extraído de los dos bloques byte-idénticos de api/setup.py. Phase 3
(domain/participation.py::apply_pct) consume el READ; este es el WRITE canónico.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.db.models.participations import Participation


async def upsert_participation(
    session: AsyncSession,
    *,
    party_id: int,
    account_id: int,
    organization_id: int,
    pct: Decimal,
    at: date,
) -> None:
    """SCD-2: si hay fila abierta con distinto pct, la cierra (valid_to=at) e inserta
    la nueva; si el pct no cambió, no-op. NO commitea (el caller maneja la tx)."""
    existing = await session.scalar(
        select(Participation).where(
            Participation.party_id == party_id,
            Participation.account_id == account_id,
            Participation.valid_to.is_(None),
        )
    )
    if existing is not None:
        if existing.pct == pct:
            return
        existing.valid_to = at
        await session.flush()
    session.add(
        Participation(
            party_id=party_id,
            account_id=account_id,
            organization_id=organization_id,
            pct=pct,
            valid_from=at,
            valid_to=None,
        )
    )
