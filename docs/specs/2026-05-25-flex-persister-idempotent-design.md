# Flex persister idempotente — diseño

**Status:** locked
**Date:** 2026-05-25
**Phase context:** Phase 2.5 (entre Phase 2 wizard redesign cerrado y Phase 3 lotes/domain layer pendiente)
**Supersedes:** comportamiento del persister documentado en `docs/specs/2026-05-24-phase2-ingestion-design.md` §4 "Persister" y §6 "Dedup strategy"

## Contexto

Bug detectado el 2026-05-25 durante refresh manual desde Settings:

- UI mostraba "✓ Refresh completado correctamente" (race condition entre `useEffect` hooks en `ManualRefreshButton.tsx` — fix aparte en el mismo PR)
- `ingest_log` mostraba TRM=ok pero **Flex=failed** en cada corrida
- Causa raíz: `UniqueViolationError: duplicate key value violates unique constraint "trades_transaction_id_key"` en el persister

El persister Phase 2 dedupea solo a nivel `flex_imports.xml_hash`. La Flex YTD del Web Service cambia byte-a-byte cada día (mark prices, timestamp del reporte, nuevos eventos) → cada hash es nuevo → el persister intenta re-insertar todos los trades del año vía `session.add(Trade(...))` → choca con la UNIQUE(transaction_id) que existe globalmente.

Consecuencia: **el cron diario de Flex también está roto desde el primer día post-wizard**, no solo el manual refresh. Phase 2 no lo detectó porque su único smoke E2E fue la primera corrida post-wizard (única que funciona — DB vacía, no hay conflicto).

Solución: rewrite del persister a UPSERT por natural key per-tabla, con semántica diferenciada para entidades immutable vs snapshot.

## Decisiones lockeadas (A0-A8, + A1-bis como corolario de A1)

Brainstormeadas via `superpowers:brainstorming` el 2026-05-25. Estado: todas confirmadas por el usuario, no re-discutibles en implementación. A1-bis emerge como consecuencia de A1+A3 (semántica de `flex_import_id` diferenciada según entity-type es immutable o snapshot).

### A0 — Guardar XML bytes raw en `flex_imports`

Agregar columna `xml_bytes BYTEA NOT NULL` a `flex_imports`. Permite replay completo desde DB sin depender de los archivos en disco del usuario.

**Why:** Sin esto, cualquier refactor futuro del persister requiere pedir al usuario los XML files originales. Con xml_bytes en DB, los refactors son fully recoverable. Costo: ~100KB-1MB por import × ~3650 imports/5y/user = ~1.8GB max por usuario en 5 años. Postgres TOAST lo maneja transparentemente; compresible.

**How to apply:** El persister escribe `xml_bytes=xml_bytes` al crear FlexImport row. Migration backfill: los 3 imports existentes no tienen los bytes → wipe + re-ingest (ver A6).

### A1 — Semántica de `flex_import_id` en entidades immutable = **first seen**

Para `trades`, `closed_lots`, `cash_transactions`, `transfers`: `flex_import_id` apunta al primer import que observó la fila. **Nunca se actualiza** después del INSERT inicial.

**Why:** Aligned con ortodoxia contable (general ledger): un asiento se escribe una vez y nunca se actualiza. Trade ejecutado = hecho histórico inmutable. "Last seen" introduce write amplification + pierde la semántica "cuándo entró este dato a nuestro sistema". Join table many-to-many (`flex_import_observations`) es YAGNI — no hay query real que lo demande y la replay capability viene de A0.

**How to apply:** El persister usa `ON CONFLICT DO NOTHING`. Si la fila ya existe, `flex_import_id` queda con su valor original.

### A1-bis — Semántica de `flex_import_id` en entidades snapshot = **last updated by**

Para `open_position_lots`, `change_in_dividend_accruals`, `open_dividend_accruals`: la fila representa un snapshot mutable; `flex_import_id` apunta al import que escribió los valores actuales.

**Why:** Semántica consistente con el contenido de la row (mark_price viene del último import). Distinto de A1 pero consistente con el rol del entity.

**How to apply:** UPSERT con `DO UPDATE SET flex_import_id = EXCLUDED.flex_import_id, ...`.

### A2 — `ON DELETE` del FK `child.flex_import_id → flex_imports.id` = **SET NULL** + columna nullable

Borrar un flex_import deja vivos a los children con `flex_import_id = NULL`. Filas immutable sobreviven al borrado de su fuente.

**Why:** Aligned con append-only ledger. Los hechos ya ocurridos no dependen de que su archivo fuente siga en la DB. `RESTRICT + soft-delete` (con `superseded_at`) es world-class para sistemas con compliance externo pero YAGNI para single-user. `CASCADE` viola la semántica de first-seen (mata data sana que apareció en imports posteriores).

**How to apply:** Migration cambia `flex_import_id` a NULLABLE + altera FK a `ON DELETE SET NULL` en todas las tablas children.

### A3 — Natural keys de entidades snapshot/accrual

