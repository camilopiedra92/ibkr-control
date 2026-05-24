# D5 + D2 Persistent State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminar dos pedazos de estado in-memory (APScheduler jobstore D5 + `_LAST_TRIGGER` rate limit D2) reemplazándolos por estado persistente en Postgres, sin tocar lógica de negocio.

**Architecture:** APScheduler 3.x usa `SQLAlchemyJobStore` (sync) corriendo sobre la misma DB que la app (vía driver `psycopg` v3 paralelo a `asyncpg`); el rate limit del endpoint `/api/ingest/trigger` se mueve a una columna `users.last_ingest_trigger_at` actualizada vía `UPDATE ... WHERE ...` atómico (sin race condition TOCTOU). Single feature branch `chore/persistent-state-d5-d2` con dos commits temáticos + un commit de docs.

**Tech Stack:** Python 3.12, APScheduler 3.11.2, SQLAlchemy 2.x async + core sync, asyncpg (app) + psycopg v3 (scheduler jobstore), Alembic, FastAPI, pytest + testcontainer.

---

## File Structure

### D5 — Persistent scheduler

- **Modify** `backend/pyproject.toml` — agregar `psycopg[binary]>=3.1`, pin `apscheduler==3.11.*`
- **Modify** `backend/src/ibkr_control/config.py` — agregar property `database_url_sync`
- **Modify** `backend/src/ibkr_control/scheduler/__init__.py` (actualmente vacío, 0 bytes) — crear factory `create_scheduler()`
- **Modify** `backend/src/ibkr_control/scheduler/jobs.py` — agregar `replace_existing=True` + `misfire_grace_time=21600` a los 3 `add_job()`; eliminar el loop manual `remove_job`
- **Modify** `backend/src/ibkr_control/main.py` — usar `create_scheduler()` en lugar de `AsyncIOScheduler()` directo
- **Modify** `backend/tests/test_scheduler.py` — agregar tests para `misfire_grace_time` + factory + persistencia integration

### D2 — DB-backed rate limit

- **Modify** `backend/src/ibkr_control/auth/models.py` — agregar columna `last_ingest_trigger_at`
- **Create** `backend/alembic/versions/<hash>_add_users_last_ingest_trigger_at.py` — migración G (autogenerada)
- **Modify** `backend/src/ibkr_control/api/ingest.py` — eliminar `_LAST_TRIGGER` dict + `_utcnow()` helper; reemplazar check con `UPDATE` atómico condicional
- **Modify** `backend/tests/api/test_ingest.py` — eliminar `monkeypatch` del dict; agregar test de persistencia + test de columna actualizada

### Docs

- **Modify** `CLAUDE.md` — sacar D2 y D5 de "Deuda conocida heredada de Phase 2"
- **Modify** `docs/plans/2026-05-24-phase2-polish-backlog.md` — marcar D2 y D5 como `RESOLVED` con hash de commit
- **Modify** `docs/specs/2026-05-24-phase2-ingestion-design.md` — agregar nota "superseded" en §D5/D6 (si existe)

---

