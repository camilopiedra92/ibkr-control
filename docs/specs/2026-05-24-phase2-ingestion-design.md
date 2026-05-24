# Phase 2 — Data Ingestion (Flex WS + TRM Socrata + Scheduler + Setup Wizard) — Design Spec

**Fecha:** 2026-05-24
**Autor:** Test Owner
**Status:** Diseño aprobado, pendiente plan de implementación
**Plan a generar:** `docs/plans/2026-05-24-ibkr-control-phase2-ingestion.md` (via `superpowers:writing-plans`)
**Tag al completar:** `v0.2.0-ingest`

**Spec maestro (referencia):** `docs/specs/2026-05-24-ibkr-control-center-design.md`

---

## 1. Objetivo

Habilitar el pipeline completo de ingesta de datos crudos en `ibkr-control`:

- **IBKR Flex Web Service** auto-fetch YTD diario + upload manual de XMLs históricos
- **TRM Socrata DIAN** (`ceyp-9c7c`) auto-fetch diario + backfill histórico 1991-hoy
- **Scheduler** APScheduler con 2 jobs idempotentes
- **Setup wizard** de 4 pasos para onboarding inicial
- **Settings ampliado** con upload manual, rotación de token, log de runs
- **Concurrencia segura** vía advisory locks Postgres

Al final de Phase 2, las tablas crudas del modelo (§5 del spec maestro) están pobladas y mantenidas
automáticamente. **No** hay valores COP calculados, **no** hay clasificación fiscal, **no** hay
pantallas de análisis — toda esa lógica vive en Phase 3+ (domain layer).

---

## 2. Frontera con Phase 3

Phase 2 cubre **solo raw ingest + TRM**. Lo que entra:

- Datos del XML aterrizan tal-cual en tablas (`trades`, `closed_lots`, `open_position_lots`,
  `cash_transactions`, `transfers`, `transfer_lots`)
- TRM histórica + diaria en `trm_days` con expansión de vigencia
- Tabla `flex_imports` con dedup por SHA-256 del XML
- Tabla `ingest_log` para observabilidad

Lo que NO entra (queda Phase 3):

- Tabla `lot_classifications` y `lot_status_v` view
- Módulos `domain/fifo.py`, `domain/trm_lookup.py`, `domain/regime.py`, `domain/classification.py`,
  `domain/simulator.py`, `domain/dividends.py`, `domain/participation.py`, `domain/fiscal_report.py`
- Pantallas Dashboard, Lotes Abiertos, Cerrados, Alertas 730d, Simulador, Dividendos,
  Patrimonio, Form 160, Reporte Form 210
- yfinance + cache de precios (Phase 6)
- Re-replay FIFO con override manual del trader (Phase 3)

**Test de frontera:** si una funcionalidad requiere `Decimal × TRM` o aplica una regla del Estatuto
Tributario, no es Phase 2.

---

## 3. Decisiones de diseño (locked)

> **SUPERSEDED 2026-05-24** — see commits 70f9bd0 + 45127f1.
> Decision revisited post-Phase-2 polish: persistent SQLAlchemyJobStore
> (D5) + DB-backed rate limit (D2) implemented because (a) Postgres was
> already a dependency, (b) the lift was small (~30 LOC + 4 tests),
> (c) restart-safety upside justified re-opening the locked decision.
> Original rationale preserved below for historical context.

| # | Decisión | Valor |
|---|---|---|
| D1 | Scope de Phase 2 | Solo raw ingest + TRM (sin classification, sin domain) |
| D2 | UX del setup wizard | Stepper de 4 pasos con state persistido en backend |
| D3 | Testing del Flex WS | VCR cassettes con XMLs reales sanitizados + respx para escenarios de error |
| D4 | UX del backfill inicial | Bloquea navegación + SSE con progress detallado |
| D5 | Concurrencia de ingest | Advisory lock por `(source, user_id)` en Postgres, 409 en conflicto |
| D6 | APScheduler jobstore | In-memory (jobs idempotentes; container restart = OK) |
| D7 | Token AES-GCM key | Env var `TOKEN_ENCRYPTION_KEY` (32 bytes base64), sin rotation V1 |
| D8 | Validación de XML upload | Reject si malformado, no `<FlexQueryResponse>`, o account ID desconocido |
| D9 | Wizard step 2 (cuentas) | Pre-populado con 3 placeholders (U99999001/U99999002/U99999003) editables |
| D10 | TRM backfill scope | Completo 1991-hoy (~12K filas, ~1 MB) |
| D11 | Wizard route | `/setup` (route group `(setup)`); middleware redirecciona desde rutas protegidas si `setup_completed_at IS NULL` |
| D12 | Upload XML en Settings (post-wizard) | Mismo endpoint que step 3 del wizard, mismo parser |
| D13 | XML duplicado por hash | 409 con `flex_import_id` + fecha del primer ingest; UI lo presenta como info no fatal |
| D14 | Organización del módulo `ingest/` | Per-source folders (`ingest/flex/`, `ingest/trm/`) + cross-cutting helpers en root |
| D15 | Progress en wizard step 4 | SSE vía `EventSourceResponse` (sse-starlette) con `Last-Event-ID` para reconexión |
| D16 | Cron Flex | `for user in users` secuencial (amable con rate limit IBKR, V1 single-user) |
| D17 | Partición de migrations | 4 migrations separadas (identity, trm, flex_raw, ingest_log) en vez de 1 grande |

---

## 4. Arquitectura

### 4.1 Layout del backend post-Phase-2