| Tabla | Natural key | Semántica de UPDATE |
|---|---|---|
| `open_position_lots` | `(account_id, symbol, open_date, snapshot_date, originating_transaction_id)` | UPDATE qty, cost_basis_usd, mark_price_usd, mark_value_usd, flex_import_id |
| `change_in_dividend_accruals` | `(account_id, conid, ex_date, pay_date, accrual_date, report_date, action_id, code)` | UPDATE all mutable fields |
| `open_dividend_accruals` | `(account_id, conid, ex_date, pay_date, report_date, action_id, code)` | UPDATE all mutable fields |

**⚠ A3 AMENDED 2026-05-25 during Task 8 implementation:** original natural key for `open_position_lots` was `(account_id, symbol, open_date, snapshot_date)` — collided against real IBKR data where the SAME `(account, symbol, open_date)` has multiple distinct LOT rows from multi-fill orders (e.g., AMD 2025-02-05 has 2 lots: qty=0.8638 from txn 31233843972, qty=1 from txn 31233844034). IBKR distinguishes them via `originatingTransactionID` XML attribute. Added `originating_transaction_id` to the natural key. Required:
- New parser field on `ParsedOpenPositionLot.originating_transaction_id: str`
- New column on `OpenPositionLot` model (NOT NULL, since IBKR always emits it for LOT-level rows)
- New Alembic Revision 3 to add column + swap UNIQUE constraint (drops `open_position_lots_natural_key` from Rev2, creates the same-named constraint with the extended column list)
- Persister update: pass `originating_transaction_id` in conflict_cols
- No impact on snapshot semantics — same lot keeps getting UPDATEd as mark price changes; different fills stay as separate rows.

**⚠ A3 AMENDED #2 2026-05-25 during Task 8 implementation (accruals):** original natural keys for `change_in_dividend_accruals` and `open_dividend_accruals` were incomplete — collided against real IBKR data with multiple accrual lifecycle events (Posted/Reversed) for the same dividend payment. E.g., ASML ex_date=2025-02-11 + pay_date=2025-02-19 + accrual_date=2025-02-10 has 3 distinct rows discriminated by `report_date + action_id + code` (Po=Posted vs Re=Reversal). Extended natural keys with `(report_date, action_id, code)`. Required:
- Promote `code` from raw_attrs to first-class column (Mapped[str | None] mapped_column(String, nullable=True)) on both `ChangeInDividendAccrual` and `OpenDividendAccrual` models (and add to `ParsedDividendAccrual` + `ParsedOpenDividendAccrual` dataclasses + parser extraction)
- `report_date` and `action_id` already exist as columns — no change
- New Alembic Revision 4: add `code` column + drop/recreate both UNIQUE constraints with extended column lists
- Persister update: pass extended natural key in both `_upsert_snapshot` calls + include `code` in the row dict + `code` to update_cols
- **Preemptive mirror to `open_dividend_accruals`:** the 2025 fixture has only 1 row (no observable collision), but the same IBKR pattern emits Po/Re events for open accruals too. Apply the same fix preemptively to avoid the bug surfacing in production with bigger fixtures (world-class = fix the class of bug, not just the observed instance).
- `code` can be NULL for some IBKR rows (e.g., rows without a lifecycle marker), so UNIQUE on `(..., code)` works because Postgres treats NULL != NULL in UNIQUE constraints by default — but to avoid the surprise of "two rows with NULL code collide", we use Postgres's `NULLS NOT DISTINCT` option on the UNIQUE constraint (PG15+). Alternatively coalesce NULL to '' at write time. **Decision: COALESCE to '' at persister write time** (simpler, no PG15+ requirement; matches our pattern for other "or ''" fallbacks like `transaction_id`).

**Why:** `snapshot_date` (y equivalentes `accrual_date`/`report_date` para accruals) en la natural key preserva series temporal naturalmente. Para Patrimonio Dec 31 (Art. 261-263 ET), Dec 31 queda preservado para siempre — el cron del Jan 1 inserta una row nueva, no sobreescribe. Sin `snapshot_date`, Opción 2 (overwrite in place) requeriría workaround tipo "freeze cron" que es exactamente el anti-patrón a evitar.

Volumen proyectado: ~20 lotes × 365 días × 5 años + sealed historicals = ~7,400 rows. Trivial para Postgres.

Tabla separada (Opción 3) es over-engineering: ahorra rows en "current" pero duplica el path en persister. Índice `(account_id, symbol, open_date, snapshot_date DESC)` resuelve "current state" en <1ms incluso a 100k rows.

**How to apply:** Migration agrega los `UNIQUE` constraints. Persister usa `ON CONFLICT DO UPDATE`.

### A4 — Hash dedup a nivel `flex_imports.xml_hash` = **mantener como fast-path**

Si `xml_hash` ya existe en `flex_imports`, return early sin parsear ni intentar UPSERTs.

**Why:** Ahorra parsing (~50-200ms) + N inserts no-op cuando el usuario re-sube el mismo XML byte-idéntico. Defense in depth: hash dedup (capa rápida) + row-level UPSERT (capa correcta). Si una falla, la otra preserva idempotencia.

**How to apply:** Sin cambios respecto a Phase 2; el `if existing_id: return existing_id` arriba del persister se mantiene.

