# Test infra PR-C — Flip del model world a app_rls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el "model world" de tests (`db_session` + `db_engine`) deje de conectar como OWNER (que bypassa RLS) y conecte como el rol no-bypass **`app_rls`** con `org_context` — así RLS se enforce­a en los tests del model world igual que en prod (un persister que escriba el `organization_id` equivocado se rechaza en el test, no solo en prod). El OWNER queda como **excepción nombrada** (`owner_session`/`owner_engine`) para los casos genuinamente cross-tenant / control-plane / tenant-wipe.

**Architecture:** `db_session`/`db_engine` pasan a app_rls. El contexto org se **centraliza**: el fixture `sample_org` (del que cuelga toda la cadena `sample_*`) llama `set_session_org_context(db_session, org_id=org.id, ...)` tras crear el org, y el listener `after_begin` de `db/rls.py` re-aplica el GUC en cada transacción de la sesión (self-healing, sobrevive commits intra-test). Los ~95 tests single-tenant que pasan `organization_id=` al persister quedan cubiertos por ese único cambio. Los ~7 tests cross-tenant/control-plane se rutean a `owner_session`/`owner_engine`. Los ~50 RLS-irrelevant (DDL, control-plane TRM, advisory locks, identity-only) corren bajo app_rls sin contexto sin cambios.

**Tech Stack:** pytest + pytest-asyncio + pytest-xdist + testcontainers-postgres + SQLAlchemy async (asyncpg) + Postgres RLS (FORCE) + el rol `app_rls`.

**Spec:** `docs/specs/2026-06-11-test-infra-worldclass-design.md` (§Rollout PR-C; D2 app_rls default fail-closed, D4 principio de seeding, owner excepción nombrada).

