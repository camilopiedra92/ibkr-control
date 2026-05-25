# Phase 2 Polish Backlog Implementation Plan

> **Status:** ✅ CERRADO — todos los items resueltos antes de cerrar Phase 2 oficialmente.
> **Fecha de cierre:** 2026-05-24
> **Tag al completar:** `v0.2.0-ingest` (re-pointed al último commit de polish)

**Goal:** Cerrar los items detectados durante el audit post-merge de Phase 2 antes
de arrancar Phase 3. Detectados por audit técnico contra criterios spec §12,
auditoría de paridad con sibling renta, y revisión de seguridad/concurrencia.

**Architecture:** Cambios quirúrgicos en código de Phase 2 ya mergeado a `main`.
Cada item cierra en commit propio. No nueva phase, no nuevo branch — todo directo
a `main` siguiendo convención de Phase 1 polish backlog.

**Resumen ejecutivo:**

| Métrica | Antes polish | Después polish |
|---|---|---|
| Backend tests | 151 | **186** (+35) |
| Coverage overall | 83% | **88%** |
| Coverage `api/credentials.py` | 39% | **100%** |
| Coverage `api/setup.py` | 34% | **73%** |
| Migrations | 4 | **6** (+ E dividend_accruals + F widen_precision) |
| Numeric precision | (20,2) totals | **(20,4) USD totals + (20,8) qty** |
| Polish commits | 0 | **10** |

---

## Items cerrados (en orden de commit)

### Item 0 — Migration E: dividend accruals addendum

**Origen:** Comparación con sibling renta (`renta/documentos/ibkr_flex/_known_tags.py`
vs ibkr-control) detectó gap: renta ingiere `ChangeInDividendAccrual` y
`OpenDividendAccrual` pero nosotros no. Phase 5 (Form 210 paridad) requeriría esto.

**Solución:** Migration E con 2 nuevas tablas siguiendo schema de renta:
- `change_in_dividend_accruals` — per-event accrual changes
- `open_dividend_accruals` — period-end snapshot

Parser extendido + persister extendido + filtro `accountId == "-"` matching renta.
En el 2025 fixture: 51 rows en change_in (DETAIL level) + 1 en open
(NKE Q4 2025 declarado, paga 2026-01-02). 10 tests nuevos.

**Commit:** `fa9c1df` — feat(phase2): dividend accruals addendum (paridad gap with renta)

---

### Item 1 — Migration F: widening precision

**Origen:** Review en Task 8 flaggeó `Numeric(20,2)` para `cost_basis_usd` —
XML trae 6 decimales (`207.104329`), DB redondea a 2 silenciosamente, pérdida
sub-centavo acumulable en FIFO de Phase 3.

**Solución:** Migration F (metadata-only, no rewrite Postgres):
- Monetary USD totals: `Numeric(20,2)` → `Numeric(20,4)` (17 columnas)
- Quantities: `Numeric(20,6)` → `Numeric(20,8)` (6 columnas)
- Prices, TRM, commissions, pct: sin cambio (ya correcto)

**Rationale documentado:** investigación de formato (Stripe integer minor units
vs NUMERIC) → elegido NUMERIC por (a) volumen bajo, (b) multi-currency USD/COP/TRM,
(c) Python Decimal interop.

**Commit:** `f810f08` — feat(phase2): Migration F — widen Numeric precision pre-Phase 3

---

### Item 2 — DoS prevention: validate upload size BEFORE reading into memory

**Origen:** Audit detectó `xml_bytes = await file.read()` antes de check de tamaño.
Un POST de 1GB consume 1GB de RAM antes de rechazarse.

**Solución:** En `api/imports.py`:
1. Chequear `UploadFile.size` (Starlette property derivado de Content-Length) FIRST
2. Si exceeds → 413 antes de cualquier I/O
3. Si size es None (cliente no envió Content-Length) → streaming read con
   acumulador de bytes; abort cuando excede limit

2 tests nuevos (rechazo a 51MB declarado, default limit).

**Commit:** `907cc7a` — fix(phase2): validate upload size before reading into memory (DoS prevention)

