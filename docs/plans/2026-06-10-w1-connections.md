# W1+W4 Connections/Providers + State Machine — Implementation Plan (PR-1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar `flex_credentials` por el modelo Plaid-grade `institutions → connections → connection_ibkr_flex` con state machine de conexión (W4) cableada al cron/job/API/UI, y linaje `connection_id` en `flex_imports`/`ingest_log`.

**Architecture:** Expand/contract (parallel change): la migración *expand* crea las tablas nuevas + copia datos + reescribe la función `SECURITY DEFINER`; los consumidores se reescriben task a task con la suite verde; la migración *contract* dropea `flex_credentials` al final. Transiciones de estado SOLO vía `ingest/connection_state.py` (T1-D5). Spec: `docs/specs/2026-06-10-tier1-worldclass-model-design.md` (T1-S1..S4, T1-D1..D6, D15).

**Tech Stack:** SQLAlchemy 2.x async + Alembic (autogenerate canónico en container) + FastAPI + Pydantic + Next.js/TanStack/orval + pytest/testcontainers + vitest/Playwright.

**Branch:** `saas/w1-connections` (ya existe, spec commiteado). NO crear branches nuevos; verificar `git branch --show-current` después de cada commit.

**Reglas del repo que aplican a TODOS los tasks:**
- TDD: failing test → impl mínima → test verde → commit.
- Migraciones: generar con `alembic revision --autogenerate` DENTRO del container backend (`docker compose -f compose.yaml -f compose.dev.yaml exec backend uv run alembic revision --autogenerate -m "..."`) y luego editar a mano lo no-autogenerable. Remover el falso positivo `apscheduler_jobs` si aparece.
- Tests corren desde el HOST: `cd backend && uv run pytest` (testcontainers necesita el Docker socket del host).
- `cd backend && uv run ruff check . && uv run ruff format .` antes de cada commit.
- No emojis en código. `Decimal` para dinero. Settings via `get_settings()` dentro de funciones.

**CR-3 (checkpoint resuelto durante planning):** los errores auth-class del Flex WS son `FlexAuthError` (códigos `1003/1004/1018`, ver `client.py::ERR_AUTH_CODES`) y `FlexQueryNotFoundError` (`1005`, query_id mal configurado — también requiere acción del usuario). Ambos → `reauth_required`. `FlexBusyError` (1001), `FlexPollTimeoutError`, `httpx` errors → fallo transitorio (`consecutive_failures++`).

---

## File Map (visión completa del PR)

| Acción | Path | Responsabilidad |
|---|---|---|
| Create | `backend/src/ibkr_control/db/models/institutions.py` | Catálogo global de brokers (control plane) |
| Create | `backend/src/ibkr_control/db/models/connections.py` | `Connection` + `ConnectionIbkrFlex` |
| Create | `backend/src/ibkr_control/ingest/connection_state.py` | State machine (única puerta de transiciones) |
| Create | `backend/src/ibkr_control/api/connections.py` | Router `/api/connections` |
| Create | `backend/alembic/versions/<rev1>_w1_connections_expand.py` | Expand: tablas nuevas + data copy + función nueva + policies |
| Create | `backend/alembic/versions/<rev2>_w1_drop_flex_credentials.py` | Contract: drop tabla vieja |
| Modify | `backend/alembic/versions/a1f2c3d4e5b6_system_enum_function.py` | Congelar SQL histórico (quitar import vivo) |
| Modify | `backend/src/ibkr_control/db/__init__.py` | Exports |
| Modify | `backend/src/ibkr_control/db/rls.py` | `ORG_SCOPED_TABLES` + builder de la función |
| Modify | `backend/src/ibkr_control/db/models/flex_raw.py` | `FlexImport.connection_id` |
| Modify | `backend/src/ibkr_control/db/models/ingest_log.py` | `IngestLog.connection_id` |
| Modify | `backend/src/ibkr_control/ingest/log.py` | param `connection_id` |
| Modify | `backend/src/ibkr_control/ingest/flex/job.py` | `run()` itera connections activas + transiciones |
| Modify | `backend/src/ibkr_control/ingest/flex/persister.py` | `persist(connection_id=...)` |
| Modify | `backend/src/ibkr_control/scheduler/jobs.py` | docstring/comментarios (función renombrada de fuente) |
| Modify | `backend/src/ibkr_control/api/_schemas.py` | Schemas Connection* |
| Modify | `backend/src/ibkr_control/api/setup.py` | step1/state/step2_detect sobre connections |
| Modify | `backend/src/ibkr_control/api/health.py` | estado de connections en la respuesta |
| Modify | `backend/src/ibkr_control/main.py` | router connections (reemplaza credentials) |
| Delete | `backend/src/ibkr_control/db/models/flex_credentials.py` | (Task 7) |
| Delete | `backend/src/ibkr_control/api/credentials.py` | (Task 4) |
| Create | `frontend/src/components/settings/ConnectionsSection.tsx` | Cards con badge de estado + acciones |
| Modify | `frontend/src/components/settings/RotateTokenModal.tsx` | Opera sobre connection id |
| Modify | `frontend/src/components/wizard/Step1Credentials.tsx` | display_name opcional (endpoint igual) |
| Delete | `frontend/src/components/settings/FlexCredentialsSection.tsx` | Reemplazada |
| Tests | `backend/tests/ingest/test_connection_state.py` (new), `backend/tests/api/test_connections_api.py` (new), `backend/tests/test_w1_connections_migration.py` (new), updates en `test_job.py`, `test_job_rls.py`, `test_scheduler.py`, `test_rls.py`, `test_credentials.py` (delete), `test_setup_*.py`, `test_identity_models.py`, vitest + Playwright | |

---

### Task 0: Congelar el SQL histórico de `a1f2c3d4e5b6` (replay safety)

La migración `a1f2c3d4e5b6_system_enum_function.py` importa `system_enum_function_sql()` VIVO desde `db/rls.py`. Cuando el builder cambie a leer `connections` (Task 1), un `alembic upgrade head` sobre DB fresca rompería: esa revisión corre ANTES de que `connections` exista y Postgres valida el body de funciones `LANGUAGE sql` al crearlas. Las migraciones deben ser time-frozen.

**Files:**
- Modify: `backend/alembic/versions/a1f2c3d4e5b6_system_enum_function.py`
- Verify: `backend/tests/test_migrations.py` (replay fresco ya existe — es el test que atraparía el bug)

- [ ] **Step 1: Leer la migración completa** (`Read` del archivo) y copiar el output EXACTO de `system_enum_function_sql()` actual (las 3 sentencias: `CREATE OR REPLACE FUNCTION system_credentialed_org_ids() ... FROM flex_credentials $$`, `REVOKE ...`, `GRANT ...` — ver `db/rls.py:178-185`).

- [ ] **Step 2: Reemplazar el import vivo por el SQL congelado.** En `upgrade()` (y `downgrade()` si también importa), sustituir:

```python
    from ibkr_control.db.rls import system_enum_function_sql

    for stmt in system_enum_function_sql():
        op.execute(stmt)
```

por las sentencias inline (texto idéntico al output actual del builder):

