# SP1 RLS Runtime Wiring — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the deployed app enforce RLS at runtime by connecting as the non-bypass `app_rls` role (not the bootstrap superuser), with a fail-closed boot guard, self-healing per-transaction org context, and runtime-parity tests.

**Architecture:** Per-container privilege separation — a one-shot `migrate` service runs migrations as the owner; the long-running `backend` service connects only as `app_rls` and never holds owner credentials. A boot guard refuses to start if the runtime role can bypass RLS. An `after_begin` Session listener re-applies the org GUC on every transaction so isolation survives intra-request commits. Because `app_rls` lacks `CREATE`, the APScheduler jobstore table is pre-created in a migration so the app never issues DDL.

**Tech Stack:** FastAPI, SQLAlchemy 2.x async, asyncpg, Alembic, APScheduler 3.11.x, Postgres 16, Docker Compose, pytest + testcontainers.

**Spec:** `docs/specs/2026-06-05-sp1-rls-runtime-wiring-design.md`

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `backend/alembic/versions/<new>_apscheduler_jobs_table.py` | Pre-create `apscheduler_jobs` as owner + grant to `app_rls` | Create |
| `backend/src/ibkr_control/db/rls.py` | RLS SSOT: add shared GUC-apply helper + `after_begin` listener registration | Modify |
| `backend/src/ibkr_control/db/guards.py` | Fail-closed runtime-role guard | Create |
| `backend/src/ibkr_control/api/_context.py` | `org_context` stores ctx in `session.info` + applies to current tx | Modify |
| `backend/src/ibkr_control/main.py` | Call boot guard in `lifespan` before serving | Modify |
| `backend/Dockerfile` | `CMD` stops chaining `alembic && uvicorn` (migrate service owns migrations) | Modify |
| `compose.yaml` | New `migrate` service (owner DSN); `backend` → `app_rls` DSN + `depends_on` | Modify |
| `compose.dev.yaml` | Same split for dev | Modify |
| `compose.coolify.yaml` | Same split for prod | Modify |
| `.env.example` | Document owner DSN, app_rls DSN, `APP_RLS_PASSWORD` | Modify |
| `backend/tests/test_runtime_role_guard.py` | Guard raises as owner, passes as app_rls | Create |
| `backend/tests/test_rls_context_survives_commit.py` | Org GUC survives an intra-request commit | Create |
| `backend/tests/test_apscheduler_jobs_grants.py` | app_rls can DML `apscheduler_jobs`; table exists post-migration | Create |

---

## Task 1: Pre-create `apscheduler_jobs` as owner (app never does DDL)

**Why:** The scheduler runs in the `backend` (app_rls) process and lazily creates `apscheduler_jobs` via `SQLAlchemyJobStore` (`create_all(checkfirst=True)`). `app_rls` has `USAGE` but not `CREATE` on schema `public`, so this DDL fails at boot. Pre-creating the table as owner in a migration makes APScheduler's `checkfirst` a no-op (table already exists) and keeps the app role DDL-free.

**Files:**
- Create: `backend/alembic/versions/<rev>_apscheduler_jobs_table.py`
- Test: `backend/tests/test_apscheduler_jobs_grants.py`

- [ ] **Step 1: Generate an empty revision (canonical path, inside the container)**

The host cannot reach Postgres (port 5432 unmapped) and autogenerate would false-positive `apscheduler_jobs`; we hand-write a tiny, stable DDL revision (schema is fixed for the pinned `apscheduler==3.11.*`). Generate the revision file scaffold:

Run (from repo root): `docker compose -f compose.yaml -f compose.dev.yaml run --rm backend uv run alembic revision -m "apscheduler_jobs table"`

Then replace the generated file body with the content in Step 2. Set `down_revision = "a1f2c3d4e5b6"` (the current head — verify with `uv run alembic heads`).

- [ ] **Step 2: Write the migration**

