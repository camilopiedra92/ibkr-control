# CLAUDE.md — IBKR Control Center

## ⏯ Cómo continuar (próxima sesión)

**Phase 2.x + Phase 2.7 (persister cleanup) + lint/format cleanup TODOS completos y mergeados a `main` (HEAD `e363d3f`). Tags `v0.2.0`→`v0.2.5`. Próximo: Phase 3.**

> Verificado contra git/tests el 2026-06-02 (cierre de sesión): `main` en `e363d3f`, working tree limpio. `cd backend && uv run pytest -q` → **299 passed**. `ruff check .` → clean, `ruff format --check .` → 0 drift. `cd frontend && pnpm lint` → 0 errores, `pnpm build` → exit 0, vitest 7 passed. El pre-Phase-3 checklist de abajo quedó todo en verde.

> ⚠️ **PENDIENTE DE PUSH (acción del usuario):** `main` está **19 commits ahead de `origin/main`** y el tag `v0.2.5-persister-cleanup` es **local** (sin push). Todo está commiteado local (nada se pierde), pero para respaldar en remote: `git push origin main && git push origin v0.2.5-persister-cleanup`. Tags `v0.2.0`→`v0.2.4` ya están en `origin`.

Todas las branches de Phase 2.x ya mergeadas a `main`: `phase2/ingestion` + `feat/wizard-redesign` + `phase25/flex-persister-idempotent` + `phase26/flex-hardening` (esta última via PR #2, merge `0e576f3`). Smoke tests en dev completados (wizard 2026-05-24; persister idempotente 2026-05-25).

Tag `v0.2.0-ingest` apunta al cierre original de polish (`a606be2`). Tag `v0.2.1-persistent-state` apunta a `172cc12` — incluye SQLAlchemyJobStore + DB-backed rate limit. Tag `v0.2.2-wizard-redesign` apunta al cierre del rewrite del wizard (detect-first, F-filter en persister, Migration H wipea legacy). Tag `v0.2.3-persister-idempotent` apunta al fix del bug del 2026-05-25 (D13 [BUG-FIXED]): rewrite del Flex persister a UPSERT por natural key — cron + manual refresh ahora idempotentes fila por fila + smoke test end-to-end validado (segundo Flex refresh real-conditions devuelve `items_processed=0` sin failures). Tag `v0.2.4-flex-hardening` apunta a `0e576f3` (merge de Phase 2.6). Tag `v0.2.5-persister-cleanup` apunta al cierre de Phase 2.7 (counterparties + exclusive arc; drop transfer_lots — ver Retrospectiva §"Phase 2.7"). Post-Phase-2.7 se agregó lint/format cleanup en la misma sesión: ruff (backend, 41 errores → 0 + `ruff format` en toda la base) + ESLint flat config nuevo en frontend (no existía linter — ver §"Phase 2.7" lecciones). **299/0 backend tests pasan, frontend lint/build/vitest clean.**

Fixes post-deploy del wizard ya en `main` + pusheados: `8bd578f` infra DNS fix para container backend (resolver local AdGuard/NextDNS SERVFAILa `gdcdyn.interactivebrokers.com` → pinned a `1.1.1.1`/`8.8.8.8`), `24aa5f9` fix gap del fallback "subir XML manual" (ahora persiste igual que `step2/detect` para que `step2/save` valide contra `accounts`), `4eb4f80` migración a endpoints V3 oficiales (`ndcdyn` + `/AccountManagement/FlexWebService/`) + User-Agent header requerido. Ver §"Wizard redesign post-deploy" abajo para detalle completo.

### Camino A — Planificar Phase 3 (recomendado)

Phase 3 = domain layer + 3 pantallas (Lotes Abiertos/Cerrados/Alertas 730d). Spec maestro §6 + §4.2. Phase 3 NO requiere prod deploy ni datos reales — desarrolla 100% contra testcontainer + fixtures sanitizadas.

```
1. Verificar Phase 2.x cerrada (ya confirmado 2026-06-02):
   git tag -l "v0.2*" → debe mostrar v0.2.0-ingest + v0.2.1-persistent-state +
                       v0.2.2-wizard-redesign + v0.2.3-persister-idempotent +
                       v0.2.4-flex-hardening
2. Leer este CLAUDE.md (lo cargás automáticamente)
3. Leer docs/plans/2026-05-24-phase2-polish-backlog.md (entender deuda conocida D1-D13
   + apendice "Post-Phase-2: Wizard redesign"; D13 = persister idempotente RESOLVED)
4. Invocar `superpowers:brainstorming` con prompt:
   "Phase 3 = domain layer + lotes. Decisiones abiertas listadas en
    CLAUDE.md §Roadmap Phase 3. Resolver una a una, luego escribir spec
    en docs/specs/YYYY-MM-DD-phase3-domain-design.md"
5. Después `superpowers:writing-plans` → docs/plans/YYYY-MM-DD-ibkr-control-phase3-lotes.md
6. Update tabla "Estado actual" con el link al plan nuevo
7. Ejecución: superpowers:subagent-driven-development (mismo método que Phase 2)
8. Branch: git checkout -b phase3/lotes main
```

### Camino B — Deploy a Coolify (tarea del usuario)

Smoke test en dev + push a remote YA están hechos (2026-05-24/25, ver §"Wizard redesign post-deploy"). Repo privado en `https://github.com/owner/ibkr-control`. Falta solo deploy prod:

```
1. Configurar TOKEN_ENCRYPTION_KEY en Coolify (1 min):
   openssl rand -base64 32  # generar key
   # Pegarla en Coolify env vars del backend container
   # CRÍTICO: sin esta key, decrypt_token() crashea al primer fetch del cron

2. Confirmar DNS en Coolify (1 min):
   # El compose.yml local ahora pinea `dns: [1.1.1.1, 8.8.8.8]` en backend.
   # Coolify normalmente usa DNS público por default — verificar que no
   # haya un override de network que herede DNS del host server. Si lo hay,
   # replicar el pin en la config de Coolify.

3. Trigger redeploy desde Coolify UI apuntando a main (~3 min build):
   # Verificar logs: "Registered 3 ingest jobs: flex_daily, trm_daily, cleanup_job_tracker"
   # Migration H wipea data legacy — preserva flex_credentials + apscheduler_jobs
   # Si las migrations no se aplican automáticamente:
   docker exec <backend-container> uv run alembic upgrade head

4. Smoke test end-to-end del wizard en prod (~20-30 min):
   # Mismo flow validado en dev: Step 1 creds → Step 2 detect (online o
   # XML fallback) → Step 2 configure → Step 3 históricos → finish.
   # Si IBKR responde 1001 BUSY (puede pasar — es 1x/día por design),
   # usar "Subir XML manualmente" — funciona end-to-end.
```

### Pre-Phase-3 checklist (TODO verificado verde el 2026-06-02)

- [x] `git status` limpio en `main` (HEAD `e363d3f`; ⚠️ 19 commits ahead de `origin/main` — push pendiente, ver arriba)
- [x] `git tag -l "v0.2*"` muestra `v0.2.0`→`v0.2.5` (6 tags). `v0.2.0`→`v0.2.4` en remote; `v0.2.5-persister-cleanup` local sin push
- [x] `cd backend && uv run pytest -q` → 299 passed (era 290 al cierre de Phase 2.6; +9 por Phase 2.7 — counterparties model + migración + persister + integración)
- [x] `cd backend && uv run ruff check .` → clean · `ruff format --check .` → 0 drift (lint/format adoptados en toda la base esta sesión)
- [x] `cd frontend && pnpm lint` → 0 errores (ESLint flat config nuevo) · `pnpm build` → exit 0 · vitest 7 passed
- [ ] Leer `docs/plans/2026-05-24-phase2-polish-backlog.md` § "Deuda conocida" (D1-D13; D13 [BUG-FIXED] documenta el incidente del persister + lecciones)
- [ ] (Opcional) `docker compose ps` para confirmar postgres + backend healthy si vas a smoke test

**Nada bloquea el desarrollo de Phase 3.** Lo único pendiente es del usuario y NO bloquea dev local: deploy a Coolify + `TOKEN_ENCRYPTION_KEY` + verificar DNS + smoke test en prod (Camino B). El siguiente paso para avanzar es Camino A (brainstorming → spec → plan).

### Convenciones (heredadas)

- **La sección "⏯ Cómo continuar" puede quedar stale** — se redacta durante la sesión anterior, a veces ANTES del merge/tag/push final. Verificá el estado real contra git/tests antes de confiar en ella: `git status`, `git log --oneline -5`, `git ls-remote --tags origin` (no solo `git tag -l`, que es local), `uv run pytest -q`, `pnpm build`. Caso 2026-06-02: el doc decía "phase26 local, pending merge" cuando ya estaba mergeada (PR #2), tageada y pusheada.
- TDD: failing test → minimal impl → passing test → commit
- Frequent commits: cada step del plan termina en commit
- **No commitear shortcuts sin canonicalizar** — atajos en exploración/debugging OK; antes de `git commit` reemplazar por el flujo canónico documentado **O** pedir aprobación explícita al usuario surface-eando el trade-off (ver §Convenciones de código → "Shortcuts y flujos canónicos")
- No emojis en código (Unicode arrows ✓ ⚠ ✗ → como content UI sí)
- `Decimal` para dinero, nunca float
- Postgres-specific allowed (JSONB, ON CONFLICT, advisory locks)
- No capturar `settings = get_settings()` a nivel módulo — llamar dentro de funciones
- English identifiers en código, Spanish OK en UI strings + docstrings
- Sibling `renta` para referencia fiscal: leer `docs/references/renta-cross-references.md` antes de implementar reglas fiscales. **Reimplementar, NO importar**.

### Open items (pendientes — no bloquean Phase 3)

| Item | Quién | Bloquea? |
|---|---|---|
| **Push main + tags a remote** ✅ completado — repo privado `owner/ibkr-control`, `main` up-to-date con `origin/main` + los **5 tags `v0.2*` pusheados** (verificado 2026-06-02: `git ls-remote --tags origin` lista los 5, SHAs locales == remotos). v0.2.3 y v0.2.4 ya en remote | — | — |
| **Deploy a Coolify + setear `TOKEN_ENCRYPTION_KEY` + verificar DNS público** | Usuario | No bloquea Phase 3 dev local |
| **Smoke test end-to-end en dev — Phase 2 wizard original** ✅ completado 2026-05-24 | — | — |
| **Smoke test end-to-end en dev — Phase 2.5 persister idempotente** ✅ completado 2026-05-25: 3 imports cargados, 154 closed_lots / 302 trades / 327 open_lots persistidos correctamente, segundo refresh `items_processed=0` confirma D13 fix | — | — |
| **Smoke test end-to-end en prod** | Usuario | No bloquea Phase 3 (dev validó el flow) |
| **Counterparty account `CS-######-##` queda como orphan account** ✅ RESUELTO en Phase 2.7 (`v0.2.5-persister-cleanup` pending merge) — `CS-999999-99` migrado a la nueva tabla `counterparties` vía migration `1702589703e1`; el invariante "accounts = solo cuentas propias" quedó restaurado (dev DB: `accounts CS-* = 0`) | — | — |
| **11 items de deuda conocida D1-D11** documentados en polish backlog (D12 RESOLVED en `5dba1b4` 2026-05-25 — wizard TRM ahora con SSE feedback; D13 [BUG-FIXED] RESOLVED en tag `v0.2.3-persister-idempotent` — persister rewrite) | Aceptados como V1 | No bloquean — la mayoría tienen mitigation o son features (fail-loud audit, etc.) |

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
- **D2 + D5 + D12 + D13 RESOLVED**: D2/D5 persistent state migration (commits 70f9bd0 + 45127f1); D12 wizard TRM fire-and-forget resuelto en `5dba1b4` — `step2/save` ahora registra job en `JobTracker` y devuelve `trm_backfill_job_id`, frontend renderiza `TrmBackfillBanner` con SSE state running/ok/failed encima del stepper. **D13 [BUG-FIXED]** = persister Flex no idempotente — resuelto en tag `v0.2.3-persister-idempotent` (Phase 2.5): rewrite a Core UPSERT por natural key per-entity, 6 Alembic revisions + 2 scripts manuales (wipe + backfill_closed_lots), decisiones A0-A8 + 3 amendments lockeadas en `docs/specs/2026-05-25-flex-persister-idempotent-design.md`

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
  - **(1) DNS infra (`8bd578f`):** backend container heredaba el resolver del host (Docker Desktop → `/etc/resolv.conf` → `127.0.2.2/3`). En macOS con AdGuard/NextDNS/VPN/Little Snitch activo, `gdcdyn.interactivebrokers.com` daba `SERVFAIL`. Fix: pin `dns: [1.1.1.1, 8.8.8.8]` al servicio backend en `compose.yaml`. En Coolify hay que verificar que la red del compose use DNS público también.
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

- **Persister Flex no idempotente — bug crítico detectado post-deploy + rewrite Phase 2.5 (2026-05-25, tag `v0.2.3-persister-idempotent`)** — smoke test del manual refresh el 2026-05-25 reveló que cron Flex + manual refresh fallaban al 2do run con `UniqueViolationError trades_transaction_id_key`. Phase 2 dedupea solo a nivel xml_hash pero los XMLs YTD cambian byte-a-byte cada día (mark prices, timestamp del reporte, eventos nuevos) → cada hash es nuevo → re-INSERT de todos los trades del año → choque con UNIQUE global. El cron diario también estaba roto, solo no se detectó porque el único smoke E2E de Phase 2 cubría el primer run (DB vacía). Fix Phase 2.5: rewrite del persister a UPSERT por natural key per-entity con helpers Core (`pg_insert + on_conflict_*`), semántica diferenciada immutable (DO NOTHING, first-seen) vs snapshot (DO UPDATE, last-updated-by). Schema migration en 6 Alembic revisions + 2 scripts manuales (`scripts/wipe_flex_data.py` para mitigar TRUNCATE accidental, `scripts/backfill_closed_lots.py` que replay xml_bytes via A0 cuando se aplicó A3 #3 sin pedir re-upload al usuario). Decisiones A0-A8 lockeadas + **3 amendments descubiertas durante implementación**: **A3 #1** agregó `originating_transaction_id` a `open_position_lots` (multi-fill orders con mismo `(account, symbol, open_date)`); **A3 #2** agregó `(report_date, action_id, code)` a accruals + promovió `code` de raw_attrs a column (Po/Re lifecycle events); **A3 #3** agregó `(close_datetime, fifo_pnl_usd)` a closed_lots (multi-execution closes del mismo open_lot con mismo transactionID compartido — caso ICSH 2025 verificado: 7 close events colapsados a 1, recuperamos $8.75 de realized PnL; caso IBIT 2024 verificado: 2 rows con mismo key + distinto pnl por wash-sale adjustment). Las 3 amendments se descubrieron solo ejecutando tests integration y smoke real contra el XML real del fixture 2025 — el spec original quedaba lossy contra real data en 3 tablas distintas. Agregamos `xml_bytes BYTEA` a flex_imports para replay capability futura (A0) — usado exitosamente para el backfill de A3 #3 sin re-upload. Plan: `docs/plans/2026-05-25-flex-persister-rewrite.md` (15 tasks + extension). Spec: `docs/specs/2026-05-25-flex-persister-idempotent-design.md` (decisiones + 3 amendments inline). Tests: 215 → 245 (+30: helpers + integration + migration + regression + 3 amendments). D13 [BUG-FIXED] documenta el incidente + lecciones en polish backlog. **Smoke test end-to-end validado 2026-05-25 con datos reales IBKR**: 3 imports cargados (2024 sealed + 2025 sealed + 2026 YTD Flex WS), 154 closed_lots + 302 trades + 327 open_lots persistidos, n_observed=n_new en todos (excepto transfers por behavior IBKR-mirror documentado); segundo refresh manual `items_processed=0` (hash dedup fast-path A4) sin failures — idempotencia probada end-to-end.

- **Lecciones del incidente del persister** (Phase 2.5, additional al smoke test post-merge):
  - **Natural keys validar contra data REAL antes de lockear como UNIQUE.** Spec A3 original tenía 2 colisiones contra el fixture 2025 (open_position_lots multi-fill + accruals Po/Re). Solo se detectaron al ejecutar tests integration contra el XML real. Lección para Phase 3+ brainstorming: verificar fixture data antes de cerrar natural keys, especialmente snapshot/mutable tables donde el discriminador no es obvio del schema.
  - **Smoke E2E debe disparar jobs ≥2 veces consecutivas.** El smoke de Phase 2 corrió Flex 1 vez sola (DB vacía, sin conflictos). Bug invisible. Aplicar a cualquier background task Phase 3+ que toque DB con UNIQUE constraints.
  - **UNIQUE constraint sin UPSERT es trampa garantizada.** Pareo obligatorio: cada UNIQUE nueva en el schema requiere decisión explícita de qué hacer ON CONFLICT (DO NOTHING / DO UPDATE), y un test que dispare el caso de re-insert.
  - **Hash dedup a nivel "documento entero" es engañoso para fuentes mutables.** Para YTD/rolling data, dedupear a nivel fila (natural key) es la única respuesta correcta. Hash queda como fast-path optimization (skip parsing), no como contract de idempotencia.
  - **Append-only ledger pattern es el default world-class para hechos immutables.** SET NULL en FK preserva data ante deletes accidentales del parent; CASCADE viola la semántica de first-seen porque puede matar hechos sanos que también aparecen en imports posteriores.

- **Phase 2.6 — Flex Ingest Hardening (2026-05-25, branch `phase26/flex-hardening` mergeada via PR #2 `0e576f3`, tag `v0.2.4-flex-hardening` creado + pusheado)** — cierra los 6 riesgos identificados en el architecture review post Phase 2.5: **R1** latest-1 retention para `flex_imports.xml_bytes` (cleanup inline en `persist()`, sealed pinned, poison preserved as forensic); **R2** dead-letter via `status` + `poison_reason` columns + INSERT-outside-SAVEPOINT capture en `job._insert_poison_row()` + `scripts/poison_reset.py` recovery (deletes only `status='poison'`, never `'ok'`); **R3** replay test suite via `testcontainers-postgres` (idempotency × 2 fixtures + counts + FIFO parity via raw XML `<Lot levelOfDetail="CLOSED_LOT">` sum + cross-schema migration replay using Core INSERT under phase25 schema); **R4** `GET /api/health/ingest` endpoint + Dashboard `IngestHealthBanner` (3 severity states: ok/warning/error based on 24h/48h/consec_failures≥2) + Settings "Salud de ingesta" section con sparkline custom SVG (sin recharts dep — 50 LOC inline); **R5** `RetryPolicy` class + `execute_with_retry` HTTP-agnostic (validates `max_attempts >= 1` at construction) + `send_request` retries 1001/5xx/NetworkError + `poll_statement` refactor (split `_poll_once`/`poll_statement`, late-binds `asyncio.sleep` for monkeypatch propagation, `FlexPollTimeoutError.waited` now reflects actual `time.monotonic()` elapsed) + per-retry logging via `on_retry` callback; **R6** Migration atómica `UNIQUE(user_id, xml_hash)` (drop global, fix latent multi-user collision bug) + `check_hash_status(session, user_id, hash) -> Literal['absent','ok','poison']` + per-user fast-path en `job.run()` y `ingest_xml()` con logging diferenciado info/warning. Migration única `2b0b2863c6e9` (downgrade reversible). 22 tasks TDD, 28 commits, 290 backend tests (245 → +45 net), 7 vitest unit tests, 3 Playwright E2E specs. Coverage 86% (gap en `scheduler/jobs.py` 30% pre-existing — D11 acceptable per Phase 2 polish backlog). Spec: `docs/specs/2026-05-25-phase26-flex-hardening-design.md`. Plan: `docs/plans/2026-05-25-phase26-flex-hardening.md`.

- **Lecciones de Phase 2.6** (aprendizajes que valen para sesiones futuras):
  - **Plan-vs-reality drift en schema migrations:** el spec asumía que `flex_imports.status` no existía cuando ya existía con `CHECK IN ('ok','failed')`. Antes de escribir una migration, hacer `\d <tabla>` contra la DB real, no solo leer el modelo. La adaptación fue `alter_column` con `existing_type` en lugar de `add_column`, dejando los datos intactos.
  - **`_sleep=asyncio.sleep` default param binding es late-vs-early:** captures at function-def time. `monkeypatch.setattr(asyncio, 'sleep', fake)` no propaga al default param. Solución: late-binding (`_sleep=None` → `sleep_fn = _sleep or asyncio.sleep` adentro de la fn). Lección aplicable a cualquier inyección de seam vía default kwarg en utilities reusables.
  - **`from None` vs `from exc` en exception chaining:** `from None` swallows context, hace debugging miserable en prod. Siempre `from exc` cuando estás convirtiendo una exception en otra del mismo grupo lógico (ej. `FlexStatementPendingError` → `FlexPollTimeoutError`).
  - **`accounts.ibkr_account_id` es UNIQUE global, no per-user:** asyncio.gather de 2 persist() simultáneos para diferentes users con el mismo XML pega contra esa UNIQUE — el "isolation parallel" test corrió secuencial. Documented para Phase 3+: cuando multi-user real prod aparezca, `accounts` necesita partitioning o un UNIQUE per-user.
  - **Orval a veces genera GETs como `useMutation`** (mismo quirk que CLAUDE.md ya documentaba para POSTs como `useQuery`): el workaround es usar `useQuery` de TanStack directamente con la función fetch generada por Orval (no el hook auto-generado). Vale para `useGetIngestHealthApiHealthIngestGet` y `listLogsApiIngestLogsGet`.
  - **POSIX-portable shell scripts en containers Alpine:** el script `fetch-openapi.sh` usaba `BASH_SOURCE[0]` + `set -euo pipefail` + `curl`, todas bash-isms o tools no instalados en `node:22-alpine`. Fix: `#!/bin/sh` + `set -eu` + `$0` + `wget -qO`. Aplica a cualquier shell helper que tenga que correr dentro del container frontend.
  - **Coverage targets vs reality:** el plan target era ≥88% pero terminamos en 86%. Las nuevas líneas son 92-100% cubiertas; el drag-down es `scheduler/jobs.py` 30% (pre-existing, D11). Lección: targets de coverage deben respetar el baseline documentado, no aspiracional.
  - **Recharts overhead vs custom SVG:** spec sugería recharts para sparkline de 30 bars × 18px. Implementación: 50 LOC inline SVG, cero deps nuevas, render time despreciable. Lección de "world-class V1.5-clean": una librería de gráficos completa para un mini-spark es scope creep injustificado.

- **Phase 2.7 — Persister cleanup (2026-06-02, branch `fix/persister-counterparties`, tag `v0.2.5-persister-cleanup` pending merge)** — cleanup pre-Phase-3 que cerró los items #6 + #7 del Roadmap Phase 3 bajo criterio "world-class, sin deuda/workarounds/legacy". **#6:** tabla `counterparties` (`id`, `external_id` UNIQUE, `source_label`, `created_at` — espeja `accounts`, sin user_id) + **exclusive arc** en `transfers` (2 CHECK: `(src_account_id IS NOT NULL) <> (src_counterparty_id IS NOT NULL)`, idem dst). El persister dejó de meter peers de `<Transfer>` en `all_account_ids` (la fuente del bug — los peers externos creaban `Account` huérfanos); ahora `_resolve_side` rutea cada lado a account propio (si está en `accounts_map`) o counterparty externo (vía `_ensure_counterparties`). Restaura el invariante "`accounts` = solo cuentas propias". **#7:** drop de `transfer_lots`. La decisión `counterparty_ref TEXT` del CLAUDE.md viejo fue **rechazada** (blob sin tipo, dos formas de expresar el peer); el FK ya era nullable (no hubo "drop FK"). Migration única atómica `1702589703e1` (`down_revision 2b0b2863c6e9`): crea counterparties + columnas + reconcilia huérfanos (mueve `CS-999999-99`, re-apunta su transfer, borra el Account) + assert fail-loud del arc + crea los CHECK + dropea transfer_lots; downgrade reversible. Ejecutado vía **subagent-driven-development** (6 tasks, implementer + spec-review + code-quality-review por task). Tests: 290 → **299**. Specs: `docs/specs/2026-06-02-persister-counterparties-cleanup-design.md` (C1-C6). Plan: `docs/plans/2026-06-02-persister-counterparties-cleanup.md`.

- **Lecciones de Phase 2.7** (valen para sesiones futuras):
  - **El `<TransferLot>` de Activity Flex es impoblable** — sibling no-anidado + sin cost_basis/open_date. Documentado en memoria [[transferlot-unpopulatable-activity-flex]]. Si Phase 3 necesita "cost basis por transfer", reconstruir por join `transfers → lots resultantes`, NO re-agregar la tabla.
  - **Investigar contra data REAL antes de decidir el fix** — el plan original asumía "#7 = gap de parser, arreglar para poblar la tabla". Extraer el XML 2026 real de `flex_imports.xml_bytes` reveló que la tabla era impoblable (la fuente carece de los datos), cambiando la decisión de "fix parser" a "drop table". Replica la lección Phase 2.5 "validar contra data real".
  - **Constraint names en migrations deben matchear el auto-naming de Postgres** cuando los modelos usan bare `ForeignKey`/`unique=True` sin naming convention — si no, `alembic revision --autogenerate` emite renames espurios (drift latente que el drift test de solo-tablas no atrapa). FKs → `{table}_{col}_fkey`, unique de columna → `{table}_{col}_key`. Verificar con autogenerate-diff-vacío.
  - **Tests con `testcontainers` corren desde el HOST**, no dentro del container backend (no tiene Docker socket). `cd backend && uv run pytest`.
  - **Subagents pueden crear branches divergentes** — un implementer reportó commitear "en un branch nuevo off main" (en realidad branchó del HEAD actual). Instruir explícitamente "stay on branch X, no crees branches" en cada dispatch; verificar `git branch --show-current` post-commit y reconciliar con `merge --ff-only` si hace falta.

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
| 2. Data ingestion (Flex WS + TRM Socrata + scheduler + upload XML + setup wizard + persister idempotente) | ✅ completado + smoke test real validado · 20 tasks + polish backlog (10 items) + persistent state D5+D2 + wizard redesign (16 tasks) + Phase 2.5 persister rewrite (15 tasks + A3 amendments #1/#2/#3, D13 [BUG-FIXED]) · **245 tests** · tags `v0.2.0-ingest` + `v0.2.1-persistent-state` + `v0.2.2-wizard-redesign` + `v0.2.3-persister-idempotent` ✓ | `docs/plans/2026-05-24-ibkr-control-phase2-ingestion.md` + `docs/plans/2026-05-24-phase2-polish-backlog.md` + `docs/plans/2026-05-24-d5-d2-persistent-state.md` + `docs/plans/2026-05-24-wizard-redesign.md` + `docs/plans/2026-05-25-flex-persister-rewrite.md` | `docs/specs/2026-05-24-phase2-ingestion-design.md` (D1-D17 — D6 SUPERSEDED) + `docs/specs/2026-05-24-wizard-redesign-design.md` (D1-D12) + `docs/specs/2026-05-25-flex-persister-idempotent-design.md` (A0-A8 + 3 amendments) | `v0.2.3-persister-idempotent` ✓ |
| 2.6. Flex hardening (RetryPolicy + poison-pill + retention + replay tests + health UI + multi-user isolation) | ✅ completado + mergeado (PR #2 `0e576f3`) + tag pusheado · 22 tasks · **290 tests** (245 → +45 net) · cierra R1-R6 del architecture review · tag `v0.2.4-flex-hardening` ✓ | `docs/plans/2026-05-25-phase26-flex-hardening.md` | `docs/specs/2026-05-25-phase26-flex-hardening-design.md` (R1-R6 + 12 locked decisions) | `v0.2.4-flex-hardening` ✓ |
| 2.7. Persister cleanup (counterparties + exclusive arc; drop transfer_lots) | ✅ completado · branch `fix/persister-counterparties` (6 tasks subagent-driven) · **299 tests** (290 → +9) · cierra Roadmap Phase 3 #6 + #7 · tag `v0.2.5-persister-cleanup` pending merge | `docs/plans/2026-06-02-persister-counterparties-cleanup.md` | `docs/specs/2026-06-02-persister-counterparties-cleanup-design.md` (C1-C6) | `v0.2.5-persister-cleanup` (post-merge) |
| 3. Domain layer + lotes (FIFO, classification, lotes abiertos/cerrados/alertas) | ⏳ por brainstormear + planificar — ver §Roadmap Phase 3 abajo (#6 + #7 YA resueltos en Phase 2.7) | — | spec maestro §6 (domain) + §4.2 (Lotes Abiertos/Cerrados/Alertas) + `renta/docs/flex_fifo_loader_spec.md` | `v0.3.0-lotes` |
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
6. **[RESUELTO en Phase 2.7, tag `v0.2.5-persister-cleanup` pending merge]** ~~Refactor del persister para no crear `Account` rows huérfanos por counterparties externos~~ — resuelto con tabla `counterparties` + **exclusive arc** (`CHECK` exactly-one por lado: `src/dst_account_id` XOR `src/dst_counterparty_id`), NO con `counterparty_ref TEXT` (descartado por ser blob sin tipo, sin integridad referencial). El persister dejó de recolectar peers de `<Transfer>` en `all_account_ids`; los peers no-propios van a `counterparties` vía `_ensure_counterparties`. Migration atómica `1702589703e1` reconcilió el huérfano real `CS-999999-99`. NOTA: el FK ya era nullable (el CLAUDE.md viejo se equivocaba al decir "drop FK"). Spec: `docs/specs/2026-06-02-persister-counterparties-cleanup-design.md`.
7. **[RESUELTO en Phase 2.7]** ~~`transfer_lots` queda vacío para FOP IN~~ — la investigación contra el XML real 2026 (`flex_imports.id=18`) confirmó la causa: **`<TransferLot>` es _sibling_ de `<Transfer>` (no anidado)** y, peor, **carece de `costBasis`/`openDateTime`** (las columnas NOT NULL de la tabla). `transfer_lots` era estructuralmente impoblable desde Activity Flex → **se eliminó** (tabla + modelo + parser + persister). El cost basis sigue preservado en `open_position_lots`/`closed_lots`; la auditabilidad "cost basis por transfer" es reconstruible por join en Phase 3 (domain layer). Ver memoria [[transferlot-unpopulatable-activity-flex]].

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

### Shortcuts y flujos canónicos

Durante exploración o debugging, atajos están OK (generar artefactos offline, bypass temporal de scripts, comandos manuales con env vars puntuales). **Antes de `git commit`**:

1. Reemplazar el atajo por el flujo canónico documentado (CLAUDE.md §Comandos comunes, scripts de `package.json`/`pyproject.toml`, targets del `Makefile`, conventions de los `compose*.yaml`), **O**
2. Surface el trade-off al usuario con un por-qué explícito y pedir aprobación para commitear el estado no-canónico.

**Señales de alerta** (revisar antes de cada commit):
- Container running en código viejo mientras el source tiene cambios que afectan OpenAPI schema o comportamiento de endpoints
- Build artifacts (`openapi.json`, `generated.ts`, migrations, `node_modules`) generados por un path no documentado en scripts del repo
- Comandos manuales con env vars o flags inventados que no aparecen en ningún script documentado (e.g. `DATABASE_URL="..." uv run python -c "..."`)
- El `docker compose ps` muestra containers "Created N hours ago" después de un cambio de source que debería estar en ellos

**Caso ejemplo (2026-05-25 — D12 fix, commits `5dba1b4` + `d39ae3d`):** generé `frontend/openapi.json` offline via `app.openapi()` en lugar de `docker compose up -d --build backend && pnpm openapi:gen`. El output era idéntico al canónico, pero dejó (a) el container deployado en código viejo — out-of-sync con el source que el commit incluía, (b) `openapi.json` con `indent=2` en lugar del formato compact del repo, y (c) ningún signal de que el código nuevo arrancaba bien (lifespan, conexiones DB, scheduler). Resolución forzada después del usuario detectarlo: rebuild container, re-correr el `openapi:gen` canónico, amend del commit con el `openapi.json` reformatted. **Lección:** "output equivalente" no es suficiente — el camino importa porque valida boot, deja el entorno dev en estado reproducible, y matchea el repo (formatting incluido). Si tomás un atajo, replanteá antes del commit, no después de que el usuario te corrija.

**No aplica a:** deuda aceptada como V1 documentada en `docs/plans/2026-05-24-phase2-polish-backlog.md` (D1-D11). Esa es deuda cerrada con rationale; este rule cubre deuda *nueva* no autorizada (workaround introducido en una sesión sin discutirlo).

## Polish backlog (cerrado, ver `docs/plans/2026-05-24-phase1-polish-backlog.md`)

Los 6 items detectados durante Phase 1 fueron resueltos en el plan de polish del 2026-05-24:

- ✓ CORS wildcard guard (config-level via `field_validator`, falla al boot — `backend/src/ibkr_control/config.py`)
- ✓ `test_migrations_apply_cleanly_and_match_metadata` (corre `alembic upgrade head` contra container fresh, verifica drift contra `Base.metadata` — `backend/tests/test_migrations.py`)
- ✓ Refactor module-level `settings = get_settings()` (lazy factories `@lru_cache` en `db/session.py`, `@property` en `auth/manager.py`, inline en `auth/backend.py`)
- ✓ Dockerfile `USER appuser` (UID 1001, /home/appuser) + remove `ports: 5432` de compose.yaml
- ✓ `UserSettingsUpdate.timezone` validado contra `zoneinfo.available_timezones()` (devuelve 422 si TZ desconocido)
- ✓ `UserSettingsUpdate.marginal_rate` con `max_digits=5, decimal_places=4` (devuelve 422 en vez de silent rounding a Numeric(5,4))

## Comandos comunes

> **Quirk de entorno (shell no-interactivo):** `node`/`npm` son funciones lazy de nvm y `pnpm` NO está en PATH hasta cargar nvm. Comandos `pnpm`/`node` fuera de Docker fallan con `command not found: pnpm`. Prefijar con: `export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"` (node 24 + pnpm 11). Para validar el build sin este lío, usar `make prod-local` (corre dentro del container, sin depender del PATH del host).

```bash
# Dev stack (HMR backend + frontend, bind mounts)
make dev          # equivale a: docker compose -f compose.yaml -f compose.dev.yaml up -d --build
make dev-down
make dev-logs     # tail de logs

# Prod-like local (espejo de Coolify, sin reload — para validar el build de deploy)
make prod-local   # equivale a: docker compose -f compose.yaml up -d --build

# Coolify compose local (validar el compose de prod tal cual)
make coolify-local
make help         # lista todos los targets

# Backend tests
cd backend && uv run pytest -v

# Lint + format (deben quedar en 0 errores / 0 drift)
cd backend && uv run ruff check .       # lint backend (raíz, no solo src/)
cd backend && uv run ruff format .      # formatear backend (Phase 2.7: adoptado en toda la base)
cd frontend && pnpm lint                # ESLint flat (eslint-config-next + typescript-eslint type-aware)
cd frontend && pnpm lint:fix            # auto-fix

# Regenerar cliente TS del OpenAPI (cuando cambia el backend)
cd frontend && pnpm openapi:gen

# E2E
cd frontend && pnpm e2e

# Migration nueva
cd backend && uv run alembic revision --autogenerate -m "descripción"
cd backend && uv run alembic upgrade head
```

**Diferencias dev vs prod (lock):**
- **Dev** (`make dev`) — backend con `uvicorn --reload --reload-dir src` + dev deps (pytest, ruff disponibles en el container); frontend con `next dev` + HMR + `WATCHPACK_POLLING=true` (macOS Docker Desktop necesita polling para detectar file events del bind mount).
- **Prod** (`make prod-local` y Coolify) — backend sin reload, dev deps excluidas (`--no-dev`); frontend con `next build` + `node server.js` (standalone bundle, sin source). Source horneado en la imagen.
- Para iterar UI/lógica: `make dev`. Para validar antes de push a Coolify: `make prod-local` (debe arrancar igual que en Coolify).

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