## Task 1: Setup branch + dependencies

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock` (regenerated)

- [ ] **Step 1: Create branch from main**

```bash
git checkout main
git pull --ff-only || true  # ensure local main is at tip if remote exists
git checkout -b chore/persistent-state-d5-d2
```

Expected: `Switched to a new branch 'chore/persistent-state-d5-d2'`

- [ ] **Step 2: Update pyproject.toml dependencies**

Locate the existing `apscheduler>=3.10` line in `backend/pyproject.toml` and modify the dependencies block.

Change `"apscheduler>=3.10",` to `"apscheduler==3.11.*",` and add `"psycopg[binary]>=3.1",` immediately after.

Resulting block should look like (example showing surrounding context — adapt to actual file shape):

```toml
dependencies = [
    # ... existing deps ...
    "apscheduler==3.11.*",
    "psycopg[binary]>=3.1",
    # ... rest ...
]
```

- [ ] **Step 3: Sync dependencies**

```bash
cd backend && uv sync
```

Expected: lock file updated, both packages installed. No errors.

- [ ] **Step 4: Verify installed versions**

```bash
cd backend && uv run python -c "import apscheduler, psycopg; print(apscheduler.__version__, psycopg.__version__)"
```

Expected: `3.11.x` and `3.x.x` printed. If either fails to import, troubleshoot before continuing.

- [ ] **Step 5: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock
git commit -m "$(cat <<'EOF'
chore(deps): pin apscheduler 3.11.* + add psycopg v3

Prepares for D5 (persistent SQLAlchemyJobStore — needs sync driver
distinct from asyncpg) and pins APScheduler minor version to avoid
silent jobstore schema changes on uv sync.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add `database_url_sync` property to Settings

**Files:**
- Modify: `backend/src/ibkr_control/config.py`
- Modify: `backend/tests/test_config.py`

- [ ] **Step 1: Write failing test**

Add to `backend/tests/test_config.py` (append at end of file):

```python
def test_database_url_sync_swaps_asyncpg_for_psycopg(monkeypatch):
    """Sync URL replaces +asyncpg with +psycopg for APScheduler jobstore use."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@host:5432/db")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")

    from ibkr_control.config import Settings, get_settings
    get_settings.cache_clear()

    s = Settings()  # type: ignore[call-arg]
    assert s.database_url_sync == "postgresql+psycopg://u:p@host:5432/db"


def test_database_url_sync_idempotent_if_already_sync(monkeypatch):
    """If DATABASE_URL is already a sync form, leave it alone."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@host:5432/db")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")

    from ibkr_control.config import Settings, get_settings
    get_settings.cache_clear()

    s = Settings()  # type: ignore[call-arg]
    assert s.database_url_sync == "postgresql+psycopg://u:p@host:5432/db"
```

- [ ] **Step 2: Run failing test**

```bash
cd backend && uv run pytest tests/test_config.py::test_database_url_sync_swaps_asyncpg_for_psycopg -v
```

Expected: `FAILED ... AttributeError: 'Settings' object has no attribute 'database_url_sync'`

- [ ] **Step 3: Implement `database_url_sync` property**

In `backend/src/ibkr_control/config.py`, immediately after the existing `cors_origins_list` property, add:

```python
    @property
    def database_url_sync(self) -> str:
        """Sync DB URL for APScheduler jobstore (uses psycopg v3, not asyncpg).

        APScheduler 3.x SQLAlchemyJobStore is sync-only; it issues blocking
        SELECT/UPDATE/INSERT against `apscheduler_jobs` from within
        AsyncIOScheduler's wake-up thread. We need a sync driver distinct
        from the async one used by the app's request pipeline.
        """
        return self.database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/test_config.py -v
