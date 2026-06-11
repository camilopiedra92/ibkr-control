# Test infra PR-A — Template DB + clon por test (endpoint world) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar el provisioning "contenedor nuevo + `alembic upgrade head` POR TEST" del endpoint world por un **template database migrado una vez por worker + clon barato por test** (`CREATE DATABASE ... TEMPLATE`), bajando ese slice de ~200s a ~15s sin perder fidelidad.

**Architecture:** Una DB `template_migrated` se migra UNA vez por sesión/worker (síncrono, psycopg2 + alembic directo). Cada test clona `test_<worker>_<n> TEMPLATE template_migrated` (~100ms, copia filesystem) y la dropea en teardown. Todo el provisioning es síncrono (sin fixtures async session-scoped); solo las sesiones del test son async. El model world (`db_session`/`create_all` en la DB `test` del mismo contenedor) queda INTACTO — el template vive en otra DB, no colisiona.

**Tech Stack:** pytest + pytest-asyncio (`asyncio_mode=auto`) + pytest-xdist (nuevo) + testcontainers-postgres + SQLAlchemy async (asyncpg) + psycopg2 (sync, para DDL) + Alembic.

**Spec:** `docs/specs/2026-06-11-test-infra-worldclass-design.md` (D1, D5, D6; PR-A en §Rollout).

**Branch:** `test-infra/worldclass-db-isolation` (ya creada, spec commiteado). Repo PÚBLICO, `main` PROTEGIDO — esto va por PR → CI verde → merge.

**Scope OUT de PR-A (NO tocar):** el model world `db_session`/`create_all` (es PR-B); los fixtures `ephemeral_*` de `conftest_ephemeral_db.py` (replay tests — siguen con contenedor-por-test, se convergen después); el rol de conexión de los tests modelo (PR-C).

**Convención de comandos:** todo desde `backend/` salvo el cambio de CI. Tests corren desde el HOST con testcontainers: `cd backend && uv run pytest ...`. Ruff debe quedar limpio: `cd backend && uv run ruff check . && uv run ruff format --check .`.

---

## File Structure

- **Create** `backend/tests/conftest_template_db.py` — fixtures `_maintenance_engine`, `template_db`, `test_db` + el contador de clones. Responsabilidad única: provisioning de DB migrada por clon de template.
- **Create** `backend/tests/test_template_clone_fidelity.py` — guard de paridad 1 (FORCE RLS + rol app_rls non-bypass sobre clon fresco).
- **Create** `backend/tests/test_rls_fail_closed.py` — guard de paridad 2 (app_rls sin contexto → 0 filas).
- **Modify** `backend/tests/conftest_ephemeral_db.py` — agregar helper puro `swap_dsn_database`.
- **Modify** `backend/tests/conftest.py` — importar los fixtures nuevos; repointar `app_with_db`/`app_owner_engine`/`app_rls_db_session` de `_migrated_app_db` a `test_db`; ELIMINAR `_migrated_app_db`.
- **Modify** `backend/pyproject.toml` — dev dep `pytest-xdist` + `addopts = ["-n", "auto"]`.
- **Modify** `backend/uv.lock` — regenerado por `uv add`.
- **Modify** `.github/workflows/ci.yml` — invocación explícita `-n auto`.

---

## Task 1: Helper `swap_dsn_database` (DSN puro)

**Files:**
- Modify: `backend/tests/conftest_ephemeral_db.py` (agregar función junto a `swap_dsn_credentials`)
- Test: `backend/tests/test_dsn_helpers.py` (crear)

- [ ] **Step 1: Write the failing test**

Crear `backend/tests/test_dsn_helpers.py`:

```python
from tests.conftest_ephemeral_db import swap_dsn_database


def test_swap_dsn_database_replaces_last_path_segment():
    dsn = "postgresql+asyncpg://test:test@localhost:5432/test"
    assert (
        swap_dsn_database(dsn, "template_migrated")
        == "postgresql+asyncpg://test:test@localhost:5432/template_migrated"
    )


def test_swap_dsn_database_preserves_credentials_and_host():
    dsn = "postgresql+psycopg2://app_rls:pw@db.internal:6543/old_db"
    assert (
        swap_dsn_database(dsn, "postgres")
        == "postgresql+psycopg2://app_rls:pw@db.internal:6543/postgres"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_dsn_helpers.py -v`
Expected: FAIL con `ImportError: cannot import name 'swap_dsn_database'`.

