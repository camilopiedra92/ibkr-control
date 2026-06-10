# SP1 follow-up — DB hardening (índices FK, pool, invariantes explícitos)

**Fecha:** 2026-06-10
**Branch destino:** `saas/sp1-db-hardening` (desde `main`, post-merge PR #7 `731cacc`)
**Depende de:** SP1 + SP1-hardening + SP1-rls-runtime-wiring — los tres en `main`.
**Estado:** spec aprobado, pendiente plan.

## 1. Problema

La auditoría de DB del 2026-06-10 (modelo de datos + arquitectura RLS + patrones
de acceso, verificada contra código y fixtures reales) confirmó que la fundación
es sólida, pero detectó **cinco gaps que no estaban mapeados a ningún SP del
roadmap** — es decir, deuda real sin dueño, a diferencia de los cortes de
alcance documentados (cola → SP5, retención → SP8, 3 roles → SP4):

1. **Índices FK faltantes (§2.1, el grave).** Postgres no indexa FKs
   automáticamente. El cleanup latest-1 del persister hace `DELETE FROM
   flex_imports` en **cada ingest** (`persister.py:245-274`); cada delete
   dispara el `ON DELETE SET NULL` de `flex_import_id` en 7 tablas de hechos
   **sin índice** → 7 seq scans por ingest, por org, por día. Costo que crece
   cuadráticamente con orgs × filas. Además: `closed_lots.source_trade_id` y
   los 4 lados de `transfers` (joins de linaje que Phase 3 necesita),
   `parties.user_id`, y las 4 FKs de `access_grants` — cuya política RLS
   `grant_visibility` evalúa `grantor/grantee` en **cada query** y que SP2 va a
   golpear en el hot path de autorización.
2. **Pool de conexiones sin configurar (§2.2).** `create_async_engine(url)` a
   secas (`db/session.py:16`) → defaults implícitos (`pool_size=5`,
   `max_overflow=10`), sin `pool_pre_ping` (un restart de Postgres produce
   errores `connection closed` post-incidente), sin `pool_recycle`. Los crons
   crean engines efímeros con los mismos defaults (`scheduler/jobs.py:49,87`).
   El presupuesto de conexiones es hoy tres defaults dispersos, no una decisión.
3. **`closed_lots.close_datetime` es el único timestamp TZ-naive del schema**
   (§2.3, `flex_raw.py:188`) y no documenta por qué. Es parte de la natural key
   y entra al cálculo 730d (Art. 300 ET) — una interpretación de timezone
   equivocada a futuro mueve un lote de RO a GO.
4. **`ondelete` implícito en FKs de hechos → accounts/counterparties (§2.4).**
   El default `NO ACTION` es el comportamiento correcto (protege el ledger),
   pero es un invariante load-bearing que existe por omisión, no por
   declaración.
5. **Sin `created_at` en las 4 tablas de lotes/accruals (§2.5).** El linaje vía
   `flex_import_id → fetched_at` se pierde cuando el cleanup latest-1 borra el
   import padre (`SET NULL`) — "¿cuándo supimos esto?" es una pregunta de
   auditoría fiscal legítima que hoy no siempre tiene respuesta.

## 2. Investigación contra data real (cierra la decisión de §2.3)

Los fixtures reales muestran que IBKR emite `dateTime="YYYYMMDD;HHMMSS"` o
`dateTime="YYYYMMDD"` — **sin información de timezone en la fuente**.
`_parse_datetime` (`parser.py:62`) lo materializa naive, fiel al XML. Convertir
la columna a `timezone=True` obligaría a *inventar* una zona (¿exchange?, ¿ET?,
¿UTC?) introduciendo una mentira en el dato. **Decisión: naive es correcto por
diseño; el fix es documentarlo, no cambiarlo.** La regla 730d opera a
granularidad de día sobre la fecha exchange-local que IBKR reporta, consistente
con `trade_date`/`close_date`.

## 3. Decisiones (locked)

- **D1 — Índices FK declarados en los modelos**, no solo en la migración, para
  que el drift test (`compare_metadata`) los proteja para siempre. Lista
  completa:
  - `flex_import_id` en: `trades`, `closed_lots`, `open_position_lots`,
    `transfers`, `cash_transactions`, `change_in_dividend_accruals`,
    `open_dividend_accruals`. (`flex_import_accounts` NO: su PK compuesta
    `(flex_import_id, account_id)` ya cubre el prefijo.)
  - `closed_lots.source_trade_id`.
  - `transfers`: `src_account_id`, `dst_account_id`, `src_counterparty_id`,
    `dst_counterparty_id`.
  - `parties.user_id`.
  - `access_grants`: `grantor_party_id`, `grantee_organization_id`,
    `grantee_user_id`, `organization_id` (única tabla tenant sin índice en
    org_id).
  - NO se indexan las FKs `account_id` de tablas de hechos: los índices
    compuestos `(account_id, symbol)` existentes ya cubren el prefijo
    (falso positivo descartado en la auditoría).
  - Nombres vía naming convention (`ix_%(table_name)s_%(column_0_N_name)s`).
- **D2 — Pool explícito vía Settings** (decisión usuario 2026-06-10): campos
  `db_pool_size=10`, `db_max_overflow=20`, `db_pool_recycle=1800` en
  `config.py`, tunables por env en Coolify sin redeploy de imagen.
  `pool_pre_ping=True` **fijo** (no configurable — no hay caso legítimo para
  apagarlo). Un helper único `engine_kwargs()` consumido por
  `db/session.py::get_engine()` y los engines efímeros de los crons. El
  jobstore sync de APScheduler recibe `pool_pre_ping` vía `engine_options`.
  El presupuesto total de conexiones (app + crons + jobstore) queda documentado
  en `config.py`.
- **D3 — `close_datetime` queda naive por diseño**, documentado con `comment=`
  en la columna (semántica exchange-local de la fuente IBKR, granularidad de
  día para 730d), espejado en la migración con `alter_column(comment=...)`.
  Mismo patrón que los comments de invariantes de H4 (Phase 2.8).
- **D4 — `ondelete="RESTRICT"` explícito** en las FKs de hechos →
  `accounts` (trades, closed_lots, open_position_lots, transfers src/dst,
  cash_transactions, ambos accruals) y → `counterparties` (transfers src/dst).
  Mismo comportamiento efectivo que el `NO ACTION` actual; la intención queda
  declarada en el schema y blindada contra un futuro "CASCADE para que funcione
  el delete". En la migración: drop + recreate de cada FK (instantáneo a este
  tamaño de datos). `closed_lots.source_trade_id` conserva su semántica actual
  (linaje opcional) — solo gana índice, no cambia ondelete.
