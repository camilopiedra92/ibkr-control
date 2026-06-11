"""Core UPSERT helpers para el Flex persister idempotente (spec A8).

Usa sqlalchemy.dialects.postgresql.insert + ON CONFLICT semantics. Batched
con _BATCH_SIZE para respetar el techo de asyncpg de 32767 bind params per
statement (mismo patrón que TRM bulk_upsert_days post-commit 989652d).
"""

from collections.abc import Callable, Iterable, Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import Table, select, text, tuple_
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


def _quantize_to_scale(value: Decimal, scale: int | None) -> Decimal:
    """Cuantiza un Decimal a la escala de la columna (como lo guardaría Postgres).

    El persister recibe valores full-precision del XML (e.g. 255.869378) pero la
    columna es NUMERIC(20,4), así que Postgres trunca/redondea a 255.8694. Comparar
    el valor STORED (ya cuantizado, leído de la DB) contra el INCOMING crudo daría
    un falso positivo en cada re-ingest. Cuantizamos el incoming igual que la DB
    antes de diffear. scale None (sin escala declarada) => sin cuantizar.
    """
    if scale is None:
        return value
    return value.quantize(Decimal(1).scaleb(-scale))


def _values_differ(old: Any, new: Any, *, scale: int | None = None) -> bool:
    """Comparación material-aware de dos valores de columna.

    Numeric/Decimal: cuantiza ambos a la escala de la columna (lo que la DB
    guardaría) y compara por valor numérico — así un re-ingest del mismo valor
    de fuente NO marca restatement por la pérdida de precisión del NUMERIC(p,s).
    El resto: igualdad directa de Python. None vs no-None => difieren.
    """
    if isinstance(old, Decimal) or isinstance(new, Decimal):
        if old is None or new is None:
            return old is not new
        return _quantize_to_scale(Decimal(old), scale) != _quantize_to_scale(Decimal(new), scale)
    return old != new


# audit_sink(table_name, natural_key_dict, column_name, old_value, new_value)
AuditSink = Callable[[str, dict[str, Any], str, Any, Any], None]


async def _upsert_snapshot_with_audit(
    session: AsyncSession,
    table: Table,
    rows: Sequence[dict[str, Any]],
    conflict_cols: list[str],
    update_cols: list[str],
    *,
    material_cols: list[str],
    natural_key_cols: list[str],
    audit_sink: AuditSink,
) -> int:
    """Igual que _upsert_snapshot pero detecta restatements ANTES de upsertear (W3).

    (1) SELECT batched de las filas existentes por natural key (tuple_-IN, soportado
        limpio en Postgres); (2) para cada fila entrante cuyo key ya existe, compara
        las columnas materiales (Decimal-aware vía _values_differ) contra el valor
        almacenado; (3) por cada columna material que cambió, invoca audit_sink con
        el natural key + old/new; (4) delega a _upsert_snapshot sin cambios.

    Detection-only: no muta restatement_log acá (el caller acumula vía audit_sink y
    escribe al final) ni altera la semántica del upsert. Re-run idéntico => 0
    invocaciones del sink (las columnas materiales no cambian).
    """
    if not rows:
        return 0

    # Index entrante por natural key (tupla en el orden de natural_key_cols).
    incoming_by_key: dict[tuple, dict[str, Any]] = {}
    for r in rows:
        key = tuple(r[c] for c in natural_key_cols)
        # Last-seen gana dentro del batch (espeja la semántica del DO UPDATE).
        incoming_by_key[key] = r

    key_cols = [table.c[c] for c in natural_key_cols]
    existing_by_key: dict[tuple, Row] = {}
    keys = list(incoming_by_key.keys())
    select_cols = [*key_cols, *(table.c[c] for c in material_cols)]
    for batch in _chunks(keys, _BATCH_SIZE):  # type: ignore[arg-type]
        stmt = select(*select_cols).where(tuple_(*key_cols).in_(batch))
        result = await session.execute(stmt)
        for row in result.all():
            existing_by_key[tuple(getattr(row, c) for c in natural_key_cols)] = row

    for key, incoming in incoming_by_key.items():
        existing = existing_by_key.get(key)
        if existing is None:
            continue  # INSERT path — no restatement (la fila no existía)
        nk = dict(zip(natural_key_cols, key, strict=True))
        for col in material_cols:
            old = getattr(existing, col)
            new = incoming.get(col)
            # Escala de la columna NUMERIC(p,s) para cuantizar el incoming como la DB.
            scale = getattr(table.c[col].type, "scale", None)
            if _values_differ(old, new, scale=scale):
                audit_sink(table.name, nk, col, old, new)

    return await _upsert_snapshot(session, table, rows, conflict_cols, update_cols)


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