- [ ] **Step 3: Write minimal implementation**

En `backend/tests/conftest_ephemeral_db.py`, agregar después de `swap_dsn_credentials` (después de la línea 37):

```python
def swap_dsn_database(dsn: str, dbname: str) -> str:
    """Return ``dsn`` with its database (last path segment) replaced by ``dbname``.

    Sibling of ``swap_dsn_credentials``: used to point a maintenance/clone engine
    at a specific database on the same container (e.g. ``postgres`` for DDL,
    ``template_migrated`` for the template, ``test_<n>`` for a per-test clone).
    Assumes no path segments after the database name (true for our DSNs).
    """
    head, _old_db = dsn.rsplit("/", 1)
    return f"{head}/{dbname}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_dsn_helpers.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/camilopiedra/Development/ibkr-control
git add backend/tests/conftest_ephemeral_db.py backend/tests/test_dsn_helpers.py
git commit -m "test(infra): add swap_dsn_database DSN helper"
```

---

## Task 2: Agregar `pytest-xdist` como dev dep

**Files:**
- Modify: `backend/pyproject.toml` (dependency-groups dev)
- Modify: `backend/uv.lock` (regenerado)

- [ ] **Step 1: Agregar la dependencia con uv**

Run: `cd backend && uv add --group dev "pytest-xdist>=3.6"`
Expected: actualiza `pyproject.toml` (lista dev) + `uv.lock`. Output termina en `Resolved ... packages`.

- [ ] **Step 2: Verificar que xdist está disponible**

Run: `cd backend && uv run pytest -n 2 tests/test_dsn_helpers.py -q`
Expected: PASS (2 passed); el flag `-n 2` no produce `error: unrecognized arguments` (prueba que el plugin cargó).

- [ ] **Step 3: Commit**

```bash
cd /Users/camilopiedra/Development/ibkr-control
git add backend/pyproject.toml backend/uv.lock
git commit -m "build(infra): add pytest-xdist dev dependency"
```

---

## Task 3: Fixtures de template + clon (driven por el guard de fidelidad)

**Files:**
- Create: `backend/tests/conftest_template_db.py`
- Create: `backend/tests/test_template_clone_fidelity.py`
- Modify: `backend/tests/conftest.py` (importar los fixtures nuevos)

**Contexto técnico (leer antes de implementar):**
- `alembic/env.py` lee `get_settings().database_url` y lo setea como `sqlalchemy.url`. Por eso `template_db` setea `DATABASE_URL` al DSN del template + `get_settings.cache_clear()` ANTES de `command.upgrade`, y lo deshace después.
- `command.upgrade(cfg, "head")` se llama **síncrono** (env.py corre su propio loop async internamente — igual que `tests/test_migrations.py::test_migrations_apply_cleanly_and_match_metadata` que lo invoca directo).
- `CREATE/DROP DATABASE` no corren en transacción → el `_maintenance_engine` usa `isolation_level="AUTOCOMMIT"`.
- `CREATE DATABASE ... TEMPLATE template_migrated` exige cero conexiones al template → el maintenance engine conecta a la DB `postgres` (no al template), y `command.upgrade` dispone su engine al terminar.
- `postgres_container` (en `conftest.py`, session-scoped, creado con `driver="asyncpg"`) → `get_connection_url()` devuelve `postgresql+asyncpg://test:test@host:port/test`.

- [ ] **Step 1: Write the failing guard test**

Crear `backend/tests/test_template_clone_fidelity.py`:

```python
"""Guard de paridad 1: un clon fresco preserva el contexto de seguridad de prod.

Espeja el boot guard de PR #7: si el clonado dejara de copiar las policies RLS o
el rol app_rls perdiera su no-bypass, estos tests se ponen rojos — no un leak en
prod. Ver docs/specs/2026-06-11-test-infra-worldclass-design.md (Verificacion 3).
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from ibkr_control.db.rls import ORG_SCOPED_TABLES, app_rls_password
from tests.conftest_ephemeral_db import swap_dsn_credentials


async def test_clone_has_force_rls_on_all_org_scoped_tables(test_db):
    """El clon tiene FORCE ROW LEVEL SECURITY en las 17 tablas org-scoped."""
    engine = create_async_engine(test_db)  # test_db = owner DSN del clon
    try:
        async with engine.connect() as conn:
            forced = (
                await conn.execute(
                    text(
                        "SELECT relname FROM pg_class "
                        "WHERE relname = ANY(:names) AND relforcerowsecurity"
                    ).bindparams(names=list(ORG_SCOPED_TABLES))
                )
            ).scalars().all()
        assert set(forced) == set(ORG_SCOPED_TABLES)
    finally:
        await engine.dispose()


async def test_clone_app_rls_role_is_non_bypass(test_db):
    """app_rls en el clon es login no-superuser y no-BYPASSRLS."""
    app_dsn = swap_dsn_credentials(test_db, "app_rls", app_rls_password())
    engine = create_async_engine(app_dsn)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'app_rls'")
                )
            ).one()
        assert row.rolsuper is False
        assert row.rolbypassrls is False
    finally:
        await engine.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_template_clone_fidelity.py -n0 -v`
