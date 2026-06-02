"""Core UPSERT helpers para el Flex persister idempotente (spec A8).

Usa sqlalchemy.dialects.postgresql.insert + ON CONFLICT semantics. Batched
con _BATCH_SIZE para respetar el techo de asyncpg de 32767 bind params per
statement (mismo patrón que TRM bulk_upsert_days post-commit 989652d).
"""
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import Table, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession


_BATCH_SIZE = 5000
# Margin del techo asyncpg 32767 bind params. Con ~20 cols por trade ~250
# params/row → ~125 rows/batch worst case; 5000 dejamos espacio holgado para
# entities chicas (~5 cols).


def _chunks(rows: Sequence[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for i in range(0, len(rows), size):
        yield list(rows[i : i + size])


async def _upsert_immutable(
    session: AsyncSession,
    table: Table,
    rows: Sequence[dict[str, Any]],
    conflict_cols: list[str],
) -> int:
    """INSERT ... ON CONFLICT DO NOTHING. Devuelve count de rows realmente insertadas.

    Para entidades immutable (Trade, ClosedLot, CashTransaction, Transfer): las
    rows que ya existen quedan intactas (preserva first-seen semantics del spec A1).
    """
    if not rows:
        return 0
    n_new = 0
    for batch in _chunks(rows, _BATCH_SIZE):
        stmt = pg_insert(table).values(batch)
        stmt = stmt.on_conflict_do_nothing(index_elements=conflict_cols)
        stmt = stmt.returning(text("1"))
        result = await session.execute(stmt)
        n_new += sum(1 for _ in result)
    return n_new


async def _upsert_snapshot(
    session: AsyncSession,
    table: Table,
    rows: Sequence[dict[str, Any]],
    conflict_cols: list[str],
    update_cols: list[str],
) -> int:
    """INSERT ... ON CONFLICT DO UPDATE. Devuelve count de rows touched (insert + update).

    Para entidades snapshot (OpenPositionLot, accruals): rows existentes se
    actualizan con los valores del nuevo XML (spec A1-bis: flex_import_id queda
    como "last updated by").
    """
    if not rows:
        return 0
    n_touched = 0
    for batch in _chunks(rows, _BATCH_SIZE):
        stmt = pg_insert(table).values(batch)
        excluded = stmt.excluded
        stmt = stmt.on_conflict_do_update(
            index_elements=conflict_cols,
            set_={col: excluded[col] for col in update_cols},
        )
        result = await session.execute(stmt)
        n_touched += result.rowcount
    return n_touched


async def _upsert_immutable_returning_inserted(
    session: AsyncSession,
    table: Table,
    rows: Sequence[dict[str, Any]],
    conflict_cols: list[str],
    returning_cols: list[str],
) -> list[Row]:
    """Como _upsert_immutable pero devuelve solo las rows insertadas con las
    columnas pedidas. Las NO-OP por conflicto NO aparecen en el resultado.

    Usado cuando el caller necesita inspeccionar las rows nuevamente insertadas
    (e.g. contar dividends por tipo en el path de CashTransaction).
    """
    if not rows:
        return []
    inserted: list[Row] = []
    for batch in _chunks(rows, _BATCH_SIZE):
        stmt = pg_insert(table).values(batch)
        stmt = stmt.on_conflict_do_nothing(index_elements=conflict_cols)
        stmt = stmt.returning(*[table.c[col] for col in returning_cols])
        result = await session.execute(stmt)
        inserted.extend(result.all())
    return inserted