```python
    # FROZEN 2026-06-10 (W1): este SQL era el output de
    # db/rls.py::system_enum_function_sql() al momento de esta revisión.
    # Las migraciones no importan builders vivos — el builder evoluciona
    # (W1 lo apunta a `connections`) y esta revisión corre antes de que esa
    # tabla exista; Postgres valida el body de funciones LANGUAGE sql al crearlas.
    for stmt in [
        "CREATE OR REPLACE FUNCTION system_credentialed_org_ids() "
        "RETURNS SETOF bigint LANGUAGE sql STABLE SECURITY DEFINER "
        "SET search_path = pg_catalog, public AS $$ "
        "SELECT DISTINCT organization_id FROM flex_credentials $$",
        "REVOKE EXECUTE ON FUNCTION system_credentialed_org_ids() FROM PUBLIC",
        "GRANT EXECUTE ON FUNCTION system_credentialed_org_ids() TO app_rls",
    ]:
        op.execute(stmt)
```

(Aplicar el mismo tratamiento al `downgrade()` con su contenido actual congelado.)

- [ ] **Step 3: Verificar replay fresco**

Run: `cd backend && uv run pytest tests/test_migrations.py -v`
Expected: PASS (mismo resultado que antes — el SQL es byte-idéntico).

- [ ] **Step 4: Commit**

```bash
git add backend/alembic/versions/a1f2c3d4e5b6_system_enum_function.py
git commit -m "fix(migrations): freeze system_enum_function SQL inline (no live builder imports)"
```

---

### Task 1: Modelos nuevos + migración expand

**Files:**
- Create: `backend/src/ibkr_control/db/models/institutions.py`
- Create: `backend/src/ibkr_control/db/models/connections.py`
- Modify: `backend/src/ibkr_control/db/models/flex_raw.py` (FlexImport.connection_id)
- Modify: `backend/src/ibkr_control/db/models/ingest_log.py` (connection_id)
- Modify: `backend/src/ibkr_control/db/__init__.py`
- Modify: `backend/src/ibkr_control/db/rls.py` (ORG_SCOPED_TABLES + system_enum_function_sql)
- Create: `backend/alembic/versions/<rev1>_w1_connections_expand.py`
- Test: `backend/tests/test_w1_connections_migration.py`

- [ ] **Step 1: Escribir el failing test de la migración** en `backend/tests/test_w1_connections_migration.py`. Seguir el patrón de los migration tests existentes (testcontainers + upgrade a la revisión previa + seed + upgrade head). Contenido:

```python
"""Migration tests W1 expand: data-copy flex_credentials -> connections + detail,
seed de institutions, función SECURITY DEFINER apuntando a connections."""

# Usar las fixtures/helpers del migration-test existente más reciente
# (tests/test_migrations.py y el patrón de test_phaseX_migration que sobrevivió
# al squash). Estructura:

def test_expand_copies_credentials_to_connections(fresh_pg_engine):
    # 1. alembic upgrade fd27737af54e  (revisión previa: account-multihome)
    # 2. INSERT org de prueba + 2 rows en flex_credentials (mismo org —
    #    el caso >1 login por org) con token_encrypted=b"tok", ytd_query_id='111'/'222'
    # 3. alembic upgrade head
    # 4. SELECT: institutions tiene 1 row code='ibkr'
    # 5. SELECT: connections tiene 2 rows para el org, status='active',
    #    provider_type='ibkr_flex', institution_id=el de 'ibkr'
    # 6. SELECT: connection_ibkr_flex tiene 2 rows con los token/query_id ORIGINALES
    #    (mapeo 1:1 por fila, no cartesiano)
    # 7. SELECT prosrc FROM pg_proc WHERE proname='system_credentialed_org_ids'
    #    → contiene 'FROM connections' y "status <> 'disabled'"
    ...

def test_expand_downgrade_reversible(fresh_pg_engine):
    # upgrade head → downgrade -1 → las tablas nuevas no existen,
    # flex_credentials intacta (expand NUNCA borra rows de flex_credentials),
    # la función vuelve a leer flex_credentials
    ...
```

(El subagent escribe el cuerpo real con los helpers del archivo de migración tests vigente — leerlo primero; los asserts listados son el contrato.)

Run: `cd backend && uv run pytest tests/test_w1_connections_migration.py -v`
Expected: FAIL (revisión inexistente / tablas inexistentes).

- [ ] **Step 2: Crear `backend/src/ibkr_control/db/models/institutions.py`:**

```python
"""Institutions = catálogo global de brokers/proveedores (control plane, sin RLS).

Mismo plano que trm_days (dato de sistema, una sola verdad): el catálogo de
instituciones no es de ningún tenant. Seeded por migración — no es input de
usuario. W1, spec 2026-06-10 T1-D3.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, text
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class Institution(Base):
    __tablename__ = "institutions"
    __table_args__ = (
        {
            "comment": (
                "Control plane (global, sin RLS, como trm_days): catálogo de "
                "instituciones. Seeded por migración, no input de usuario."
            )
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
```

- [ ] **Step 3: Crear `backend/src/ibkr_control/db/models/connections.py`:**

```python
"""Connection = vínculo org↔institución con credenciales y estado de sync (W1+W4).

Patrón Plaid Item: `connections` es org-scoped (RLS) y genérica; el detalle
provider-specific vive en una tabla 1:1 tipada por provider (T1-D1) con
integridad de subtipo enforced en SQL (T1-D2): UNIQUE(id, provider_type) en el
padre + FK compuesto (connection_id, provider_type) + CHECK del literal en la
detail — imposible colgar una detail ibkr_flex de una connection de otro
provider. Las transiciones de `status` pasan SOLO por
ibkr_control.ingest.connection_state (T1-D5) — nunca UPDATE directo.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base

PROVIDER_IBKR_FLEX = "ibkr_flex"
CONNECTION_STATUSES = ("active", "degraded", "reauth_required", "disabled")


class Connection(Base):
    __tablename__ = "connections"
    __table_args__ = (
        CheckConstraint("provider_type IN ('ibkr_flex')", name="provider_type"),
        CheckConstraint(
            "status IN ('active', 'degraded', 'reauth_required', 'disabled')",
            name="status",
        ),
        CheckConstraint(
            "last_sync_status IS NULL OR last_sync_status IN ('ok', 'failed')",
            name="last_sync_status",
        ),
        # Ancla del FK compuesto de subtipo (T1-D2).
        UniqueConstraint("id", "provider_type", name="uq_connections_id_provider_type"),
        Index(None, "organization_id"),
        {
            "comment": (
                "Org-scoped (RLS). Vínculo org<->institución (patrón Plaid Item). "
                "Config provider-specific en la detail 1:1 (connection_ibkr_flex). "
                "status SOLO vía ingest/connection_state.py (W4)."
            )
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    institution_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("institutions.id", ondelete="RESTRICT"), nullable=False
    )
    provider_type: Mapped[str] = mapped_column(
        String, nullable=False, default=PROVIDER_IBKR_FLEX
    )
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    # default Python + server_default juntos: con expire_on_commit=False, un
    # server_default solo deja el atributo None en memoria post-commit
    # (lección Phase 2.8, DataAccessGrant.role).
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="active", server_default=text("'active'")
    )
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_sync_status: Mapped[str | None] = mapped_column(String, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class ConnectionIbkrFlex(Base):
    __tablename__ = "connection_ibkr_flex"
    __table_args__ = (
        CheckConstraint("provider_type = 'ibkr_flex'", name="provider_type"),
        ForeignKeyConstraint(
            ["connection_id", "provider_type"],
            ["connections.id", "connections.provider_type"],
            ondelete="CASCADE",
        ),
        Index(None, "organization_id"),
        {
            "comment": (
                "Detail 1:1 tipada del provider ibkr_flex (T1-D1, cero JSONB). "
                "Org-scoped (RLS). Subtipo enforced por FK compuesto + CHECK (T1-D2)."
            )
        },
    )

    connection_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    provider_type: Mapped[str] = mapped_column(
        String, nullable=False, default=PROVIDER_IBKR_FLEX, server_default=text("'ibkr_flex'")
    )
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    token_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    query_id: Mapped[str] = mapped_column(String, nullable=False)
    last_rotated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
```