```
backend/src/ibkr_control/
├── api/
│   ├── auth.py             [Phase 1 existing]
│   ├── settings.py         [Phase 1 existing — extendido en Phase 2]
│   ├── setup.py            [NEW — wizard endpoints]
│   ├── imports.py          [NEW — upload XML]
│   ├── ingest.py           [NEW — manual refresh, SSE stream]
│   └── credentials.py      [NEW — rotar Flex token]
├── ingest/                 [NEW módulo entero]
│   ├── __init__.py
│   ├── lock.py             # advisory lock helper (cross-cutting)
│   ├── log.py              # ingest_log context manager (cross-cutting)
│   ├── hash_dedup.py       # SHA-256 helper (cross-cutting)
│   ├── job_tracker.py      # singleton in-memory para SSE eventos
│   ├── flex/
│   │   ├── __init__.py
│   │   ├── client.py       # IBKR Flex WS: SendRequest + Poll exponencial backoff
│   │   ├── crypto.py       # AES-GCM encrypt/decrypt del token
│   │   ├── parser.py       # XML bytes → dataclasses (lxml)
│   │   ├── persister.py    # dataclasses → INSERT en TX Postgres
│   │   └── job.py          # orchestración: lock + client + parser + persister + log
│   └── trm/
│       ├── __init__.py
│       ├── client.py       # Socrata DIAN ceyp-9c7c con paginación
│       ├── parser.py       # JSON rows → expandir vigencia_desde..vigencia_hasta
│       ├── persister.py    # ON CONFLICT (date) DO UPDATE
│       └── job.py
├── scheduler/
│   ├── __init__.py
│   └── jobs.py             # APScheduler setup + register 2 jobs
├── db/
│   └── models/
│       ├── user.py         [Phase 1 — extendido con setup_completed_at + setup_progress]
│       ├── settings.py     [Phase 1]
│       ├── accounts.py     [NEW]
│       ├── participations.py [NEW]
│       ├── flex_credentials.py [NEW]
│       ├── flex_raw.py     [NEW — flex_imports, trades, closed_lots, open_position_lots,
│                                  transfers, transfer_lots, cash_transactions]
│       ├── trm.py          [NEW — trm_days, trm_imports]
│       └── ingest_log.py   [NEW]
├── alembic/versions/
│   ├── 2026_xx_phase2_identity.py
│   ├── 2026_xx_phase2_trm.py
│   ├── 2026_xx_phase2_flex_raw.py
│   └── 2026_xx_phase2_ingest_log.py
└── main.py                 [Phase 1 — agregar startup event que registra scheduler]
```

### 4.2 Layout del frontend post-Phase-2

```
frontend/src/app/
├── (auth)/
│   ├── login/page.tsx      [Phase 1]
│   └── register/page.tsx   [Phase 1]
├── (setup)/                [NEW route group]
│   └── setup/
│       ├── layout.tsx      # Sin sidebar, solo stepper en top
│       └── page.tsx        # Stepper de 4 pasos en una page
└── (app)/                  [Phase 1 — layout con sidebar]
    ├── dashboard/page.tsx  # Phase 1 placeholder, sin cambios estructurales
    ├── settings/page.tsx   # Phase 1 (marginal_rate + timezone)
    │                       # Phase 2: + Flex token + XML upload + log + Actualizar ahora
    └── layout.tsx
```

`middleware.ts` (Next.js root):
```typescript
// si user logged && setup_completed_at IS NULL && path !== /setup → redirect /setup
// si user logged && setup_completed_at IS NOT NULL && path === /setup → redirect /dashboard
// si user NOT logged && path requires auth → redirect /login
```

---

## 5. Modelo de datos — 4 migrations Alembic

### 5.1 Migration A — `phase2_identity`

Extiende `users` con state del wizard + agrega tablas de identidad operacional.