- **D5 — `created_at` en `closed_lots`, `open_position_lots`,
  `change_in_dividend_accruals`, `open_dividend_accruals`:**
  `DateTime(timezone=True), server_default=func.now(), NOT NULL`. En las tablas
  snapshot (`ON CONFLICT DO UPDATE`), `created_at` **no entra en
  `update_cols`** — semántica first-seen ("cuándo apareció esta fila"),
  consistente con el ledger. Filas dev existentes quedan selladas con el
  timestamp de la migración (decisión usuario 2026-06-10: aceptable pre-prod,
  sin datos de clientes; NO wipe & reload).
- **D6 — Una sola migración Alembic, autogenerada canónicamente** dentro del
  container backend (lección Phase 2.9: el camino valida boot/drift/formato),
  con el falso-positivo `apscheduler_jobs` removido del autogenerate. Downgrade
  completo y reversible. Todo aditivo — cero pérdida de datos.
- **D7 — Un solo branch/PR** (`saas/sp1-db-hardening`). La alternativa
  per-hallazgo daría 5 migraciones triviales encadenadas sin beneficio: el
  paquete es atómico, aditivo y reversible.

## 4. Fuera de alcance (mapeado, no es deuda de este PR)

- Retención de `ingest_log`/`trm_imports` → SP8 (observabilidad).
- Cola durable + paralelización del cron + HTTP fuera de la transacción → SP5/SP7.
- JobTracker/`_step3_stash` multi-réplica (Redis) → SP5.
- Split de 3 roles Postgres (owner non-superuser dedicado) → SP4.
- Enforcement de `access_grants` → SP2 (este PR solo deja sus índices listos).

## 5. Testing

- TDD por task (convención del repo). El drift test endurecido valida paridad
  modelo ↔ migración automáticamente para D1/D3/D4/D5.
- Tests nuevos de comportamiento:
  - `RESTRICT`: borrar un `Account` con trades referencia falla con
    `IntegrityError` (lockea D4).
  - `created_at`: poblado en persist inicial; **estable** ante re-upsert de la
    misma fila (first-seen, lockea D5).
  - Settings: defaults de pool correctos + override por env (lockea D2).
- Verificación final: suite completa (351+) bajo `app_rls`, `ruff check`/`format`
  en 0, boot smoke `make dev` (el boot guard valida el rol), y `EXPLAIN` del
  delete del cleanup latest-1 confirmando index scan sobre las tablas de hechos.

## 6. Criterios de aceptación

1. `alembic upgrade head` sobre DB con datos dev existentes corre sin wipe y el
   downgrade es reversible.
2. Drift test verde: `compare_metadata` sin diffs (modelos = DB real).
3. Los índices `ix_*_flex_import_id` existen en las 7 tablas de hechos
   (verificable vía `\d`) y el costo del cleanup latest-1 baja: los chequeos
   `ON DELETE SET NULL` corren como triggers FK, así que la evidencia es el
   trigger-time de `EXPLAIN (ANALYZE)` del DELETE (en transacción con ROLLBACK)
   o, más simple, la existencia de los índices + drift test verde.
4. Suite completa verde bajo `app_rls`; tests nuevos de D2/D4/D5 incluidos.
5. Ningún cambio de comportamiento funcional: mismos resultados de persister,
   mismos counts, misma semántica de deletes (RESTRICT == NO ACTION efectivo).
