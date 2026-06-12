# SP2 Authorization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir el choke-point de autorización (`require_scope` + `AuthzContext`), el enforcement del grant cross-org party-scoped (contador), roles de membership con dientes, y el CRUD de grants — per spec `docs/specs/2026-06-12-sp2-authorization-design.md` (SP2-D1..D10).

**Architecture:** Autorización app-interna Postgres-native (SP2-D1, PEP/PDP): paquete nuevo `authz/` decide (resolver puro sobre `memberships` + `access_grants` vía función `SECURITY DEFINER`), los endpoints declaran `Depends(require_scope("..."))` y `api/_context.py` muere. Tres barreras para el grantee: scope en app, `SET LOCAL transaction_read_only` self-healing en DB, y filtro `visible_account_ids` por participations del party. UN baseline amendment (#8): CHECK half-open, `restatement_log.account_id`, función `authz_grant_party_ids`.

**Tech Stack:** FastAPI dependencies, SQLAlchemy 2 async, Alembic (baseline amendment canónico en container), pytest (template-clone: `db_session` = `app_rls`, auto-scopeado por `sample_org`).

**Reglas del repo (idénticas a W1/W2/W3/lineage/exact-precision):** TDD; tests desde el HOST (`cd backend && uv run pytest`); `uv run ruff check . && uv run ruff format .` antes de cada commit; quedarse en el branch `saas/sp2-authorization` (NO crear branches nuevos); baseline amendment canónico (autogenerate temporal contra DB virgen en container + splice entre markers, MISMO revision id `a9977ac077e5` — hand-edits rechazados en amendment #6); el drift test (`compare_metadata`) es el gate del schema; `_ORG_SCOPED_TABLES` NO cambia (no hay tablas nuevas).

**Contexto que el implementer necesita saber:**

- La suite corre como `app_rls` (NO bypass, FORCE RLS). `db_session` queda auto-scopeada por `sample_org`; sesiones abiertas aparte necesitan `await scope_session_to_org(session, org_id)` ANTES de la primera query. Cross-tenant/seeding usa `owner_session`/`owner_engine` (excepción nombrada). Ver `backend/tests/conftest.py`.
- `db/rls.py` es el SSOT del contrato GUC (`app.current_org`/`app.current_user`, `SET LOCAL` vía `set_config(..., true)`, listener `after_begin` que re-aplica desde `session.info`). Las migraciones NUNCA importan de `ibkr_control.*`: el baseline lleva copias FROZEN de los builders.
- `api/_context.py::org_context` es la dependency actual (resuelve membership + setea GUCs). SP2 la reemplaza entera.
- Endpoints actuales y su scope destino (spec SP2-D5):

| Ruta | Scope |
|---|---|
| `GET /api/connections` | `ops:read` |
| `POST /api/connections` · `PATCH /{id}` · `POST /{id}/rotate-token` · `POST /{id}/disable` · `POST /{id}/enable` · `DELETE /{id}` | `connections:write` |
| `GET /api/setup/state` + los 8 `POST /api/setup/*` | `setup:write` |
| `POST /api/ingest/trigger` | `ingest:trigger` |
| `POST /api/imports/upload` | `ingest:trigger` (es un write de ingesta; ver nota SP2-D5) |
| `GET /api/ingest/logs` · `GET /api/health/ingest` | `ops:read` |
| `GET /api/ingest/restatements` | `data:read` (+ filtro party SP2-D9) |
| `GET /api/grants` | `grants:read` (nuevo) |
| `POST /api/grants` · `POST /api/grants/{id}/revoke` | `grants:write` (nuevo) |
| `GET /api/ingest/stream/{job_id}` | allowlist (user-scoped D1, ownership en JobTracker) |
| `/api/auth/*`, `/api/settings/*` (user-scoped), `GET /health` (liveness), docs/openapi | allowlist |

---

### Task 1: Schema — CHECK half-open + `restatement_log.account_id` + función `authz_grant_party_ids` + baseline amendment #8

**Files:**
- Modify: `backend/src/ibkr_control/db/models/access_grants.py` (CHECK `>=`)
- Modify: `backend/src/ibkr_control/db/models/restatements.py` (columna + índice)
- Modify: `backend/src/ibkr_control/db/rls.py` (builder `authz_grant_function_sql`)
- Modify: `backend/alembic/versions/a9977ac077e5_tier1_baseline.py` (amendment #8, regen canónica + frozen function)
- Test: `backend/tests/test_sp2_schema.py` (nuevo)

- [ ] **Step 1: Failing tests del schema**

Crear `backend/tests/test_sp2_schema.py`:

```python
"""SP2 schema: CHECK half-open de access_grants (SP2-D8), restatement_log.account_id
(SP2-D9) y la función SECURITY DEFINER authz_grant_party_ids (SP2-D3)."""

from datetime import date

import pytest
from sqlalchemy import select, text

from ibkr_control.db.models.access_grants import AccessGrant

pytestmark = pytest.mark.anyio


async def _mk_grantee_org(owner_session):
    from ibkr_control.db.models.organizations import Organization

    org = Organization(type="firm", name="Estudio Contable")
    owner_session.add(org)
    await owner_session.commit()
    await owner_session.refresh(org)
    return org


async def test_check_allows_empty_interval_same_day_revoke(
    db_session, sample_org, sample_party, owner_session
):
    """SP2-D8: valid_to == valid_from es legal (intervalo vacío = nunca activo)."""
    firm = await _mk_grantee_org(owner_session)
    g = AccessGrant(
        grantor_party_id=sample_party.id,
        grantee_organization_id=firm.id,
        organization_id=sample_org.id,
        valid_from=date(2026, 6, 12),
        valid_to=date(2026, 6, 12),  # mismo día — viola el CHECK viejo (>)
    )
    db_session.add(g)
    await db_session.commit()  # no debe levantar IntegrityError
    await db_session.refresh(g)
    assert g.valid_to == g.valid_from


async def test_restatement_log_has_account_id(db_session):
    """SP2-D9: account_id es columna de primera clase NOT NULL."""
    from ibkr_control.db.models.restatements import RestatementLog

    cols = RestatementLog.__table__.c
    assert "account_id" in cols
    assert cols.account_id.nullable is False


async def test_authz_grant_party_ids_returns_only_vigentes(
    db_session, sample_org, sample_party, sample_user, owner_session
):
    """SP2-D3: la función SECURITY DEFINER devuelve grantor parties de grants
    vigentes hacia el user (directo), corriendo como app_rls. La función no
    depende del GUC (definer = owner, exenta de RLS) — el bootstrap del resolver
    la llama ANTES de que exista contexto org."""
    from ibkr_control.auth.models import User

    accountant = User(email="cpa@t.com", hashed_password="x", is_active=True, name="CPA")
    owner_session.add(accountant)
    await owner_session.commit()
    await owner_session.refresh(accountant)

    today = date.today()
    db_session.add_all(
        [
            AccessGrant(  # vigente, directo al user
                grantor_party_id=sample_party.id,
                grantee_user_id=accountant.id,
                organization_id=sample_org.id,
                valid_from=today,
            ),
        ]
    )
    await db_session.commit()

    rows = (
        await db_session.execute(
            text("SELECT * FROM authz_grant_party_ids(:u, :o)"),
            {"u": accountant.id, "o": sample_org.id},
        )
    ).scalars()
    assert set(rows.all()) == {sample_party.id}

    # Grant revocado hoy (valid_to=hoy, half-open) NO aparece.
    await db_session.execute(
        text("UPDATE access_grants SET valid_to = CURRENT_DATE WHERE grantor_party_id = :p"),
        {"p": sample_party.id},
    )
    await db_session.commit()
    rows = (
        await db_session.execute(
            text("SELECT * FROM authz_grant_party_ids(:u, :o)"),
            {"u": accountant.id, "o": sample_org.id},
        )
    ).scalars()
    assert set(rows.all()) == set()


async def test_authz_grant_party_ids_via_firm_membership(
    db_session, sample_org, sample_party, owner_session
):
    """Grant a un org-firm: cualquier member del firm resuelve los party_ids."""
    from datetime import date as _date

    from ibkr_control.auth.models import User
    from ibkr_control.db.models.memberships import Membership

    firm = await _mk_grantee_org(owner_session)
    staff = User(email="staff@firm.com", hashed_password="x", is_active=True, name="Staff")
    owner_session.add(staff)
    await owner_session.flush()
    owner_session.add(Membership(user_id=staff.id, organization_id=firm.id, role="member"))
    await owner_session.commit()
    await owner_session.refresh(staff)

    db_session.add(
        AccessGrant(
            grantor_party_id=sample_party.id,
            grantee_organization_id=firm.id,
            organization_id=sample_org.id,
            valid_from=_date.today(),
        )
    )
    await db_session.commit()

    rows = (
        await db_session.execute(
            text("SELECT * FROM authz_grant_party_ids(:u, :o)"),
            {"u": staff.id, "o": sample_org.id},
        )
    ).scalars()
    assert set(rows.all()) == {sample_party.id}
```

Nota para el implementer: `sample_party` cuelga de `sample_org` (conftest); `db_session` queda scopeada a `sample_org` por la fixture — los INSERT de grants pasan el WITH CHECK. El UPDATE de revocación corre en la misma sesión scopeada (arm grantor-org de la policy).

- [ ] **Step 2: Correr los tests — deben FALLAR**

Run: `cd backend && uv run pytest tests/test_sp2_schema.py -n0 -v`
Expected: FAIL — `IntegrityError` (CHECK viejo `>`), `AssertionError` (no account_id), `UndefinedFunction` (authz_grant_party_ids no existe).

- [ ] **Step 3: Cambios en modelos + builder**

En `backend/src/ibkr_control/db/models/access_grants.py`:

```python
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="valid_range"),
```

(reemplaza el `>` por `>=`; agregar al docstring del módulo: "Vigencia half-open [valid_from, valid_to): valid_to == hoy ⇒ inactivo ya; intervalo vacío legal (revoke same-day, SP2-D8).")

En `backend/src/ibkr_control/db/models/restatements.py`, después de `flex_import_id`:

```python
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
```

y en `__table_args__` agregar (antes del Index existente):

```python
        # SP2-D9: filtro party-scoped del grantee (visible_account_ids) — el
        # access path es (org, account) bajo RLS.
        Index(None, "organization_id", "account_id"),
```

En `backend/src/ibkr_control/db/rls.py`, después de `system_enum_function_sql()`:

```python
def authz_grant_function_sql() -> list[str]:
    """CONTROL-PLANE capability: resolución cross-org de grants (SP2-D3).

    El resolver de autorización corre ANTES de setear contexto RLS — bajo
    ``app_rls`` + FORCE, ``grant_visibility`` default-denia, y el arm
    ``grantee_organization_id = current_org`` solo expone el grant del firm con
    el GUC en el org del FIRM (que no es el org solicitado ni adivinable si el
    user tiene N memberships). "¿Puede U entrar al org X?" es inherentemente
    cross-org → misma envolvente de seguridad que system_credentialed_org_ids():
    SECURITY DEFINER + search_path pinned + REVOKE PUBLIC + GRANT app_rls.
    Vigencia half-open [valid_from, valid_to) evaluada con CURRENT_DATE (UTC).
    """
    return [
        "CREATE OR REPLACE FUNCTION authz_grant_party_ids("
        "p_user_id bigint, p_org_id bigint) "
        "RETURNS SETOF bigint LANGUAGE sql STABLE SECURITY DEFINER "
        "SET search_path = pg_catalog, public AS $$ "
        "SELECT g.grantor_party_id FROM access_grants g "
        "WHERE g.organization_id = p_org_id "
        "AND (g.grantee_user_id = p_user_id "
        "OR g.grantee_organization_id IN ("
        "SELECT m.organization_id FROM memberships m WHERE m.user_id = p_user_id)) "
        "AND g.valid_from <= CURRENT_DATE "
        "AND (g.valid_to IS NULL OR g.valid_to > CURRENT_DATE) $$",
        "REVOKE EXECUTE ON FUNCTION authz_grant_party_ids(bigint, bigint) FROM PUBLIC",
        f"GRANT EXECUTE ON FUNCTION authz_grant_party_ids(bigint, bigint) TO {APP_ROLE}",
    ]
```

- [ ] **Step 4: Baseline amendment #8 — regen canónica en container**

Procedimiento canónico W1/W2/W3/lineage/exact-precision (autogenerate temporal contra DB virgen DENTRO del container → splice entre markers → MISMO revision id `a9977ac077e5` → borrar el temporal):

```bash
make dev   # el servicio migrate aplica el baseline VIEJO al boot
docker compose -f compose.yaml -f compose.dev.yaml exec backend sh -c '
  cd /app &&
  uv run alembic downgrade base &&
  uv run alembic revision --autogenerate -m "tmp_sp2_full_ddl"
'
```

**Verificación del diff antes del splice:** el archivo temporal vs el baseline actual debe diferir SOLO en: (a) el CHECK `ck_access_grants_valid_range` con `>=`, (b) `restatement_log.account_id` + su FK + el índice `ix_restatement_log_organization_id_account_id`. Si aparece CUALQUIER otro delta (tablas, constraints ajenos), STOP — plan-vs-reality drift, reportar antes de seguir.

Splice en `a9977ac077e5_tier1_baseline.py`:
1. Reemplazar la sección autogenerada entre los markers por la del temporal.
2. En la sección manual (donde el baseline aplica la función de sistema frozen), agregar el bloque frozen de `authz_grant_party_ids` — copia VERBATIM de las 3 sentencias de `authz_grant_function_sql()` (el baseline no importa de `ibkr_control.*`), junto al bloque existente de `system_credentialed_org_ids`.
3. Borrar el archivo temporal.
4. Agregar al docstring del baseline el bloque **Amendment #8** (después del #7):

```
**Amendment #8 (SP2 — authorization):** (a) ``ck_access_grants_valid_range``
pasa a ``valid_to >= valid_from`` (vigencia half-open [from, to): intervalo
vacío legal para revoke same-day, SP2-D8); (b) ``restatement_log.account_id``
FK RESTRICT NOT NULL + índice ``(organization_id, account_id)`` (filtro
party-scoped del grantee, SP2-D9); (c) función SECURITY DEFINER
``authz_grant_party_ids(p_user_id, p_org_id)`` (bootstrap del resolver de
autorización — frozen idéntico a ``db/rls.py::authz_grant_function_sql()``,
SP2-D3). Regenerado canónicamente en container (autogenerate temporal contra
DB virgen, splice entre markers).
```

- [ ] **Step 5: Wipe & reload + correr tests**

```bash
docker compose -f compose.yaml -f compose.dev.yaml down -v
make dev
cd backend && uv run pytest tests/test_sp2_schema.py tests/test_migrations.py tests/test_template_clone_fidelity.py -n0 -v
```

Expected: los 4 tests de Task 1 PASS; drift test PASS (autogenerate-diff vacío); fidelity PASS (el template del test infra se reconstruye desde el baseline amendado). **Nota:** la suite COMPLETA todavía NO está verde — el persister aún no puebla `restatement_log.account_id` (NOT NULL) → los tests de W3/golden fallan hasta Task 2. Correr solo los archivos listados.

- [ ] **Step 6: ruff + commit**

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add backend/src/ibkr_control/db/models/access_grants.py \
        backend/src/ibkr_control/db/models/restatements.py \
        backend/src/ibkr_control/db/rls.py \
        backend/alembic/versions/a9977ac077e5_tier1_baseline.py \
        backend/tests/test_sp2_schema.py
git commit -m "feat(sp2): CHECK half-open + restatement_log.account_id + authz_grant_party_ids (SP2-D3/D8/D9, baseline amendment #8)"
```

---

### Task 2: Persister puebla `restatement_log.account_id`

**Files:**
- Modify: `backend/src/ibkr_control/ingest/flex/persister.py` (collector + sibling + insert)
- Test: `backend/tests/test_restatement_log.py` (existente — extender)

- [ ] **Step 1: Failing test**

En `backend/tests/ingest/flex/test_restatements.py` (el archivo ya tiene los helpers `_open_lot`/`_xml_with_open_lots`/`_closed_lot`/`_xml_with_closed_lots` y llama `persist()` directo) agregar al final:

```python
async def test_restatement_rows_carry_account_id(db_session: AsyncSession, sample_org):
    """SP2-D9: cada fila de restatement_log lleva el account_id del hecho afectado
    — en AMBOS kinds (value_update vía audit_sink + sibling_row vía add_sibling)."""
    from ibkr_control.db.models.accounts import Account

    # value_update: mismo natural key, qty material cambia entre ingests.
    await persist(
        session=db_session,
        parsed=_xml_with_open_lots([_open_lot(qty=Decimal("100"))]),
        organization_id=sample_org.id,
        xml_bytes=b"<acct-a/>",
        source="manual_upload",
    )
    await persist(
        session=db_session,
        parsed=_xml_with_open_lots([_open_lot(qty=Decimal("90"))]),
        organization_id=sample_org.id,
        xml_bytes=b"<acct-b/>",
        source="manual_upload",
    )
    # sibling_row: mismo (txn, close_datetime, qty), distinto fifo_pnl.
    await persist(
        session=db_session,
        parsed=_xml_with_closed_lots([_closed_lot(fifo_pnl=Decimal("10.00"))]),
        organization_id=sample_org.id,
        xml_bytes=b"<acct-c/>",
        source="manual_upload",
    )
    await persist(
        session=db_session,
        parsed=_xml_with_closed_lots([_closed_lot(fifo_pnl=Decimal("-2.50"))]),
        organization_id=sample_org.id,
        xml_bytes=b"<acct-d/>",
        source="manual_upload",
    )
    await db_session.commit()

    acct = await db_session.scalar(
        select(Account).where(Account.ibkr_account_id == "U99999001")
    )
    rows = (await db_session.scalars(select(RestatementLog))).all()
    kinds = {r.kind for r in rows}
    assert kinds == {"value_update", "sibling_row"}
    assert all(r.account_id == acct.id for r in rows)
```

Además: los tests EXISTENTES de este archivo FALLAN con `NotNullViolationError` (account_id NOT NULL sin poblar) hasta el Step 2 — esa es la señal TDD del gap completo.

Run: `cd backend && uv run pytest tests/ingest/flex/test_restatements.py -n0 -v`
Expected: FAIL (NotNullViolationError en los INSERT de restatement_log).

- [ ] **Step 2: Implementación en `persister.py`**

Tres puntos (las natural keys de las 3 tablas snapshot YA contienen `account_id` — DB id vía `accounts_map` — porque sus `natural_key_cols` lo incluyen; el sibling path NO lo tiene y hay que dárselo):

(a) `_RestatementCollector.audit_sink` — extraerlo del natural key:

```python
    def audit_sink(
        self, table_name: str, natural_key: dict[str, Any], column_name: str, old: Any, new: Any
    ) -> None:
        self.rows.append(
            {
                "table_name": table_name,
                "account_id": natural_key["account_id"],  # SP2-D9: las 3 snapshot lo tienen en su key
                "natural_key": {k: _json_safe(v) for k, v in natural_key.items()},
                "_natural_key_raw": natural_key,
                "column_name": column_name,
                "old_value": _json_safe(old),
                "new_value": _json_safe(new),
                "kind": "value_update",
            }
        )
```

(b) `add_sibling` gana parámetro explícito `account_id: int` (mismo dict shape, key `"account_id": account_id`). En `_detect_closed_lot_siblings`, el caller necesita el account de la fila insertada: agregar `"account_id"` a la lista `returning_cols` del call `_upsert_immutable_returning_inserted` en `persister.py` (línea ~577: `["transaction_id", "close_datetime", "qty", "fifo_pnl_usd", "close_date"]` → + `"account_id"`) y pasar `row.account_id` a `collector.add_sibling(...)`. (Mismo `transaction_id` ⇒ misma cuenta: el sibling preexistente comparte account por construcción del natural key.)

(c) `_persist_restatements` — agregar `"account_id": row["account_id"]` al dict de `insert_rows`.

- [ ] **Step 3: Correr restatements + API de restatements**

Run: `cd backend && uv run pytest tests/ingest/flex/test_restatements.py tests/api/test_restatements_api.py -n0 -v`
Expected: PASS — incluido el golden (re-ingest idéntico ⇒ 0 restatements) y los siblings con account_id.

- [ ] **Step 4: Suite completa**

Run: `cd backend && uv run pytest -q`
Expected: PASS (la suite vuelve a verde — Task 1 + 2 cierran el schema y su único productor).

- [ ] **Step 5: ruff + commit**

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add backend/src/ibkr_control/ingest/flex/persister.py backend/tests/test_restatement_log.py
git commit -m "feat(sp2): persister puebla restatement_log.account_id (SP2-D9)"
```

---

### Task 3: Contrato GUC gana `read_only` (barrera DB del grantee)

**Files:**
- Modify: `backend/src/ibkr_control/db/rls.py`
- Test: `backend/tests/test_rls_read_only.py` (nuevo)

- [ ] **Step 1: Failing tests**

Crear `backend/tests/test_rls_read_only.py`:

```python
"""SP2-D6 barrera 2: contexto grantee → SET LOCAL transaction_read_only=on.

Self-healing: el listener after_begin re-aplica el flag en CADA transacción
nueva (igual que los GUCs org/user) — un commit intra-request no lo pierde.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from ibkr_control.db.rls import apply_org_context, set_session_org_context

pytestmark = pytest.mark.anyio


async def test_read_only_context_blocks_writes_at_db(db_session, sample_org):
    """Un INSERT en contexto read_only muere en Postgres (25006), sin capa app."""
    set_session_org_context(db_session, org_id=sample_org.id, user_id=None, read_only=True)
    await apply_org_context(db_session, org_id=sample_org.id, user_id=None, read_only=True)
    with pytest.raises(DBAPIError) as exc_info:
        await db_session.execute(
            text(
                "INSERT INTO counterparties (external_id, organization_id) "
                "VALUES ('RO-TEST', :o)"
            ),
            {"o": sample_org.id},
        )
        await db_session.commit()
    assert "read-only" in str(exc_info.value).lower()
    await db_session.rollback()


async def test_read_only_survives_commit(db_session, sample_org):
    """Self-healing: tras un commit, la PRÓXIMA transacción sigue read-only
    (listener after_begin re-aplica el flag stashed)."""
    set_session_org_context(db_session, org_id=sample_org.id, user_id=None, read_only=True)
    await apply_org_context(db_session, org_id=sample_org.id, user_id=None, read_only=True)
    await db_session.execute(text("SELECT 1"))
    await db_session.commit()  # cierra la tx; la próxima autobegins
    val = await db_session.scalar(text("SELECT current_setting('transaction_read_only')"))
    assert val == "on"


async def test_default_context_remains_read_write(db_session, sample_org):
    """Un contexto member (read_only=False, el default) NO bloquea writes."""
    set_session_org_context(db_session, org_id=sample_org.id, user_id=None)
    await apply_org_context(db_session, org_id=sample_org.id, user_id=None)
    val = await db_session.scalar(text("SELECT current_setting('transaction_read_only')"))
    assert val == "off"
```

Run: `cd backend && uv run pytest tests/test_rls_read_only.py -n0 -v`
Expected: FAIL — `TypeError: unexpected keyword argument 'read_only'`.

- [ ] **Step 2: Implementación en `db/rls.py`**

```python
def _org_context_set_config(
    org_id: int, user_id: int | None, read_only: bool
) -> tuple[str, dict[str, str]]:
    """SQL + params to SET LOCAL the RLS GUCs (+ transaction_read_only, SP2-D6).

    transaction_read_only via set_config(..., true) == SET LOCAL: tightening to
    read-only is allowed mid-transaction (the resolver's SELECTs precede it);
    the reverse (off after on, post-query) is what Postgres rejects — never our
    path because each new transaction starts read-write and the listener applies
    the stashed flag at after_begin (before any statement).
    """
    return (
        "SELECT set_config('app.current_org', :o, true), "
        "set_config('app.current_user', :u, true), "
        "set_config('transaction_read_only', :r, true)",
        {
            "o": str(org_id),
            "u": "" if user_id is None else str(user_id),
            "r": "on" if read_only else "off",
        },
    )
```

`apply_org_context` y `set_session_org_context` ganan `read_only: bool = False` (keyword-only, después de `user_id`) y lo threadean; stash key nueva `_READ_ONLY_KEY = "rls_read_only"`; el listener `_reapply_org_context` lee `session.info.get(_READ_ONLY_KEY, False)` y lo pasa a `_org_context_set_config`. Default `False` en todos los call sites existentes (cron, conftest `scope_session_to_org`) — NO tocarlos.

- [ ] **Step 3: Correr tests**

Run: `cd backend && uv run pytest tests/test_rls_read_only.py tests/test_rls.py tests/test_rls_fail_closed.py -n0 -v`
Expected: PASS (incluida la suite RLS existente — el contrato viejo no cambió para callers sin `read_only`).

- [ ] **Step 4: ruff + commit**

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add backend/src/ibkr_control/db/rls.py backend/tests/test_rls_read_only.py
git commit -m "feat(sp2): transaction_read_only en el contrato GUC self-healing (SP2-D6 barrera DB)"
```

---

### Task 4: `authz/` — `AuthzContext` + `resolve_authz` (PDP)

**Files:**
- Create: `backend/src/ibkr_control/authz/__init__.py`
- Create: `backend/src/ibkr_control/authz/context.py`
- Create: `backend/src/ibkr_control/authz/resolver.py`
- Test: `backend/tests/test_authz_resolver.py` (nuevo)

- [ ] **Step 1: Failing tests — matriz del resolver**

Crear `backend/tests/test_authz_resolver.py`:

```python
"""Matriz de resolve_authz (SP2-D3): member/grantee/none × org × vigencia."""

from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from ibkr_control.authz.resolver import resolve_authz
from ibkr_control.db.models.access_grants import AccessGrant

pytestmark = pytest.mark.anyio


async def _mk_user(owner_session, email, *, org=None, role="member"):
    from ibkr_control.auth.models import User
    from ibkr_control.db.models.memberships import Membership

    u = User(email=email, hashed_password="x", is_active=True, name=email)
    owner_session.add(u)
    await owner_session.flush()
    if org is not None:
        owner_session.add(Membership(user_id=u.id, organization_id=org.id, role=role))
    await owner_session.commit()
    await owner_session.refresh(u)
    return u


async def _mk_org(owner_session, name, type_="firm"):
    from ibkr_control.db.models.organizations import Organization

    org = Organization(type=type_, name=name)
    owner_session.add(org)
    await owner_session.commit()
    await owner_session.refresh(org)
    return org


async def test_single_membership_autoresolves(db_session, sample_org, sample_user):
    ctx = await resolve_authz(db_session, user_id=sample_user.id, requested_org_id=None)
    assert (ctx.org_id, ctx.actor, ctx.role, ctx.party_ids) == (
        sample_org.id, "member", "owner", None,
    )


async def test_multi_membership_without_header_requires_selection(
    db_session, sample_org, sample_user, owner_session
):
    org_b = await _mk_org(owner_session, "Org B", "personal")
    from ibkr_control.db.models.memberships import Membership

    owner_session.add(Membership(user_id=sample_user.id, organization_id=org_b.id, role="member"))
    await owner_session.commit()
    with pytest.raises(HTTPException) as exc:
        await resolve_authz(db_session, user_id=sample_user.id, requested_org_id=None)
    assert (exc.value.status_code, exc.value.detail) == (400, "ORG_SELECTION_REQUIRED")


async def test_no_memberships_no_header_403(db_session, owner_session):
    u = await _mk_user(owner_session, "nobody@t.com")
    with pytest.raises(HTTPException) as exc:
        await resolve_authz(db_session, user_id=u.id, requested_org_id=None)
    assert (exc.value.status_code, exc.value.detail) == (403, "NO_ORG_MEMBERSHIP")


async def test_requested_own_org_resolves_member_role(db_session, sample_org, sample_user):
    ctx = await resolve_authz(
        db_session, user_id=sample_user.id, requested_org_id=sample_org.id
    )
    assert (ctx.actor, ctx.role) == ("member", "owner")


async def test_requested_org_without_access_403(db_session, sample_user, owner_session):
    other = await _mk_org(owner_session, "Ajena", "personal")
    with pytest.raises(HTTPException) as exc:
        await resolve_authz(db_session, user_id=sample_user.id, requested_org_id=other.id)
    assert (exc.value.status_code, exc.value.detail) == (403, "NO_ORG_ACCESS")


async def test_valid_grant_resolves_grantee_context(
    db_session, sample_org, sample_party, owner_session
):
    cpa = await _mk_user(owner_session, "cpa@t.com")
    db_session.add(
        AccessGrant(
            grantor_party_id=sample_party.id,
            grantee_user_id=cpa.id,
            organization_id=sample_org.id,
            valid_from=date.today(),
        )
    )
    await db_session.commit()
    ctx = await resolve_authz(db_session, user_id=cpa.id, requested_org_id=sample_org.id)
    assert (ctx.actor, ctx.role) == ("grantee", "read_only")
    assert ctx.party_ids == frozenset({sample_party.id})


async def test_grant_not_yet_valid_is_403(db_session, sample_org, sample_party, owner_session):
    cpa = await _mk_user(owner_session, "cpa2@t.com")
    db_session.add(
        AccessGrant(
            grantor_party_id=sample_party.id,
            grantee_user_id=cpa.id,
            organization_id=sample_org.id,
            valid_from=date.today() + timedelta(days=1),  # futuro
        )
    )
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        await resolve_authz(db_session, user_id=cpa.id, requested_org_id=sample_org.id)
    assert exc.value.detail == "NO_ORG_ACCESS"


async def test_grant_never_requires_explicit_org(
    db_session, sample_org, sample_party, owner_session
):
    """User sin memberships + un grant: SIN header → NO_ORG_MEMBERSHIP (el
    contexto cross-org jamás se adivina, SP2-D3 punto 3)."""
    cpa = await _mk_user(owner_session, "cpa3@t.com")
    db_session.add(
        AccessGrant(
            grantor_party_id=sample_party.id,
            grantee_user_id=cpa.id,
            organization_id=sample_org.id,
            valid_from=date.today(),
        )
    )
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        await resolve_authz(db_session, user_id=cpa.id, requested_org_id=None)
    assert exc.value.detail == "NO_ORG_MEMBERSHIP"


async def test_membership_wins_over_grant_in_same_org(
    db_session, sample_org, sample_party, sample_user
):
    """Si sos member del org solicitado, sos member (el grant no degrada)."""
    db_session.add(
        AccessGrant(
            grantor_party_id=sample_party.id,
            grantee_user_id=sample_user.id,
            organization_id=sample_org.id,
            valid_from=date.today(),
        )
    )
    await db_session.commit()
    ctx = await resolve_authz(
        db_session, user_id=sample_user.id, requested_org_id=sample_org.id
    )
    assert ctx.actor == "member"
```

Run: `cd backend && uv run pytest tests/test_authz_resolver.py -n0 -v`
Expected: FAIL — `ModuleNotFoundError: ibkr_control.authz`.

- [ ] **Step 2: Implementación**

`backend/src/ibkr_control/authz/context.py`:

```python
"""AuthzContext: el resultado tipado de la decisión de autorización (SP2-D3)."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class AuthzContext:
    """Quién actúa, en qué org, con qué rol efectivo y qué scope de party.

    party_ids is None => sin restricción (member del org).
    party_ids set     => grantee: solo la data de esos grantor parties.
    """

    org_id: int
    user_id: int
    actor: Literal["member", "grantee"]
    role: str  # owner|admin|member (member) · read_only (grantee)
    party_ids: frozenset[int] | None
```

`backend/src/ibkr_control/authz/resolver.py`:

```python
"""resolve_authz — el PDP (SP2-D1/D3). Funciones puras: deciden, no setean RLS.

Memberships primero (sin RLS — identidad); grants después vía la función
SECURITY DEFINER authz_grant_party_ids (el resolver corre ANTES de que exista
contexto RLS; ver spec SP2-D3 §mecanismo). El contexto cross-org via grant es
SIEMPRE explícito — sin header no se adivina.
"""

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.authz.context import AuthzContext
from ibkr_control.db.models.memberships import Membership

__all__ = ["resolve_authz"]


async def _grant_party_ids(session: AsyncSession, *, user_id: int, org_id: int) -> frozenset[int]:
    rows = await session.execute(
        text("SELECT * FROM authz_grant_party_ids(:u, :o)"), {"u": user_id, "o": org_id}
    )
    return frozenset(rows.scalars().all())


async def resolve_authz(
    session: AsyncSession, *, user_id: int, requested_org_id: int | None
) -> AuthzContext:
    memberships = (
        await session.execute(
            select(Membership.organization_id, Membership.role).where(
                Membership.user_id == user_id
            )
        )
    ).all()
    role_by_org = {org_id: role for org_id, role in memberships}

    if requested_org_id is None:
        if not role_by_org:
            raise HTTPException(status_code=403, detail="NO_ORG_MEMBERSHIP")
        if len(role_by_org) > 1:
            raise HTTPException(status_code=400, detail="ORG_SELECTION_REQUIRED")
        org_id, role = next(iter(role_by_org.items()))
        return AuthzContext(
            org_id=org_id, user_id=user_id, actor="member", role=role, party_ids=None
        )

    if requested_org_id in role_by_org:
        return AuthzContext(
            org_id=requested_org_id,
            user_id=user_id,
            actor="member",
            role=role_by_org[requested_org_id],
            party_ids=None,
        )

    party_ids = await _grant_party_ids(session, user_id=user_id, org_id=requested_org_id)
    if not party_ids:
        raise HTTPException(status_code=403, detail="NO_ORG_ACCESS")
    return AuthzContext(
        org_id=requested_org_id,
        user_id=user_id,
        actor="grantee",
        role="read_only",
        party_ids=party_ids,
    )
```

`backend/src/ibkr_control/authz/__init__.py`:

```python
from ibkr_control.authz.context import AuthzContext
from ibkr_control.authz.resolver import resolve_authz

__all__ = ["AuthzContext", "resolve_authz"]
```

- [ ] **Step 3: Correr tests**

Run: `cd backend && uv run pytest tests/test_authz_resolver.py -n0 -v`
Expected: PASS (9 tests).

- [ ] **Step 4: ruff + commit**

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add backend/src/ibkr_control/authz/ backend/tests/test_authz_resolver.py
git commit -m "feat(sp2): paquete authz — AuthzContext + resolve_authz (PDP, SP2-D3)"
```

---

### Task 5: `scopes.py` — taxonomía + `require_scope` (PEP) + header

**Files:**
- Create: `backend/src/ibkr_control/authz/scopes.py`
- Modify: `backend/src/ibkr_control/authz/__init__.py`
- Test: `backend/tests/test_authz_scopes.py` (nuevo)

- [ ] **Step 1: Failing tests**

Crear `backend/tests/test_authz_scopes.py`:

```python
"""require_scope (PEP, SP2-D2/D5): scope check + header + contexto RLS."""

import pytest
from fastapi import HTTPException

from ibkr_control.authz.scopes import ROLE_SCOPES, SCOPES, require_scope

pytestmark = pytest.mark.anyio


def test_role_scope_map_matches_spec_table():
    """SP2-D5: la tabla del spec, lockeada como data."""
    assert ROLE_SCOPES["owner"] == SCOPES  # owner: todo
    assert "grants:write" not in ROLE_SCOPES["admin"]
    assert "connections:write" in ROLE_SCOPES["admin"]
    assert ROLE_SCOPES["member"] == frozenset({"ops:read", "data:read", "grants:read"})
    assert ROLE_SCOPES["read_only"] == frozenset({"data:read", "grants:read"})


def test_unknown_scope_fails_loud_at_factory():
    """Un typo en el scope rompe al IMPORT del módulo del endpoint, no en runtime."""
    with pytest.raises(ValueError):
        require_scope("connectons:write")  # typo


async def test_insufficient_scope_403(client, auth_headers_with_org, owner_engine):
    """Un member (no admin) rebota un endpoint connections:write."""
    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ibkr_control.auth.models import User
    from ibkr_control.db.models.memberships import Membership
    from ibkr_control.db.models.organizations import Organization

    # Registrar un segundo user y meterlo como MEMBER en el org del owner.
    await client.post(
        "/api/auth/register",
        json={"email": "plain_member@test.com", "password": "supersecret123", "name": "Member"},
    )
    maker = async_sessionmaker(owner_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        member = await s.scalar(select(User).where(User.email == "plain_member@test.com"))
        org = await s.scalar(
            select(Organization).where(Organization.name == "Org Owner Household")
        )
        s.add(Membership(user_id=member.id, organization_id=org.id, role="member"))
        await s.commit()
    login = await client.post(
        "/api/auth/jwt/login",
        data={"username": "plain_member@test.com", "password": "supersecret123"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    r = await client.post(
        "/api/connections",
        headers=headers,
        json={"display_name": "x", "query_id": "1", "token": "t"},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "INSUFFICIENT_SCOPE"

    # Pero el mismo member SÍ lee ops:read.
    r = await client.get("/api/connections", headers=headers)
    assert r.status_code == 200


async def test_malformed_org_header_422(client, auth_headers_with_org):
    r = await client.get(
        "/api/connections",
        headers={**auth_headers_with_org, "X-Organization-Id": "abc"},
    )
    assert r.status_code == 422


async def test_org_header_for_foreign_org_403(client, auth_headers_with_org):
    r = await client.get(
        "/api/connections",
        headers={**auth_headers_with_org, "X-Organization-Id": "999999"},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "NO_ORG_ACCESS"
```

Nota: `test_insufficient_scope_403` y los de header dependen de que `api/connections.py` ya use `require_scope` — quedan en FAIL (o XFAIL temporal) hasta Task 6; los dos primeros (unit del mapa + factory) deben pasar al final de ESTA task. El implementer corre Task 5 y 6 seguidas; el commit de Task 5 incluye solo los unit, moviendo los de endpoint a Task 6 si prefiere commits verdes (preferido: este plan pone los tests de endpoint en Task 6 — copiarlos allá y dejar acá solo los 2 unit).

- [ ] **Step 2: Implementación**

`backend/src/ibkr_control/authz/scopes.py`:

```python
"""Taxonomía de scopes + mapeo rol→scopes + require_scope (PEP) — SP2-D2/D5.

El mapeo es DATA (un dict), no ifs dispersos. require_scope(scope) devuelve la
dependency FastAPI que: autentica → resuelve AuthzContext (PDP) → chequea scope
→ setea contexto RLS (GUCs + read_only si grantee, SP2-D6) → devuelve el ctx.
Marca la dependency con ._authz_scope para el route-sweep guard.
"""

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.auth.backend import current_active_user
from ibkr_control.auth.models import User
from ibkr_control.authz.context import AuthzContext
from ibkr_control.authz.resolver import resolve_authz
from ibkr_control.db.rls import apply_org_context, set_session_org_context
from ibkr_control.db.session import get_async_session

__all__ = ["SCOPES", "ROLE_SCOPES", "require_scope"]

SCOPES: frozenset[str] = frozenset(
    {
        "connections:write",
        "setup:write",
        "ingest:trigger",
        "ops:read",
        "data:read",
        "grants:write",
        "grants:read",
    }
)

# SP2-D5 — la tabla del spec como data. owner = todo; admin = todo menos
# grants:write (compartir data fiscal es del dueño); member = read-only;
# read_only (grantee) = data-plane + descubrir sus grants.
ROLE_SCOPES: dict[str, frozenset[str]] = {
    "owner": SCOPES,
    "admin": SCOPES - {"grants:write"},
    "member": frozenset({"ops:read", "data:read", "grants:read"}),
    "read_only": frozenset({"data:read", "grants:read"}),
}


def require_scope(scope: str):
    """Factory de la dependency PEP. Fail-loud en import si el scope no existe."""
    if scope not in SCOPES:
        raise ValueError(f"Unknown authz scope: {scope!r}")

    async def dependency(
        user: User = Depends(current_active_user),
        session: AsyncSession = Depends(get_async_session),
        x_organization_id: int | None = Header(None, alias="X-Organization-Id"),
    ) -> AuthzContext:
        ctx = await resolve_authz(
            session, user_id=user.id, requested_org_id=x_organization_id
        )
        if scope not in ROLE_SCOPES[ctx.role]:
            raise HTTPException(status_code=403, detail="INSUFFICIENT_SCOPE")
        read_only = ctx.actor == "grantee"
        set_session_org_context(
            session, org_id=ctx.org_id, user_id=ctx.user_id, read_only=read_only
        )
        await apply_org_context(
            session, org_id=ctx.org_id, user_id=ctx.user_id, read_only=read_only
        )
        return ctx

    dependency._authz_scope = scope  # marker del route-sweep guard (Task 7)
    return dependency
```

Actualizar `authz/__init__.py` exportando `require_scope`, `ROLE_SCOPES`, `SCOPES`.

- [ ] **Step 3: Correr unit tests**

Run: `cd backend && uv run pytest tests/test_authz_scopes.py -n0 -v -k "map or factory"`
Expected: PASS los 2 unit.

- [ ] **Step 4: ruff + commit**

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add backend/src/ibkr_control/authz/ backend/tests/test_authz_scopes.py
git commit -m "feat(sp2): require_scope + taxonomía de scopes como data (PEP, SP2-D2/D5)"
```

---

### Task 6: Migración de TODOS los endpoints + muerte de `api/_context.py`

**Files:**
- Modify: `backend/src/ibkr_control/api/connections.py`, `imports.py`, `ingest.py`, `setup.py`, `health.py`
- Delete: `backend/src/ibkr_control/api/_context.py`
- Modify/Delete: `backend/tests/api/test_org_context.py` (lógica subsumida por `test_authz_resolver.py` — BORRAR el archivo; sus 4 casos ya están en la matriz de Task 4)
- Test: mover acá los tests de endpoint de Task 5 Step 1 (`test_insufficient_scope_403`, `test_malformed_org_header_422`, `test_org_header_for_foreign_org_403`) dentro de `backend/tests/test_authz_scopes.py`

- [ ] **Step 1: Failing tests** — los 3 tests de endpoint listados arriba (ya escritos en Task 5 Step 1). Run: `cd backend && uv run pytest tests/test_authz_scopes.py -n0 -v` → los de endpoint FAIL (los endpoints aún usan `org_context`, que no conoce header ni scopes).

- [ ] **Step 2: Migrar endpoint por endpoint**

Patrón mecánico — en cada archivo:

```python
# ANTES
from ibkr_control.api._context import org_context
...
    org_id: int = Depends(org_context),

# DESPUÉS
from ibkr_control.authz import AuthzContext, require_scope
...
    ctx: AuthzContext = Depends(require_scope("<scope de la tabla>")),
```

y dentro del body, reemplazar usos de `org_id` por `ctx.org_id`. Scopes por ruta: ver la tabla del header del plan. Detalles:

- `connections.py`: GET list → `ops:read`; los otros 6 → `connections:write`.
- `setup.py`: los 9 → `setup:write`.
- `imports.py` upload → `ingest:trigger`.
- `ingest.py`: trigger → `ingest:trigger`; logs → `ops:read`; restatements → `data:read`; `stream/{job_id}` NO se toca (user-scoped D1, allowlist).
- `health.py` → `ops:read`.
- Donde el endpoint ya recibía `user: User = Depends(current_active_user)` SOLO para autenticar (p.ej. `imports.py`), borrar ese parámetro si quedó sin uso: `require_scope` ya autentica (el `user_id` queda en `ctx.user_id`). En `ingest.py::trigger_manual_refresh` el `user.id` se usa para el JobTracker → usar `ctx.user_id`.
- Borrar `backend/src/ibkr_control/api/_context.py` y `backend/tests/api/test_org_context.py`. Grep final obligatorio: `grep -rn "org_context\|_context" backend/src backend/tests` → cero referencias (excepto `set_session_org_context`/`apply_org_context` de `db/rls.py`, que son otra cosa).

- [ ] **Step 3: Suite completa**

Run: `cd backend && uv run pytest -q`
Expected: PASS — los tests de endpoint existentes usan `auth_headers_with_org` (role=owner → todos los scopes pasan) y siguen verdes; los 3 nuevos de Task 5/6 pasan.

- [ ] **Step 4: ruff + commit**

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add -A backend/src/ibkr_control/api backend/tests
git commit -m "feat(sp2): todos los endpoints migrados a require_scope; org_context eliminado (SP2-D2)"
```

---

### Task 7: Route-sweep guard estructural

**Files:**
- Test: `backend/tests/test_route_authz_coverage.py` (nuevo)

- [ ] **Step 1: Escribir el guard (es un test — TDD se invierte: debe pasar YA, y fallar si alguien agrega una ruta sin scope)**

```python
"""Route-sweep guard (SP2-D2): TODA ruta declara require_scope o está en el
allowlist nombrado. Agregar un endpoint sin autorización rompe la suite —
misma clase de guard estructural que el boot guard RLS (PR #7).
"""

from fastapi.routing import APIRoute

from ibkr_control.main import create_app

# (method, path) → por qué NO lleva require_scope. Cada entrada nombrada.
ALLOWLIST: dict[tuple[str, str], str] = {
    ("GET", "/health"): "liveness probe del container — sin auth por diseño",
    ("GET", "/api/ingest/stream/{job_id}"): "SSE user-scoped: ownership en JobTracker (D1)",
    # /api/auth/* (fastapi-users) y /api/settings/* (user-scoped, sin org) se
    # allowlistean por PREFIJO abajo — son recursos de identidad/usuario, no de org.
}
ALLOWLISTED_PREFIXES: dict[str, str] = {
    "/api/auth": "authN de fastapi-users — anterior a cualquier contexto org",
    "/api/settings": "UserSettings es user-scoped (sin organization_id)",
}


def _has_scope(route: APIRoute) -> bool:
    return any(
        getattr(dep.call, "_authz_scope", None) is not None
        for dep in route.dependant.dependencies
    )


def test_every_route_declares_scope_or_is_allowlisted():
    app = create_app()
    unprotected = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue  # docs, openapi, etc. — no son APIRoute de la app
        for method in route.methods - {"HEAD", "OPTIONS"}:
            if (method, route.path) in ALLOWLIST:
                continue
            if any(route.path.startswith(p) for p in ALLOWLISTED_PREFIXES):
                continue
            if not _has_scope(route):
                unprotected.append((method, route.path))
    assert not unprotected, (
        f"Rutas sin require_scope ni allowlist (agregá el scope o una entrada "
        f"NOMBRADA al allowlist): {unprotected}"
    )


def test_allowlist_has_no_stale_entries():
    """Una entrada del allowlist cuya ruta ya no existe es ruido — falla."""
    app = create_app()
    actual = {
        (m, r.path)
        for r in app.routes
        if isinstance(r, APIRoute)
        for m in r.methods - {"HEAD", "OPTIONS"}
    }
    stale = [k for k in ALLOWLIST if k not in actual]
    assert not stale, f"Entradas del allowlist sin ruta viva: {stale}"
```

Nota: si `route.dependant.dependencies` no expone el sub-dependency con el atributo (depende de cómo FastAPI anida dependants), el implementer ajusta `_has_scope` para recorrer el dependant tree recursivamente (`dep.dependencies` anidadas) — el contrato del test NO cambia. Verificar contra una ruta conocida (`GET /api/connections`) antes de dar por bueno el mecanismo: el test debe PASAR con los endpoints ya migrados y FALLAR si se le quita el scope a una ruta (probarlo a mano una vez, no commitear esa prueba).

- [ ] **Step 2: Correr — debe PASAR (Task 6 ya migró todo)**

Run: `cd backend && uv run pytest tests/test_route_authz_coverage.py -n0 -v`
Expected: PASS los 2.

- [ ] **Step 3: ruff + commit**

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add backend/tests/test_route_authz_coverage.py
git commit -m "test(sp2): route-sweep guard — toda ruta declara scope o allowlist nombrado (SP2-D2)"
```

---

### Task 8: CRUD de grants (`/api/grants`)

**Files:**
- Create: `backend/src/ibkr_control/api/grants.py`
- Modify: `backend/src/ibkr_control/api/_schemas.py` (schemas al final)
- Modify: `backend/src/ibkr_control/main.py` (registrar router)
- Test: `backend/tests/api/test_grants.py` (nuevo)

- [ ] **Step 1: Failing tests**

Crear `backend/tests/api/test_grants.py`:

```python
"""CRUD /api/grants (SP2-D7): owner-only write, revoke=valid_to, direcciones."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.anyio


async def _seed_ids(owner_engine):
    """Devuelve (org_id, party_id) del org del fixture auth_headers_with_org."""
    from ibkr_control.db.models.organizations import Organization
    from ibkr_control.db.models.parties import Party

    maker = async_sessionmaker(owner_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        org = await s.scalar(
            select(Organization).where(Organization.name == "Org Owner Household")
        )
        party = await s.scalar(select(Party).where(Party.organization_id == org.id))
        return org.id, party.id


async def _mk_firm(owner_engine, name="Estudio X"):
    from ibkr_control.db.models.organizations import Organization

    maker = async_sessionmaker(owner_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        firm = Organization(type="firm", name=name)
        s.add(firm)
        await s.commit()
        await s.refresh(firm)
        return firm.id


async def test_owner_creates_and_lists_grant(client, auth_headers_with_org, owner_engine):
    org_id, party_id = await _seed_ids(owner_engine)
    firm_id = await _mk_firm(owner_engine)
    r = await client.post(
        "/api/grants",
        headers=auth_headers_with_org,
        json={"grantor_party_id": party_id, "grantee_organization_id": firm_id},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["role"] == "read_only"
    assert body["valid_to"] is None

    r = await client.get("/api/grants", headers=auth_headers_with_org)
    assert r.status_code == 200
    [g] = r.json()
    assert g["direction"] == "granted"


async def test_revoke_same_day_is_legal_and_immediate(
    client, auth_headers_with_org, owner_engine
):
    """SP2-D7/D8: revoke = valid_to hoy (half-open ⇒ inactivo ya), mismo día OK."""
    _, party_id = await _seed_ids(owner_engine)
    firm_id = await _mk_firm(owner_engine, "Estudio Y")
    r = await client.post(
        "/api/grants",
        headers=auth_headers_with_org,
        json={"grantor_party_id": party_id, "grantee_organization_id": firm_id},
    )
    gid = r.json()["id"]
    r = await client.post(f"/api/grants/{gid}/revoke", headers=auth_headers_with_org)
    assert r.status_code == 200
    assert r.json()["valid_to"] is not None
    # Doble revoke → 409 (ya inactivo).
    r = await client.post(f"/api/grants/{gid}/revoke", headers=auth_headers_with_org)
    assert r.status_code == 409


async def test_foreign_party_404(client, auth_headers_with_org, second_auth_headers_with_org, owner_engine):
    """El grantor party de OTRO org no se encuentra (RLS) → 404 sin leak."""
    from ibkr_control.db.models.organizations import Organization
    from ibkr_control.db.models.parties import Party

    maker = async_sessionmaker(owner_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        other_org = await s.scalar(
            select(Organization).where(Organization.name == "Org Owner 2 Household")
        )
        other_party = await s.scalar(
            select(Party).where(Party.organization_id == other_org.id)
        )
    firm_id = await _mk_firm(owner_engine, "Estudio Z")
    r = await client.post(
        "/api/grants",
        headers=auth_headers_with_org,
        json={"grantor_party_id": other_party.id, "grantee_organization_id": firm_id},
    )
    assert r.status_code == 404


async def test_self_grant_422(client, auth_headers_with_org, owner_engine):
    org_id, party_id = await _seed_ids(owner_engine)
    r = await client.post(
        "/api/grants",
        headers=auth_headers_with_org,
        json={"grantor_party_id": party_id, "grantee_organization_id": org_id},
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "SELF_GRANT"
```

Run: `cd backend && uv run pytest tests/api/test_grants.py -n0 -v`
Expected: FAIL — 404 (router no existe).

- [ ] **Step 2: Schemas** — al final de `backend/src/ibkr_control/api/_schemas.py`:

```python
class GrantCreate(BaseModel):
    grantor_party_id: int
    grantee_organization_id: int | None = None
    grantee_user_id: int | None = None
    valid_from: date | None = None  # default: CURRENT_DATE (en el endpoint)
    valid_to: date | None = None


class GrantRead(BaseModel):
    id: int
    grantor_party_id: int
    grantee_organization_id: int | None
    grantee_user_id: int | None
    organization_id: int
    role: str
    valid_from: date
    valid_to: date | None
    direction: Literal["granted", "received"]
```

- [ ] **Step 3: Router** — `backend/src/ibkr_control/api/grants.py`:

```python
"""CRUD /api/grants (SP2-D7): el write-path del enforcement de SP2.

Revoke = valid_to (NUNCA DELETE — audit trail). Semántica half-open
[valid_from, valid_to). Sin UPDATE: cambiar vigencia/grantee = revocar + crear.
La policy RLS grant_visibility muestra ambas direcciones en el GET; el write
queda doblemente guardado (scope grants:write + WITH CHECK org = current_org).
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.api._schemas import GrantCreate, GrantRead
from ibkr_control.authz import AuthzContext, require_scope
from ibkr_control.db.models.access_grants import AccessGrant
from ibkr_control.db.models.parties import Party
from ibkr_control.db.session import get_async_session

router = APIRouter(prefix="/grants", tags=["grants"])


def _ser(g: AccessGrant, *, org_id: int) -> GrantRead:
    return GrantRead(
        id=g.id,
        grantor_party_id=g.grantor_party_id,
        grantee_organization_id=g.grantee_organization_id,
        grantee_user_id=g.grantee_user_id,
        organization_id=g.organization_id,
        role=g.role,
        valid_from=g.valid_from,
        valid_to=g.valid_to,
        direction="granted" if g.organization_id == org_id else "received",
    )


@router.get("", response_model=list[GrantRead])
async def list_grants(
    ctx: AuthzContext = Depends(require_scope("grants:read")),
    session: AsyncSession = Depends(get_async_session),
) -> list[GrantRead]:
    # RLS (grant_visibility) scopea: grantor-org + grantee — ambas direcciones.
    rows = await session.scalars(select(AccessGrant).order_by(AccessGrant.id))
    return [_ser(g, org_id=ctx.org_id) for g in rows.all()]


@router.post("", response_model=GrantRead, status_code=201)
async def create_grant(
    payload: GrantCreate,
    ctx: AuthzContext = Depends(require_scope("grants:write")),
    session: AsyncSession = Depends(get_async_session),
) -> GrantRead:
    if (payload.grantee_organization_id is None) == (payload.grantee_user_id is None):
        raise HTTPException(status_code=422, detail="GRANTEE_EXACTLY_ONE")
    if payload.grantee_organization_id == ctx.org_id:
        raise HTTPException(status_code=422, detail="SELF_GRANT")
    # El grantor party se resuelve bajo RLS del org activo: ajeno = no existe.
    party = await session.scalar(select(Party).where(Party.id == payload.grantor_party_id))
    if party is None:
        raise HTTPException(status_code=404, detail="PARTY_NOT_FOUND")
    today = (await session.execute(select(func.current_date()))).scalar_one()
    grant = AccessGrant(
        grantor_party_id=party.id,
        grantee_organization_id=payload.grantee_organization_id,
        grantee_user_id=payload.grantee_user_id,
        organization_id=ctx.org_id,
        valid_from=payload.valid_from or today,
        valid_to=payload.valid_to,
    )
    session.add(grant)
    try:
        await session.commit()
    except IntegrityError:
        # FK del grantee inexistente / CHECK de rango — sin leak de cuál.
        await session.rollback()
        raise HTTPException(status_code=422, detail="INVALID_GRANT") from None
    await session.refresh(grant)
    return _ser(grant, org_id=ctx.org_id)


@router.post("/{grant_id}/revoke", response_model=GrantRead)
async def revoke_grant(
    grant_id: int,
    ctx: AuthzContext = Depends(require_scope("grants:write")),
    session: AsyncSession = Depends(get_async_session),
) -> GrantRead:
    # Dirección explícita: solo grants OTORGADOS por el org activo (la policy
    # también muestra los recibidos, pero revocarlos no es de este org — y el
    # WITH CHECK lo rebotaría con un error DB; el WHERE da un 404 limpio).
    grant = await session.scalar(
        select(AccessGrant).where(
            AccessGrant.id == grant_id, AccessGrant.organization_id == ctx.org_id
        )
    )
    if grant is None:
        raise HTTPException(status_code=404, detail="GRANT_NOT_FOUND")
    today = (await session.execute(select(func.current_date()))).scalar_one()
    if grant.valid_to is not None and grant.valid_to <= today:
        raise HTTPException(status_code=409, detail="GRANT_ALREADY_INACTIVE")
    grant.valid_to = today  # half-open: inactivo desde YA (SP2-D7)
    await session.commit()
    await session.refresh(grant)
    return _ser(grant, org_id=ctx.org_id)
```

En `backend/src/ibkr_control/main.py`: importar y registrar `grants_router` junto a los demás (`app.include_router(grants_router, prefix="/api")`).

- [ ] **Step 4: Correr tests + suite + route-sweep**

Run: `cd backend && uv run pytest tests/api/test_grants.py tests/test_route_authz_coverage.py -n0 -v && uv run pytest -q`
Expected: PASS (el guard de Task 7 ve los 3 endpoints nuevos con scope).

- [ ] **Step 5: ruff + commit**

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add backend/src/ibkr_control/api/grants.py backend/src/ibkr_control/api/_schemas.py \
        backend/src/ibkr_control/main.py backend/tests/api/test_grants.py
git commit -m "feat(sp2): CRUD /api/grants — revoke=valid_to half-open, owner-only (SP2-D7)"
```

---

### Task 9: Party scope — `visible_account_ids` + filtro en restatements

**Files:**
- Create: `backend/src/ibkr_control/authz/party_scope.py`
- Modify: `backend/src/ibkr_control/authz/__init__.py`
- Modify: `backend/src/ibkr_control/api/ingest.py` (`list_restatements`)
- Test: `backend/tests/test_authz_party_scope.py` (nuevo)

- [ ] **Step 1: Failing tests**

```python
"""visible_account_ids (SP2-D6 barrera 3): None para members; set histórico
de cuentas del party para grantees (participación pasada cuenta — el año
fiscal N exige cuentas ya cerradas/vendidas)."""

from datetime import date

import pytest

from ibkr_control.authz.context import AuthzContext
from ibkr_control.authz.party_scope import visible_account_ids

pytestmark = pytest.mark.anyio


def _member_ctx(org_id: int) -> AuthzContext:
    return AuthzContext(org_id=org_id, user_id=1, actor="member", role="owner", party_ids=None)


def _grantee_ctx(org_id: int, party_ids: set[int]) -> AuthzContext:
    return AuthzContext(
        org_id=org_id, user_id=1, actor="grantee", role="read_only",
        party_ids=frozenset(party_ids),
    )


async def test_member_is_unrestricted(db_session, sample_org):
    assert await visible_account_ids(db_session, _member_ctx(sample_org.id)) is None


async def test_grantee_sees_only_party_accounts_including_expired(
    db_session, sample_org, sample_party, sample_account
):
    from ibkr_control.db.models.accounts import Account
    from ibkr_control.db.models.participations import Participation

    other_acc = Account(
        ibkr_account_id="U88888888", organization_id=sample_org.id, alias="other", currency="USD"
    )
    db_session.add(other_acc)
    await db_session.flush()
    # Participación EXPIRADA del party en sample_account: igual cuenta (histórico).
    from decimal import Decimal

    db_session.add(
        Participation(
            party_id=sample_party.id,
            account_id=sample_account.id,
            organization_id=sample_org.id,
            pct=Decimal("1.0"),
            valid_from=date(2024, 1, 1),
            valid_to=date(2025, 1, 1),
        )
    )
    await db_session.commit()

    got = await visible_account_ids(db_session, _grantee_ctx(sample_org.id, {sample_party.id}))
    assert got == {sample_account.id}  # other_acc NO (el party nunca participó)


async def test_grantee_restatements_filtered_by_party(
    db_session, sample_org, sample_party, sample_account, sample_flex_import
):
    """El endpoint-level filter: restatements de cuentas fuera del party no se emiten.
    Se prueba la QUERY (no el endpoint HTTP — eso es Task 10): insertar dos
    restatement rows (una en sample_account con participación del party, otra en
    una cuenta ajena al party) y verificar que el WHERE account_id IN (...) del
    endpoint las separa."""
    from sqlalchemy import select

    from ibkr_control.db.models.accounts import Account
    from ibkr_control.db.models.participations import Participation
    from ibkr_control.db.models.restatements import RestatementLog

    other_acc = Account(
        ibkr_account_id="U77777777", organization_id=sample_org.id, alias="o2", currency="USD"
    )
    db_session.add(other_acc)
    await db_session.flush()
    from decimal import Decimal

    db_session.add(
        Participation(
            party_id=sample_party.id,
            account_id=sample_account.id,
            organization_id=sample_org.id,
            pct=Decimal("1.0"),
            valid_from=date(2024, 1, 1),
        )
    )
    for acc in (sample_account, other_acc):
        db_session.add(
            RestatementLog(
                organization_id=sample_org.id,
                flex_import_id=sample_flex_import.id,
                account_id=acc.id,
                table_name="open_position_lots",
                natural_key={"account_id": acc.id},
                column_name="qty",
                old_value="1",
                new_value="2",
                kind="value_update",
            )
        )
    await db_session.commit()

    visible = await visible_account_ids(
        db_session, _grantee_ctx(sample_org.id, {sample_party.id})
    )
    rows = (
        await db_session.scalars(
            select(RestatementLog).where(RestatementLog.account_id.in_(visible))
        )
    ).all()
    assert {r.account_id for r in rows} == {sample_account.id}
```

Run: `cd backend && uv run pytest tests/test_authz_party_scope.py -n0 -v`
Expected: FAIL — `ModuleNotFoundError: ibkr_control.authz.party_scope`.

- [ ] **Step 2: Implementación**

`backend/src/ibkr_control/authz/party_scope.py`:

```python
"""visible_account_ids — la barrera 3 del grantee (SP2-D6).

None = sin restricción (member). Para grantees: cuentas con CUALQUIER
participación (histórica o vigente) de los grantor parties — revisar el año
fiscal N exige cuentas que el party ya vendió/cerró (SCD-2: un valid_to pasado
sigue habilitando lectura). Corre bajo el contexto RLS del org ya seteado.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.authz.context import AuthzContext
from ibkr_control.db.models.participations import Participation

__all__ = ["visible_account_ids"]


async def visible_account_ids(session: AsyncSession, ctx: AuthzContext) -> set[int] | None:
    if ctx.party_ids is None:
        return None
    rows = await session.scalars(
        select(Participation.account_id)
        .distinct()
        .where(Participation.party_id.in_(ctx.party_ids))
    )
    return set(rows.all())
```

En `api/ingest.py::list_restatements` (ya migrado a `require_scope("data:read")` en Task 6), después de construir `stmt`:

```python
    visible = await visible_account_ids(session, ctx)
    if visible is not None:
        stmt = stmt.where(RestatementLog.account_id.in_(visible))
```

(import: `from ibkr_control.authz.party_scope import visible_account_ids`). Exportar `visible_account_ids` en `authz/__init__.py`.

- [ ] **Step 3: Correr tests + suite**

Run: `cd backend && uv run pytest tests/test_authz_party_scope.py -n0 -v && uv run pytest -q`
Expected: PASS.

- [ ] **Step 4: ruff + commit**

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add backend/src/ibkr_control/authz/ backend/src/ibkr_control/api/ingest.py \
        backend/tests/test_authz_party_scope.py
git commit -m "feat(sp2): visible_account_ids + filtro party en restatements (SP2-D6/D9)"
```

---

### Task 10: Integration end-to-end del grantee (las 3 barreras por HTTP)

**Files:**
- Test: `backend/tests/api/test_grantee_e2e.py` (nuevo)

- [ ] **Step 1: Escribir la suite E2E (failing solo si Tasks 1-9 dejaron algo suelto — es el test de convergencia)**

```python
"""E2E del contador (SP2): switch cross-org explícito + 3 barreras + revocación.

Seeding como OWNER (cross-tenant); todo el ejercicio va por HTTP con el client
real (app conectada como app_rls — la suite hereda la parity RLS)."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.anyio


@pytest.fixture
async def grantee_world(client, auth_headers_with_org, owner_engine):
    """Household (auth_headers_with_org) + contador con org firm + grant vigente
    + un restatement en una cuenta del party y otro en una cuenta ajena.

    Devuelve dict: cpa_headers, client_org_id, party_account_id, other_account_id,
    grant_id (creado via API por el owner — ejercita el CRUD real)."""
    from ibkr_control.auth.models import User
    from ibkr_control.db.models.accounts import Account
    from ibkr_control.db.models.flex_raw import FlexImport
    from ibkr_control.db.models.memberships import Membership
    from ibkr_control.db.models.organizations import Organization
    from ibkr_control.db.models.participations import Participation
    from ibkr_control.db.models.parties import Party
    from ibkr_control.db.models.restatements import RestatementLog

    # Contador: user + org firm propia.
    await client.post(
        "/api/auth/register",
        json={"email": "cpa@firm.com", "password": "supersecret123", "name": "CPA"},
    )
    maker = async_sessionmaker(owner_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        cpa = await s.scalar(select(User).where(User.email == "cpa@firm.com"))
        firm = Organization(type="firm", name="CPA Firm")
        s.add(firm)
        await s.flush()
        s.add(Membership(user_id=cpa.id, organization_id=firm.id, role="owner"))
        org = await s.scalar(
            select(Organization).where(Organization.name == "Org Owner Household")
        )
        party = await s.scalar(select(Party).where(Party.organization_id == org.id))
        # Cuentas + participación del party SOLO en la primera + restatements.
        await s.execute(
            text("SELECT set_config('app.current_org', :o, true)"), {"o": str(org.id)}
        )
        acc1 = Account(
            ibkr_account_id="U10000001", organization_id=org.id, alias="a1", currency="USD"
        )
        acc2 = Account(
            ibkr_account_id="U10000002", organization_id=org.id, alias="a2", currency="USD"
        )
        s.add_all([acc1, acc2])
        await s.flush()
        s.add(
            Participation(
                party_id=party.id, account_id=acc1.id, organization_id=org.id,
                pct=Decimal("1.0"), valid_from=date(2024, 1, 1),
            )
        )
        fi = FlexImport(
            organization_id=org.id, anyo=2025, xml_hash="e2e-hash", xml_size_bytes=1,
            xml_bytes=b"<x/>", source="manual_upload",
            period_covered_from=date(2025, 1, 1), period_covered_to=date(2025, 12, 31),
            year_status="sealed", status="ok",
        )
        s.add(fi)
        await s.flush()
        for acc in (acc1, acc2):
            s.add(
                RestatementLog(
                    organization_id=org.id, flex_import_id=fi.id, account_id=acc.id,
                    table_name="closed_lots", natural_key={"account_id": acc.id},
                    column_name="*", old_value="1", new_value="2", kind="sibling_row",
                )
            )
        await s.commit()
        org_id, party_id, a1, a2 = org.id, party.id, acc1.id, acc2.id

    # El OWNER crea el grant via API (write-path real).
    r = await client.post(
        "/api/grants",
        headers=auth_headers_with_org,
        json={"grantor_party_id": party_id, "grantee_user_id": cpa.id},
    )
    assert r.status_code == 201, r.text

    login = await client.post(
        "/api/auth/jwt/login",
        data={"username": "cpa@firm.com", "password": "supersecret123"},
    )
    cpa_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    return {
        "cpa_headers": cpa_headers,
        "client_org_id": org_id,
        "party_account_id": a1,
        "other_account_id": a2,
        "grant_id": r.json()["id"],
        "owner_headers": auth_headers_with_org,
    }


async def test_grantee_reads_party_scoped_restatements(client, grantee_world):
    w = grantee_world
    headers = {**w["cpa_headers"], "X-Organization-Id": str(w["client_org_id"])}
    r = await client.get("/api/ingest/restatements", headers=headers)
    assert r.status_code == 200, r.text
    accounts = {row["account_id"] for row in r.json()}
    assert accounts == {w["party_account_id"]}  # la cuenta ajena al party NO


async def test_grantee_without_header_stays_in_own_org(client, grantee_world):
    """Sin header, el contador resuelve SU org (firm) — no el del cliente."""
    r = await client.get("/api/ingest/restatements", headers=grantee_world["cpa_headers"])
    assert r.status_code == 200
    assert r.json() == []  # su firm no tiene restatements


async def test_grantee_denied_admin_plane_and_writes(client, grantee_world):
    w = grantee_world
    headers = {**w["cpa_headers"], "X-Organization-Id": str(w["client_org_id"])}
    r = await client.get("/api/connections", headers=headers)  # ops:read
    assert r.status_code == 403
    assert r.json()["detail"] == "INSUFFICIENT_SCOPE"
    r = await client.post(  # connections:write
        "/api/connections", headers=headers,
        json={"display_name": "x", "query_id": "1", "token": "t"},
    )
    assert r.status_code == 403


async def test_revocation_is_immediate(client, grantee_world):
    w = grantee_world
    r = await client.post(
        f"/api/grants/{w['grant_id']}/revoke", headers=w["owner_headers"]
    )
    assert r.status_code == 200
    headers = {**w["cpa_headers"], "X-Organization-Id": str(w["client_org_id"])}
    r = await client.get("/api/ingest/restatements", headers=headers)
    assert r.status_code == 403
    assert r.json()["detail"] == "NO_ORG_ACCESS"


async def test_grantee_lists_received_grants_from_own_org(client, grantee_world):
    r = await client.get("/api/grants", headers=grantee_world["cpa_headers"])
    assert r.status_code == 200
    [g] = r.json()
    assert g["direction"] == "received"
```

- [ ] **Step 2: Correr**

Run: `cd backend && uv run pytest tests/api/test_grantee_e2e.py -n0 -v`
Expected: PASS los 5. Si alguno falla, es un bug REAL de integración de Tasks 1-9 — debuggear, no ablandar el test.

- [ ] **Step 3: Suite completa + ruff + commit**

```bash
cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format .
git add backend/tests/api/test_grantee_e2e.py
git commit -m "test(sp2): E2E del grantee — switch explícito, party scope, denegaciones, revocación inmediata"
```

---

### Task 11: OpenAPI/orval regen canónico + frontend build

**Files:**
- Modify: `frontend/openapi.json` + `frontend/src/lib/api/generated.ts` (generados)

- [ ] **Step 1: Regen canónico (flujo documentado — NUNCA offline)**

```bash
make dev   # backend container corriendo el código nuevo
export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"
cd frontend && pnpm openapi:gen
```

- [ ] **Step 2: Verificar build + tests frontend**

```bash
cd frontend && pnpm lint && pnpm test -- --run && pnpm build
```

Expected: lint 0 errores; vitest PASS (sin cambios de UI — los endpoints viejos no cambiaron shape; los nuevos de grants solo agregan al cliente generado); build OK.

- [ ] **Step 3: Commit**

```bash
git add frontend/openapi.json frontend/src/lib/api/generated.ts
git commit -m "chore(sp2): orval regen — endpoints /api/grants en el cliente TS"
```

---

### Task 12: Boot smoke prod-local + verificación final + docs

**Files:**
- Modify: `CLAUDE.md` (bullet SP2 en "Estado del programa SaaS" + ⏯ SIGUIENTE)
- Modify: `docs/specs/2026-06-03-saas-program-roadmap.md` (fila SP2 → mergeado/estado)

- [ ] **Step 1: Boot smoke prod-local**

```bash
docker compose -f compose.yaml down -v
make prod-local
docker compose -f compose.yaml logs migrate | tail -5
docker compose -f compose.yaml exec backend python -c "print('boot ok')"
docker compose -f compose.yaml exec db psql -U postgres -d ibkr -c \
  "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname='app_rls';"
docker compose -f compose.yaml exec db psql -U postgres -d ibkr -c \
  "SELECT proname, prosecdef FROM pg_proc WHERE proname='authz_grant_party_ids';"
docker compose -f compose.yaml exec db psql -U postgres -d ibkr -c "\d restatement_log" | head -20
```

Verificar: (1) `migrate` aplica el baseline #8 sin error; (2) `backend` arranca como `app_rls` (`rolsuper=f`, `rolbypassrls=f` — boot guard); (3) la función existe con `prosecdef=t`; (4) `restatement_log` muestra `account_id bigint NOT NULL`. (Ajustar nombres de DB/user del psql a los del compose real si difieren — ver `compose.yaml`.)

- [ ] **Step 2: Suite completa final + lint**

```bash
cd backend && uv run pytest -q && uv run ruff check .
cd frontend && pnpm lint && pnpm build
```

Expected: todo verde. Anotar el test count final (442 + ~30 nuevos).

- [ ] **Step 3: Docs**

- `CLAUDE.md`: bullet nuevo "SP2 — Authorization" en §Estado del programa SaaS (modelo del bullet de W3: decisiones, números de tests, spec/plan links) + actualizar "⏯ SIGUIENTE" a SP3 (el roadmap SSOT ya tiene la dirección pre-decidida del onboarding gate).
- Roadmap: fila SP2 → estado con PR.

```bash
git add CLAUDE.md docs/specs/2026-06-03-saas-program-roadmap.md
git commit -m "docs(sp2): estado del programa — SP2 authorization completo"
```

- [ ] **Step 4: PR**

```bash
git push -u origin saas/sp2-authorization
gh pr create --title "SP2 — Authorization: choke-point, enforcement de grants party-scoped, roles con dientes" --body "$(cat <<'EOF'
## Summary
- SP2-D1..D10 per docs/specs/2026-06-12-sp2-authorization-design.md
- Choke-point require_scope en el 100% de los endpoints + route-sweep guard estructural; org_context eliminado
- Enforcement del grant cross-org (contador): AuthzContext, header X-Organization-Id, 3 barreras (scope app / SET LOCAL transaction_read_only self-healing / visible_account_ids party-scoped)
- CRUD /api/grants (revoke=valid_to half-open, owner-only) + función SECURITY DEFINER authz_grant_party_ids
- restatement_log.account_id de primera clase + baseline amendment #8 (regen canónica, T1-D14)

## Test plan
- [ ] CI backend + frontend verdes
- [ ] Boot smoke prod-local (migrate baseline #8, backend como app_rls)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review del plan (hecho al escribirlo)

- **Cobertura del spec:** D1 (Task 4-5 módulo authz), D2 (Tasks 5-7), D3 (Tasks 1, 4, 5 — función + resolver + header), D4 (Task 5), D5 (Task 5 mapa + Task 6 asignación por ruta), D6 (Task 3 barrera DB, Task 5 barrera scope, Task 9 barrera party, Task 10 E2E), D7 (Task 8), D8 (Task 1), D9 (Tasks 1-2 + 9), D10 (Task 1 amendment). Testing §5 del spec: matriz (T4), integration app_rls (T10), barrera DB aislada (T3), route-sweep (T7), header (T5/T6), CRUD (T8), persister (T2).
- **Orden de dependencia:** Task 1→2 (schema antes que productor; suite roja entre ambas, documentado en T1 Step 5), 3→5 (read_only antes del PEP que lo usa), 4→5 (resolver antes del PEP), 5→6 (PEP antes de migrar), 6→7 (guard pasa solo con todo migrado), 6→9 (el endpoint ya recibe ctx), 8→10 (E2E usa el CRUD).
- **Sin placeholders:** todo step de código muestra el código; los puntos donde el implementer adapta nombres a helpers existentes lo dicen explícitamente con el comando para encontrarlos.