### A5 — Counters en `flex_imports` = **dos sets (`n_observed_*` + `n_new_*`)**

| Columna actual | Columna nueva | Semántica |
|---|---|---|
| `n_trades` | `n_observed_trades` (rename) | rows en el XML |
| — | `n_new_trades` (add) | rows que resultaron en DB write (INSERT path o, para snapshot, INSERT+UPDATE combinado) |

Mismo patrón para `n_lots_closed`, `n_open_lots`, `n_cash_tx`, `n_dividends`, `n_transfers`.

**Why:** Capturan dos preguntas distintas, ambas legítimas:
- `n_observed_*` → "¿IBKR sigue mandando data nueva? ¿el download llegó completo?" (signal de salud)
- `n_new_*` → "¿qué cambió en mi DB?" (signal del valor del ingest)

Drop counters enteras (recompute on demand) rompe el wizard response y agrega ~50ms × 6 counters × N imports en UI. Solo `n_new_*` pierde el signal "size del XML para detectar truncamiento". Status quo es ruidoso (cada cron muestra "1200 trades" aunque todo sea no-op).

Schema cost: 6 columnas INT extra. Trivial.

**Implementation hint:** Postgres `RETURNING (xmax = 0) AS inserted` permite distinguir INSERT (xmax=0) de UPDATE per-row. Para immutable: `n_new = rowcount(DO NOTHING returning *)`. Para snapshot: `n_new = count(xmax=0 OR xmax≠0)` = rows touched. Future: si querés desagregar insert vs update en snapshot, agregás `n_inserted_*` + `n_updated_*` sin tocar lo existente.

**How to apply:** Migration renombra + agrega columnas. Backfill: rows existentes setean `n_new_* = n_observed_*` (correcto porque las 3 imports actuales fueron fresh inserts sin overlap). API + SSE payloads exponen ambos campos en `IngestSummary`.

### A6 — Migración de la data existente = **wipe + re-ingest**

Migration H-style wipe: TRUNCATE `flex_imports` + children, preserva `flex_credentials` + `accounts` + `apscheduler_jobs` + `users`. Usuario re-sube manualmente los XMLs de 2024 + 2025 sealed. 2026 YTD lo trae el primer Flex WS fetch nuevo (cron 06:00 COT o manual desde Settings).

**Why:** Migration in place no es viable: `closed_lots.transaction_id`, `cash_transactions.transaction_id`, `transfers.transaction_id` no están persistidos hoy aunque sí parseados (el parser ya los lee). Backfill requeriría re-parsear los XMLs originales — que no están en DB (A0 resuelve esto a futuro). Sin esos campos, no podemos agregar las UNIQUE constraints requeridas por la nueva arquitectura.

Wipe es limpio porque el usuario tiene los 2 sealed historicals localmente y el YTD se regenera trivialmente. Con A0 implementado en la misma migration, este es el último wipe manual de la historia — futuros refactors podrán replay 100% desde DB.

**How to apply:** Migration nueva `revision="phase25_idempotent_persister"`:
1. Add columns + UNIQUE constraints
2. Change FK to `ON DELETE SET NULL`, make `flex_import_id` nullable
3. TRUNCATE `flex_imports CASCADE` (children siguen por el CASCADE actual, que aún existe en este punto)
4. Add the `ON DELETE SET NULL` constraint AFTER truncate (orden importa)
5. Add `xml_bytes BYTEA NOT NULL` (válido porque la tabla está vacía post-truncate)
6. Add `n_new_*` counters NULL allowed

Post-migration: usuario re-upload manual desde wizard o endpoint dedicado.

### A7 — `year_status` (sealed/rolling) = **mantener informacional**

Mantener columna; persister NO ramifica behavior por year_status. UPSERT per-entity-type es la única fuente de truth para correctness.

**Why:** Las UPSERT semantics por entity-type ya resuelven el invariant "datos sealed son immutable":
- Immutable + ON CONFLICT DO NOTHING → si IBKR cambia retroactivamente un trade de 2024, lo ignoramos por diseño
- Snapshot + ON CONFLICT DO UPDATE → si IBKR re-emite un accrual con nueva info, absorbemos el update

Agregar enforcement basado en year_status crea una segunda fuente de verdad redundante con el UPSERT, y abre un branch matrix en testing. Cuando Phase 5 agregue "year lock" (decisión del usuario, distinta de "calendario terminó"), se agrega como columna separada `filed_at` o `locked_at` — NO extendiendo year_status.

**Future hook (no V1):** loguear cuando un sealed-year snapshot recibe UPDATE para detección de cambios retroactivos legítimos de IBKR (dividend WHT reclassification, etc.). Cheap signal pero no urgente.

**How to apply:** Sin cambios al schema ni al persister respecto a Phase 2. Mantener column + check constraint.

### A8 — ORM vs Core para UPSERTs = **Core para children, ORM para FlexImport**

`sqlalchemy.dialects.postgresql.insert` (Core) para todos los loops UPSERT de children. `session.add(FlexImport(...))` (ORM) para el row parent porque es 1 row y mantenemos relationships si las usamos.