- [ ] **Step 4: Agregar `connection_id` a `FlexImport` y `IngestLog`.**

En `flex_raw.py`, dentro de `class FlexImport`, después de `organization_id`:

```python
    # W1: linaje import -> connection. NULL para manual_upload (no hay conexión)
    # y para imports que sobreviven al borrado de su conexión (SET NULL —
    # append-only ledger: el hecho del import no muere con la credencial).
    connection_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("connections.id", ondelete="SET NULL"), nullable=True
    )
```

En `ingest_log.py`, después de `organization_id` (mismo bloque y comment análogo):

```python
    connection_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("connections.id", ondelete="SET NULL"), nullable=True
    )
```

Agregar `Index(None, "connection_id")` en `__table_args__` de ambas (lección sp1-db-hardening: FK sin índice = seq scan en el SET NULL del delete).

- [ ] **Step 5: Exports y RLS.** En `db/__init__.py` agregar (manteniendo `FlexCredentials` por ahora — se va en Task 7):

```python
from ibkr_control.db.models.institutions import Institution  # noqa: F401
from ibkr_control.db.models.connections import Connection, ConnectionIbkrFlex  # noqa: F401
```

y sumar `"Institution", "Connection", "ConnectionIbkrFlex"` a `__all__`.

En `db/rls.py`:
1. `ORG_SCOPED_TABLES`: agregar `"connections"` y `"connection_ibkr_flex"` (mantener `"flex_credentials"` hasta Task 7). `institutions` NO va (control plane).
2. Reemplazar el body de `system_enum_function_sql()` (el docstring se conserva, actualizando la mención de tabla):

```python
    return [
        "CREATE OR REPLACE FUNCTION system_credentialed_org_ids() "
        "RETURNS SETOF bigint LANGUAGE sql STABLE SECURITY DEFINER "
        "SET search_path = pg_catalog, public AS $$ "
        "SELECT DISTINCT organization_id FROM connections "
        "WHERE provider_type = 'ibkr_flex' AND status <> 'disabled' $$",
        "REVOKE EXECUTE ON FUNCTION system_credentialed_org_ids() FROM PUBLIC",
        f"GRANT EXECUTE ON FUNCTION system_credentialed_org_ids() TO {APP_ROLE}",
    ]
```

- [ ] **Step 6: Generar la migración canónicamente** (container corriendo con `make dev`):

```bash
docker compose -f compose.yaml -f compose.dev.yaml exec backend \
  uv run alembic revision --autogenerate -m "w1_connections_expand"
```

Remover el falso positivo `apscheduler_jobs` si aparece. El autogenerate produce: create `institutions`, `connections`, `connection_ibkr_flex`, add column + FK + index en `flex_imports`/`ingest_log`. Verificar `down_revision = "fd27737af54e"`.

- [ ] **Step 7: Completar a mano la migración** (después del DDL autogenerado, en `upgrade()`):

```python
    # --- Hand-written section (W1 expand) ---
    bind = op.get_bind()

    # 1. Seed del catálogo (control plane, T1-D3).
    bind.execute(
        sa.text(
            "INSERT INTO institutions (code, name) VALUES ('ibkr', 'Interactive Brokers') "
            "ON CONFLICT (code) DO NOTHING"
        )
    )
    ibkr_id = bind.execute(
        sa.text("SELECT id FROM institutions WHERE code = 'ibkr'")
    ).scalar_one()

    # 2. Data-copy flex_credentials -> connections + connection_ibkr_flex.
    #    Loop por fila (no JOIN por organization_id): >1 credencial por org es
    #    legal y un join produciría mapeo cartesiano.
    rows = bind.execute(
        sa.text(
            "SELECT id, organization_id, token_encrypted, ytd_query_id, last_rotated_at "
            "FROM flex_credentials ORDER BY id"
        )
    ).fetchall()
    for r in rows:
        conn_id = bind.execute(
            sa.text(
                "INSERT INTO connections "
                "(organization_id, institution_id, provider_type, status, consecutive_failures) "
                "VALUES (:org, :inst, 'ibkr_flex', 'active', 0) RETURNING id"
            ),
            {"org": r.organization_id, "inst": ibkr_id},
        ).scalar_one()
        bind.execute(
            sa.text(
                "INSERT INTO connection_ibkr_flex "
                "(connection_id, provider_type, organization_id, token_encrypted, "
                " query_id, last_rotated_at) "
                "VALUES (:cid, 'ibkr_flex', :org, :tok, :qid, :rot)"
            ),
            {
                "cid": conn_id,
                "org": r.organization_id,
                "tok": r.token_encrypted,
                "qid": r.ytd_query_id,
                "rot": r.last_rotated_at,
            },
        )
    # NOTA: expand NO borra flex_credentials (contract = revisión separada, Task 7).

    # 3. RLS policies para las tablas nuevas (frozen inline — las migraciones
    #    no importan builders vivos; ver Task 0).
    for table in ("connections", "connection_ibkr_flex"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY org_isolation ON {table} "
            "USING (organization_id = NULLIF(current_setting('app.current_org', true), '')::bigint) "
            "WITH CHECK (organization_id = NULLIF(current_setting('app.current_org', true), '')::bigint)"
        )

    # 4. Función SECURITY DEFINER ahora enumera connections (frozen inline).
    op.execute(
        "CREATE OR REPLACE FUNCTION system_credentialed_org_ids() "
        "RETURNS SETOF bigint LANGUAGE sql STABLE SECURITY DEFINER "
        "SET search_path = pg_catalog, public AS $$ "
        "SELECT DISTINCT organization_id FROM connections "
        "WHERE provider_type = 'ibkr_flex' AND status <> 'disabled' $$"
    )
    op.execute("REVOKE EXECUTE ON FUNCTION system_credentialed_org_ids() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION system_credentialed_org_ids() TO app_rls")
```

En `downgrade()`: antes del DDL autogenerado (drop tables/columns), restaurar la función vieja (frozen inline, leyendo `flex_credentials`) — los datos de connections se pierden (documentado: flex_credentials conserva los originales porque expand no los borró).

- [ ] **Step 8: Correr la suite de migración + drift test**

Run: `cd backend && uv run pytest tests/test_w1_connections_migration.py tests/test_migrations.py -v`
Expected: PASS (data-copy correcto, downgrade reversible, drift cero contra `Base.metadata`).