Expected: FAIL/ERROR con `fixture 'test_db' not found`.

- [ ] **Step 3: Crear el módulo de fixtures**

Crear `backend/tests/conftest_template_db.py`:

```python
"""Provisioning world-class: un template migrado por worker, clon barato por test.

Reemplaza el patron "bootear contenedor + alembic upgrade head POR TEST"
(_migrated_app_db) por una DB ``template_migrated`` migrada UNA vez, y un
``CREATE DATABASE ... TEMPLATE`` por test (~100ms, copia filesystem). Todo el
provisioning (CREATE/DROP DATABASE, alembic) es SINCRONO (psycopg2 + alembic
directo) -> sin fixtures async session-scoped; solo las sesiones del test son
async. Ver docs/specs/2026-06-11-test-infra-worldclass-design.md (D1, D5, D6).
"""

import itertools
import os

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from ibkr_control.config import get_settings
from tests.conftest_ephemeral_db import build_alembic_config, swap_dsn_database

TEMPLATE_DB = "template_migrated"
_JWT = "test-secret-32-chars-minimum-please-ok"

# Contador per-PROCESO. Bajo xdist cada worker es un proceso distinto (arranca en
# 0) y dentro de un worker los tests son seriales -> sin carrera, unico al
# combinar con PYTEST_XDIST_WORKER.
_clone_counter = itertools.count()


def _sync_maint_url(async_dsn: str) -> str:
    """asyncpg DSN del contenedor -> psycopg2 DSN sobre la DB `postgres` (DDL)."""
    return swap_dsn_database(async_dsn.replace("+asyncpg", "+psycopg2"), "postgres")


@pytest.fixture(scope="session")
def _maintenance_engine(postgres_container):
    """Engine SINCRONO AUTOCOMMIT sobre la DB `postgres` para CREATE/DROP DATABASE.

    AUTOCOMMIT porque CREATE/DROP DATABASE no corren en transaccion. Conecta a
    `postgres` (no al `test` del model world, no al template) para no bloquear un
    CREATE ... TEMPLATE ni colisionar con el create_all del model world.
    """
    engine = create_engine(
        _sync_maint_url(postgres_container.get_connection_url()),
        isolation_level="AUTOCOMMIT",
    )
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def template_db(postgres_container, _maintenance_engine):
    """Construye la DB template migrada UNA vez por sesion/worker.

    CREATE DATABASE template_migrated + `alembic upgrade head` (crea el rol
    app_rls, policies FORCE RLS, la fn SECURITY DEFINER y los seeds de
    control-plane). Devuelve el DSN asyncpg OWNER del template. El teardown del
    contenedor lo dropea; sin teardown explicito.
    """
    async_url = postgres_container.get_connection_url()
    with _maintenance_engine.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{TEMPLATE_DB}"'))

    template_dsn = swap_dsn_database(async_url, TEMPLATE_DB)

    mp = pytest.MonkeyPatch()
    try:
        mp.setenv("DATABASE_URL", template_dsn)
        mp.setenv("JWT_SECRET", _JWT)
        get_settings.cache_clear()
        command.upgrade(build_alembic_config(), "head")
    finally:
        mp.undo()
        get_settings.cache_clear()
    return template_dsn


@pytest.fixture
def test_db(postgres_container, template_db, _maintenance_engine, monkeypatch):
    """Una DB pristina, totalmente migrada, clonada del template para UN test.

    CREATE DATABASE test_<worker>_<n> TEMPLATE template_migrated (copia
    filesystem, ~100ms) -> yield del DSN asyncpg OWNER -> DROP ... WITH (FORCE).
    Setea DATABASE_URL para que el boot/lifespan de la app vea esta DB.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    dbname = f"test_{worker}_{next(_clone_counter)}"

    with _maintenance_engine.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{dbname}" TEMPLATE "{TEMPLATE_DB}"'))

    owner_dsn = swap_dsn_database(postgres_container.get_connection_url(), dbname)
    monkeypatch.setenv("DATABASE_URL", owner_dsn)
    monkeypatch.setenv("JWT_SECRET", _JWT)
    get_settings.cache_clear()

    try:
        yield owner_dsn
    finally:
        with _maintenance_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)'))
```

