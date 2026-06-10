# W1+W4 Connections/Providers + State Machine — Implementation Plan (PR-1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar `flex_credentials` por el modelo Plaid-grade `institutions → connections → connection_ibkr_flex` con state machine de conexión (W4) cableada al cron/job/API/UI, y linaje `connection_id` en `flex_imports`/`ingest_log`.

**Architecture:** **Re-baseline pre-deploy (T1-D14 amended):** no hay deployment todavía, así que NO hay migraciones transicionales ni data-copy — el branch squashea la cadena actual (5 revisiones) en UN baseline pristino y lo va **amendando canónicamente** task a task hasta que `flex_credentials` no exista más. La DB dev se wipea en cada amendment (`down -v`; el usuario recarga XMLs por wizard). `main` mergeado termina con UNA migración baseline y cero rastro del modelo viejo. Las transiciones de estado pasan SOLO por `ingest/connection_state.py` (T1-D5). Spec: `docs/specs/2026-06-10-tier1-worldclass-model-design.md` (T1-S1..S4, T1-D1..D6, D14 amended, D15).

**Tech Stack:** SQLAlchemy 2.x async + Alembic (autogenerate canónico en container) + FastAPI + Pydantic + Next.js/TanStack/orval + pytest/testcontainers + vitest/Playwright.

**Branch:** `saas/w1-connections` (ya existe, spec + plan commiteados). NO crear branches nuevos; verificar `git branch --show-current` después de cada commit.

**Reglas del repo que aplican a TODOS los tasks:**
- TDD: failing test → impl mínima → test verde → commit. Cada task termina con la suite COMPLETA verde.
- Baseline amendments: regenerar DENTRO del container backend (ver procedimiento en Task 1/Step 4) y re-aplicar las secciones hand-written congeladas. Remover el falso positivo `apscheduler_jobs` del autogenerate si aparece (la tabla va en la sección hand-written).
- Tests corren desde el HOST: `cd backend && uv run pytest` (testcontainers necesita el Docker socket del host).
- `cd backend && uv run ruff check . && uv run ruff format .` antes de cada commit.
- No emojis en código. `Decimal` para dinero. Settings via `get_settings()` dentro de funciones.

**CR-3 (checkpoint resuelto durante planning):** los errores auth-class del Flex WS son `FlexAuthError` (códigos `1003/1004/1018`, ver `client.py::ERR_AUTH_CODES`) y `FlexQueryNotFoundError` (`1005`, query_id mal configurado — también requiere acción del usuario). Ambos → `reauth_required`. `FlexBusyError` (1001), `FlexPollTimeoutError`, `httpx` errors → fallo transitorio (`consecutive_failures++`).

**Política re-baseline (T1-D14 amended — leer antes de tocar migraciones):**
- El baseline es **mutable hasta el primer deploy**. Cada amendment = regenerar el schema autogenerado + re-aplicar las secciones hand-written + wipe de la DB dev.
- Las migraciones NUNCA importan builders vivos de `db/rls.py` — todas las secciones hand-written van **frozen inline** (el squash elimina el caso `a1f2c3d4e5b6`, que importaba `system_enum_function_sql()` vivo: una bomba de replay si el builder cambiaba).
- Al primer deployment esta política EXPIRA: migraciones inmutables + expand/contract cuando haga falta.

---

## File Map (visión completa del PR)