- [ ] **Step 9: Ruff + commit**

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add backend/src/ibkr_control/db backend/alembic/versions backend/tests/test_w1_connections_migration.py
git commit -m "feat(w1): institutions + connections + connection_ibkr_flex + expand migration"
```

---

### Task 2: State machine `ingest/connection_state.py`

**Files:**
- Create: `backend/src/ibkr_control/ingest/connection_state.py`
- Test: `backend/tests/ingest/test_connection_state.py`

- [ ] **Step 1: Failing tests** en `backend/tests/ingest/test_connection_state.py`:

```python
"""Unit tests de la state machine de Connection (W4, T1-D5).

Mutadores puros sobre el objeto ORM (sin session) — el caller commitea.
"""

from datetime import datetime, timezone

from ibkr_control.db.models.connections import Connection
from ibkr_control.ingest import connection_state as cs

NOW = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)


def _conn(status: str = "active", failures: int = 0) -> Connection:
    return Connection(
        organization_id=1,
        institution_id=1,
        provider_type="ibkr_flex",
        status=status,
        consecutive_failures=failures,
    )


def test_sync_ok_resets_to_active():
    c = _conn(status="degraded", failures=3)
    cs.mark_sync_ok(c, now=NOW)
    assert c.status == "active"
    assert c.consecutive_failures == 0
    assert c.status_reason is None
    assert c.last_sync_at == NOW
    assert c.last_sync_status == "ok"


def test_sync_ok_after_reauth_required_recovers():
    # El usuario arregló el token por fuera — el retry natural lo detecta.
    c = _conn(status="reauth_required", failures=1)
    cs.mark_sync_ok(c, now=NOW)
    assert c.status == "active"


def test_auth_failure_goes_reauth_required():
    c = _conn()
    cs.mark_auth_failed(c, reason="Flex auth error 1018: bad token", now=NOW)
    assert c.status == "reauth_required"
    assert "1018" in c.status_reason
    assert c.last_sync_status == "failed"


def test_transient_failure_degrades_at_threshold():
    c = _conn()
    cs.mark_sync_failed(c, reason="boom", now=NOW)
    assert c.status == "active"          # 1 fallo: aún no degraded
    assert c.consecutive_failures == 1
    cs.mark_sync_failed(c, reason="boom", now=NOW)
    assert c.status == "degraded"        # 2do consecutivo: umbral R4
    assert c.consecutive_failures == 2


def test_transient_failure_does_not_mask_reauth_required():
    c = _conn(status="reauth_required", failures=2)
    cs.mark_sync_failed(c, reason="net", now=NOW)
    assert c.status == "reauth_required"  # estado más específico se conserva


def test_disabled_is_sticky_for_sync_events():
    c = _conn(status="disabled")
    cs.mark_sync_ok(c, now=NOW)
    assert c.status == "disabled"
    cs.mark_auth_failed(c, reason="x", now=NOW)
    assert c.status == "disabled"


def test_rotate_reactivates_unless_disabled():
    c = _conn(status="reauth_required", failures=4)
    cs.mark_rotated(c)
    assert c.status == "active"
    assert c.consecutive_failures == 0
    d = _conn(status="disabled")
    cs.mark_rotated(d)
    assert d.status == "disabled"        # rotar no des-pausa


def test_set_enabled_toggles():
    c = _conn(status="degraded", failures=5)
    cs.set_enabled(c, enabled=False)
    assert c.status == "disabled"
    cs.set_enabled(c, enabled=True)
    assert c.status == "active"
    assert c.consecutive_failures == 0
```

Run: `cd backend && uv run pytest tests/ingest/test_connection_state.py -v`
Expected: FAIL (módulo inexistente).

- [ ] **Step 2: Implementar `backend/src/ibkr_control/ingest/connection_state.py`:**

```python
"""Única puerta de transiciones del status de Connection (W4, spec T1-D5).

Mutadores puros sobre el objeto ORM — sin session, sin commit (el caller
persiste). Reglas:
- `disabled` es sticky para eventos de sync (solo set_enabled lo cambia).
- `reauth_required` no se enmascara con fallos transitorios posteriores
  (estado más específico gana); un sync OK sí lo limpia (token arreglado).
- 2 fallos transitorios consecutivos => degraded (umbral compartido con el
  health banner R4).
"""

from datetime import datetime

from ibkr_control.db.models.connections import Connection

DEGRADED_THRESHOLD = 2
_REASON_MAX = 500


def mark_sync_ok(conn: Connection, *, now: datetime) -> None:
    if conn.status != "disabled":
        conn.status = "active"
    conn.status_reason = None
    conn.consecutive_failures = 0
    conn.last_sync_at = now
    conn.last_sync_status = "ok"


def mark_auth_failed(conn: Connection, *, reason: str, now: datetime) -> None:
    if conn.status != "disabled":
        conn.status = "reauth_required"
    conn.status_reason = reason[:_REASON_MAX]
    conn.consecutive_failures += 1
    conn.last_sync_at = now
    conn.last_sync_status = "failed"


def mark_sync_failed(conn: Connection, *, reason: str, now: datetime) -> None:
    conn.consecutive_failures += 1
    conn.status_reason = reason[:_REASON_MAX]
    conn.last_sync_at = now
    conn.last_sync_status = "failed"
    if (
        conn.status not in ("disabled", "reauth_required")
        and conn.consecutive_failures >= DEGRADED_THRESHOLD
    ):
        conn.status = "degraded"


def mark_rotated(conn: Connection) -> None:
    conn.consecutive_failures = 0
    conn.status_reason = None
    if conn.status != "disabled":
        conn.status = "active"


def set_enabled(conn: Connection, *, enabled: bool) -> None:
    if enabled:
        conn.status = "active"
        conn.consecutive_failures = 0
        conn.status_reason = None
    else:
        conn.status = "disabled"
```

- [ ] **Step 3: Verificar verde + commit**

Run: `cd backend && uv run pytest tests/ingest/test_connection_state.py -v` → PASS

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add backend/src/ibkr_control/ingest/connection_state.py backend/tests/ingest/test_connection_state.py
git commit -m "feat(w1): connection state machine (W4) — única puerta de transiciones"
```

---

### Task 3: Rewire del job/persister/log al modelo connections

**Files:**
- Modify: `backend/src/ibkr_control/ingest/log.py`
- Modify: `backend/src/ibkr_control/ingest/flex/persister.py`
- Modify: `backend/src/ibkr_control/ingest/flex/job.py`
- Modify: `backend/src/ibkr_control/scheduler/jobs.py` (solo docstrings/comentarios)
- Test: `backend/tests/ingest/flex/test_job.py`, `backend/tests/ingest/flex/test_job_rls.py`, `backend/tests/test_scheduler.py`

- [ ] **Step 1: Failing tests.** En `test_job.py` (leer primero las fixtures existentes — crean `FlexCredentials`; reemplazarlas por `Connection` + `ConnectionIbkrFlex` con un helper):