```

Expected: all tests in `test_config.py` PASS, including the two new ones.

- [ ] **Step 5: No commit yet** — bundled with Task 3 + Task 4 in the D5 commit.

---

## Task 3: Create scheduler factory with persistent jobstore

**Files:**
- Modify: `backend/src/ibkr_control/scheduler/__init__.py` (currently empty)
- Modify: `backend/tests/test_scheduler.py`

- [ ] **Step 1: Write failing test for factory**

Append to `backend/tests/test_scheduler.py`:

```python
def test_create_scheduler_uses_sqlalchemy_jobstore(monkeypatch):
    """Factory returns AsyncIOScheduler configured with persistent jobstore."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")

    from ibkr_control.config import get_settings
    get_settings.cache_clear()

    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
    from ibkr_control.scheduler import create_scheduler

    scheduler = create_scheduler()
    jobstore = scheduler._jobstores["default"]  # noqa: SLF001 (internal access intentional)
    assert isinstance(jobstore, SQLAlchemyJobStore)
```

Note: we access `_jobstores` (private) because APScheduler doesn't expose a public getter. This is a unit test of config, so reading internals is acceptable here.

- [ ] **Step 2: Run failing test**

```bash
cd backend && uv run pytest tests/test_scheduler.py::test_create_scheduler_uses_sqlalchemy_jobstore -v
```

Expected: `FAILED ... ImportError: cannot import name 'create_scheduler' from 'ibkr_control.scheduler'`

- [ ] **Step 3: Implement factory**

Write `backend/src/ibkr_control/scheduler/__init__.py` (replacing the empty file):

```python
"""Scheduler factory — single source of truth for AsyncIOScheduler config."""
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ibkr_control.config import get_settings


def create_scheduler() -> AsyncIOScheduler:
    """Create the production scheduler with persistent SQLAlchemyJobStore.

    Jobs are stored in `apscheduler_jobs` (auto-created by APScheduler on
    first start — no Alembic migration needed). Restart-safe: cron triggers
    re-register via register_jobs() with replace_existing=True, and missed
    runs within misfire_grace_time get caught up at boot.
    """
    settings = get_settings()
    jobstores = {
        "default": SQLAlchemyJobStore(
            url=settings.database_url_sync,
            tablename="apscheduler_jobs",
        ),
    }
    return AsyncIOScheduler(jobstores=jobstores, timezone="UTC")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/test_scheduler.py -v
```

Expected: all tests PASS, including the new factory test.

- [ ] **Step 5: No commit yet** — bundled with Task 4 + Task 5.

---

## Task 4: Persistence integration test + wire factory into main.py

**Files:**
- Modify: `backend/tests/test_scheduler.py`
- Modify: `backend/src/ibkr_control/main.py`

- [ ] **Step 1: Add module-level noop function for serialization**

At the top of `backend/tests/test_scheduler.py` (after existing imports, before existing tests), add:

```python
def _persistence_noop() -> None:
    """Module-level noop so APScheduler SQLAlchemyJobStore can serialize the reference.

    APScheduler stores a string-path reference like
    'tests.test_scheduler:_persistence_noop' — anonymous lambdas would fail
    to serialize via the jobstore.
    """
    pass
```

- [ ] **Step 2: Write failing persistence integration test**

Append to `backend/tests/test_scheduler.py`:

```python
def test_jobs_persist_across_scheduler_instances(postgres_container):
    """SQLAlchemyJobStore actually persists jobs across separate scheduler instances.

    Simulates the "container restart" scenario: scheduler A adds a job, shuts
    down, scheduler B (new instance, same DB) recovers the job from the
    persistent jobstore.

    Uses BackgroundScheduler (not AsyncIO) to avoid event-loop complications
    in a sync test — the jobstore behavior is identical regardless of
    scheduler class.
    """
    from datetime import datetime, timedelta

    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
    from apscheduler.schedulers.background import BackgroundScheduler

    async_url = postgres_container.get_connection_url()
    sync_url = async_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    jobstore_kwargs = dict(url=sync_url, tablename="apscheduler_jobs_test_persist")

    s1 = BackgroundScheduler(jobstores={"default": SQLAlchemyJobStore(**jobstore_kwargs)})
    s1.start(paused=True)
    try:
        s1.add_job(
            _persistence_noop,
            trigger="date",
            run_date=datetime.utcnow() + timedelta(days=365),
            id="persist_test",
            replace_existing=True,
        )
    finally:
        s1.shutdown(wait=False)

    # New scheduler, same DB → should recover the job
    s2 = BackgroundScheduler(jobstores={"default": SQLAlchemyJobStore(**jobstore_kwargs)})
    s2.start(paused=True)
    try:
        recovered = s2.get_job("persist_test")
        assert recovered is not None, "Job was not persisted by SQLAlchemyJobStore"
        assert recovered.id == "persist_test"
        s2.remove_job("persist_test")
    finally:
        s2.shutdown(wait=False)
```

- [ ] **Step 3: Run persistence integration test**

```bash
cd backend && uv run pytest tests/test_scheduler.py::test_jobs_persist_across_scheduler_instances -v
```

Expected: PASS (this test creates its own scheduler with explicit SQLAlchemyJobStore — it doesn't depend on `main.py`'s setup). This confirms the jobstore mechanism works end-to-end with the real DB. **If it fails**, troubleshoot driver/network issues before continuing.

Note: This is not a "failing test driving impl" — it's a sanity check that the SQLAlchemyJobStore actually persists in our testcontainer setup. The real driver is the factory test in Task 3 (which already passes).

- [ ] **Step 4: Wire factory into main.py**

In `backend/src/ibkr_control/main.py`, replace the lifespan body. The current lifespan is:

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup: start APScheduler with daily ingest jobs. Shutdown: stop it."""
    scheduler = AsyncIOScheduler()
    register_jobs(scheduler)
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
```

Change to:

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup: start APScheduler with daily ingest jobs. Shutdown: stop it."""
    scheduler = create_scheduler()
    register_jobs(scheduler)
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
```

And replace the import line `from apscheduler.schedulers.asyncio import AsyncIOScheduler` with `from ibkr_control.scheduler import create_scheduler`. Leave `from ibkr_control.scheduler.jobs import register_jobs` as-is.

- [ ] **Step 5: Run full test suite to confirm no regression**

```bash
cd backend && uv run pytest -q
```

