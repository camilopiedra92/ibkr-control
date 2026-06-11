# Spec — Test infra world-class (template DB + clon por test + RLS real)

**Fecha:** 2026-06-11
**Branch:** `test-infra/worldclass-db-isolation`
**Estado:** diseño aprobado (brainstorming) — pendiente plan de implementación

## Contexto y problema

La suite backend (423 tests) tarda **311.70s (5:11)** medido el 2026-06-11
(`uv run pytest -q --durations=25`, baseline en `main`). El reparto del costo:

| Mundo | Tests | Costo/test | % wall-clock |
|---|---|---|---|
| **Endpoint** (`tests/api/` + `test_settings.py`) | 118 | ~1.7s setup | **~64% (~200s)** |
| **Modelo** (`db_session`) | 305 | ~0.36s | ~36% (~110s) |

Dos causas estructurales:

1. **`_migrated_app_db` arranca un `PostgresContainer` NUEVO + corre `alembic
   upgrade head` POR TEST** (conftest `tests/conftest.py:51-86`). Los 118
   endpoint tests pagan ~1.7s de provisioning cada uno. Las 25 duraciones más
   lentas de la suite son TODAS `setup` de este fixture.
2. **Dos fuentes de schema divergentes.** El mundo `db_session`
   (`tests/conftest.py:124-165`) construye las tablas con
   `Base.metadata.create_all`, **no con migraciones**, y conecta como **owner
   (superusuario → bypassa RLS)**. Por eso 17 archivos de tests corren contra un
   schema paralelo con **RLS apagado**, y el conftest tiene que **reconstruir a
   mano** el rol `app_rls` + la función `SECURITY DEFINER`
   (`tests/conftest.py:145-156`); las policies RLS con `FORCE` directamente no
   existen en ese mundo.

El split de dos contenedores (mundo create_all/owner vs mundo alembic/app_rls)
existe SOLO para evitar la colisión `create_all` vs `alembic` sobre un
contenedor compartido — es un workaround, no un requisito.

### Por qué importa para un SaaS multi-tenant

RLS es la frontera de seguridad del producto. Correr tests de datos como owner
(bypass RLS) es **exactamente** la clase de falsa confianza que el proyecto ya
pagó caro: ver memoria `rls-runtime-vs-test-parity` y PR #7
(`docs/specs/2026-06-05-sp1-rls-runtime-wiring-design.md`) — RLS estaba verde en
tests (app_rls) pero APAGADO en prod (superusuario). Un persister que escriba una
fila con el `organization_id` equivocado pasaría verde en un test que corre como
owner, y filtraría cross-tenant en prod. El test debe correr bajo el rol real.

## Objetivos

1. **Costo por-test constante respecto al largo de la cadena de migraciones.**
   La política post-deploy es migraciones inmutables → la cadena crece por años;
   `alembic upgrade head` por-test se vuelve linealmente más lento para siempre.
   El target debe ser O(1) respecto al nº de migraciones.
2. **Una sola fuente de schema: las migraciones.** Eliminar `create_all` de los
   tests → cero divergencia, el test corre contra el schema que se deploya
   (incluidas policies RLS con FORCE, rol app_rls, función SECURITY DEFINER, seed
   `institutions.ibkr`).
3. **Fidelidad de seguridad: el código bajo prueba corre como `app_rls` bajo RLS
   real**, default fail-closed.
4. **Velocidad:** de 311s a la franja de ~10-20s con paralelismo, sin perder
   fidelidad.
5. **Sin tech debt, sin workarounds, sin legacy code** (requisito explícito del
   owner).

## Decisiones (locked)

### D1 — Aislamiento por clon de template database (no rollback, no truncate)

Migrar **una vez** a una `template_migrated` (`alembic upgrade head`). Por test:
`CREATE DATABASE test_<wid>_<n> TEMPLATE template_migrated` (~80-120ms) → correr
→ `DROP DATABASE` en teardown.