**Why:** ORM no expone `ON CONFLICT` cleanly. Core lo hace explicit y leg​ible: `pg_insert(Trade.__table__).values([...]).on_conflict_do_nothing(index_elements=['transaction_id'])`. Performance ~10x mejor en batches grandes (un INSERT batch vs N session.add + flush). Para FlexImport (1 row, possibly used as parent in tests), ORM sigue siendo más natural.

`Account` (existing `_ensure_accounts`): se mantiene en ORM por simplicidad — pocos rows, lookup-then-insert pattern ya documentado en su docstring.

**How to apply:** Rewrite total de `persist()`. Helpers `_upsert_immutable(...)` y `_upsert_snapshot(...)` encapsulan el patrón Core con xmax trick para counters.

## Arquitectura resultante

### Schema (post-migration)

```
flex_imports
├── id BIGSERIAL PK
├── xml_hash VARCHAR UNIQUE NOT NULL
├── xml_bytes BYTEA NOT NULL           [A0 NEW]
├── xml_size_bytes INT NOT NULL
├── source VARCHAR NOT NULL (CHECK web_service|manual_upload)
├── user_id BIGINT FK → users ON DELETE CASCADE
├── anyo INT NOT NULL
├── period_covered_from DATE NOT NULL
├── period_covered_to DATE NOT NULL
├── year_status VARCHAR NOT NULL (CHECK rolling|sealed)
├── fetched_at TIMESTAMPTZ DEFAULT now()
├── status VARCHAR NOT NULL (CHECK ok|failed)
├── n_observed_trades INT             [A5 RENAMED from n_trades]
├── n_observed_lots_closed INT
├── n_observed_open_lots INT
├── n_observed_cash_tx INT
├── n_observed_dividends INT
├── n_observed_transfers INT
├── n_new_trades INT                  [A5 NEW]
├── n_new_lots_closed INT
├── n_new_open_lots INT
├── n_new_cash_tx INT
├── n_new_dividends INT
└── n_new_transfers INT

trades
├── id BIGSERIAL PK
├── flex_import_id BIGINT NULLABLE FK → flex_imports ON DELETE SET NULL  [A2]
├── transaction_id VARCHAR UNIQUE NOT NULL                                [existing]
├── account_id BIGINT FK → accounts
├── ... (other columns unchanged)
└── raw_attrs JSONB

closed_lots
├── id BIGSERIAL PK
├── flex_import_id BIGINT NULLABLE FK → flex_imports ON DELETE SET NULL  [A2]
├── transaction_id VARCHAR UNIQUE NOT NULL                                [NEW — parser ya lo lee]
├── ... (other columns unchanged)

cash_transactions
├── id BIGSERIAL PK
├── flex_import_id BIGINT NULLABLE FK → flex_imports ON DELETE SET NULL  [A2]
├── transaction_id VARCHAR UNIQUE NOT NULL                                [NEW — parser update requerido]
├── ... (other columns unchanged)

transfers
├── id BIGSERIAL PK
├── flex_import_id BIGINT NULLABLE FK → flex_imports ON DELETE SET NULL  [A2]
├── transaction_id VARCHAR UNIQUE NOT NULL                                [NEW — parser update requerido]
├── ... (other columns unchanged)

open_position_lots
├── id BIGSERIAL PK
├── flex_import_id BIGINT NULLABLE FK → flex_imports ON DELETE SET NULL  [A2]
├── UNIQUE (account_id, symbol, open_date, snapshot_date)                 [A3 NEW]
└── ... (other columns unchanged)

change_in_dividend_accruals
├── id BIGSERIAL PK
├── flex_import_id BIGINT NULLABLE FK → flex_imports ON DELETE SET NULL  [A2]
├── UNIQUE (account_id, conid, ex_date, pay_date, accrual_date)          [A3 NEW]
└── ... (other columns unchanged)

open_dividend_accruals
├── id BIGSERIAL PK
├── flex_import_id BIGINT NULLABLE FK → flex_imports ON DELETE SET NULL  [A2]
├── UNIQUE (account_id, conid, ex_date, pay_date, report_date)           [A3 NEW]
└── ... (other columns unchanged)
```

### Flujo del persister (post-rewrite)