| Acción | Path | Responsabilidad |
|---|---|---|
| Create | `backend/alembic/versions/<rev>_tier1_baseline.py` | Baseline único pristino (squash + W1, amendado por task) |
| Delete | `backend/alembic/versions/05943d9efcdb_saas_baseline.py` y las otras 4 revisiones | Cadena vieja squasheada (Task 1) |
| Create | `backend/src/ibkr_control/db/models/institutions.py` | Catálogo global de brokers (control plane) |
| Create | `backend/src/ibkr_control/db/models/connections.py` | `Connection` + `ConnectionIbkrFlex` |
| Create | `backend/src/ibkr_control/ingest/connection_state.py` | State machine (única puerta de transiciones) |
| Create | `backend/src/ibkr_control/api/connections.py` | Router `/api/connections` |
| Modify | `backend/src/ibkr_control/db/__init__.py` | Exports |
| Modify | `backend/src/ibkr_control/db/rls.py` | `ORG_SCOPED_TABLES` + builder de la función (SSOT para tests; las migraciones congelan su copia) |
| Modify | `backend/src/ibkr_control/db/models/flex_raw.py` | `FlexImport.connection_id` |
| Modify | `backend/src/ibkr_control/db/models/ingest_log.py` | `IngestLog.connection_id` |
| Modify | `backend/src/ibkr_control/ingest/log.py` | param `connection_id` |
| Modify | `backend/src/ibkr_control/ingest/flex/job.py` | `run()` itera connections activas + transiciones |
| Modify | `backend/src/ibkr_control/ingest/flex/persister.py` | `persist(connection_id=...)` |
| Modify | `backend/src/ibkr_control/scheduler/jobs.py` | docstrings (enumera connections) |
| Modify | `backend/src/ibkr_control/api/_schemas.py` | Schemas Connection*; muere FlexCredentials* |
| Modify | `backend/src/ibkr_control/api/setup.py` | step1/state/step2_detect sobre connections |
| Modify | `backend/src/ibkr_control/api/health.py` | estado de connections en la respuesta |
| Modify | `backend/src/ibkr_control/main.py` | router connections (reemplaza credentials) |
| Delete | `backend/src/ibkr_control/db/models/flex_credentials.py` | (Task 7) |
| Delete | `backend/src/ibkr_control/api/credentials.py` | (Task 5) |
| Create | `frontend/src/components/settings/ConnectionsSection.tsx` | Cards con badge de estado + acciones |
| Modify | `frontend/src/components/settings/RotateTokenModal.tsx` | Opera sobre connection id |
| Modify | `frontend/src/components/wizard/Step1Credentials.tsx` | display_name opcional |
| Delete | `frontend/src/components/settings/FlexCredentialsSection.tsx` | Reemplazada |
| Tests | `backend/tests/ingest/test_connection_state.py` (new), `backend/tests/api/test_connections_api.py` (new), `backend/tests/test_tier1_baseline.py` (new), updates en `test_job.py`, `test_job_rls.py`, `test_scheduler.py`, `test_rls.py`, `test_setup_*.py`, `test_identity_models.py`; delete `test_credentials.py` y `test_w1_*` obsoletos; vitest + Playwright | |

---

### Task 1: Re-baseline mecánico (squash de la cadena actual — SIN cambios de modelo)

Squash de las 5 revisiones (`05943d9efcdb` baseline → `7fdaf6528762` apscheduler → `544a0b2c362c` db-hardening → `a1f2c3d4e5b6` system function → `fd27737af54e` multihome) en UN baseline nuevo que reproduce el estado ACTUAL de `Base.metadata`. Aislar la corrección del squash de los cambios W1 (lección Phase 2.8: el squash anterior destapó una cadena rota y 15 tests obsoletos — mejor descubrir eso sin ruido de features).

**Files:**
- Create: `backend/alembic/versions/<rev>_tier1_baseline.py`
- Delete: las 5 revisiones existentes en `backend/alembic/versions/`
- Test: `backend/tests/test_tier1_baseline.py` (new) + audit de tests por-revisión

- [ ] **Step 1: Inventariar las secciones hand-written de la cadena vieja ANTES de borrar.** Leer los 5 archivos y extraer VERBATIM a un scratch local: (a) del baseline `05943d9efcdb`: bloques RLS (ENABLE/FORCE/CREATE POLICY por tabla, policy especial de `access_grants`, grants del rol `app_rls` — comparar con los builders de `db/rls.py` para confirmar que son equivalentes); (b) de `7fdaf6528762`: DDL completo de `apscheduler_jobs` (`CREATE TABLE IF NOT EXISTS` — dirty-volume safe, `app_rls` no tiene CREATE); (c) de `a1f2c3d4e5b6`: las 3 sentencias de `system_credentialed_org_ids()` (body actual lee `flex_credentials`); (d) verificar que `544a0b2c362c`/`fd27737af54e` no tengan hand-sections no capturadas por autogenerate (índices/constraints ya viven en los modelos — drift test lo garantiza).

- [ ] **Step 2: Failing test** en `backend/tests/test_tier1_baseline.py`:

```python
"""Baseline tier1: replay fresco + secciones hand-written presentes."""

# Patrón de tests/test_migrations.py (container fresco + alembic upgrade head).

def test_baseline_is_single_revision():
    # listdir de alembic/versions: exactamente 1 archivo de revisión,
    # down_revision is None
    ...

def test_apscheduler_jobs_exists_after_upgrade(fresh_pg_engine):
    # upgrade head → tabla apscheduler_jobs existe (no está en Base.metadata,
    # el drift test la ignora — este assert es su única red)
    ...

def test_system_function_exists_and_is_security_definer(fresh_pg_engine):
    # SELECT prosecdef, prosrc FROM pg_proc WHERE proname='system_credentialed_org_ids'
    # → prosecdef=True; prosrc contiene 'FROM flex_credentials' (Task 4 lo cambia)
    ...
```

