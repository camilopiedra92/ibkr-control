# Persister Cleanup — Counterparties + transfer_lots drop (design)

**Fecha:** 2026-06-02
**Branch destino:** `fix/persister-counterparties` (pre-Phase-3 cleanup)
**Tag al completar:** `v0.2.5-persister-cleanup` (post-merge, per convención Phase 2.5)
**Autor:** brainstorming session 2026-06-02

## 1. Contexto y motivación

Resuelve dos items de la capa de datos (Phase 2 persister) documentados en
`CLAUDE.md` §Roadmap Phase 3 como decisiones abiertas #6 y #7. Ambos tocan el
mismo path (`<Transfer>` parsing + persist), por eso van en un solo branch
**antes** de Phase 3: el domain layer (FIFO, classification) se construye sobre
`accounts` + `transfers`, y no se debe edificar sobre datos sucios o schema
muerto.

Criterio rector acordado con el usuario: **arquitectura de clase mundial, sin
tech debt, sin workarounds, sin código legacy.** Cuando una decisión "V1
pragmática" choca con ese criterio, gana el criterio.

### #6 — `Account` huérfanos por counterparties externos (correctness)

`persister.py:124-129` recolecta los `src`/`dst` de cada `<Transfer>` y los
mete en `all_account_ids` → `_ensure_accounts` les crea filas en `accounts`.
El único filtro es F-suffix (shadow accounts). Cuando un `<Transfer>` viene de
un broker externo (FOP IN de RSUs desde Shareworks/Solium/Morgan Stanley
StockPlan, ej. `CS-999999-99`, `deliveringBroker="0015"`), ese ID externo
queda como fila `Account` huérfana: sin participation, sin trades, referenciada
solo por 1 transfer. **Viola el invariante "toda fila en `accounts` es una
cuenta propia del usuario".** Verificado vivo en el código y en la DB real
(1 fila huérfana `CS-999999-99`).

### #7 — `transfer_lots` impoblable (dead schema)

Investigación contra el XML real 2026 (`flex_imports.id=18`, web_service,
2026-01-01..2026-05-22) + fixtures 2024/2025. Hallazgos definitivos:

1. **`transfer_lots` tiene 0 filas para los 16 transfers reales** (3 imports).
   El bug no es específico de FOP — es universal.
2. **Causa: `<TransferLot>` es _sibling_ de `<Transfer>` dentro de
   `<Transfers>`, no hijo anidado.** El parser hace
   `tr.iterchildren("TransferLot")` (`parser.py:543`) → encuentra cero.
   Confirmado en 2025 Y 2026. El branch `elif tag == "TransferLots": pass`
   (`parser.py:153-155`) ya ignoraba la sección standalone, y su comentario
   "when nested inside a Transfer they are handled" es **incorrecto** — nunca
   están anidados.
3. **El `<TransferLot>` de Activity Flex no contiene los datos que la tabla
   necesita.** Columnas NOT NULL: `original_open_date`, `cost_basis_usd`. El
   elemento XML solo trae `symbol`, `quantity`, `date`, `transferPrice="0"`,
   `levelOfDetail="LOT"`, `transactionID=""` (vacío). **`costBasis` y
   `openDateTime`/`originalOpenDate` AUSENTES.**