- [ ] **Step 4: Importar los fixtures en `conftest.py`**

En `backend/tests/conftest.py`, después del bloque de import de `conftest_ephemeral_db` (línea 26-33), agregar:

```python
from tests.conftest_template_db import (  # noqa: F401
    _maintenance_engine,
    template_db,
    test_db,
)
```

- [ ] **Step 5: Run guard test to verify it passes**

Run: `cd backend && uv run pytest tests/test_template_clone_fidelity.py -n0 -v`
Expected: PASS (2 passed). Si falla con `source database "template_migrated" is being accessed by other users` en el primer clon, significa que `command.upgrade` dejó una conexión al template: agregar, al final de `template_db` (antes del `return`, fuera del `mp`), un terminate defensivo:
`with _maintenance_engine.connect() as c: c.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:d"). bindparams(d=TEMPLATE_DB))`.

- [ ] **Step 6: Ruff + Commit**

Run: `cd backend && uv run ruff check tests/conftest_template_db.py tests/test_template_clone_fidelity.py tests/conftest.py && uv run ruff format tests/conftest_template_db.py tests/test_template_clone_fidelity.py`
Expected: `All checks passed!`

```bash
cd /Users/camilopiedra/Development/ibkr-control
git add backend/tests/conftest_template_db.py backend/tests/test_template_clone_fidelity.py backend/tests/conftest.py
git commit -m "test(infra): template DB + clone-per-test fixtures + clone-fidelity guard"
```

---

## Task 4: Guard de paridad 2 — fail-closed (app_rls sin contexto → 0 filas)

**Files:**
- Create: `backend/tests/test_rls_fail_closed.py`

- [ ] **Step 1: Write the failing test**

Crear `backend/tests/test_rls_fail_closed.py`:

```python
"""Guard de paridad 2: el default app_rls es fail-closed (default-deny sin contexto).

Siembra una fila como owner (con contexto) y luego la consulta como app_rls SIN
setear app.current_org -> debe devolver 0 filas (NULLIF('','')::bigint = NULL ->
organization_id = NULL nunca es true). Prueba que olvidar el org_context FALLA
ruidoso, no filtra. Ver spec (Verificacion 4) + memoria rls-runtime-vs-test-parity.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ibkr_control.db.models.accounts import Account
from ibkr_control.db.models.organizations import Organization
from ibkr_control.db.rls import app_rls_password
from tests.conftest_ephemeral_db import swap_dsn_credentials


async def test_app_rls_without_context_sees_zero_rows(test_db):
    # Seed: una cuenta como OWNER (set_config defensivo para el WITH CHECK).
    owner_engine = create_async_engine(test_db)
    owner_sm = async_sessionmaker(owner_engine, expire_on_commit=False, class_=AsyncSession)
    async with owner_sm() as s:
        org = Organization(type="personal", name="FailClosed Org")
        s.add(org)
        await s.flush()
        await s.execute(
            text("SELECT set_config('app.current_org', :o, true)").bindparams(o=str(org.id))
        )
        s.add(Account(ibkr_account_id="U00000000", organization_id=org.id, currency="USD"))
        await s.commit()
    await owner_engine.dispose()

    # Query como app_rls SIN contexto -> default-deny -> 0 filas.
    app_dsn = swap_dsn_credentials(test_db, "app_rls", app_rls_password())
    app_engine = create_async_engine(app_dsn)
    app_sm = async_sessionmaker(app_engine, expire_on_commit=False, class_=AsyncSession)
    async with app_sm() as s:
        count = (await s.execute(text("SELECT count(*) FROM accounts"))).scalar_one()
    await app_engine.dispose()

    assert count == 0
```

- [ ] **Step 2: Run test to verify it fails (red) — primero confirmá que el assert es real**

