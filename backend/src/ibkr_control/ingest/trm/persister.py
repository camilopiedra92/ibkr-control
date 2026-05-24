"""Persiste rows TRM expandidos com INSERT ... ON CONFLICT DO UPDATE.

bulk_upsert_days: inserta o actualiza rows en trm_days (1 row por dia).
record_import: crea un row en trm_imports (audit por run).

El upsert es idem potente por diseno: si el mismo dia se ingiere dos veces
(e.g., backfill sobre datos existentes), value_cop y vigencia_* se actualizan
y fetched_at se pone a NOW() para que los updates sean auditables.
"""
from datetime import date as date_type

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.db.models.trm import TrmDay, TrmImport


async def bulk_upsert_days(session: AsyncSession, expanded: list[dict]) -> int:
    """Inserta o actualiza rows en trm_days. Devuelve N rows procesados.

    Args:
        session: sesion SQLAlchemy async.
        expanded: lista de dicts con keys: date, value_cop, vigencia_desde, vigencia_hasta.

    Returns:
        Numero de rows procesados (len(expanded)).
    """
    if not expanded:
        return 0

    stmt = pg_insert(TrmDay).values(expanded)
    stmt = stmt.on_conflict_do_update(
        index_elements=["date"],
        set_={
            "value_cop": stmt.excluded.value_cop,
            "vigencia_desde": stmt.excluded.vigencia_desde,
            "vigencia_hasta": stmt.excluded.vigencia_hasta,
            "fetched_at": text("NOW()"),
        },
    )
    await session.execute(stmt)
    await session.flush()
    return len(expanded)


async def record_import(
    session: AsyncSession,
    *,
    date_from: date_type,
    date_to: date_type,
    n_rows_api: int,
    n_days_expanded: int,
) -> int:
    """Crea un row en trm_imports para auditar el run. Devuelve el id creado."""
    row = TrmImport(
        date_range_from=date_from,
        date_range_to=date_to,
        n_rows_api=n_rows_api,
        n_days_expanded=n_days_expanded,
    )
    session.add(row)
    await session.flush()
    return row.id
