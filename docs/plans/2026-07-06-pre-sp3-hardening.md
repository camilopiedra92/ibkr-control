# Pre-SP3 Hardening Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cerrar 7 guards estructurales fail-closed (HD-1..HD-7) que el `pre-deploy-hardening-backlog` subestimó bajo la vieja calibración de "3 usuarios", antes de que Phase 3 los pise — y registrar formalmente las decisiones diferidas DEF-B (escala→SP5/SP7) y DEF-6 (DML identidad→SP4).

**Architecture:** Todo el pass es **schema-free** (cero migraciones, no toca el baseline `a9977ac077e5`). Son tests aditivos derivados del metadata/reflexión, dos refactors que uniforman write-paths al patrón `ON CONFLICT + re-select` ya endurecido, un helper compartido de participations, y un boot guard fail-loud. Ejecución subagent-driven (implementer + spec-review + code-quality-review por task, review holístico final), TDD estricto.

**Tech Stack:** Python 3.12 · FastAPI · SQLAlchemy 2.x async · asyncpg · Postgres 16 (RLS) · pytest + pytest-xdist · Alembic.

## Global Constraints

- **Spec SSOT:** `docs/specs/2026-07-06-pre-sp3-hardening-design.md` (HD-1..HD-7, DEF-B, DEF-6). Ante duda, el spec manda.
- **Cero migraciones.** Ningún task agrega/modifica DDL. El marcador `info={"rls_exempt": ...}` de HD-1 es metadata Python, NO DDL (no aparece en el drift test ni requiere baseline amendment). Si un task parece necesitar una migración, PARÁ — está mal encuadrado.
- **Suite bajo `app_rls` + FORCE RLS.** `db_session`/`db_engine` conectan como `app_rls` (no bypass). Tests single-tenant que cuelgan de `sample_org`/`sample_*` quedan auto-scopeados. Tests que crean org inline o abren sesión aparte tocando tablas org-scoped DEBEN llamar `await scope_session_to_org(session, org_id)` antes de la primera query. Tests cross-tenant/control-plane/owner-only usan `owner_session`/`owner_engine`.
- **`Decimal` para dinero, nunca float.** No emojis en código. Identifiers en inglés, UI/docstrings en español OK.
- **Ruff limpio:** `cd backend && uv run ruff check .` + `uv run ruff format .` (formatear, no solo checkear) tras cada task.
- **Baseline pineado:** `a9977ac077e5` (`down_revision=None`). No regenerar.
- **Tests default paralelo** (`-n auto` en addopts). Para un archivo puntual: `uv run pytest -n0 tests/<file>.py -v`.
- **Commits frecuentes:** cada task cierra en ≥1 commit. Convención de mensaje: `<tipo>(hardening): <qué> [HD-n]`.
- Al terminar TODOS los tasks: suite 475 → ~487, `ruff` limpio, boot smoke prod-local OK (HD-7 NO debe dispararse con 1 worker default), PR único `saas/pre-sp3-hardening` → `main`.

---

### Task 1: HD-1 — Guard inverso de cobertura RLS

**Files:**
- Modify: `backend/src/ibkr_control/db/models/memberships.py:21-25` (agregar `info` al table kwargs)
- Test: `backend/tests/test_identity_models.py` (2 tests nuevos al final)

**Interfaces:**
- Consumes: `ibkr_control.db.base.Base` (metadata), `ibkr_control.db.rls.ORG_SCOPED_TABLES` (lista SSOT), fixture `owner_engine` (async engine que conecta como owner, ve todas las tablas).
- Produces: nada consumido por tasks posteriores. Establece la convención `__table_args__[..., {"info": {"rls_exempt": "<razón>"}}]` para declarar exención RLS en el modelo.

- [ ] **Step 1: Escribir el test de completitud (metadata-derived, sin DB)**

Agregar al final de `backend/tests/test_identity_models.py`:

```python
def test_every_org_scoped_table_is_rls_covered():
    """Guard inverso (HD-1): TODA tabla con organization_id debe tener régimen RLS
    declarado — en ORG_SCOPED_TABLES (loop org_isolation), en el set policied-aparte
    (access_grants con grant_visibility), o marcada info={'rls_exempt': ...} en el
    modelo. Una tabla org-scoped nueva (p.ej. lot_classifications de Phase 3) que se
    olvide de todo esto rompe acá — fail-closed, análogo tabla-nivel del boot guard
    de roles (PR #7). Aserción pura sobre Base.metadata, no toca DB.
    """
    from ibkr_control.db.base import Base
    from ibkr_control.db.rls import ORG_SCOPED_TABLES

    policied = set(ORG_SCOPED_TABLES) | {"access_grants"}
    uncovered = []
    for table in Base.metadata.tables.values():
        if "organization_id" not in table.columns:
            continue
        if table.name in policied:
            continue
        if table.info.get("rls_exempt"):
            continue
        uncovered.append(table.name)

    assert not uncovered, (
        "Tablas con organization_id SIN cobertura RLS declarada — agregalas a "
        "ORG_SCOPED_TABLES, o marcá __table_args__ = (..., {'info': {'rls_exempt': "
        f"'razón'}}) en el modelo: {uncovered}"
    )
```

- [ ] **Step 2: Correr el test — debe FALLAR**

Run: `cd backend && uv run pytest -n0 tests/test_identity_models.py::test_every_org_scoped_table_is_rls_covered -v`
Expected: FAIL — `uncovered == ['memberships']` (tiene organization_id, no está en la lista ni marcada exenta).

- [ ] **Step 3: Marcar `memberships` como exenta en el modelo**

En `backend/src/ibkr_control/db/models/memberships.py`, reemplazar el dict final de `__table_args__` (línea 24) para incluir `info`:

```python
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "organization_id"),
        CheckConstraint("role IN ('owner', 'admin', 'member')", name="role"),
        {
            "comment": "User<->org con rol. Identidad; sin org-RLS (se lee para resolver contexto).",
            "info": {
                "rls_exempt": (
                    "load-bearing: memberships se lee SIN contexto org para resolver "
                    "authz (list_grants grants.py:55-57, authz_grant_party_ids rls.py:249). "
                    "RLS acá haría default-deny del propio resolver. Régimen revisado en SP4."
                )
            },
        },
    )
```

- [ ] **Step 4: Correr el test — debe PASAR**

Run: `cd backend && uv run pytest -n0 tests/test_identity_models.py::test_every_org_scoped_table_is_rls_covered -v`
Expected: PASS.

- [ ] **Step 5: Escribir el test de realidad-DB (reflection contra owner_engine)**

Agregar a `backend/tests/test_identity_models.py`:

```python
@pytest.mark.asyncio
async def test_org_scoped_tables_actually_force_rls(owner_engine):
    """HD-1 capa 2: cada tabla policied (ORG_SCOPED_TABLES + access_grants) tiene
    ENABLE + FORCE RLS + >=1 policy en la DB real. Cierra el caso 'está en la lista
    pero el baseline no le aplicó FORCE'. owner_engine (no app_rls) para leer
    pg_class/pg_policies sin RLS de por medio.
    """
    from sqlalchemy import text
    from ibkr_control.db.rls import ORG_SCOPED_TABLES

    policied = list(ORG_SCOPED_TABLES) + ["access_grants"]
    async with owner_engine.connect() as conn:
        for t in policied:
            flags = (
                await conn.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity "
                        "FROM pg_class WHERE relname = :t"
                    ),
                    {"t": t},
                )
            ).one()
            assert flags.relrowsecurity and flags.relforcerowsecurity, (
                f"{t}: RLS no está ENABLE+FORCE (relrowsecurity={flags.relrowsecurity}, "
                f"relforcerowsecurity={flags.relforcerowsecurity})"
            )
            npol = (
                await conn.execute(
                    text("SELECT count(*) FROM pg_policies WHERE tablename = :t"),
                    {"t": t},
                )
            ).scalar()
            assert npol >= 1, f"{t}: sin ninguna RLS policy"
```

- [ ] **Step 6: Correr ambos tests HD-1 — deben PASAR**

Run: `cd backend && uv run pytest -n0 tests/test_identity_models.py -k "rls_covered or force_rls" -v`
Expected: PASS (2 passed). Si `test_org_scoped_tables_actually_force_rls` falla, es un hallazgo real de RLS faltante — reportarlo, no silenciarlo.

- [ ] **Step 7: Ruff + commit**

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add tests/test_identity_models.py src/ibkr_control/db/models/memberships.py
git commit -m "test(hardening): guard inverso de cobertura RLS + marker rls_exempt en memberships [HD-1]"
```

---

### Task 2: HD-2 — Guard de ruta para la barrera-3 (`visible_account_ids`)

**Files:**
- Test: `backend/tests/test_route_authz_coverage.py` (1 helper + 1 test nuevos)

**Interfaces:**
- Consumes: `ibkr_control.main.create_app`, `fastapi.routing.APIRoute`, el marker `._authz_scope` que `require_scope` adjunta (ver `authz/scopes.py`), `inspect.getsource`.
- Produces: nada. Lockea la convención "todo endpoint `data:read` referencia `visible_account_ids`". Guardrail forward para Phase 3.

- [ ] **Step 1: Escribir el helper + test**

Agregar a `backend/tests/test_route_authz_coverage.py` (después de `_has_scope`):

```python
import inspect


def _route_scope(route: APIRoute) -> str | None:
    """Devuelve el string de scope declarado por la ruta (el primer ._authz_scope
    hallado en su árbol de dependencias), o None si no lleva require_scope.
    """
    found: list[str] = []

    def walk(dependencies) -> None:
        for dep in dependencies:
            scope = getattr(dep.call, "_authz_scope", None)
            if scope is not None:
                found.append(scope)
            walk(dep.dependencies)

    walk(route.dependant.dependencies)
    return found[0] if found else None


def test_data_read_routes_apply_party_scope():
    """HD-2: TODA ruta data:read debe aplicar la barrera-3 (party filter via
    visible_account_ids). Hoy solo list_restatements es data:read y ya la aplica;
    el guard lockea la convención ANTES de que Phase 3 agregue endpoints data-plane
    (un grantee de firma que salte el filtro ve las filas de TODOS los clientes del
    org, no solo los parties otorgados). Non-vacuo: exige >=1 ruta data:read viva.
    """
    app = create_app()
    data_read = [
        r for r in app.routes if isinstance(r, APIRoute) and _route_scope(r) == "data:read"
    ]
    assert data_read, (
        "No hay ninguna ruta data:read — el guard sería vacuo. ¿Se renombró el scope "
        "o se movió require_scope('data:read')?"
    )
    missing = [
        (method, r.path)
        for r in data_read
        for method in r.methods - {"HEAD", "OPTIONS"}
        if "visible_account_ids" not in inspect.getsource(r.endpoint)
    ]
    assert not missing, (
        "Rutas data:read que NO referencian visible_account_ids (barrera-3, SP2 — "
        f"todo endpoint data-plane DEBE filtrar por party): {missing}"
    )