Run: `cd backend && uv run pytest tests/test_tier1_baseline.py -v` → FAIL.

- [ ] **Step 3: Borrar las 5 revisiones** (`git rm backend/alembic/versions/05943d9efcdb_*.py 7fdaf6528762_*.py 544a0b2c362c_*.py a1f2c3d4e5b6_*.py fd27737af54e_*.py`).

- [ ] **Step 4: Generar el baseline nuevo canónicamente.** Con el stack dev arriba (`make dev`) y la DB del container WIPEADA para que autogenerate vea schema vacío:

```bash
docker compose -f compose.yaml -f compose.dev.yaml down -v && make dev
docker compose -f compose.yaml -f compose.dev.yaml exec backend \
  uv run alembic revision --autogenerate -m "tier1_baseline"
```

(El servicio `migrate` puede haber aplicado `head` al boot — si la DB no está vacía al autogenerar, dropear el schema dentro del container o regenerar tras `down -v` sin el servicio migrate. El resultado esperado: un archivo con TODO el DDL de `Base.metadata`, `down_revision = None`.)

- [ ] **Step 5: Re-aplicar las secciones hand-written congeladas** al final del `upgrade()` (todo inline, CERO imports de `db/rls.py`):

```python
    # --- Hand-written frozen sections (T1-D14 amended: no live builder imports) ---
    # 1. apscheduler_jobs (runtime de APScheduler, fuera de Base.metadata;
    #    pre-creada porque app_rls no tiene CREATE; IF NOT EXISTS = dirty-volume safe)
    #    [DDL copiado VERBATIM de la vieja 7fdaf6528762]
    # 2. Rol app_rls + grants  [output actual de app_role_grants_sql(), inline]
    # 3. RLS ENABLE/FORCE/POLICY por cada tabla org-scoped  [lista inline snapshot
    #    de ORG_SCOPED_TABLES a hoy — INCLUYE flex_credentials todavía]
    # 4. Policy especial access_grants  [inline]
    # 5. system_credentialed_org_ids()  [inline, body ACTUAL: FROM flex_credentials]
```

`downgrade()`: `op.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")` NO — mantener el patrón del baseline viejo (leerlo: si su downgrade era no-op/`pass` documentado, replicar; el baseline es el piso).

- [ ] **Step 6: Audit de tests por-revisión** (lección Phase 2.8): `grep -rn "05943d9efcdb\|7fdaf6528762\|544a0b2c362c\|a1f2c3d4e5b6\|fd27737af54e" backend/tests/` → tests de mecánica-de-revisión (upgrade/downgrade a revisiones muertas) se BORRAN; tests de comportamiento (CHECK enforcement, CASCADE, RLS) se PRESERVAN actualizando referencias.

- [ ] **Step 7: Suite completa + wipe dev**

Run: `cd backend && uv run pytest -q`
Expected: PASS — en particular `test_migrations.py` (replay fresco + drift cero) y `test_rls.py` (policies presentes).

```bash
docker compose -f compose.yaml -f compose.dev.yaml down -v && make dev   # dev DB renace del baseline nuevo
```

- [ ] **Step 8: Commit**

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add -A backend
git commit -m "chore(w1): re-baseline tier1 — squash 5 revisiones en baseline pristino (pre-deploy, T1-D14 amended)"
```

---

### Task 2: State machine `ingest/connection_state.py`

**Files:**
- Create: `backend/src/ibkr_control/ingest/connection_state.py`
- Test: `backend/tests/ingest/test_connection_state.py`

(El módulo depende solo del modelo `Connection` de Task 3 — escribir los tests importando el modelo; quedarán rojos hasta que exista. Para mantener este task verde standalone, Task 2 se ejecuta DESPUÉS de Task 3 si el implementer lo prefiere; el orden 2↔3 es intercambiable. Si se ejecuta antes, marcar los tests con el modelo como dependencia y mover el commit al final de Task 3.)

**Orden recomendado: ejecutar Task 3 primero y este después.** Se numera así para mantener la correspondencia con el spec.

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

Run: `cd backend && uv run pytest tests/ingest/test_connection_state.py -v` → FAIL (módulo inexistente).

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

- [ ] **Step 3: Verde + commit**

Run: `cd backend && uv run pytest tests/ingest/test_connection_state.py -v` → PASS

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add backend/src/ibkr_control/ingest/connection_state.py backend/tests/ingest/test_connection_state.py
git commit -m "feat(w1): connection state machine (W4) — única puerta de transiciones"
```

