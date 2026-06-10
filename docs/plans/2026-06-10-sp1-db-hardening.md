# SP1 DB Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cerrar los 5 hallazgos de la auditoría de DB 2026-06-10 (spec D1–D7): índices FK, pool explícito, `close_datetime` documentado, `ondelete=RESTRICT` explícito, `created_at` first-seen en lotes/accruals.

**Architecture:** Cambios en modelos SQLAlchemy (fuente de verdad, protegidos por el drift test) + UNA migración Alembic autogenerada canónicamente dentro del container (lección Phase 2.9) con ops manuales para lo que autogenerate no detecta (ondelete de FKs, comments). Pool vía Settings con helper único `engine_kwargs()`.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x async, Alembic, Postgres 16, pytest + testcontainers (corren desde el HOST, no dentro del container backend).

**Spec:** `docs/specs/2026-06-10-sp1-db-hardening-design.md` (D1–D7 locked — leerlo antes de empezar).

**Branch:** `saas/sp1-db-hardening` (ya creado desde `main`). NO crear branches nuevos; verificar `git branch --show-current` antes de cada commit.

**Baseline:** 351 tests backend verdes, head Alembic = `7fdaf6528762`.

---

### Task 1: D2 — Pool de conexiones explícito vía Settings

**Files:**
- Modify: `backend/src/ibkr_control/config.py`
- Modify: `backend/src/ibkr_control/db/session.py`
- Modify: `backend/src/ibkr_control/scheduler/jobs.py:49,87`
- Modify: `backend/src/ibkr_control/scheduler/__init__.py`
- Modify: `.env.example`
- Test: `backend/tests/test_db_pool_config.py` (nuevo)

- [ ] **Step 1: Write the failing test**

Crear `backend/tests/test_db_pool_config.py`:

```python
"""D2 (sp1-db-hardening): pool de conexiones explícito vía Settings.

Lockea que el presupuesto de conexiones sea una decisión declarada en config,
no defaults implícitos de SQLAlchemy dispersos en 3 call sites.
"""

from ibkr_control.config import get_settings
from ibkr_control.db.session import engine_kwargs


def test_pool_settings_defaults():
    s = get_settings()
    assert s.db_pool_size == 10
    assert s.db_max_overflow == 20
    assert s.db_pool_recycle == 1800


def test_engine_kwargs_reflects_settings_and_forces_pre_ping():
    kw = engine_kwargs()
    assert kw == {
        "pool_size": 10,
        "max_overflow": 20,
        "pool_recycle": 1800,
        "pool_pre_ping": True,
    }


def test_pool_settings_env_override(monkeypatch):
    monkeypatch.setenv("DB_POOL_SIZE", "3")
    get_settings.cache_clear()
    try:
        assert get_settings().db_pool_size == 3
        assert engine_kwargs()["pool_size"] == 3
    finally:
        get_settings.cache_clear()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_db_pool_config.py -v`
Expected: FAIL con `ImportError: cannot import name 'engine_kwargs'`

- [ ] **Step 3: Write minimal implementation**

En `backend/src/ibkr_control/config.py`, agregar después de `ingest_trigger_cooldown_seconds` (línea 19):

```python
    # DB connection pool (D2 sp1-db-hardening). Presupuesto total de
    # conexiones por réplica = app (pool_size + max_overflow) + engines
    # efímeros de crons (lazy, se disponen al final de cada run) + jobstore
    # sync de APScheduler (psycopg, pool default chico). Tunables por env en
    # Coolify sin redeploy. pool_pre_ping NO es configurable: siempre True
    # (no hay caso legítimo para apagarlo).
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle: int = 1800
```

En `backend/src/ibkr_control/db/session.py`, reemplazar el archivo completo por:

```python
from collections.abc import AsyncGenerator
from functools import lru_cache
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ibkr_control.config import get_settings


def engine_kwargs() -> dict[str, Any]:
    """Pool config explícito (D2 sp1-db-hardening) — único punto de verdad.

    Consumido por get_engine() y por los engines efímeros de los crons
    (scheduler/jobs.py) para que el presupuesto de conexiones sea un solo
    concepto. pool_pre_ping=True fijo: detecta conexiones muertas post
    restart de Postgres en vez de fallar el primer request.
    """
    s = get_settings()
    return {
        "pool_size": s.db_pool_size,
        "max_overflow": s.db_max_overflow,
        "pool_recycle": s.db_pool_recycle,
        "pool_pre_ping": True,
    }


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(
        get_settings().database_url, echo=False, future=True, **engine_kwargs()
    )


@lru_cache
def get_session_maker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with get_session_maker()() as session:
        yield session
```

En `backend/src/ibkr_control/scheduler/jobs.py`, los DOS call sites (líneas 49 y 87):

```python
# línea 49 (en _run_flex_for_all_orgs) — antes:
#   engine = create_async_engine(settings.database_url, echo=False)
# después:
    engine = create_async_engine(settings.database_url, echo=False, **engine_kwargs())

# línea 87 (en _run_trm_global) — mismo cambio:
    engine = create_async_engine(settings.database_url, echo=False, **engine_kwargs())
```

Y agregar el import arriba del archivo (junto al import existente de session/sqlalchemy):

```python
from ibkr_control.db.session import engine_kwargs
```