```

- [ ] **Step 2: Correr el test — debe PASAR (no vacuo)**

Run: `cd backend && uv run pytest -n0 tests/test_route_authz_coverage.py::test_data_read_routes_apply_party_scope -v`
Expected: PASS — `list_restatements` (`api/ingest.py:229`, scope `data:read`) referencia `visible_account_ids` en su source (`:253`), y `data_read` no está vacío.

- [ ] **Step 3: Verificar que el guard TIENE dientes (mutación temporal)**

Para probar que no es vacuo, temporalmente comentar la línea `visible = await visible_account_ids(session, ctx)` en `api/ingest.py:253` (y su uso) NO es práctico sin romper el endpoint. En su lugar, verificar el mecanismo con una aserción inline manual:

Run: `cd backend && uv run python -c "import inspect; from ibkr_control.api import ingest; print('visible_account_ids' in inspect.getsource(ingest.list_restatements))"`
Expected: `True`. (Confirma que el matcher de source funciona sobre el endpoint real.)

- [ ] **Step 4: Ruff + commit**

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add tests/test_route_authz_coverage.py
git commit -m "test(hardening): guard de ruta para la barrera-3 visible_account_ids [HD-2]"
```

---

### Task 3: HD-3 — Race de `_ensure_counterparties`

**Files:**
- Modify: `backend/src/ibkr_control/ingest/flex/persister.py:917-957` (`_ensure_counterparties`)
- Test: `backend/tests/test_persister_counterparties.py` (crear) o el archivo de persister existente que ejercite `_ensure_counterparties`

**Interfaces:**
- Consumes: `sqlalchemy.dialects.postgresql.insert as pg_insert` (ya importado en persister.py — verificar el alias), `Counterparty.__table__`, `inspect.getsource`, fixture `sample_org` + `scope_session_to_org`.
- Produces: `_ensure_counterparties` con la misma semántica pero race-safe (idéntica firma y retorno `dict[str, int]`).

**Nota de testing:** la carrera REAL (dos INSERTs concurrentes del mismo unique key) NO es testeable determinísticamente sin hilos — dos transacciones que insertan el mismo key se bloquean en el lock del índice, así que un test "secuencial en un event loop" se cuelga esperando el commit de la otra. Por eso el driver rojo es un **guard estructural** (el source usa `on_conflict_do_nothing` — mismo estilo que HD-5b), acompañado de un **test de idempotencia** de comportamiento. Es el mismo trade-off que `_ensure_accounts`/`_ensure_instruments` ya asumieron.

- [ ] **Step 1: Escribir el guard estructural (driver) + el test de idempotencia (companion)**

Crear `backend/tests/test_persister_counterparties.py`:

```python
import inspect

import pytest
from sqlalchemy import select

from ibkr_control.db.models.counterparties import Counterparty
from ibkr_control.ingest.flex import persister
from ibkr_control.ingest.flex.persister import _ensure_counterparties
from tests.conftest import scope_session_to_org


def test_ensure_counterparties_uses_on_conflict():
    """HD-3 (driver): _ensure_counterparties debe usar el patrón endurecido
    ON CONFLICT DO NOTHING (race-safe) igual que _ensure_accounts/_ensure_instruments,
    no el add+flush racy. Guard de source porque la carrera real no es testeable
    determinísticamente (lock del índice cuelga un test secuencial).
    """
    src = inspect.getsource(_ensure_counterparties)
    assert "on_conflict_do_nothing" in src, (
        "_ensure_counterparties aún usa add+flush racy — migrar a ON CONFLICT + re-select"
    )


@pytest.mark.asyncio
async def test_ensure_counterparties_is_idempotent(db_session, sample_org):
    """HD-3 (companion): dos corridas del mismo external_id en el mismo org devuelven
    el mismo id y dejan UNA fila (no double-insert)."""
    org_id = sample_org.id
    await scope_session_to_org(db_session, org_id)

    first = await _ensure_counterparties(db_session, ["CS-999999-99"], organization_id=org_id)
    await db_session.flush()
    second = await _ensure_counterparties(db_session, ["CS-999999-99"], organization_id=org_id)
    await db_session.flush()

    assert first["CS-999999-99"] == second["CS-999999-99"]
    rows = (
        await db_session.scalars(
            select(Counterparty).where(Counterparty.organization_id == org_id)
        )
    ).all()
    assert len([c for c in rows if c.external_id == "CS-999999-99"]) == 1
```

- [ ] **Step 2: Correr — el driver debe FALLAR, el companion PASA**

Run: `cd backend && uv run pytest -n0 tests/test_persister_counterparties.py -v`
Expected: `test_ensure_counterparties_uses_on_conflict` FALLA (el source aún tiene `add`+`flush`); `test_ensure_counterparties_is_idempotent` PASA (el `existing` map ya dedupea en-proceso en la misma sesión).

- [ ] **Step 3: Reescribir `_ensure_counterparties` al patrón ON CONFLICT + re-select**

Reemplazar el cuerpo de `_ensure_counterparties` (`persister.py:917-957`) — desde el `missing = ...` en adelante — por:

```python
    missing = sorted(set(external_ids) - set(existing))
    if missing:
        stmt = (
            pg_insert(Counterparty.__table__)
            .values([{"organization_id": organization_id, "external_id": ext_id} for ext_id in missing])
            .on_conflict_do_nothing(index_elements=["organization_id", "external_id"])
        )
        await session.execute(stmt)
        result2 = await session.scalars(
            select(Counterparty).where(
                Counterparty.organization_id == organization_id,
                Counterparty.external_id.in_(missing),
            )
        )
        for c in result2.all():
            existing[c.external_id] = c.id

    return existing
```

Verificar que `pg_insert` está importado en `persister.py` (lo usan `_ensure_accounts`/`_ensure_instruments` — grep `from sqlalchemy.dialects.postgresql import insert`). Si el alias es `insert as pg_insert`, usarlo consistente.

- [ ] **Step 4: Correr el test — debe PASAR**

Run: `cd backend && uv run pytest -n0 tests/test_persister_counterparties.py -v`
Expected: PASS.

- [ ] **Step 5: Correr la suite de persister completa (no romper nada)**

Run: `cd backend && uv run pytest -n0 -k persister -v`
Expected: todo PASS (el ingest real sigue creando counterparties bien).

