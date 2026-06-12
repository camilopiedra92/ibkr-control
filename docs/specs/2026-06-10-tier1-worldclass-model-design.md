# Tier 1 World-class Model — Connections/Providers + Securities Master + Restatement Log

> **Spec de diseño (2026-06-10).** Implementa los 3 items Tier 1 del gap analysis vs Plaid/Sharesight (`docs/specs/2026-06-03-saas-program-roadmap.md` §Ampliaciones W1–W10) **+ W4 (state machine de conexión), absorbido por decisión del usuario**. El usuario decidió adelantar el modelado completo ahora (2026-06-10) en vez de esperar SP7/Phase 3: "solución completa de SaaS de clase mundial, pensando en muchos providers, sin workarounds ni tech debt ni legacy". Este spec amenda el anti-scope del roadmap ("cada W en su SP") para W1/W2/W3/W4 — el resto del anti-scope sigue vigente.
>
> Base: `main` post-merge de account-multihome (PR #10, `9b0f5e5`). La semántica de tenancy per-org (multi-home) es definitiva y este spec construye sobre ella.

## Decisiones de alcance (locked)

| # | Decisión | Elección |
|---|---|---|
| T1-S1 | Empaquetado | **1 spec, 3 PRs secuenciales**: PR-1 (W1+W4 connections) → PR-2 (W2 securities master) → PR-3 (W3 restatement log). Cada branch sale de `main` post-merge del anterior. |
| T1-S2 | Profundidad W1 | **Completa**: `institutions` + `connections` + detail table tipada por provider + **state machine (W4) cableada a consumidores reales** — no status muerto. |
| T1-S3 | Capa UI | **Backend completo + UI completa**: estado de conexión en Settings (badge + CTA) y restatements visibles (badge + detalle). Rationale: "fire-and-forget = bug invisible" — un log sin UI es un log que nadie mira. |
| T1-S4 | Postura multi-provider | **Multi-provider desde el día 1** en las estructuras de identidad (detail tables por provider; `instrument_identifiers` como filas). La implementación concreta sigue siendo IBKR-only. |

## Decisiones de diseño (locked — no re-discutir)

| # | Decisión | Elección + rationale |
|---|---|---|
| T1-D1 | Config provider-specific de connections | **Detail table tipada 1:1 por provider** (`connection_ibkr_flex`), NO JSONB (blob sin tipo — mismo patrón rechazado en `counterparty_ref TEXT`), NO columnas IBKR en la tabla genérica. |
| T1-D2 | Integridad de subtipo | **Enforced en SQL**: `UNIQUE(connections.id, provider_type)` + FK compuesto desde la detail `(connection_id, provider_type)` + CHECK `provider_type = 'ibkr_flex'` en la detail. Imposible colgar una detail IBKR de una connection de otro provider. |
| T1-D3 | `institutions` | Tabla control-plane global (sin RLS, como `trm`), seeded por migración (1 fila: `ibkr`). El catálogo de brokers es dato público de sistema. |
| T1-D4 | Linaje import→connection | `flex_imports.connection_id` FK **nullable** `ON DELETE SET NULL` (`manual_upload` = NULL) + `ingest_log.connection_id` ídem. El import sobrevive al borrado de la conexión (append-only ledger pattern). |
| T1-D5 | State machine | Estados: `active / degraded / reauth_required / disabled`. Transiciones SOLO vía helper de dominio (módulo único), nunca UPDATE directo: sync OK → `active` + reset failures · error de auth IBKR → `reauth_required` + `status_reason` · otro fallo → `consecutive_failures++`, ≥2 → `degraded` (mismo umbral del health banner R4) · rotate token → `active` · `disabled` = pausa manual (el cron la salta). |
| T1-D6 | Cron multi-connection | El cron sigue enumerando orgs vía `SECURITY DEFINER` (sin cambios de privilegios — SP1 hardening intacto); dentro del contexto org itera **sus connections activas** (no-`disabled`). El ">1 login por org" pasa a primera clase. |
| T1-D7 | Identidad de instrumentos | `instruments.id` surrogate PK; identidad externa vía **`instrument_identifiers`** (`id_type` ∈ `conid/isin/cusip/figi`, `UNIQUE(id_type, id_value)`). El conid es *una fila*, no *la* PK: provider 2 agrega filas, no migra el master. La resolución actual es por `(id_type='conid')`. |
| T1-D8 | Hechos → instrumento | `instrument_id` FK `RESTRICT`: **NOT NULL fail-loud** en trades/closed_lots/open_position_lots/accruals ×2 (sujeto a checkpoint CR-1); **nullable** en cash_transactions (intereses/fees sin instrumento) y en transfers si CR-1 lo exige. **`symbol` y `asset_class` SE QUEDAN en los hechos** (fidelidad de fuente + discriminador fiscal load-bearing de Phase 2.9). Phase 3 agrupa FIFO por `(account_id, instrument_id)` — inmune a ticker changes. **[PARTIALLY SUPERSEDED 2026-06-11: security transfers = creators fail-loud; resolver cash = DB-wide + convergencia monótona — ver docs/specs/2026-06-11-transfer-instrument-lineage-design.md]** |
| T1-D9 | Escritura a tablas globales | `app_rls` recibe GRANT plano INSERT/UPDATE sobre `instruments`/`instrument_identifiers` (sin RLS que bypassear — son control plane). Riesgo de poisoning acotado: los valores vienen del XML de IBKR parseado, no de input directo del usuario. Documentado en comment de tabla. |
| T1-D10 | Detección de restatements (snapshot) | **Diff en Python pre-upsert**: SELECT batched de rows existentes por natural key (chunked, mismo patrón `_BATCH_SIZE`), comparar columnas materiales, escribir `restatement_log`, luego upsertear. NO triggers (lógica invisible en DB + fragilidad documentada de depender de comportamiento de triggers), NO CTE compleja. Las tablas snapshot son chicas (cientos de rows/org/año) — el round-trip extra es irrelevante. |
| T1-D11 | Columnas materiales | Constantes por entidad **en el persister** (junto a `update_cols`): `open_position_lots` → `qty, cost_basis_usd, open_date, asset_class`; `open_dividend_accruals` → `quantity, gross_amount_usd, tax_usd, net_amount_usd, ex_date, pay_date`. Excluidas: mark prices, `snapshot_date`, `report_date`, `flex_import_id` (churn diario esperado, no restatement). Sujeto a checkpoint CR-2. |
| T1-D12 | Restatements en immutables | `closed_lots`: detección post-persist de **sibling rows** — fila nueva cuyo natural key difiere SOLO en `fifo_pnl_usd` de una existente (caso IBIT 2024 wash-sale) → `kind='sibling_row'`. **Detección-only, nunca borra** (ambas filas son hechos first-seen). |
| T1-D13 | Severidad | `restatement_log.sealed_year` boolean: el dato restateado cae en un año con `flex_imports.year_status='sealed'` = señal "tu declaración pudo haber cambiado" (máxima severidad en UI). |
| T1-D14 | Rollout (los 3 PRs) | **AMENDED 2026-06-10 (pre-implementación, decisión del usuario):** sin deployment aún → **re-baseline + wipe por PR**. Política: **el baseline es mutable hasta el primer deploy** — cada PR de Tier 1 squashea/regenera UN baseline pristino canónicamente (container) y wipea la DB dev (`down -v`; el usuario recarga XMLs por wizard). Cero migraciones transicionales, cero expand/contract, cero data-copy de `flex_credentials` (la tabla simplemente nunca existe en el baseline nuevo). Esto supersede la versión original de esta decisión ("PR-1 data-preserving + PR-2 wipe&reload con migración"): ese patrón es el correcto POST-deploy; pre-deploy era ceremonia sin beneficiario. Precedente: SP1 re-baseline (H2) + Phase 2.9 wipe&reload. **Al primer deployment esta política expira**: migraciones inmutables + expand/contract cuando haga falta. Corolario: las migraciones NUNCA importan builders vivos de `db/rls.py` (el squash elimina el caso `a1f2c3d4e5b6`; el baseline nuevo congela todo inline). |
| T1-D15 | Naming API | `/api/credentials` → `/api/connections`. Sin alias de compatibilidad (pre-deploy, sin consumidores externos; el cliente TS se regenera canónicamente). |

## PR-1 — W1+W4: Connections, providers y state machine

Branch: `saas/w1-connections`.

### Schema

```
institutions (control plane, global, SIN RLS — patrón trm)
  id            BigInt PK
  code          String UNIQUE NOT NULL      -- 'ibkr'
  name          String NOT NULL             -- 'Interactive Brokers'
  created_at    timestamptz NOT NULL default NOW()
  -- seed por migración: 1 fila ('ibkr', 'Interactive Brokers')

connections (org-scoped, RLS)
  id                    BigInt PK
  organization_id       BigInt FK organizations CASCADE NOT NULL
  institution_id        BigInt FK institutions RESTRICT NOT NULL
  provider_type         String NOT NULL CHECK IN ('ibkr_flex')
  display_name          String NULL          -- alias del usuario p.ej. 'Cuenta familia'
  status                String NOT NULL default 'active'
                        CHECK IN ('active','degraded','reauth_required','disabled')
  status_reason         Text NULL
  last_sync_at          timestamptz NULL
  last_sync_status      String NULL CHECK IN ('ok','failed')
  consecutive_failures  Integer NOT NULL default 0
  created_at            timestamptz NOT NULL default NOW()
  UNIQUE (id, provider_type)                -- ancla del FK compuesto de subtipo (T1-D2)
  INDEX (organization_id)

connection_ibkr_flex (detail 1:1, org-scoped, RLS)
  connection_id     BigInt PK
  provider_type     String NOT NULL CHECK (provider_type = 'ibkr_flex')
  organization_id   BigInt FK organizations CASCADE NOT NULL   -- RLS necesita el GUC local
  token_encrypted   LargeBinary NOT NULL
  query_id          String NOT NULL
  last_rotated_at   timestamptz NOT NULL default NOW()
  FK (connection_id, provider_type) → connections (id, provider_type) ON DELETE CASCADE
  INDEX (organization_id)
```

- `flex_imports.connection_id` BigInt FK connections `ON DELETE SET NULL`, NULL (manual_upload = NULL). Comment documenta el linaje.
- `ingest_log.connection_id` ídem (los runs Flex registran la conexión; TRM no toca `ingest_log` — control plane, sin cambio).
- `flex_credentials` se **elimina** (datos migrados en la misma migración: cada fila → 1 `connections` (`status='active'`, `institution_id`=ibkr) + 1 `connection_ibkr_flex`). Downgrade reversible (reconstruye `flex_credentials` desde el par).

### State machine (W4) — transiciones

Módulo único de dominio (p.ej. `ingest/connections.py` o equivalente que el plan decida) — **toda transición pasa por acá** (testeable, audit-friendly); el job/cron/API nunca hacen UPDATE directo de `status`:

| Evento | Transición |
|---|---|
| Sync OK | → `active`; `consecutive_failures=0`; `last_sync_at=now`; `last_sync_status='ok'` |
| Error de autenticación IBKR (token inválido/expirado — códigos exactos a verificar en CR-3) | → `reauth_required`; `status_reason` con el error |
| Cualquier otro fallo de sync | `consecutive_failures++`; `last_sync_status='failed'`; si `>= 2` → `degraded` (umbral compartido con el health banner R4) |
| Usuario rota el token | → `active`; reset failures; `last_rotated_at=now` |
| Usuario pausa/reanuda | → `disabled` / → `active` |

El cron salta connections `disabled`. `reauth_required` NO detiene el cron (el retry natural detecta si el usuario arregló el token por fuera), pero la UI muestra el CTA.

### API + UI

- `/api/connections`: list/create/update(display_name)/rotate-token/enable/disable/delete. Shapes Pydantic nuevas; respuesta incluye `status`, `status_reason`, `last_sync_at`.
- Wizard Step 1: crea `connection` (mismo flujo, endpoint nuevo).
- Settings: sección "Conexiones" con card por connection — badge de estado (`active` verde / `degraded` ámbar / `reauth_required` rojo con CTA "Rotar token" / `disabled` gris), `last_sync_at`, acciones (rotar, pausar, borrar).
- `GET /api/health/ingest` se extiende con el estado de connections (la salud de ingesta y la salud de conexión son señales distintas y complementarias).
- Orval regen canónico (`docker compose up -d --build backend && pnpm openapi:gen`).

## PR-2 — W2: Securities master

Branch: `saas/w2-securities-master` (desde `main` post-merge PR-1).

### Schema

```
instruments (control plane, global, SIN RLS)
  id           BigInt PK
  symbol       String NOT NULL        -- ticker actual, last-seen (ticker change lo actualiza)
  name         String NULL            -- description del XML cuando está
  asset_class  String NOT NULL        -- mismo dominio que los hechos (STK/FUT/OPT/...)
  currency     String NULL
  multiplier   Numeric(20,4) NULL     -- futuros
  created_at   timestamptz NOT NULL default NOW()
  updated_at   timestamptz NOT NULL default NOW()

instrument_identifiers (control plane, global, SIN RLS)
  id             BigInt PK
  instrument_id  BigInt FK instruments CASCADE NOT NULL
  id_type        String NOT NULL CHECK IN ('conid','isin','cusip','figi')
  id_value       String NOT NULL
  UNIQUE (id_type, id_value)
  INDEX (instrument_id)
```

- Hechos: columna `instrument_id` BigInt FK instruments `RESTRICT` — NOT NULL en `trades`, `closed_lots`, `open_position_lots`, `change_in_dividend_accruals`, `open_dividend_accruals`; nullable en `cash_transactions`; `transfers` según CR-1 **[SUPERSEDED 2026-06-11: `transfers` dejó de ser "según CR-1" — NOT NULL para securities (`asset_class != 'CASH'`) / NULL para CASH, con CHECK bicondicional `ck_transfers_transfer_cash_iff_no_instrument` — ver docs/specs/2026-06-11-transfer-instrument-lineage-design.md]**. Se **agrega** index `(account_id, instrument_id)` (hot path del FIFO Phase 3) y se **conserva** `(account_id, symbol)` (los consumidores actuales consultan por symbol; remover índices se decide con evidencia de uso, no especulando — criterio db-hardening).
- `symbol`/`asset_class` quedan en los hechos (T1-D8).

### Persister

- Stage `_ensure_instruments` (espejo de `_ensure_accounts`): recolecta `(conid, symbol, asset_class, isin?, currency?, multiplier?, description?)` de todas las entidades parseadas → resuelve por identifier `('conid', value)` → inserta instruments + identifier rows faltantes (conid siempre; isin cuando el XML lo trae) → DO UPDATE last-seen de atributos del instrument (ticker change actualiza `symbol` + `updated_at`).
- Parser: helper fail-loud `_require_conid` (patrón `_require_asset_class` de Phase 2.9) en los tags donde CR-1 confirme presencia 100%.
- Concurrencia cross-org sobre las tablas globales: ON CONFLICT en `(id_type, id_value)` la resuelve (mismo patrón que el resto del persister).

### Rollout

Wipe & reload (T1-D14): la migración exige tablas de hechos vacías para el NOT NULL (documentado en docstring de la migración, patrón Phase 2.9). El usuario recarga los XMLs vía wizard/manual upload.

### Notas de implementación (post-review W2, 2026-06-10 — decisiones conscientes)

- **Last-seen-wins intra-batch**: cuando dos creators del mismo conid traen atributos distintos en un batch, gana el último SOLO como resolución silenciosa para `currency`/`multiplier`/`name` (IBKR es internamente consistente por conid dentro de un statement; `symbol` last-seen es by-design para ticker changes; `asset_class` queda congelado first-seen — estable por conid).
- **`trades.raw_attrs` slimming**: conid/isin/description/currency/multiplier promovidos a columnas tipadas salen del JSONB — sigue la convención existente (`_TRADE_TYPED_ATTRS` excluye lo tipado del catch-all); el dato vive ahora en `instruments`/columnas, no se pierde.
- **Hook para W3**: el loop DO UPDATE de `_ensure_instruments` computa un boolean `changed` y descarta los valores viejos — W3 (restatement log) debe refactorizarlo para capturar old→new ANTES de mutar, no asumir que el diff existe.
- **Orphan instruments por carrera cross-org**: el perdedor del ON CONFLICT puede dejar un `Instrument` sin identifier (inofensivo — los hechos apuntan al ganador vía re-SELECT). Aceptado; un janitor futuro puede cosecharlos.

## PR-3 — W3: Restatement log

Branch: `saas/w3-restatement-log` (desde `main` post-merge PR-2).

### Schema

```
restatement_log (org-scoped, RLS)
  id               BigInt PK
  organization_id  BigInt FK organizations CASCADE NOT NULL
  flex_import_id   BigInt FK flex_imports SET NULL NULL   -- el import que lo causó
  table_name       String NOT NULL
  natural_key      JSONB NOT NULL          -- columnas del natural key + valores
  column_name      String NOT NULL         -- '*' para kind='sibling_row'
  old_value        Text NULL
  new_value        Text NULL
  kind             String NOT NULL CHECK IN ('value_update','sibling_row')
  sealed_year      Boolean NOT NULL        -- T1-D13
  detected_at      timestamptz NOT NULL default NOW()
  INDEX (organization_id, detected_at)
  INDEX (flex_import_id)
```

### Detección

1. **Snapshot (`value_update`)**: en `_upsert_snapshot` (o variante `_upsert_snapshot_with_audit` que el plan decida): SELECT batched por natural keys → diff Python sobre columnas materiales (T1-D11) → INSERT a `restatement_log` → upsert normal. Re-run idéntico ⇒ 0 restatements (test de oro del replay suite).
2. **Siblings (`sibling_row`)**: post-persist de `closed_lots`, para las rows recién insertadas (vía `_upsert_immutable_returning_inserted`), buscar existentes con mismo `(organization_id, transaction_id, close_datetime, qty)` y distinto `fifo_pnl_usd` → log una vez por sibling nuevo. Nunca borra.

### Surfacing

- `GET /api/ingest/restatements` (paginado, filtros por import/tabla/sealed).
- `GET /api/health/ingest` extendido con counts de restatements recientes.
- Settings → "Salud de ingesta": badge "N restatements (M en año sealed)" — rojo si `sealed_year`, con tabla de detalle (tabla afectada, columna, old → new, import).
- El SSE del manual refresh agrega el count al summary (mismo contrato `step`/`status` ya tested — sin romper el parser del frontend).

## Checkpoints contra data real (obligatorios — lección Phase 2.5/2.7/2.9)

| # | Checkpoint | Cuándo | Qué valida |
|---|---|---|---|
| CR-1 | Grep de `conid` por tag contra los 3 fixtures reales (2024/2025/2026) | PR-2, antes de lockear NOT NULL | Presencia 100% de conid en Trade/Lot/OpenPosition/accruals; decidir transfers y cash por evidencia **[PARTIALLY SUPERSEDED 2026-06-11: security transfers = creators fail-loud; resolver cash = DB-wide + convergencia monótona — ver docs/specs/2026-06-11-transfer-instrument-lineage-design.md]** |
| CR-2 | Diff de dos XMLs YTD reales consecutivos | PR-3, antes de lockear T1-D11 | Que las columnas materiales no produzcan falsos positivos por churn diario esperado |
| CR-3 | Códigos de error de auth del Flex WS (client.py + cassettes VCR reales) | PR-1, antes de cablear transiciones | Qué errores son "token inválido/expirado" vs transitorios (1001 BUSY NO es auth) |

Si un checkpoint contradice el spec, se documenta como amendment inline (patrón A3 de Phase 2.5) — el spec no se reescribe en silencio.

## Testing

- TDD por task (failing test → impl → commit), subagent-driven con doble review (spec + code-quality) por task + review holístico final — flujo SP1.
- Migraciones: autogeneradas canónicamente (container), downgrade reversible (salvo el wipe documentado de PR-2), drift test (automático con los modelos), migration tests de data-migration (PR-1: creds → connections round-trip).
- Replay suite extendido: idempotencia con instruments (PR-2); re-run idéntico ⇒ 0 restatements + XML modificado sintéticamente ⇒ restatements esperados (PR-3).
- State machine: unit tests del helper de transiciones + integration test del job con fake auth error ⇒ `reauth_required` (PR-1).
- Contrato cross-stack: al menos 1 test que invoque el productor real del SSE/health y asserte la shape que consume el frontend (lección TRM 2026-05-25).
- Frontend: vitest para componentes nuevos (ConnectionCard, RestatementsPanel) + Playwright specs de Settings.

## Docs a actualizar (en los PRs)

- Roadmap SSOT: filas SP3/SP5/SP7/SP8 — W1/W4 dejan de estar mapeados a SP7 (hechos acá); W2/W3 marcados hechos; anti-scope amendado con nota "adelantados por decisión del usuario 2026-06-10, spec tier1".
- CLAUDE.md §"Cómo continuar": entrada por PR mergeado, como siempre.
- SP7 hereda: el refactor del pipeline sobre cola durable opera ya sobre `connections` (su decisión gorda se simplifica).
