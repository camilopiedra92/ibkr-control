# Phase 2.6 — Flex Ingest Hardening (Spec)

| Campo | Valor |
|---|---|
| **Status** | Draft (post-brainstorming, pre-plan) |
| **Date** | 2026-05-25 |
| **Author** | Test Owner (brainstormed with Claude) |
| **Predecessor** | `2026-05-25-flex-persister-idempotent-design.md` (Phase 2.5) |
| **Successor (plan)** | `docs/plans/2026-05-25-phase26-flex-hardening.md` (to be written) |
| **Branch** | `phase26/flex-hardening` |
| **Target tag** | `v0.2.4-flex-hardening` |

---

## 1. Contexto

El architecture review del 2026-05-25 sobre el pipeline Flex (post Phase 2.5) confirmó arquitectura world-class para V1 single-user pero identificó **6 riesgos** que no son bloqueantes pero conviene cerrar antes de Phase 3:

| ID | Riesgo |
|---|---|
| R1 | `flex_imports.xml_bytes` BYTEA crece monótono sin política de retention |
| R2 | XMLs venenosos (parser crash) reintentan 1×/día sin dead-letter |
| R3 | Sin test que cubra cross-schema replay (A3 #1/#2/#3 se descubrieron en prod) |
| R4 | Fallos del cron silenciosos en UI (TRM falló 4 días sin signal) |
| R5 | `send_request` no reintenta 1001 BUSY transient |
| R6 | Multi-user isolation no testeado + `xml_hash UNIQUE` global es bug latente |

## 2. Scope

**V1.5-clean**: cerrar bien las 6 + introducir 1-2 capas que pavimentan V2 sin implementar los backends V2. Mantiene scope chico (~5-7 días), cero infra nueva (sin S3, sin Redis, sin OTel), boundaries limpios para swap futuro.

### Explicitly out of scope (V2+)

| Item | Razón de diferir | Cuándo abordar |
|---|---|---|
| Push notifications / email alerts | Single-user activo abre dashboard 1+/día; banner cubre 95% | V2 con >1 stakeholder pasivo |
| Circuit breaker en `FlexClient` | Overkill para 1×/día cron | V2 si Phase 3+ agrega manual refresh frecuente |
| S3/MinIO blob storage para `xml_bytes` | Latest-1 mantiene ~50MB; BYTEA suficiente | V2 cuando >5GB o multi-replica HA |
| Runtime middleware multi-tenant guard | Tests E2E ya cubren isolation; runtime guard es overhead sin user real | V2 con >1 user real en prod |
| OTel/Prometheus métricas | `ingest_log` + endpoint `/health` son métricas suficientes | V2 cuando team >1 ingeniero |
| Redis JobTracker (multi-replica SSE) | Single container Coolify suficiente | V2 si Coolify HA scaling |
| Auto-reset poison via `parser_version` bump | Riesgo de auto-romper; recovery manual es 1 comando | V2 si >5 schema upgrades/año |

## 3. Decisiones locked

### R1. Retention `flex_imports.xml_bytes` — Latest-1 + sealed pinned

**Política:** Para cada `(user_id, anyo, source)`:
- `year_status='sealed'`: conservar TODOS los xml_bytes (~1-2 por año, no crecen monótono).
- `year_status='ytd'`: conservar SOLO el más reciente. Inserts más viejos del mismo key se eliminan inline al final de cada `persist()` exitoso.

**Cleanup SQL (al final de `persist()` exitoso, dentro de la misma tx):**

```sql
DELETE FROM flex_imports
WHERE user_id = :uid
  AND anyo = :anyo
  AND source = :source
  AND year_status = 'ytd'
  AND id != :current_id
  AND status = 'ok';
```

**No incluye:** `status='poison'` rows — son evidencia forense, no se borran automáticamente.

**Rationale:** ~50MB/user total para foreseeable future. Pierde "cómo se veía YTD en marzo" — aceptable, no es caso de uso real (debugging es contra última versión). Sin cleanup job nuevo — cleanup es transaccional con el persist. Storage queda en BYTEA hoy; boundary (`flex_imports` table) permite swap a S3 como cambio aislado en V2.

### R2. Poison-pill — columnas `status` + `poison_reason`

**Schema:** agregar a `flex_imports`:

| Columna | Tipo | Default | Notas |
|---|---|---|---|
| `status` | `VARCHAR(20) NOT NULL` | `'ok'` | CHECK `IN ('ok', 'poison')` |
| `poison_reason` | `TEXT NULL` | — | `str(exc)` sin traceback completo (keep concise) |

**Flujo de poison capture:**

```
parse(xml_bytes) | persist(parsed) FAILS
  ↓
SAVEPOINT rollback (data parcial revertida)
  ↓
INSERT INTO flex_imports (
  user_id, xml_hash, xml_bytes, status='poison', poison_reason=str(exc),
  anyo, source, year_status, period_covered_*, fetched_at, ...
)
ON CONFLICT (user_id, xml_hash) DO NOTHING   -- idempotente
  ↓
session.commit() del log row (ingest_log status='failed')
  ↓
RAISE (caller decide acción)
```

El INSERT poison ocurre FUERA del SAVEPOINT revertido — dentro del catch block del `try` del job, antes del `raise`. Cubre tanto `parser.parse()` exceptions (que ocurren antes del savepoint) como exceptions del persister.

**Fast-path refactor:**

```python
# antes:
async def is_known_hash(session, h) -> bool: ...

# después:
async def check_hash_status(
    session: AsyncSession,
    user_id: int,
    h: str,
) -> Literal['absent', 'ok', 'poison']:
    """Scope per-user (matchea R6 UNIQUE constraint)."""
```

**Logging diferenciado en `job.run()` cuando hash existe:**
- `'ok'`: `logger.info("flex: duplicate hash, skipped — items_processed=0")`
- `'poison'`: `logger.warning("flex: previously poisoned hash, skipped — run scripts/poison_reset.py %s to retry", h[:12])`

**Recovery:** script nuevo `backend/scripts/poison_reset.py`:

```bash
uv run python -m scripts.poison_reset --user-id 1 --xml-hash abc123...
# DELETE WHERE user_id=1 AND xml_hash=abc123... AND status='poison'
# Próximo run reprocesa from scratch.
```

**Rationale:** column-on-existing-entity vs separate table = mantiene `flex_imports` como source of truth del lifecycle. No fragmenta la query "historial del XML X". Auto-clear via `parser_version` (brainstorm option 2) descartado por riesgo de auto-romper.

### R3. Replay test suite — full scope

**Test file:** `backend/tests/test_flex_ingest_replay.py`.

**Cobertura (4 layers de safety):**

**(1) Idempotencia per-fixture.** 3 fixtures × 2 runs cada uno:
```python
@pytest.mark.parametrize("fixture", ["2024_sealed", "2025_sealed", "2026_ytd"])
async def test_persist_twice_yields_zero_new(fixture, ephemeral_db):
    xml = load_fixture(fixture)
    _, counters_1 = await persist(session, parse(xml), ...)
    _, counters_2 = await persist(session, parse(xml), ...)
    assert counters_2["n_new_trades"] == 0
    assert counters_2["n_new_lots_closed"] == 0
    # ... (todas las entidades)
```

**(2) Counts esperados.** Hardcoded por fixture, validados manualmente contra renta `_invariants.py`:

```python
EXPECTED_2025 = {
    "trades": 302, "closed_lots": 154, "open_lots": 327,
    "cash_tx": ..., "transfers": ..., "change_accruals": 51, "open_accruals": 1,
}
```

**(3) FIFO parity invariant** (heredado de renta):
```python
async def test_closed_lots_match_pool_sum(fixture_2025, ephemeral_db):
    """Σ closed_lots.fifo_pnl_usd == Σ <ClosedLot>.fifoPnlRealized del XML."""
```

**(4) Cross-schema migration replay** (el test que hubiera atrapado A3 #1/#2/#3):

```python
async def test_cross_schema_replay(testcontainers_postgres):
    """
    1. Boot postgres clean.
    2. alembic upgrade <N-1>  (pre-phase26 revision)
    3. Cargar fixtures con schema viejo (sin status, sin per-user UNIQUE).
    4. alembic upgrade head.
    5. Reingest mismos fixtures con código actual.
    6. Assert: no errors + counts esperados + 0 schema drift.
    """
```

**Fixtures:** reuso de `backend/tests/fixtures/flex/{2024,2025,2026_ytd}.xml` sanitizadas (ya existen).

**Infra nueva:** `testcontainers-postgres` agregado a `pyproject.toml` dev-deps.

**Rationale:** Las 3 amendments A3 fueron descubiertas SOLO al correr persist contra fixture real. Cross-schema test es el patrón gold para evitar repetir el incidente. CI cost: ~30s extra por test (boot postgres ephemeral).

### R4. Visibilidad UI de fallos — endpoint + banner + tab

**Backend:**

Nuevo módulo `backend/src/ibkr_control/api/health.py`:

```python
class IngestSourceHealth(BaseModel):
    source: Literal['flex', 'trm']
    last_success_at: datetime | None
    last_failure_at: datetime | None
    consecutive_failures: int
    last_error: str | None  # truncado a 500 chars

class IngestHealthResponse(BaseModel):
    sources: list[IngestSourceHealth]
    checked_at: datetime

@router.get("/api/health/ingest", response_model=IngestHealthResponse)
async def get_ingest_health(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
) -> IngestHealthResponse: ...
```

**Implementación:** query agregada con `ROW_NUMBER() OVER (PARTITION BY kind ORDER BY started_at DESC)` sobre `ingest_log`, scoped por `user_id`. Una query, devuelve last success + last failure + count de failures consecutivos desde último success.

**Frontend:**

`frontend/src/components/dashboard/IngestHealthBanner.tsx`:
- Hidden si todas las sources healthy (último success ≤24h, consecutive_failures = 0).
- Yellow warning si cualquier source con último success >24h y ≤48h.
- Red alert si cualquier source con último success >48h O `consecutive_failures >= 2`.
- Click → navega a Settings → tab "Salud de ingesta".
- Dismissible per-session (`sessionStorage` flag — re-aparece al recargar).

`frontend/src/components/settings/IngestHealthTable.tsx`:
- Nuevo tab "Salud de ingesta" en Settings (después de "Credenciales Flex").
- Tabla per-source con cols: source, last_success, last_failure, consec_failures, last_error (truncated tooltip-expandible), sparkline.
- Sparkline: bars verde/rojo de últimos 30 runs (success/fail) via `recharts` (dep existente).
- Query: TanStack Query con `staleTime: 5min`, `refetchOnWindowFocus: true`.

**Rationale:** push notifications descartado para V1.5 — single user activo cubre con banner. Threshold 24h/48h documentado como ajustable post-30-días-operación.

### R5. RetryPolicy class + refactor poll_statement

**Nuevo módulo:** `backend/src/ibkr_control/ingest/retry.py`.

```python
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

@dataclass(frozen=True)
class RetryPolicy:
    """Backoff exponencial parametrizable, cero acoplamiento con HTTP."""
    initial_delay_s: float
    max_delay_s: float
    multiplier: float
    max_attempts: int
    retryable_exceptions: tuple[type[Exception], ...]
    retryable_predicate: Callable[[Exception], bool] | None = None

async def execute_with_retry(
    fn: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    on_retry: Callable[[Exception, int, float], None] | None = None,
) -> T:
    """Ejecuta fn() con backoff. Lanza la última exception si max_attempts excedido."""
```

**Aplicación en `client.py`:**

```python
# Constants en client.py
SEND_REQUEST_POLICY = RetryPolicy(
    initial_delay_s=5, max_delay_s=30, multiplier=3, max_attempts=3,
    retryable_exceptions=(FlexBusyError, httpx.NetworkError, httpx.HTTPStatusError),
    retryable_predicate=lambda e: (
        isinstance(e, FlexBusyError)
        or isinstance(e, httpx.NetworkError)
        or (isinstance(e, httpx.HTTPStatusError) and e.response.status_code >= 500)
    ),
)

POLL_STATEMENT_POLICY = RetryPolicy(
    initial_delay_s=1, max_delay_s=16, multiplier=2, max_attempts=30,
    retryable_exceptions=(FlexStatementPendingError,),
)
```

**Refactor de `poll_statement`:** elimina el `while elapsed < max_wait_seconds` embebido. Función ahora hace UNA llamada y lanza `FlexStatementPendingError` si pending (nueva exception). `execute_with_retry` maneja el loop. `max_attempts=30` con max_delay=16 cubre ~5min total (suficiente — más que 300s actual).

```python
# Nueva exception
class FlexStatementPendingError(FlexClientError):
    """ErrorCode 1019 — statement aún generándose. Retryable."""

# poll_statement nuevo (sin while loop):
async def poll_statement(self, reference_code: str) -> bytes:
    return await execute_with_retry(
        lambda: self._poll_once(reference_code),
        policy=POLL_STATEMENT_POLICY,
        on_retry=lambda exc, attempt, delay: logger.info(
            "Flex pending, retry %d in %.1fs", attempt, delay
        ),
    )
```

**Tests:** `backend/tests/test_retry.py` con casos paramétricos:
- No retry (fn succeeds first try)
- 1 retry (fn fails once, succeeds)
- Max attempts exceeded (fn always fails → original exception propagated)
- Non-retryable exception (fn raises FlexAuthError → no retry)
- Predicate blocks retry (4xx HTTP → no retry)
- `on_retry` callback invocado con args correctos

**Rationale:** Extract clase reusable. Hoy hay backoff lógica duplicada (poll loop interno + ausente en send_request). World-class es 1 implementación + parametrización + tests aislados del policy.

### R6. Multi-user isolation — migration + tests integración

**Migration:**
- Drop `UNIQUE(xml_hash)` global.
- Add `UNIQUE(user_id, xml_hash)`.

Sin esto, dos users que suban el mismo XML chocan con `UniqueViolationError` (correctness bug latente).

**Refactor:**
- `hash_dedup.py`: `check_hash_status(session, user_id, h)` con WHERE `user_id=:user_id AND xml_hash=:h`. Scope per-user.
- `persister.py`: validar que `_ensure_accounts` filtra por user_id (heredado, ya OK — confirmar en test).

**Tests:** `backend/tests/test_flex_isolation_multi_user.py`:

1. **Persister isolation (async parallel):** 2 users en paralelo (`asyncio.gather`), mismo fixture XML, assert que cada user ve solo su data (queries scoped a user_id no cruzan).
2. **Advisory lock per-user:** user A toma lock para `(source='flex', user_id=A)`, user B intenta lock para `(source='flex', user_id=B)` → succeeds (locks son per-user por design del `_lock_key`).
3. **Hash collision after migration:** user A persist XML X (status='ok'), user B persist XML X (debe ser ok, NO `UniqueViolation`). Valida la migration.
4. **API endpoint integration:** GET `/api/health/ingest` con auth de user A devuelve solo data de user A (`ingest_log.user_id=A`). Requiere fastapi-users en tests (setup heredado de Phase 1).

**Rationale:** "world-class no work-arounds" implica testear lo que el schema documenta como "multi-user-ready". Bug del UNIQUE global es prueba de que aspirational claims sin tests son tech debt acumulado.

## 4. Cambios de schema (1 Alembic revision atómica)

Revision name: `phase26_flex_hardening`.

```python
"""phase26 flex hardening: poison-pill + per-user xml_hash unique."""

revision = '<new_id>'
down_revision = '<current_head>'

def upgrade() -> None:
    # R2: poison-pill columns
    op.add_column(
        'flex_imports',
        sa.Column('status', sa.String(20), nullable=False, server_default='ok'),
    )
    op.add_column(
        'flex_imports',
        sa.Column('poison_reason', sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        'flex_imports_status_check',
        'flex_imports',
        "status IN ('ok', 'poison')",
    )

    # R6: xml_hash scope per-user
    op.drop_constraint('flex_imports_xml_hash_key', 'flex_imports', type_='unique')
    op.create_unique_constraint(
        'flex_imports_user_xml_hash_key',
        'flex_imports',
        ['user_id', 'xml_hash'],
    )


def downgrade() -> None:
    op.drop_constraint('flex_imports_user_xml_hash_key', 'flex_imports', type_='unique')
    op.create_unique_constraint(
        'flex_imports_xml_hash_key', 'flex_imports', ['xml_hash']
    )
    op.drop_constraint('flex_imports_status_check', 'flex_imports', type_='check')
    op.drop_column('flex_imports', 'poison_reason')
    op.drop_column('flex_imports', 'status')
```

**Regression coverage:** `test_migrations_apply_cleanly_and_match_metadata` (existente) cubre drift contra `Base.metadata`.

## 5. Orden de implementación

| Step | Cambio | Schema? | Tests nuevos |
|---|---|---|---|
| 1 | `RetryPolicy` class + refactor `client.py` (R5) | No | ~12 |
| 2 | Alembic migration: status + poison_reason + UNIQUE(user, hash) | Sí | ~3 |
| 3 | Poison-pill code + `check_hash_status` refactor (R2 + R6 fast-path) | No | ~15 |
| 4 | Latest-1 cleanup en `persist()` (R1) | No | ~6 |
| 5 | Replay test suite (R3) — incl. `testcontainers-postgres` | No | ~10 |
| 6 | Health endpoint + multi-user API tests (R4 backend + R6 API) | No | ~8 |
| 7 | Frontend banner + Settings tab (R4 UI) | No | ~5 Playwright |
| **Total** | | | **~59 tests** (245 → ~304 backend + Playwright) |

**Dependencias hard:**
- Step 2 antes que Step 3 (código usa columnas nuevas)
- Step 2 antes que Step 4 (cleanup WHERE `status='ok'`)
- Step 3 antes que Step 5 (replay test exercise poison recovery semantics)
- Step 6 antes que Step 7 (frontend consume el endpoint)

**Paralelizable:** Step 5 y Step 6 son independientes después de Step 4.

## 6. Test strategy

**TDD obligatorio per-step:** failing test → minimal impl → passing test → commit. Mismo método de Phase 2 y Phase 2.5.

**Test pyramid:**
- Unit (~70%): `RetryPolicy`, `check_hash_status`, latest-1 cleanup logic, schemas
- Integration (~25%): persister con multi-user, poison lifecycle, health endpoint
- E2E (~5%): replay cross-schema (testcontainers), Playwright banner

**Tools:**
- `pytest-asyncio` (existente)
- `testcontainers-postgres` (NUEVO — agregar a `pyproject.toml` dev-deps)
- `fastapi-users` test fixtures (heredado de Phase 1)
- Playwright (existente)

**Coverage target:** mantener ≥88% (Phase 2 baseline). Subir donde aplique sin forzar.

## 7. Migration plan a producción

1. PR sobre rama `phase26/flex-hardening` desde `main`.
2. CI verde (245 → ~304 tests).
3. Local: `make prod-local` smoke test:
   - rebuild + alembic upgrade
   - manual refresh + verificar banner verde
   - forzar failure (token inválido temporal) + verificar banner rojo + recovery
4. Merge a `main` + tag `v0.2.4-flex-hardening`.
5. Coolify redeploy. Migration corre automática al boot (alembic upgrade head en startup hook).
6. Smoke test prod: trigger manual refresh, verificar banner verde. Trigger failure controlada, verificar banner rojo + recovery via DELETE poison.

**Rollback plan:** `alembic downgrade -1` revierte clean (downgrade implementado). Datos en `flex_imports` preservados (las columnas nuevas se dropean, UNIQUE vuelve a global — pero como hoy solo hay 1 user, no hay colision).

## 8. Métricas de éxito

- ✅ Backend tests: 245 → ~304, todos pasan
- ✅ Frontend Playwright: 3 specs nuevos pasan
- ✅ Coverage: ≥88%
- ✅ Smoke test prod: banner cambia de estado correctamente (verde/yellow/rojo)
- ✅ `flex_imports` size estable después de 30 días de cron diario (post-merge validation)
- ✅ Force poison test: parser bug forzado → status='poison' registrado → recovery via DELETE funciona
- ✅ Multi-user smoke: crear user B en prod, subir mismo XML que user A — no choca

## 9. Open items (NO bloquean implementation)

- **Ubicación del tab "Salud de ingesta" en Settings:** después de "Credenciales Flex" — UX decision al implementar Step 7, ajustable.
- **Sparkline visual:** bars verde/rojo (default) vs line — definir al implementar Step 7.
- **Threshold yellow vs red (24h vs 48h):** documentados, ajuste tras 30 días de operación real.
- **Truncation length de `last_error`:** 500 chars default — ajustable si se vuelve útil para debugging UI.

## 10. Decisiones rechazadas (con razón)

| Rechazado | Razón |
|---|---|
| Comprimir gzip xml_bytes (R1 opción 3) | Latest-1 ya mantiene ~50MB; gzip agrega complejidad sin valor |
| Política diferenciada por year_status auto-promotion (R1 opción 4) | Sobre-ingeniería; latest-1 + sealed pinned es suficiente |
| `parser_version` auto-reset poison (R2 opción 2) | Riesgo de auto-romper si bump se hace por error |
| Tabla separada `flex_poison_imports` (R2 opción 3) | Fragmenta entidad import; legacy-feeling |
| Solo idempotencia test (R3 opción 2) | No atrapa drift de counts ni paridad FIFO |
| Wrapper inline sin refactor `poll_statement` (R5 opción 2) | Tech-debt feeling, duplica backoff logic |
| Circuit breaker en FlexClient (R5 opción 3) | Overkill para 1×/día cron; V2 si manual frequente |
| Push notifications (R4 opción 3) | Single-user activo no lo necesita; V2 |
| Solo persister tests sin API (R6 opción 3) | Acumula deuda; "schema-multi-user-ready" debe ser testeado |

## 11. Sucesor

Una vez approved + spec self-review pasa, invocar `superpowers:writing-plans` para producir:
`docs/plans/2026-05-25-phase26-flex-hardening.md`

El plan desglosa cada Step (1-7) en tasks atómicas TDD-friendly con commits por task, siguiendo método de Phase 2 y Phase 2.5.

---

**End of spec.**