**Branch:** crear `test-infra/pr-c-app-rls-flip` desde `main` (post-merge de PR-A #15 + PR-B #17). Repo PÚBLICO, `main` PROTEGIDO → branch → PR → CI verde → merge.

**Naturaleza:** refactor de fixtures + routing por-test, validado por la suite (428 tests). El criterio es 428 verde con el model world bajo app_rls; un rojo es señal legítima (un test dependía del bypass owner) → se rutea a `owner_session` o se le agrega contexto, NO se esconde.

**Mecanismo de contexto (leer antes de empezar):** `ibkr_control.db.rls` expone `set_session_org_context(session, *, org_id, user_id)` (stashea en `session.info`) + un listener `@event.listens_for(Session, "after_begin")` que re-aplica `SET LOCAL app.current_org/current_user` en CADA transacción nueva de esa sesión. También `apply_org_context(session, *, org_id, user_id)` (await, aplica a la transacción ABIERTA actual). El patrón robusto para una sesión larga (`db_session`) es `set_session_org_context` (cubre transacciones futuras vía el listener); como `sample_org` COMMITEA el org antes de devolver, la siguiente transacción (Party de `sample_user`, writes del test) ya nace con el contexto aplicado por el listener. Las tablas de identidad `organizations`/`users`/`memberships`/`user_settings` NO tienen RLS → app_rls las inserta sin contexto; `parties` SÍ tiene RLS (necesita contexto antes del insert).

**Scope OUT (NO tocar):** `app_with_db`/`client`/`test_db`/`template_db`/`_maintenance_engine`/`app_rls_db_session` (PR-A/B); los fixtures `ephemeral_*`; los 4 falsos positivos que ya usan `app_rls_db_session` (`test_app_runs_under_rls`, `test_apscheduler_jobs_grants`, `test_rls_context_survives_commit`, `test_runtime_role_guard`).

**Convención:** desde `backend/`. `addopts` fuerza `-n auto`; `-n0` para medir/leer fallos seriales. Ruff: `uv run ruff check . && uv run ruff format --check .`.

---

## Routing de tests (del bucketing — mapa para el triage)

| Bucket | Routing | Archivos / tests |
|---|---|---|
| **A — app_rls + contexto single-tenant** (vía `sample_org`) | cubierto por el contexto central en `sample_org`; NADA por-test salvo los inline-org | `tests/ingest/flex/*` (test_persister, _idempotent, _instruments, test_restatements, _f_filter, test_counterparties_model, test_upsert_helpers, test_job), `tests/ingest/test_hash_dedup` (menos el cross-org), `tests/ingest/test_log`, `tests/test_poison_reset_script`, `tests/test_flex_poison_recovery`, `tests/test_db_hardening` (los 2 single-tenant), `tests/test_provision_org`, los tests constraint-writing de `test_phase2_*_migration` |
| **A-inline — crean org inline** | setean contexto ellos mismos (o son identity-only) | `tests/test_identity_models.py` (parties/accounts/participations inline), `tests/ingest/flex/test_persister.py::test_persist_stamps_organization_id_on_all_rows`, `tests/api/test_org_context.py` (identity-only → debería pasar sin contexto) |
| **B — OWNER (excepción nombrada)** | `owner_session` / `owner_engine` | b1 cross-tenant: `tests/test_flex_isolation_multi_user.py` (3 tests con `second_sample_user`), `tests/ingest/test_hash_dedup.py::test_check_hash_status_absent_when_hash_exists_for_different_org`; b2 control-plane: `tests/test_scheduler.py::test_run_flex_for_all_orgs_iterates_distinct_orgs`; b4 tenant-wipe: `tests/test_db_hardening.py::test_delete_organization_cascades_full_tenant`. **`second_sample_user` → owner_session.** |
| **C — ya RLS-aware** | sin cambios | `tests/test_account_multihome.py` (3 tests vía `rls_session_factory`) |
| **RLS-irrelevant** | sin cambios (app_rls sin contexto OK) | `tests/ingest/trm/*`, `tests/ingest/test_lock.py`, `tests/test_phase2_trm_migration.py`, las partes `to_regclass`/metadata de los `test_phase2_*` |

---

## Task 1: Stage del OWNER exception (sin flip todavía)

Introduce las excepciones nombradas y rutea los Bucket B a ellas MIENTRAS `db_session`/`db_engine` siguen siendo owner — así no cambia el comportamiento (los Bucket B solo se mueven a un fixture owner explícito) y la suite queda verde antes del flip riesgoso.

**Files:** Modify `backend/tests/conftest.py`; modify the Bucket B test files + `second_sample_user`.

- [ ] **Step 1: Crear `owner_session` + renombrar `app_owner_engine` → `owner_engine`**

En `backend/tests/conftest.py`:

(a) Agregar un fixture `owner_session` (sesión owner sobre `test_db` — lo que `db_session` es HOY):
```python
@pytest.fixture
async def owner_session(test_db):  # noqa: F811 — test_db es fixture importada, no redefinida
    """Sesion OWNER (bypass RLS) sobre un clon migrado — EXCEPCION nombrada.

    Para los tests que genuinamente necesitan bypassear RLS: seeding cross-tenant
    (multiples orgs), operaciones control-plane (enumeracion del cron), y
    tenant-wipe (DELETE FROM organizations + cascade). El default del model world
    es ``db_session`` (app_rls); esto es la salida explicita per D2/D4 del spec.
    """
    engine = create_async_engine(test_db)
    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with session_maker() as session:
            yield session
    finally:
        await engine.dispose()
```

(b) Renombrar el fixture `app_owner_engine` → `owner_engine` (su cuerpo ya es "owner engine sobre test_db"; solo cambia el nombre + el docstring para que sea genérico, no endpoint-flavored). Actualizar sus consumidores: `auth_headers_with_org` y `second_auth_headers_with_org` (cambiar el parámetro `app_owner_engine` → `owner_engine` en sus firmas).

- [ ] **Step 2: Rutear `second_sample_user` a owner**

`second_sample_user` (en conftest.py) crea un org SEPARADO → es inherentemente cross-tenant. Cambiar su firma de `(db_session, ...)` a `(owner_session)` y reemplazar `db_session` por `owner_session` en su cuerpo. (Crea Organization/User/Membership/Party para su propio org; como owner bypassa RLS, el Party inserta sin contexto.)

- [ ] **Step 3: Rutear los tests Bucket B a owner_session/owner_engine**

Para CADA uno, cambiar el/los parámetro(s) de fixture de `db_session`/`db_engine` a `owner_session`/`owner_engine` (y nada más — el cuerpo no cambia, siguen siendo owner):
- `tests/test_flex_isolation_multi_user.py`: los 3 tests que usan `second_sample_user` (`test_persister_isolation_two_orgs_sequential`, `test_advisory_lock_is_per_org`, `test_same_xml_hash_two_orgs_no_unique_collision`) → `owner_engine` donde usan `db_engine`. (El 4º, `test_advisory_lock_same_org_two_sessions_conflict`, es single-org advisory-lock → dejar en `db_engine`; se reevalúa en Task 3.)
- `tests/ingest/test_hash_dedup.py::test_check_hash_status_absent_when_hash_exists_for_different_org` → `owner_session`.
- `tests/test_scheduler.py::test_run_flex_for_all_orgs_iterates_distinct_orgs` → `owner_session`.
- `tests/test_db_hardening.py::test_delete_organization_cascades_full_tenant` → `owner_session`.

- [ ] **Step 4: Suite completa verde (sigue todo owner)**

Run: `cd backend && uv run pytest -q`
Expected: **428 passed**. Como `db_session`/`db_engine` siguen siendo owner y los Bucket B solo se movieron a fixtures owner equivalentes, NADA de comportamiento cambió. Si hay rojo, es un consumidor de `app_owner_engine` que no actualizaste al renombre → arreglar.

- [ ] **Step 5: Ruff + Commit**

Run: `cd backend && uv run ruff check . && uv run ruff format --check .` → `All checks passed!`
```bash
cd /Users/camilopiedra/Development/ibkr-control
git add backend/tests/
git commit -m "test(infra): owner_session/owner_engine como excepcion nombrada + rutear Bucket B"
```

---

## Task 2: Flip `db_session` → app_rls + contexto central en `sample_org`

**Files:** Modify `backend/tests/conftest.py` (`db_session`, `sample_org`); possibly the A-inline test files.

- [ ] **Step 1: Flip `db_session` a app_rls**

Reemplazar el cuerpo de `db_session` para conectar como `app_rls` (swap de credenciales sobre `test_db`):
```python
@pytest.fixture
async def db_session(test_db):  # noqa: F811 — test_db es fixture importada, no redefinida
    """Sesion del model world como ``app_rls`` (NO bypass) sobre un clon migrado.

    RLS se enforce­a igual que en prod: cada query org-scoped se filtra por
    ``app.current_org``. El contexto lo centraliza ``sample_org`` (via
    ``set_session_org_context`` + el listener ``after_begin`` de db/rls.py), asi
    que los tests single-tenant que cuelgan de ``sample_*`` quedan scopeados sin
    boilerplate. Tests sin ``sample_org`` que escriben filas org-scoped deben
    setear contexto ellos mismos; los cross-tenant/control-plane usan
    ``owner_session``. Default fail-closed: sin contexto, las tablas org-scoped
    devuelven 0 filas / el WITH CHECK rechaza el insert.
    """
    app_dsn = swap_dsn_credentials(test_db, "app_rls", app_rls_password())
    engine = create_async_engine(app_dsn)
    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with session_maker() as session:
            yield session
    finally:
        await engine.dispose()
```

- [ ] **Step 2: Centralizar el contexto en `sample_org`**

En `sample_org` (que hace `add(org); commit(); refresh(org)`), tras el commit del org agregar el seteo de contexto para el resto de la sesión:
```python
    from ibkr_control.db.rls import set_session_org_context

    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)
    set_session_org_context(db_session, org_id=org.id, user_id=None)
    return org
```
El listener `after_begin` aplicará el GUC en cada transacción siguiente de `db_session` (el insert del Party de `sample_user`, los writes del test). `organizations` no tiene RLS, así que el `add(org)` previo al seteo funciona.

- [ ] **Step 3: Correr los Bucket A de mayor valor (persister/ingest, serial)**

Run: `cd backend && uv run pytest tests/ingest/flex/test_persister.py tests/ingest/flex/test_persister_idempotent.py tests/ingest/flex/test_restatements.py tests/ingest/test_log.py tests/test_flex_poison_recovery.py -n0 -q`
Expected: PASS. Estos pasan `organization_id=sample_org.id` al persister y ahora el contexto está seteado → los writes pasan el WITH CHECK y las lecturas ven sus filas. Si un rojo aparece:
  - **WITH CHECK violation al insertar:** el contexto no se aplicó antes del write → verificá que el test usa `sample_org` (no crea org inline) y que el insert ocurre en una transacción posterior al `set_session_org_context`. Si crea org inline → es A-inline (Step 5).
  - **0 filas al leer lo que se escribió:** el read corre en una sesión distinta sin contexto (p.ej. un re-read vía `db_engine`) → ese caso es Task 3 (db_engine). Si es la misma `db_session`, el listener debería cubrirlo; investigá.

- [ ] **Step 4: Correr TODO el model world (serial)**

Run: `cd backend && uv run pytest tests/ingest tests/test_identity_models.py tests/test_provision_org.py tests/test_db_hardening.py tests/test_account_multihome.py tests/test_hash_dedup.py tests/test_poison_reset_script.py tests/test_flex_poison_recovery.py tests/test_flex_isolation_multi_user.py tests/test_scheduler.py tests/api/test_org_context.py tests/test_phase2_identity_migration.py tests/test_phase2_flex_raw_migration.py tests/test_phase2_ingest_log_migration.py tests/test_phase2_dividend_accruals_migration.py tests/test_phase2_trm_migration.py -n0 -q`
Expected: mayormente PASS. Triage de los rojos restantes:
  - **A-inline** (`test_identity_models.py`, `test_persist_stamps_organization_id_on_all_rows`): crean su org inline → agregarles, tras crear el org, `set_session_org_context(db_session, org_id=<org>.id, user_id=None)` (o `await apply_org_context(db_session, org_id=...)` si el insert es en la misma transacción abierta). Para `test_identity_models`, el test de `access_grants` puede necesitar `user_id` también.
  - **`test_org_context.py`**: identity-only (users/orgs/memberships sin RLS) → debería pasar sin contexto. Si rompe, investigá (no debería tocar tablas org-scoped).
  - **RLS-irrelevant** (`trm/*`, `test_lock`, las partes DDL de los migration tests): no deberían romper (control-plane sin RLS / metadata). Si una parte constraint-writing de un `test_phase2_*` escribe una fila org-scoped vía `sample_org`, ya tiene contexto por Step 2.
  - Si un rojo no encaja, escalá BLOCKED con el output.

- [ ] **Step 5: Arreglar los A-inline detectados** (código concreto por test, según Step 4). Cada fix = agregar el seteo de contexto tras crear el org inline. Mantené los cambios mínimos.

- [ ] **Step 6: Suite completa (paralelo) + conteo**

Run: `cd backend && uv run pytest -q`
Expected: **428 passed**. (db_engine sigue owner; sus consumidores Bucket A que re-leen vía db_engine podrían fallar — si es así, es Task 3; anotá cuáles y seguí, o si bloquean el conteo, hacé Task 3 a continuación.)

- [ ] **Step 7: Ruff + Commit**

Run: `cd backend && uv run ruff check . && uv run ruff format --check .` → `All checks passed!`
```bash
cd /Users/camilopiedra/Development/ibkr-control
git add backend/tests/
git commit -m "test(infra): db_session como app_rls + contexto central en sample_org (RLS enforced)"
```

---

## Task 3: Flip `db_engine` → app_rls

**Files:** Modify `backend/tests/conftest.py` (`db_engine`); possibly its remaining consumers.

- [ ] **Step 1: Identificar consumidores restantes de `db_engine`**

Run: `cd backend && grep -rln "db_engine" tests/`
Tras Task 1, los cross-tenant ya usan `owner_engine`. Los que quedan en `db_engine` deberían ser: `tests/ingest/test_lock.py` (advisory locks, RLS-irrelevant), `tests/test_flex_poison_recovery.py` (re-read), `tests/ingest/flex/test_job.py`, y el single-org `test_advisory_lock_same_org_two_sessions_conflict`.

- [ ] **Step 2: Flip `db_engine` a app_rls**

Reemplazar el cuerpo de `db_engine` para conectar como `app_rls`:
```python
@pytest.fixture
async def db_engine(test_db):  # noqa: F811 — test_db es fixture importada, no redefinida
    """Engine ``app_rls`` (NO bypass) sobre el clon migrado, para tests que abren
    multiples sesiones concurrentes (p.ej. contencion de advisory locks).

    Multi-conexion real; cada sesion abierta sobre este engine arranca SIN
    contexto org (fail-closed) -> el consumidor debe setear ``app.current_org`` por
    sesion si toca tablas org-scoped. Los tests cross-tenant usan ``owner_engine``."""
    app_dsn = swap_dsn_credentials(test_db, "app_rls", app_rls_password())
    engine = create_async_engine(app_dsn, echo=False)
    try:
        yield engine
    finally:
        await engine.dispose()
```

- [ ] **Step 3: Correr los consumidores de `db_engine` (serial) + triage**

Run: `cd backend && uv run pytest $(grep -rln "db_engine" tests/ | tr '\n' ' ') -n0 -v`
Expected/triage:
  - **`test_lock.py`** (advisory locks por `scope_id` entero, no toca tablas org-scoped): PASS sin contexto.
  - **`test_flex_poison_recovery.py`** (re-lee la FlexImport poison vía una sesión fresca de `db_engine`): bajo app_rls, esa sesión necesita contexto. Agregar `set_session_org_context(<session>, org_id=sample_org.id, user_id=None)` (o `apply_org_context`) a la sesión que abre desde `db_engine` antes de la query. `flex_imports` es org-scoped.
  - **`test_job.py`**: el seeding vía `db_session` ya tiene contexto (Task 2); `flex_job.run` setea su propio contexto. Si abre sesiones vía `db_engine` para re-leer, mismo fix que poison_recovery.
  - **`test_advisory_lock_same_org_two_sessions_conflict`**: advisory lock single-org, no toca tablas org-scoped → PASS.

- [ ] **Step 4: Suite completa (paralelo) verde**

Run: `cd backend && uv run pytest -q`
Expected: **428 passed**. Ahora TODO el model world corre bajo app_rls (owner solo vía las excepciones nombradas).

- [ ] **Step 5: Ruff + Commit**

Run: `cd backend && uv run ruff check . && uv run ruff format --check .` → `All checks passed!`
```bash
cd /Users/camilopiedra/Development/ibkr-control
git add backend/tests/
git commit -m "test(infra): db_engine como app_rls; re-reads setean contexto por sesion"
```

---

## Task 4: Guard de RLS-enforced + suite + medición + PR

**Files:** Create `backend/tests/test_model_world_under_rls.py`; modify nothing else.

- [ ] **Step 1: Guard "el model world corre bajo app_rls"**

Crear `backend/tests/test_model_world_under_rls.py`:
```python
"""Guard: el model world (db_session) corre como app_rls, no como owner bypass.

Lockea el flip de PR-C: si alguien revierte db_session a owner, este test se pone
rojo. Espeja el guard de runtime de PR #7 a nivel fixture.
"""

from sqlalchemy import text


async def test_db_session_connects_as_app_rls(db_session):
    role = (await db_session.execute(text("SELECT current_user"))).scalar_one()
    assert role == "app_rls"


async def test_db_session_is_fail_closed_without_context(test_db):
    # Sin sample_org (sin contexto), una query org-scoped por db_session ve 0 filas.
    # Usamos test_db directo + app_rls para no arrastrar el contexto de sample_org.
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
    from ibkr_control.db.rls import app_rls_password
    from tests.conftest_ephemeral_db import swap_dsn_credentials

    app_dsn = swap_dsn_credentials(test_db, "app_rls", app_rls_password())
    engine = create_async_engine(app_dsn)
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with sm() as s:
        count = (await s.execute(text("SELECT count(*) FROM accounts"))).scalar_one()
    await engine.dispose()
    assert count == 0
```

- [ ] **Step 2: Verificar el guard (teeth)**

Run: `cd backend && uv run pytest tests/test_model_world_under_rls.py -n0 -v`
Expected: PASS (2). Confirmá que `test_db_session_connects_as_app_rls` tiene dientes: temporalmente cambiá el assert a `== "test"` (el owner), corré → debe FALLAR mostrando `app_rls`, revertí.

- [ ] **Step 3: `owner_session` sigue siendo owner (contraprueba)**

Agregar al mismo archivo:
```python
async def test_owner_session_connects_as_owner(owner_session):
    role = (await owner_session.execute(text("SELECT current_user"))).scalar_one()
    assert role != "app_rls"  # el OWNER del contenedor (superuser), bypass RLS
```
Run: `cd backend && uv run pytest tests/test_model_world_under_rls.py -n0 -v` → PASS (3).

- [ ] **Step 4: Suite completa verde + conteo final**

Run: `cd backend && uv run pytest -q`
Expected: **431 passed** (428 + 3 guards nuevos).

- [ ] **Step 5: Medición**

Run: `cd backend && uv run pytest -n0 -q` (serial) y `cd backend && time uv run pytest -q` (paralelo). Anotá ambos (no debería cambiar mucho vs PR-B: ~88s serial / ~25s paralelo; el swap de credenciales es gratis).

- [ ] **Step 6: Sanity — el model world está bajo app_rls**

Run: `cd backend && grep -n "swap_dsn_credentials\|create_async_engine(test_db)" tests/conftest.py`
Expected: `db_session` y `db_engine` usan `swap_dsn_credentials(test_db, "app_rls", ...)`; solo `owner_session`/`owner_engine` usan `create_async_engine(test_db)` directo (owner).

- [ ] **Step 7: Ruff final + Commit**

Run: `cd backend && uv run ruff check . && uv run ruff format --check .` → `All checks passed!`
```bash
cd /Users/camilopiedra/Development/ibkr-control
git add backend/tests/test_model_world_under_rls.py
git commit -m "test(infra): guard del model world bajo app_rls (fail-closed + owner_session contraprueba)"
```

- [ ] **Step 8: Push + PR** (confirmar con el usuario antes, per política del repo)

```bash
cd /Users/camilopiedra/Development/ibkr-control
git push -u origin test-infra/pr-c-app-rls-flip
gh pr create --title "test infra PR-C: model world bajo app_rls (RLS enforced)" \
  --body "PR-C (ultimo del track) del spec docs/specs/2026-06-11-test-infra-worldclass-design.md. db_session/db_engine pasan de owner a app_rls + org_context (centralizado en sample_org); owner_session/owner_engine como excepcion nombrada para cross-tenant/control-plane/tenant-wipe. RLS enforced en el model world igual que en prod. Guard nuevo. Suite 431 verde."
```

Esperar CI verde antes de merge.

---

## Self-Review (cubierto en este plan)

- **Spec coverage:** D2 (app_rls default fail-closed) → Task 2/3 (db_session/db_engine flip) + Task 4 (guard fail-closed). D4 (owner excepción nombrada) → Task 1 (`owner_session`/`owner_engine` + routing Bucket B). La convergencia "una fuente de schema + rol real" del programa queda completa con PR-A+B+C.
- **Placeholder scan:** sin TBD/TODO; el código de los fixtures (owner_session, db_session/db_engine app_rls, contexto en sample_org) y los guards está completo e inline. El triage por-test (A-inline, re-reads) está acotado con el routing del bucketing + reglas concretas + "escalá si no encaja" — el set exacto de A-inline se confirma corriendo, como en PR-B.
- **Type/contract consistency:** `set_session_org_context`/`apply_org_context`/`swap_dsn_credentials`/`app_rls_password` son símbolos reales de `ibkr_control.db.rls` / `conftest_ephemeral_db`; `owner_session`/`owner_engine` espejan el patrón de `app_rls_db_session`; `# noqa: F811` consistente con PR-A/B. El renombre `app_owner_engine`→`owner_engine` actualiza sus 2 consumidores (auth_headers_with_org/_2).
- **Riesgo conocido:** Task 1 deja la suite verde (staging sin flip); Task 2 es el de mayor blast radius (el flip + triage A-inline); Task 3 cierra los re-reads vía db_engine; Task 4 lockea con guards. Cada task commitea verde (a diferencia de PR-B, acá NO hay commit intermedio rojo: el staging de Task 1 precede al flip).
