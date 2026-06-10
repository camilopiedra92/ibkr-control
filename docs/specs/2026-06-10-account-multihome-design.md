# Account multi-home — tenancy canónica per-org (patrón Plaid/Sharesight)

**Fecha:** 2026-06-10
**Branch destino:** `saas/account-multihome` (desde `main`, post-merge PR #9 `2d6206a`)
**Depende de:** SP1 completo (tenancy + RLS + runtime wiring + db-hardening) — todo en `main`.
**Supersede:** la decisión "cuenta broker single-org por diseño" (`accounts.ibkr_account_id` UNIQUE global) y **H2** del spec `2026-06-03-sp1-hardening-close-gaps-design.md` (`AccountClaimedError` / 409 genérico).
**Estado:** implementado (PR #N, CI verde — pending merge).

## 1. Problema y decisión de producto

La decisión SP1 "una cuenta broker vive en exactamente UN org" (`ibkr_account_id`
UNIQUE global + `transaction_id` UNIQUE global) era coherente pero **no es el
patrón canónico de SaaS multi-tenant** y tiene dos costos de producto reales:

1. **Co-titulares en orgs distintas** (divorcio, socios, dos households): ambos
   tienen login IBKR propio y token Flex propio, ambos necesitan la cuenta en
   SU declaración — pero solo un org podía conectarla; el otro dependía de un
   access_grant de lectura sobre el org ajeno.
2. **Vector de squatting**: el path de upload manual de XML no es
   auto-verificante — un XML fabricado podía reclamar cualquier
   `ibkr_account_id` y bloquear el onboarding del dueño legítimo, que solo veía
   un 409 genérico sin recurso.

**Decisión (usuario, 2026-06-10): adoptar el patrón Plaid/Sharesight.** Dos
orgs pueden conectar la misma cuenta IBKR, cada uno en su universo aislado. El
SaaS no verifica exclusividad de propiedad; los tenants son mundos
independientes. Trade-offs aceptados explícitamente:

- **Duplicación de hechos**: cada org tiene SU copia de trades/lots de la
  cuenta compartida; las copias pueden divergir temporalmente según el timing
  de ingest de cada org. Fiscalmente correcto: cada org responde por su copia.
- **`access_grants` se mantiene intacto**: multi-home resuelve co-titulares
  (cada uno conecta en su org); la delegación de lectura (contador) es otro
  caso y sigue siendo el objeto de SP2.

## 2. Decisiones técnicas (locked)

- **M1 — Org-scoping uniforme de unicidad** (enfoque A, aprobado): la regla
  única del schema pasa a ser *"los hechos son únicos POR TENANT"*.
  - `accounts`: `UNIQUE (organization_id, ibkr_account_id)` con
    `name="uq_accounts_org_ibkr_account_id"` (reemplaza el `unique=True`
    global).
  - `trades`, `transfers`, `cash_transactions`:
    `UNIQUE (organization_id, transaction_id)` con
    `name="uq_<tabla>_org_transaction_id"` (reemplaza el `unique=True` global
    de `transaction_id`). `transfers` fuerza org-scoping de todos modos: sus
    columnas de cuenta son nullables (exclusive arc con counterparties).
  - `closed_lots`: la natural key gana `organization_id` al frente →
    `UNIQUE (organization_id, transaction_id, close_datetime, qty, fifo_pnl_usd)`,
    mismo `name="uq_closed_lots_natural_key"`.
  - `open_position_lots` + los 2 accruals: **sin cambios** — sus natural keys
    arrancan en `account_id`, que ahora es per-org transitivo (cada org tiene
    su propia fila de accounts).
  - Enfoque B (scoping mixto por `account_id`) rechazado: dos reglas distintas
    sin ganancia práctica. Enfoque C (org + account en la key) rechazado:
    redundante.
- **M2 — Limpieza de índices redundantes**: las composite uniques nuevas
  empiezan en `organization_id` y cubren el prefijo que usa la política RLS →
  los 5 índices sueltos `ix_accounts_organization_id`,
  `ix_trades_organization_id`, `ix_transfers_organization_id`,
  `ix_cash_transactions_organization_id`, `ix_closed_lots_organization_id`
  quedan redundantes y **se eliminan**. Las tablas sin composite nueva
  (open_position_lots, accruals, etc.) conservan su índice de org.
- **M3 — Desmantelamiento completo de H2**: `AccountClaimedError`, el SAVEPOINT
  de claim en `_ensure_accounts`, y los handlers 409 en
  `api/setup.py`/`api/imports.py`/`ingest/flex/job.py` se eliminan. Con
  unicidad per-org + RLS, el conflicto cross-org **deja de existir**; el único
  conflicto posible es same-org y el re-SELECT bajo RLS sí lo ve → el patrón
  idempotente simple (`ON CONFLICT DO NOTHING` + re-select) pasa a ser
  correcto, y de paso cubre el race same-org de dos uploads concurrentes (hoy
  daría un 409 engañoso "pertenece a otra organización"). El razonamiento de
  H2 ("ON CONFLICT NO alcanza") era correcto SOLO bajo unicidad global — queda
  documentado como SUPERSEDED, no como error.
- **M4 — Comments del schema reescritos** (son contrato, pasarían a ser
  falsos): `accounts` ("ibkr_account_id UNIQUE global es correcto" → per-org,
  multi-home por diseño) y los 3 comments de hechos ("transaction_id UNIQUE
  global correcto" → único por tenant; la misma cuenta broker puede existir en
  N orgs, cada una con su copia de los hechos). Espejados en la migración.
- **M5 — OpenAPI + frontend**: remover los 409 handlers cambia `openapi.json`
  → regen canónico del cliente TS (`pnpm openapi:gen`, container corriendo,
  lección D12) + grep en `frontend/src` por el manejo del 409
  `ACCOUNT_CLAIMED`/"pertenece a otra organización" para eliminar branches y
  strings muertos.
- **M6 — Una sola migración Alembic autogenerada canónicamente** (container,
  lección Phase 2.9; falso positivo `apscheduler_jobs` removido si aparece):
  drop de los 5 uniques viejos + create de los 5 composites + drop de los 5
  índices redundantes + comments. Dev DB es single-org → las composites
  validan sin conflicto, cero pérdida de datos. Downgrade reversible (el
  downgrade recrea los uniques globales — solo es ejecutable si no hay datos
  multi-home, documentarlo en el docstring).
- **M7 — Docs según convención del repo** (cambio de criterio = nota
  SUPERSEDED, no reescritura de historia):
  - Spec SP1-hardening §H2: nota "SUPERSEDED 2026-06-10 por account-multihome".
  - CLAUDE.md: la línea "accounts.ibkr_account_id UNIQUE-global = cuenta broker
    single-org por diseño" (cortes de alcance de SP1-hardening) y las
    menciones en lecciones Phase 2.6/2.8 ganan nota de superseded apuntando a
    este spec; bullet nuevo en "Estado del programa SaaS".
  - Roadmap SSOT (`2026-06-03-saas-program-roadmap.md`): actualizar la mención
    si existe.

## 3. Lo que NO cambia

- `participations` (SCD-2, propiedad multi-party DENTRO de un org) — la
  conjunta 50/50 sigue modelándose igual; PK por surrogate `account_id` ya es
  per-org transitivo.
- `access_grants` + su política RLS especial — intacto; SP2 construye el
  enforcement sobre esto.
- RLS, roles, boot guard, pool, índices FK del db-hardening — intactos.
- `counterparties` y `flex_imports` — ya eran per-org desde antes (el patrón
  que ahora se generaliza).
- Dedup de imports `(organization_id, xml_hash)` — ya correcto: dos orgs pueden
  ingerir el mismo XML, cada uno una vez.

## 4. Testing

- Drift test (gratis para M1/M2/M4).
- **Tests nuevos de multi-home** (reemplazan los de H2, no solo se borran):
  1. Dos orgs conectan la misma `ibkr_account_id` → ambos INSERTs funcionan;
     bajo RLS cada org ve solo SU fila de accounts.
  2. El mismo XML ingerido por ambos orgs (persister directo, fixture real) →
     dos copias independientes de los hechos, counts iguales en ambos
     universos, cero cross-talk.
  3. Re-ingest same-org sigue idempotente (`items_processed` estable) — la
     idempotencia per-org sobrevive el re-scoping de conflict_cols.
  4. Race same-org de `_ensure_accounts` simplificado: segundo insert de la
     misma cuenta en el mismo org no explota (ON CONFLICT + re-select).
- Suite completa (357 baseline) + ruff + boot smoke + openapi regen verificado.

## 5. Criterios de aceptación

1. Test multi-home: dos orgs con la misma cuenta IBKR y el mismo XML ingerido,
   universos completamente aislados (verificado bajo `app_rls`).
2. `AccountClaimedError` no existe en el codebase (grep = 0); ningún endpoint
   devuelve el 409 de cuenta reclamada; openapi.json y cliente TS regenerados
   sin referencias.
3. Drift test verde; migración upgrade+downgrade reversible sobre dev DB con
   datos.
4. Los 4 comments de schema reflejan la semántica per-org (verificable vía
   `\d+` o pg_description).
5. Suite completa verde; idempotencia per-org probada (test #3).
6. Docs: notas SUPERSEDED en spec H2 + CLAUDE.md + roadmap; bullet de programa
   actualizado.