Run: `cd backend && uv run pytest tests/test_rls_fail_closed.py -n0 -v`
Expected: PASS directamente (la infra de Task 3 ya existe y el comportamiento RLS ya está en el schema migrado). Este guard no maneja código nuevo de app — bloquea una REGRESIÓN futura. Para confirmar que el assert tiene dientes, cambiá temporalmente `assert count == 0` por `assert count == 1`, corré (debe FALLAR mostrando `count == 0`), y revertí a `== 0`.

- [ ] **Step 3: (revert ya hecho en Step 2) — correr verde**

Run: `cd backend && uv run pytest tests/test_rls_fail_closed.py -n0 -v`
Expected: PASS (1 passed).

- [ ] **Step 4: Ruff + Commit**

Run: `cd backend && uv run ruff check tests/test_rls_fail_closed.py && uv run ruff format tests/test_rls_fail_closed.py`
Expected: `All checks passed!`

```bash
cd /Users/camilopiedra/Development/ibkr-control
git add backend/tests/test_rls_fail_closed.py
git commit -m "test(infra): fail-closed guard (app_rls without context sees zero rows)"
```

---

## Task 5: Repointar el endpoint world a `test_db` y eliminar `_migrated_app_db`

**Files:**
- Modify: `backend/tests/conftest.py` (fixtures `app_with_db`, `app_owner_engine`, `app_rls_db_session`; eliminar `_migrated_app_db`)

**Contexto:** `_migrated_app_db` (conftest.py:51-86) hoy bootea un contenedor nuevo + `alembic upgrade head` por test y yield­ea el owner DSN. `test_db` yield­ea el MISMO contrato (owner asyncpg DSN de una DB migrada, con `DATABASE_URL` ya seteado). El cambio es repointar los 3 consumidores y borrar `_migrated_app_db`.

- [ ] **Step 1: Repointar `app_with_db`**

En `backend/tests/conftest.py`, en `app_with_db` (línea ~89-114), cambiar la firma y el cuerpo:

```python
@pytest.fixture
async def app_with_db(test_db, monkeypatch):
    """The endpoint app, wired to a MIGRATED clone DB and connecting as ``app_rls``."""
    owner_url = test_db
    app_dsn = swap_dsn_credentials(owner_url, "app_rls", app_rls_password())
```

(El resto del cuerpo — `create_async_engine(app_dsn)`, override de `get_async_session`, `create_app()` — queda IGUAL.)

- [ ] **Step 2: Repointar `app_owner_engine` y `app_rls_db_session`**

En `app_owner_engine` (línea ~185-202): cambiar `async def app_owner_engine(_migrated_app_db):` por `async def app_owner_engine(test_db):` y, en el cuerpo, `create_async_engine(_migrated_app_db, ...)` por `create_async_engine(test_db, ...)`.

En `app_rls_db_session` (línea ~205-219): cambiar `async def app_rls_db_session(_migrated_app_db):` por `async def app_rls_db_session(test_db):` y `swap_dsn_credentials(_migrated_app_db, ...)` por `swap_dsn_credentials(test_db, ...)`.

- [ ] **Step 3: Eliminar `_migrated_app_db`**

Borrar el fixture `_migrated_app_db` completo de `conftest.py` (línea ~51-86, el bloque `@pytest.fixture` + `async def _migrated_app_db(monkeypatch):` y su docstring/cuerpo). Eliminar también el import ahora-huérfano si quedara (`from alembic import command` en conftest.py sigue usándose? — verificar: si `_migrated_app_db` era el único usuario de `command` en conftest.py, quitar ese import; si `db_session` u otro lo usa, dejarlo).

- [ ] **Step 4: Correr el slice endpoint completo (serial, para aislar el delta por-test)**

Run: `cd backend && uv run pytest tests/api tests/test_settings.py -n0 -q`
Expected: PASS, mismo conteo que el baseline del slice (118 passed). Sin `fixture '_migrated_app_db' not found` en ningún lado (grep de seguridad: `grep -rn _migrated_app_db backend/tests` debe devolver vacío).

- [ ] **Step 5: Medir el delta del slice**

Run: `cd backend && uv run pytest tests/api tests/test_settings.py -n0 -q --durations=5`
Expected: el wall-clock del slice cae de ~200s a la franja de ~15-40s (serial); las duraciones top ya NO son `setup` de ~1.7s. Anotar el número para el PR.

- [ ] **Step 6: Ruff + Commit**

Run: `cd backend && uv run ruff check tests/conftest.py && uv run ruff format tests/conftest.py`
Expected: `All checks passed!`