---

### Item 3 — Remove leaked real IBKR account IDs from frontend defaults

**Origen:** `frontend/src/components/wizard/Step2Accounts.tsx` tenía hardcoded:
```typescript
const DEFAULT_ACCOUNTS = [
  { ibkr_account_id: "U99999001", alias: "Joint Test Owner+Joint Holder", pct: "0.5000" },
  { ibkr_account_id: "U99999002", alias: "Personal swing", pct: "1.0000" },
  { ibkr_account_id: "U99999003", alias: "Personal futuros", pct: "1.0000" },
];
```

Estos son **IDs reales del usuario** del CLAUDE.md que leakearon a código
committeable. Cualquiera con acceso al repo veía sus account IDs.

**Solución:** Replaced con un solo row vacío `{ ibkr_account_id: "U", alias: "", pct: "1.0000" }`.
Usuario completa el wizard desde cero — UX trade-off aceptable por privacidad.

**Commit:** `6d8aa24` — fix(phase2): remove leaked real account IDs from wizard defaults

---

### Item 4 — Type-safe RotateTokenModal (remove `as unknown as` cast)

**Origen:** `frontend/src/components/settings/RotateTokenModal.tsx:33` tenía:
```typescript
onSuccess(data as unknown as FlexCredentialsRead);
```
Doble cast = bypass de TypeScript. Workaround por orval generando POST como
useQuery con shape incompatible.

**Solución:** El backend `PUT /api/credentials/flex` retorna `{"ok": true}`,
no `FlexCredentialsRead`. El modal ahora llama `onSuccess()` sin payload y el
parent (`FlexCredentialsSection`) re-fetchea los metadata. Cast eliminado.

**Commit:** `8f8cbeb` — fix(phase2): type-safe RotateTokenModal — refetch instead of casting

---

### Item 5 — Narrow `except Exception` handlers

**Origen:** Audit encontró 5 `except Exception` clauses en scheduler/jobs.py,
flex/job.py, api/imports.py. Esconden la causa raíz de errores en producción.

**Solución:** Catches específicos en `_run_flex_for_all_users`:
```python
except LockHeldError:
    logger.warning(...)  # esperado, skip user
except (FlexAuthError, httpx.HTTPError):
    logger.error(...)  # transient, sigue con siguiente user
except Exception:
    logger.exception(...)  # inesperado, full traceback
```
Catches del SAVEPOINT en flex/job.py + SSE background quedan broad con comment
explicando por qué (rollback semantics + advisory_lock release).

**Commit:** `41c1cee` — refactor(phase2): narrow exception handlers for clearer error attribution

---

### Item 6 — Extract `_is_summary_row` helper (dedup in parser)

**Origen:** Pattern `if elem.get("levelOfDetail") == "SUMMARY": continue` repetido
en 3 lugares (`_parse_cash_transactions`, `_parse_open_positions` semánticamente similar,
`_parse_change_in_dividend_accruals`).

**Solución:** Helper `_is_summary_row(elem) -> bool` con docstring explicando
el patrón IBKR SUMMARY/DETAIL duplication. Aplicado donde la semántica es
filter-SUMMARY. `_parse_open_positions` mantiene su `!= "LOT"` stricto (semántica
distinta). Accruals usan `accountId == "-"` (más específico, equivalente).

**Commit:** `461693c` — refactor(phase2): extract _is_summary_row helper (dedup)

---

### Item 7 — Periodic JobTracker cleanup (memory leak prevention)

**Origen:** `JobTracker.cleanup(job_id)` existía pero nunca se invocaba. Long-running
container = `_jobs` dict crece sin límite.

**Solución:**
- `_JobState` ahora tiene `created_at: datetime` field
- Nuevo método `JobTracker.cleanup_old(older_than: timedelta) -> int`
- Nuevo cron job `cleanup_job_tracker` (horario at :00 UTC) que llama
  `cleanup_old(older_than=timedelta(hours=1))`
- Test del scheduler actualizado: ahora son **3 jobs** (flex_daily + trm_daily + cleanup_job_tracker)
- 3 unit tests nuevos para `cleanup_old`