**Rechazado — rollback/savepoint por test:** no cruza conexiones (los tests de
contención de advisory locks usan 2+ conexiones concurrentes; el fixture
`db_engine` existe para eso), y emular el commit interno del persister exige el
patrón savepoint-restart, que envuelve la ejecución en una transacción externa
que prod no tiene → divergencia test/prod. No cubre todos los casos → obligaría a
mantener un segundo mecanismo.

**Rechazado — TRUNCATE por test:** más rápido (~20ms) pero exige preservar los
seeds de control-plane al truncar (lógica "qué tabla sí/no" = coupling/deuda),
rompe los tests de DDL/migración, y exige reset manual de secuencias. El delta de
velocidad vs clon desaparece bajo xdist; queda como palanca de velocidad
reservada (D7), no como mecanismo principal.

**Elegido — clon de template:** un solo mecanismo uniforme para single-conn,
multi-conn, commits mid-request y DDL. Cero mantenimiento (el clon copia schema +
seeds). Aislamiento perfecto (DB fresca por test). Costo O(1) respecto a la cadena
de migraciones (el clon copia el resultado, no re-corre migraciones).

### D2 — `app_rls` es el rol de conexión por defecto para todo lo que toca la DB

`owner` queda como excepción explícita y nombrada (`owner_session`/`owner_engine`)
para: (a) seeding de precondiciones cross-org, (b) control-plane
(`system_credentialed_org_ids`, catálogo `institutions`), (c) tests de
DDL/migración.

El default es **fail-closed**: un test que olvide setear `org_context` ve cero
filas → falla ruidoso, no pasa en silencio. Honra la memoria
`rls-runtime-vs-test-parity`.

### D3 — Tres tiers de test

| Tier | Qué prueba | Fixtures | DB |
|---|---|---|---|
| 0 — sin DB | funciones puras (parser XML, domain, config, RetryPolicy) | ninguno de DB | no |
| 1 — DB / app_rls | persister, modelos, endpoints | `test_db` + `app_rls_session` + `org_context` | clon |
| 2 — DB / owner | seeding multi-org, control-plane, DDL/migración | `test_db` + `owner_session` | clon |

### D4 — Principio de seeding

Las **precondiciones** se siembran libremente (owner, o app_rls+context para un
solo tenant). El **código bajo prueba** se invoca siempre como **app_rls con
org_context** — porque eso corre en prod. No se testea el setup; se testea el SUT
bajo el rol real. Los tests cross-tenant (p.ej. `second_sample_user`, que crea un
org separado) son inherentemente Tier 2 → `owner_session`.

### D5 — Convergencia: un contenedor, un schema, dos roles, N clones

El split de dos contenedores se elimina. `db_session` y `_migrated_app_db` pasan a
ser "un `test_db` clonado del template". Se borra:
- El parche manual `app_role_grants_sql` + `system_enum_function_sql`
  (`tests/conftest.py:145-156`) — ahora en el template vía migración.
- La lógica + docstrings del split de dos contenedores
  (`tests/conftest.py:61-69, 185-219`).
- `create_all`/`drop_all` por test.

### D6 — Paralelismo con pytest-xdist, un contenedor + template por worker

Local y CI corren `pytest -n auto` (= `os.cpu_count()`). Cada worker de xdist
tiene su propio `PostgresContainer` + `template_migrated` (aislamiento total, sin
coordinación cross-worker). Nombre del clon namespaced por `PYTEST_XDIST_WORKER`.
CI: `ubuntu-latest` (4 cores hoy) → 4 workers; testcontainers ya corre en CI (no
usa `services: postgres`).

**`-n auto` también en CI (NO pin `-n 4`).** El determinismo que importa es de
correctitud, no de timing: el clon-de-template garantiza aislamiento perfecto por
test → el nº de workers no afecta el resultado, solo la velocidad. Pinnear un
número lo vuelve stale cuando GitHub cambia los runners (ya pasó 2→4 cores) o al
correr en un runner más grande. El único riesgo de `auto` es memoria (N
contenedores); mitigación opcional sin pinnear: `PYTEST_XDIST_AUTO_NUM_WORKERS`
(env que xdist respeta) como **tope**, default sin tope. Acotar solo si se observa
contención — no pre-optimizar un número mágico.