- [ ] **Step 6: Ruff + commit**

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add tests/test_persister_counterparties.py src/ibkr_control/ingest/flex/persister.py
git commit -m "fix(hardening): _ensure_counterparties race-safe con ON CONFLICT + re-select [HD-3]"
```

---

### Task 4: HD-6 — Guard de `ondelete` en el drift test

**Files:**
- Test: `backend/tests/test_fk_ondelete_policy.py` (crear)

**Interfaces:**
- Consumes: `owner_engine` (reflexión sobre `pg_constraint`), `information_schema`.
- Produces: nada. Lockea el mapa de políticas `ondelete` que `compare_metadata` no diffea.

- [ ] **Step 1: Derivar el mapa esperado de la DB real (paso exploratorio, NO se commitea como shortcut)**

Run (para generar el mapa inicial que va HARDCODEADO en el test):
```bash
cd backend && uv run python -c "
import asyncio
from sqlalchemy import text
from ibkr_control.db.session import get_engine
async def main():
    q = text('''
      SELECT c.conname, t.relname AS tbl, c.confdeltype
      FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid
      WHERE c.contype = 'f' ORDER BY t.relname, c.conname
    ''')
    async with get_engine().connect() as conn:
        for r in (await conn.execute(q)).all():
            print(r.tbl, r.conname, r.confdeltype)
asyncio.run(main())
"
```
`confdeltype`: `a`=NO ACTION, `r`=RESTRICT, `c`=CASCADE, `n`=SET NULL, `d`=SET DEFAULT. Copiar el output al mapa del test (Step 2). Este comando es exploratorio (leer el estado) — el artefacto que se commitea es el TEST con el mapa embebido, no un script.

- [ ] **Step 2: Escribir el test con el mapa esperado embebido**

Crear `backend/tests/test_fk_ondelete_policy.py`. Poblar `EXPECTED` con el output del Step 1 (una entrada por FK `conname -> confdeltype`):

```python
import pytest
from sqlalchemy import text

# HD-6: compare_metadata de Alembic NO diffea el ondelete de las FK, así que la
# semántica append-only-ledger (SET NULL en flex_import_id/user_id) vs org-wipe
# (CASCADE) vs facts->accounts (RESTRICT) queda desprotegida. Este mapa PINEA cada
# FK; un cambio de ondelete en un modelo que diverja de la DB rompe acá.
# confdeltype: a=NO ACTION, r=RESTRICT, c=CASCADE, n=SET NULL, d=SET DEFAULT.
EXPECTED: dict[str, str] = {
    # <<< PEGAR el output del Step 1, formato "conname": "confdeltype", >>>
    # p.ej.:
    # "trades_account_id_fkey": "r",
    # "flex_imports_connection_id_fkey": "n",
    # "memberships_user_id_fkey": "c",
}


@pytest.mark.asyncio
async def test_fk_ondelete_policy_is_pinned(owner_engine):
    q = text(
        "SELECT c.conname, c.confdeltype FROM pg_constraint c "
        "JOIN pg_class t ON t.oid = c.conrelid WHERE c.contype = 'f'"
    )
    async with owner_engine.connect() as conn:
        actual = {r.conname: r.confdeltype for r in (await conn.execute(q)).all()}

    # Ignorar FKs de tablas fuera de Base.metadata (apscheduler_jobs no tiene FKs;
    # defensivo por si el jobstore cambia).
    actual = {k: v for k, v in actual.items() if not k.startswith("apscheduler")}

    assert actual == EXPECTED, (
        "Deriva de política ondelete de FK (append-only-ledger / org-wipe / RESTRICT). "
        "Si el cambio es INTENCIONAL, actualizá EXPECTED; si no, es un bug de semántica "
        f"del ledger.\nFaltan/cambiaron: {set(EXPECTED.items()) ^ set(actual.items())}"
    )
```

- [ ] **Step 3: Correr el test — debe PASAR**

Run: `cd backend && uv run pytest -n0 tests/test_fk_ondelete_policy.py -v`
Expected: PASS (el mapa fue derivado de la DB real, así que matchea). Si falla, hay un typo en el paste — corregir hasta verde.

- [ ] **Step 4: Ruff + commit**

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add tests/test_fk_ondelete_policy.py
git commit -m "test(hardening): guard de política ondelete de FK (append-only-ledger) [HD-6]"
```

---

### Task 5: HD-5a — Helper compartido de participations (SCD-2)

**Files:**
- Create: `backend/src/ibkr_control/db/participations.py`
- Modify: `backend/src/ibkr_control/api/setup.py` (dos bloques SCD-2: ~`:570-590` step2/save y ~`:730-750` step3)
- Test: `backend/tests/test_participations_helper.py` (crear)

**Interfaces:**
- Consumes: `AsyncSession`, `Participation`, `date`, `Decimal`.
- Produces: `async def upsert_participation(session, *, party_id: int, account_id: int, organization_id: int, pct: Decimal, at: date) -> None` — cierra la fila abierta (si su pct difiere) e inserta la nueva; no-op si el pct no cambió. Consumido por ambos handlers de `setup.py` y por `domain/participation.py` de Phase 3.

- [ ] **Step 1: Escribir el test del helper**

Crear `backend/tests/test_participations_helper.py`:

```python
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from ibkr_control.db.models.accounts import Account
from ibkr_control.db.models.parties import Party
from ibkr_control.db.models.participations import Participation
from ibkr_control.db.participations import upsert_participation
from tests.conftest import scope_session_to_org


@pytest.mark.asyncio
async def test_upsert_participation_scd2_close_and_insert(db_session, sample_org):
    """HD-5a: cambiar el pct cierra la fila abierta (valid_to=at) e inserta la nueva.
    Mismo pct = no-op (sin fila nueva ni cierre)."""
    org_id = sample_org.id
    await scope_session_to_org(db_session, org_id)
    party = Party(organization_id=org_id, display_name="Owner")
    acc = Account(ibkr_account_id="U99999001", organization_id=org_id, currency="USD")
    db_session.add_all([party, acc])
    await db_session.flush()

    at1 = date(2026, 1, 1)
    await upsert_participation(
        db_session, party_id=party.id, account_id=acc.id, organization_id=org_id,
        pct=Decimal("0.5000"), at=at1,
    )
    await db_session.flush()

    # Mismo pct → no-op
    await upsert_participation(
        db_session, party_id=party.id, account_id=acc.id, organization_id=org_id,
        pct=Decimal("0.5000"), at=date(2026, 2, 1),
    )
    await db_session.flush()
    rows = (await db_session.scalars(select(Participation).where(
        Participation.party_id == party.id))).all()
    assert len(rows) == 1
    assert rows[0].valid_to is None

    # pct nuevo → cierra la vieja + inserta
    at2 = date(2026, 3, 1)
    await upsert_participation(
        db_session, party_id=party.id, account_id=acc.id, organization_id=org_id,
        pct=Decimal("1.0000"), at=at2,
    )
    await db_session.flush()
    rows = (await db_session.scalars(select(Participation).where(
        Participation.party_id == party.id).order_by(Participation.valid_from))).all()
    assert len(rows) == 2
    assert rows[0].valid_to == at2 and rows[0].pct == Decimal("0.5000")
    assert rows[1].valid_to is None and rows[1].pct == Decimal("1.0000")
```

- [ ] **Step 2: Correr — debe FALLAR (módulo no existe)**

Run: `cd backend && uv run pytest -n0 tests/test_participations_helper.py -v`
Expected: FAIL — `ModuleNotFoundError: ibkr_control.db.participations`.

- [ ] **Step 3: Crear el helper**

Crear `backend/src/ibkr_control/db/participations.py`:

```python
"""Write-path compartido de participations (SCD-2) — HD-5a.

Extraído de los dos bloques byte-idénticos de api/setup.py. Phase 3
(domain/participation.py::apply_pct) consume el READ; este es el WRITE canónico.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.db.models.participations import Participation


async def upsert_participation(
    session: AsyncSession,
    *,
    party_id: int,
    account_id: int,
    organization_id: int,
    pct: Decimal,
    at: date,
) -> None:
    """SCD-2: si hay fila abierta con distinto pct, la cierra (valid_to=at) e inserta
    la nueva; si el pct no cambió, no-op. NO commitea (el caller maneja la tx)."""
    existing = await session.scalar(
        select(Participation).where(
            Participation.party_id == party_id,
            Participation.account_id == account_id,
            Participation.valid_to.is_(None),
        )
    )
    if existing is not None:
        if existing.pct == pct:
            return
        existing.valid_to = at
        await session.flush()
    session.add(
        Participation(
            party_id=party_id,
            account_id=account_id,
            organization_id=organization_id,
            pct=pct,
            valid_from=at,
            valid_to=None,
        )
    )
```

- [ ] **Step 4: Correr — debe PASAR**

Run: `cd backend && uv run pytest -n0 tests/test_participations_helper.py -v`
Expected: PASS.

- [ ] **Step 5: Reemplazar el bloque SCD-2 de step2/save por la llamada al helper**

En `api/setup.py` step2/save (~`:573-591`), reemplazar el bloque `existing = await session.scalar(...) ... session.add(Participation(...))` por:

```python
        from ibkr_control.db.participations import upsert_participation

        await upsert_participation(
            session,
            party_id=party_id,
            account_id=acc.id,
            organization_id=ctx.org_id,
            pct=item.pct,
            at=today,
        )
```

(Mover el import al tope del archivo si el linter lo prefiere.) Preservar el `if item.alias is not None: acc.alias = item.alias` que va ANTES.

- [ ] **Step 6: Reemplazar el bloque SCD-2 de step3 igual**

En `api/setup.py` step3 (~`:730-750`), reemplazar el mismo bloque SCD-2 por la misma llamada `await upsert_participation(...)`. (El account create de step3 se toca en Task 6.)

- [ ] **Step 7: Correr los tests de setup — deben PASAR**

Run: `cd backend && uv run pytest -n0 -k "setup or wizard" -v`
Expected: PASS (el comportamiento SCD-2 es idéntico; los E2E del wizard siguen verdes).