```
persist(parsed, xml_bytes, source, user_id) → (flex_import_id, summary_dict)
│
├── 1. hash = xml_hash(xml_bytes)
│
├── 2. [A4 fast-path] existing = SELECT FlexImport WHERE xml_hash = hash
│   └── if existing: return (existing.id, {"hash_dedup": True})
│
├── 3. accounts_map = _ensure_accounts(session, all_ibkr_ids_in_xml)
│      (existing logic; mantiene ORM pattern + F-suffix filter)
│
├── 4. [A0] fi = FlexImport(..., xml_bytes=xml_bytes, n_observed_* = len(...))
│      session.add(fi); await session.flush()
│
├── 5. [A8 Core UPSERTs en orden de dependencia FK]
│
│   ├── n_new_trades = _upsert_immutable(
│   │       Trade.__table__,
│   │       rows=[_row(t) for t in parsed.trades if not _is_shadow(t)],
│   │       conflict_cols=['transaction_id'],
│   │   )
│   │
│   ├── trade_id_map = SELECT transaction_id, id FROM trades WHERE transaction_id IN (...)
│   │      (necesario para linkar ClosedLot.source_trade_id; queries solo los IDs que aparecen
│   │       en parsed.closed_lots para evitar full scan)
│   │
│   ├── n_new_closed = _upsert_immutable(ClosedLot.__table__, [_row(cl, trade_id_map)], ['transaction_id'])
│   │
│   ├── n_new_cash = _upsert_immutable(CashTransaction.__table__, ..., ['transaction_id'])
│   ├── n_new_transfers_data = _upsert_transfers(...)  # ver "Transfers con children" abajo
│   │
│   ├── n_new_open_lots = _upsert_snapshot(
│   │       OpenPositionLot.__table__,
│   │       rows=[...],
│   │       conflict_cols=['account_id', 'symbol', 'open_date', 'snapshot_date'],
│   │       update_cols=['qty', 'cost_basis_usd', 'mark_price_usd', 'mark_value_usd', 'flex_import_id'],
│   │   )
│   │
│   ├── n_new_change_accruals = _upsert_snapshot(
│   │       ChangeInDividendAccrual.__table__, ...,
│   │       conflict_cols=['account_id', 'conid', 'ex_date', 'pay_date', 'accrual_date'],
│   │       update_cols=[...all mutable fields..., 'flex_import_id'],
│   │   )
│   │
│   └── n_new_open_accruals = _upsert_snapshot(
│           OpenDividendAccrual.__table__, ...,
│           conflict_cols=['account_id', 'conid', 'ex_date', 'pay_date', 'report_date'],
│           update_cols=[...all mutable fields..., 'flex_import_id'],
│       )
│
├── 6. [A5] fi.n_new_trades = n_new_trades; fi.n_new_lots_closed = ...
│      await session.flush()
│
└── 7. return (fi.id, {n_observed_*, n_new_*, hash_dedup=False})
```

### Helpers Core

```python
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

_BATCH_SIZE = 5000  # margin del techo asyncpg 32767 bind params

async def _upsert_immutable(session, table, rows, conflict_cols) -> int:
    """INSERT ... ON CONFLICT DO NOTHING. Devuelve n_new (rows realmente insertadas)."""
    if not rows:
        return 0
    n_new = 0
    for batch in _chunks(rows, _BATCH_SIZE):
        stmt = pg_insert(table).values(batch)
        stmt = stmt.on_conflict_do_nothing(index_elements=conflict_cols)
        stmt = stmt.returning(text("1 AS inserted"))
        result = await session.execute(stmt)
        n_new += sum(1 for _ in result)
    return n_new


async def _upsert_snapshot(session, table, rows, conflict_cols, update_cols) -> int:
    """INSERT ... ON CONFLICT DO UPDATE. Devuelve n_touched (insert + update combinado)."""
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
        stmt = stmt.returning(text("(xmax = 0) AS inserted"))
        result = await session.execute(stmt)
        n_touched += result.rowcount  # incluye INSERTs y UPDATEs
    return n_touched
```

### Transfers con children (TransferLot)

`TransferLot` rows están atadas a `Transfer.id` (autoincrement PK), no tienen natural key propia. El patrón:

1. UPSERT del `Transfer` por `transaction_id` con `ON CONFLICT DO NOTHING returning(id, transaction_id)` → devuelve solo los **nuevamente insertados** (las NO-OP no aparecen en RETURNING para DO NOTHING)
2. SELECT post-UPSERT: `SELECT id, transaction_id FROM transfers WHERE transaction_id IN (parsed_tx_ids)` → mapa completo `transaction_id → id` (incluyendo los que ya existían)
3. Para cada Transfer **nuevo** (set difference: id en RETURNING): insertar todos sus `TransferLot` children via Core bulk insert (no UPSERT — TransferLot no tiene UNIQUE)
4. Para Transfers **existentes**: NO tocar sus TransferLots — asumir que ya están en DB (insertados en el ingest original)

**Por qué este patrón es seguro:** un Transfer immutable + sus children TransferLot son co-inmutables (un transfer no puede cambiar sus lots después de ejecutado, IBKR no los modifica). Si el Transfer ya existe, sus lots ya existen por construcción.