Conclusión: aún arreglando el bug de sibling, `transfer_lots` es
**estructuralmente impoblable** desde Activity Flex (la única fuente del
proyecto, decisión locked #10). Una tabla con columnas NOT NULL que la fuente
real nunca provee = schema muerto/legacy.

El cost basis del transfer **sí se preserva** donde importa:
`open_position_lots.cost_basis_usd` (lotes resultantes) + `closed_lots.cost_basis_usd`
(al vender). Verificado en smoke test: $42.40/acción GLOB. La auditabilidad
"cuánto cost basis trajo el transfer X" es una pregunta **derivada/de dominio**
(Phase 3), reconstruible con join `transfers → lots resultantes` por
símbolo/fecha — no es responsabilidad de la capa de ingest cruda.

## 2. Decisiones locked (no re-discutir)

| # | Decisión | Rationale |
|---|---|---|
| **C1** | #6: tabla `counterparties` + **exclusive arc** en `transfers` | Integridad referencial, queryable, dedup, estados ilegales irrepresentables. Descarta `counterparty_ref TEXT` (dos formas de decir lo mismo, sin tipo, sin FK) y el par `src/dst_counterparty_ref` (columnas siempre-NULL). |
| **C2** | `counterparties` espeja a `accounts`: `id`, `external_id` (UNIQUE global), `source_label` (nullable), `created_at`. **Sin `user_id`** | Consistencia con `accounts` (también global single-user). El particionado per-user multi-user es item futuro que aplica a ambas tablas juntas — no se adelanta. |
| **C3** | #7: **drop `transfer_lots`** (tabla, modelo, parser, persister, tests) | Schema impoblable desde Activity Flex. Cost basis preservado en open/closed lots. Re-agregable trivialmente si aparece fuente más rica. |
| **C4** | Todo en **una sola Alembic revision atómica** con data-migration inline | Schema nunca queda inconsistente; deploy atómico; downgrade reversible; sin paso manual. |
| **C5** | Persister crea `Account` SOLO para IDs en `<AccountInformation>` (`parsed.accounts`, no-shadow) | Restaura el invariante. Los peers de transfers se resuelven contra `accounts` existentes; los no-propios → `counterparties`. |
| **C6** | CHECK del exclusive arc se valida contra data real antes de lockear | Lección Phase 2.5: "natural keys/constraints validar contra data real". Data real (16 transfers) confirma que cada lado siempre tiene endpoint → "exactamente uno" es seguro. La migration **asserta** la precondición (fail-loud) antes de crear el CHECK. |

## 3. Diseño #6 — counterparties + exclusive arc

### 3.1 Schema

Tabla nueva `counterparties`:

```
counterparties
  id            BigInteger PK autoincrement
  external_id   String  UNIQUE NOT NULL   -- ej. 'CS-999999-99' (el attr `account` del Transfer)
  source_label  String  NULL              -- ej. 'Shareworks/Solium' o deliveringBroker; best-effort
  created_at    timestamptz NOT NULL DEFAULT NOW()
```

Columnas nuevas en `transfers` (los FKs `src_account_id`/`dst_account_id` ya
existen como nullable — **no se dropea nada**):

```
transfers
  src_account_id        FK accounts        nullable  (ya existe)
  dst_account_id        FK accounts        nullable  (ya existe)
  src_counterparty_id   FK counterparties  nullable  (NUEVO)
  dst_counterparty_id   FK counterparties  nullable  (NUEVO)
```

CHECK constraints (exclusive arc, uno por lado):

```sql
CONSTRAINT ck_transfers_src_arc
  CHECK ((src_account_id IS NOT NULL) <> (src_counterparty_id IS NOT NULL))
CONSTRAINT ck_transfers_dst_arc
  CHECK ((dst_account_id IS NOT NULL) <> (dst_counterparty_id IS NOT NULL))
```

`<>` (XOR booleano) garantiza **exactamente uno** por lado. Data real confirma
que todo transfer tiene ambos endpoints (accountId propio + peer propio/externo),
así que "exactamente uno" se cumple. Si durante la implementación un import real
violara la precondición (lado sin endpoint), la migration falla-loud y se
relaja a "a lo sumo uno" (`NOT (a AND b)`) documentando el caso — pero la data
disponible no lo requiere.

`direction` (IN/OUT) se mantiene: es source data de IBKR y desambigua qué lado
es el peer.

### 3.2 Persister

Cambios en `persister.py`:

1. **`all_account_ids` se construye SOLO desde `parsed.accounts`** (no-shadow).
   Eliminar la recolección desde `transfers` (líneas 124-129). Trades, lots,
   cash_tx y accruals siempre pertenecen a cuentas en `<AccountInformation>`
   (estructura de Activity Flex) → siguen resolviendo vía `accounts_map`; si un
   ID inesperado no resuelve, es una anomalía de datos (fail-loud, no
   auto-crear).
2. **Nuevo helper `_ensure_counterparties(session, external_ids) -> dict[str,int]`**
   análogo a `_ensure_accounts`: UPSERT idempotente por `external_id`
   (`ON CONFLICT (external_id) DO NOTHING` + SELECT), devuelve map id.
3. **Resolución de peer por lado** en el bloque de transfers (323-354): para
   cada `src`/`dst`:
   - Si el `ibkr_account_id` está en `accounts_map` (cuenta propia) → setear
     `*_account_id` FK, `*_counterparty_id = NULL`.
   - Si no → resolver/crear en `counterparties`, setear `*_counterparty_id`,
     `*_account_id = NULL`.
   - `source_label`: best-effort desde el contexto del transfer (no crítico;
     puede quedar NULL en V1 y enriquecerse después).
4. El filtro shadow de transfers (326-333) se mantiene (F-suffix nunca debe
   llegar como account ni como counterparty).

### 3.3 Edge case — cuenta propia aún no importada

Si un transfer referencia una cuenta propia que todavía no está en `accounts`,
se trataría como counterparty. Mitigaciones:
- El wizard detect-first registra todas las cuentas propias antes del ingest.
- Los formatos de ID difieren (`Uxxxxxxxx` propio vs `CS-xxxxxx-xx` externo) →
  colisión improbable.
- Aceptable para V1; documentar el contrato. Un import posterior no
  "re-parenta" automáticamente (los transfers son immutable por `transaction_id`).

## 4. Diseño #7 — drop transfer_lots

Eliminar (en orden, sin dejar referencias colgantes):

| Artefacto | Acción |
|---|---|
| `db/models/flex_raw.py:190-199` `class TransferLot` | Borrar |
| `db/__init__.py:9,18` export `TransferLot` | Quitar de import + `__all__` |
| `ingest/flex/_models.py:87,91` `ParsedTransfer.lots` + `ParsedTransferLot` | Borrar el campo `lots` y la dataclass |
| `ingest/flex/parser.py:542-549` loop de lots nested + `parser.py:24` import `ParsedTransferLot` | Borrar el loop muerto y el import |
| `ingest/flex/parser.py:153-155` branch `elif tag == "TransferLots": pass` | Mantener el branch con `pass` y **corregir el comentario**: "TransferLots ignorado deliberadamente — impoblable desde Activity Flex (sin cost_basis/open_date), ver spec 2026-06-02". No remover el branch (documenta intención explícita vs caer en ignore genérico). |
| `ingest/flex/persister.py:13-17` docstring + `47` import + `367-395` bloque de lot_rows | Borrar el bloque de persist de lots + import + actualizar docstring |
| `ingest/flex/_upsert_helpers.py:89` comentario que menciona TransferLot | Actualizar (el helper `_upsert_immutable_returning_inserted` sigue usándose para Transfers, solo cambia la razón) |
| `_known_tags.py:21` `"TransferLots"` | Mantener (el tag sigue apareciendo en el XML; el audit no debe abortar). Actualizar comentario: "ignored — unpopulatable from Activity Flex, see spec 2026-06-02" |
| `tests/ingest/flex/test_persister_idempotent.py:223-264` `test_transfer_lots_not_duplicated_on_reingest` + imports | Borrar el test y los imports `TransferLot`/`ParsedTransferLot` |
| `tests/test_phase2_flex_raw_migration.py:14` `'transfer_lots'` en lista de tablas esperadas | Quitar de la lista |
| `tests/ingest/flex/test_persister.py:11` import `ParsedTransferLot` | Quitar si no se usa tras el cleanup |

**Nota:** `_upsert_immutable_returning_inserted` se introdujo (Phase 2.5) en
parte para identificar qué Transfers eran nuevos y solo entonces insertar sus
TransferLot children. Al eliminar los children, los Transfers pueden volver al
UPSERT immutable normal (`_upsert_immutable` sin returning), simplificando el
bloque. Verificar que ningún otro caller dependa del returning antes de
simplificar; si lo hace, dejar el helper y solo quitar el uso de su resultado
para lots.

## 5. Migración (revision única, atómica)

`down_revision = "2b0b2863c6e9"` (head actual, Phase 2.6).

**upgrade()** en orden:
1. `CREATE TABLE counterparties` (+ UNIQUE en `external_id`).
2. `ADD COLUMN transfers.src_counterparty_id`, `dst_counterparty_id` (FK, nullable).
3. **Data-migration (reconciliación):**
   a. Para cada `Account` que NO esté en ninguna `<AccountInformation>` y solo
      sea referenciado por transfers (huérfano counterparty, ej.
      `CS-999999-99`): `INSERT INTO counterparties (external_id) ... ON CONFLICT
      DO NOTHING`; `UPDATE transfers SET src_counterparty_id=<cp>,
      src_account_id=NULL WHERE src_account_id=<orphan>` (idem dst);
      `DELETE FROM accounts WHERE id=<orphan>`.
      - Identificación del huérfano: `accounts` cuyo `ibkr_account_id` NO matchea
        el patrón de cuenta propia esperado / no tiene participation / no aparece
        en otras tablas de hechos. En la práctica: el set de `accounts` no
        referenciados por trades/closed_lots/open_position_lots/cash_tx/accruals
        y sí por transfers. Implementar como query explícita, no heurística frágil.
   b. **Assert precondición del CHECK** (fail-loud): verificar que tras la
      reconciliación, todo transfer cumple exactamente-uno por lado. Si no,
      abortar la migration con mensaje claro (no crear un CHECK que rebote).
4. `ADD CONSTRAINT ck_transfers_src_arc`, `ck_transfers_dst_arc`.
5. `DROP TABLE transfer_lots`.

**downgrade()** en orden inverso:
1. `CREATE TABLE transfer_lots` (definición original).
2. `DROP CONSTRAINT` los 2 CHECK.
3. Data-migration inversa best-effort: re-crear `Account` desde
   `counterparties` referenciados + re-apuntar transfers a `src/dst_account_id`.
   (Lossy en `source_label`; documentar que el downgrade restaura el estado
   buggy a propósito para reversibilidad.)
4. `DROP COLUMN src_counterparty_id`, `dst_counterparty_id`.
5. `DROP TABLE counterparties`.

El test `test_migrations_apply_cleanly_and_match_metadata` (corre `upgrade head`
contra container fresh + verifica drift vs `Base.metadata`) debe pasar tras los
cambios de modelo.

## 6. Estrategia de testing (TDD)

- **Fixture nuevo sanitizado**: `<Transfer type="FOP" direction="IN">` con peer
  externo (`account="CS-999999-99"`, `deliveringBroker="0015"`, símbolo
  sanitizado) + un INTERNAL entre cuentas propias. Construido a partir de la
  estructura real observada (NO commitear el XML real con IDs reales).
- **#6 tests** (`test_persister*.py`):
  - Persist de un FOP externo → crea fila en `counterparties`, NO en `accounts`;
    el transfer tiene `src_counterparty_id` set + `src_account_id NULL`.
  - Persist de un INTERNAL propio → ambos lados `*_account_id` set, counterparty
    NULL.
  - Re-ingest idempotente → no duplica counterparties (UPSERT por `external_id`).
  - Exclusive arc CHECK: intento de insertar transfer con ambos NULL o ambos set
    en un lado → rebota (test a nivel DB).
- **Migration tests** (`testcontainers-postgres`, patrón Phase 2.6 R3):
  - Aplicar la revision sobre un schema con el huérfano `CS-999999-99`
    pre-sembrado → counterparty creado, transfer re-apuntado, account borrado,
    CHECK presente, `transfer_lots` ausente.
  - Downgrade revierte limpio.
- **#7**: confirmar que el persister ya no referencia `TransferLot` y que el
  parser ignora `<TransferLots>` sin abortar el audit de `_known_tags`.
- Suite completa: `uv run pytest -q` debe quedar verde (ajustar el conteo;
  baseline 290).

## 7. Fuera de scope (queda Phase 3+)

- Reconstrucción de auditabilidad "cost basis por transfer" vía join/vista
  (domain layer Phase 3).
- Particionado per-user de `accounts` + `counterparties` (item multi-user
  futuro, documentado).
- Enriquecimiento de `source_label` con nombre canónico del broker
  (best-effort en V1).
- Cualquier cambio al recompute de lotes (Phase 3).

## 8. Criterios de aceptación

- [ ] `accounts` no contiene ningún ID que no esté en `<AccountInformation>`
      (invariante restaurado); el huérfano `CS-999999-99` migrado a
      `counterparties`.
- [ ] `transfers` con peer externo tienen `*_counterparty_id` set y
      `*_account_id NULL`, cumpliendo el exclusive arc CHECK.
- [ ] `transfer_lots` eliminada del schema y del código; sin referencias
      colgantes (`grep -rn "TransferLot\|transfer_lots" src tests` vacío salvo
      el comentario de `_known_tags`).
- [ ] Migration única aplica y revierte limpio contra container fresh.
- [ ] `uv run pytest -q` verde; `pnpm build` exit 0 (frontend no debería tocarse,
      pero el cliente OpenAPI puede regenerarse si cambia algún schema expuesto —
      `transfers` no se expone aún, así que probablemente no).
- [ ] Smoke test (manual, usuario): re-ingest del Flex real 2x → counterparty no
      duplicado, sin huérfanos nuevos, idempotente.

## 9. Referencias

- `CLAUDE.md` §Roadmap Phase 3 decisiones #6 y #7
- `docs/specs/2026-05-25-flex-persister-idempotent-design.md` (patrón UPSERT,
  helpers Core, lección "validar contra data real")
- Memoria `ibkr-control-counterparty-accounts` (contexto del bug #6)
- Investigación de data real: `flex_imports.id=18` (2026 YTD), fixtures
  2024/2025 sanitizados
