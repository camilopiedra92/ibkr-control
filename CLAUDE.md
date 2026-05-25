# CLAUDE.md — IBKR Control Center

## ⏯ Cómo continuar (próxima sesión)

**Phase 2 + wizard redesign completos (tags `v0.2.0-ingest`, `v0.2.1-persistent-state`, `v0.2.2-wizard-redesign`). Próximo: Phase 3.**

Branches `phase2/ingestion` + `feat/wizard-redesign` ya mergeadas a `main` (merge SHA `b458ac6`). Smoke test del wizard en dev completado exitosamente con datos reales IBKR el 2026-05-24.
Tag `v0.2.0-ingest` apunta al cierre original de polish (`a606be2`). Tag `v0.2.1-persistent-state` apunta a `172cc12` — incluye SQLAlchemyJobStore + DB-backed rate limit. Tag `v0.2.2-wizard-redesign` apunta al cierre del rewrite del wizard (detect-first, F-filter en persister, Migration H wipea legacy). **209/209 backend tests pasan, frontend builds clean, 3 nuevos Playwright wizard specs.**

Post-merge sobre `main` (4 commits sin push aún): `8bd578f` infra DNS fix para container backend (resolver local AdGuard/NextDNS SERVFAILa `gdcdyn.interactivebrokers.com` → pinned a `1.1.1.1`/`8.8.8.8`), `24aa5f9` fix gap del fallback "subir XML manual" (ahora persiste igual que `step2/detect` para que `step2/save` valide contra `accounts`), `4eb4f80` migración a endpoints V3 oficiales (`ndcdyn` + `/AccountManagement/FlexWebService/`) + User-Agent header requerido. Ver §"Wizard redesign post-deploy" abajo para detalle completo.

### Camino A — Planificar Phase 3 (recomendado)

Phase 3 = domain layer + 3 pantallas (Lotes Abiertos/Cerrados/Alertas 730d). Spec maestro §6 + §4.2. Phase 3 NO requiere prod deploy ni datos reales — desarrolla 100% contra testcontainer + fixtures sanitizadas.

```
1. Verificar Phase 2 + wizard redesign cerradas:
   git tag -l "v0.2*" → debe mostrar v0.2.0-ingest + v0.2.1-persistent-state + v0.2.2-wizard-redesign
2. Leer este CLAUDE.md (lo cargás automáticamente)
3. Leer docs/plans/2026-05-24-phase2-polish-backlog.md (entender deuda conocida D1-D12
   + apendice "Post-Phase-2: Wizard redesign")
4. Invocar `superpowers:brainstorming` con prompt:
   "Phase 3 = domain layer + lotes. Decisiones abiertas listadas en
    CLAUDE.md §Roadmap Phase 3. Resolver una a una, luego escribir spec
    en docs/specs/YYYY-MM-DD-phase3-domain-design.md"
5. Después `superpowers:writing-plans` → docs/plans/YYYY-MM-DD-ibkr-control-phase3-lotes.md
6. Update tabla "Estado actual" con el link al plan nuevo
7. Ejecución: superpowers:subagent-driven-development (mismo método que Phase 2)
8. Branch: git checkout -b phase3/lotes main
```

### Camino B — Push a remote + deploy a Coolify (tarea del usuario)

Smoke test en dev YA está hecho (2026-05-24, ver §"Wizard redesign post-deploy"). Falta solo push + deploy prod:

```
1. Push main + tags a remote (~30s):
   git push origin main --follow-tags
   # Sube los 4 commits post-merge (b458ac6, 8bd578f, 24aa5f9, 4eb4f80) +
   # tags v0.2.0/v0.2.1/v0.2.2 a GitHub

2. Configurar TOKEN_ENCRYPTION_KEY en Coolify (1 min):
   openssl rand -base64 32  # generar key
   # Pegarla en Coolify env vars del backend container
   # CRÍTICO: sin esta key, decrypt_token() crashea al primer fetch del cron

3. Confirmar DNS en Coolify (1 min):
   # El compose.yml local ahora pinea `dns: [1.1.1.1, 8.8.8.8]` en backend.
   # Coolify normalmente usa DNS público por default — verificar que no
   # haya un override de network que herede DNS del host server. Si lo hay,
   # replicar el pin en la config de Coolify.

4. Trigger redeploy desde Coolify UI apuntando a main (~3 min build):
   # Verificar logs: "Registered 3 ingest jobs: flex_daily, trm_daily, cleanup_job_tracker"
   # Migration H wipea data legacy — preserva flex_credentials + apscheduler_jobs
   # Si las migrations no se aplican automáticamente:
   docker exec <backend-container> uv run alembic upgrade head

5. Smoke test end-to-end del wizard en prod (~20-30 min):
   # Mismo flow validado en dev: Step 1 creds → Step 2 detect (online o
   # XML fallback) → Step 2 configure → Step 3 históricos → finish.
   # Si IBKR responde 1001 BUSY (puede pasar — es 1x/día por design),
   # usar "Subir XML manualmente" — funciona end-to-end.
```

### Pre-Phase-3 checklist (verificar al arrancar la próxima sesión)

- [ ] `git status` limpio en `main`
- [ ] `git tag -l "v0.2*"` muestra `v0.2.0-ingest` + `v0.2.1-persistent-state` + `v0.2.2-wizard-redesign`
- [ ] `cd backend && uv run pytest -q` → 215 passed (era 209 al cierre del wizard redesign; +3 por TRM/SSE fix `989652d`, +3 por D12 fix `5dba1b4`)
- [ ] `cd frontend && pnpm build` → exit 0
- [ ] Leer `docs/plans/2026-05-24-phase2-polish-backlog.md` § "Deuda conocida (D1-D12)"
- [ ] (Opcional) `docker compose ps` para confirmar postgres + backend healthy si vas a smoke test

### Convenciones (heredadas)