En `backend/src/ibkr_control/scheduler/__init__.py`, reemplazar `create_scheduler` (el jobstore gana `pool_pre_ping` y el docstring deja de decir "auto-created by APScheduler" — la tabla la pre-crea la migración `7fdaf6528762` desde PR #7):

```python
def create_scheduler() -> AsyncIOScheduler:
    """Create the production scheduler with persistent SQLAlchemyJobStore.

    Jobs are stored in `apscheduler_jobs` (pre-created by Alembic migration
    7fdaf6528762 as owner — app_rls has no CREATE). Restart-safe: cron
    triggers re-register via register_jobs() with replace_existing=True, and
    missed runs within misfire_grace_time get caught up at boot.
    pool_pre_ping: el jobstore sync mantiene su conexión psycopg viva entre
    wake-ups del scheduler; sin pre-ping, un restart de Postgres rompe el
    siguiente wake-up.
    """
    settings = get_settings()
    jobstores = {
        "default": SQLAlchemyJobStore(
            url=settings.database_url_sync,
            tablename="apscheduler_jobs",
            engine_options={"pool_pre_ping": True},
        ),
    }
    return AsyncIOScheduler(jobstores=jobstores, timezone="UTC")
```

En `.env.example`, agregar al final de la sección de backend:

```bash
# DB pool (opcional — defaults: 10 / 20 / 1800). Tunables sin redeploy.
# DB_POOL_SIZE=10
# DB_MAX_OVERFLOW=20
# DB_POOL_RECYCLE=1800
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_db_pool_config.py -v`
Expected: 3 PASS

- [ ] **Step 5: Run la suite de scheduler + boot (regresión de los call sites tocados)**

Run: `cd backend && uv run pytest tests/test_scheduler_jobs.py tests/test_runtime_role_guard.py -q 2>/dev/null || uv run pytest -q -k "scheduler or guard"`
Expected: PASS (0 failures)

- [ ] **Step 6: Lint + commit**

```bash
cd backend && uv run ruff check . && uv run ruff format --check .
git add backend/src/ibkr_control/config.py backend/src/ibkr_control/db/session.py \
    backend/src/ibkr_control/scheduler/jobs.py backend/src/ibkr_control/scheduler/__init__.py \
    backend/tests/test_db_pool_config.py .env.example
git commit -m "feat(sp1-db-hardening): D2 — pool explícito vía Settings (pre_ping fijo, helper engine_kwargs único)"
```

---

### Task 2: D1+D3+D4+D5 — Cambios de modelos (deja el drift test en rojo a propósito)

**Files:**
- Modify: `backend/src/ibkr_control/db/models/flex_raw.py`
- Modify: `backend/src/ibkr_control/db/models/parties.py`
- Modify: `backend/src/ibkr_control/db/models/access_grants.py`

Este task NO tiene test nuevo: el "failing test" es el drift test existente
(`tests/test_migrations.py`), que DEBE quedar rojo al final del task (modelos ≠
migraciones) y se pone verde en Task 3. NO commitear este task solo — se
commitea junto con Task 3 (modelos + migración atómicos).

- [ ] **Step 1: `flex_raw.py` — índices `flex_import_id` (D1) en las 7 tablas de hechos**

Agregar `Index(None, "flex_import_id"),` al `__table_args__` de: `Trade`,
`ClosedLot`, `OpenPositionLot`, `Transfer`, `CashTransaction`,
`ChangeInDividendAccrual`, `OpenDividendAccrual`. Ejemplo exacto en `Trade`
(las otras 6 son la misma línea en su `__table_args__`, antes del dict de
comment):

```python
class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        CheckConstraint("open_close IS NULL OR open_close IN ('O', 'C')", name="open_close"),
        CheckConstraint("buy_sell IN ('BUY', 'SELL')", name="buy_sell"),
        Index(None, "account_id", "symbol"),
        Index(None, "trade_date"),
        Index(None, "organization_id"),
        Index(None, "flex_import_id"),
        {
            "comment": (
```

NO tocar `FlexImportAccount` (su PK compuesta ya cubre el prefijo `flex_import_id`).

- [ ] **Step 2: `flex_raw.py` — índices de linaje (D1)**

En `ClosedLot.__table_args__` agregar:

```python
        Index(None, "source_trade_id"),
```

En `Transfer.__table_args__` agregar las 4 líneas:

```python
        Index(None, "src_account_id"),
        Index(None, "dst_account_id"),
        Index(None, "src_counterparty_id"),
        Index(None, "dst_counterparty_id"),
```

- [ ] **Step 3: `flex_raw.py` — `ondelete="RESTRICT"` explícito (D4)**

Cambiar las FKs a `accounts`/`counterparties` en las tablas de hechos (hoy sin
`ondelete`, NO ACTION implícito). Lista exhaustiva — 10 columnas:

| Clase | Columna | Cambio |
|---|---|---|
| `Trade` | `account_id` | `ForeignKey("accounts.id", ondelete="RESTRICT")` |
| `ClosedLot` | `account_id` | `ForeignKey("accounts.id", ondelete="RESTRICT")` |
| `OpenPositionLot` | `account_id` | `ForeignKey("accounts.id", ondelete="RESTRICT")` |
| `Transfer` | `src_account_id` | `ForeignKey("accounts.id", ondelete="RESTRICT")` |
| `Transfer` | `dst_account_id` | `ForeignKey("accounts.id", ondelete="RESTRICT")` |
| `Transfer` | `src_counterparty_id` | `ForeignKey("counterparties.id", ondelete="RESTRICT")` |
| `Transfer` | `dst_counterparty_id` | `ForeignKey("counterparties.id", ondelete="RESTRICT")` |
| `CashTransaction` | `account_id` | `ForeignKey("accounts.id", ondelete="RESTRICT")` |
| `ChangeInDividendAccrual` | `account_id` | `ForeignKey("accounts.id", ondelete="RESTRICT")` |
| `OpenDividendAccrual` | `account_id` | `ForeignKey("accounts.id", ondelete="RESTRICT")` |

NO tocar: `closed_lots.source_trade_id` (linaje opcional, queda como está),
ningún `flex_import_id` (`SET NULL` correcto), ningún `organization_id`
(`CASCADE` correcto — borrar el org borra el tenant entero).

- [ ] **Step 4: `flex_raw.py` — comment en `close_datetime` (D3)**

```python
    # A3 amendment #3: per-execution timestamp discriminator (multiple <Lot>
    # rows can share the same transaction_id when a close trade closes
    # fractions of one open_lot across separate execution events).
    close_datetime: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        comment=(
            "Naive POR DISEÑO (D3 sp1-db-hardening): IBKR emite "
            "'YYYYMMDD;HHMMSS' sin timezone (exchange-local); timestamptz "
            "inventaría una zona. La regla 730d (Art. 300 ET) opera a "
            "granularidad de día sobre close_date."
        ),
    )
```

- [ ] **Step 5: `flex_raw.py` — `created_at` first-seen (D5) en las 4 tablas de lotes/accruals**

Agregar como ÚLTIMA columna de `ClosedLot`, `OpenPositionLot`,
`ChangeInDividendAccrual` y `OpenDividendAccrual` (idéntico patrón que
`accounts.created_at`):

```python
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
```

CRÍTICO (semántica first-seen): NO agregar `created_at` a ningún
`update_cols` del persister (`ingest/flex/persister.py`) ni a los dicts de
rows. El persister no se toca en este task — el server_default puebla en
INSERT y `ON CONFLICT DO UPDATE` solo toca las columnas listadas en
`update_cols`, así que first-seen sale gratis. Task 4 lo lockea con test.

- [ ] **Step 6: `parties.py` — índice `user_id` (D1)**

En `Party.__table_args__`:

```python
    __table_args__ = (
        Index(None, "organization_id"),
        Index(None, "user_id"),
```

- [ ] **Step 7: `access_grants.py` — 4 índices (D1)**

Agregar `Index` al import de sqlalchemy (línea 11) y los índices al
`__table_args__`:

```python
from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Index, String, text
```

```python
    __table_args__ = (
        CheckConstraint(
            "(grantee_organization_id IS NOT NULL) <> (grantee_user_id IS NOT NULL)",
            name="grantee_arc",
        ),
        CheckConstraint("role IN ('read_only')", name="role"),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="valid_range"),
        # D1 sp1-db-hardening: la policy RLS grant_visibility evalúa
        # grantor/grantee en CADA query a esta tabla, y SP2 la pone en el hot
        # path de autorización.
        Index(None, "grantor_party_id"),
        Index(None, "grantee_organization_id"),
        Index(None, "grantee_user_id"),
        Index(None, "organization_id"),
        {"comment": "Grant cross-org party-scoped. RLS especial (grantor-org OR grantee)."},
    )
```

- [ ] **Step 8: Verificar que el drift test queda ROJO (red de este task)**

Run: `cd backend && uv run pytest tests/test_migrations.py -v`
Expected: FAIL con "DRIFT:" listando los índices/columnas nuevos (add_index,
add_column created_at). Si pasa en verde, los cambios de modelo no se
aplicaron — revisar Steps 1–7.

---

### Task 3: D6 — Migración canónica (autogenerate dentro del container + ops manuales)

**Files:**
- Create: `backend/alembic/versions/<hash>_sp1_db_hardening.py` (autogenerado)
- Test: `backend/tests/test_migrations.py` (existente — pasa de rojo a verde)

- [ ] **Step 1: Levantar el dev stack y poner la DB en head**

```bash
make dev
docker compose -f compose.yaml -f compose.dev.yaml exec backend uv run alembic upgrade head
```

Expected: sin errores; head = `7fdaf6528762`. NOTA: las migraciones dentro del
dev stack corren con el servicio `migrate`/owner según el compose post-PR-#7;
si `exec backend` falla por permisos DDL (app_rls no puede CREATE), usar el
servicio migrate: `docker compose -f compose.yaml -f compose.dev.yaml run --rm migrate uv run alembic upgrade head` y lo mismo para el `revision --autogenerate` de abajo.

- [ ] **Step 2: Autogenerate canónico (lección Phase 2.9 — el camino valida boot/drift/formato)**

```bash
docker compose -f compose.yaml -f compose.dev.yaml exec backend \
  uv run alembic revision --autogenerate -m "sp1 db hardening: fk indexes, restrict, created_at, close_datetime comment"
```

Si el archivo no aparece en `backend/alembic/versions/` del host (bind mount
no cubre alembic/), copiarlo:

```bash
docker compose -f compose.yaml -f compose.dev.yaml cp \
  backend:/app/alembic/versions/$(docker compose -f compose.yaml -f compose.dev.yaml exec backend ls /app/alembic/versions/ | grep sp1_db_hardening) \
  backend/alembic/versions/
```

- [ ] **Step 3: Auditar y completar la migración generada**

Revisar el archivo generado contra esta checklist:

1. **Remover el falso positivo `apscheduler_jobs`** si autogenerate emitió
   `drop_table("apscheduler_jobs")` o similar (tabla runtime fuera de
   `Base.metadata` — mismo falso positivo documentado en Phase 2.9).
2. **Índices (D1):** deben estar los 17 `create_index` con nombres por
   convención: `ix_trades_flex_import_id`, `ix_closed_lots_flex_import_id`,
   `ix_closed_lots_source_trade_id`, `ix_open_position_lots_flex_import_id`,
   `ix_transfers_flex_import_id`, `ix_transfers_src_account_id`,
   `ix_transfers_dst_account_id`, `ix_transfers_src_counterparty_id`,
   `ix_transfers_dst_counterparty_id`, `ix_cash_transactions_flex_import_id`,
   `ix_change_in_dividend_accruals_flex_import_id`,
   `ix_open_dividend_accruals_flex_import_id`, `ix_parties_user_id`,
   `ix_access_grants_grantor_party_id`,
   `ix_access_grants_grantee_organization_id`,
   `ix_access_grants_grantee_user_id`, `ix_access_grants_organization_id`.
3. **`created_at` (D5):** 4 `add_column` con
   `sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False)`
   en closed_lots, open_position_lots, change_in_dividend_accruals,
   open_dividend_accruals. (Postgres 11+ hace fast-default: no reescribe la
   tabla; filas dev existentes quedan selladas con el timestamp de la
   migración — decisión usuario 2026-06-10, NO wipe.)
4. **D4 — agregar A MANO (autogenerate NO detecta cambios de ondelete).**
   Bloque al final de `upgrade()`:

```python
    # D4: ondelete RESTRICT explícito (antes NO ACTION implícito — mismo
    # comportamiento efectivo, intención declarada). Autogenerate no diffea
    # ondelete; ops manuales. Nombres por naming convention fk_<t>_<col>_<ref>.
    _RESTRICT_FKS = [
        ("trades", "account_id", "accounts"),
        ("closed_lots", "account_id", "accounts"),
        ("open_position_lots", "account_id", "accounts"),
        ("transfers", "src_account_id", "accounts"),
        ("transfers", "dst_account_id", "accounts"),
        ("transfers", "src_counterparty_id", "counterparties"),
        ("transfers", "dst_counterparty_id", "counterparties"),
        ("cash_transactions", "account_id", "accounts"),
        ("change_in_dividend_accruals", "account_id", "accounts"),
        ("open_dividend_accruals", "account_id", "accounts"),
    ]
    for table, col, ref in _RESTRICT_FKS:
        name = f"fk_{table}_{col}_{ref}"
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(name, table, ref, [col], ["id"], ondelete="RESTRICT")
```

   (Definir `_RESTRICT_FKS` a nivel módulo para reusarlo en `downgrade()`.)
5. **D3 — comment (si autogenerate no lo emitió):**

```python
    op.alter_column(
        "closed_lots",
        "close_datetime",
        existing_type=sa.DateTime(),
        existing_nullable=False,
        comment=(
            "Naive POR DISEÑO (D3 sp1-db-hardening): IBKR emite "
            "'YYYYMMDD;HHMMSS' sin timezone (exchange-local); timestamptz "
            "inventaría una zona. La regla 730d (Art. 300 ET) opera a "
            "granularidad de día sobre close_date."
        ),
    )
```

6. **`downgrade()` completo y simétrico:** drop de los 17 índices, drop de las
   4 columnas `created_at`, re-crear las 10 FKs SIN ondelete
   (`op.create_foreign_key(name, table, ref, [col], ["id"])`), y
   `op.alter_column(..., comment=None)`. Verificar que `down_revision = "7fdaf6528762"`.
7. **Docstring de la migración:** anotar que es 100% aditiva, cero data loss,
   y que las filas existentes de lotes/accruals reciben `created_at` =
   timestamp de migración (decisión spec D5).

- [ ] **Step 4: Verificar upgrade + reversibilidad dentro del container**

```bash
docker compose -f compose.yaml -f compose.dev.yaml exec backend uv run alembic upgrade head
docker compose -f compose.yaml -f compose.dev.yaml exec backend uv run alembic downgrade -1
docker compose -f compose.yaml -f compose.dev.yaml exec backend uv run alembic upgrade head
```

Expected: las 3 corren sin error (usar el servicio `migrate` si backend no
tiene permisos DDL, igual que Step 1).

- [ ] **Step 5: Verificar índices y FKs en la DB real**

```bash
docker compose -f compose.yaml -f compose.dev.yaml exec postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\d trades" -c "\d access_grants" | grep -E "ix_|RESTRICT"
```

Expected: `ix_trades_flex_import_id` listado; FKs de `account_id` con `ON DELETE RESTRICT`; los 4 `ix_access_grants_*`. (Los nombres de env vars salen del `.env` del repo.)

- [ ] **Step 6: Drift test verde (green de Task 2+3)**

Run: `cd backend && uv run pytest tests/test_migrations.py -v`
Expected: PASS — `compare_metadata` sin diffs.

- [ ] **Step 7: Lint + commit atómico (modelos + migración)**

```bash
cd backend && uv run ruff check . && uv run ruff format --check .
git add backend/src/ibkr_control/db/models/ backend/alembic/versions/
git commit -m "feat(sp1-db-hardening): D1+D3+D4+D5 — 17 índices FK, RESTRICT explícito, created_at first-seen, close_datetime naive documentado

Migración única autogenerada canónicamente (container), 100% aditiva,
downgrade reversible. D4 a mano (autogenerate no diffea ondelete)."
```

---

### Task 4: Tests de comportamiento (lockean D4 + D5)

**Files:**
- Test: `backend/tests/test_db_hardening.py` (nuevo)

- [ ] **Step 1: Write the failing tests**

Crear `backend/tests/test_db_hardening.py`. Usa las fixtures existentes de
conftest (`db_session` conecta como owner — bypassea RLS, patrón estándar de
las fixtures de modelo; `sample_org`/`sample_account` ya existen):

```python
"""Behavior locks de sp1-db-hardening.

D4: borrar una cuenta con ledger colgando debe fallar (RESTRICT — antes era
NO ACTION implícito; mismo efecto, ahora declarado y lockeado).
D5: created_at es first-seen — el ON CONFLICT DO UPDATE de snapshots NUNCA
lo pisa (no está en update_cols del persister).
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from ibkr_control.db.models.accounts import Account
from ibkr_control.db.models.flex_raw import OpenPositionLot, Trade
from ibkr_control.ingest.flex._upsert_helpers import _upsert_snapshot

# Mismas listas que usa el persister para OpenPositionLot (persister.py
# ~línea 466): si el persister algún día agrega created_at a update_cols,
# este test deja de representar la realidad — actualizar AMBOS o ninguno.
_OPL_KEY = ["account_id", "symbol", "open_date", "snapshot_date", "originating_transaction_id"]
_OPL_UPDATE = ["flex_import_id", "asset_class", "qty", "cost_basis_usd", "mark_price_usd", "mark_value_usd"]


async def test_delete_account_with_trades_is_restricted(db_session, sample_org, sample_account):
    """D4: la FK trades.account_id -> accounts es RESTRICT."""
    db_session.add(
        Trade(
            organization_id=sample_org.id,
            transaction_id="T-RESTRICT-1",
            account_id=sample_account.id,
            symbol="VOO",
            asset_class="STK",
            trade_date=date(2026, 1, 5),
            qty=Decimal("1"),
            price_usd=Decimal("500"),
            proceeds_usd=Decimal("-500"),
            commission_usd=Decimal("-1"),
            buy_sell="BUY",
        )
    )
    await db_session.flush()

    with pytest.raises(IntegrityError):
        await db_session.execute(delete(Account).where(Account.id == sample_account.id))
    await db_session.rollback()


async def test_created_at_populated_and_first_seen_on_reupsert(
    db_session, sample_org, sample_account
):
    """D5: created_at se puebla en INSERT y sobrevive re-upserts snapshot."""
    base_row = {
        "organization_id": sample_org.id,
        "account_id": sample_account.id,
        "symbol": "VOO",
        "asset_class": "STK",
        "open_date": date(2026, 1, 5),
        "snapshot_date": date(2026, 6, 1),
        "originating_transaction_id": "OTID-1",
        "qty": Decimal("10"),
        "cost_basis_usd": Decimal("5000.0000"),
        "flex_import_id": None,
        "mark_price_usd": None,
        "mark_value_usd": None,
    }
    await _upsert_snapshot(
        db_session, OpenPositionLot.__table__, [dict(base_row)], _OPL_KEY, _OPL_UPDATE
    )
    row = (await db_session.execute(select(OpenPositionLot))).scalar_one()
    assert row.created_at is not None  # server_default pobló en INSERT

    # Sentinel: NOW() dentro de una misma transacción es constante, así que
    # para probar que el DO UPDATE no pisa created_at lo movemos a un valor
    # imposible de reproducir y re-upserteamos.
    sentinel = datetime(2020, 1, 1, tzinfo=UTC)
    await db_session.execute(update(OpenPositionLot).values(created_at=sentinel))

    changed = dict(base_row)
    changed["qty"] = Decimal("12")
    await _upsert_snapshot(
        db_session, OpenPositionLot.__table__, [changed], _OPL_KEY, _OPL_UPDATE
    )
    db_session.expire_all()
    row2 = (await db_session.execute(select(OpenPositionLot))).scalar_one()
    assert row2.qty == Decimal("12")  # el snapshot SÍ se actualizó
    assert row2.created_at == sentinel  # created_at NO se pisó (first-seen)
```

- [ ] **Step 2: Run tests to verify they pass (la implementación ya existe — Task 2/3)**

Run: `cd backend && uv run pytest tests/test_db_hardening.py -v`
Expected: 2 PASS. Si `test_delete_account_with_trades_is_restricted` falla con
DELETE exitoso → la migración D4 no se aplicó en la DB de test (revisar Task 3).
Si `created_at` es None → la columna no existe (revisar Task 3 Step 3.3).

NOTA TDD: estos tests nacen "verdes" porque lockean schema ya migrado en Task
3 — son regression locks, no drivers. Para validar que el test RESTRICT
realmente muerde, correrlo una vez contra `main` (git stash / checkout) NO es
necesario: basta verificar que el assert del sentinel falla si se agrega
`"created_at"` temporalmente a `_OPL_UPDATE` (un edit de 1 línea, revertirlo).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_db_hardening.py
git commit -m "test(sp1-db-hardening): lockear D4 RESTRICT + D5 created_at first-seen"
```

---

### Task 5: Verificación final + docs

**Files:**
- Modify: `CLAUDE.md` (sección "Estado del programa SaaS")

- [ ] **Step 1: Suite completa backend (host, testcontainers)**

Run: `cd backend && uv run pytest -q`
Expected: **356 passed** (351 baseline + 3 pool + 2 hardening), 0 failures.

- [ ] **Step 2: Lint en 0**

Run: `cd backend && uv run ruff check . && uv run ruff format --check .`
Expected: "All checks passed!" / sin diffs.

- [ ] **Step 3: Boot smoke real (el boot guard valida el rol app_rls)**

```bash
make dev
docker compose -f compose.yaml -f compose.dev.yaml logs backend --tail 30
```

Expected: uvicorn arranca sin RuntimeError del guard; el lifespan completa.

- [ ] **Step 4: Evidencia del costo del cleanup (criterio de aceptación #3 del spec)**

```bash
docker compose -f compose.yaml -f compose.dev.yaml exec postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT indexname FROM pg_indexes WHERE indexname LIKE 'ix_%flex_import_id' ORDER BY 1;"
```

Expected: 7 filas (uno por tabla de hechos).

- [ ] **Step 5: Actualizar CLAUDE.md**

En la sección "Estado del programa SaaS", agregar bullet después del de
SP1-rls-runtime-wiring (ajustar el conteo de tests al real del Step 1):

```markdown
- **SP1-db-hardening (branch `saas/sp1-db-hardening`):** cerró los 5 hallazgos de la auditoría de DB 2026-06-10 (no mapeados a ningún SP — deuda sin dueño): 17 índices FK (flex_import_id ×7 — el cleanup latest-1 hacía seq scan de las 8 tablas de hechos en cada ingest —, transfers ×4, access_grants ×4 pre-SP2, parties.user_id, source_trade_id), pool explícito vía Settings (`engine_kwargs()` único, pre_ping fijo), `close_datetime` naive POR DISEÑO documentado (IBKR no emite timezone), `ondelete=RESTRICT` explícito en FKs de hechos (antes NO ACTION implícito), `created_at` first-seen en lotes/accruals. UNA migración autogenerada canónica, 100% aditiva, reversible. Tests 351 → 356. Spec: `docs/specs/2026-06-10-sp1-db-hardening-design.md` · Plan: `docs/plans/2026-06-10-sp1-db-hardening.md`. Falsos positivos descartados en la auditoría (NO "arreglar"): account_id ya cubierto por índices compuestos `(account_id, symbol)`; PKs bigint correctos vs UUID; CHECK-enums correctos vs ENUM nativo.
```

Y en la línea "SIGUIENTE — SP2", actualizar la dependencia: "Arrancar tras el
merge de SP1 + follow-ups (hardening + rls-runtime-wiring + db-hardening)".

- [ ] **Step 6: Commit docs**

```bash
git add CLAUDE.md
git commit -m "docs(claude-md): registrar SP1-db-hardening (auditoría DB 2026-06-10 cerrada)"
```

- [ ] **Step 7: Push + PR**

```bash
git push -u origin saas/sp1-db-hardening
gh pr create --title "SP1 follow-up: DB hardening (FK indexes, pool, invariantes explícitos)" --body "$(cat <<'EOF'
Cierra los 5 hallazgos de la auditoría de DB 2026-06-10 (spec D1-D7,
docs/specs/2026-06-10-sp1-db-hardening-design.md):

- **D1 — 17 índices FK**: flex_import_id en las 7 tablas de hechos (el cleanup
  latest-1 disparaba seq scans vía ON DELETE SET NULL en cada ingest),
  transfers (4 lados), access_grants (hot path RLS de SP2), parties.user_id,
  closed_lots.source_trade_id. Declarados en los modelos → protegidos por el
  drift test.
- **D2 — Pool explícito vía Settings**: db_pool_size/max_overflow/recycle
  tunables por env; pool_pre_ping fijo; helper engine_kwargs() único para app
  + crons + jobstore.
- **D3 — close_datetime naive POR DISEÑO**: IBKR emite 'YYYYMMDD;HHMMSS' sin
  timezone; documentado con comment= en columna y migración.
- **D4 — ondelete=RESTRICT explícito** en FKs de hechos → accounts/counterparties
  (antes NO ACTION implícito; mismo efecto, intención declarada + test).
- **D5 — created_at first-seen** en closed_lots/open_position_lots/accruals
  (server_default NOW(), fuera de update_cols; test con sentinel).

UNA migración autogenerada canónicamente dentro del container, 100% aditiva,
cero data loss, downgrade reversible. Tests 351 → 356.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR creado; CI (`backend` + `frontend`) debe quedar verde antes del merge.
