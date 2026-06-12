"""Core UPSERT helpers para el Flex persister idempotente (spec A8).

Usa sqlalchemy.dialects.postgresql.insert + ON CONFLICT semantics. Batched
con _BATCH_SIZE para respetar el techo de asyncpg de 32767 bind params per
statement (mismo patrón que TRM bulk_upsert_days post-commit 989652d).
"""

from collections.abc import Callable, Iterable, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import Table, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession


_BATCH_SIZE = 5000
# Margin del techo asyncpg 32767 bind params. Con ~20 cols por trade ~250
# params/row → ~125 rows/batch worst case; 5000 dejamos espacio holgado para
# entities chicas (~5 cols).

_MAX_BIND_PARAMS = 30000
# Techo de bind params para chunking key-count-aware (W3): los chunks que expanden
# N params POR ITEM (tuple_-IN de natural keys de 8 columnas, INSERTs multi-col)
# deben dividir este techo por el ancho del item — _BATCH_SIZE plano con keys de
# 8 cols daría 40k params, sobre el límite int16 de asyncpg (32767) que el repo
# ya quemó en TRM bulk_upsert (989652d).


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
    """Cuantiza un Decimal a la escala declarada de la columna (como lo guardaría PG).

    Post spec 2026-06-12 (PD-1/PD-3) las columnas fuente-IBKR son NUMERIC
    unconstrained -> scale=None -> pass-through: la comparación es exacta, que
    con storage exacto es la semántica correcta. El helper queda latente para
    columnas con scale documentada (e.g. si trm/participations se vuelven
    material cols algún día): "comparar a precisión de storage" sigue siendo
    el principio; hoy la precisión de storage es la de la fuente.
    """
    if scale is None:
        return value
    # PG NUMERIC redondea HALF_UP; el default de Decimal es HALF_EVEN — sin esto
    # un valor en el boundary exacto (e.g. 255.86945 a escala 4) produce un falso positivo.
    return value.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)


def _values_differ(old: Any, new: Any, *, scale: int | None = None) -> bool:
    """Comparación material-aware de dos valores de columna.

    Numeric/Decimal: compara a precisión de storage (con NUMERIC unconstrained
    = exacta; con scale declarada, cuantiza ambos como la DB) — así un re-ingest
    del mismo valor de fuente NUNCA marca restatement.
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
    # Chunk key-count-aware: cada key expande len(key_cols) bind params (8 en los
    # accruals) — el tamaño del chunk debe dividir el techo por ese ancho.
    chunk_size = max(1, _MAX_BIND_PARAMS // max(1, len(key_cols)))
    for batch in _chunks(keys, chunk_size):  # type: ignore[arg-type]
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


async def _upsert_immutable_with_resolution(
    session: AsyncSession,
    table: Table,
    rows: Sequence[dict[str, Any]],
    conflict_cols: list[str],
    returning_cols: list[str],
    resolution_col: str = "instrument_id",
) -> list[Row]:
    """Como _upsert_immutable_returning_inserted + convergencia monótona (TL-D3).

    Las columnas de HECHO siguen first-seen inmutables; la columna de RESOLUCIÓN
    (enriquecimiento contra el catálogo control-plane) converge NULL->valor
    cuando un re-ingest trae la resolución que faltaba. El WHERE hace el update
    monótono y cero-churn: filas ya resueltas o sin resolución nueva se
    comportan como DO NOTHING (cero versiones nuevas de tupla, cero bloat; el
    row lock del conflicto sí se toma — irrelevante acá: el ingest está
    serializado per-org por advisory lock).

    Devuelve SOLO los INSERTs estrictos (xmax = 0): una convergencia no es una
    fila nueva — n_new/ingest_log no se inflan (conteo honesto, spec TL-D3).
    xmax es detalle de implementación MVCC, no API documentada — estable desde
    PG 9.5; no "simplificar" este filtro.

    Dedupe intra-batch por conflict key (last-seen gana): a diferencia del
    DO NOTHING, el DO UPDATE rechaza afectar la misma fila dos veces en un
    statement (CardinalityViolationError).
    """
    if not rows:
        return []
    deduped: dict[tuple, dict[str, Any]] = {}
    for r in rows:
        deduped[tuple(r[c] for c in conflict_cols)] = r
    inserted: list[Row] = []
    for batch in _chunks(list(deduped.values()), _BATCH_SIZE):
        stmt = pg_insert(table).values(batch)
        excluded = stmt.excluded
        stmt = stmt.on_conflict_do_update(
            index_elements=conflict_cols,
            set_={resolution_col: excluded[resolution_col]},
            where=table.c[resolution_col].is_(None) & excluded[resolution_col].is_not(None),
        )
        stmt = stmt.returning(
            *[table.c[col] for col in returning_cols],
            text("(xmax = 0) AS strictly_inserted"),
        )
        result = await session.execute(stmt)
        inserted.extend(row for row in result.all() if row.strictly_inserted)
    return inserted
