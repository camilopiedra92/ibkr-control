# Pre-SP3 Hardening Pass — Design

> **Fecha:** 2026-07-06 · **Branch:** `saas/pre-sp3-hardening` · **Estado:** spec aprobado, pendiente plan
>
> **Origen:** auditoría multi-agente read-only del 2026-07-06 (3 finders especializados — escala/concurrencia, completitud authz multi-tenant, modelo de datos/migraciones/seams — verificada contra código). Deriva de que el `docs/plans/2026-06-03-pre-deploy-hardening-backlog.md` quedó **calibrado explícitamente para "app personal de ~3 usuarios"** (su línea 7), premisa que el pivote a SaaS del mismo día (2026-06-03, ver [[saas-pivot-target]]) dejó obsoleta. Bajo el target world-class SaaS, varios guards estructurales baratos faltan y hay que cerrarlos **antes** de que Phase 3 (primeros endpoints data-plane + tabla derivada `lot_classifications`) los pise.

## Premisa y framing

El core que SP1 (tenancy + RLS) y SP2 (authorization) construyeron **aguanta el escrutinio** — la auditoría lo confirmó explícitamente:

- El choke-point `require_scope` cubre los 24 endpoints org-scoped; el route-sweep guard tiene el fix de segment-boundary presente (el escape del `startswith` naive está cerrado).
- Las 3 barreras del grantee (scope app / `SET LOCAL transaction_read_only` self-healing / filtro party) están cableadas e independientemente enforced.
- Las funciones `SECURITY DEFINER` son least-privilege, sin superficie de inyección.
- El pool (`pool_pre_ping`/`recycle`/sizing) está bien en app + crons + jobstore (O3 del backlog cerrado por SP1-db-hardening).
- Precisión (PD-1), datetime, y drift consistentes.

El problema **no es el core**. Son dos cosas: **(1)** un desajuste de calibración (el backlog trata como "over-engineering para 3 usuarios" cosas que bajo SaaS son deuda real), y **(2)** un puñado de **guards estructurales fail-closed** que faltan y que Phase 3 introduciría como leaks si no están.

**Este pass es schema-free:** puro tests aditivos + refactors + guards + un boot guard. **Cero migraciones.** No toca el baseline pristino `a9977ac077e5` → la política T1-D14 (baseline mutable hasta el primer deploy) **no se ejerce**. No arriesga nada del modelo de datos.

## Alcance

- **Implementa ahora:** HD-1..HD-7 (guards estructurales + un fix de race real + seams).
- **Difiere con decisión formal registrada:** la deuda de arquitectura de escala (→ SP5/SP7) y el narrowing del DML de identidad (→ SP4). No se adelantan features; se registra la decisión + su porqué + la acción barata de contención de hoy.

El principio "no adelantar features" del repo (aplicado con disciplina en RLS-antes-de-tenants y multihome-antes-de-la-2da-org) **sigue vigente**: solo se construye ahora lo que es (a) barato, (b) fail-closed, y (c) caro de retrofitear o una trampa que Phase 3 pisaría. La cola durable, el leader election y el backing de singletons NO se retrofitean peor por esperar a su SP — lo único caro de retrofitear son los *guards*, que van ahora.

---

## Decisiones (HD-1 .. HD-7 — se implementan)

### HD-1 · Guard inverso de cobertura RLS *(cross-verificado por 2 finders — el de más alto apalancamiento)*

**Problema.** Los tests verifican solo la dirección forward: `test_all_tenant_tables_have_organization_id` (`tests/test_identity_models.py:119`) asserta que toda tabla *en* `ORG_SCOPED_TABLES` tiene `organization_id`; `test_tier1_baseline.py:36` asserta que la lista frozen de la migración == `db/rls.py::ORG_SCOPED_TABLES` (dos listas a mano que concuerdan entre sí). **Nada deriva del metadata que toda tabla CON `organization_id` esté en la lista.** RLS se aplica por un loop a mano sobre esa lista en el baseline; una tabla ausente de la lista se crea con `organization_id` pero **sin policy y sin FORCE RLS**, y `app_rls` tiene DML abierto sobre ella (`GRANT ... ON ALL TABLES`).