**Commit:** `5ac0f79` — feat(phase2): periodic JobTracker cleanup to prevent memory leak

---

### Item 8 — Externalize MAX_XML_SIZE_BYTES + _COOLDOWN to settings

**Origen:** Hardcoded en módulos: `MAX_XML_SIZE_BYTES = 50 * 1024 * 1024` en
imports.py, `_COOLDOWN = timedelta(minutes=5)` en ingest.py. No tuneable por env var.

**Solución:** Movidos a `Settings`:
```python
max_xml_size_bytes: int = 50 * 1024 * 1024
ingest_trigger_cooldown_seconds: int = 300
```
Consumers leen via `get_settings()` INSIDE function (no module-level — CLAUDE.md convention).

**Commit:** `c6989e7` — refactor(phase2): externalize upload size + rate limit cooldown to settings

---

### Item 9 — Document `_ensure_accounts` race condition trade-off

**Origen:** Audit detectó SELECT-then-INSERT pattern sin ON CONFLICT. Race
condition teórica entre concurrent ingestions del mismo account ID nuevo.

**Solución:** Docstring agregado explicando:
- Cron flow: safe by advisory_lock(source='flex', user_id=X)
- Manual upload flow: NO lock por spec § 6.1 (hashes distintos = trabajo independiente)
- Race teórica para uploads del MISMO account ID nuevo en paralelo
- Mitigación: o segundo lee el row creado por el primero (race ganada sin pérdida)
  o hit del UNIQUE constraint y job fail (re-tryable)
- Acceptable V1; si surge problema, wrap con `ON CONFLICT DO NOTHING`

**Commit:** `d521a52` — docs(phase2): document _ensure_accounts race condition trade-off

---

### Item 10 — Coverage improvements on api/setup.py + api/credentials.py

**Origen:** Spec § 9.4 target 85% para módulos API. Actual al cierre de Phase 2:
- `api/credentials.py`: 39%
- `api/setup.py`: 34%
- `api/imports.py`: 57%
- `api/ingest.py`: 66%

**Solución:** 30 tests nuevos en `test_credentials.py` + `test_setup.py`:

- **`api/credentials.py`: 39% → 100%** ✓ (exceeds 85% target). Tests cubren:
  401 sin auth, 404 sin creds, 400 sin token+query_id en primera vez, 502 si
  IBKR inalcanzable, rotación token-only, rotación query-id-only (sin ping IBKR),
  retorno solo metadata (nunca plaintext del token).

- **`api/setup.py`: 34% → 73%**. Tests cubren: ConnectTimeout en step1 → 502,
  step2 alias update + same-pct idempotent + pct > 1 rejection, step3 con
  uploads pre-existentes, step4_start happy path, `_run_setup_meta_job`
  success + TRM failure early-return. Gap restante (27%) son route bodies
  alcanzables solo via direct-handler calls — limitación de coverage.py con
  ASGI async frames; funcionalmente cubierto por HTTP tests pero no medible.

**Commit:** `b4172d4` — test(phase2): increase coverage on api/setup.py + api/credentials.py

---

## Deuda conocida (NO se resuelve aquí — aceptada como V1)

Estos items fueron evaluados y se decidió no fixarlos en Phase 2 polish:

### D1 — SSE endpoint no verifica ownership de `job_id`

**Archivo:** `backend/src/ibkr_control/api/ingest.py` GET `/stream/{job_id}`

**Issue:** Cualquier usuario autenticado puede stream el progreso de un job de
cualquier otro usuario, pasando un job_id arbitrario.