- TDD: failing test → minimal impl → passing test → commit
- Frequent commits: cada step del plan termina en commit
- No emojis en código (Unicode arrows ✓ ⚠ ✗ → como content UI sí)
- `Decimal` para dinero, nunca float
- Postgres-specific allowed (JSONB, ON CONFLICT, advisory locks)
- No capturar `settings = get_settings()` a nivel módulo — llamar dentro de funciones
- English identifiers en código, Spanish OK en UI strings + docstrings
- Sibling `renta` para referencia fiscal: leer `docs/references/renta-cross-references.md` antes de implementar reglas fiscales. **Reimplementar, NO importar**.

### Open items (pendientes — no bloquean Phase 3)

| Item | Quién | Bloquea? |
|---|---|---|
| **Push main + 4 commits post-merge + tags a remote** (`git push origin main --follow-tags`) | Usuario | No bloquea Phase 3 dev local |
| **Deploy a Coolify + setear `TOKEN_ENCRYPTION_KEY` + verificar DNS público** | Usuario | No bloquea Phase 3 dev local |
| **Smoke test end-to-end en dev** ✅ completado 2026-05-24 | — | — |
| **Smoke test end-to-end en prod** | Usuario | No bloquea Phase 3 (dev validó el flow) |
| **Counterparty account `CS-######-##` queda como orphan account** (1 row dejada de smoke test — `CS-999999-99` del transfer GLOB desde Shareworks) | Aceptado | No bloquea Phase 3 — se resuelve cuando Phase 3 reescriba el persister, ver §Roadmap Phase 3 |
| **11 items de deuda conocida D1-D11** documentados en polish backlog (D12 RESOLVED en `5dba1b4` 2026-05-25 — wizard TRM ahora con SSE feedback) | Aceptados como V1 | No bloquean — la mayoría tienen mitigation o son features (fail-loud audit, etc.) |

### Deuda conocida heredada de Phase 2 (resumen)

Ver `docs/plans/2026-05-24-phase2-polish-backlog.md` § "Deuda conocida" para detalle completo + rationale. Highlights:

- **D1** SSE endpoint no verifica ownership de `job_id` — V1 single-user OK
- **D3** `proxy.ts` Next 16 no-op (auth client-side localStorage) — Phase 1 lock
- **D4** E2E wizard "resume after browser close" test SKIPPED — necesita IBKR mocks
- **D6** Cron times hardcoded (07:00/19:30 COT) — V1 acceptable
- **D7** Paridad numérica con renta — trabajo de Phase 5
- **D8** `_known_tags.py` fail-loud audit — diseño deliberado, no bug
- **D9** Phase 1 Task 15: Coolify deploy — usuario
- **D10** `auth_headers` fixture per-test overhead — V2 perf
- **D11** Coverage gaps en imports/ingest/scheduler — requieren live infra para subir más
- **D2 + D5 + D12 RESOLVED**: D2/D5 persistent state migration (commits 70f9bd0 + 45127f1); D12 wizard TRM fire-and-forget resuelto en `5dba1b4` — `step2/save` ahora registra job en `JobTracker` y devuelve `trm_backfill_job_id`, frontend renderiza `TrmBackfillBanner` con SSE state running/ok/failed encima del stepper

### Decisiones locked (no re-discutir)

**Spec maestro (12 decisiones):** ver "Decisiones arquitectónicas" abajo. Acordadas en la sesión de brainstorming del 2026-05-24.

**Phase 2 spec (17 decisiones D1-D17):** ver `docs/specs/2026-05-24-phase2-ingestion-design.md` §3. Cubren scope, UX del wizard, testing strategy, advisory locks, AES-GCM, validación XML, organización per-source folders, SSE para progress, etc. (D6 APScheduler in-memory + `_LAST_TRIGGER` in-memory rate limit fueron SUPERSEDED post-Phase-2 — ver commits `70f9bd0` + `45127f1` y notas en el spec).

Si surge una pregunta cuya respuesta ya está en cualquiera de estos specs, NO re-hacer la pregunta al usuario — referenciá el spec/CLAUDE.md y avanzá.

### Phase 2 — Retrospectiva (deviaciones del spec original)

Detalles útiles para evitar re-depurar en fases futuras:

- **Real Activity XMLs tienen 31+ top-level tags** — el spec asumía un catálogo más pequeño. Task 8 construyó `_known_tags.py` enumerando todos los tags reales encontrados. Si aparecen XMLs de cuentas nuevas, puede haber tags nuevos → el audit los detecta y aborta con error claro.
- **CashTransaction SUMMARY/DETAIL duplication** — los XMLs reales tienen filas tanto de detalle individual como de subtotales SUMMARY para la misma transacción. Task 8 agregó lógica de filtro para excluir filas con `levelOfDetail != "DETAIL"` antes de parsear.
- **`_lock_key` debe ser determinístico** — la primera implementación generaba la clave con `hash()` de Python, que varía entre procesos (PYTHONHASHSEED). Task 5 lo reemplazó con `zlib.crc32` para garantizar que el advisory lock sea el mismo en worker y cron.
- **Next.js 16 renombró `middleware.ts` semántica** — el proxy de dev (`/api/*` → backend) se mueve a `src/lib/proxy.ts` o similar porque `middleware.ts` en Next 16 tiene restricciones de Edge Runtime que no permiten `http-proxy`. Task 16 resolvió esto con una API route handler en `app/api/[...path]/route.ts`.
- **Orval genera POSTs como `useQuery` en algunos casos** — cuando el endpoint tiene `requestBody` pero Orval no puede inferir mutación, genera un hook `useQuery` en vez de `useMutation`. Workaround: usar `useMutation` de TanStack directamente con la función fetch generada por Orval, no el hook auto-generado.
- **Dividend accruals — addendum post-Phase-2** — el plan original de Phase 2 no incluía `ChangeInDividendAccrual` / `OpenDividendAccrual`. Al comparar con el sibling renta (que SÍ los ingiere), se detectó gap antes del merge. Task 21 agregó Migration E con 2 tablas (`change_in_dividend_accruals`, `open_dividend_accruals`) + parser + persister filtrando rows con `accountId="-"` (matching renta's `_ingest_dividends.py`). En el fixture 2025: 51 rows DETAIL persistidas en change_in + 1 row en open. Fixture 2024 no tiene accruals (Flex Query distinta).
- **Polish backlog post-merge (10 items)** — audit post-Phase-2 detectó bugs + tech debt resueltos antes del cierre oficial: (1) DoS upload sin streaming size check, (2) IDs de cuentas reales leakeados a frontend defaults, (3) double-cast `as unknown as` en RotateTokenModal, (4) `except Exception` genéricos en scheduler/jobs, (5) `_is_summary_row` helper extraído, (6) `JobTracker.cleanup_old()` + cron job horario nuevo, (7) `MAX_XML_SIZE_BYTES` + `_COOLDOWN` movidos a Settings, (8) docstring de race condition `_ensure_accounts`, (9) coverage `api/credentials.py` 39%→100% + `api/setup.py` 34%→73%, (10) Migration F: widening precision `Numeric(20,2)→(20,4)` USD totals + `(20,6)→(20,8)` quantities (matches IBKR XML source + sub-cent FIFO room para Phase 3). Total: 186 tests (era 151, +35), coverage overall 88% (era 83%). Ver `docs/plans/2026-05-24-phase2-polish-backlog.md` para detalle + deuda conocida documentada (D1-D11; D12 agregado el 2026-05-25 post-TRM fixes).
- **Formato de datos numéricos — decisión locked** — Phase 2 polish llegó a `Numeric(20,4)` para totales USD, `Numeric(20,8)` para quantities, `Numeric(20,6)` para prices, `Numeric(12,4)` para TRM. Rechazado integer minor units (Stripe-style) por (a) volumen bajo, (b) multi-currency USD/COP/TRM hace error-prone trackear scale por columna, (c) Python `Decimal` interop más limpio con NUMERIC. Match precisión del XML IBKR source + DIAN TRM.
- **Post-Phase-2 persistent state migration (D5+D2 RESOLVED)** — sesión 2026-05-24 mergeó 5 commits (deps `c86e308` + D5 scheduler `70f9bd0` + D2 rate limit `45127f1` + docs `27f9442` + plan `8b823f8`) reabriendo dos decisiones "locked" del spec Phase 2: (a) APScheduler ahora usa `SQLAlchemyJobStore` persistente — tabla `apscheduler_jobs` auto-creada al boot, `misfire_grace_time=21600` (6h) en los 3 crons para recuperar runs perdidos por container restart; (b) rate limit del endpoint `POST /api/ingest/trigger` movido de dict in-memory `_LAST_TRIGGER` a columna `users.last_ingest_trigger_at` (Migration G) + UPDATE atómico condicional (`WHERE last_ingest_trigger_at IS NULL OR < now() - cooldown`) sin race TOCTOU. Nueva dep: `psycopg[binary]>=3.1` (driver sync que APScheduler 3.x requiere — el app sigue usando `asyncpg` para todo lo demás). APScheduler pinned a `==3.11.*` para evitar drift de schema del jobstore. Lecciones: (1) las decisiones "locked" pueden re-abrirse si el lift es chico y el upside es real — el patrón es documentar el cambio de criterio en commit + spec note "SUPERSEDED", no editar la decisión vieja; (2) APScheduler 3.x SQLAlchemyJobStore es sync incluso bajo `AsyncIOScheduler` (corre las ops del jobstore en el thread del scheduler, no en el event loop) — por eso necesitamos un driver sync separado del asyncpg de la app. Tests: 186 → 192. Ver `docs/plans/2026-05-24-d5-d2-persistent-state.md`.
- **Wizard redesign post-deploy (2026-05-24, tag v0.2.2-wizard-redesign)** — el smoke test end-to-end con datos reales reveló que el wizard pedía IDs de cuenta ciegos (typos), no validaba contra cuentas reales del usuario, y poblaba `accounts` con 3 F-suffix shadow accounts IB-UK Limited (NAV=0, fees/journals only). Reescritura "detect-first": Step 1 solo guarda creds, Step 2 fetchea Flex YTD + auto-detecta cuentas filtrando F-suffix, pre-pobla alias desde `<AccountInformation accountAlias=>`. Step 3 multi-file drag-drop con detect de cuentas nuevas en XMLs históricos (modal de confirmación). Filtro F a nivel persister (allowlist pattern del sibling renta) garantiza que ningún ingest futuro re-introduzca shadow accounts. Migration H wipea data legacy (preserva `flex_credentials` + `apscheduler_jobs`). Backend: 9 endpoints reescritos bajo `/api/setup/*` + in-memory `_step3_stash` con TTL. Frontend: state machine de 7 pantallas en `WizardPage` reemplaza los 4 steps lineales originales. Tests: 192 → 207 (+15 backend) + 3 nuevos Playwright wizard specs. Spec: `docs/specs/2026-05-24-wizard-redesign-design.md` (D1-D12 locked). Plan: `docs/plans/2026-05-24-wizard-redesign.md` (16 tareas). Branch: `feat/wizard-redesign` (17 commits + docs). Cleanup: legacy `frontend/e2e/wizard.spec.ts` eliminado (superseded por los 3 specs nuevos).

- **Wizard redesign post-deploy fixes (smoke test dev, 2026-05-24, post-merge a main)** — el primer smoke test contra IBKR real reveló 4 problemas adicionales que el spec del wizard no contempló. Todos resueltos pre-deploy a prod, en `main` post-merge:
  - **(1) DNS infra (`8bd578f`):** backend container heredaba el resolver del host (Docker Desktop → `/etc/resolv.conf` → `127.0.2.2/3`). En macOS con AdGuard/NextDNS/VPN/Little Snitch activo, `gdcdyn.interactivebrokers.com` daba `SERVFAIL`. Fix: pin `dns: [1.1.1.1, 8.8.8.8]` al servicio backend en `docker-compose.yml`. En Coolify hay que verificar que la red del compose use DNS público también.
  - **(2) Fallback gap (`24aa5f9`):** el spec D2 mandaba `step2/detect_from_xml` como "parse-only, no DB writes". Pero `step2/save` valida cada `ibkr_account_id` contra `accounts` table (anti-typo) — entonces si IBKR responde 1001 BUSY y el usuario usa el fallback de upload manual, `save` rebotaba 400 `ACCOUNT_NOT_DETECTED` para cada cuenta. Fix: `detect_from_xml` ahora persiste igual que `detect` con `source="manual_upload"`. El persister ya dedupea por SHA-256 (idempotente). +2 tests (regression lock + dedup). Test count 207 → 209.
  - **(3) Migración Flex Web Service V3 (`4eb4f80`):** estábamos en el host legacy `gdcdyn.interactivebrokers.com` con paths `/Universal/servlet/FlexStatementService.*`. La V3 oficial (per `interactivebrokers.com/campus/ibkr-api-page/flex-web-service/`) vive en `ndcdyn.interactivebrokers.com` + `/AccountManagement/FlexWebService/`. Adicionalmente, V3 exige `User-Agent` header explícito ("all requests must include a User-Agent header") — sin él, httpx mandaba `python-httpx/x.y.z` que IBKR puede penalizar como bot. Tests + VCR cassettes actualizados con find-replace. El param `v=3` ya estaba.
  - **(4) Counterparty accounts (DEFERRED a Phase 3):** smoke test detectó una 4ta cuenta `CS-999999-99` huérfana en `accounts` post-wizard. Investigación: era un `<Transfer>` `IN` con symbol `GLOB` (94 acciones) desde Shareworks/Solium/Morgan Stanley StockPlan Connect (broker externo donde Globant deposita el bono RSU) a `U99999002` (cuenta personal del usuario). El persister recolecta `account_id` de TODOS los tags del XML (incluido `<Transfer>`), no solo de `<AccountInformation>`. Counterparties externos terminan como `Account` rows huérfanos (sin participation, sin trades, solo referenciados en 1 transfer). Fix sistémico: persister debería crear `Account` rows SOLO para IDs en `<AccountInformation>`; los counterparties en transfers deberían guardarse como `counterparty_ref TEXT` (no FK). Ver §Roadmap Phase 3 decisión #6.

- **TRM fetch — bulk upsert + SSE contract drift (2026-05-25)** — primer disparo real del TRM post-deploy (cron 00:30 UTC + manual desde Settings + wizard `step2/save`) reveló que el job venía fallando silenciosamente desde el merge. `trm_days` quedó en 0 con 4 rows `failed` en `ingest_log`. Dos bugs independientes que se compusieron para producir la sensación "no hace nada":
  - **(1) `bulk_upsert_days` excedía el límite de bind params de Postgres:** el persister armaba un solo `INSERT … VALUES (…)` con todas las filas expandidas. En full backfill (DB vacía → `since=None`), Socrata devuelve ~8.275 rows que expanden a ~12.595 días × 4 cols = ~50.380 bind params, y asyncpg / Postgres usan int16 para parameter count (techo 32.767). Crash en `_prepare_and_execute` con `InterfaceError: the number of query arguments cannot exceed 32767`. Fix: chunking en batches de 5000 filas (~60% del techo, deja margen si alguien agrega columnas al insert; con 4 cols → 7 round-trips, irrelevante vs los 1.5s del fetch HTTP). Constante `_BATCH_SIZE` monkeypatchable + regression test que fuerza `_BATCH_SIZE=4` con 15 filas → 4 chunks. Aplica también al cron diario porque la primera corrida con DB vacía siempre hace backfill total — el bug no era exclusivo del wizard.
  - **(2) Contrato SSE backend ↔ frontend desincronizado:** `api/ingest.py::_run_manual` emitía `{step: "trm"}` y `{step: "flex"}`, pero `ManualRefreshButton.tsx::buildSubstepStates` esperaba `step: "trm_backfill" | "flex_ytd"` (mismas keys que `test_job_tracker.py:9-14` ya usaba en aislado). Eventos pasaban el filter `if (ev.step in states)` y ambos substeps quedaban ⏸ pending todo el run. Y el catch-all emitía `{step: "error"}` mientras la UI watcheaba `ev.status === "failed"` para `hasFailed` → al final aparecía "✓ Completado correctamente" aun cuando el job había crasheado. Fix: backend ahora emite `trm_backfill` / `flex_ytd` y, en el catch-all, taggea `status: "failed"` + `step: current_step` (tracking explícito del substep en curso) para que la UI pinte la fila correcta en rojo. Dos tests nuevos en `test_ingest.py` corren `_run_manual` directo con fakes e inspeccionan eventos del tracker (uno por path happy, uno por path failed). Backend tests: 209 → 212. Frontend no requiere cambios — el contrato esperado por el componente ya era el correcto.
  - **D12 RESOLVED (`5dba1b4`, 2026-05-25):** `api/setup.py::_trm_backfill_background` ya no es fire-and-forget. `step2/save` registra un job en `JobTracker` con `tracker.create_job()` antes del `background.add_task`, devuelve `trm_backfill_job_id` en la response (Pydantic `Step2SaveResponse`), y el background fn emite `{step: "trm_backfill", status: "running" | "ok" | "failed"}` + `{step: "done"}` + `mark_done` en `finally`. El frontend wizard (`WizardPage` + nuevo `TrmBackfillBanner`) consume `/api/ingest/stream/{job_id}` vía el hook `useIngestStream` y muestra un banner persistente encima del stepper que sobrevive screen-switches hasta que el SSE entrega `ok` (auto-dismiss 8s con n_days) o `failed` (dismissable con el error visible + CTA "reintentá desde Settings → Refresh manual"). Reutiliza el contrato `step="trm_backfill"` ya tested por `_run_manual` para que un futuro consolidation pueda compartir el SSE parser. Tests: 212 → 215 (3 nuevos: response shape, happy path events, failure path).

- **Lecciones del smoke test post-merge** (no son código — son aprendizajes que valen para sesiones futuras):
  - **IBKR 1001 BUSY es inherente al diseño**, no a la versión del API. Doc oficial: *"Activity Statement Flex Queries contain data that is only updated once daily at close of business, so there is no benefit to generating and retrieving these reports more than once per day."* El pacing oficial es 1 req/s, 10/min — nuestro retry `[5,15,30]s` está bien. En prod el cron 1x/día post-cierre US lo evita naturalmente. **La migración V3 NO resuelve 1001** — solo el fallback manual lo hace, y por eso era crítico cerrar el gap (2).
  - **macOS local resolvers (AdGuard/NextDNS/VPN) son una clase de bug recurrente** para containers Docker que necesitan egress. Worth documentar en cualquier nuevo servicio que llame APIs externas.
  - **Tests por unidad NO atrapan drift de contrato cross-stack.** `test_job_tracker.py` usaba `step="trm_backfill"` aislado y el `_run_manual` de `api/ingest.py` (no testeado en `test_ingest.py` hasta hoy) emitía `step="trm"`. Cada test pasaba; el contrato cruzado no se verificaba. Lección para Phase 3+: cuando un payload viaja backend → wire → frontend, agregar al menos un test que invoque al productor real y assert la shape consumida por el frontend (no solo que el productor "emite algo"). En este caso bastó con `_run_manual(...)` directo + inspect del tracker — overhead mínimo, atrapa el bug.
  - **Fire-and-forget = bug invisible.** `_trm_backfill_background` venía fallando desde el primer disparo del wizard sin generar ningún signal en UI. Solo se detectó cuando el usuario reportó "no hace nada" + miramos `ingest_log` a mano. Regla práctica: si un background task NO tiene UI feedback, mínimo `logger.exception` + escribir en `ingest_log`; preferentemente integrar al `JobTracker` para que aparezca en Settings → Logs. Aplica a cualquier nuevo `BackgroundTasks` en Phase 3+.

---

## Qué es este proyecto

Web app personal de Test Owner para visualizar y operar
sobre la información de Interactive Brokers con foco en decisiones de
inversión e impuestos (subset IBKR de la declaración de renta colombiana).

Reemplaza la necesidad de abrir Excel + scripts Python + portales IB
para responder preguntas como:
- ¿Qué lotes tengo abiertos y cuántos días llevan?
- ¿Cuánto falta para que un lote cruce a Ganancia Ocasional (730d)?
- Si vendo este lote hoy, ¿cuánto impuesto pago?
- ¿Cuáles son los dividendos del año y el WHT pagado?
- ¿Qué números van en mi Form 210 cas.74/76/77/78 por IBKR?

**No reemplaza** el proyecto `renta` (sibling, `/Users/owner/Development/renta/`)
— ese sigue siendo source of truth de la declaración completa incluyendo
Globant, AFC, leasing, etc. Esta app es el **centro de control IBKR-only**.

## Estado actual

| Phase | Status | Plan | Spec | Tag al completar |
|---|---|---|---|---|
| 1. Foundation | ✅ código completo (Tasks 1-14) + polish backlog cerrado (6/6) + tag `v0.1.0-foundation` ✓ · Task 15 (deploy manual a Coolify) pendiente del usuario | `docs/plans/2026-05-24-ibkr-control-phase1-foundation.md` + `docs/plans/2026-05-24-phase1-polish-backlog.md` | spec maestro §3, §5.1 | `v0.1.0-foundation` ✓ |
| 2. Data ingestion (Flex WS + TRM Socrata + scheduler + upload XML + setup wizard) | ✅ completado · 20 tasks + polish backlog (10 items) + persistent state D5+D2 + wizard redesign (16 tasks) · 207 tests · tags `v0.2.0-ingest` + `v0.2.1-persistent-state` + `v0.2.2-wizard-redesign` ✓ | `docs/plans/2026-05-24-ibkr-control-phase2-ingestion.md` + `docs/plans/2026-05-24-phase2-polish-backlog.md` + `docs/plans/2026-05-24-d5-d2-persistent-state.md` + `docs/plans/2026-05-24-wizard-redesign.md` | `docs/specs/2026-05-24-phase2-ingestion-design.md` (D1-D17 — D6 SUPERSEDED) + `docs/specs/2026-05-24-wizard-redesign-design.md` (D1-D12) | `v0.2.2-wizard-redesign` ✓ |
| 3. Domain layer + lotes (FIFO, classification, lotes abiertos/cerrados/alertas) | ⏳ por brainstormear + planificar — ver §Roadmap Phase 3 abajo | — | spec maestro §6 (domain) + §4.2 (Lotes Abiertos/Cerrados/Alertas) + `renta/docs/flex_fifo_loader_spec.md` | `v0.3.0-lotes` |
| 4. Simulador (STK + FUT con neteo YTD) | ⏳ por planificar | — | spec maestro §4.2 (Simulador) + `renta2025.py` § A.5 régimen DUAL | `v0.4.0-simulator` |
| 5. Dividendos + Patrimonio + Form 160 + Reporte Form 210 | ⏳ por planificar | — | spec maestro §4.2 (Dividendos/Patrimonio/Form 160/Reporte) + §6.1 reglas D-E | `v0.5.0-reports` |
| 6. Polish (yfinance + composición dashboard + multi-year report) | ⏳ por planificar | — | spec maestro §2 dec.#9 + §4.2 (Dashboard composición) | `v1.0.0` |

**Spec maestro** (autoridad final sobre QUÉ se construye, todas las phases): `docs/specs/2026-05-24-ibkr-control-center-design.md`

### Cómo actualizar esta tabla
- Al iniciar una phase: cambiar status a `⚙ ejecutando · task N/M`
- Al completar una phase: cambiar status a `✅ completado · <tag>` y tagear `git tag <tag>`
- Al escribir el plan de phase N+1: status `📋 plan escrito, listo para ejecutar` + link al archivo

## Roadmap Phase 3 (preview — planificar cuando Phase 2 termine)

Phase 3 = **domain layer + 3 pantallas**: convertir los datos crudos que dejó Phase 2 en información fiscalmente útil + renderizarla en Lotes Abiertos, Cerrados, y Alertas 730d.

### Scope IN

- **Tabla derivada `lot_classifications`** + view `lot_status_v` (spec maestro §5.4) — pobladas por un recompute job que corre después de cada ingest Flex
- **Módulos domain puros** (`backend/src/ibkr_control/domain/`):
  - `fifo.py` — re-replay del lot book con override manual del trader (pool CLOSED_LOT del XML es autoritativo)
  - `trm_lookup.py` — `get_trm(date) → Decimal` con fallback vía vigencia DB
  - `regime.py` — `classify_asset(asset_class, symbol) → 'STK_ART288' | 'FUT_DEC1797'`
  - `classification.py` — `compute_lot_status(lot) → ClassificationResult` (730d rule, GO/RO, days_held, days_until_go)
  - `participation.py` — `apply_pct(amount, user_id, account_id, at_date)`
- **API endpoints**: `GET /api/lots/open`, `GET /api/lots/closed`, `GET /api/lots/alerts` (filtros, sort, paginación)
- **3 pantallas frontend**: Lotes Abiertos (tabla con semáforo 730d), Cerrados (historial GO/RO), Alertas 730d (subset urgente)
- **Recompute job**: después de cada `flex_job.run()` o `flex_job.ingest_xml()`, disparar `domain.recompute_lot_classifications(user_id, anyo)` que reemplaza `lot_classifications` de ese año

### Scope OUT (queda Phase 4+)

- Simulador (Phase 4)
- Dividendos / Patrimonio / Form 160 / Reporte (Phase 5)
- yfinance + composición dashboard (Phase 6)

### Decisiones abiertas (brainstormear antes del plan)

1. **¿Recompute es síncrono dentro del flex_job o async post-commit?** — Síncrono es más simple pero alarga el ingest (~5-30s extra); async (vía background task o re-trigger del job_tracker) deja el ingest rápido pero agrega complejidad de coordinación
2. **¿Cómo manejamos override manual de FIFO?** — el `flex_fifo_loader_spec.md` del sibling habla del pool CLOSED_LOT como autoridad. ¿Hacemos lo mismo? ¿Qué pasa si IBKR cambia retroactivamente?
3. **¿Cuándo recomputar TODOS los años vs solo el año del ingest?** — un cambio de TRM histórico afecta cost_basis_cop de lotes viejos; ¿recompute selectivo o full?
4. **Stale-while-revalidate en pantalla Lotes** — ¿mostramos clasificaciones cacheadas mientras recomputa, o esperamos?
5. **`lot_status_v` view recomputa días_hasta_730 con CURRENT_DATE** — ¿alcanza para mostrar el semáforo en real-time, o necesitamos un materialized view + refresh diario?
6. **Refactor del persister para no crear `Account` rows huérfanos por counterparties externos** — bug detectado en smoke test del wizard redesign (ver Retrospectiva §"Wizard redesign post-deploy fixes" item 4). El persister actual recolecta `account_id` de TODOS los tags del XML; cuando un `<Transfer>` IN viene desde un broker externo (e.g. `CS-999999-99` = Shareworks/Solium/MS StockPlan para GLOB RSU bonus), el ID del counterparty queda como Account row sin participation. Decisión Phase 3: crear `Account` rows SOLO para IDs en `<AccountInformation>`; los src/dst de transfers que no matchean a un `Account` propio se guardan como `counterparty_ref TEXT` separado del FK. Requiere Migration nueva (drop FK O nullable + nueva columna) + cleanup de rows huérfanos preexistentes. Es work pequeño (~3-5 LOC en persister + migración) pero del scope de Phase 3 porque toca el mismo persister que recompute_lot_classifications va a invocar.
7. **`transfer_lots` queda vacío para FOP IN — pérdida de auditabilidad del cost basis por transfer** — descubierto en sesión 2026-05-25 al revisar las 94 acciones GLOB transferidas desde Shareworks (FOP IN del 2026-04-30, vesting 2026-04-28). El cost basis comunicado por el broker fuente ($42,40/acción uniforme = $3.985,60 total) **sí se preserva** en `open_position_lots.cost_basis_usd` (para los 19 que quedaron abiertos) y en `closed_lots.cost_basis_usd` (para los 75 vendidos el 2026-05-01 — el `open_date=2026-04-28` se respeta = vesting date, no transfer_date). PERO la tabla `transfer_lots` quedó en 0 rows para ese FOP. Hipótesis a verificar: (a) IBKR no anida `<Lots>` bajo `<Transfer>` para FOP entrantes (sí lo hace para transfers INTERNAL entre cuentas IB del mismo usuario), o (b) `parser.py:_parse_transfers` (línea ~514) tiene un gap específico para FOP. Implicación: si el usuario vende TODAS las acciones de un transfer antes del corte YTD del Flex Query, el cost basis solo vive en `<ClosedLot>` (que sí captamos) — no se pierde nada para FIFO ni para Form 210, pero "cuánto cost basis trajo el transfer X" no es queryable directo. Decisión Phase 3: revisar el XML real de un FOP IN, confirmar la causa, y o bien rellenar `transfer_lots` desde el cost basis de los lots resultantes (post-hoc reconstruction) o aceptar que la tabla queda vacía para FOPs y documentar el contrato. Cross-ref con #6: ambos items tocan el parsing de `<Transfer>` — work compartible.

### Cross-references a renta (Phase 3 específicos)

- `renta/docs/flex_fifo_loader_spec.md` — **spec completo** del FIFO. Leer ANTES de empezar
- `renta/documentos/ibkr_flex/loader.py` — ~600 LOC de referencia
- `renta/documentos/ibkr_flex/_fifo_replay.py` — lógica de replay durante backfill
- `renta/tests/_invariants.py` — pinned values verificados manualmente. Phase 3 tests deben matchear: "58/58 cierres coinciden con pool CLOSED_LOT (0 diffs)" en 2025
- `renta2025.py` buscar `_clasificar_dias_held` y `Art.300` — referencia de implementación de la regla 730 días

### Entry point para próxima sesión que arme Phase 3

```
1. Verificar Phase 2 cerrada (tags v0.2.0-ingest + v0.2.1-persistent-state)
2. Invocar `superpowers:brainstorming` con prompt:
   "Phase 3 = domain layer + lotes. Decisiones abiertas listadas en
    CLAUDE.md §Roadmap Phase 3. Resolver, luego escribir spec en
    docs/specs/YYYY-MM-DD-phase3-domain-design.md"
3. Después `superpowers:writing-plans` → plan en
   docs/plans/YYYY-MM-DD-ibkr-control-phase3-lotes.md
4. Update tabla "Estado actual" arriba con link al plan
```

## Tech stack

| Capa | Tecnología |
|---|---|
| Backend | Python 3.12 + FastAPI + SQLAlchemy 2.x async + asyncpg + Alembic + fastapi-users + APScheduler + uvicorn |
| Frontend | Next.js 16 + React 19 + TypeScript + Tailwind v4 + shadcn/ui (`base-nova`, `neutral`) + TanStack Query + orval (plan dice "14" — el ecosistema avanzó; ver Notas frontend abajo) |
| DB | Postgres 16 |
| Hosting | Coolify self-hosted en Hetzner (Docker Compose) |
| Tooling | uv (Python), pnpm (Node), Playwright, GitHub Actions |
| External APIs | IBKR Flex Web Service (auto-fetch YTD diario), Socrata DIAN `ceyp-9c7c` (TRM), yfinance (precios) |

## Cuentas IBKR

| Cuenta | Descripción | % Test Owner |
|---|---|---|
| U99999001 | Conjunta Test Owner & Joint Holder | **50%** |
| U99999002 | Personal swing trading | **100%** |
| U99999003 | Personal micro-futuros | **100%** |

## Reglas fiscales IBKR (referencia rápida)

Cobertura completa en `docs/specs/2026-05-24-ibkr-control-center-design.md` §6.1.

### Aplicables

| Regla | Resumen | Cita |
|---|---|---|
| Residencia fiscal CO | Residente año completo, grava fuente extranjera | Art. 9 + 10 ET |
| Fuente extranjera | Todo IBKR califica; sustenta Art.254 | Art. 21-1 ET |
| Costo fiscal | Adquisición + comisiones capitalizadas | Art. 69 + 71 + 90 ET |
| Costo a TRM compra | `cost_cop = cost_usd × TRM(open_date)` | Art. 288 ET |
| Proceeds a TRM venta | `proceeds_cop = proceeds_usd × TRM(close_date)` | Art. 288 ET |
| 730 días → GO 15% | <730 = Renta Ordinaria, ≥730 = Ganancia Ocasional 15% | Art. 300 + 313 ET |
| Tarifa RO marginal | Configurable por usuario (default 0.39, top bracket Art.241) | Art. 241 ET |
| FUT/OPT netting per-contrato | Régimen DUAL: agrupar por `(cuenta, símbolo)`, netear, positivo→cas.74, negativo→cas.77 (postura permisiva) | Decreto 1797/2008 Art. 8 + DUR 1625/2016 Art. 1.2.4.2.74 |
| WHT US dividendos | Descuento Art.254 = WHT × pct_participación | Art. 254 ET |
| Return of Capital | NO es ingreso fiscal; reduce cost basis (V2) | Art. 46-1 ET |
| Patrimonio bruto 31-dic | `Σ mark_value_usd × TRM(31-dic) × pct` | Art. 261-263 ET |
| Form 160 (activos exterior) | Obligado si > 2000 UVT al 1-ene | Art. 607 ET |
| Medios Magnéticos | Brutos cas.74 IB contribuyen al umbral 11800 UVT | Res. 162/2023 + 227/2025 |

### NO aplicables (no confundir con reglas del sibling `renta`)

- **Art. 36-1, 153** — solo BVC, IBKR offshore no califica
- **Art. 242, 254-1** — dividendos sociedades nacionales (IBKR son extranjeros)
- **Art. 38-40** — componente inflacionario solo títulos COP
- **Art. 115** — GMF, no aplica a operaciones US
- **Conceptos DIAN 008706/2025, 003517/2025** — MGC BVC, no IBKR
- **Art. 408** — IBKR no es agente retenedor colombiano

## Notas frontend (drift del plan vs realidad post-Task 7)

El plan fue escrito asumiendo Next 14 / Tailwind v3 / shadcn Slate. `pnpm create next-app@latest` en May 2026 instala Next 16 + React 19, y `pnpm dlx shadcn@latest init -d` configura `base-nova` con `neutral`. Cambios prácticos para Tasks 8-12:

- **No existe `tailwind.config.ts`**. Tailwind v4 se configura via CSS: `@import "tailwindcss"` en `globals.css`. Ya está hecho.
- **`next.config.ts`** (no `.mjs`). Mismo `output: "standalone"`.
- **shadcn `form` no existe en `base-nova`** — usar `field` en su lugar para login/register/settings. La API es distinta: en vez de `<Form>{ <FormField name="x" render={...} />}</Form>` se usa `<Field>` primitive con `register()` de react-hook-form. Ver `frontend/src/components/ui/field.tsx`. Cuando un task del plan diga "shadcn add form", reemplazar mental por "field".
- **Node 22** en Dockerfile (no 20) — pnpm@latest requiere `node:sqlite` builtin.
- **`pnpm-workspace.yaml` committeado** con `onlyBuiltDependencies` (sharp, esbuild, msw). pnpm 10 requiere ese archivo para `--frozen-lockfile` cuando hay build scripts approved.
- **Backend container tiene healthcheck** (Task 3 polish): `curl http://localhost:8000/health`. Frontend depende de backend pero NO espera health (no necesita — es estática).

## Decisiones arquitectónicas (locked, ver spec §2)

1. Repo nuevo independiente del `renta`, re-ingiere XML por su cuenta
2. Hosting: Coolify self-hosted en Hetzner (Docker)
3. Stack backend: Python 3.12 + FastAPI + SQLAlchemy async + Alembic + APScheduler
4. Stack frontend: Next.js 14 + TypeScript + TanStack Query + Tremor/shadcn
5. DB: Postgres 16
6. Auth: `fastapi-users` con JWT (single-user V1, schema multi-user-ready)
7. Ingest IBKR: Flex Web Service auto-fetch YTD diario + upload manual XMLs históricos
8. Ingest TRM: Socrata DIAN dataset `ceyp-9c7c`, automático, sin uploads
9. Precios actuales: yfinance con cache TTL 15min
10. Docs IBKR V1: solo Flex XML (sin CSV dividends ni 1042-S)
11. Tarifa marginal RO: configurable por usuario, default 0.39
12. Arquitectura: Monolito modular (API + scheduler + ingest en un proceso)

## 10 pantallas V1 (ver spec §4)

1. 📊 Dashboard — resumen + composición + banner Form 160
2. 📦 Lotes abiertos — tabla central con semáforo 730d
3. ✓ Cerrados — historial GO/RO
4. ⚠ Alertas 730d — subset filtrado urgente
5. 🧮 Simulador — modo STK y modo FUT
6. 💰 Dividendos — filtrados ROC vs dividend real
7. 🏦 Patrimonio — por cuenta + por símbolo + histórico
8. 🌍 Form 160 — status + listado activos exterior
9. 📋 Reporte Form 210 — single + multi-year
10. ⚙ Settings — config + import histórico

## Convenciones de código

- **TDD**: failing test → minimal impl → passing test → commit
- **Frequent commits**: cada step del plan termina en commit
- **DRY, YAGNI**: no over-engineering
- **No emojis en código** salvo iconografía UI explícita
- **Spanish para identifiers de UI/UX**, English para identifiers técnicos
- **Decimal para dinero**, nunca float
- **Postgres-specific** (JSONB, ON CONFLICT, ranges) — no abstraer a SQLite
- **Dedup de imports por SHA-256** (mismo patrón que `renta/documentos/_hash.py`)
- **No capturar `settings = get_settings()` a nivel módulo** — llamar `get_settings()` dentro de funciones/métodos. El patrón module-level cachea valores en cada módulo independientemente; cuando los tests hacen `monkeypatch.setenv` + `get_settings.cache_clear()`, los módulos ya cargados siguen viendo los settings viejos. (Code review de Task 5 detectó que los tests actuales pasan por coincidencia — los valores de conftest y fixture son idénticos.) Aplicar a código nuevo desde Task 6+; refactor de los 3 archivos existentes (`db/session.py`, `auth/manager.py`, `auth/backend.py`) está en el polish backlog.

## Polish backlog (cerrado, ver `docs/plans/2026-05-24-phase1-polish-backlog.md`)

Los 6 items detectados durante Phase 1 fueron resueltos en el plan de polish del 2026-05-24:

- ✓ CORS wildcard guard (config-level via `field_validator`, falla al boot — `backend/src/ibkr_control/config.py`)
- ✓ `test_migrations_apply_cleanly_and_match_metadata` (corre `alembic upgrade head` contra container fresh, verifica drift contra `Base.metadata` — `backend/tests/test_migrations.py`)
- ✓ Refactor module-level `settings = get_settings()` (lazy factories `@lru_cache` en `db/session.py`, `@property` en `auth/manager.py`, inline en `auth/backend.py`)
- ✓ Dockerfile `USER appuser` (UID 1001, /home/appuser) + remove `ports: 5432` de docker-compose.yml
- ✓ `UserSettingsUpdate.timezone` validado contra `zoneinfo.available_timezones()` (devuelve 422 si TZ desconocido)
- ✓ `UserSettingsUpdate.marginal_rate` con `max_digits=5, decimal_places=4` (devuelve 422 en vez de silent rounding a Numeric(5,4))

## Comandos comunes

```bash
# Dev local
docker compose up -d --build

# Backend tests
cd backend && uv run pytest -v

# Frontend dev
cd frontend && pnpm dev

# Regenerar cliente TS del OpenAPI (cuando cambia el backend)
cd frontend && pnpm openapi:gen

# E2E
cd frontend && pnpm e2e

# Migration nueva
cd backend && uv run alembic revision --autogenerate -m "descripción"
cd backend && uv run alembic upgrade head
```

## Sibling project: `renta`

`/Users/owner/Development/renta/` — generador del Form 210 completo
de Test Owner. Sigue siendo el source of truth para:
- Cédula trabajo (Globant Form 220, AFC, leasing, SURA)
- Tope 1340 UVT Art.336
- Anticipo Art.807
- Comparación patrimonial Art.236-239
- Patrimonio fuera de IBKR (apto, vehículo, etc.)

El proyecto `ibkr-control` produce el subset IBKR del Form 210; el output JSON
(via "Exportar JSON" en la pantalla Reporte) se puede consumir desde `renta`
para complementar la declaración completa.

### Cómo consultar renta desde sesiones en ibkr-control

**Lee `docs/references/renta-cross-references.md`** — ese doc lista archivos
exactos en `/Users/owner/Development/renta/` que vale leer para cada
área de implementación (reglas fiscales, FIFO, ingest Flex, TRM, validación
de paridad). Tiene mapping regla → archivo de implementación referencia.

**Patrón:**
- `Read /Users/owner/Development/renta/<path>` cuando necesités contexto
- Reimplementar la lógica en ibkr-control desde cero (decisión locked #1)
- **NO** importar código de renta, **NO** copiar archivos enteros
- **NO** modificar archivos de renta desde sesiones en ibkr-control (abrir sesión separada en renta si hay que cambiar algo allá)

**Cuándo SÍ consultar renta:**
- Implementando Phase 2 (ingest Flex/TRM) → `renta/documentos/ibkr_flex/`
- Implementando Phase 3 (FIFO + clasificación) → `renta/docs/flex_fifo_loader_spec.md`
- Implementando Phase 4 (simulador FUT con neteo) → `renta2025.py` § A.5 régimen DUAL
- Implementando Phase 5 (Reporte Form 210) → validar paridad numérica con `renta/tests/_invariants.py`
- Cualquier momento que aparezca duda fiscal → `renta/docs/tax_rules_co.md` + `renta/CLAUDE.md`

**Cuándo NO:**
- Si la sección de renta no menciona "IB", "IBKR", "Art.288" o "Decreto 1797",
  probablemente no aplica a este proyecto. Ignorar.
