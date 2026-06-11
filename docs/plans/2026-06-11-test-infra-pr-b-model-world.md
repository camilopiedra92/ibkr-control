# Test infra PR-B — Convergencia de schema del model world Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el "model world" de tests (`db_session` + `db_engine`, ~33 archivos) deje de construir el schema con `Base.metadata.create_all` (un schema paralelo, RLS apagado) y pase a usar un **clon del template migrado** (`test_db`, ya existente de PR-A), **siguiendo como rol OWNER** — una sola fuente de schema (las migraciones), cero cambio de rol (el flip owner→app_rls es PR-C).

**Architecture:** `db_session` y `db_engine` hoy hacen `create_all`/`drop_all` sobre la DB default `test` del contenedor + parchean a mano el rol `app_rls` y la función SECURITY DEFINER (que `create_all` no produce). PR-B los reescribe para construir su sesión/engine sobre un clon `test_db` (DB migrada vía `alembic upgrade head`, que YA trae rol + policies FORCE RLS + función + seeds de control-plane). El owner es el superuser del contenedor → bypassa RLS aun bajo FORCE, así que los `sample_*` siguen sembrando sin contexto y la semántica de los tests no cambia. Lo que SÍ cambia: el schema bajo prueba pasa a ser el que se deploya.

**Tech Stack:** pytest + pytest-asyncio + pytest-xdist + testcontainers-postgres + SQLAlchemy async (asyncpg) + Alembic + Postgres RLS.

**Spec:** `docs/specs/2026-06-11-test-infra-worldclass-design.md` (§Rollout PR-B; D2, D4, D5).