**Por qué no se fixa:** Phase 2 es single-user V1 (decisión locked #6 spec maestro).
No hay otro usuario para spy on. Fix sería trivial post-Phase 3 cuando se
agregue multi-user: `if job_id not in current_user.owned_jobs: raise 404`.

**Quien hereda:** Phase 3 si activa multi-user. O fix oportunista cuando alguien
toque ese endpoint.

### D2 — `_LAST_TRIGGER` in-memory rate limit — RESOLVED 45127f1

**Resolution:** Reemplazado por columna `users.last_ingest_trigger_at` +
UPDATE atomico condicional. Ver commit 45127f1.

**Original issue (preserved for context):**

**Archivo:** `backend/src/ibkr_control/api/ingest.py:26`

**Issue:** Module-level dict que resetea con container restart. Coolify
auto-restart bypassea el cooldown trivialmente. Multi-replica: cada réplica
tiene su propio dict, total rate limit es N × declared.

**Por qué no se fixa:** Spec § 8.2 explicit V1 trade-off. Fix futuro = redis o
DB-backed (`ingest_trigger_log` table). No urgente para uso personal.

**Quien hereda:** V2 cuando agreguemos Redis (decisión locked #6 also).

### D3 — `proxy.ts` es no-op passthrough

**Archivo:** `frontend/src/proxy.ts`

**Issue:** Next.js 16 renombró `middleware.ts → proxy.ts`. El plan original
asumía server-side auth via httpOnly cookie. Phase 1 implementó auth con
localStorage. `proxy.ts` quedó como passthrough.

**Por qué no se fixa:** Cambiar a httpOnly cookies es trabajo significativo
(invalida JWT actuales, requiere cambios en login/register flow, CSRF
considerations). Phase 1 decision locked.

**Riesgo:** XSS attacks pueden leer el token de localStorage. Mitigado por:
no hay user input renderizado sin escape, Next es React 19 con escape por default,
app personal (no untrusted users).

**Quien hereda:** Polish backlog Phase 4+ si decidimos exponer multi-user.

### D4 — Wizard "resume after browser close" E2E skipped

**Archivo:** `frontend/e2e/wizard.spec.ts:88` (test.skip)

**Issue:** El flujo "user cierra browser en step 3, vuelve mañana, retoma" tiene
lógica de persistencia en `users.setup_progress JSONB` pero ningún E2E lo verifica.

**Por qué no se fixa:** Requiere o (a) mock backend de IBKR Flex WS para que
step 1 pase sin token real, o (b) backend test helper que pre-completa steps
1-2 vía API directa. Ambos son trabajo no trivial.

**Mitigación parcial:** Integration tests en `test_setup.py` cubren la persistencia
del `setup_progress` JSONB. El gap es solo el path end-to-end UI.

**Quien hereda:** Phase 3 o Phase 6 polish.

### D5 — APScheduler in-memory jobstore — RESOLVED 70f9bd0

**Resolution:** Migrado a SQLAlchemyJobStore (jobs persisten en tabla
`apscheduler_jobs` auto-creada por APScheduler) + `misfire_grace_time=21600`
en los 3 crons. Ver commit 70f9bd0.

**Original issue (preserved for context):**

**Archivo:** `backend/src/ibkr_control/scheduler/jobs.py`

**Issue:** Jobs no persisten across restart. Container restart en hora exacta de
cron = ese día no corre el job.

**Por qué no se fixa:** Spec § 6.5 + § decisión #D6: jobs son idempotentes
(re-correr no causa daño), restart frequency es baja, missed run del cron diario
se compensa al día siguiente que ya pulla todo el rango pendiente.

**Quien hereda:** V2 con Redis jobstore si surge necesidad.

### D6 — Cron times hardcoded (07:00/19:30 COT)

**Archivo:** `backend/src/ibkr_control/scheduler/jobs.py`

**Issue:** Hours hardcoded en código. Cambio requiere redeploy.

**Por qué no se fixa:** V1 single-user. No hay caso de uso para cambiar. Si surge,
mover a settings es 5 min.

### D7 — Paridad numérica con renta sibling (Phase 5)

**Issue:** Renta usa Python float arbitrario en cálculos. Nosotros NUMERIC con
precisión finita. Diferencias < $0.0001 esperadas.

**Por qué no se fixa ahora:** Es trabajo de Phase 5 (Reporte Form 210), no de
Phase 2. Documentado en spec § 9.5 explícitamente.

**Quien hereda:** Phase 5 con tolerancias documentadas en tests de paridad.

### D8 — `_known_tags.py` audit fail-loud

**Archivo:** `backend/src/ibkr_control/ingest/flex/_known_tags.py`

**Issue:** Si IBKR agrega un tag nuevo mañana, TODO el ingest aborta hasta que
alguien actualice el catálogo. Cron job que falla diariamente puede ser molesto.

**Por qué no se fixa:** Es feature, no bug. Preferimos fail-loud sobre silent
data loss. Pattern replicado de renta `_audit.py`.

**Mitigación:** El error message es claro y específico (incluye el tag desconocido).

**Quien hereda:** Si se vuelve recurrente, considerar env var `FLEX_AUDIT_MODE=warn`
para downgrade a warning log (V2).

### D9 — Phase 1 Task 15: deploy manual a Coolify

**Issue:** Phase 1 dejó esto como pending del usuario. Phase 2 también.

**Quien hereda:** Usuario, post-merge a remote. CLAUDE.md § "Open from Phase 1"
mantiene el item visible.

### D10 — `auth_headers` fixture re-registra user per-test

**Issue:** Performance — ~30 tests × ~200ms overhead = ~6s por full suite run.

**Por qué no se fixa:** Aislamiento de tests vale la pena. Optimization V2 si la
suite crece a > 1000 tests.

### D11 — Coverage gaps en api/imports.py (57%), api/ingest.py (66%), scheduler/jobs.py (41%)

**Issue:** Spec § 9.4 targets de 85% (API) y 70% (scheduler) no se cumplen para
estos módulos.

**Por qué no se fixa:** Gaps son por SSE streaming paths (no testeables con
httpx async client, requieren live server) y cron handlers (no ejecutables sin
APScheduler corriendo en event loop real). Smoke test post-deploy a Coolify
cubre estos flows operacionalmente.

**Quien hereda:** Phase 3 si toca estos endpoints, agregar tests al pasar.

### D12 — `setup._trm_backfill_background` es fire-and-forget silencioso (post-2026-05-25)

**Issue:** El wizard dispara el TRM backfill via `BackgroundTasks` después de
`step2/save` (per spec D6). El handler envuelve la llamada en
`try/except` con `logger.exception(...)` pero NO emite ningún evento al
frontend del wizard. Si Socrata falla, si el persister crashea, o si la red del
container no resuelve `datos.gov.co` (clase de bug DNS macOS, ver
`8bd578f`), el wizard sigue mostrando "Setup completado!" como si todo
hubiera salido bien. La evidencia queda solo en `ingest_log` + container logs.

Esto fue precisamente lo que ocultó el bug del 32767 (D2 hijo, ver "TRM fetch"
en CLAUDE.md retrospectiva 2026-05-25): el job venía fallando desde el primer
disparo del wizard sin signal alguno en UI.

**Por qué no se fixa ahora:** El path "Settings → Refresh manual → Ejecutar
ahora" YA tiene el patrón SSE completo (post-fix del 2026-05-25 a
`/api/ingest/stream/{job_id}` con substeps `trm_backfill` / `flex_ytd` y
status=failed con error). Eso cubre el caso "verificar manualmente que el TRM
quedó OK post-wizard". Para hacer lo mismo dentro del wizard hay que:

1. Registrar un job en `JobTracker` antes del `background.add_task`
2. Devolver el `job_id` en la respuesta de `step2/save` (cambio de shape →
   regenerar cliente TS + actualizar el componente del wizard)
3. Que el wizard consuma `/api/ingest/stream/{job_id}` (nuevo `useIngestStream`
   en el flujo) y bloquee el "Continuar a Step 3" hasta que termine OK/fail
4. Definir UX de error: ¿permitir retry inline? ¿skip + warn? ¿bloquear?

Es scope distinto + decisión de UX. Mientras tanto la mitigation operacional es
"Settings → Refresh manual" que ya funciona con feedback completo.

**Quien hereda:** Phase 5 (polish UI) o un mini-PR de polish post-Phase-3 si
empieza a doler en uso real. Si el cron diario corre bien (y ahora con el fix
del chunking lo hace), la deuda es de UX del wizard, no operacional.

---

### D13 [BUG-FIXED] — Persister Flex no idempotente (resuelto en Phase 2.5)

**Status:** **RESOLVED** en tag `v0.2.3-persister-idempotent` (branch `phase25/flex-persister-idempotent`).

**Bug:** El persister Phase 2 dedupea solo a nivel `xml_hash`. La Flex YTD del
Web Service cambia byte-a-byte cada día (mark prices, timestamp, eventos nuevos)
→ hash siempre nuevo → `session.add(Trade(...))` choca con `UNIQUE(transaction_id)`
global → cron + manual refresh fallan al 2do run con `UniqueViolationError`.

**Detectado:** 2026-05-25 durante refresh manual desde Settings. Síntoma visible:
UI mostraba "✓ Refresh completado" (race condition independiente en
`ManualRefreshButton.tsx` — fix en el mismo PR) mientras `ingest_log` mostraba
`flex=failed` con stacktrace.

**Causa raíz arquitectónica:** mezcla de dos modelos contradictorios
(dedup a nivel XML vs UNIQUE global por transaction_id) sin idempotencia
real per-row.

**Fix (Phase 2.5):** rewrite del persister a UPSERT por natural key per-entity,
con semántica diferenciada immutable (DO NOTHING, first-seen) vs snapshot
(DO UPDATE, last-updated-by). Decisiones A0-A8 lockeadas + 2 amendments durante
implementación (A3 #1 para `originating_transaction_id` en open_position_lots,
A3 #2 para `code` en accruals). Spec: `docs/specs/2026-05-25-flex-persister-idempotent-design.md`.
Plan: `docs/plans/2026-05-25-flex-persister-rewrite.md`. 4 Alembic revisions +
script manual de wipe entre Rev1 y Rev2. Tests: 215 → ~240+.

**Lecciones (escritas para evitar repetir):**

1. **Smoke E2E debe disparar jobs ≥2 veces consecutivas.** El único smoke de
   Phase 2 corrió Flex 1 vez (DB vacía, sin conflictos). Bug invisible. Aplicar
   a cualquier background task Phase 3+ que toque DB.

2. **UNIQUE constraint sin UPSERT es trampa.** Si una tabla tiene `UNIQUE(X)`
   y el escritor hace plain INSERT, cualquier re-run rompe. Siempre que se
   agregue UNIQUE, parear con decisión explícita de qué hacer ON CONFLICT.

3. **Hash dedup a nivel "documento entero" es engañoso para fuentes que cambian
   continuamente.** Para YTD/rolling data, dedupear a nivel fila (natural key)
   es la única respuesta correcta. Hash queda como fast-path optimization.

4. **Natural keys validar contra data REAL antes de lockear como UNIQUE.** Spec
   A3 original tenía 2 colisiones contra el fixture 2025: open_position_lots
   (multi-fill orders) y dividend accruals (Po/Re events). Solo se detectaron
   ejecutando los tests integration contra el XML real. Lección: brainstorming
   debe verificar fixture data antes de cerrar natural keys.

5. **Append-only ledger pattern con SET NULL.** Borrar un flex_import no debe
   matar hechos immutable que también aparecen en otros imports. SET NULL en
   FK preserva data; CASCADE viola la semántica de first-seen.

---

## Verificación final

```bash
cd backend && uv run pytest -v
# 186 passed

cd frontend && pnpm build
# Compiled successfully

git log --oneline main 6b97e46..HEAD
# 10 commits since Phase 2 merge

git tag -l "v0.2*"
# v0.2.0-ingest (re-pointed)
```

---

## Lecciones de Phase 2 polish

1. **Audit thorough post-merge vale la pena**. Detectó 8 fixes reales + 30 tests nuevos
   antes de tagear oficialmente. Mejor cerrar todo de una.
2. **Coverage targets aspiracionales no siempre alcanzables** — coverage.py + ASGI
   async tiene una limitación conocida que no se puede solucionar con tests "normales".
   Documentar el gap es honest engineering.
3. **Privacy / PII leak en frontend defaults** es un patrón fácil de evitar: nunca
   poner valores reales del usuario en código que va al repo, ni siquiera en
   comments. Use placeholders genéricos siempre.
4. **Real Activity XMLs > docs spec**: el catálogo de `_known_tags` empezó con
   ~25 tags estimados y terminó con 56+. Lección: leer DATA real antes de
   escribir spec, no solo docs.
5. **Renta sibling paridad detectó gap real**: dividend accruals. Sin la
   comparación, lo descubríamos en Phase 5 cuando el Form 210 no matcheara.

---

**Commit final del polish:** después de este doc + actualización CLAUDE.md →
re-point `v0.2.0-ingest` tag.

---

## Post-Phase-2: Wizard redesign (closed 2026-05-24, tag v0.2.2-wizard-redesign)

Smoke test del 2026-05-24 reveló 5 bugs en el wizard original (IDs ciegos, no
validación contra cuentas reales, F-suffix shadow accounts en `accounts` table,
re-validación redundante contra IBKR, flags JSONB desync). Reescritura
completa:

- Spec: `docs/specs/2026-05-24-wizard-redesign-design.md`
- Plan: `docs/plans/2026-05-24-wizard-redesign.md` (16 tasks)
- Implementación: 17 commits + tag `v0.2.2-wizard-redesign` en branch `feat/wizard-redesign`
- Tests: backend 207 (was 192, +15), frontend 3 nuevos Playwright specs
- Migration H: wipea data legacy preservando flex_credentials y apscheduler_jobs

Cierra el item D6 del CLAUDE.md (cron times) parcialmente — el persister ahora
filtra F-shadow universalmente, lo que mejora la calidad de los snapshots de
cualquier cron.

---

## Post-merge a main: smoke test dev fixes (2026-05-24)

Después de mergear `feat/wizard-redesign` a `main`, el smoke test contra IBKR
real reveló 4 items no contemplados por el spec del wizard. 3 resueltos pre-deploy
a prod, 1 deferred a Phase 3. Tests 207 → 209.

### Resueltos en `main` (no en branch, no en tag)

**1. DNS infra** (`8bd578f` — `fix(infra): force public DNS on backend container`)
- **Síntoma:** `httpx.ConnectError: [Errno -2] Name or service not known` para `gdcdyn.interactivebrokers.com` desde container backend.
- **Causa raíz:** macOS host con resolver local en `127.0.2.2/3` (AdGuard/NextDNS/VPN/Little Snitch) devolviendo SERVFAIL para ese dominio. Docker Desktop hereda el resolver del host vía `192.168.65.7`.
- **Fix:** pin `dns: [1.1.1.1, 8.8.8.8]` en `docker-compose.yml` para servicio backend (solo backend lo necesita — postgres no toca internet, frontend solo sirve assets).
- **Implicación prod:** Coolify normalmente usa DNS público por default. Verificar que la network del compose en Coolify NO tenga override que herede el resolver del host server.

**2. Fallback de upload manual no persistía** (`24aa5f9` — `fix(api): step2/detect_from_xml must persist so step2/save accepts the IDs`)
- **Síntoma:** cuando IBKR responde 1001 BUSY y el usuario usa "Subir XML manualmente", `step2/save` rebota cada cuenta con `400 ACCOUNT_NOT_DETECTED`.
- **Causa raíz:** spec D2 del wizard mandaba que `detect_from_xml` fuera "parse-only, no DB writes". Pero `step2/save` valida cada `ibkr_account_id` contra `accounts` table como anti-typo defense. Si el fallback no persiste, save siempre falla.
- **Fix:** `detect_from_xml` ahora invoca `flex_persister.persist(...)` con `source="manual_upload"` (mismo path que `detect` pero distinto source). El persister dedupea por SHA-256 → re-subir el mismo XML es idempotente.
- **Tests nuevos:** `test_detect_from_xml_persists_accounts_and_flex_import` (regression lock) + `test_detect_from_xml_is_idempotent_on_same_sha` (dedup verification).
- **Spec note:** D2 está SUPERSEDED. El fallback ahora es semánticamente equivalente a un upload manual de Step 3.

**3. Migración a Flex Web Service V3** (`4eb4f80` — `feat(flex): migrate client to Flex Web Service V3 endpoints`)
- **Síntoma:** ningún error inmediato — pero la doc oficial (`interactivebrokers.com/campus/ibkr-api-page/flex-web-service/`) documenta endpoints distintos a los que usábamos.
- **Causa raíz:** estábamos usando el host legacy `gdcdyn.interactivebrokers.com` con paths `/Universal/servlet/FlexStatementService.*`. La V3 oficial (la única documentada como "current") vive en:
  - `https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest`
  - `https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement`
- **V3 requiere `User-Agent` header explícito** ("all requests must include a User-Agent header"). httpx mandaba `python-httpx/x.y.z` por default → IBKR puede penalizarlo como bot.
- **Fix:** swap de constantes `DEFAULT_BASE_URL` + `SEND_REQUEST_PATH` + `GET_STATEMENT_PATH` en `flex/client.py`. Adicionar `headers={"User-Agent": "ibkr-control/0.2 (+...)"}` a ambos `httpx.AsyncClient(...)`. Find-replace en tests + VCR cassettes (5 archivos). El param `v=3` ya estaba.
- **Verificado en prod:** ambos hosts (legacy + V3) responden HTTP 200 con el mismo XML format (`<FlexStatementResponse>` + ErrorCodes) ante token fake. Cambio es backwards-compatible en response shape.
- **Aclaración importante:** la migración V3 **NO resuelve el 1001 BUSY**. Per doc oficial: "Activity Statement Flex Queries contain data that is only updated once daily at close of business, so there is no benefit to generating and retrieving these reports more than once per day." El pacing oficial es 1 req/s, 10/min. En prod el cron 1x/día post-cierre US lo evita.

### Deferred a Phase 3

**4. Counterparty accounts dejan huérfanos en `accounts`** (sin commit, flagged)
- **Síntoma:** post-smoke-test, `accounts` table tenía 4 rows aunque el usuario configuró 3 (las 3 U-prefix reales). La 4ta era `CS-999999-99` sin participation.
- **Investigación:** `CS-999999-99` apareció en un único `<Transfer>` con `direction=IN`, `symbol=GLOB`, 94 acciones, `dst=U99999002`. Confirmación del usuario: era el bono RSU de Globant pagado vía Shareworks/Solium/Morgan Stanley StockPlan, transferido manualmente a IBKR.
- **Causa raíz:** persister recolecta `account_id` de TODOS los tags del XML (trades, cash, lots, transfers, dividend_accruals), no solo de `<AccountInformation>`. Counterparties externos terminan como Account rows huérfanos.
- **Fix sistémico (Phase 3):** persister crea `Account` rows SOLO para IDs en `<AccountInformation>`. Para transfers, src/dst que no matchee a un Account propio se guarda en una columna `counterparty_ref TEXT` separada del FK. Requiere migration nueva (drop o nullable FK + add counterparty_ref).
- **Decisión:** deferred a Phase 3 (item #6 del §Roadmap Phase 3 en CLAUDE.md) porque (a) requiere refactor de modelo que es del scope del persister rewrite de Phase 3, (b) los counterparty rows son inertes (cero queries downstream los referencian salvo el transfer correspondiente), (c) cleanup tactical del row existente en dev es trivial (`DELETE FROM accounts WHERE ibkr_account_id LIKE 'CS-%'`).

### Lecciones nuevas (escritas en CLAUDE.md §"Wizard redesign post-deploy fixes")

- IBKR 1001 BUSY es inherente al diseño (1x/día post-cierre).
- macOS local resolvers (AdGuard/NextDNS/VPN) son una clase de bug recurrente para containers Docker con egress.
- Flex Web Service V3: host `ndcdyn`, path `/AccountManagement/FlexWebService/`, User-Agent obligatorio.
- "Parse-only, no DB writes" en endpoints fallback es un anti-pattern si endpoints downstream validan contra DB state — el fallback debe dejar el sistema en un estado válido para los siguientes steps.