```python
"""apscheduler_jobs table (owner-created; app_rls is DDL-free)

APScheduler's SQLAlchemyJobStore lazily create_all()s this table on
scheduler.start(). The app connects as the non-bypass app_rls role, which has
USAGE but not CREATE on schema public — so it cannot create the table. We
pre-create it here (run as the owner) so APScheduler's checkfirst sees it and
skips DDL. Schema matches APScheduler 3.x SQLAlchemyJobStore exactly:
  id VARCHAR(191) PRIMARY KEY, next_run_time DOUBLE PRECISION (indexed),
  job_state BYTEA NOT NULL.
Not part of Base.metadata (runtime table) — the drift test already ignores it
(tests/test_migrations.py: "apscheduler_jobs" in text). Explicit grant to
app_rls so the running app can read/write job rows.

Revision ID: <rev>
Revises: a1f2c3d4e5b6
Create Date: 2026-06-05
"""

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "<rev>"
down_revision: Union[str, Sequence[str], None] = "a1f2c3d4e5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "apscheduler_jobs",
        sa.Column("id", sa.Unicode(191), primary_key=True, nullable=False),
        sa.Column("next_run_time", sa.Float(25), nullable=True),
        sa.Column("job_state", sa.LargeBinary(), nullable=False),
    )
    op.create_index(
        "ix_apscheduler_jobs_next_run_time", "apscheduler_jobs", ["next_run_time"]
    )
    from ibkr_control.db.rls import APP_ROLE

    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON apscheduler_jobs TO {APP_ROLE}"
    )


def downgrade() -> None:
    op.drop_index("ix_apscheduler_jobs_next_run_time", table_name="apscheduler_jobs")
    op.drop_table("apscheduler_jobs")
```

- [ ] **Step 3: Write the failing test**

```python
# backend/tests/test_apscheduler_jobs_grants.py
"""apscheduler_jobs is owner-created in a migration and app_rls can DML it,
so the scheduler (running as app_rls) never needs CREATE on schema public.
"""

import pytest
from sqlalchemy import text


@pytest.mark.anyio
async def test_apscheduler_jobs_exists_and_app_rls_can_dml(app_rls_db_session):
    # Table exists post-migration (created by owner) ...
    exists = (
        await app_rls_db_session.scalars(
            text("SELECT to_regclass('public.apscheduler_jobs')")
        )
    ).one()
    assert exists == "apscheduler_jobs"

    # ... and app_rls has DML on it (insert a row, read it back, clean up).
    await app_rls_db_session.execute(
        text(
            "INSERT INTO apscheduler_jobs (id, next_run_time, job_state) "
            "VALUES ('t1', 1.0, :s)"
        ),
        {"s": b"x"},
    )
    n = (
        await app_rls_db_session.scalars(
            text("SELECT count(*) FROM apscheduler_jobs WHERE id = 't1'")
        )
    ).one()
    assert n == 1
    await app_rls_db_session.execute(text("DELETE FROM apscheduler_jobs WHERE id = 't1'"))
    await app_rls_db_session.commit()
```

- [ ] **Step 4: Run the test, expect FAIL (table missing before migration applied / or PASS once migration runs)**

Run: `cd backend && uv run pytest tests/test_apscheduler_jobs_grants.py -v`
Expected before Step 2 applied: FAIL (`to_regclass` returns None). After the migration exists and the test DB is migrated to head: PASS.

- [ ] **Step 5: Run the drift + migration suite to confirm no regression**