**Branch:** `test-infra/pr-b-model-world` (ya creada desde `main` post-merge de PR-A #15 + docs #16). Repo PÚBLICO, `main` PROTEGIDO → branch → PR → CI verde (`backend`+`frontend`) → merge.

**Naturaleza del cambio:** es un **refactor de fixtures validado por la suite existente** — los ~428 tests SON la red de seguridad. No hay "test nuevo que falla primero"; el criterio es que la suite quede verde con el mismo conteo, y que cualquier rojo que aparezca se diagnostique como señal legítima (el schema migrado destapa una diferencia que `create_all` escondía), NO se esconda.

**Scope OUT (NO tocar):** `app_with_db`/`client`/`test_db`/`template_db`/`_maintenance_engine`/`app_owner_engine`/`app_rls_db_session` (PR-A, estables); el flip de rol owner→`app_rls` (PR-C); los fixtures `ephemeral_*` de `conftest_ephemeral_db.py` (se convergen aparte, fuera de este PR).

**Convención de comandos:** desde `backend/`. `addopts` ya fuerza `-n auto`; para medir serial usar `-n0`. Ruff: `cd backend && uv run ruff check . && uv run ruff format --check .`.

---

## File Structure

- **Modify** `backend/tests/conftest.py` — reescribir `db_session` (líneas 87-128) y `db_engine` (líneas 131-145) para depender de `test_db`; quitar `create_all`/`drop_all`, el parche de grants/función, el import de registro de modelos y el param `monkeypatch` de `db_session`; limpiar el import top-level `Base` si queda huérfano.
- **Possibly modify** algún archivo de test del model world SI el schema migrado destapa una diferencia legítima vs `create_all` (ej. seed `institutions.ibkr` presente, tabla `apscheduler_jobs` presente, server_defaults). Se identifica al correr la suite; cada fix se justifica y se commitea con su test.

Ningún archivo nuevo.

---

## Task 1: Reescribir `db_session` sobre el clon migrado (owner)

**Files:**
- Modify: `backend/tests/conftest.py` (fixture `db_session`, líneas 87-128)

**Contexto:** `test_db` (fixture function-scoped de `conftest_template_db.py`, importada en `conftest.py`) yield­ea el **DSN asyncpg OWNER** de un clon recién creado del template migrado, y ya setea `DATABASE_URL` + `JWT_SECRET` + `get_settings.cache_clear()`. Por eso `db_session` ya no necesita setear el env ni hacer `create_all` ni parchear grants — todo eso lo aporta el clon.

- [ ] **Step 1: Leer el estado actual** del fixture `db_session` (conftest.py:87-128) para confirmar las líneas exactas antes de reemplazar.

- [ ] **Step 2: Reemplazar el cuerpo de `db_session`**

Reemplazar el fixture completo (líneas 87-128, desde `@pytest.fixture` hasta `await engine.dispose()` inclusive) por:

```python
@pytest.fixture
async def db_session(test_db):  # noqa: F811 — test_db es fixture importada, no redefinida
    """Sesion de DB directa (OWNER) sobre un clon migrado del template.

    Para tests de schema/modelos sin HTTP layer. El schema viene del template
    migrado via ``alembic upgrade head`` (NO de ``Base.metadata.create_all``), asi
    que estos tests corren contra el schema que se deploya: constraints,
    server_defaults, las policies RLS con FORCE, el rol ``app_rls`` y la funcion
    SECURITY DEFINER, y los seeds de control-plane (p.ej. ``institutions.ibkr``)
    existen igual que en prod. Conecta como el OWNER del contenedor (superuser ->
    bypassa RLS aun bajo FORCE), igual que antes; el flip a ``app_rls`` es PR-C.

    Cada test recibe su propio clon (``test_db`` es function-scoped), asi que el
    aislamiento ya no necesita ``create_all``/``drop_all``: el clon nace pristino
    y se dropea en el teardown de ``test_db``.
    """
    engine = create_async_engine(test_db)
    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with session_maker() as session:
            yield session
    finally:
        await engine.dispose()
```

Esto elimina: el `import ibkr_control.db` de registro de modelos, el `Base.metadata.create_all`/`drop_all`, el parche `app_role_grants_sql`/`system_enum_function_sql`, el seteo de `DATABASE_URL`/`JWT_SECRET`, el `get_settings.cache_clear()`, y el param `monkeypatch` (todo lo aporta `test_db` o ya no aplica).

- [ ] **Step 3: Correr un archivo representativo del model world (rápido, serial)**

Run: `cd backend && uv run pytest tests/test_identity_models.py -n0 -v`
Expected: PASS (6 tests). Estos crean orgs/users/parties/memberships vía `sample_*` y asertan invariantes de identidad — si pasan, el clon-como-owner sirve de sustituto de `create_all` para el caso típico.

- [ ] **Step 4: Correr TODO el model world (serial, para leer fallos claros)**

Run: `cd backend && uv run pytest tests/test_identity_models.py tests/test_provision_org.py tests/test_db_hardening.py tests/test_account_multihome.py tests/test_rls_context_survives_commit.py tests/test_app_runs_under_rls.py tests/test_apscheduler_jobs_grants.py tests/test_poison_reset_script.py tests/test_runtime_role_guard.py tests/api/test_org_context.py tests/test_phase2_identity_migration.py tests/test_phase2_flex_raw_migration.py tests/test_phase2_trm_migration.py tests/test_phase2_ingest_log_migration.py tests/test_phase2_dividend_accruals_migration.py -n0 -q`
Expected: idealmente PASS. **Si hay rojos, NO los escondas** — diagnosticá la causa:

  - **`institutions` no vacío:** el clon migrado trae la fila seed `institutions.ibkr` (la migración la inserta); `create_all` no. Si un test asume `institutions` vacío, el fix correcto es ajustar la aserción a la realidad del schema deployado (p.ej. filtrar/contar excluyendo el seed, o asertar `>= 1`). Commitealo con una nota.
  - **`apscheduler_jobs` existe:** la migración crea esa tabla (`IF NOT EXISTS`); `create_all` no (no está en `Base.metadata`). Si `test_apscheduler_jobs_grants.py` la creaba a mano o asumía ausencia, ajustar el test a que ya existe.
  - **server_default presente:** el schema migrado puede traer un `server_default` (p.ej. `created_at NOW()`) que `create_all` también pone — si difiere, es un drift real ya cubierto por `test_migrations.py`; alinear el test.
  - **RLS policies existen pero owner las bypassa:** no debería romper nada (superuser bypassa FORCE); si un test rompe acá, es señal de que dependía de "sin policies" — investigá.
  - Si un rojo NO encaja en estas categorías y parece un bug real del cambio, escalá BLOCKED con el output en vez de adivinar.

- [ ] **Step 5: Correr la suite COMPLETA (paralelo) para confirmar que no rompiste el endpoint world ni otros**

Run: `cd backend && uv run pytest -q`
Expected: **428 passed** (mismo conteo que post-PR-A). Si el conteo bajó o hay rojos fuera del model world, investigá (no debería: solo tocaste `db_session`).

- [ ] **Step 6: Ruff + Commit**

Run: `cd backend && uv run ruff check tests/conftest.py && uv run ruff format tests/conftest.py`
Expected: `All checks passed!` (Si quedó un import `Base` huérfano y ruff marca F401, NO lo borres todavía — `db_engine` aún no se tocó y podría usarse; el cleanup de imports es Task 3. Si ruff marca F401 de `Base` ya en este punto, dejá el import con un `# noqa: F401` temporal y anotalo para Task 3.)

```bash
cd /Users/camilopiedra/Development/ibkr-control
git add backend/tests/conftest.py
# incluí también cualquier archivo de test ajustado en Step 4, si hubo:
# git add backend/tests/<archivo_ajustado>.py
git commit -m "test(infra): db_session sobre clon migrado (owner), borra create_all + parche grants"
```

---

## Task 2: Reescribir `db_engine` sobre el clon migrado (owner)

**Files:**
- Modify: `backend/tests/conftest.py` (fixture `db_engine`, líneas ~131-145 — recontar tras Task 1)

**Contexto:** `db_engine` lo usan los tests que abren MÚLTIPLES sesiones concurrentes (contención de advisory locks). Antes compartía la DB default del contenedor con `db_session`. Ahora apunta al mismo clon `test_db` — multi-conexión funciona porque es una DB real. `db_engine` y `db_session` ambos dependen de `test_db` (function-scoped) → un test que pida los dos recibe EL MISMO clon (pytest cachea el fixture por test), que es justo lo que un test de contención quiere (dos engines sobre la misma DB).

- [ ] **Step 1: Identificar qué tests usan `db_engine`**

Run: `cd backend && grep -rln "db_engine" tests/`
Anotá los archivos (esperado: tests de advisory lock / sesiones concurrentes). Estos son los que validan este cambio.

- [ ] **Step 2: Reemplazar el cuerpo de `db_engine`**

Reemplazar el fixture `db_engine` completo por:

```python
@pytest.fixture
async def db_engine(test_db):  # noqa: F811 — test_db es fixture importada, no redefinida
    """OWNER engine sobre el mismo clon migrado, para tests que abren multiples
    sesiones concurrentes (p.ej. contencion de advisory locks).

    Multi-conexion real: ``test_db`` es una base de datos independiente, asi que
    dos engines/conexiones se comportan como en prod. Function-scoped: un clon por
    test (compartido con ``db_session`` si el test pide ambos -> misma DB)."""
    engine = create_async_engine(test_db, echo=False)
    try:
        yield engine
    finally:
        await engine.dispose()
```

- [ ] **Step 3: Correr los tests que usan `db_engine` (serial)**

Run: `cd backend && uv run pytest $(grep -rln "db_engine" tests/ | tr '\n' ' ') -n0 -v`
Expected: PASS. Si un test de contención asumía algo de la DB default compartida (`test`) que el clon no replica, diagnosticá; el clon es una DB limpia migrada, debería ser estrictamente mejor. Escalá BLOCKED si hay un rojo que no entendés.

- [ ] **Step 4: Ruff + Commit**

Run: `cd backend && uv run ruff check tests/conftest.py && uv run ruff format tests/conftest.py`
Expected: `All checks passed!`

```bash
cd /Users/camilopiedra/Development/ibkr-control
git add backend/tests/conftest.py
git commit -m "test(infra): db_engine sobre clon migrado (owner) para tests multi-conexion"
```

---

## Task 3: Cleanup de imports huérfanos + suite completa + medición

**Files:**
- Modify: `backend/tests/conftest.py` (imports top-level que quedaron sin uso)

- [ ] **Step 1: Detectar imports huérfanos**

Run: `cd backend && uv run ruff check tests/conftest.py`
Esto marca cualquier import sin uso (F401). Tras Tasks 1-2, candidatos a quedar huérfanos: `from ibkr_control.db.base import Base` (era para `create_all`/`drop_all`). Verificá con `grep -n "Base\b" tests/conftest.py` que `Base` NO se use en ningún otro lado del archivo antes de borrarlo. NO borres imports que sigan en uso (p.ej. `create_async_engine`, `async_sessionmaker`, `AsyncSession`, `swap_dsn_credentials`, `app_rls_password`, `PostgresContainer` — este último lo usa `postgres_container`).

- [ ] **Step 2: Quitar el/los import(es) huérfano(s)**

Borrar la línea de import que ruff marque como F401 (típicamente `from ibkr_control.db.base import Base`). Si removiste un `# noqa: F401` temporal puesto en Task 1, quitalo también.

- [ ] **Step 3: Confirmar que `postgres_container` sigue existiendo y en uso**

Run: `cd backend && grep -n "def postgres_container\|postgres_container" tests/conftest.py tests/conftest_template_db.py`
Expected: `postgres_container` SIGUE definido en conftest.py y lo consumen `_maintenance_engine`/`template_db`/`test_db` (el contenedor host). NO debe quedar huérfano (si quedara, sería señal de que algo más cambió — investigá, no lo borres a ciegas).

- [ ] **Step 4: Suite completa verde (paralelo) + conteo**

Run: `cd backend && uv run pytest -q`
Expected: **428 passed**, mismo conteo. Sin rojos.

- [ ] **Step 5: Medir el delta serial del model world**

Run: `cd backend && uv run pytest -n0 -q` y anotá el wall-clock (control PR-A serial era ~129s; el model world deja de pagar `create_all`/`drop_all` por test, pero ahora paga el clon — el neto debería ser similar o algo mejor; lo importante es que ya NO hay dos rutas de schema). También `cd backend && time uv run pytest -q` para el paralelo (debería seguir en la franja de ~30-45s).

- [ ] **Step 6: Sanity — una sola fuente de schema**

Run: `cd backend && grep -rn "create_all\|drop_all\|app_role_grants_sql\|system_enum_function_sql" tests/conftest.py`
Expected: **vacío** (el model world ya no usa `create_all`/`drop_all` ni parchea grants — el template migrado es la única fuente de schema). Nota: `app_role_grants_sql`/`system_enum_function_sql` pueden seguir existiendo en `tests/conftest_template_db.py`? NO — el template los obtiene de la migración, no los parchea. Si aparecen en conftest.py, no se limpiaron.

- [ ] **Step 7: Ruff final + Commit**

Run: `cd backend && uv run ruff check . && uv run ruff format --check .`
Expected: `All checks passed!`

```bash
cd /Users/camilopiedra/Development/ibkr-control
git add backend/tests/conftest.py
git commit -m "test(infra): limpiar imports huerfanos del model world (Base/create_all)"
```

- [ ] **Step 8: Push + PR** (confirmar con el usuario antes, per política del repo)

```bash
cd /Users/camilopiedra/Development/ibkr-control
git push -u origin test-infra/pr-b-model-world
gh pr create --title "test infra PR-B: model world sobre clon migrado (sigue owner)" \
  --body "PR-B del spec docs/specs/2026-06-11-test-infra-worldclass-design.md. Converge el model world (db_session/db_engine, ~33 archivos) de create_all -> clon del template migrado, SIGUIENDO owner (cero cambio semantico). Borra el parche de grants/funcion (una sola fuente de schema = migraciones). El flip owner->app_rls es PR-C. Suite 428 verde."
```

Esperar CI verde (`backend` + `frontend`) antes de merge.

---

## Self-Review (cubierto en este plan)

- **Spec coverage:** §Rollout PR-B "db_session pasa a test_db clonado, conecta igual como owner, cero cambio semántico, retira create_all + el parche de grants" → Task 1 (db_session) + Task 2 (db_engine) + Task 3 (cleanup). D5 "una sola fuente de schema" → Task 3 Step 6 (grep vacío). D2/D4 "sigue owner, app_rls es PR-C" → ambos fixtures conectan a `test_db` (owner DSN), sin swap a app_rls. El scope OUT (app_with_db, test_db, ephemeral_*, flip a app_rls) está explícito.
- **Placeholder scan:** sin TBD/TODO; el código de reemplazo de ambos fixtures está completo e inline. La única parte "abierta" (triage de latentes en Task 1 Step 4) está acotada con categorías concretas + la regla "diagnosticá/no escondas/escalá si no encaja", que es lo correcto para un refactor-bajo-suite donde el set exacto de latentes no se conoce a priori (replica la disciplina "validar contra data real" del proyecto).
- **Type/contract consistency:** `db_session(test_db)` y `db_engine(test_db)` dependen del mismo `test_db` (owner asyncpg DSN) que PR-A ya entrega; usan `create_async_engine`/`async_sessionmaker`/`AsyncSession` ya importados en conftest.py; `# noqa: F811` consistente con el patrón de PR-A (fixture importada usada como parámetro).