```bash
cd /Users/camilopiedra/Development/ibkr-control
git add backend/tests/conftest.py
git commit -m "test(infra): repoint endpoint fixtures to test_db clone, drop _migrated_app_db"
```

---

## Task 6: xdist por defecto + suite completa verde + medición + PR

**Files:**
- Modify: `backend/pyproject.toml` (`[tool.pytest.ini_options].addopts`)
- Modify: `.github/workflows/ci.yml` (línea 27)

- [ ] **Step 1: `-n auto` por defecto en pyproject**

En `backend/pyproject.toml`, en `[tool.pytest.ini_options]` (línea ~37-39), agregar `addopts`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
# Paralelo por defecto (local + CI). Override para debug/medición serial: -n0.
# Tope opcional de workers via env PYTEST_XDIST_AUTO_NUM_WORKERS (no pin numérico:
# con aislamiento real por test, el worker-count no afecta correctitud, solo
# velocidad — pinear envejece cuando GitHub cambia los runners).
addopts = ["-n", "auto"]
```

- [ ] **Step 2: CI explícito**

En `.github/workflows/ci.yml`, cambiar la línea 27 de:
```yaml
          uv run pytest -v
```
a:
```yaml
          uv run pytest -n auto
```

- [ ] **Step 3: Suite completa verde en paralelo**

Run: `cd backend && uv run pytest -q`
Expected: PASS, **423 passed** (mismo conteo que el baseline; el `addopts` aplica `-n auto`). Sin fallos por colisión de nombres de DB ni por contención.

- [ ] **Step 4: Medir el wall-clock total**

Run: `cd backend && time uv run pytest -q`
Expected: wall-clock total en la franja de ~10-30s (vs baseline 311.70s). Anotar el número.

Run (control serial, opcional): `cd backend && uv run pytest -n0 -q` para comparar el piso serial con template-clone.

- [ ] **Step 5: Sanity check del model world intacto**

Run: `grep -rn "create_all\|postgres_container\|_migrated_app_db" backend/tests/conftest.py`
Expected: `create_all` y `postgres_container` SIGUEN presentes (model world intacto); `_migrated_app_db` NO aparece (eliminado). Confirma que PR-A no tocó el model world.

- [ ] **Step 6: Commit final**

```bash
cd /Users/camilopiedra/Development/ibkr-control
git add backend/pyproject.toml .github/workflows/ci.yml
git commit -m "ci(infra): run pytest -n auto (template-clone makes per-test isolation cheap)"
```

- [ ] **Step 7: Push + PR** (confirmar con el usuario antes, per política del repo)

```bash
cd /Users/camilopiedra/Development/ibkr-control
git push -u origin test-infra/worldclass-db-isolation
gh pr create --title "test infra PR-A: template DB + clon por test (endpoint world)" \
  --body "PR-A del spec docs/specs/2026-06-11-test-infra-worldclass-design.md. Endpoint world: ~200s -> ~Xs; suite completa 311.70s -> ~Ys. Model world intacto (PR-B). Guards de paridad: clone-fidelity + fail-closed."
```

Esperar CI verde (`backend` + `frontend`) antes de merge.

---

## Self-Review (cubierto en este plan)

- **Spec coverage:** D1 (clon de template) → Task 3; D5 (un contenedor, borra `_migrated_app_db` + el parche de grants NO se toca en PR-A porque vive en el model world create_all — correcto, es PR-B) → Task 5; D6 (`-n auto`, namespacing por worker) → Task 3 (`PYTEST_XDIST_WORKER`) + Task 6; Verificación 3 (clone-fidelity) → Task 3; Verificación 4 (fail-closed) → Task 4; Verificación 5 (wall-clock medido) → Task 5 Step 5 + Task 6 Step 4. PR-A explícitamente NO converge el model world (PR-B) ni `ephemeral_*` — documentado en Scope OUT.
- **Placeholder scan:** sin TBD/TODO; todo el código de fixtures + guards está completo e inline.
- **Type/contract consistency:** `test_db` yield­ea owner asyncpg DSN (mismo contrato que el viejo `_migrated_app_db`); `swap_dsn_database`/`swap_dsn_credentials`/`build_alembic_config` referencian las firmas reales de `conftest_ephemeral_db.py`; `ORG_SCOPED_TABLES`/`app_rls_password` importados de `ibkr_control.db.rls` (existen).