Trade-off aceptado: N contenedores Postgres (~N×40MB RAM) + N `alembic upgrade`
en el arranque (paralelo, ~2-4s one-time) a cambio de cero coordinación
cross-worker.

### D7 — TRUNCATE reservado como palanca de velocidad futura

Si algún día el overhead del clon (~100ms) molesta a escala, TRUNCATE sobre una DB
por-worker es la optimización reservada. No se construye ahora (YAGNI): bajo
xdist el clon ya deja la suite en la franja de ~10-20s.

## Arquitectura — topología de fixtures

```
SESSION (una vez por worker de xdist)
 ├─ pg_container          PostgresContainer("postgres:16-alpine")   (renombre de postgres_container)
 ├─ _maintenance_engine   engine AUTOCOMMIT sobre la DB `postgres` (CREATE/DROP DATABASE)
 └─ template_db           CREATE DATABASE template_migrated
                          + alembic upgrade head  (incluye rol app_rls + policies FORCE + seeds)
                          + dispose del engine (cero conexiones al template)

FUNCTION (por test que toca DB)
 ├─ test_db               CREATE DATABASE test_<wid>_<n> TEMPLATE template_migrated  (~100ms)
 │                        ...yield DSN...   DROP DATABASE ... WITH (FORCE)  (teardown)
 ├─ app_rls_session/_engine   sobre test_db como app_rls   (NullPool)  ← DEFAULT Tier 1
 ├─ owner_session/_engine     sobre test_db como owner     (NullPool)  ← excepción Tier 2
 └─ org_context(org_id, user_id?)   setea app.current_org / current_user (envuelve apply_org_context)
```

### Mapa de migración fixture-por-fixture

**Nace:** `pg_container`, `_maintenance_engine`, `template_db`, `test_db`,
`app_rls_session`/`app_rls_engine`, `owner_session`/`owner_engine`, `org_context`.

**Se transforma:**
- `db_session` → sesión sobre `test_db`; default app_rls + org_context a un org
  sembrado; `owner_session` para multi-tenant.
- `_migrated_app_db` → **eliminado** (reemplazado por `template_db` + `test_db`).
- `app_with_db` / `client` → misma interfaz; engine apunta al clon `test_db`. La
  app sigue como app_rls.
- `db_engine` + `app_owner_engine` → unificados en `owner_engine` sobre `test_db`
  (multi-conexión real: es una DB de verdad).
- `app_rls_db_session` → absorbido por `app_rls_session` (default, no caso
  especial).
- `sample_org/user/party/account/flex_import` → siembran su tenant; single-tenant
  app_rls+context, cross-tenant vía `owner_session`.
- `auth_headers_with_org` / `_2` → mismo patrón (ya siembran como owner + setean
  GUC para el Party); solo cambian de qué DSN derivan.

**Deps nuevas:** `pytest-xdist` (dev). Invocación: `pytest -n auto` (local + CI).

## Manejo de errores / edge cases

| Edge case | Mitigación |
|---|---|
| `CREATE DATABASE ... TEMPLATE` falla con conexiones abiertas al template | `template_db` dispone su engine al terminar de migrar; ningún clon conecta al template |
| `CREATE`/`DROP DATABASE` no corren en transacción | `_maintenance_engine` con `isolation_level="AUTOCOMMIT"` sobre la DB `postgres` |
| `DROP DATABASE` falla con conexiones al test_db | teardown dispone engines del test; `DROP DATABASE ... WITH (FORCE)` (PG 16) termina rezagados |
| Engines per-test con conexiones colgadas | `poolclass=NullPool` en engines per-test |
| Colisión de nombres bajo xdist | nombre del clon = `test_<PYTEST_XDIST_WORKER>_<contador>` |
| Costo del template build | una vez por worker al arrancar (alembic O(migraciones) solo ahí), nunca por test |