- [ ] **Step 8: Ruff + commit**

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add tests/test_participations_helper.py src/ibkr_control/db/participations.py src/ibkr_control/api/setup.py
git commit -m "refactor(hardening): helper compartido upsert_participation (SCD-2), consumido por ambos handlers [HD-5a]"
```

---

### Task 6: HD-5b — Account write-path race-safe en step3

**Files:**
- Modify: `backend/src/ibkr_control/api/setup.py` step3 (~`:711-725`, el `if acc is None: Account(...) + flush`)
- Test: `backend/tests/test_setup_step3_account_race.py` (crear) o extender el test de setup existente

**Interfaces:**
- Consumes: `_ensure_accounts(session, ibkr_ids, *, organization_id) -> dict[str, int]` de `ingest/flex/persister.py` (helper endurecido ON CONFLICT + re-select).
- Produces: step3 crea cuentas vía el helper canónico (elimina la 3ra copia racy). Sin cambio de contrato del endpoint.

- [ ] **Step 1: Escribir el test de que step3 NO tiene un create racy propio**

Este guard es estructural: asserta que el source de step3 NO contiene un `Account(` inline de creación (la 3ra copia) y SÍ usa `_ensure_accounts`. Crear `backend/tests/test_setup_step3_account_race.py`:

```python
import inspect

from ibkr_control.api import setup


def test_step3_uses_hardened_account_helper_not_inline_create():
    """HD-5b: step3 no debe recrear el write-path de cuentas inline (racy). Debe
    delegar en _ensure_accounts (ON CONFLICT + re-select). Guard de source: la 3ra
    copia era `if acc is None: session.add(Account(...))`.
    """
    # localizar el handler step3 (save_new_accounts) por nombre
    fn = getattr(setup, "step3_save_new_accounts", None) or getattr(
        setup, "save_new_accounts", None
    )
    assert fn is not None, "no encontré el handler de step3 — ajustá el nombre"
    src = inspect.getsource(fn)
    assert "_ensure_accounts" in src, "step3 no usa el helper endurecido _ensure_accounts"
    assert "Account(" not in src, "step3 aún crea Account inline (write-path racy — HD-5b)"
```

Ajustar el nombre del handler al real (grep `def .*step3` / `save_new_accounts` en `api/setup.py`).

- [ ] **Step 2: Correr — debe FALLAR**

Run: `cd backend && uv run pytest -n0 tests/test_setup_step3_account_race.py -v`
Expected: FAIL — el source de step3 aún tiene `Account(` inline.

- [ ] **Step 3: Rutear el create de step3 por `_ensure_accounts`**

En `api/setup.py` step3, reemplazar el bloque:

```python
        acc = await session.scalar(
            select(Account).where(
                Account.organization_id == ctx.org_id,
                Account.ibkr_account_id == item.ibkr_account_id,
            )
        )
        if acc is None:
            acc = Account(
                organization_id=ctx.org_id,
                ibkr_account_id=item.ibkr_account_id,
                alias=item.alias,
                currency="USD",
            )
            session.add(acc)
            await session.flush()
        elif item.alias is not None:
            acc.alias = item.alias
```

por (usando el helper endurecido para obtener el id race-safe, luego set del alias):

```python
        from ibkr_control.ingest.flex.persister import _ensure_accounts

        ids = await _ensure_accounts(
            session, [item.ibkr_account_id], organization_id=ctx.org_id
        )
        acc = await session.scalar(
            select(Account).where(Account.id == ids[item.ibkr_account_id])
        )
        if item.alias is not None:
            acc.alias = item.alias
```

(Mover el import al tope si el linter lo prefiere. `acc.id` sigue disponible para la llamada a `upsert_participation` de Task 5.)

- [ ] **Step 4: Correr el guard + los tests de setup — deben PASAR**

Run: `cd backend && uv run pytest -n0 tests/test_setup_step3_account_race.py -k "setup or step3" -v` y `cd backend && uv run pytest -n0 -k "setup or wizard" -v`
Expected: PASS (guard verde + comportamiento de step3 idéntico, ahora race-safe).

- [ ] **Step 5: Ruff + commit**

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add tests/test_setup_step3_account_race.py src/ibkr_control/api/setup.py
git commit -m "fix(hardening): step3 crea cuentas via _ensure_accounts (elimina write-path racy) [HD-5b]"
```

---

### Task 7: HD-4 — Guard del flip del baseline (T1-D14) + item en el backlog

**Files:**
- Test: `backend/tests/test_tier1_baseline.py` (1 test nuevo)
- Modify: `docs/plans/2026-06-03-pre-deploy-hardening-backlog.md` (agregar item HD-4 en WS5)

**Interfaces:**
- Consumes: `_revision_files()`, `_VERSIONS_DIR` (ya en `test_tier1_baseline.py`), `pathlib.Path`, `pytest.skip`.
- Produces: guard fail-closed que, con el sentinel presente (post-deploy), pinea el `revision` del baseline.

- [ ] **Step 1: Escribir el guard del sentinel**

Agregar a `backend/tests/test_tier1_baseline.py`:

```python
_PINNED_BASELINE_REVISION = "a9977ac077e5"


def test_baseline_frozen_after_first_deploy():
    """HD-4: la política T1-D14 (baseline mutable, regen + wipe dev por PR) EXPIRA al
    primer deploy. A partir de ahí, regenerar el baseline = wipe de tenants en prod.
    Con el sentinel backend/.baseline_frozen presente, este guard pinea el revision:
    un regen accidental cambia el hash y rompe acá — no la DB. Pre-deploy (sin
    sentinel) se skipea: la política mutable sigue viva.
    """
    sentinel = Path(__file__).resolve().parents[1] / ".baseline_frozen"
    if not sentinel.exists():
        pytest.skip(
            "baseline aún mutable (pre-deploy, T1-D14) — creá backend/.baseline_frozen "
            "al hacer el primer deploy para activar este guard"
        )
    files = _revision_files()
    assert len(files) >= 1, "no hay revisiones — el baseline desapareció"
    roots = [f for f in files if f.name.startswith(_PINNED_BASELINE_REVISION)]
    assert roots, (
        f"el baseline pineado {_PINNED_BASELINE_REVISION} ya no existe — "
        "¿se regeneró el baseline POST-deploy? Eso destruiría datos de tenants."
    )
```

- [ ] **Step 2: Correr — debe PASAR (skipped pre-deploy)**

Run: `cd backend && uv run pytest -n0 tests/test_tier1_baseline.py::test_baseline_frozen_after_first_deploy -v`
Expected: SKIPPED (`backend/.baseline_frozen` no existe todavía — correcto).

- [ ] **Step 3: Verificar que el guard tiene dientes (con sentinel temporal)**

```bash
cd backend && touch .baseline_frozen
uv run pytest -n0 tests/test_tier1_baseline.py::test_baseline_frozen_after_first_deploy -v
rm .baseline_frozen
```
Expected: PASS con el sentinel presente (el baseline pineado existe). Confirma que no se skipea cuando el sentinel está. NO commitear el sentinel.

- [ ] **Step 4: Agregar el item HD-4 al backlog**

En `docs/plans/2026-06-03-pre-deploy-hardening-backlog.md`, sección WS5 (Arquitectura), agregar:

```markdown
### [ ] HD4 — Flip del baseline a inmutable al primer deploy (T1-D14 expira)

- **Severidad:** HIGH (data-loss si se olvida) · **Tipo:** 🟢 core · **Effort:** S · **WS:** WS5
- **Estado:** ⏳ Pendiente (guard fail-closed ya en `tests/test_tier1_baseline.py::test_baseline_frozen_after_first_deploy`)

**Problema:** la política "baseline mutable (regen + `down -v` dev por PR)" EXPIRA al primer deploy. Un regen post-deploy = wipe de datos de tenants. Vivía solo como prosa.

**Fix (checklist al primer deploy):** (1) crear `backend/.baseline_frozen` (activa el guard que pinea `a9977ac077e5`); (2) dejar de regenerar el baseline — pasar a migraciones inmutables + expand/contract; (3) documentar el corte en CLAUDE.md. El guard fail-closed ya está; este item es el recordatorio operacional + el paso de crear el sentinel.
```

- [ ] **Step 5: Commit**

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add tests/test_tier1_baseline.py ../docs/plans/2026-06-03-pre-deploy-hardening-backlog.md
git commit -m "test(hardening): guard fail-closed del flip del baseline + item HD4 en backlog [HD-4]"
```

---

### Task 8: HD-7 — Fail-loud del invariante 1-proceso

**Files:**
- Modify: `backend/src/ibkr_control/db/guards.py` (agregar `assert_single_process`) — o crear `backend/src/ibkr_control/guards.py` si preferís no mezclarlo con los DB guards
- Modify: `backend/src/ibkr_control/main.py:20-36` (llamar el guard en `lifespan`, antes de `create_scheduler`)
- Test: `backend/tests/test_single_process_guard.py` (crear)

**Interfaces:**
- Consumes: `os.environ` (o un `Mapping` inyectable para testear), `create_scheduler`/`register_jobs` (orden en lifespan).
- Produces: `def assert_single_process(env: Mapping[str, str] | None = None) -> None` — raise `RuntimeError` si detecta >1 worker configurado.

- [ ] **Step 1: Escribir el test**

Crear `backend/tests/test_single_process_guard.py`:

```python
import pytest

from ibkr_control.db.guards import assert_single_process


def test_single_process_guard_passes_with_one_worker():
    assert_single_process({"WEB_CONCURRENCY": "1"}) is None
    assert_single_process({}) is None  # sin la var = default 1 worker


@pytest.mark.parametrize("var", ["WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS"])
def test_single_process_guard_fails_with_multiple_workers(var):
    with pytest.raises(RuntimeError, match="1-proceso|in-process|SP5"):
        assert_single_process({var: "2"})
```

- [ ] **Step 2: Correr — debe FALLAR (función no existe)**

Run: `cd backend && uv run pytest -n0 tests/test_single_process_guard.py -v`
Expected: FAIL — `ImportError: cannot import name 'assert_single_process'`.

- [ ] **Step 3: Implementar el guard**

Agregar a `backend/src/ibkr_control/db/guards.py`:

```python
import os
from collections.abc import Mapping


def assert_single_process(env: Mapping[str, str] | None = None) -> None:
    """Fail-loud si se configuró >1 worker (HD-7 / DEF-B).

    El scheduler (APScheduler in-process, sin leader election), JobTracker y
    Step3Stash son singletons per-proceso. Con >1 worker/réplica: los crons
    disparan una vez por worker, el SSE cae en el worker equivocado (404), y el
    Step3Stash del wizard es invisible entre workers (onboarding roto) — todo en
    SILENCIO. Hasta que SP5 extraiga el scheduler + cola durable, >1 worker está
    roto: fallar al arranque es correcto.
    """
    env = os.environ if env is None else env
    for var in ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS"):
        raw = (env.get(var) or "").strip()
        if raw.isdigit() and int(raw) > 1:
            raise RuntimeError(
                f"{var}={raw}: correr >1 worker rompe crons/SSE/onboarding en SILENCIO "
                "(scheduler/JobTracker/Step3Stash son in-process, invariante 1-proceso). "
                "Ver DEF-B del spec pre-SP3 + SP5 (extracción del scheduler + cola durable) "
                "antes de escalar horizontalmente."
            )
```

- [ ] **Step 4: Correr — debe PASAR**

Run: `cd backend && uv run pytest -n0 tests/test_single_process_guard.py -v`
Expected: PASS (todos).

- [ ] **Step 5: Cablear el guard en el lifespan**

En `main.py`, dentro de `lifespan`, ANTES de `create_scheduler()`:

```python
    from ibkr_control.db.guards import assert_runtime_role_enforces_rls, assert_single_process
    from ibkr_control.db.session import get_engine

    assert_single_process()
    await assert_runtime_role_enforces_rls(get_engine())
```

- [ ] **Step 6: Boot smoke — el guard NO debe dispararse con la config default**

Run: `make prod-local` (desde la raíz del repo), luego `docker compose logs backend | tail -30`
Expected: el backend arranca normal (config default = 1 worker); NO aparece el `RuntimeError` de HD-7. Confirmar `rolsuper=f/rolbypassrls=f` sigue en los logs (el guard de RLS de PR #7 intacto). Luego `make dev-down` o dejar corriendo según prefieras.

- [ ] **Step 7: Ruff + commit**

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add tests/test_single_process_guard.py src/ibkr_control/db/guards.py src/ibkr_control/main.py
git commit -m "feat(hardening): boot guard fail-loud si >1 worker (invariante 1-proceso) [HD-7]"
```

---

### Task 9: Docs de decisiones diferidas + review holístico + PR

**Files:**
- Modify: `docs/specs/2026-06-03-saas-program-roadmap.md` (notas DEF-B en SP5/SP7, DEF-6 en SP4)
- Modify: `CLAUDE.md` (bullet de estado del programa: hardening pass completo + link al spec/plan)

**Interfaces:** —

- [ ] **Step 1: Registrar DEF-B y DEF-6 en el roadmap SSOT**

En `docs/specs/2026-06-03-saas-program-roadmap.md`, en la sección "Qué dejó SP1 shaped..." (o donde viven las notas por-SP):
- **SP4:** agregar una línea — *"DEF-6 (pre-SP3 hardening 2026-07-06): `organizations`/`memberships` son RLS-exempt Y `app_rls` tiene DML completo sobre ellas — las dos tablas más críticas sin backstop RLS, escribibles por el rol que sirve tráfico. Fix limpio = rol de escritura dedicado (el split de 3 roles ya reservado a SP4) + revoke DML. No se hizo antes porque el aprovisionamiento (CLI hoy, `POST /api/organizations` de SP3) escribe como `app_rls`; revocar ahora lo rompe. Spec: `docs/specs/2026-07-06-pre-sp3-hardening-design.md` §DEF-6."*
- **SP5/SP7:** agregar — *"DEF-B (pre-SP3 hardening 2026-07-06): la auditoría confirmó el scheduler co-locado sin leader election (CRÍTICO en el primer `--workers 2`: crons disparan una vez por worker), singletons in-memory (JobTracker/Step3Stash → SSE 404 / onboarding roto bajo scale-out), y conexión DB pineada durante el HTTP a IBKR. HD-7 lo hace fail-loud hoy (se niega a arrancar con >1 worker); el arreglo real (motor de cola + leader election + backing de singletons) es la decisión gorda de SP5. Spec §DEF-B."*

- [ ] **Step 2: Actualizar el estado del programa en CLAUDE.md**

Agregar un bullet en "Estado del programa SaaS" (después del bullet de SP2), resumiendo el hardening pass: HD-1..HD-7 implementados, DEF-B/DEF-6 diferidos, schema-free, suite 475 → ~487, links a spec + plan. Actualizar el "⏯ SIGUIENTE" para que apunte a SP3 tras este merge.

- [ ] **Step 3: Suite completa + lint + frontend**

```bash
cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .
cd ../frontend && export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh" && pnpm lint && pnpm test:run && pnpm build
```
Expected: backend ~487 passed, ruff 0 drift, frontend lint/vitest/build verdes. (El frontend no se tocó — debe seguir igual.)

- [ ] **Step 4: Commit docs**

```bash
git add docs/specs/2026-06-03-saas-program-roadmap.md CLAUDE.md
git commit -m "docs(hardening): registra DEF-B (SP5/SP7) + DEF-6 (SP4) + estado del programa [HD-docs]"
```

- [ ] **Step 5: Review holístico final (subagent-driven)**

Dispatch de un review holístico cross-task (patrón Tier 1): un subagent que lee el diff COMPLETO de la branch contra `main` y busca lo que los reviews por-task no ven — interacciones entre HD-3/HD-5 (¿el step3 refactorizado sigue llamando `upsert_participation` con el `acc.id` correcto del helper?), que HD-1/HD-2 no sean vacuos, que ningún guard nuevo enmascare un fallo, y que la suite corra bajo `app_rls`. Aplicar los findings reales antes del PR.

- [ ] **Step 6: PR a `main`**

```bash
git push -u origin saas/pre-sp3-hardening
gh pr create --base main --title "Pre-SP3 hardening pass (HD-1..HD-7)" --body "$(cat <<'EOF'
Cierra 7 guards estructurales fail-closed que el pre-deploy-hardening-backlog subestimó bajo la vieja calibración de "3 usuarios", antes de que Phase 3 los pise. Difiere formalmente DEF-B (escala→SP5/SP7) y DEF-6 (DML identidad→SP4).

- HD-1: guard inverso de cobertura RLS (cross-verificado) + marker rls_exempt en memberships
- HD-2: guard de ruta para la barrera-3 visible_account_ids
- HD-3: _ensure_counterparties race-safe (ON CONFLICT + re-select)
- HD-4: guard fail-closed del flip del baseline + item en backlog
- HD-5: helper compartido upsert_participation + step3 via _ensure_accounts (elimina write-path racy)
- HD-6: guard de política ondelete de FK (append-only-ledger)
- HD-7: boot guard fail-loud si >1 worker (invariante 1-proceso)

Schema-free: cero migraciones, no toca el baseline. Suite 475 → ~487.
Spec: docs/specs/2026-07-06-pre-sp3-hardening-design.md
Plan: docs/plans/2026-07-06-pre-sp3-hardening.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
Expected: PR abierto, CI (`backend` + `frontend`) corre. Mergear cuando esté verde.

---

## Notas de ejecución

- **Orden:** los tasks son mayormente independientes; el orden 1→9 va de guards test-only (bajo riesgo) a cambios de comportamiento (HD-3, HD-5) a boot guard (HD-7) a docs+PR. HD-5a (Task 5) debe ir ANTES de HD-5b (Task 6) — Task 6 asume que step3 ya llama `upsert_participation`.
- **Método:** subagent-driven-development. Fresh subagent por task + spec-review + code-quality-review. El code-review corre `pytest -q` COMPLETO (no solo el test del task) — esa es la red que atrapó bugs de diseño en Phase 2.8.
- **Verificación por task:** cada uno cierra con ruff limpio + su test verde. Verificación final en Task 9 Step 3.
- **Si un test HD-1 capa-2 o HD-6 falla al derivarse:** es un hallazgo REAL (RLS faltante / ondelete inesperado), no un test mal escrito. Reportarlo y resolver la causa, no ablandar la aserción.
```