**Edge case (defensa):** si por bug histórico un Transfer en DB tiene TransferLots faltantes/parciales (e.g., el FOP IN bug del 2026-05-25 — ver CLAUDE.md §"counterparty accounts" + Phase 3 roadmap #7), no se auto-corrige aquí. Es deuda separada de Phase 3. El patrón "Transfer ya existe → skip children" es correcto para el caso happy y no introduce regresión en el edge case.

**Helper variant requerido:**
```python
async def _upsert_immutable_returning_inserted(session, table, rows, conflict_cols, returning_cols) -> list[Row]:
    """Como _upsert_immutable pero devuelve las rows realmente insertadas
    (sus columnas pedidas en returning_cols). NO-OP rows no aparecen."""
    if not rows:
        return []
    inserted_rows = []
    for batch in _chunks(rows, _BATCH_SIZE):
        stmt = pg_insert(table).values(batch)
        stmt = stmt.on_conflict_do_nothing(index_elements=conflict_cols)
        stmt = stmt.returning(*[table.c[col] for col in returning_cols])
        result = await session.execute(stmt)
        inserted_rows.extend(result.all())
    return inserted_rows
```

### Parser updates requeridos

- `_parse_cash_transactions`: agregar `transaction_id=tx.get("transactionID") or ""` al `ParsedCashTransaction`
- `_parse_transfers`: agregar `transaction_id=elem.get("transactionID") or ""` al `ParsedTransfer`
- `_parse_closed_lots*`: ya lee `transactionID` en `lot.get("transactionID")` (líneas 271, 305); update persister para escribirlo a la nueva columna

### Frontend / API changes

- `backend/src/ibkr_control/api/_schemas.py` campos del response de `/api/imports/upload` (`IngestSummary` o `ImportUploadResponse` — chequear nombre exacto al implementar) incluyen ambos sets de counters: `n_observed_*` + `n_new_*`
- `useIngestStream.ts` `StreamEvent` agrega campos opcionales `n_new_trades?`, `n_new_open_lots?`, etc.
- `ManualRefreshButton.tsx` no cambia su UI (puede usar `n_new_*` futuro para badges "5 nuevos" si querés en Phase 5)
- `WizardPage` response Step3: muestra ambos counters como info al usuario (cuántos rows vio vs cuántos nuevos)

## Migration plan

**Split en 2 revisions + 1 script manual** (mitiga el riesgo "TRUNCATE accidental sin re-upload" — ver § Riesgos):

### Revision 1: `phase25_idempotent_schema` (safe, no destructive)

```python
def upgrade():
    # 1. Drop CASCADE, make flex_import_id nullable, SET NULL
    for table in ['trades', 'closed_lots', 'cash_transactions', 'transfers',
                  'open_position_lots', 'change_in_dividend_accruals', 'open_dividend_accruals']:
        op.drop_constraint(f'{table}_flex_import_id_fkey', table, type_='foreignkey')
        op.alter_column(table, 'flex_import_id', nullable=True)
        op.create_foreign_key(
            f'{table}_flex_import_id_fkey', table, 'flex_imports',
            ['flex_import_id'], ['id'], ondelete='SET NULL',
        )

    # 2. Add transaction_id columns NULLABLE to closed_lots, cash_transactions, transfers
    # (rows existentes quedan con NULL — sin UNIQUE constraint todavía)
    op.add_column('closed_lots', sa.Column('transaction_id', sa.String, nullable=True))
    op.add_column('cash_transactions', sa.Column('transaction_id', sa.String, nullable=True))
    op.add_column('transfers', sa.Column('transaction_id', sa.String, nullable=True))

    # 3. Add xml_bytes NULLABLE (NOT NULL post-wipe en Revision 2)
    op.add_column('flex_imports', sa.Column('xml_bytes', sa.LargeBinary, nullable=True))

    # 4. Add n_new_* counters NULLABLE
    for col in ['n_new_trades', 'n_new_lots_closed', 'n_new_open_lots',
                'n_new_cash_tx', 'n_new_dividends', 'n_new_transfers']:
        op.add_column('flex_imports', sa.Column(col, sa.Integer, nullable=True))

    # 5. Rename n_* → n_observed_*
    op.alter_column('flex_imports', 'n_trades', new_column_name='n_observed_trades')
    op.alter_column('flex_imports', 'n_lots_closed', new_column_name='n_observed_lots_closed')
    op.alter_column('flex_imports', 'n_open_lots', new_column_name='n_observed_open_lots')
    op.alter_column('flex_imports', 'n_cash_tx', new_column_name='n_observed_cash_tx')
    op.alter_column('flex_imports', 'n_dividends', new_column_name='n_observed_dividends')
    op.alter_column('flex_imports', 'n_transfers', new_column_name='n_observed_transfers')
```

Post-Revision-1: schema preparada, data vieja sigue ahí (transaction_id=NULL en closed_lots/cash_tx/transfers, xml_bytes=NULL en flex_imports). El persister nuevo NO funciona aún — UNIQUE constraints faltan. Por eso necesitamos:

### Script manual: `backend/scripts/wipe_flex_data.py`

```python
"""Wipe + prepare para Revision 2. Pregunta confirmación al usuario."""
import sys
import asyncio
from sqlalchemy import text
from ibkr_control.db.session import get_engine

async def main():
    print("Este script va a BORRAR todos los flex_imports + children (trades,")
    print("closed_lots, open_position_lots, cash_transactions, transfers, accruals).")
    print()
    print("Preserva: flex_credentials, accounts, users, user_settings,")
    print("apscheduler_jobs, ingest_log, trm_days.")
    print()
    print("Después tenés que re-uploadear XMLs históricos manualmente desde el wizard")
    print("y disparar un Flex WS fetch para el YTD del año actual.")
    print()
    resp = input("Confirmar wipe [y/N]: ").strip().lower()
    if resp != "y":
        print("Cancelado.")
        sys.exit(1)

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE flex_imports CASCADE"))
    print("Wipe completado. Ahora corré: alembic upgrade head (Revision 2)")

if __name__ == "__main__":
    asyncio.run(main())
```

### Revision 2: `phase25_idempotent_constraints` (depends on wipe being done)

```python
def upgrade():
    # 1. Promote transaction_id en closed_lots/cash_tx/transfers a NOT NULL + UNIQUE
    # (válido porque las tablas están vacías post-wipe)
    op.alter_column('closed_lots', 'transaction_id', nullable=False)
    op.create_unique_constraint('closed_lots_transaction_id_key', 'closed_lots', ['transaction_id'])
    op.alter_column('cash_transactions', 'transaction_id', nullable=False)
    op.create_unique_constraint('cash_transactions_transaction_id_key', 'cash_transactions', ['transaction_id'])
    op.alter_column('transfers', 'transaction_id', nullable=False)
    op.create_unique_constraint('transfers_transaction_id_key', 'transfers', ['transaction_id'])

    # 2. UNIQUE constraints en snapshot tables
    op.create_unique_constraint(
        'open_position_lots_natural_key',
        'open_position_lots',
        ['account_id', 'symbol', 'open_date', 'snapshot_date'],
    )
    op.create_unique_constraint(
        'change_in_dividend_accruals_natural_key',
        'change_in_dividend_accruals',
        ['account_id', 'conid', 'ex_date', 'pay_date', 'accrual_date'],
    )
    op.create_unique_constraint(
        'open_dividend_accruals_natural_key',
        'open_dividend_accruals',
        ['account_id', 'conid', 'ex_date', 'pay_date', 'report_date'],
    )

    # 3. Promote xml_bytes a NOT NULL (válido porque flex_imports está vacío)
    op.alter_column('flex_imports', 'xml_bytes', nullable=False)
```

### Sequence ops para deploy

**Local dev / Coolify:**
1. Pull código nuevo (persister rewrite + Revision 1 + script + Revision 2)
2. `alembic upgrade <revision-1>` → schema prep, data preserved
3. `python backend/scripts/wipe_flex_data.py` → confirma + TRUNCATE
4. `alembic upgrade head` → Revision 2 agrega constraints (válido porque tablas vacías)
5. App restart con persister nuevo
6. Usuario re-upload manual: 2024 + 2025 sealed XMLs via wizard
7. Usuario trigger manual del Flex WS para 2026 YTD
8. Verificación: `SELECT id, anyo, n_observed_*, n_new_* FROM flex_imports` → 3 rows con `n_new = n_observed`

**Preserva:** `flex_credentials`, `accounts`, `apscheduler_jobs`, `users`, `user_settings`, `ingest_log` (historial intacto), `trm_days` (TRM separado del Flex).

## Plan post-migration (manual ops por el usuario)

1. Re-upload XML 2024 sealed via wizard o endpoint dedicado
2. Re-upload XML 2025 sealed via wizard o endpoint dedicado
3. Trigger manual del Flex WS fetch para YTD 2026 (Settings → Refresh manual) — primer fetch que va a funcionar con el persister nuevo
4. Verificación: `SELECT id, anyo, year_status, n_observed_trades, n_new_trades FROM flex_imports;` debería mostrar 3 rows con `n_new = n_observed` (DB arrancó vacía)

## Test strategy

### Unit tests del persister

- `test_upsert_immutable_no_op_on_conflict` — segundo INSERT con misma transaction_id devuelve n_new=0
- `test_upsert_immutable_returns_inserted_ids_only` — para uso por children (TransferLot pattern)
- `test_upsert_snapshot_updates_mutable_fields` — mismo natural key con mark_price distinto → row actualizada, n_touched=1
- `test_batch_size_respects_postgres_param_limit` — 10k+ rows con `_BATCH_SIZE=4` → N batches sin excede​r 32767 params

### Integration tests del persister (fixtures de XML reales)

- `test_re_ingest_same_xml_byte_identical` — fast-path A4 dispara, return early sin parsear (verificar via mock parser counter)
- `test_re_ingest_modified_xml_same_trades_plus_one_new` — n_new_trades=1, n_new_open_lots=0..N (snapshots se touchean)
- `test_re_ingest_after_mark_price_change` — open_position_lot UPDATE en place; new row solo si snapshot_date cambió
- `test_sealed_year_immutable_unchanged` — second ingest de 2024 con un trade modificado → DO NOTHING preserva la fila original
- `test_transfer_lots_only_inserted_for_new_transfers` — re-ingest no duplica TransferLot rows
- `test_xml_bytes_persisted` — column populated, recuperable post-fetch

### Regression tests del job orchestrator

- `test_run_idempotent_double_call` — `flex_job_mod.run()` dos veces consecutivas con mismo mock fetch → segundo call ok, ingest_log marca segundo como ok n_new=0
- `test_ingest_xml_idempotent_double_call` — mismo para manual upload
- `test_savepoint_isolates_failed_persister` — si UPSERT crashea, ingest_log marca failed pero no deja parcial data

### Migration tests

- `test_migration_preserves_flex_credentials` — credenciales sobreviven al TRUNCATE
- `test_migration_preserves_accounts` — accounts sobreviven
- `test_migration_preserves_apscheduler_jobs` — cron sobrevive
- `test_migration_drops_cascade_correctly` — borrar un flex_import nuevo deja children con flex_import_id=NULL
- `test_migration_adds_unique_constraints` — duplicate transaction_id insert ahora falla con IntegrityError

### E2E

- `wizard-happy-path.spec.ts` no cambia (re-upload de sealed XML va a producir mismas counters)
- `settings_refresh.spec.ts` actualizar para verificar n_new_* en UI feedback (cuando se implemente el badge — Phase 5 optional)

## Open questions deferred (no V1)

- **Audit log de cambios retroactivos en sealed years** (mencionado en A7). Hook para Phase 5 cuando agreguemos `year_lock` real.
- **Desagregación de n_inserted vs n_updated en snapshot tables** (mencionado en A5). Agregar columnas separadas si Phase 5 lo demanda.
- **Soft delete de flex_imports** (`superseded_at` column). Lo evaluamos si aparece use case de "borrar import mal subido" en producción.
- **Storage growth de xml_bytes a largo plazo** (~1.8GB/usuario/5y). Si crece más de lo esperado, considerar compresión explícita (BYTEA con `lz4` extension) o offload a object storage. No urgente.

## Riesgo / mitigaciones

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| TRUNCATE accidental en prod sin que el usuario haya re-uploaded XMLs | Baja | Alto | **Decisión locked:** la migration NO ejecuta `TRUNCATE` directamente. Split en dos pasos: (1) Alembic revision schema-only (add columns nullable + drop FK CASCADE + add FK SET NULL, sin promover transaction_id a NOT NULL ni agregar UNIQUE todavía); (2) script Python explícito `backend/scripts/wipe_flex_data.py` con prompt `[y/N]` que ejecuta TRUNCATE + segunda Alembic revision que promueve NOT NULL + UNIQUE. El usuario controla cuándo wipear. Local dev: corre el script antes de re-uploader. Coolify: usuario SSH al container post-deploy y corre el script — explicit + un-automatable |
| Storage explosion de xml_bytes en BYTEA | Baja | Medio | A largo plazo ~1.8GB/user/5y. Monitor; si crece, switch a `xml_path` con archivo local + presigned URL |
| Re-ingest del XML correcto pero el persister tiene un bug que crashea — usuario pierde la data | Baja | Alto | Test coverage extenso del persister (unit + integration); SAVEPOINT pattern del orchestrator aisla el crash del log |
| Race condition entre cron Flex + manual refresh durante el wipe-and-rebuild | Media | Bajo | Coolify deploy detiene container → migración corre → restart con persister nuevo. Sin overlap. Local dev: el usuario controla la secuencia |
| Reparse del XML para el primer YTD post-migration tarda más de lo esperado | Baja | Bajo | Solo el primer fetch hace work completo; subsiguientes son UPSERT NO-OP rápidos |

## Definition of Done

- [ ] Migration aplicada en dev + tests de migration pasan
- [ ] Persister rewrite (`persister.py`) completo + unit tests + integration tests pasan
- [ ] Parser updates para CashTransaction + Transfer + ClosedLot transaction_id
- [ ] `_schemas.py` actualizado con n_observed_* + n_new_*
- [ ] Job orchestrator (`job.py`) verificado idempotente: `run()` × 2 consecutive → segundo es no-op
- [ ] Regression test del bug original: triggers `_run_manual` con kind=both 3 veces → 3 corridas successful en ingest_log
- [ ] Manual smoke test en dev: re-upload 2024 + 2025 + Flex WS fetch → 3 imports verdes
- [ ] Frontend: typed events actualizados (`useIngestStream.ts`)
- [ ] Total backend tests: 215 → ~235+ (estimación: +15 unit + integration)
- [ ] CLAUDE.md update: tag `v0.2.3-persister-idempotent` apuntando al merge SHA, sección "Estado actual" Phase 2 status actualizado con la deuda D13 cerrada (D13 = "persister Flex no idempotente, cron + manual refresh fallan al segundo run", a documentarse retroactivamente en `docs/plans/2026-05-24-phase2-polish-backlog.md` § "Deuda conocida"). Eliminar también el item del bug 2 mencionado en "ManualRefreshButton race condition" si quedó en backlog tras el fix de UI
- [ ] Deploy a Coolify confirmado (cuando el usuario lo decida)

## Cross-references

- Spec maestro: `docs/specs/2026-05-24-ibkr-control-center-design.md`
- Phase 2 ingest spec (superseded en parte): `docs/specs/2026-05-24-phase2-ingestion-design.md`
- Phase 2 polish backlog (relacionado D11): `docs/plans/2026-05-24-phase2-polish-backlog.md`
- Wizard redesign post-deploy fixes (contexto del descubrimiento): `CLAUDE.md` § "Wizard redesign post-deploy fixes" item 4
- TRM bulk_upsert fix (mismo patrón _BATCH_SIZE): commit `989652d`
- D12 fix (mismo file `api/ingest.py`): commit `5dba1b4`
- Sibling renta delete-and-reinsert pattern (rechazado por nuestro use case YTD): `renta/documentos/ibkr_flex/db_ingest.py` líneas 85-128