## Verificación de paridad

1. **Los 423 tests existentes quedan verdes, mismo conteo, mismas aserciones** —
   prueba primaria de que el clon es fiel.
2. **`test_migrations.py` (drift) se queda** — el template es el único schema; su
   correctitud vs `Base.metadata` es la red.
3. **Guard nuevo "el clon es fiel":** sobre un `test_db` recién clonado, asertar
   `relforcerowsecurity=true` en las 17 tablas org-scoped Y que `app_rls` es
   `rolsuper=false / rolbypassrls=false`. Espeja el boot guard de PR #7.
4. **Guard nuevo "fail-closed":** query sin `org_context` → asertar **0 filas**
   (no error, no leak). Prueba que el default app_rls atrapa el bug de contexto
   faltante.
5. **Aceptación = wall-clock medido** antes/después, reportado en cada PR.

## Rollout incremental (secuencia de PRs)

**Principio: un PR cambia UNA sola variable.** Empaquetar la convergencia de
schema (create_all→clon) con la de seguridad (owner→app_rls) impediría saber, ante
un test rojo, cuál de las dos lo rompió. Por eso son **tres PRs**, no dos — cada
uno aísla su variable (la misma disciplina que el clon le da a los tests). Cada
PR: suite verde + delta de wall-clock en la descripción.

- **PR-A — infra + endpoint world (variable: provisioning del endpoint world):**
  nace `template_db`/`test_db`/`_maintenance_engine` + clon + `pytest-xdist`.
  Migra los 118 endpoint tests (que **ya** corren como app_rls → sin cambio
  semántico, solo provisioning más rápido). Borra `_migrated_app_db`. El mundo
  `db_session` queda intacto (todavía create_all). Esperado: ~200s → ~15s en ese
  slice. Máximo retorno, mínimo riesgo.
- **PR-B — convergencia de schema del model world (variable: provisioning del
  model world, `create_all`→clon, SIGUE owner):** `db_session` pasa a `test_db`
  clonado pero **conecta igual como owner** (RLS sigue bypasseado). **Cero cambio
  semántico** — los 305 tests se comportan idénticos, solo cambia la fuente del
  schema. Retira `create_all` + el parche de grants/función (conftest 145-156).
  Verde = prueba de que el clon es un sustituto fiel de create_all. De-riskea
  PR-C: deja el schema ya migrado/fiel antes de tocar el rol.
- **PR-C — convergencia de seguridad del model world (variable: rol owner→app_rls):**
  los tests modelo conectan como `app_rls` + `org_context`; `owner_session` queda
  como excepción nombrada (D2/D4); formaliza los Tier 2. **Acá** se enforce­a RLS y
  pueden saltar bugs latentes — aislado, así cualquier rojo ES el flip de rol, no
  la fuente del schema.

## Fuera de alcance

- TRUNCATE como mecanismo de aislamiento (D7 — reservado, YAGNI).
- Compartir un contenedor entre workers de xdist (D6 — container-por-worker
  elegido por simplicidad).
- Cambios al schema de producción, a las migraciones, o al runtime de la app —
  esto es exclusivamente test infra.
- Frontend (vitest/Playwright) — fuera de alcance, esto es backend.

## Referencias

- Baseline medido: `uv run pytest -q --durations=25` en `main`, 2026-06-11
  (311.70s / 423 tests).
- `tests/conftest.py` (estado actual: dos mundos, líneas 36-507).
- `src/ibkr_control/db/rls.py` (apply_org_context, policies, app_rls role,
  SECURITY DEFINER fn).
- `alembic/versions/a9977ac077e5_tier1_baseline.py` (seed `institutions.ibkr`
  inline en upgrade).
- PR #7 `docs/specs/2026-06-05-sp1-rls-runtime-wiring-design.md` (boot guard,
  test/prod parity del rol de conexión).
- Memoria `rls-runtime-vs-test-parity`.