```python
async def _seed_connection(session, org_id: int, *, query_id: str = "12345",
                           status: str = "active") -> int:
    from ibkr_control.db.models.connections import Connection, ConnectionIbkrFlex
    from ibkr_control.db.models.institutions import Institution
    from sqlalchemy import select

    inst_id = await session.scalar(select(Institution.id).where(Institution.code == "ibkr"))
    conn = Connection(
        organization_id=org_id, institution_id=inst_id,
        provider_type="ibkr_flex", status=status,
    )
    session.add(conn)
    await session.flush()
    session.add(ConnectionIbkrFlex(
        connection_id=conn.id, organization_id=org_id,
        token_encrypted=encrypt_token("test-token-1234567890"), query_id=query_id,
    ))
    await session.commit()
    return conn.id
```

Tests nuevos (además de adaptar los existentes al seeding nuevo):

```python
async def test_run_iterates_all_active_connections(...):
    # 2 connections activas en el org, FlexClient fake que devuelve XML fixture.
    # run() → 2 flex_imports, cada uno con connection_id correcto,
    # 2 ingest_log rows kind='flex' cada una con su connection_id,
    # ambas connections quedan status='active', last_sync_status='ok'.

async def test_run_skips_disabled_connections(...):
    # 1 activa + 1 disabled → solo 1 import; la disabled no se toca.

async def test_run_auth_error_transitions_reauth_required(...):
    # FlexClient fake lanza FlexAuthError("1018", "bad token") para la conn A
    # y devuelve XML válido para la B.
    # → conn A: status='reauth_required', status_reason contiene '1018',
    #   ingest_log row failed con su connection_id.
    # → conn B: import OK (el fallo de A NO bloquea a B).
    # → run() NO lanza (hubo al menos un éxito).

async def test_run_raises_when_all_connections_fail(...):
    # Todas las conns lanzan FlexAuthError → run() re-lanza la última excepción
    # (contrato con _run_manual: el SSE muestra el fallo).

async def test_run_no_active_connections_raises(...):
    # Org sin connections (o todas disabled) → RuntimeError claro.
```

Run: `cd backend && uv run pytest tests/ingest/flex/test_job.py -v`
Expected: FAIL.

- [ ] **Step 2: `ingest/log.py` — param `connection_id`:**

```python
async def ingest_log_entry(
    session: AsyncSession,
    job_kind: str,
    organization_id: int,
    trigger: str,
    connection_id: int | None = None,
):
```

y en el constructor del row: `connection_id=connection_id,` (docstring: "connection_id: conexión que produjo el run (flex); None para manual_upload/TRM/runs pre-W1").

- [ ] **Step 3: `persister.py` — threading de `connection_id`.** Agregar a la firma de `persist()` el kwarg `connection_id: int | None = None` y pasarlo en el INSERT/values de `FlexImport`. Hacer lo mismo en `job._insert_poison_row` (param + values). Leer el persister para ubicar el punto exacto del INSERT de `FlexImport` (es el primer write de `persist()`).

- [ ] **Step 4: Reescribir `job.run()`** (firma y semántica nuevas; `ingest_xml()` queda igual — manual upload no tiene conexión):

```python
async def run(
    session_factory: async_sessionmaker,
    *,
    organization_id: int,
    trigger: str,  # 'cron' | 'manual' | 'wizard'
) -> dict[int, int | None]:
    """Fetchea + ingiere TODAS las connections ibkr_flex activas del org.

    Devuelve {connection_id: flex_import_id | None} (None = hash dedup, sin
    cambios). Aislamiento per-connection: el fallo de una conexión transiciona
    SU estado (connection_state) y registra SU ingest_log row, pero no bloquea
    a las demás. Si TODAS fallaron y hubo >=1 excepción, re-lanza la última
    (contrato con _run_manual: el SSE debe mostrar el fallo).

    RLS: usa set_session_org_context (stash + after_begin listener) además del
    apply inmediato, porque ingest_log_entry commitea por conexión y las
    transiciones de estado corren en transacciones nuevas que necesitan el GUC
    re-aplicado (mecanismo de PR #7).
    """
    from ibkr_control.db.models.connections import Connection, ConnectionIbkrFlex
    from ibkr_control.db.rls import set_session_org_context
    from ibkr_control.ingest import connection_state

    results: dict[int, int | None] = {}
    last_exc: Exception | None = None

    async with session_factory() as session:
        set_session_org_context(session, org_id=organization_id, user_id=None)
        await apply_org_context(session, org_id=organization_id)
        async with advisory_lock(session, scope_id=organization_id, source="flex"):
            conns = (
                await session.scalars(
                    select(Connection)
                    .where(
                        Connection.provider_type == "ibkr_flex",
                        Connection.status != "disabled",
                    )
                    .order_by(Connection.id)
                )
            ).all()
            if not conns:
                raise RuntimeError(
                    f"No active ibkr_flex connections for organization_id={organization_id}"
                )

            for conn in conns:
                detail = await session.get(ConnectionIbkrFlex, conn.id)
                now = datetime.now(timezone.utc)
                try:
                    results[conn.id] = await _run_one_connection(
                        session,
                        organization_id=organization_id,
                        trigger=trigger,
                        connection_id=conn.id,
                        token_encrypted=detail.token_encrypted,
                        query_id=detail.query_id,
                    )
                except (flex_client_mod.FlexAuthError, flex_client_mod.FlexQueryNotFoundError) as exc:
                    # CR-3: auth-class (1003/1004/1018) o query_id mal configurado
                    # (1005) — ambos requieren acción del usuario.
                    connection_state.mark_auth_failed(conn, reason=str(exc), now=now)
                    await session.commit()
                    last_exc = exc
                    continue
                except Exception as exc:
                    # Transitorio (1001 BUSY agotado, timeout, red, parse/persist).
                    # Broad a propósito: cualquier fallo debe transicionar estado
                    # y seguir con la próxima conexión, no matar el loop.
                    connection_state.mark_sync_failed(conn, reason=str(exc), now=now)
                    await session.commit()
                    last_exc = exc
                    continue
                connection_state.mark_sync_ok(conn, now=datetime.now(timezone.utc))
                await session.commit()

    if not any(v is not None or k in results for k, v in results.items()) and last_exc:
        raise last_exc
    if not results and last_exc is not None:
        raise last_exc
    return results
```

(Nota para el implementer: la condición final es simplemente "si `results` quedó vacío y hubo excepción, re-lanzar" — `results` solo gana keys en éxito. Simplificar a `if not results and last_exc is not None: raise last_exc` y eliminar la primera condición redundante.)

`_run_one_connection` es la extracción del cuerpo actual de `run()` (SendRequest + poll + hash check + SAVEPOINT + persist + items_processed), con tres cambios: recibe `connection_id` y lo pasa a `ingest_log_entry(...)`, a `persist(connection_id=...)` y a `_insert_poison_row(connection_id=...)`. El `ingest_log_entry` envuelve TODO el cuerpo (igual que hoy) para que un fallo marque `failed` su propio row.

- [ ] **Step 5: `scheduler/jobs.py`** — el código del loop NO cambia (la función `system_credentialed_org_ids()` ya devuelve orgs con connections activas tras la migración). Actualizar el docstring de `_run_flex_for_all_orgs` (mencionar connections, no flex_credentials) y el comentario del catch `FlexAuthError` (ahora es defensa residual: `run()` ya maneja auth per-connection y solo re-lanza si TODAS fallaron).