Expected: all 186 existing tests PASS + the 3 new ones added in Tasks 2-4. Total ~189.

- [ ] **Step 6: No commit yet** — bundled with Task 5.

---

## Task 5: Add misfire_grace_time + replace_existing to register_jobs

**Files:**
- Modify: `backend/src/ibkr_control/scheduler/jobs.py`
- Modify: `backend/tests/test_scheduler.py`

- [ ] **Step 1: Write failing test**

Append to `backend/tests/test_scheduler.py`:

```python
def test_jobs_have_misfire_grace_time_set():
    """Jobs configured with 6h grace so a restart between trigger and exec catches up."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    scheduler = AsyncIOScheduler()
    register_jobs(scheduler)
    for j in scheduler.get_jobs():
        assert j.misfire_grace_time == 21600, f"{j.id} missing misfire_grace_time=21600"
```

- [ ] **Step 2: Run failing test**

```bash
cd backend && uv run pytest tests/test_scheduler.py::test_jobs_have_misfire_grace_time_set -v
```

Expected: `FAILED` — current jobs default to APScheduler's default grace (which is 1 second), not 21600.

- [ ] **Step 3: Modify register_jobs**

In `backend/src/ibkr_control/scheduler/jobs.py`, replace the entire `register_jobs()` function (currently lines 98-136):

```python
def register_jobs(scheduler: AsyncIOScheduler) -> None:
    """Registra los 3 cron jobs en el scheduler. Idempotente — usa replace_existing=True.

    Flex    daily:   07:00 COT = 12:00 UTC (Bogota no tiene DST).
    TRM     daily:   19:30 COT = 00:30 UTC del dia siguiente.
    Cleanup hourly:  cada hora en :00 UTC para limpiar JobTracker stale entries.

    Todos con max_instances=1 + coalesce=True para evitar solapamiento y backlog.
    misfire_grace_time=21600 (6h) para que un container restart en la ventana
    del cron diario recupere el run perdido al boot (jobs son idempotentes).
    """
    scheduler.add_job(
        _run_flex_for_all_users,
        CronTrigger(hour=12, minute=0, timezone="UTC"),
        id="flex_daily",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=21600,
        replace_existing=True,
    )

    scheduler.add_job(
        _run_trm_global,
        CronTrigger(hour=0, minute=30, timezone="UTC"),
        id="trm_daily",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=21600,
        replace_existing=True,
    )

    scheduler.add_job(
        _cleanup_old_jobs,
        CronTrigger(minute=0, timezone="UTC"),  # every hour at :00
        id="cleanup_job_tracker",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=21600,
        replace_existing=True,
    )

    logger.info("Registered 3 ingest jobs: flex_daily, trm_daily, cleanup_job_tracker")
```

Note: the loop `for job_id in ("flex_daily", ...): scheduler.remove_job(job_id)` is removed — `replace_existing=True` does the same job idempotently and atomically.

- [ ] **Step 4: Run tests to verify pass**

```bash
cd backend && uv run pytest tests/test_scheduler.py -v
```

Expected: ALL scheduler tests PASS including the new `test_jobs_have_misfire_grace_time_set`.

- [ ] **Step 5: Run full suite**

```bash
cd backend && uv run pytest -q
```

Expected: 186 + 4 new = 190 tests PASS.

- [ ] **Step 6: Commit D5**

```bash
git add backend/src/ibkr_control/config.py \
        backend/src/ibkr_control/scheduler/__init__.py \
        backend/src/ibkr_control/scheduler/jobs.py \
        backend/src/ibkr_control/main.py \
        backend/tests/test_config.py \
        backend/tests/test_scheduler.py

git commit -m "$(cat <<'EOF'
refactor(scheduler): persistent SQLAlchemyJobStore + misfire recovery (D5)

APScheduler jobs ahora viven en tabla apscheduler_jobs (auto-creada por
APScheduler al boot). Container restart en cualquier momento (incluyendo
durante una ventana de cron) ya no pierde el run del dia: misfire_grace_time
de 6h captura el job al proximo start si la ventana sigue valida.

- Settings.database_url_sync property (swap asyncpg -> psycopg para el
  jobstore sync que APScheduler 3.x requiere)
- scheduler.__init__.create_scheduler() factory: single source of truth
  para AsyncIOScheduler + SQLAlchemyJobStore config
- register_jobs() usa replace_existing=True (mas limpio que el loop manual
  de remove_job) y misfire_grace_time=21600 en los 3 crons
- main.py lifespan llama create_scheduler() en lugar de instanciar
  AsyncIOScheduler directo

Resuelve D5 del polish backlog Phase 2. Re-abre la decision locked spec
§D6 (in-memory jobstore) — cambio justificado: ya tenemos Postgres en el
mismo container, el lift fue chico (~10 lineas + un test), el upside es
real (restart-safe sin perder ingests diarios).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Add `last_ingest_trigger_at` column to User model + Alembic migration

**Files:**
- Modify: `backend/src/ibkr_control/auth/models.py`
- Create: `backend/alembic/versions/<autogen-hash>_add_users_last_ingest_trigger_at.py`

- [ ] **Step 1: Modify User model**

In `backend/src/ibkr_control/auth/models.py`, after the existing `setup_progress` line (line 23), add a new column:

```python
    last_ingest_trigger_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