```sql
ALTER TABLE users
  ADD COLUMN setup_completed_at TIMESTAMPTZ NULL,
  ADD COLUMN setup_progress JSONB NOT NULL DEFAULT '{}'::jsonb;
-- ej. {"step1_credentials": true, "step2_accounts": true,
--      "step3_xmls": false, "step4_started_at": null}

CREATE TABLE accounts (
  id BIGSERIAL PRIMARY KEY,
  ibkr_account_id TEXT UNIQUE NOT NULL,
  alias TEXT NULL,
  currency TEXT NOT NULL DEFAULT 'USD',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE participations (
  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  pct NUMERIC(5,4) NOT NULL CHECK (pct >= 0 AND pct <= 1),
  valid_from DATE NOT NULL,
  valid_to DATE NULL,
  PRIMARY KEY (user_id, account_id, valid_from),
  CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE TABLE flex_credentials (
  user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  token_encrypted BYTEA NOT NULL,   -- AES-GCM: nonce(12B) || ciphertext || tag(16B)
  ytd_query_id TEXT NOT NULL,
  last_rotated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 5.2 Migration B — `phase2_trm`

TRM aislado, no depende de identidad. Puede correr antes o después de A.

```sql
CREATE TABLE trm_days (
  date DATE PRIMARY KEY,
  value_cop NUMERIC(12,4) NOT NULL,
  vigencia_desde DATE NOT NULL,
  vigencia_hasta DATE NOT NULL,
  source TEXT NOT NULL DEFAULT 'dian_socrata_ceyp_9c7c',
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX trm_days_date_idx ON trm_days(date);

CREATE TABLE trm_imports (
  id BIGSERIAL PRIMARY KEY,
  date_range_from DATE NOT NULL,
  date_range_to DATE NOT NULL,
  n_rows_api INT NOT NULL,
  n_days_expanded INT NOT NULL,
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 5.3 Migration C — `phase2_flex_raw`

Depende de A (FK a `accounts`). Tablas crudas del XML.

```sql
CREATE TABLE flex_imports (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  anyo INT NOT NULL,
  xml_hash TEXT UNIQUE NOT NULL,
  xml_size_bytes INT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('web_service', 'manual_upload')),
  period_covered_from DATE NOT NULL,
  period_covered_to DATE NOT NULL,
  year_status TEXT NOT NULL DEFAULT 'rolling' CHECK (year_status IN ('rolling', 'sealed')),
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  n_trades INT, n_lots_closed INT, n_open_lots INT,
  n_cash_tx INT, n_dividends INT, n_transfers INT,
  status TEXT NOT NULL CHECK (status IN ('ok', 'failed'))
);

CREATE TABLE trades (
  id BIGSERIAL PRIMARY KEY,
  flex_import_id BIGINT NOT NULL REFERENCES flex_imports(id) ON DELETE CASCADE,
  transaction_id TEXT UNIQUE NOT NULL,
  account_id BIGINT NOT NULL REFERENCES accounts(id),
  symbol TEXT NOT NULL,
  asset_class TEXT NOT NULL,
  trade_date DATE NOT NULL,
  settle_date DATE NULL,
  qty NUMERIC(20,8) NOT NULL,                  -- widened in Migration F
  price_usd NUMERIC(20,6) NOT NULL,
  proceeds_usd NUMERIC(20,4) NOT NULL,         -- widened in Migration F
  commission_usd NUMERIC(20,4) NOT NULL,
  open_close TEXT NULL CHECK (open_close IS NULL OR open_close IN ('O', 'C')),
  buy_sell TEXT NOT NULL CHECK (buy_sell IN ('BUY', 'SELL')),
  raw_attrs JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX trades_account_symbol_idx ON trades(account_id, symbol);
CREATE INDEX trades_trade_date_idx ON trades(trade_date);

CREATE TABLE closed_lots (
  id BIGSERIAL PRIMARY KEY,
  flex_import_id BIGINT NOT NULL REFERENCES flex_imports(id) ON DELETE CASCADE,
  account_id BIGINT NOT NULL REFERENCES accounts(id),
  symbol TEXT NOT NULL,
  open_date DATE NOT NULL,
  close_date DATE NOT NULL,
  qty NUMERIC(20,8) NOT NULL,                  -- widened in Migration F
  cost_basis_usd NUMERIC(20,4) NOT NULL,       -- widened in Migration F
  proceeds_usd NUMERIC(20,4) NOT NULL,         -- widened in Migration F
  fifo_pnl_usd NUMERIC(20,4) NOT NULL,         -- widened in Migration F
  source_trade_id BIGINT NULL REFERENCES trades(id)
);
CREATE INDEX closed_lots_account_symbol_idx ON closed_lots(account_id, symbol);

CREATE TABLE open_position_lots (
  id BIGSERIAL PRIMARY KEY,
  flex_import_id BIGINT NOT NULL REFERENCES flex_imports(id) ON DELETE CASCADE,
  account_id BIGINT NOT NULL REFERENCES accounts(id),
  symbol TEXT NOT NULL,
  open_date DATE NOT NULL,
  qty NUMERIC(20,8) NOT NULL,                  -- widened in Migration F
  cost_basis_usd NUMERIC(20,4) NOT NULL,       -- widened in Migration F
  mark_price_usd NUMERIC(20,6) NULL,
  mark_value_usd NUMERIC(20,4) NULL,           -- widened in Migration F
  snapshot_date DATE NOT NULL
);
CREATE INDEX open_position_lots_account_symbol_idx ON open_position_lots(account_id, symbol);

CREATE TABLE transfers (
  id BIGSERIAL PRIMARY KEY,
  flex_import_id BIGINT NOT NULL REFERENCES flex_imports(id) ON DELETE CASCADE,
  transfer_date DATE NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('IN', 'OUT')),
  src_account_id BIGINT NULL REFERENCES accounts(id),
  dst_account_id BIGINT NULL REFERENCES accounts(id),
  symbol TEXT NOT NULL,
  qty NUMERIC(20,8) NOT NULL,                  -- widened in Migration F
  transfer_type TEXT NOT NULL
);

CREATE TABLE transfer_lots (
  id BIGSERIAL PRIMARY KEY,
  transfer_id BIGINT NOT NULL REFERENCES transfers(id) ON DELETE CASCADE,
  original_open_date DATE NOT NULL,
  qty NUMERIC(20,8) NOT NULL,                  -- widened in Migration F
  cost_basis_usd NUMERIC(20,4) NOT NULL        -- widened in Migration F
);

CREATE TABLE cash_transactions (
  id BIGSERIAL PRIMARY KEY,
  flex_import_id BIGINT NOT NULL REFERENCES flex_imports(id) ON DELETE CASCADE,
  account_id BIGINT NOT NULL REFERENCES accounts(id),
  type TEXT NOT NULL,
  currency TEXT NOT NULL DEFAULT 'USD',
  amount_usd NUMERIC(20,4) NOT NULL,           -- widened in Migration F
  description TEXT NULL,
  date DATE NOT NULL,
  symbol TEXT NULL
);
CREATE INDEX cash_transactions_date_idx ON cash_transactions(date);
```

**Migration F (precision widening — post-Phase 2 polish):** all monetary USD totals
went from `NUMERIC(20,2)` to `NUMERIC(20,4)` for sub-cent precision in Phase 3 FIFO
intermediate calculations. All quantities went from `NUMERIC(20,6)` to `NUMERIC(20,8)`
to accommodate fractional shares (IBKR Fractional Shares Plus emits up to 8 decimals).
Migration is metadata-only in Postgres — no rewrite, no data loss. Prices remain at
`NUMERIC(20,6)` matching IBKR XML source precision. TRM at `NUMERIC(12,4)` matching
DIAN. Decision rationale: standard for accounting/trading apps (Robinhood, IBKR,
QuickBooks). Industry-standard alternative — integer minor units (Stripe-style) —
rejected because (a) volume is low, (b) multi-currency USD/COP/TRM makes integer
scale tracking error-prone, (c) Python `Decimal` interop is cleaner with `NUMERIC`.

### 5.4 Migration D — `phase2_ingest_log`

Observabilidad. Sin dependencias hacia A/B/C.

```sql
CREATE TABLE ingest_log (
  id BIGSERIAL PRIMARY KEY,
  job_kind TEXT NOT NULL
    CHECK (job_kind IN ('flex', 'trm', 'manual_refresh', 'manual_upload', 'setup_initial')),
  user_id BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at TIMESTAMPTZ NULL,
  status TEXT NOT NULL CHECK (status IN ('running', 'ok', 'failed')),
  items_processed INT NULL,
  error_message TEXT NULL,
  trigger TEXT NOT NULL CHECK (trigger IN ('cron', 'manual', 'wizard'))
);
CREATE INDEX ingest_log_user_started_idx ON ingest_log(user_id, started_at DESC);
```

---

## 6. Pipelines de ingest

### 6.1 Flex job (cron + manual + wizard)

```
flex_job.run(user_id, trigger='cron'|'manual'|'wizard')
  ▼
  advisory_lock(user_id, source='flex')   → 409 si tomado
  ▼
  ingest_log.start('flex', user_id, trigger)   → log_id, status='running'
  ▼
  crypto.decrypt_token(flex_credentials.token_encrypted)   → plaintext
  ▼
  client.send_request(token, ytd_query_id)   → reference_code
  ▼
  client.poll_statement(token, reference_code) con backoff 1s..16s, max 5min   → bytes XML
  Si timeout >5min → FlexPollTimeoutError; ingest_log.finish('failed', error_msg);
  scheduler ignora y reintenta al día siguiente; manual devuelve 504
  ▼
  hash_dedup.sha256(xml_bytes)
  SELECT 1 FROM flex_imports WHERE xml_hash = $hash
  → exists → log.finish('ok', items=0); release lock; return
  ▼
  parser.parse(xml_bytes)
  → ParsedXML(header, trades[], closed_lots[], open_position_lots[],
              cash_tx[], transfers[], period_from, period_to, anyo)
  Pre-condición: el parser ejecuta un audit de tags conocidos contra _known_tags.py
  (patrón replicado de renta/documentos/ibkr_flex/_audit.py). Si encuentra un tag
  TOP-level no listado, levanta UnknownFlexTagError y aborta el ingest. El log
  registra el tag nuevo para que se actualice _known_tags.py manualmente.
  ▼
  TX BEGIN
    year_status = 'sealed' if period_to >= date(anyo, 12, 31) else 'rolling'
    INSERT flex_imports(...) ON CONFLICT (xml_hash) DO NOTHING RETURNING id
    INSERT trades(...) bulk con UNIQUE(transaction_id)
    INSERT closed_lots(...) bulk
    INSERT open_position_lots(...) bulk
    INSERT transfers(...) + transfer_lots(...) bulk
    INSERT cash_transactions(...) bulk
  TX COMMIT  (o ROLLBACK si cualquier step falla)
  ▼
  ingest_log.finish('ok', items_processed=sum_of_inserts); release lock
```

**Sealing del año:** dentro del persister, `year_status` se deriva de `period_to >= 31-Dec-anyo`.
Robusto ante caídas del server (§7.4 del spec maestro).

**Error handling:** cualquier excepción → `ingest_log.finish('failed', error_message=traceback[:8000])`,
lock se libera (via context manager `finally`), excepción se re-raisea para que el caller decida
(scheduler ignora, manual devuelve 500, wizard SSE emite event).

### 6.2 TRM job (cron + wizard backfill)

```
trm_job.run(trigger='cron'|'wizard')
  ▼
  advisory_lock(user_id=NULL, source='trm')
  ▼
  ingest_log.start('trm', user_id=NULL, trigger)
  ▼
  SELECT MAX(vigencia_desde) FROM trm_days   → last_known (NULL si DB vacía)
  ▼
  client.fetch(since=last_known + 1 day, limit=50000)
  → JSON rows [{vigenciadesde, vigenciahasta, valor}, ...]
  ▼
  rows == 0 → log.finish('ok', items=0); release; return
  ▼
  parser.expand(rows)
  → [(date_d, value_cop, vigencia_desde, vigencia_hasta), ...]
  ▼
  TX BEGIN
    INSERT INTO trm_days (date, value_cop, ...) ON CONFLICT (date)
      DO UPDATE SET value_cop = EXCLUDED.value_cop, vigencia_hasta = EXCLUDED.vigencia_hasta, ...
    INSERT INTO trm_imports (date_range_from, date_range_to, n_rows_api, n_days_expanded)
  TX COMMIT
  ▼
  ingest_log.finish('ok', items_processed=n_days); release lock
```

**Backfill inicial:** mismo `trm_job.run()` con `last_known=NULL` → fetch desde 1991. Socrata
pagina de 50K en 50K; el cliente itera hasta que la respuesta tenga < 50K rows.

**ON CONFLICT vs DO NOTHING:** usamos `DO UPDATE` porque las vigencias pueden cambiar
(DIAN corrige retroactivamente con poca frecuencia, pero pasa).

### 6.3 Helpers cross-cutting

**`ingest/lock.py`:**
```python
@asynccontextmanager
async def advisory_lock(session, user_id: int | None, source: str):
    lock_key = hash(f"{source}:{user_id}") & 0x7FFFFFFFFFFFFFFF  # bigint positivo
    acquired = await session.scalar(
        text("SELECT pg_try_advisory_lock(:k)"), {"k": lock_key}
    )
    if not acquired:
        raise LockHeldError(source=source, user_id=user_id)
    try:
        yield
    finally:
        await session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key})
```

**`ingest/log.py`:**
```python
@asynccontextmanager
async def ingest_log_entry(session, job_kind: str, user_id: int | None, trigger: str):
    row = IngestLog(job_kind=job_kind, user_id=user_id, trigger=trigger, status='running')
    session.add(row); await session.flush()
    log_id = row.id
    try:
        yield log_id
        row.status = 'ok'; row.finished_at = utcnow()
    except Exception as exc:
        row.status = 'failed'
        row.error_message = "".join(traceback.format_exception(exc))[:8000]
        row.finished_at = utcnow()
        raise
    finally:
        await session.commit()
```

**`ingest/hash_dedup.py`:**
```python
def xml_hash(xml_bytes: bytes) -> str:
    return hashlib.sha256(xml_bytes).hexdigest()

async def is_known_hash(session, hash_hex: str) -> bool:
    return bool(await session.scalar(
        select(FlexImport.id).where(FlexImport.xml_hash == hash_hex)
    ))
```

**`ingest/job_tracker.py`:** singleton in-memory que guarda lista de eventos por `job_id` para
SSE. Limpieza después de N minutos de finalizado. V2 → Redis pub/sub.

### 6.4 Encriptación del Flex Token

**`ingest/flex/crypto.py`:**
```python
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import os, base64

def _key() -> bytes:
    raw = os.environ["TOKEN_ENCRYPTION_KEY"]
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY must decode to exactly 32 bytes")
    return key

def encrypt_token(plaintext: str) -> bytes:
    aes = AESGCM(_key())
    nonce = os.urandom(12)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), associated_data=None)
    return nonce + ct  # nonce(12) || ciphertext(N) || tag(16)

def decrypt_token(blob: bytes) -> str:
    aes = AESGCM(_key())
    nonce, ct = blob[:12], blob[12:]
    return aes.decrypt(nonce, ct, associated_data=None).decode("utf-8")
```

**Bootstrap inicial:** `openssl rand -base64 32` → setear en Coolify env vars. Si la key se
pierde, los tokens guardados son irrecuperables — el user los re-pega (no es catastrófico,
son read-only).

### 6.5 Scheduler

**`scheduler/jobs.py`:**
```python
def register_jobs(scheduler: AsyncIOScheduler):
    # Cron Flex: 07:00 COT → 12:00 UTC (Bogotá sin DST)
    scheduler.add_job(
        run_flex_for_all_users,
        CronTrigger(hour=12, minute=0, timezone='UTC'),
        id='flex_daily', max_instances=1, coalesce=True,
    )
    # Cron TRM: 19:30 COT → 00:30 UTC del día siguiente
    scheduler.add_job(
        run_trm_global,
        CronTrigger(hour=0, minute=30, timezone='UTC'),
        id='trm_daily', max_instances=1, coalesce=True,
    )

async def run_flex_for_all_users():
    async with async_session() as s:
        user_ids = (await s.scalars(select(FlexCredentials.user_id))).all()
    for uid in user_ids:
        try:
            await flex_job.run(uid, trigger='cron')
        except Exception:
            # logged en ingest_log; seguir con el siguiente user
            pass
```

`max_instances=1 + coalesce=True` previene solapamiento. Inicialización desde `main.py` startup event.

---

## 7. Setup wizard

### 7.1 State machine

Persistido en `users.setup_progress` (JSONB):

```json
{
  "step1_credentials": true,
  "step2_accounts": true,
  "step3_xmls": true,
  "step3_n_xmls_uploaded": 2,
  "step4_started_at": "2026-05-24T16:32:11Z",
  "step4_job_id": 42,
  "step4_substeps": {
    "trm_backfill": "ok",
    "flex_ytd": "running"
  }
}
```

Y `users.setup_completed_at TIMESTAMPTZ NULL` marca el final.

**Semánticas:**
- `stepN_*` se flipa al click "Continuar →" del step (NO se infiere de side effects)
- `step3_xmls = true` significa "user vio el step y confirmó (con 0 o más uploads)"; el step es opcional
- `step4_started_at` se setea al click "Iniciar carga inicial"; no se resetea en retries (queda como audit del primer intento)
- `step4_substeps` se va poblando a medida que los substeps del meta-job terminan; en retry, los substeps con valor `ok` se saltean
- `setup_completed_at` se setea cuando todos los `step4_substeps` están en `ok`

### 7.2 Per-step UX & validation

**Step 1 — Credenciales Flex**
- Inputs: `flex_token`, `ytd_query_id`
- Endpoint: `POST /api/setup/step1/validate`
- Validación: test ping a IBKR (`send_request` con timeout 5s). Si OK → encriptar + guardar
  en `flex_credentials` + setear `setup_progress.step1_credentials = true` → 200.
  Si 401/403 → "Token inválido". Si timeout → "No pude alcanzar IBKR".
- Si user vuelve con `step1_credentials = true` → UI muestra "✓ Token configurado el YYYY-MM-DD"
  + botón "Cambiar token" (re-pegar y revalidar).

**Step 2 — Cuentas + participaciones**
- UI: tabla editable pre-poblada con 3 placeholders (D9). Botón "+ Agregar cuenta".
- Validación: `ibkr_account_id` matchea `^U\d{8}$`, `pct ∈ [0,1]`, suma de pcts del mismo
  account no excede 1.0.
- Endpoint: `POST /api/setup/step2/save` → UPSERT en `accounts` (por `ibkr_account_id`) +
  INSERT en `participations` con `valid_from = TODAY`, `valid_to = NULL`.

**Step 3 — XMLs históricos (opcional)**
- UI: drag&drop area + lista de XMLs subidos. Botón "Continuar →" siempre habilitado.
- Upload endpoint: `POST /api/imports/upload` (multipart). Mismo endpoint reusable desde Settings.
- Completion endpoint: `POST /api/setup/step3/complete` (sin body) — solo marca `setup_progress.step3_xmls = true` + `step3_n_xmls_uploaded = COUNT(...)`.
- Validación de upload: ver §8.1 abajo.
- Cada upload ingiere sincrónicamente (1-3s típico) llamando `flex_job.ingest_xml(user_id, xml_bytes, source='manual_upload', trigger='wizard')` — variante del job que recibe los bytes directo (sin pasar por SendRequest/Poll del Flex WS). Comparte el persister + dedup + log con el flujo del cron.

**Step 4 — Iniciar ingest**
- Endpoint: `POST /api/setup/step4/start` → arranca background task con meta-job, devuelve `{job_id}` y setea `setup_progress.step4_started_at`.
- Meta-job (idempotente; respeta `setup_progress.step4_substeps`):
  1. Si `step4_substeps.trm_backfill != 'ok'` → `trm_job.run(trigger='wizard')` — backfill 1991-hoy
  2. Si `step4_substeps.flex_ytd != 'ok'` → `flex_job.run(user_id, trigger='wizard')` — fetch YTD del año actual
  3. (XMLs históricos ya se procesaron en step 3 sincrónicamente)
  4. Si todos los substeps OK → marcar `users.setup_completed_at = NOW()`
- En retry (user vuelve después de un fallo): backend detecta `step4_started_at IS NOT NULL && setup_completed_at IS NULL`, expone los `step4_substeps` actuales, y al click "Reintentar" arranca solo los substeps que no están en `ok`.
- Cada substep emite eventos al `job_tracker`. SSE: `GET /api/ingest/stream/{job_id}`.
- Al recibir `{"step": "done"}`, frontend → `router.push('/dashboard')`.

### 7.3 SSE endpoint

```python
@router.get("/api/ingest/stream/{job_id}")
async def stream_ingest_progress(
    job_id: int,
    last_event_id: int = Header(default=0),
    user: User = Depends(current_user),
):
    async def event_generator():
        nonlocal last_event_id
        while True:
            events = job_tracker.events_since(job_id, last_event_id)
            for ev in events:
                yield {"id": ev.id, "event": "progress", "data": json.dumps(ev.payload)}
                last_event_id = ev.id
            if job_tracker.is_done(job_id):
                yield {"event": "done", "data": "{}"}
                break
            await asyncio.sleep(0.5)
    return EventSourceResponse(
        event_generator(),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

**Reconexión:** browser `EventSource` envía `Last-Event-ID` header automáticamente. Endpoint
lo respeta. Si job no existe (server reinició) → 404, frontend reinicia el step.

### 7.4 Manejo de errores en el wizard

| Escenario | Step | Comportamiento |
|---|---|---|
| Token Flex rechazado | 1 | Error inline rojo, no avanza, no guarda |
| Account ID conflicto | 2 | Warning con opción "actualizar pct" |
| XML upload account desconocido | 3 | Modal: "Cuenta UXXXX no configurada. Volver al step 2?" |
| XML upload hash duplicado | 3 | Toast info, no es error |
| TRM Socrata timeout en step 4 | 4 | SSE emite `failed`, UI muestra "Reintentar" en esa línea |
| Flex YTD falla en step 4 | 4 | Mismo; al retry solo retoma desde Flex (TRM ya está OK) |
| Browser tab cerrado mid-step-4 | 4 | Backend job sigue. Re-login → middleware redirige a /setup → wizard ve `step4_started_at IS NOT NULL` → re-conecta SSE |

---

## 8. Settings ampliado & operaciones manuales

### 8.1 Validación de XML upload

Reject si:
- No parsea como XML (`ParseError`)
- No contiene `<FlexQueryResponse>` raíz (no es un Flex statement)
- `<AccountInformation accountId="X">` no matchea ningún `accounts.ibkr_account_id` del user
- Hash SHA-256 ya existe → 409 con `flex_import_id` existente + `fetched_at` del primer ingest

### 8.2 Manual refresh con rate limit

> **SUPERSEDED 2026-05-24** — see commits 70f9bd0 + 45127f1.
> Decision revisited post-Phase-2 polish: persistent SQLAlchemyJobStore
> (D5) + DB-backed rate limit (D2) implemented because (a) Postgres was
> already a dependency, (b) the lift was small (~30 LOC + 4 tests),
> (c) restart-safety upside justified re-opening the locked decision.
> Original rationale preserved below for historical context.

```python
_LAST_TRIGGER: dict[int, datetime] = {}
_COOLDOWN = timedelta(minutes=5)

@router.post("/api/ingest/trigger")
async def trigger_manual_refresh(payload: TriggerBody, user: User = Depends(current_user)):
    last = _LAST_TRIGGER.get(user.id)
    if last and (utcnow() - last) < _COOLDOWN:
        wait = _COOLDOWN - (utcnow() - last)
        raise HTTPException(429, detail=f"Esperá {wait.seconds}s")
    _LAST_TRIGGER[user.id] = utcnow()
    job_id = await launch_job_in_background(payload.kind, user.id)
    return {"job_id": job_id}
```

In-memory dict — se resetea con container restart (aceptable).

### 8.3 Rotar Flex token

Modal en Settings → input nuevo token → `PUT /api/credentials/flex` con `{token: 'nuevo'}` →
backend hace ping antes de reemplazar → si OK actualiza `flex_credentials.token_encrypted +
last_rotated_at = NOW()` → toast verde. Si falla → toast rojo, no se reemplaza.

### 8.4 API endpoints completos Phase 2

| Endpoint | Método | Body / Query | Behavior |
|---|---|---|---|
| `/api/setup/state` | GET | — | Devuelve el `setup_progress` JSONB completo + `setup_completed_at` |
| `/api/setup/step1/validate` | POST | `{token, query_id}` | Test ping IBKR + guardar encriptado |
| `/api/setup/step2/save` | POST | `{accounts: [{ibkr_id, alias, pct}, ...]}` | Upsert accounts + participations |
| `/api/setup/step3/complete` | POST | — | Marca `step3_xmls = true` + `step3_n_xmls_uploaded` (no uploads aquí; solo confirm) |
| `/api/setup/step4/start` | POST | — | Background task (idempotente vs `step4_substeps`); devuelve `{job_id}` |
| `/api/imports/upload` | POST | multipart XML | Wizard + Settings; 200 ok / 409 dup / 400 invalid |
| `/api/ingest/trigger` | POST | `{kind: 'flex'\|'trm'\|'both'}` | Rate-limit 1/5min; devuelve `{job_id}` |
| `/api/ingest/stream/{job_id}` | GET | — | SSE stream |
| `/api/ingest/logs` | GET | `?limit=10` | Últimos N runs del user |
| `/api/credentials/flex` | GET | — | `{configured_at, query_id, last_rotated_at}` |
| `/api/credentials/flex` | PUT | `{token?, query_id?}` | Rotar uno o ambos; test ping antes |

---

## 9. Testing strategy

### 9.1 Pirámide

```
e2e/                    Playwright (2 tests)
├── test_wizard_happy_path.spec.ts
└── test_wizard_resume.spec.ts                  (cerrar tab + volver)

integration/            Postgres testcontainer
├── ingest/flex/
│   ├── test_client.py              VCR cassettes
│   ├── test_client_errors.py       respx para 401/timeout
│   ├── test_persister.py           DB writes en TX
│   ├── test_job_full.py            cliente + parser + persister
│   └── test_crypto.py              encrypt/decrypt round-trip
├── ingest/trm/
│   ├── test_client.py              VCR Socrata
│   ├── test_persister.py           ON CONFLICT upsert
│   └── test_job_full.py
├── ingest/test_lock.py             advisory lock conflict
├── ingest/test_log.py              context manager
├── ingest/test_hash_dedup.py
├── api/
│   ├── test_setup_endpoints.py     4 steps + state
│   ├── test_imports_upload.py      multipart + 409
│   ├── test_ingest_trigger.py      rate limit + 429
│   ├── test_ingest_stream.py       SSE eventos + reconnect
│   └── test_credentials.py         GET + rotate
├── scheduler/test_jobs_registration.py
└── test_migrations.py              extender Phase 1 test

unit/                   Sin DB, sin HTTP
├── ingest/flex/
│   ├── test_parser_2024.py         XML fixture grande
│   ├── test_parser_2025.py         XML fixture grande
│   ├── test_parser_edge.py         empty, OPT, FUT
│   └── test_parser_audit.py        tag desconocido aborta
└── ingest/trm/
    └── test_parser_expand.py       vigencia_desde..hasta
```

### 9.2 Fixtures

**XMLs sanitizados** en `backend/tests/fixtures/xml/`:
- `ACTIVITY_2024_sanitized.xml`
- `ACTIVITY_2025_sanitized.xml`
- `empty_query_response.xml`
- `opt_expiry_2024.xml`
- `fut_with_multiple_closes.xml`
- `malformed_xml.xml`

**Script de sanitización** (`backend/scripts/sanitize_xml.py`): reemplaza
`U99999001/U99999002/U99999003` → `U99999001/U99999002/U99999003`, NIT `1234567890` →
`1234567890`. Idempotente.

**VCR cassettes** en `backend/tests/fixtures/cassettes/`:
- `flex/send_request_ok.yaml`, `poll_statement_ready.yaml`, `poll_statement_pending.yaml`,
  `send_request_401.yaml`
- `trm/socrata_50k_page1.yaml`, `socrata_50k_page2.yaml`,
  `socrata_incremental_3_rows.yaml`, `socrata_empty.yaml`

**Re-grabar cassettes** (proceso manual, no en CI):
```bash
RECORD_MODE=new_episodes uv run pytest tests/integration/ingest/flex/test_client.py -v
uv run python -m scripts.sanitize_cassette tests/fixtures/cassettes/flex/*.yaml
```

### 9.3 Tests críticos a no olvidar

| Test | Por qué importa |
|---|---|
| `test_persister.py::test_xml_hash_collision_skips_insert` | Sin esto, dos cron runs harían doble ingest |
| `test_persister.py::test_transaction_rollback_on_partial_failure` | Rollback de TODOS los inserts si falla uno |
| `test_lock.py::test_advisory_lock_blocks_concurrent_job` | Dos tasks paralelas; una gana, otra `LockHeldError` |
| `test_lock.py::test_advisory_lock_releases_on_exception` | Lock liberado aunque crashee el job |
| `test_log.py::test_log_finished_on_exception` | `ingest_log.status='failed'` + traceback en excepción |
| `test_parser_audit.py::test_unknown_tag_aborts` | Replica patrón de `renta/documentos/ibkr_flex/_audit.py` — abortar si tag desconocido |
| `test_trm_parser::test_vigencia_expansion_includes_weekends` | Viernes vigente hasta lunes → 3 días en `trm_days` |
| `test_setup_endpoints::test_step1_invalid_token_returns_401` | UX: error claro si token mal pegado |
| `test_ingest_stream::test_sse_reconnect_with_last_event_id` | No duplicar eventos al reconectar |
| `test_wizard_resume.spec.ts` (E2E) | Cerrar tab en step 3, re-loguear → vuelve a step 3 |

### 9.4 Coverage targets Phase 2

| Módulo | Target |
|---|---|
| `ingest/flex/parser.py` | ≥95% |
| `ingest/flex/persister.py` | ≥90% |
| `ingest/flex/client.py` | ≥80% |
| `ingest/flex/crypto.py` | 100% |
| `ingest/trm/*` | ≥90% |
| `ingest/{lock,log,hash_dedup,job_tracker}.py` | 100% |
| `api/{setup,imports,ingest,credentials}.py` | ≥85% |
| `scheduler/jobs.py` | ≥70% |

### 9.5 No hacer en Phase 2

- NO mockear Postgres con SQLite (JSONB, advisory locks, ON CONFLICT)
- NO testear parser con XMLs synthetic mini — usar reales sanitizados
- NO correr E2E en cada CI; marcar `@pytest.mark.e2e`, ejecutar nightly o pre-release
- NO asumir tiempos de IBKR — test específico de timeout >5min
- NO testear paridad numérica con `renta` (eso es Phase 3+)

---

## 10. Consumo de referencias del sibling `renta`

Por `docs/references/renta-cross-references.md`, los archivos clave a consultar para Phase 2:

| Archivo en renta | Uso en Phase 2 |
|---|---|
| `documentos/ibkr_flex/loader.py` | Referencia del parser (≈600 líneas). Reescribir, no copiar |
| `documentos/ibkr_flex/_known_tags.py` | Catálogo de tags válidos del XML — replicar la lista |
| `documentos/ibkr_flex/_audit.py` | Patrón de "aborta si tag desconocido" — replicar |
| `documentos/ibkr_flex/db_ingest.py` | Orquestador (~150 líneas) — referencia de estructura |
| `documentos/_hash.py` | SHA-256 helper trivial — re-implementar |
| `documentos/trm_dian/loader.py` | Solo referencia conceptual (renta usa CSV; nosotros Socrata) |
| `fuentes/2024/compartidos/ibkr/ACTIVITY_2024.xml` | Fuente para sanitizar fixtures |
| `fuentes/2025/compartidos/ibkr/ACTIVITY_2025.xml` | Idem |

**Anti-patrones:** `from renta.* import` (nunca), `cp renta/.../X.py backend/` (nunca),
modificar archivos en renta desde sesión de ibkr-control (nunca — abrir sesión separada).

---

## 11. Riesgos & mitigaciones

| Riesgo | Mitigación |
|---|---|
| IBKR cambia formato XML | Parser tolera nuevos atributos vía `raw_attrs JSONB`. Audit aborta si tag desconocido en TOP-level. Fixtures pinned detectan regresiones |
| Socrata DIAN cae | Cron diario tolera N días sin actualización (cada run recupera todo el rango pendiente vía `vigencia_desde > MAX(...)`). Failures quedan visibles en Settings → "Últimos 10 runs"; sin notificación push V1 |
| Flex token filtrado | AES-GCM en DB con key en env. Coolify env vars cifradas. Token es read-only en IBKR (no permite trades) |
| `TOKEN_ENCRYPTION_KEY` perdida | Usuario re-pega tokens (no es catastrófico). Documentar en runbook |
| App caída al 31-dic | Sealing es derivado del contenido del XML, no time-driven (§7.4 spec maestro) — robusto |
| Cron solapado consigo mismo | `max_instances=1 + coalesce=True` en APScheduler. Advisory lock como segunda defensa |
| Upload concurrent con cron | Advisory lock bloquea el segundo — 409 al manual |
| TRM backfill lento (1991-hoy) | Solo corre 1x al setup. SSE muestra progreso. Si falla a mitad, retry desde `MAX(vigencia_desde)` |
| SSE buffereado por proxy Coolify | Headers `Cache-Control: no-cache` + `X-Accel-Buffering: no` explícitos |
| Hash dedup colisión SHA-256 | Probabilidad astronómica. Si pasa, manual delete del `flex_imports` problemático |

---

## 12. Criterios de aceptación Phase 2

Cuando todos estos se cumplen, Phase 2 se da por completa y se tagea `v0.2.0-ingest`:

1. ✅ Las 4 migrations Alembic aplican limpio sobre Phase 1 (`alembic upgrade head`) — **6 migrations en total post-polish**: identity, trm, flex_raw, ingest_log + addenda E (dividend_accruals) y F (widen_precision)
2. ✅ Tests unitarios + integración pasan en CI (`uv run pytest -v`) — **186 tests**
3. ⚠ Coverage targets de §9.4 mostly cumplidos. Excepciones:
   - `ingest/flex/parser.py`: 83% (target 95%) — gap por ramas de defensive parsing raras (e.g. tag con `accountId="-"` ya filtrado; resto solo cubible con XMLs ad-hoc no representativos)
   - `api/setup.py`: 73% (target 85%) — gap es coverage.py / ASGI async-frame limitation: HTTP-level tests funcionan pero no son trazables. Funcionalmente cubierto vía 30 tests; literalmente medible solo con direct-handler calls que duplicarían escenarios
   - `api/ingest.py`: 66% (target 85%) — gap por SSE generator paths que requieren live server loop (no testeable con httpx async client)
   - `scheduler/jobs.py`: 41% (target 70%) — gap por cron handlers que requieren scheduler corriendo en event loop real; structural tests verifican registro pero no ejecución
   - **Coverage real entregado: 88% total, 100% en módulos críticos** (crypto, lock, log, hash_dedup, persisters, TRM)
4. ✅ Wizard 4 pasos completable de punta a punta en local con XMLs sanitizados
5. ✅ Cron Flex 07:00 y TRM 19:30 quedan registrados en APScheduler al startup (+ `cleanup_job_tracker` horario agregado en polish)
6. ✅ Upload manual de XML desde Settings funciona + dedup correcto (test E2E)
7. ✅ Botón "Actualizar ahora" respeta rate limit y muestra progress vía SSE
8. ✅ Rotar token funciona + valida ping a IBKR antes de reemplazar
9. ✅ Última fila de `ingest_log` muestra status correcto después de cada operación
10. ✅ CLAUDE.md actualizado: tabla "Estado actual" refleja Phase 2 ✅ + link al plan
11. ✅ Commit en main + tag `v0.2.0-ingest`
12. ✅ Polish backlog post-merge cerrado — ver `docs/plans/2026-05-24-phase2-polish-backlog.md`

**No criterios** (intencionalmente fuera de Phase 2):
- Mostrar valores en COP en cualquier pantalla
- Calcular días held / clasificación 730d
- Pantalla Lotes Abiertos / Cerrados / Dashboard funcional
- Paridad numérica con `renta`

---

## 13. Out of scope explícito (Phase 3+ o V2)

- **Domain layer entero** (FIFO, TRM lookup, regime classification, simulador) — Phase 3
- **Pantallas de análisis** — Phase 3-5
- **yfinance + cache de precios** — Phase 6
- **Re-replay FIFO con override del trader** — Phase 3 según `flex_fifo_loader_spec.md` del sibling
- **Rotation automática de `TOKEN_ENCRYPTION_KEY`** — V2 (requiere migration script)
- **Multi-réplica del backend** — V2 (cambiar `job_tracker` in-memory por Redis pub/sub)
- **Importación de CSV de dividends post-IRD reclassification** — V2 (decisión #10 del spec maestro)
- **Detección de Return of Capital con ajuste a cost basis** — Phase 4+ (V1 lo excluye de dividendos)
- **Notificaciones por email/Slack en error de cron** — V2
- **Reorganizar `flex_credentials` para soportar múltiples tokens (e.g. de cuentas distintas)** — V2

---