- [ ] **Step 6: Adaptar `test_job_rls.py` y `test_scheduler.py`** al seeding por connections (mismo helper `_seed_connection`; moverlo a `tests/ingest/flex/conftest.py` si ambos lo usan — DRY).

- [ ] **Step 7: Suite verde + commit**

Run: `cd backend && uv run pytest tests/ingest -v && uv run pytest -q`
Expected: PASS completo.

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add -A backend
git commit -m "feat(w1): flex job itera connections activas con state transitions per-connection"
```

---

### Task 4: API `/api/connections` (reemplaza `/api/credentials`)

**Files:**
- Create: `backend/src/ibkr_control/api/connections.py`
- Modify: `backend/src/ibkr_control/api/_schemas.py`
- Modify: `backend/src/ibkr_control/main.py`
- Delete: `backend/src/ibkr_control/api/credentials.py`
- Test: `backend/tests/api/test_connections_api.py` (new), delete `backend/tests/api/test_credentials.py`

- [ ] **Step 1: Failing tests** en `test_connections_api.py` (usar las fixtures de auth/org existentes en `tests/api/conftest.py` — leerlas primero; espejo del estilo de `test_credentials.py` actual):

```python
# Casos (cada uno un test):
# - GET /api/connections vacío → []
# - POST /api/connections con token inválido → 401 (FlexClient fake lanza FlexAuthError)
# - POST /api/connections OK (FlexClient fake responde) → 201 con ConnectionRead,
#   status='active', institution_code='ibkr'; el token NO aparece en la response
# - GET lista la creada (query_id visible, token nunca)
# - PATCH {id} display_name → actualizado
# - POST {id}/rotate-token con token válido → last_rotated_at avanza, status='active'
#   (seedear la conn en 'reauth_required' para verificar la transición)
# - POST {id}/disable → status='disabled'; POST {id}/enable → 'active'
# - DELETE {id} → 204; sus flex_imports/ingest_log quedan con connection_id NULL
#   (seedear un flex_import vinculado antes de borrar)
# - Aislamiento RLS: org B no ve ni puede operar la connection de org A (404)
```

Run: `cd backend && uv run pytest tests/api/test_connections_api.py -v` → FAIL.

- [ ] **Step 2: Schemas** en `api/_schemas.py` (reemplazan a los FlexCredentials* — borrarlos en Step 4):

```python
class ConnectionRead(BaseModel):
    id: int
    institution_code: str
    provider_type: str
    display_name: str | None
    status: str
    status_reason: str | None
    last_sync_at: datetime | None
    last_sync_status: str | None
    consecutive_failures: int
    query_id: str
    last_rotated_at: datetime
    created_at: datetime


class ConnectionCreate(BaseModel):
    token: str = Field(min_length=10, max_length=512)
    query_id: str = Field(min_length=1, max_length=64)
    display_name: str | None = Field(default=None, max_length=120)


class ConnectionRotate(BaseModel):
    token: str = Field(min_length=10, max_length=512)
    query_id: str | None = Field(default=None, min_length=1, max_length=64)


class ConnectionUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
```

- [ ] **Step 3: Router `api/connections.py`:**

```python
"""Router /api/connections — CRUD + rotate + enable/disable (W1+W4, T1-D15).

Reemplaza /api/credentials/flex sin alias (pre-deploy, sin consumidores
externos). El token nunca sale en responses. Las transiciones de status pasan
por ingest/connection_state.py — este router nunca asigna status directo.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.api._context import org_context
from ibkr_control.api._schemas import (
    ConnectionCreate,
    ConnectionRead,
    ConnectionRotate,
    ConnectionUpdate,
)
from ibkr_control.db.models.connections import Connection, ConnectionIbkrFlex
from ibkr_control.db.models.institutions import Institution
from ibkr_control.db.session import get_async_session
from ibkr_control.ingest import connection_state
from ibkr_control.ingest.flex import client as flex_client_mod
from ibkr_control.ingest.flex import crypto as flex_crypto_mod

router = APIRouter(prefix="/connections", tags=["connections"])


async def _get_or_404(session: AsyncSession, connection_id: int) -> Connection:
    # RLS ya scopea por org; el 404 cubre tanto inexistente como cross-org
    # (no filtra existencia — mismo criterio que D1/SSE).
    conn = await session.get(Connection, connection_id)
    if conn is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return conn


async def _detail_or_500(session: AsyncSession, connection_id: int) -> ConnectionIbkrFlex:
    detail = await session.get(ConnectionIbkrFlex, connection_id)
    if detail is None:
        # Invariante de subtipo roto — fail-loud, no degradar en silencio.
        raise HTTPException(status_code=500, detail="Connection detail missing")
    return detail


async def _validate_token_against_ibkr(token: str, query_id: str) -> None:
    try:
        client = flex_client_mod.FlexClient(token=token)
        await client.send_request(query_id=query_id)
    except flex_client_mod.FlexAuthError as e:
        raise HTTPException(status_code=401, detail=f"Token invalido: {e.error_message}")
    except flex_client_mod.FlexQueryNotFoundError as e:
        raise HTTPException(status_code=400, detail=f"Query ID invalido: {e.error_message}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"No pude alcanzar IBKR: {e}")


async def _serialize(session: AsyncSession, conn: Connection) -> ConnectionRead:
    detail = await _detail_or_500(session, conn.id)
    code = await session.scalar(select(Institution.code).where(Institution.id == conn.institution_id))
    return ConnectionRead(
        id=conn.id,
        institution_code=code,
        provider_type=conn.provider_type,
        display_name=conn.display_name,
        status=conn.status,
        status_reason=conn.status_reason,
        last_sync_at=conn.last_sync_at,
        last_sync_status=conn.last_sync_status,
        consecutive_failures=conn.consecutive_failures,
        query_id=detail.query_id,
        last_rotated_at=detail.last_rotated_at,
        created_at=conn.created_at,
    )


@router.get("", response_model=list[ConnectionRead])
async def list_connections(
    org_id: int = Depends(org_context),
    session: AsyncSession = Depends(get_async_session),
) -> list[ConnectionRead]:
    conns = (await session.scalars(select(Connection).order_by(Connection.id))).all()
    return [await _serialize(session, c) for c in conns]


@router.post("", response_model=ConnectionRead, status_code=201)
async def create_connection(
    payload: ConnectionCreate,
    org_id: int = Depends(org_context),
    session: AsyncSession = Depends(get_async_session),
) -> ConnectionRead:
    await _validate_token_against_ibkr(payload.token, payload.query_id)
    inst_id = await session.scalar(select(Institution.id).where(Institution.code == "ibkr"))
    conn = Connection(
        organization_id=org_id,
        institution_id=inst_id,
        provider_type="ibkr_flex",
        display_name=payload.display_name,
    )
    session.add(conn)
    await session.flush()
    session.add(
        ConnectionIbkrFlex(
            connection_id=conn.id,
            organization_id=org_id,
            token_encrypted=flex_crypto_mod.encrypt_token(payload.token),
            query_id=payload.query_id,
        )
    )
    await session.commit()
    return await _serialize(session, conn)


@router.patch("/{connection_id}", response_model=ConnectionRead)
async def update_connection(
    connection_id: int,
    payload: ConnectionUpdate,
    org_id: int = Depends(org_context),
    session: AsyncSession = Depends(get_async_session),
) -> ConnectionRead:
    conn = await _get_or_404(session, connection_id)
    if payload.display_name is not None:
        conn.display_name = payload.display_name
    await session.commit()
    return await _serialize(session, conn)


@router.post("/{connection_id}/rotate-token", response_model=ConnectionRead)
async def rotate_token(
    connection_id: int,
    payload: ConnectionRotate,
    org_id: int = Depends(org_context),
    session: AsyncSession = Depends(get_async_session),
) -> ConnectionRead:
    conn = await _get_or_404(session, connection_id)
    detail = await _detail_or_500(session, connection_id)
    query_id = payload.query_id or detail.query_id
    await _validate_token_against_ibkr(payload.token, query_id)
    detail.token_encrypted = flex_crypto_mod.encrypt_token(payload.token)
    detail.query_id = query_id
    detail.last_rotated_at = datetime.now(timezone.utc)
    connection_state.mark_rotated(conn)
    await session.commit()
    return await _serialize(session, conn)


@router.post("/{connection_id}/disable", response_model=ConnectionRead)
async def disable_connection(
    connection_id: int,
    org_id: int = Depends(org_context),
    session: AsyncSession = Depends(get_async_session),
) -> ConnectionRead:
    conn = await _get_or_404(session, connection_id)
    connection_state.set_enabled(conn, enabled=False)
    await session.commit()
    return await _serialize(session, conn)


@router.post("/{connection_id}/enable", response_model=ConnectionRead)
async def enable_connection(
    connection_id: int,
    org_id: int = Depends(org_context),
    session: AsyncSession = Depends(get_async_session),
) -> ConnectionRead:
    conn = await _get_or_404(session, connection_id)
    connection_state.set_enabled(conn, enabled=True)
    await session.commit()
    return await _serialize(session, conn)


@router.delete("/{connection_id}", status_code=204)
async def delete_connection(
    connection_id: int,
    org_id: int = Depends(org_context),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    conn = await _get_or_404(session, connection_id)
    await session.delete(conn)  # detail cae por CASCADE; imports/logs SET NULL
    await session.commit()
    return Response(status_code=204)
```

- [ ] **Step 4: Wiring.** En `main.py`: reemplazar el include del router credentials por connections (leer cómo está incluido el actual y espejar). Borrar `api/credentials.py`, borrar los schemas `FlexCredentialsRead/Update` de `_schemas.py` (NO `FlexCredentialsValidate` — setup.py la usa hasta Task 5), borrar `tests/api/test_credentials.py`.

- [ ] **Step 5: Suite + commit**

Run: `cd backend && uv run pytest tests/api/test_connections_api.py -v && uv run pytest -q` → PASS

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add -A backend && git commit -m "feat(w1): /api/connections CRUD+rotate+enable/disable; remove /api/credentials"
```

---

### Task 5: Wizard/setup sobre connections

**Files:**
- Modify: `backend/src/ibkr_control/api/setup.py`
- Modify: `backend/src/ibkr_control/api/_schemas.py` (renombrar `FlexCredentialsValidate` → `SetupConnectionPayload` con `display_name` opcional)
- Test: `backend/tests/api/test_setup_state.py`, `backend/tests/api/test_setup_step2_detect.py`

- [ ] **Step 1: Failing tests.** Adaptar el seeding de los tests de setup al helper de connections (reusar `_seed_connection` — moverlo a un conftest compartido si Task 3 no lo hizo). Tests nuevos:

```python
# - step1_save sin connection previa → crea Connection+detail (status='active')
# - step1_save con connection existente → rota token/query de la PRIMERA
#   ibkr_flex (orden por id) + mark_rotated; NO crea duplicada
# - get_state: step1_credentials=True si la org tiene >=1 connection ibkr_flex
# - step2_detect itera todas las connections ACTIVAS y acumula cuentas
#   detectadas (2 conns con XMLs de cuentas distintas → unión de cuentas)
# - step2_detect: si TODAS las conns activas dan FlexBusyError agotado → 503 IBKR_BUSY
```

- [ ] **Step 2: Reescribir en `setup.py`:**
  - `step1_save`: buscar `select(Connection).where(Connection.provider_type == "ibkr_flex").order_by(Connection.id)` (RLS scopea org). Si no hay → crear Connection + ConnectionIbkrFlex (mismo código del POST de Task 4, sin validación IBKR — spec D5 del wizard original: step1 no llama a IBKR). Si hay → actualizar `token_encrypted`/`query_id`/`last_rotated_at` del detail de la primera + `connection_state.mark_rotated(conn)`.
  - `get_state`: `has_creds` = `count(Connection.id)` con filtro `provider_type == "ibkr_flex"`.
  - `step2_detect`: loop sobre connections activas; por cada una decrypt + fetch con el retry 1001 existente; parse + persist con `connection_id=conn.id`; acumular cuentas detectadas (dedup por `ibkr_account_id`). Si una conexión da `FlexAuthError` → transición + continuar; si TODAS fallan → propagar el mejor error (auth → 401 con detail claro; busy → 503 `IBKR_BUSY` como hoy).
  - Reemplazar todos los imports/usos de `FlexCredentials` en setup.py.

- [ ] **Step 3: Suite + commit**

Run: `cd backend && uv run pytest tests/api -v && uv run pytest -q` → PASS

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add -A backend && git commit -m "feat(w1): wizard setup opera sobre connections (step1/state/step2_detect)"
```

---

### Task 6: Health endpoint con estado de connections

**Files:**
- Modify: `backend/src/ibkr_control/api/health.py`
- Test: `backend/tests/api/test_health.py` (leer el existente y extender)

- [ ] **Step 1: Failing test:** `GET /api/health/ingest` incluye `connections: [{id, display_name, status, status_reason, last_sync_at}]` para la org (1 activa + 1 reauth_required seedeadas → ambas presentes con su status).

- [ ] **Step 2: Implementar.** Agregar al módulo:

```python
class ConnectionHealth(BaseModel):
    id: int
    display_name: str | None
    status: str
    status_reason: str | None
    last_sync_at: datetime | None


class IngestHealthResponse(BaseModel):
    sources: list[IngestSourceHealth]
    connections: list[ConnectionHealth]
    checked_at: datetime
```

y en `get_ingest_health` un `select(Connection).order_by(Connection.id)` (RLS scopea) mapeado a `ConnectionHealth`. Campo ADITIVO — `sources` no cambia (contrato frontend intacto).

- [ ] **Step 3: Suite + commit** (mismos comandos; mensaje `feat(w1): connection status en /api/health/ingest`).

---

### Task 7: Contract — drop `flex_credentials`

**Files:**
- Create: `backend/alembic/versions/<rev2>_w1_drop_flex_credentials.py`
- Delete: `backend/src/ibkr_control/db/models/flex_credentials.py`
- Modify: `backend/src/ibkr_control/db/__init__.py`, `backend/src/ibkr_control/db/rls.py` (quitar `"flex_credentials"` de `ORG_SCOPED_TABLES`)
- Test: extender `backend/tests/test_w1_connections_migration.py`; limpiar referencias residuales

- [ ] **Step 1: Verificar cero consumidores:** `grep -rn "FlexCredentials\|flex_credentials" backend/src backend/tests --include="*.py"` debe devolver SOLO el modelo, exports, rls.py, migraciones históricas y tests de migración. Si aparece un consumidor vivo → volver al task correspondiente.

- [ ] **Step 2: Failing test:** en `test_w1_connections_migration.py` agregar `test_contract_drops_flex_credentials` (upgrade head → tabla no existe; downgrade → existe de nuevo y el data-copy inverso desde connections+detail restaura las filas).

- [ ] **Step 3: Migración contract** (autogenerate canónico tras borrar el modelo + quitar exports/lista RLS; el autogenerate emite el drop). Completar `downgrade()` a mano: recrear tabla (DDL frozen del baseline) + repoblar desde `connections JOIN connection_ibkr_flex` + re-crear policy RLS frozen inline.

- [ ] **Step 4: Suite completa + commit**

Run: `cd backend && uv run pytest -q` → PASS (drift test valida que metadata == migraciones sin la tabla).

```bash
git add -A backend && git commit -m "feat(w1): contract — drop flex_credentials (expand/contract completo)"
```

---

### Task 8: Frontend — ConnectionsSection + wiring

**Files:**
- Regenerate: `frontend/openapi.json` + `frontend/src/api/generated.ts` (path según repo)
- Create: `frontend/src/components/settings/ConnectionsSection.tsx`
- Modify: `frontend/src/components/settings/RotateTokenModal.tsx`
- Modify: `frontend/src/components/wizard/Step1Credentials.tsx` (campo `display_name` opcional)
- Delete: `frontend/src/components/settings/FlexCredentialsSection.tsx`
- Modify: la page de Settings que monta la sección (grep `FlexCredentialsSection`)
- Test: `frontend/src/components/settings/ConnectionsSection.test.tsx` (vitest)

- [ ] **Step 1: Regenerar el cliente canónicamente** (¡el camino importa! — lección D12):

```bash
docker compose -f compose.yaml -f compose.dev.yaml up -d --build backend
export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"
cd frontend && pnpm openapi:gen
```

Verificar que los hooks/fetchers de connections aparecen en el cliente generado (nombres tipo `listConnectionsApiConnectionsGet`, `createConnectionApiConnectionsPost`, etc. — usar los nombres REALES generados; recordar el quirk Orval: si genera `useMutation` para GETs o `useQuery` para POSTs, usar TanStack directo con la función fetch generada).

- [ ] **Step 2: Failing vitest** para `ConnectionsSection`: renderiza una card por connection con badge según `status` (mock del fetcher con 1 `active` + 1 `reauth_required`); la `reauth_required` muestra CTA "Rotar token" y el `status_reason`; botón "Pausar"/"Reanudar" según estado.

- [ ] **Step 3: Implementar `ConnectionsSection.tsx`** (patrón existente: TanStack Query + componentes shadcn `Card`/`Badge`/`Button` + `Input`/`Label`+`useState` para el form de alta; UI strings en español). Estructura:
  - `useQuery` lista connections; estados loading/empty/error.
  - Card por connection: `display_name ?? 'Conexión IBKR'`, badge (`active`→verde "Activa", `degraded`→ámbar "Degradada", `reauth_required`→rojo "Requiere re-autenticación", `disabled`→gris "Pausada"), `last_sync_at` formateado, `status_reason` visible cuando exista.
  - Acciones: Rotar token (abre `RotateTokenModal` con `connectionId`), Pausar/Reanudar (`useMutation` → disable/enable + invalidate), Eliminar (confirm + DELETE).
  - Form "Agregar conexión" (token, query_id, display_name) → POST + invalidate; errores 401/400/502 del backend mostrados textual.
- [ ] **Step 4: Adaptar `RotateTokenModal`** para recibir `connectionId` y pegarle a `/api/connections/{id}/rotate-token` (leer el modal actual y conservar su UX; el doble-cast `as unknown as` está prohibido — tipos del cliente generado).
- [ ] **Step 5: `Step1Credentials.tsx`**: agregar `Input` opcional "Nombre de la conexión" → `display_name` en el payload de `step1/save` (el endpoint del wizard no cambió de path).
- [ ] **Step 6: Verde + lint + commit**

```bash
cd frontend && pnpm test && pnpm lint && pnpm build
git add -A frontend && git commit -m "feat(w1): ConnectionsSection con estado de conexión + rotate/pause; orval regen"
```

---

### Task 9: E2E + verificación final + docs

**Files:**
- Modify: specs Playwright que toquen Settings/wizard (grep `credentials` en `frontend/e2e/`)
- Modify: `CLAUDE.md` (§"Cómo continuar": entrada W1 + estado), `docs/specs/2026-06-03-saas-program-roadmap.md` (marcar W1/W4 hechos en este PR)

- [ ] **Step 1: Playwright:** actualizar specs que referencien la sección de credenciales vieja; smoke de Settings mostrando la card de conexión. Run: `cd frontend && pnpm e2e` → PASS.
- [ ] **Step 2: Suite completa backend + frontend + boot smoke:**

```bash
cd backend && uv run pytest -q && uv run ruff check .
cd frontend && pnpm lint && pnpm build
make prod-local   # boot smoke: backend arranca como app_rls con el schema nuevo
```

- [ ] **Step 3: Smoke manual mínimo** (dev, datos reales si están cargados): `make dev` → Settings muestra la conexión migrada con badge "Activa" → "Refresh manual" → SSE ok → `ingest_log` row nueva con `connection_id` poblado.
- [ ] **Step 4: Docs:** CLAUDE.md entrada nueva en §"Cómo continuar" (W1 mergeado→pendiente, tests N→M, spec/plan links); roadmap: W1/W4 marcados "✅ hecho (PR W1)" en la sección Ampliaciones.
- [ ] **Step 5: Commit + push + PR**

```bash
git add -A && git commit -m "docs(w1): CLAUDE.md + roadmap — W1/W4 completados"
git push -u origin saas/w1-connections
gh pr create --title "W1+W4: connections/providers + connection state machine" --body "..."
```

- [ ] **Step 6: Code review holístico** (superpowers:requesting-code-review) con la suite completa corriendo — los reviewers de spec y calidad ya corrieron por task; este es el review final pre-merge.

---

## Self-Review (ejecutado al escribir el plan)

- **Spec coverage:** T1-D1 (detail table, Task 1) · T1-D2 (FK compuesto, Task 1) · T1-D3 (seed, Task 1) · T1-D4 (SET NULL nullable, Task 1) · T1-D5 (Task 2, consumido en Tasks 3-5) · T1-D6 (función nueva + cron, Tasks 1/3) · T1-D14 data-preserving (Task 1 expand) · T1-D15 (Task 4) · UI completa T1-S3 (Tasks 8-9) · CR-3 resuelto (header). W2/W3 NO están en este plan (PRs 2 y 3, planes propios).
- **Riesgo descubierto y mitigado:** migraciones históricas con imports vivos (Task 0) — sin eso, el replay fresco rompe al cambiar el builder.
- **Consistencia de tipos:** `run() -> dict[int, int | None]`; `_run_manual` ignora el retorno (verificado contra `api/ingest.py:118`) y depende del raise — contrato preservado por "re-lanza si todas fallaron".