Final shape of the model class (full content for clarity):

```python
class User(SQLAlchemyBaseUserTable[int], Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    setup_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    setup_progress: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    last_ingest_trigger_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

- [ ] **Step 2: Generate Alembic migration**

```bash
cd backend && uv run alembic revision --autogenerate -m "add users.last_ingest_trigger_at for rate limit"
```

Expected: a new file appears in `backend/alembic/versions/<hash>_add_users_last_ingest_trigger_at.py`.

- [ ] **Step 3: Inspect generated migration**

Open the new migration file and verify the `upgrade()` contains exactly:

```python
def upgrade() -> None:
    op.add_column('users', sa.Column('last_ingest_trigger_at', sa.DateTime(timezone=True), nullable=True))
```

And `downgrade()` contains:

```python
def downgrade() -> None:
    op.drop_column('users', 'last_ingest_trigger_at')
```

If autogenerate added anything else (unrelated drift, index changes, etc.), edit the file to leave ONLY the two operations above. Drift is a red flag — investigate before proceeding.

- [ ] **Step 4: Run the migration test**

```bash
cd backend && uv run pytest tests/test_migrations.py -v
```

Expected: `test_migrations_apply_cleanly_and_match_metadata` PASSES. If it fails on a metadata mismatch, the autogenerated migration is incomplete — re-inspect step 3.

- [ ] **Step 5: Run full suite to confirm no regression**

```bash
cd backend && uv run pytest -q
```

Expected: 190 PASS (no new tests yet — Tasks 7-8 add them).

- [ ] **Step 6: No commit yet** — bundled with Task 7 + Task 8.

---

## Task 7: Write failing test for DB-backed rate limit

**Files:**
- Modify: `backend/tests/api/test_ingest.py`

- [ ] **Step 1: Modify existing rate-limit test**

In `backend/tests/api/test_ingest.py`, replace the existing `test_trigger_rate_limit_429` (currently lines 14-27) with:

```python
async def test_trigger_rate_limit_429(client: AsyncClient, auth_headers: dict, monkeypatch):
    """Llamadas seguidas devuelven 429. Estado vive en DB (users.last_ingest_trigger_at)."""

    async def noop(*args, **kwargs) -> int:
        return 999

    monkeypatch.setattr("ibkr_control.api.ingest._launch_manual_job", noop)

    r1 = await client.post("/api/ingest/trigger", json={"kind": "trm"}, headers=auth_headers)
    assert r1.status_code == 200

    r2 = await client.post("/api/ingest/trigger", json={"kind": "trm"}, headers=auth_headers)
    assert r2.status_code == 429
