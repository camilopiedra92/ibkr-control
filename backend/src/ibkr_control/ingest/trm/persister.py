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

# Postgres binds use int16 for parameter count (hard cap 32767). Each row carries
# 4 explicit params (date, value_cop, vigencia_desde, vigencia_hasta) — fetched_at
# uses NOW() literal. Full Socrata backfill expands to ~12.5k days = ~50k params,
# blowing the limit in a single INSERT. We chunk well below the cap so adding a
# column to the insert later does not silently re-introduce the failure.
_BATCH_SIZE = 5000


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

    for start in range(0, len(expanded), _BATCH_SIZE):
        chunk = expanded[start : start + _BATCH_SIZE]
        stmt = pg_insert(TrmDay).values(chunk)
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