---

### Task 3: Modelos nuevos + baseline amendment #1 (tablas connections)

Agrega `institutions`, `connections`, `connection_ibkr_flex` y los `connection_id` de linaje. `flex_credentials` SIGUE existiendo (modelo + baseline) — los consumidores se rewirean en Tasks 4-6 con la suite verde, y Task 7 la elimina. El estado transicional vive SOLO dentro del branch; `main` mergeado no lo ve.

**Files:**
- Create: `backend/src/ibkr_control/db/models/institutions.py`
- Create: `backend/src/ibkr_control/db/models/connections.py`
- Modify: `backend/src/ibkr_control/db/models/flex_raw.py`, `backend/src/ibkr_control/db/models/ingest_log.py`, `backend/src/ibkr_control/db/__init__.py`, `backend/src/ibkr_control/db/rls.py`
- Modify: `backend/alembic/versions/<rev>_tier1_baseline.py` (amendment #1)
- Test: `backend/tests/test_tier1_baseline.py` (extender)

- [ ] **Step 1: Failing tests.** Extender `test_tier1_baseline.py`:

```python
def test_institutions_seeded(fresh_pg_engine):
    # upgrade head → SELECT code, name FROM institutions → [('ibkr', 'Interactive Brokers')]
    ...

def test_connections_subtype_integrity(fresh_pg_engine):
    # INSERT connections(provider_type='ibkr_flex') OK;
    # INSERT connection_ibkr_flex con (connection_id, 'ibkr_flex') OK;
    # el CHECK ck_connection_ibkr_flex_provider_type rechaza otro literal.
    ...
```

(El aislamiento RLS de las tablas nuevas lo cubre `test_rls.py` automáticamente al crecer `ORG_SCOPED_TABLES` — verificar que ese test itere la lista; si no, agregar el caso.)

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

- [ ] **Step 4: `connection_id` en `FlexImport` y `IngestLog`.** En ambos modelos, después de `organization_id`:

```python
    # W1: linaje import/run -> connection. NULL para manual_upload (no hay
    # conexión) y para rows que sobreviven al borrado de su conexión (SET NULL —
    # append-only ledger: el hecho no muere con la credencial).
    connection_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("connections.id", ondelete="SET NULL"), nullable=True
    )
```

más `Index(None, "connection_id")` en `__table_args__` de ambos (lección sp1-db-hardening: FK sin índice = seq scan en el SET NULL del delete).

- [ ] **Step 5: Exports y RLS.** `db/__init__.py`: agregar imports + `__all__` de `Institution`, `Connection`, `ConnectionIbkrFlex` (mantener `FlexCredentials` hasta Task 7). `db/rls.py`: `ORG_SCOPED_TABLES` += `"connections"`, `"connection_ibkr_flex"` (mantener `"flex_credentials"`; `institutions` NO va — control plane).

- [ ] **Step 6: Baseline amendment #1.** Regenerar el schema del baseline (mismo procedimiento de Task 1/Step 4: `down -v`, autogenerate, reemplazar la parte autogenerada del archivo, conservar el MISMO revision id para no invalidar nada) y actualizar las hand-sections: (a) RLS policies — agregar los bloques ENABLE/FORCE/POLICY de `connections` y `connection_ibkr_flex` (inline); (b) agregar el seed:

```python
    op.execute(
        "INSERT INTO institutions (code, name) VALUES ('ibkr', 'Interactive Brokers') "
        "ON CONFLICT (code) DO NOTHING"
    )
```

(la función del sistema NO cambia todavía — Task 4). Wipe dev: `down -v && make dev`.

- [ ] **Step 7: Suite + commit**

Run: `cd backend && uv run pytest -q` → PASS (drift + replay + RLS + baseline asserts).

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add -A backend
git commit -m "feat(w1): institutions + connections + connection_ibkr_flex (baseline amendment #1)"
```

**→ Ejecutar Task 2 (state machine) acá si no se hizo antes.**

---

### Task 4: Rewire del job/persister/log + función del sistema a connections

**Files:**
- Modify: `backend/src/ibkr_control/ingest/log.py`, `backend/src/ibkr_control/ingest/flex/persister.py`, `backend/src/ibkr_control/ingest/flex/job.py`, `backend/src/ibkr_control/scheduler/jobs.py` (docstrings), `backend/src/ibkr_control/db/rls.py` (builder), `backend/alembic/versions/<rev>_tier1_baseline.py` (amendment #2: body de la función)
- Test: `backend/tests/ingest/flex/test_job.py`, `backend/tests/ingest/flex/test_job_rls.py`, `backend/tests/test_scheduler.py`, `backend/tests/test_tier1_baseline.py`

- [ ] **Step 1: Failing tests.** En los tests de job, reemplazar el seeding de `FlexCredentials` por un helper compartido en `tests/ingest/flex/conftest.py`:

```python
async def _seed_connection(session, org_id: int, *, query_id: str = "12345",
                           status: str = "active") -> int:
    from sqlalchemy import select

    from ibkr_control.db.models.connections import Connection, ConnectionIbkrFlex
    from ibkr_control.db.models.institutions import Institution
    from ibkr_control.ingest.flex.crypto import encrypt_token

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

Tests nuevos (además de adaptar los existentes):

```python
# test_run_iterates_all_active_connections:
#   2 connections activas, FlexClient fake devuelve XML fixture →
#   2 flex_imports (cada uno con su connection_id), 2 ingest_log rows
#   kind='flex' con su connection_id, ambas conns quedan active/ok.
# test_run_skips_disabled_connections:
#   1 activa + 1 disabled → 1 import; la disabled intacta.
# test_run_auth_error_transitions_reauth_required:
#   conn A lanza FlexAuthError("1018", ...), conn B responde XML →
#   A: status='reauth_required', reason contiene '1018', su ingest_log failed;
#   B: import OK. run() NO lanza (hubo >=1 éxito).
# test_run_raises_when_all_connections_fail:
#   todas lanzan FlexAuthError → run() re-lanza la última (contrato _run_manual/SSE).
# test_run_no_active_connections_raises:
#   org sin connections (o todas disabled) → RuntimeError claro.
# test_tier1_baseline: actualizar el assert de prosrc → contiene 'FROM connections'
#   y "status <> 'disabled'".
```

Run: `cd backend && uv run pytest tests/ingest/flex -v` → FAIL.

- [ ] **Step 2: `ingest/log.py`** — agregar `connection_id: int | None = None` a la firma de `ingest_log_entry` y `connection_id=connection_id` al constructor del row (docstring: "conexión que produjo el run (flex); None para manual_upload/TRM").

- [ ] **Step 3: `persister.py`** — kwarg `connection_id: int | None = None` en `persist()`, pasado al values del INSERT de `FlexImport`. Ídem `job._insert_poison_row` (los poison rows también llevan linaje).

- [ ] **Step 4: Reescribir `job.run()`** (`ingest_xml()` NO cambia — manual upload no tiene conexión):

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
    a las demás. Si results quedó vacío y hubo excepción, re-lanza la última
    (contrato con _run_manual: el SSE debe mostrar el fallo).

    RLS: usa set_session_org_context (stash + after_begin listener, PR #7)
    además del apply inmediato, porque ingest_log_entry commitea por conexión
    y las transiciones de estado corren en transacciones nuevas que necesitan
    el GUC re-aplicado.
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
                try:
                    results[conn.id] = await _run_one_connection(
                        session,
                        organization_id=organization_id,
                        trigger=trigger,
                        connection_id=conn.id,
                        token_encrypted=detail.token_encrypted,
                        query_id=detail.query_id,
                    )
                except (
                    flex_client_mod.FlexAuthError,
                    flex_client_mod.FlexQueryNotFoundError,
                ) as exc:
                    # CR-3: auth-class (1003/1004/1018) o query_id mal configurado
                    # (1005) — ambos requieren acción del usuario.
                    connection_state.mark_auth_failed(
                        conn, reason=str(exc), now=datetime.now(timezone.utc)
                    )
                    await session.commit()
                    last_exc = exc
                    continue
                except Exception as exc:
                    # Transitorio (1001 BUSY agotado, timeout, red, parse/persist).
                    # Broad a propósito: cualquier fallo debe transicionar estado
                    # y seguir con la próxima conexión, no matar el loop.
                    connection_state.mark_sync_failed(
                        conn, reason=str(exc), now=datetime.now(timezone.utc)
                    )
                    await session.commit()
                    last_exc = exc
                    continue
                connection_state.mark_sync_ok(conn, now=datetime.now(timezone.utc))
                await session.commit()

    if not results and last_exc is not None:
        raise last_exc
    return results
```

`_run_one_connection(session, *, organization_id, trigger, connection_id, token_encrypted, query_id) -> int | None` es la extracción del cuerpo actual de `run()` (decrypt + SendRequest + poll + hash check + SAVEPOINT + persist + items_processed) con tres cambios: pasa `connection_id` a `ingest_log_entry(...)`, a `persist(connection_id=...)` y a `_insert_poison_row(connection_id=...)`. El `ingest_log_entry` envuelve todo el cuerpo (igual que hoy) para que un fallo marque `failed` su propio row. Devuelve `None` en hash-dedup (hoy `return None`), `flex_import_id` si persistió.

- [ ] **Step 5: Función del sistema → connections.** En `db/rls.py::system_enum_function_sql()` reemplazar el body:

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

y aplicar el MISMO texto frozen inline en la hand-section del baseline (amendment #2). Wipe dev. Actualizar docstrings de `scheduler/jobs.py::_run_flex_for_all_orgs` (enumera connections; el catch de `FlexAuthError` queda como defensa residual — `run()` ya transiciona per-connection y solo re-lanza si todas fallaron).

- [ ] **Step 6: Suite + commit**

Run: `cd backend && uv run pytest -q` → PASS.

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add -A backend
git commit -m "feat(w1): flex job itera connections activas con transiciones per-connection; función de sistema enumera connections"
```

---

### Task 5: API `/api/connections` (reemplaza `/api/credentials`)

**Files:**
- Create: `backend/src/ibkr_control/api/connections.py`
- Modify: `backend/src/ibkr_control/api/_schemas.py`, `backend/src/ibkr_control/main.py`
- Delete: `backend/src/ibkr_control/api/credentials.py`
- Test: `backend/tests/api/test_connections_api.py` (new); delete `backend/tests/api/test_credentials.py`

- [ ] **Step 1: Failing tests** en `test_connections_api.py` (fixtures de auth/org del conftest de api existente; espejo del estilo de `test_credentials.py` antes de borrarlo):

```python
# Casos (cada uno un test):
# - GET /api/connections vacío → []
# - POST /api/connections con token inválido → 401 (FlexClient fake lanza FlexAuthError)
# - POST /api/connections OK (FlexClient fake responde) → 201 ConnectionRead,
#   status='active', institution_code='ibkr'; el token NUNCA aparece en la response
# - GET lista la creada (query_id visible, token jamás)
# - PATCH {id} display_name → actualizado
# - POST {id}/rotate-token con token válido → last_rotated_at avanza; seedear la
#   conn en 'reauth_required' y verificar que rota a 'active' (mark_rotated)
# - POST {id}/disable → 'disabled'; POST {id}/enable → 'active'
# - DELETE {id} → 204; un flex_import vinculado queda con connection_id NULL
# - Aislamiento RLS: org B no ve ni opera la connection de org A (404)
```

Run: `cd backend && uv run pytest tests/api/test_connections_api.py -v` → FAIL.

- [ ] **Step 2: Schemas** en `api/_schemas.py` (borrar `FlexCredentialsRead`/`FlexCredentialsUpdate`; `FlexCredentialsValidate` sobrevive hasta Task 6):

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

- [ ] **Step 3: Router `backend/src/ibkr_control/api/connections.py`:**

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
    # RLS ya scopea por org; 404 cubre inexistente y cross-org por igual
    # (no filtra existencia — mismo criterio D1/SSE).
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
    code = await session.scalar(
        select(Institution.code).where(Institution.id == conn.institution_id)
    )
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

- [ ] **Step 4: Wiring.** `main.py`: reemplazar el include del router credentials por el de connections (leer cómo está incluido y espejar). Borrar `api/credentials.py` y `tests/api/test_credentials.py`.

- [ ] **Step 5: Suite + commit**

Run: `cd backend && uv run pytest -q` → PASS.

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add -A backend
git commit -m "feat(w1): /api/connections CRUD+rotate+enable/disable; remove /api/credentials"
```

---

### Task 6: Wizard/setup sobre connections

**Files:**
- Modify: `backend/src/ibkr_control/api/setup.py`, `backend/src/ibkr_control/api/_schemas.py` (renombrar `FlexCredentialsValidate` → `SetupConnectionPayload` + `display_name` opcional)
- Test: `backend/tests/api/test_setup_state.py`, `backend/tests/api/test_setup_step2_detect.py`

- [ ] **Step 1: Failing tests** (adaptar seeding al helper `_seed_connection`; moverlo a un conftest compartido si hace falta — DRY):

```python
# - step1_save sin connection previa → crea Connection+detail (status='active')
# - step1_save con connection existente → actualiza token/query del detail de la
#   PRIMERA ibkr_flex (orden por id) + mark_rotated; NO crea duplicada
# - get_state: step1_credentials=True si la org tiene >=1 connection ibkr_flex
# - step2_detect itera todas las connections ACTIVAS y acumula cuentas
#   detectadas (2 conns con XMLs de cuentas distintas → unión, dedup por
#   ibkr_account_id)
# - step2_detect: conn con FlexAuthError → transición reauth_required + continúa
#   con la siguiente; si TODAS fallan → el mejor error (auth → 401; busy → 503
#   IBKR_BUSY como hoy)
```

- [ ] **Step 2: Reescribir en `setup.py`:** `step1_save` (sin validación IBKR — spec D5 del wizard), `get_state` (`count(Connection.id)` con `provider_type == "ibkr_flex"`), `step2_detect` (loop sobre activas; por cada una decrypt + fetch con el retry 1001 existente + parse + persist con `connection_id=conn.id`; acumular cuentas). Reemplazar todos los imports/usos de `FlexCredentials`.

- [ ] **Step 3: Suite + commit**

Run: `cd backend && uv run pytest -q` → PASS.

```bash
git add -A backend && git commit -m "feat(w1): wizard setup opera sobre connections (step1/state/step2_detect)"
```

---

### Task 7: Eliminar `flex_credentials` (baseline amendment #3)

**Files:**
- Delete: `backend/src/ibkr_control/db/models/flex_credentials.py`
- Modify: `backend/src/ibkr_control/db/__init__.py`, `backend/src/ibkr_control/db/rls.py` (quitar `"flex_credentials"` de `ORG_SCOPED_TABLES`), `backend/alembic/versions/<rev>_tier1_baseline.py` (amendment #3)
- Test: limpiar referencias residuales

- [ ] **Step 1: Verificar cero consumidores:** `grep -rn "FlexCredentials\|flex_credentials" backend/src backend/tests --include="*.py"` → solo el modelo, exports, rls.py y el baseline. Si aparece un consumidor vivo, volver al task correspondiente.
- [ ] **Step 2: Borrar** el modelo, los exports, la entrada en `ORG_SCOPED_TABLES`, y en el baseline: la tabla del bloque autogenerado + su bloque RLS hand-written (regenerar la parte autogenerada canónicamente; el autogenerate ya no la emite porque el modelo no existe). Wipe dev (`down -v && make dev`).
- [ ] **Step 3: Suite completa**

Run: `cd backend && uv run pytest -q` → PASS (drift test confirma metadata == baseline sin la tabla; `test_tier1_baseline.py` sigue verde).

- [ ] **Step 4: Commit**

```bash
git add -A backend && git commit -m "feat(w1): flex_credentials eliminada — baseline pristino sin modelo viejo"
```

---

### Task 8: Health endpoint con estado de connections

**Files:**
- Modify: `backend/src/ibkr_control/api/health.py`
- Test: `backend/tests/api/test_health.py` (leer el existente y extender)

- [ ] **Step 1: Failing test:** `GET /api/health/ingest` incluye `connections: [{id, display_name, status, status_reason, last_sync_at}]` (seedear 1 activa + 1 `reauth_required` → ambas presentes con su status).
- [ ] **Step 2: Implementar** (campo ADITIVO — `sources` no cambia, contrato frontend intacto):

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

y en `get_ingest_health` un `select(Connection).order_by(Connection.id)` (RLS scopea) mapeado a `ConnectionHealth`.

- [ ] **Step 3: Suite + commit** (`feat(w1): connection status en /api/health/ingest`).

---

### Task 9: Frontend — ConnectionsSection + wiring

**Files:**
- Regenerate: `frontend/openapi.json` + cliente orval
- Create: `frontend/src/components/settings/ConnectionsSection.tsx`
- Modify: `frontend/src/components/settings/RotateTokenModal.tsx`, `frontend/src/components/wizard/Step1Credentials.tsx`, la page de Settings que monta la sección (grep `FlexCredentialsSection`)
- Delete: `frontend/src/components/settings/FlexCredentialsSection.tsx`
- Test: `frontend/src/components/settings/ConnectionsSection.test.tsx` (vitest)

- [ ] **Step 1: Regenerar el cliente canónicamente** (¡el camino importa! — lección D12):

```bash
docker compose -f compose.yaml -f compose.dev.yaml up -d --build backend
export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"
cd frontend && pnpm openapi:gen
```

Usar los nombres REALES generados (tipo `listConnectionsApiConnectionsGet`); recordar el quirk Orval (si genera `useMutation` para GETs o `useQuery` para POSTs, usar TanStack directo con la función fetch generada).

- [ ] **Step 2: Failing vitest** para `ConnectionsSection`: mock del fetcher con 1 `active` + 1 `reauth_required` → una card por connection con badge según `status`; la `reauth_required` muestra CTA "Rotar token" y el `status_reason`; botón "Pausar"/"Reanudar" según estado.
- [ ] **Step 3: Implementar `ConnectionsSection.tsx`** (patrón existente: TanStack Query + shadcn `Card`/`Badge`/`Button` + `Input`+`Label`+`useState` para el form de alta; UI strings en español):
  - `useQuery` lista connections; estados loading/empty/error.
  - Card por connection: `display_name ?? 'Conexión IBKR'`, badge (`active`→verde "Activa", `degraded`→ámbar "Degradada", `reauth_required`→rojo "Requiere re-autenticación", `disabled`→gris "Pausada"), `last_sync_at` formateado, `status_reason` visible cuando exista.
  - Acciones: Rotar token (abre `RotateTokenModal` con `connectionId`), Pausar/Reanudar (mutation → disable/enable + invalidate), Eliminar (confirm + DELETE).
  - Form "Agregar conexión" (token, query_id, display_name) → POST + invalidate; errores 401/400/502 mostrados textuales.
- [ ] **Step 4: `RotateTokenModal`** recibe `connectionId` y pega a `/api/connections/{id}/rotate-token` (leer el modal actual y conservar su UX; prohibido `as unknown as` — tipos del cliente generado).
- [ ] **Step 5: `Step1Credentials.tsx`**: `Input` opcional "Nombre de la conexión" → `display_name` en el payload de `step1/save`.
- [ ] **Step 6: Verde + lint + build + commit**

```bash
cd frontend && pnpm test && pnpm lint && pnpm build
git add -A frontend && git commit -m "feat(w1): ConnectionsSection con estado de conexión + rotate/pause; orval regen"
```

---

### Task 10: E2E + verificación final + docs

**Files:**
- Modify: specs Playwright que toquen Settings/wizard (grep `credentials` en `frontend/e2e/`)
- Modify: `CLAUDE.md` (§"Cómo continuar"), `docs/specs/2026-06-03-saas-program-roadmap.md` (W1/W4 hechos)

- [ ] **Step 1: Playwright:** actualizar specs que referencien la sección vieja; smoke de Settings mostrando card de conexión. Run: `cd frontend && pnpm e2e` → PASS.
- [ ] **Step 2: Verificación completa:**

```bash
cd backend && uv run pytest -q && uv run ruff check .
cd frontend && pnpm lint && pnpm build
make prod-local   # boot smoke: backend arranca como app_rls con el baseline nuevo
```

- [ ] **Step 3: Smoke manual** (dev): `make dev` (DB fresca del baseline) → wizard step1 crea conexión → Settings muestra badge "Activa" → "Refresh manual" → SSE ok → `ingest_log` row con `connection_id` poblado.
- [ ] **Step 4: Docs:** CLAUDE.md entrada W1 en §"Cómo continuar" (incluida la política "baseline mutable hasta el primer deploy" y su expiración); roadmap: W1/W4 "✅ hecho (PR W1)" en §Ampliaciones.
- [ ] **Step 5: Push + PR**

```bash
git push -u origin saas/w1-connections
gh pr create --title "W1+W4: connections/providers + connection state machine (re-baseline pre-deploy)" --body "..."
```

- [ ] **Step 6: Code review holístico** (superpowers:requesting-code-review) con la suite completa — los reviewers por task ya corrieron; este es el final pre-merge.

---

## Self-Review (ejecutado al escribir el plan)

- **Spec coverage:** T1-D1 (detail table, Task 3) · T1-D2 (FK compuesto + CHECK, Task 3) · T1-D3 (seed, Task 3) · T1-D4 (SET NULL nullable, Task 3) · T1-D5 (Task 2, consumido en 4-6) · T1-D6 (función + cron, Task 4) · T1-D14 **amended** (re-baseline, Tasks 1/3/4/7) · T1-D15 (Task 5) · UI completa T1-S3 (Tasks 9-10) · CR-3 resuelto (header). W2/W3 = PRs 2 y 3 con planes propios.
- **Sin legacy en `main` mergeado:** una sola migración baseline, cero expand/contract, cero data-copy, cero archivo congelado transicional. El estado dual (`flex_credentials` + `connections`) existe SOLO entre Tasks 3 y 7 dentro del branch, para que cada task termine con la suite verde.
- **Consistencia de tipos:** `run() -> dict[int, int | None]`; `_run_manual` ignora el retorno (verificado contra `api/ingest.py`) y depende del raise — preservado por "re-lanza si `results` vacío y hubo excepción".
- **Riesgo principal:** el squash (Task 1) — mitigado aislándolo como task mecánico sin features, con el audit de tests por-revisión (lección Phase 2.8) y el replay test fresco.