```

The change: removed `monkeypatch.setattr("ibkr_control.api.ingest._LAST_TRIGGER", {})` — the new impl doesn't have that module-level dict, so the line would error at collection time once Task 8 lands.

- [ ] **Step 2: Add new test — trigger writes to DB**

Append to `backend/tests/api/test_ingest.py`:

```python
async def test_trigger_persists_timestamp_in_user_row(
    client: AsyncClient, auth_headers: dict, app_with_db, monkeypatch
):
    """POST /api/ingest/trigger debe actualizar users.last_ingest_trigger_at."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from ibkr_control.auth.models import User
    from ibkr_control.config import get_settings

    async def noop(*args, **kwargs) -> int:
        return 999

    monkeypatch.setattr("ibkr_control.api.ingest._launch_manual_job", noop)

    r = await client.post("/api/ingest/trigger", json={"kind": "trm"}, headers=auth_headers)
    assert r.status_code == 200

    # Verify the column was updated using a fresh engine on the same DB
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    session_local = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_local() as s:
            user = (
                await s.scalars(select(User).where(User.email == "api_test@test.com"))
            ).one()
            assert user.last_ingest_trigger_at is not None, (
                "trigger endpoint did not persist last_ingest_trigger_at"
            )
    finally:
        await engine.dispose()
```

- [ ] **Step 3: Run failing test**

```bash
cd backend && uv run pytest tests/api/test_ingest.py::test_trigger_persists_timestamp_in_user_row -v
```

Expected: `FAILED ... assert user.last_ingest_trigger_at is not None` — current impl writes to `_LAST_TRIGGER` dict, not to the DB column.

The existing `test_trigger_rate_limit_429` may also fail now if removing the monkeypatch line caused state leakage from a previous test. That's expected — Task 8 makes both pass.

- [ ] **Step 4: No commit yet** — bundled with Task 8.

---

## Task 8: Implement DB-backed atomic UPDATE rate limit

**Files:**
- Modify: `backend/src/ibkr_control/api/ingest.py`

- [ ] **Step 1: Replace rate-limit logic in trigger endpoint**

Open `backend/src/ibkr_control/api/ingest.py` and apply these changes:

**Remove lines 25-32** (the `_LAST_TRIGGER` dict + `_utcnow` helper + comment):

```python
# Rate limit: module-level dict tracks last trigger time per user.
# V1: in-process, single replica. Container restart resets cooldown.
# Cooldown duration is config-driven via settings.ingest_trigger_cooldown_seconds.
_LAST_TRIGGER: dict[int, datetime] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
```

**Replace** the existing `trigger_manual_refresh` function (currently lines 35-53) with:

```python
@router.post("/trigger", response_model=IngestJobStarted)
async def trigger_manual_refresh(
    payload: IngestTrigger,
    background: BackgroundTasks,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> IngestJobStarted:
    """Trigger manual del ingest. Rate-limited via UPDATE atomico condicional.

    El UPDATE solo afecta una fila si el cooldown ya pasó; rowcount=0 indica
    rate-limited y devolvemos 429 con el tiempo restante. Esto elimina el
    race condition TOCTOU del patron check-then-set y persiste el estado en
    DB (sobrevive container restart, multi-replica safe).
    """
    from sqlalchemy import or_, select, update

    from ibkr_control.auth.models import User as UserModel

    settings = get_settings()
    cooldown = timedelta(seconds=settings.ingest_trigger_cooldown_seconds)
    now = datetime.now(timezone.utc)
    cutoff = now - cooldown

    result = await session.execute(
        update(UserModel)
        .where(UserModel.id == user.id)
        .where(
            or_(
                UserModel.last_ingest_trigger_at.is_(None),
                UserModel.last_ingest_trigger_at < cutoff,
            )
        )
        .values(last_ingest_trigger_at=now)
    )
    await session.commit()

    if result.rowcount == 0:
        current = await session.scalar(
            select(UserModel.last_ingest_trigger_at).where(UserModel.id == user.id)
        )
        wait_seconds = (
            int((cooldown - (now - current)).total_seconds()) if current else int(cooldown.total_seconds())
        )
        raise HTTPException(
            status_code=429,
            detail=f"Espera {wait_seconds}s antes de reintentar",
        )

    job_id = await _launch_manual_job(payload.kind, user.id, background)
    return IngestJobStarted(job_id=job_id)
```

**Verify imports** at the top of the file include all of: `asyncio`, `json`, `datetime`, `timedelta`, `timezone`, `APIRouter`, `BackgroundTasks`, `Depends`, `HTTPException`, `Query`, `select`, `AsyncSession`, `async_sessionmaker`. The `select` import already exists (line 7); no new imports needed at module level since `or_` and `update` are imported inside the function. `User` from `ibkr_control.auth.models` is already imported (line 12).

- [ ] **Step 2: Run the two D2 tests**

```bash
cd backend && uv run pytest tests/api/test_ingest.py -v
```

Expected: ALL tests in the file PASS, including:
- `test_trigger_rate_limit_429` (with monkeypatch removed)
- `test_trigger_persists_timestamp_in_user_row` (the new one)
- The pre-existing `test_get_logs_empty_when_no_runs`, `test_trigger_invalid_kind_returns_422`, `test_stream_unknown_job_returns_404`, `test_stream_emits_done_event`

- [ ] **Step 3: Run full suite**

```bash
cd backend && uv run pytest -q
```

Expected: 191 PASS (190 + 1 new test added in Task 7).

- [ ] **Step 4: Commit D2**

```bash
git add backend/src/ibkr_control/auth/models.py \
        backend/alembic/versions/ \
        backend/src/ibkr_control/api/ingest.py \
        backend/tests/api/test_ingest.py

git commit -m "$(cat <<'EOF'
refactor(api): DB-backed rate limit via atomic UPDATE (D2)

Reemplaza el dict in-memory _LAST_TRIGGER por una columna
users.last_ingest_trigger_at actualizada con UPDATE atomico condicional.

- Migration G: add users.last_ingest_trigger_at TIMESTAMPTZ NULL
- POST /api/ingest/trigger:
  UPDATE users SET last_ingest_trigger_at = now()
  WHERE id = $1 AND (last_ingest_trigger_at IS NULL
                     OR last_ingest_trigger_at < now() - cooldown)
  Si rowcount = 0 -> 429 con tiempo restante; sin race TOCTOU.
- Elimina _LAST_TRIGGER dict y _utcnow() helper de api/ingest.py
- Estado sobrevive container restart; multi-replica-safe (cualquier
  replica ve el ultimo trigger, no hay N * cooldown overrun)

Resuelve D2 del polish backlog Phase 2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Update docs to reflect D5 + D2 resolution

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/plans/2026-05-24-phase2-polish-backlog.md`
- Modify: `docs/specs/2026-05-24-phase2-ingestion-design.md`

- [ ] **Step 1: Capture commit hashes**

```bash
git log --oneline -n 3
```

Note the hashes for the D5 commit and the D2 commit. Use them in the next steps as `<D5_HASH>` and `<D2_HASH>`.

- [ ] **Step 2: Update CLAUDE.md — remove D2 + D5 from "Deuda conocida heredada"**

In `CLAUDE.md`, locate the section `### Deuda conocida heredada de Phase 2 (resumen)`. Find the two lines:

```
- **D2** `_LAST_TRIGGER` in-memory rate limit — resetea con restart, V2=Redis
- **D5** APScheduler in-memory jobstore — locked spec §D6
```

Delete both lines. The other items (D1, D3, D4, D6-D11) keep their existing labels. Add at the bottom of that bullet list:

```
- **D2 + D5 RESOLVED**: persistent state migration — ver commits <D5_HASH> + <D2_HASH>
```

(Replace `<D5_HASH>` and `<D2_HASH>` with the actual short hashes captured in Step 1.)

- [ ] **Step 3: Update polish backlog**

In `docs/plans/2026-05-24-phase2-polish-backlog.md`:

Find the section header `### D2 — \`_LAST_TRIGGER\` in-memory rate limit` (around line 253). Change to:

```markdown
### D2 — `_LAST_TRIGGER` in-memory rate limit — RESOLVED <D2_HASH>

**Resolution:** Reemplazado por columna `users.last_ingest_trigger_at` +
UPDATE atomico condicional. Ver commit <D2_HASH>.

**Original issue (preserved for context):**
```

(Leave the rest of the section as it was — the "Issue", "Por qué no se fixa", "Quien hereda" subsections become historical record.)

Find the section header `### D5 — APScheduler in-memory jobstore (decisión locked spec #D6)` (around line 300). Change to:

```markdown
### D5 — APScheduler in-memory jobstore — RESOLVED <D5_HASH>

**Resolution:** Migrado a SQLAlchemyJobStore (jobs persisten en tabla
`apscheduler_jobs` auto-creada por APScheduler) + `misfire_grace_time=21600`
en los 3 crons. Ver commit <D5_HASH>.

**Original issue (preserved for context):**
```

(Leave the rest of the section as historical record.)

- [ ] **Step 4: Update Phase 2 spec — superseded note**

Open `docs/specs/2026-05-24-phase2-ingestion-design.md` and grep for "in-memory" or "D5" or "D6" to locate the relevant section about scheduler decisions. There should be a discussion of why APScheduler uses in-memory jobstore.

Add a markdown block immediately after the section title (whichever section discusses the in-memory choice):

```markdown
> **SUPERSEDED 2026-05-24** — see commits <D5_HASH> + <D2_HASH>.
> Decision revisited post-Phase-2 polish: persistent SQLAlchemyJobStore
> (D5) + DB-backed rate limit (D2) implemented because (a) Postgres was
> already a dependency, (b) the lift was small (~30 LOC + 4 tests),
> (c) restart-safety upside justified re-opening the locked decision.
> Original rationale preserved below for historical context.
```

If the spec file doesn't have a section that specifically mentions `_LAST_TRIGGER` or APScheduler jobstore, skip this step (the polish backlog update is sufficient documentation).

- [ ] **Step 5: Run full suite once more as final sanity check**

```bash
cd backend && uv run pytest -q
```

Expected: 191 PASS.

- [ ] **Step 6: Commit docs**

```bash
git add CLAUDE.md \
        docs/plans/2026-05-24-phase2-polish-backlog.md \
        docs/specs/2026-05-24-phase2-ingestion-design.md

git commit -m "$(cat <<'EOF'
docs: mark D2 + D5 as resolved by persistent state migration

- CLAUDE.md "Deuda conocida heredada" drops D2/D5, adds resolved note
- polish backlog D2 and D5 sections updated with RESOLVED + commit hashes
- Phase 2 spec D5/D6 marked as superseded with rationale

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Final verification before merge

- [ ] **Step 1: Re-run the full backend suite**

```bash
cd backend && uv run pytest -q
```

Expected: 191 PASS, 0 FAIL.

- [ ] **Step 2: Verify branch is clean and ready**

```bash
git status
git log --oneline main..HEAD
```

Expected: working tree clean. 4 commits on top of main (deps, D5, D2, docs).

- [ ] **Step 3: Manual smoke check that scheduler boots OK**

```bash
docker compose up -d --build backend db
docker compose logs backend 2>&1 | grep -E "(Registered 3 ingest jobs|apscheduler_jobs|ERROR)" | head -20
docker compose exec db psql -U postgres -d ibkr_control -c "\dt apscheduler_jobs"
```

Expected:
- Log contains `Registered 3 ingest jobs: flex_daily, trm_daily, cleanup_job_tracker`
- Log contains no ERROR lines related to scheduler/jobstore
- `\dt apscheduler_jobs` shows the table exists

If anything fails: the production wire-up has a bug not caught by tests. Investigate before merging.

- [ ] **Step 4: Stop the local stack**

```bash
docker compose down
```

- [ ] **Step 5: Merge to main**

This step requires the user's explicit OK before executing. Do NOT auto-merge.

```bash
# Only after user confirms:
git checkout main
git merge --ff-only chore/persistent-state-d5-d2
git branch -d chore/persistent-state-d5-d2
```

After merge, the local smoke test (separate task tracked outside this plan) can proceed against the now-cleaner main.

---

## Self-Review

**Spec coverage check:**
- D5 (persistent scheduler): Tasks 1-5 ✓
- D2 (DB-backed rate limit): Tasks 6-8 ✓
- misfire_grace_time=21600: Task 5 ✓
- APScheduler pinned to 3.11.*: Task 1 ✓
- psycopg v3 added: Task 1 ✓
- Atomic UPDATE for rate limit (no TOCTOU): Task 8 ✓
- Spec/CLAUDE.md/polish backlog doc updates: Task 9 ✓
- Manual production smoke (scheduler boots, table exists): Final verification ✓

**Placeholder scan:** No TBD/TODO/fill-in-later. All code blocks are complete. Migration filename has `<autogen-hash>` placeholder by necessity (Alembic decides the hash); the content of the migration is fully specified.

**Type/name consistency:**
- `create_scheduler()` defined in Task 3 step 3 → used in Task 4 step 4 ✓
- `database_url_sync` property defined Task 2 step 3 → used in Task 3 step 3 ✓
- `last_ingest_trigger_at` column declared Task 6 step 1 → referenced Task 8 step 1 ✓
- Test function names referenced in `pytest -v` commands match the defined names ✓
- `_persistence_noop` defined Task 4 step 1 → referenced Task 4 step 2 ✓

**Cross-references with spec/CLAUDE.md:**
- Override of "decisión locked" convention is acknowledged in Task 5 commit message + Task 9 docs ✓
- Migration test (`test_migrations_apply_cleanly_and_match_metadata`) covers Alembic correctness — Task 6 step 4 runs it ✓

Plan is complete and self-consistent.