**Leak scenario.** Phase 3 agrega `lot_classifications` (org-scoped por naturaleza, ya nombrada en el roadmap). Si el dev agrega el modelo + FK `organization_id` pero olvida sumarla a `ORG_SCOPED_TABLES`, la tabla shippea **sin aislamiento**: cada tenant lee/escribe filas de los demás — **con toda la suite verde**. Es el análogo tabla-nivel del "verde en tests / apagado en runtime" que el boot guard de roles (PR #7, [[rls-runtime-vs-test-parity]]) cerró; para *tablas* no existe el equivalente.

**Decisión.** Dos capas, ambas derivadas de la verdad (no de listas a mano):

1. **Completitud (metadata-derived).** Para toda tabla en `Base.metadata` con columna `organization_id`, el guard exige que esté en uno de tres conjuntos reconocidos: (a) `ORG_SCOPED_TABLES` (policied por el loop `org_isolation`), (b) el conjunto de tablas **policied por separado** (hoy: `access_grants`, con su policy dedicada `grant_visibility`), o (c) marcada **exenta** en el modelo. **La exención se declara EN el modelo**, junto a la tabla, vía `__table_args__ = {"info": {"rls_exempt": "<razón>"}}` (o el mecanismo SQLAlchemy `Table.info` equivalente). Agregar una tabla org-scoped sin decidir conscientemente su régimen RLS → **test rojo**. La decisión de seguridad vive **pegada a la tabla**, self-documenting; no es una lista lejana que hay que acordarse de editar (mismo modo de falla, solo movido).
2. **Realidad DB (reflection-derived).** Contra `owner_engine`, para cada tabla policied (los conjuntos (a) y (b)) el guard asserta `relrowsecurity=t` + `relforcerowsecurity=t` + existencia de al menos una policy. Cierra el caso "la tabla está en la lista pero el baseline no le aplicó FORCE".

**Universo real del guard (verificado 2026-07-06 contra los modelos).** Solo entran las tablas **con columna `organization_id`**. Están cubiertas por construcción:
- **En `ORG_SCOPED_TABLES` (17):** `accounts`, `parties`, `participations`, `connections`, `connection_ibkr_flex`, `counterparties`, `flex_imports`, `flex_import_accounts`, `trades`, `closed_lots`, `open_position_lots`, `transfers`, `cash_transactions`, `change_in_dividend_accruals`, `open_dividend_accruals`, `ingest_log`, `restatement_log`.
- **Policied por separado (1):** `access_grants` (FORCE RLS + policy `grant_visibility` vía `access_grants_policy_sql()`, no el loop) → el guard la reconoce en el conjunto (b).
- **Exenta, a marcar en el modelo (1):** `memberships` — su exención es **load-bearing** para el subquery de `list_grants` (`grants.py:55-57`) y para `authz_grant_party_ids` (`rls.py:249`). Se marca `rls_exempt` con esa razón.
- **Fuera del guard por construcción (sin `organization_id`):** `organizations` (raíz del tenant — tiene `id`, no `organization_id`; su régimen se decide en #6/SP4) y el control-plane `institutions`, `instruments`, `instrument_identifiers`, `trm_days`, `trm_imports` (+ `apscheduler_jobs`, schema de APScheduler).

El valor del guard es que **cualquier tabla nueva con `organization_id`** (p.ej. `lot_classifications` de Phase 3) que no caiga en (a)/(b)/(c) rompe el test — fail-closed.

### HD-2 · Guard de ruta para la barrera-3 (`visible_account_ids`)

**Problema.** La barrera-3 (`party_scope.visible_account_ids`, el filtro que restringe al grantee a las cuentas de los parties otorgados) es **endpoint-local**: SP2 la aplicó a mano en cada endpoint data-aware y la registró como "deuda consciente — todo endpoint `data:read` nuevo DEBE aplicarla". **No hay ni un test que enumere las rutas `data:read`**, así que un dev de Phase 3 que agregue un endpoint data-plane y omita la barrera no recibe **ninguna señal** — y la consecuencia es un leak **dentro** del org (un grantee de una firma ve las filas de *todos* los clientes, no solo los parties otorgados).

**Decisión.** Extender el route-sweep test (`tests/test_route_authz_coverage.py`): toda ruta cuyo scope resuelto sea `data:read` debe referenciar `visible_account_ids` en su árbol de dependencias resuelto. Hoy hay ~0 endpoints data-plane (`list_restatements` ya filtra, `list_grants` es `grants:read`), así que el guard **lockea la convención antes** de que Phase 3 agregue el primero. El shape de la eventual dependencia compartida (`require_data_scope` que aplica el filtro por construcción) se decide **con** el shape de los endpoints de Phase 3 — acá va el guard estructural, no la abstracción prematura. Se registra como guardrail en el spec de Phase 3.

### HD-3 · Race de `_ensure_counterparties`

**Problema.** `ingest/flex/persister.py:932-945` — `_ensure_counterparties` hace `SELECT → session.add(Counterparty(...)) → flush`, mientras los helpers hermanos `_ensure_accounts` (`:1238`) y `_ensure_instruments` (`:1132`) usan `pg_insert(...).on_conflict_do_nothing(...)` + re-SELECT, agregados *específicamente* para cerrar el race de uploads paralelos same-org / cross-org (nota del PR multihome). Los counterparties nunca recibieron ese tratamiento: dos ingests concurrentes del mismo org que referencian un peer externo nuevo racean sobre `UNIQUE(organization_id, external_id)` → `IntegrityError` → **falla toda la transacción del ingest**. Es la misma clase de bug que ya se arregló en las cuentas, dejado a medias — un 500 que solo aparece en prod bajo concurrencia.

**Decisión.** Uniformar `_ensure_counterparties` al patrón `ON CONFLICT DO NOTHING + re-select` de sus hermanos. Test de regresión: dos `persist()` concurrentes/secuenciales del mismo org con un peer externo nuevo compartido no tiran `IntegrityError`.

### HD-4 · Guard del flip del baseline (T1-D14)

**Problema.** La política "baseline mutable hasta el primer deploy, luego inmutable + expand/contract" vive **solo como prosa** en `CLAUDE.md` y el spec de Tier 1. No es un item del `docs/plans/2026-06-03-pre-deploy-hardening-backlog.md` (el gate explícito del primer deploy, ~40 items), no hay checklist entry, no hay guard, no hay test que pinee el `revision` del baseline post-deploy. La política depende de que un humano recuerde — **exactamente en el momento (primer deploy) en que es más fácil olvidarlo** — que el workflow cómodo de "regen del baseline + `down -v` dev", usado en CADA PR de Tier 1, ahora **destruye producción**. Un solo `alembic revision` regenerando el baseline post-deploy = **wipe de datos de tenants**.

**Decisión.** Dos partes:
1. **Item HIGH nuevo en el backlog** (`docs/plans/2026-06-03-pre-deploy-hardening-backlog.md`): "Flip del baseline a inmutable al primer deploy" con checklist (dejar de regenerar el baseline; pasar a expand/contract; pinear el `revision`).
2. **Guard fail-closed:** un sentinel (archivo committeado `backend/.baseline_frozen` o env `BASELINE_FROZEN=1`) que, una vez seteado, hace que un test asserte que el `down_revision=None` del baseline sigue siendo `a9977ac077e5` (el hash pineado). Antes del deploy el sentinel no existe → el test se skipea (la política de baseline mutable sigue viva). Post-deploy, setear el sentinel → un regen accidental del baseline cambia el hash → **test rojo**, no wipe.

### HD-5 · Seams (un fix de race real + una consolidación)

**Problema.** (a) `api/setup.py:711-725` (step3) crea cuentas inline vía `select → Account(...) → flush` — un **tercer** write-path de cuentas que **no** usa `_ensure_accounts`, cargando el mismo race que HD-3. (b) El upsert SCD-2 de participations (encontrar fila abierta, cerrarla con `valid_to=today`, insertar la nueva) está **byte-idéntico** en `setup.py:569-590` (step2/save) y `:729-750` (step3). Phase 3 consume participations en el read-path (`domain/participation.py::apply_pct`) → no elimina estas copias; el primer endpoint de edición manual de participación forzaría una 4ta copia.

**Decisión.**
1. Rutear el write-path de cuentas inline de step3 por `_ensure_accounts` → **elimina la race** (no es solo tidiness).
2. Extraer el upsert SCD-2 de participations a un helper compartido **`db/participations.py::upsert_participation(session, ...)`** consumido por ambos handlers. Se coloca en `db/`, **no** en `domain/` — no se pre-monta el domain layer de Phase 3; el `domain/participation.py::apply_pct` (read) de Phase 3 consumirá este helper de escritura. Los handlers quedan en validación + orquestación.

### HD-6 · Guard de `ondelete` en el drift test

**Problema.** `tests/test_migrations.py` usa `compare_type=True` + `compare_server_default=True` (mejor que el check pre-2.8 de solo nombres de tabla), pero el `compare_metadata` de Alembic **no diffea el `ondelete` de las FK**. El baseline codifica una política mixta deliberada — `RESTRICT` en hechos→accounts/counterparties, `SET NULL` en `flex_import_id`/`user_id` (semántica append-only-ledger + first-seen), `CASCADE` en los paths de org-wipe (~60 sitios `ondelete=`). Post-primer-deploy, las migraciones se autogeneran de los modelos; si un `ondelete` de un modelo diverge de la DB, **ni el drift test ni `--autogenerate` lo emiten**. La semántica del ledger *depende* de que `SET NULL` vs `CASCADE` sea exactamente correcto.

**Decisión.** Test por reflexión que asserta el `confdeltype` de cada FK (contra `owner_engine` o via `information_schema`/`pg_constraint`) contra un mapa esperado explícito. Lockea la semántica que hoy queda desprotegida. El mapa esperado se deriva una vez de los modelos y se pinea.

### HD-7 · Fail-loud del invariante 1-proceso *(la acción "surfacear B", hecha estructural)*

**Problema.** La arquitectura asume "exactamente 1 proceso": el scheduler arranca dentro del `lifespan` de FastAPI (`main.py:30-32`), y `JobTracker`/`Step3Stash` son singletons de módulo per-proceso. Con `uvicorn --workers N` o réplicas: cada worker arranca su propio `AsyncIOScheduler` sobre el jobstore compartido **sin leader election** (APScheduler 3.x no hace locking distribuido) → los crons **disparan una vez por worker**; el SSE `/stream/{job_id}` puede caer en otro worker que el que corre el `BackgroundTask` → **404 en un job válido**; el `Step3Stash` del wizard en worker A es invisible al commit en worker B → **onboarding roto** (hot path de SP3). El backlog A2 lo trata como "correcto bajo el invariante 1-proceso, aceptado para 3 usuarios" — bajo SaaS son **roturas funcionales** en el primer scale-out, y falla **callado**.

**Decisión.** Boot guard en el `lifespan` (o en `create_app`): si detecta más de un worker (env `WEB_CONCURRENCY>1`, flag `--workers>1`, o el mecanismo equivalente), **se niega a arrancar** con un mensaje claro que apunta a esta decisión ("scheduler/JobTracker/Step3Stash son in-process; correr >1 worker rompe crons/SSE/onboarding en silencio — ver SP5 para la extracción del scheduler + cola durable"). Hasta que SP5 extraiga el scheduler, >1 worker **está roto** → fallar loud es correcto. Convierte el comentario-en-código que A2 pedía en una **garantía estructural**, en la misma línea que el boot guard de rol de PR #7.

---

## Decisiones diferidas (registradas formalmente — NO se implementan acá)

### DEF-B · Arquitectura de escala → SP5 (durable jobs) / SP7 (ingest tenant-aware)

La auditoría encontró (verificado contra código) tres consecuencias del diseño 1-proceso, todas de la misma raíz:

- **Scheduler co-locado sin leader election** (severidad: CRÍTICA en el primer `--workers 2`). Los crons Flex/TRM disparan una vez por worker/réplica; `max_instances=1`/`coalesce` son per-scheduler, no cluster-wide.
- **Singletons in-memory** (`JobTracker`, `Step3Stash`) — rotura funcional bajo scale-out (SSE 404, onboarding roto).
- **Conexión DB pineada minutos durante el HTTP a IBKR** (send+poll ≈ 5 min con el connection tomado) → agotamiento de pool que mata handlers vivos; el `BackgroundTask` del refresh manual corre sobre el pool de requests compartido.
- **El cron diario itera orgs serialmente en el event loop** compartido con tráfico vivo → tail-latency user-facing durante la ventana del cron, blast-radius al crecer el conteo de orgs.
- **(Menor) Keyspace de advisory lock de 32 bits** (`zlib.crc32(source:scope_id)` → uint32) — colisión silenciosa entre dos orgs distintos al crecer el conteo (skip con solo un `logger.warning`).

**Por qué se difiere:** el *arreglo* es el corazón de SP5 (el roadmap lo lista textual: *"Temporal vs Celery vs Arq; rate-limit IBKR por tenant; idempotencia; outbox transaccional"*) + SP7 (refactor del pipeline sobre la cola durable). Backear los singletons con Redis mete infra nueva que el modelo de deploy (Coolify single-host) aún no tiene, y `Step3Stash` es parte del hot path de SP3 (rediseñar su backing antes de SP3 es diseñar sobre arena). El modelo (cola durable) **no se retrofitea peor** por esperar a su SP. **Contención de hoy:** HD-7 (fail-loud si >1 worker) evita que se rompa por accidente antes de SP5. **Acción para SP5:** su brainstorming decide el motor de cola + el shape de la extracción del scheduler + el keyspace de lock de 2 args + el backing de los singletons.

### DEF-6 · Narrowing del DML de `organizations`/`memberships` → SP4 (secrets / roles)

`organizations` y `memberships` son RLS-exempt **y** `app_rls` tiene DML completo sobre ellas (`GRANT INSERT/UPDATE/DELETE ON ALL TABLES`, `db/rls.py:295`) — las dos tablas más críticas de seguridad simultáneamente sin backstop RLS y escribibles por el rol que sirve tráfico de usuario. Latente hoy (no hay endpoint de escritura de membership; el provisioning es CLI). **Verificado:** `provision_org` se conecta vía `get_session_maker()` = **como `app_rls`**, y crea `Organization`+`Membership` con ese rol (funciona *porque* el DML está abierto). SP3 va a agregar `POST /api/organizations` que también correrá como `app_rls`.

**Por qué se difiere:** el fix limpio (revocar el DML de `app_rls` + las escrituras de identidad bajo un rol dedicado) **rompe el aprovisionamiento** (CLI de hoy + endpoint de SP3) a menos que exista el rol de escritura dedicado — que es exactamente el "split de 3 roles" reservado para SP4. Poner RLS sobre `organizations` es incómodo: es la raíz del tenant, se crea *antes* de que exista `current_org` que satisfaga un `WITH CHECK`. Intentar el fix ahora adelanta la decisión gorda de SP4 y toca el path que SP3 va a reconstruir. **Acción para SP4:** su brainstorming absorbe el narrowing junto con el rol dedicado. **Nota de vigilancia:** hasta entonces, un `select(Organization)`/`select(Membership)` sin filtro explícito de `id`/org leakearía todos los tenants — hoy todas las lecturas usan `WHERE id = ctx.org_id`; mantener esa disciplina en SP3.

---

## Testing & rollout

- **Método:** subagent-driven-development (patrón Tier 1, [[tier1-execution-pattern]]): por task → implementer + spec-review + code-quality-review; review holístico final. TDD: failing test → impl mínima → passing test → commit.
- **Sin migraciones.** Todo el pass es aditivo (tests + refactors de persister/handlers + un helper nuevo + un boot guard). No toca `Base.metadata` con DDL (el marcador `info={"rls_exempt": ...}` de HD-1 es metadata Python, no DDL). El baseline pristino `a9977ac077e5` **no se toca** → T1-D14 no se ejerce.
- **Suite:** 475 → +~12 (guards HD-1/HD-2/HD-6, regresión HD-3, helper/seam HD-5, guard HD-4, boot guard HD-7). Bajo `app_rls` + FORCE RLS como toda la suite.
- **Verificación final:** `cd backend && uv run pytest -q` (sube de 475) + `ruff check .` + `ruff format --check .` + `cd frontend && pnpm lint && pnpm test:run && pnpm build`. Boot smoke prod-local (el fail-loud de HD-7 NO debe dispararse con la config de 1 worker por defecto).
- **Docs:** actualizar el backlog (item HD-4) + notas de decisión en el roadmap SSOT para SP4 (DEF-6) y SP5 (DEF-B).
- **PR único** `saas/pre-sp3-hardening` → `main`, CI verde (`backend` + `frontend`).

## Anti-scope (decidido NO hacer en este pass)

- Extraer el scheduler / cola durable / leader election → **SP5**. HD-7 solo lo hace fail-loud, no lo arregla.
- Backing de `JobTracker`/`Step3Stash` con Redis/DB → **SP5** (acoplado al motor de cola + al shape de onboarding de SP3).
- Revocar DML / RLS sobre `organizations`/`memberships` → **SP4** (necesita el rol dedicado).
- Construir la dependencia compartida `require_data_scope` → **Phase 3** (necesita el shape de sus endpoints; HD-2 solo lockea la convención con un guard).
- Los items del `pre-deploy-hardening-backlog` que siguen bien clasificados bajo su propia calibración (S1-S14, C-*, O-*, etc.) — no se re-abren acá salvo HD-4 que agrega un item nuevo.