Run: `cd backend && uv run pytest tests/test_migrations.py -v`
Expected: PASS (the drift filter at `tests/test_migrations.py:61` already excludes `apscheduler_jobs`).

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/*_apscheduler_jobs_table.py backend/tests/test_apscheduler_jobs_grants.py
git commit -m "feat(sp1-rls-runtime): pre-create apscheduler_jobs as owner so app_rls stays DDL-free"
```

---

## Task 2: Fail-closed runtime-role boot guard

**Why:** The class of bug we are closing is "the app boots connected to a role that bypasses RLS." A startup assertion makes that impossible to ship silently.

**Files:**
- Create: `backend/src/ibkr_control/db/guards.py`
- Modify: `backend/src/ibkr_control/main.py:18-29` (lifespan)
- Test: `backend/tests/test_runtime_role_guard.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_runtime_role_guard.py
"""The runtime-role guard refuses any role that can bypass RLS (superuser or
rolbypassrls) and accepts the non-bypass app_rls role the app actually uses.
"""

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from ibkr_control.db.guards import assert_runtime_role_enforces_rls
from ibkr_control.db.rls import app_rls_password
from tests.conftest_ephemeral_db import swap_dsn_credentials


@pytest.mark.anyio
async def test_guard_raises_for_owner_superuser(_migrated_app_db):
    engine = create_async_engine(_migrated_app_db)  # owner == bootstrap superuser
    try:
        with pytest.raises(RuntimeError, match="bypass"):
            await assert_runtime_role_enforces_rls(engine)
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_guard_passes_for_app_rls(_migrated_app_db):
    app_dsn = swap_dsn_credentials(_migrated_app_db, "app_rls", app_rls_password())
    engine = create_async_engine(app_dsn)
    try:
        await assert_runtime_role_enforces_rls(engine)  # no raise
    finally:
        await engine.dispose()
```

- [ ] **Step 2: Run the test, expect FAIL**

Run: `cd backend && uv run pytest tests/test_runtime_role_guard.py -v`
Expected: FAIL with `ModuleNotFoundError: ibkr_control.db.guards`.

- [ ] **Step 3: Implement the guard**

```python
# backend/src/ibkr_control/db/guards.py
"""Fail-closed startup guard: the app must connect with a role that is SUBJECT
to RLS. A superuser or a role with rolbypassrls ignores every org_isolation
policy — booting under such a role silently disables tenant isolation. We
assert at startup and refuse to serve otherwise.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def assert_runtime_role_enforces_rls(engine: AsyncEngine) -> None:
    """Raise RuntimeError unless the connecting role is subject to RLS.

    Checks both vectors that bypass RLS in Postgres:
      * superuser  — bypasses RLS unconditionally.
      * rolbypassrls — the per-role BYPASSRLS attribute.
    """
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT current_user AS role, "
                    "current_setting('is_superuser')::bool AS is_su, "
                    "COALESCE((SELECT rolbypassrls FROM pg_roles "
                    "WHERE rolname = current_user), false) AS bypass"
                )
            )
        ).one()
    if row.is_su or row.bypass:
        raise RuntimeError(
            f"Refusing to start: DB role '{row.role}' can bypass RLS "
            f"(is_superuser={row.is_su}, rolbypassrls={row.bypass}). The app must "
            f"connect as the non-bypass app_rls role so tenant isolation is "
            f"enforced. Point DATABASE_URL at app_rls (see compose: the backend "
            f"service uses app_rls; migrations run in the separate migrate service)."
        )
```

- [ ] **Step 4: Run the test, expect PASS**

Run: `cd backend && uv run pytest tests/test_runtime_role_guard.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Wire the guard into the app lifespan**

Modify `backend/src/ibkr_control/main.py`. Replace the existing `lifespan`:

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup: assert the runtime DB role enforces RLS (fail-closed), then start
    APScheduler with daily ingest jobs. Shutdown: stop it.
    """
    from ibkr_control.db.guards import assert_runtime_role_enforces_rls
    from ibkr_control.db.session import get_engine

    await assert_runtime_role_enforces_rls(get_engine())

    scheduler = create_scheduler()
    register_jobs(scheduler)
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
```

(Note: the endpoint test suite uses `ASGITransport` without a lifespan manager, so this guard does not run during those tests — it is exercised directly by `test_runtime_role_guard.py`.)

- [ ] **Step 6: Run lint + the new test + full suite spot-check**

Run: `cd backend && uv run ruff check . && uv run pytest tests/test_runtime_role_guard.py -v`
Expected: ruff clean, tests PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/src/ibkr_control/db/guards.py backend/src/ibkr_control/main.py backend/tests/test_runtime_role_guard.py
git commit -m "feat(sp1-rls-runtime): fail-closed boot guard rejects RLS-bypassing roles"
```

---

## Task 3: Self-healing per-transaction org context (survives commits)

**Why:** `SET LOCAL` dies on `COMMIT`. An endpoint that writes-then-reads would lose its org context under real RLS (silent default-deny). An `after_begin` listener re-applies the GUC on every new transaction so isolation is guaranteed regardless of intra-request commits.

**Ordering subtlety:** `org_context` runs `resolve_current_org_id` (a `memberships` query) which opens a transaction *before* `org_id` is known. Relying on the listener alone would leave that already-open transaction (and any endpoint query that reuses it) without context. So `org_context` both (a) stores the context in `session.info` for future transactions (listener) and (b) applies the GUC to the currently-open transaction immediately.

**Files:**
- Modify: `backend/src/ibkr_control/db/rls.py` (add shared helper + listener)
- Modify: `backend/src/ibkr_control/api/_context.py:57-64` (`org_context`)
- Test: `backend/tests/test_rls_context_survives_commit.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rls_context_survives_commit.py
"""org_context's RLS GUC survives an intra-request commit: after committing, a
subsequent query in the same session still sees app.current_org (re-applied by
the after_begin listener), so org-scoped reads remain isolated.
"""

import pytest
from sqlalchemy import text

from ibkr_control.db.rls import set_session_org_context


@pytest.mark.anyio
async def test_org_guc_reapplied_after_commit(app_rls_db_session):
    session = app_rls_db_session
    # Establish context the way org_context does: stash on session.info + apply now.
    set_session_org_context(session, org_id=4242, user_id=7)

    # Open a tx and read the GUC -> present.
    before = (
        await session.scalars(text("SELECT current_setting('app.current_org', true)"))
    ).one()
    assert before == "4242"

    # Commit ends the tx (SET LOCAL would normally be lost) ...
    await session.commit()

    # ... but the next tx re-applies it via the after_begin listener.
    after = (
        await session.scalars(text("SELECT current_setting('app.current_org', true)"))
    ).one()
    assert after == "4242"
```

- [ ] **Step 2: Run the test, expect FAIL**

Run: `cd backend && uv run pytest tests/test_rls_context_survives_commit.py -v`
Expected: FAIL with `ImportError: cannot import name 'set_session_org_context'`.

- [ ] **Step 3: Add the shared helper + listener to `db/rls.py`**

Append to `backend/src/ibkr_control/db/rls.py` (keep existing `apply_org_context` — `org_context` still uses it to apply the GUC to the already-open transaction; the listener handles every *later* transaction). `db/rls.py` already imports `text` and `AsyncSession`; add the two new imports:

```python
from sqlalchemy import event
from sqlalchemy.orm import Session

# session.info keys carrying the per-request RLS context for the after_begin
# listener. The stash lives on the SYNC session's .info (what the listener reads).
_ORG_KEY = "rls_org_id"
_USER_KEY = "rls_user_id"


def set_session_org_context(
    session: AsyncSession, *, org_id: int, user_id: int | None
) -> None:
    """Stash the RLS context on the session for the after_begin listener.

    Writes to the underlying sync session's ``.info`` — the same dict the
    ``after_begin`` listener reads — so the GUC is re-applied on every new
    transaction of this session (surviving intra-request commits). Applying the
    GUC to the *currently open* transaction is org_context's job (it awaits
    apply_org_context right after this), because the membership lookup may have
    already opened a transaction before org_id was known.
    """
    session.sync_session.info[_ORG_KEY] = org_id
    session.sync_session.info[_USER_KEY] = user_id


@event.listens_for(Session, "after_begin")
def _reapply_org_context(session: Session, transaction, connection) -> None:
    """Re-apply the org GUC on every new transaction that carries context.

    SET LOCAL is transaction-scoped; without this, the GUC would vanish after
    any commit mid-request. Fires on the sync Session under the async wrapper;
    ``connection`` is a sync Connection, so we execute synchronously here. Same
    GUC contract as apply_org_context ('' for a no-user context).
    """
    org_id = session.info.get(_ORG_KEY)
    if org_id is None:
        return
    user_id = session.info.get(_USER_KEY)
    connection.execute(
        text(
            "SELECT set_config('app.current_org', :o, true), "
            "set_config('app.current_user', :u, true)"
        ),
        {"o": str(org_id), "u": "" if user_id is None else str(user_id)},
    )
```

The Step 1 test passes the `AsyncSession` directly to `set_session_org_context`; the helper writes to `session.sync_session.info`, and the first `SELECT` opens a transaction whose `after_begin` applies the GUC — so the test needs only the stash (no explicit apply).

- [ ] **Step 4: Update `org_context` to stash + apply to the current tx**

Modify `backend/src/ibkr_control/api/_context.py`. Update imports and `org_context`:

```python
from ibkr_control.db.rls import apply_org_context, set_session_org_context
```

```python
async def org_context(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> int:
    """FastAPI dependency: resolve org, stash RLS context for the after_begin
    listener (so it survives intra-request commits), apply it to the currently
    open transaction, and return org_id.
    """
    org_id = await resolve_current_org_id(session, user_id=user.id, requested_org_id=None)
    set_session_org_context(session, org_id=org_id, user_id=user.id)
    await apply_org_context(session, org_id=org_id, user_id=user.id)
    return org_id
```

- [ ] **Step 5: Run the test, expect PASS**

Run: `cd backend && uv run pytest tests/test_rls_context_survives_commit.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full endpoint suite to confirm no regression in RLS scoping**

Run: `cd backend && uv run pytest -q`
Expected: PASS (337+ tests; the listener only adds guarantees).

- [ ] **Step 7: Commit**

```bash
git add backend/src/ibkr_control/db/rls.py backend/src/ibkr_control/api/_context.py backend/tests/test_rls_context_survives_commit.py
git commit -m "feat(sp1-rls-runtime): org RLS context survives intra-request commits (after_begin listener)"
```

---

## Task 4: Per-container privilege separation (migrate service + app_rls backend)

**Why:** The long-running app process must never hold owner credentials. A one-shot `migrate` service runs migrations as the owner; `backend` runs only uvicorn as `app_rls`.

**Files:**
- Modify: `backend/Dockerfile:44`, `backend/Dockerfile:62`
- Modify: `compose.yaml`, `compose.dev.yaml`, `compose.coolify.yaml`
- Modify: `.env.example`

- [ ] **Step 1: Dockerfile — stop chaining migrations into the app CMD**

Modify `backend/Dockerfile`. Change the `dev` target CMD (line 44) to:

```dockerfile
CMD ["sh", "-c", "exec uv run uvicorn ibkr_control.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir src"]
```

Change the `prod` target CMD (line 62) to:

```dockerfile
CMD ["sh", "-c", "exec uv run uvicorn ibkr_control.main:app --host 0.0.0.0 --port 8000"]
```

Migrations now run in the `migrate` compose service (Step 2), which overrides the command.

- [ ] **Step 2: `compose.yaml` — add migrate service, point backend at app_rls**

Modify `compose.yaml`. Add a `migrate` service and update `backend`:

```yaml
  migrate:
    build:
      context: ./backend
      dockerfile: Dockerfile
      target: prod
    environment:
      # Owner DSN — DDL + role/policy provisioning. Lives ONLY in this one-shot
      # service; the long-running backend never sees the owner credentials.
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
      JWT_SECRET: ${JWT_SECRET}
      APP_RLS_PASSWORD: ${APP_RLS_PASSWORD}
    command: ["sh", "-c", "uv run alembic upgrade head"]
    depends_on:
      postgres:
        condition: service_healthy
    restart: "no"

  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
      target: prod
    environment:
      # app_rls DSN — least privilege; subject to RLS. No owner credentials here.
      DATABASE_URL: postgresql+asyncpg://app_rls:${APP_RLS_PASSWORD}@postgres:5432/${POSTGRES_DB}
      JWT_SECRET: ${JWT_SECRET}
      JWT_LIFETIME_SECONDS: ${JWT_LIFETIME_SECONDS:-3600}
      BACKEND_CORS_ORIGINS: ${BACKEND_CORS_ORIGINS}
    depends_on:
      postgres:
        condition: service_healthy
      migrate:
        condition: service_completed_successfully
    dns:
      - 1.1.1.1
      - 8.8.8.8
```

Preserve any existing `backend` keys not shown here (ports, healthcheck, volumes) — only the `environment` (DATABASE_URL → app_rls) and `depends_on` (add `migrate`) change, plus the new `migrate` service. Verify against the current `compose.yaml` before editing.

- [ ] **Step 3: `compose.dev.yaml` — dev override keeps the split**

Modify `compose.dev.yaml`. Ensure `migrate` runs (inherits from base) and the dev `backend` override only changes the command to `--reload` (it already does); add the `migrate` dependency if the dev file redeclares `depends_on`. The owner/app_rls DSN split is inherited from `compose.yaml`. If `compose.dev.yaml` overrides `backend.environment.DATABASE_URL`, point it at `app_rls` too:

```yaml
  backend:
    environment:
      DATABASE_URL: postgresql+asyncpg://app_rls:${APP_RLS_PASSWORD}@postgres:5432/${POSTGRES_DB}
```

(If the dev file does not override DATABASE_URL, leave it inheriting from base — do not duplicate.)

- [ ] **Step 4: `compose.coolify.yaml` — same split for prod**

Modify `compose.coolify.yaml`. Add the `migrate` service (mirroring Step 2, `target: prod`, owner DSN, `restart: "no"`) and change `backend.environment.DATABASE_URL` to the `app_rls` DSN, and add `migrate: { condition: service_completed_successfully }` to `backend.depends_on`:

```yaml
  migrate:
    build:
      context: ./backend
      dockerfile: Dockerfile
      target: prod
    environment:
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
      JWT_SECRET: ${JWT_SECRET}
      APP_RLS_PASSWORD: ${APP_RLS_PASSWORD}
    command: ["sh", "-c", "uv run alembic upgrade head"]
    depends_on:
      postgres:
        condition: service_healthy
    restart: "no"
    labels:
      - "coolify.managed=true"

  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
      target: prod
    restart: unless-stopped
    environment:
      DATABASE_URL: postgresql+asyncpg://app_rls:${APP_RLS_PASSWORD}@postgres:5432/${POSTGRES_DB}
      JWT_SECRET: ${JWT_SECRET}
      JWT_LIFETIME_SECONDS: ${JWT_LIFETIME_SECONDS:-3600}
      BACKEND_CORS_ORIGINS: ${BACKEND_CORS_ORIGINS}
    depends_on:
      postgres:
        condition: service_healthy
      migrate:
        condition: service_completed_successfully
    labels:
      - "coolify.managed=true"
```

- [ ] **Step 5: `.env.example` — document the new variable**

Modify `.env.example`. Keep `DATABASE_URL` as the owner DSN (used by the `migrate` service and by local host tooling), and add `APP_RLS_PASSWORD`:

```bash
# Owner DSN — used by the one-shot `migrate` service (alembic) and host tooling.
DATABASE_URL=postgresql+asyncpg://ibkr:changeme@postgres:5432/ibkr_control

# Password for the non-bypass app_rls role the long-running backend connects as.
# The migrate service uses it to CREATE the role; the backend service embeds it
# in its own DATABASE_URL (postgresql+asyncpg://app_rls:${APP_RLS_PASSWORD}@...).
# Dev/test default is "app_rls_pw" (see db/rls.py::app_rls_password); set a real
# secret in prod.
APP_RLS_PASSWORD=app_rls_pw
```

- [ ] **Step 6: Verify the split boots (prod-local mirror of Coolify)**

Run (from repo root): `make prod-local`
Then: `docker compose -f compose.yaml ps` and `docker compose -f compose.yaml logs backend | tail -30`
Expected: `migrate` exits 0; `backend` starts; logs show NO `RuntimeError: Refusing to start` (it connects as app_rls) and the scheduler registers 3 jobs without a permission error on `apscheduler_jobs`.

- [ ] **Step 7: Update local `.env` (manual, not committed)**

Tell the operator: add `APP_RLS_PASSWORD=app_rls_pw` to local `.env` (gitignored). Do NOT commit `.env`.

- [ ] **Step 8: Commit (infra only — `.env` excluded)**

```bash
git add backend/Dockerfile compose.yaml compose.dev.yaml compose.coolify.yaml .env.example
git commit -m "feat(sp1-rls-runtime): split migrate (owner) from backend (app_rls) per-container"
```

---

## Task 5: Runtime-parity verification + full-suite green

**Why:** The original gap survived because tests ran as `app_rls` while prod ran as superuser. Lock the parity with an explicit invariant test, then verify the whole suite and boot smoke.

**Files:**
- Test: `backend/tests/test_runtime_role_guard.py` (extend with the parity invariant)

- [ ] **Step 1: Add the runtime-role invariant test**

Append to `backend/tests/test_runtime_role_guard.py`:

```python
@pytest.mark.anyio
async def test_app_rls_role_is_not_superuser_and_not_bypassrls(app_rls_db_session):
    """The role the app actually connects as must be subject to RLS."""
    row = (
        await app_rls_db_session.execute(
            text(
                "SELECT current_setting('is_superuser')::bool AS is_su, "
                "COALESCE((SELECT rolbypassrls FROM pg_roles "
                "WHERE rolname = current_user), false) AS bypass"
            )
        )
    ).one()
    assert row.is_su is False
    assert row.bypass is False
```

Add `from sqlalchemy import text` to the test file's imports if not already present.

- [ ] **Step 2: Run the parity test**

Run: `cd backend && uv run pytest tests/test_runtime_role_guard.py -v`
Expected: PASS (all three tests).

- [ ] **Step 3: Full backend suite + lint**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: all green, no drift, no format diff. Test count rises by the new tests (337 → ~342).

- [ ] **Step 4: Boot smoke as app_rls (canonical, in-container)**

Run (from repo root): `make prod-local && docker compose -f compose.yaml logs backend | grep -E "Registered 3 ingest jobs|Refusing to start"`
Expected: see `Registered 3 ingest jobs`; do NOT see `Refusing to start`.

- [ ] **Step 5: Manual cross-tenant verification (the acceptance proof)**

With two orgs seeded, issue a request as a member of org A and confirm an org-scoped table query returns only org A rows even if the query omits an explicit `organization_id` filter (RLS now enforces it because the app connects as app_rls). Document the result in the PR description.

- [ ] **Step 6: Commit**

```bash
git add backend/tests/test_runtime_role_guard.py
git commit -m "test(sp1-rls-runtime): lock runtime-role parity (app connects as non-bypass app_rls)"
```

---

## Self-Review

**Spec coverage:**
- D1 (per-container privilege separation) → Task 4. ✓
- D2 (owner stays bootstrap superuser; 3-role split deferred to SP4) → honored: `migrate` uses `POSTGRES_USER`; no new owner role created. ✓
- D3 (fail-closed boot guard) → Task 2. ✓
- D4 (self-healing SET LOCAL via after_begin) → Task 3. ✓
- D5 (runtime-parity tests) → Tasks 2 + 5 + 3 + 1 (guard reject/accept, role invariant, commit-survival, apscheduler DML). ✓
- Acceptance criteria 1-6 (spec §6) → Task 4 Step 6 (boot), Task 2 (guard), Task 3 (commit isolation), Task 5 Steps 3-5 (suite/lint/smoke/manual). ✓
- Implementation discovery not in spec: `apscheduler_jobs` DDL under app_rls → Task 1 (consistent with the "app never does DDL" principle; noted to user).

**Placeholder scan:** `<rev>` in Task 1 is a real Alembic revision id generated in Step 1 (not a plan placeholder) — the engineer fills it from `alembic revision` output and sets `down_revision = "a1f2c3d4e5b6"`. No TBD/TODO/"handle edge cases" remain.

**Type consistency:** `set_session_org_context(session, *, org_id, user_id)` and `apply_org_context(session, *, org_id, user_id)` signatures are consistent across Tasks 3-4. Guard `assert_runtime_role_enforces_rls(engine)` consistent across Tasks 2 and main.py. GUC keys `rls_org_id`/`rls_user_id` consistent within `db/rls.py`.

**Note on Task 3 sync/async seam:** `set_session_org_context` takes the `AsyncSession` and writes to `session.sync_session.info` — the same `.info` dict the `after_begin` listener (which fires on the sync `Session`) reads. `org_context` applies the GUC to the already-open transaction via `apply_org_context`; the listener covers every later transaction. No sync/async mismatch.
