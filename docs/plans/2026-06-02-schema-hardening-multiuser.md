# Schema Hardening + Multi-User RBAC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adoptar una `naming_convention` determinística, colapsar 18 migraciones en un baseline pristino, endurecer el drift test, hacer explícita la invariante de identidad compartida, y construir la primitiva de autorización multi-user (grants + resolver + dependency).

**Architecture:** El modelo de hechos ya es account-scoped + `participations` (no se toca). Se agrega una `naming_convention` en `Base.metadata`, se regenera todo el schema como un único baseline Alembic, y se construye un paquete `authz/` (separado de `auth/` que es authentication) con el resolver `visible_account_ids` y la dependency `require_account_scope` que Phase 3 consumirá. Una tabla `data_access_grants` modela la delegación de lectura del contador.

**Tech Stack:** SQLAlchemy 2.x async + Alembic + FastAPI + fastapi-users + Postgres 16 + pytest + testcontainers.

**Spec:** `docs/specs/2026-06-02-schema-hardening-multiuser-design.md` (H1-H5, G1-G6).

**Precondición confirmada por el usuario:** datos dev descartables, sin prod desplegado → el squash es seguro y es la última ventana limpia para hacerlo.

---

## File Structure

**Modificados (schema/modelos):**
- `backend/src/ibkr_control/db/base.py` — agregar `naming_convention` a `Base.metadata` (H1).
- `backend/src/ibkr_control/db/models/flex_raw.py` — ajustar names de CHECK/UNIQUE/Index + comments (H1, H4).
- `backend/src/ibkr_control/db/models/ingest_log.py` — ajustar names de CHECK/Index (H1).
- `backend/src/ibkr_control/db/models/participations.py` — ajustar names de CHECK (H1).
- `backend/src/ibkr_control/db/models/trm.py` — ajustar name de Index (H1).
- `backend/src/ibkr_control/db/models/accounts.py` — comment (H4).
- `backend/src/ibkr_control/db/models/counterparties.py` — comment (H4).

**Creados (H5 + grant model):**
- `backend/src/ibkr_control/db/models/grants.py` — modelo `DataAccessGrant` (G1).
- `backend/src/ibkr_control/authz/__init__.py` — paquete nuevo (authorization, distinto de auth/).
- `backend/src/ibkr_control/authz/scope.py` — resolver `visible_account_ids` + helpers (G2/G3).
- `backend/src/ibkr_control/authz/dependencies.py` — `require_account_scope` (G6).
- `backend/src/ibkr_control/api/grants.py` — CRUD router (G6).

**Modificados (wiring):**
- `backend/src/ibkr_control/db/__init__.py` — registrar `DataAccessGrant`.
- `backend/src/ibkr_control/api/_schemas.py` — `GrantCreate`, `GrantRead`.
- `backend/src/ibkr_control/main.py` — registrar `grants_router`.

**Migraciones:**
- Borrar las 18 en `backend/alembic/versions/` (excepto `.gitkeep`).
- Crear 1 baseline nuevo (autogenerado + revisado).

**Tests:**
- `backend/tests/test_naming_convention.py` — nombres finales (H1).
- `backend/tests/test_table_comments.py` — comments (H4).
- `backend/tests/test_grants_model.py` — CHECKs del modelo (G1).
- `backend/tests/test_migrations.py` — endurecer (H3).
- `backend/tests/test_authz_scope.py` — resolver (G2/G3).
- `backend/tests/test_authz_dependency.py` — dependency (G6).
- `backend/tests/test_grants_api.py` — CRUD + isolation (G6).

**Docs:**
- `CLAUDE.md` — corregir misdiagnóstico Phase 2.6 + retrospectiva Phase 2.8 + tabla estado.

---

## Task 1: `naming_convention` + ajuste de nombres explícitos (H1)

**Files:**
- Modify: `backend/src/ibkr_control/db/base.py`
- Modify: `backend/src/ibkr_control/db/models/flex_raw.py`
- Modify: `backend/src/ibkr_control/db/models/ingest_log.py`
- Modify: `backend/src/ibkr_control/db/models/participations.py`
- Modify: `backend/src/ibkr_control/db/models/trm.py`
- Test: `backend/tests/test_naming_convention.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_naming_convention.py
"""H1: la naming_convention produce nombres finales determinísticos, sin
doble-prefijo y sin truncado a 63 chars en natural keys compuestas."""

import ibkr_control.db  # noqa: F401 — carga modelos en Base.metadata
from ibkr_control.db.base import Base


def _names(table_name):
    t = Base.metadata.tables[table_name]
    out = set()
    for c in t.constraints:
        if c.name:
            out.add(c.name)
    for ix in t.indexes:
        out.add(ix.name)
    return out


def test_convention_is_registered():
    assert Base.metadata.naming_convention["ck"] == "ck_%(table_name)s_%(constraint_name)s"
    assert Base.metadata.naming_convention["fk"].startswith("fk_")


def test_check_names_no_double_prefix():
    names = _names("transfers")
    assert "ck_transfers_src_arc" in names
    assert "ck_transfers_dst_arc" in names
    assert "ck_transfers_direction" in names
    # El bug que esto previene: doble prefijo
    assert "ck_transfers_ck_transfers_src_arc" not in names


def test_natural_keys_explicit_uq_prefix_no_truncation():
    assert "uq_closed_lots_natural_key" in _names("closed_lots")
    assert "uq_open_position_lots_natural_key" in _names("open_position_lots")
    long_name = "uq_change_in_dividend_accruals_natural_key"
    assert long_name in _names("change_in_dividend_accruals")
    assert len(long_name) <= 63
    assert "uq_open_dividend_accruals_natural_key" in _names("open_dividend_accruals")


def test_fk_follows_convention():
    names = _names("trades")
    assert "fk_trades_account_id_accounts" in names


def test_single_col_unique_follows_convention():
    # accounts.ibkr_account_id es unique=True (sin name) -> convención
    assert "uq_accounts_ibkr_account_id" in _names("accounts")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_naming_convention.py -v`
Expected: FAIL (los nombres actuales son `ck_transfers_src_arc` por name explícito completo, pero `fk_trades_account_id_accounts` y `uq_accounts_ibkr_account_id` NO existen aún — son Postgres-auto `trades_account_id_fkey` / `accounts_ibkr_account_id_key`).

- [ ] **Step 3: Add naming_convention to Base**

```python
# backend/src/ibkr_control/db/base.py
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
```

- [ ] **Step 4: Strip `ck_<table>_` prefix from CHECK names en flex_raw.py**

En `backend/src/ibkr_control/db/models/flex_raw.py`, cambiar SOLO los `name=` de los CHECK (la convención re-agrega el prefijo). Y poner prefijo `uq_` a las natural keys:

- `name="ck_flex_imports_source"` → `name="source"`
- `name="ck_flex_imports_year_status"` → `name="year_status"`
- `name="ck_flex_imports_status"` → `name="status"`
- `UniqueConstraint(... name="flex_imports_user_xml_hash_key")` → quitar `name=` (convención genera `uq_flex_imports_user_id_xml_hash`)
- `name="ck_trades_open_close"` → `name="open_close"`
- `name="ck_trades_buy_sell"` → `name="buy_sell"`
- `name="closed_lots_natural_key"` → `name="uq_closed_lots_natural_key"`
- `name="open_position_lots_natural_key"` → `name="uq_open_position_lots_natural_key"`
- `name="change_in_dividend_accruals_natural_key"` → `name="uq_change_in_dividend_accruals_natural_key"`
- `name="open_dividend_accruals_natural_key"` → `name="uq_open_dividend_accruals_natural_key"`
- `name="ck_transfers_direction"` → `name="direction"`
- `name="ck_transfers_src_arc"` → `name="src_arc"`
- `name="ck_transfers_dst_arc"` → `name="dst_arc"`

Quitar el `name=` de TODOS los `Index(...)` de columnas planas para que la convención los genere (`trades_account_symbol_idx`, `trades_trade_date_idx`, `closed_lots_account_symbol_idx`, `open_position_lots_account_symbol_idx`, `cash_transactions_date_idx`, los `_report_date_idx` y `_account_symbol_idx` de ambos accruals). Ejemplo:

```python
# Antes:
Index("trades_account_symbol_idx", "account_id", "symbol"),
Index("trades_trade_date_idx", "trade_date"),
# Después:
Index(None, "account_id", "symbol"),
Index(None, "trade_date"),
```

- [ ] **Step 5: Ajustar ingest_log.py, participations.py, trm.py**

`ingest_log.py` — CHECKs (quitar sufijo `_check`, dejar token) + index con expresión (mantener name explícito alineado):
```python
CheckConstraint(f"job_kind IN {_JOB_KIND_VALUES}", name="job_kind"),
CheckConstraint(f"status IN {_STATUS_VALUES}", name="status"),
CheckConstraint(f"trigger IN {_TRIGGER_VALUES}", name="trigger"),
Index("ix_ingest_log_user_id_started_at", "user_id", text("started_at DESC")),
```
(El index usa `text("started_at DESC")` — la convención no puede derivar de una expresión, así que se mantiene `name=` explícito alineado al prefijo `ix_`.)

`participations.py` — CHECKs:
```python
CheckConstraint("pct >= 0 AND pct <= 1", name="pct_range"),
CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="valid_range"),
```

`trm.py` — quitar name del Index de columna:
```python
Index(None, "date"),
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_naming_convention.py -v`
Expected: PASS (5 tests).

- [ ] **Step 7: Commit**

```bash
git add backend/src/ibkr_control/db/base.py backend/src/ibkr_control/db/models/ backend/tests/test_naming_convention.py
git commit -m "feat(db): add naming_convention to Base.metadata (H1)"
```

---

## Task 2: Comments de identidad compartida (H4)

**Files:**
- Modify: `backend/src/ibkr_control/db/models/accounts.py`
- Modify: `backend/src/ibkr_control/db/models/counterparties.py`
- Modify: `backend/src/ibkr_control/db/models/flex_raw.py`
- Test: `backend/tests/test_table_comments.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_table_comments.py
"""H4: la invariante de identidad compartida queda explícita en los comments
del catálogo (visible con \\d+ y en Base.metadata)."""

import ibkr_control.db  # noqa: F401
from ibkr_control.db.base import Base


def _comment(table_name):
    return Base.metadata.tables[table_name].comment


def test_accounts_comment_marks_shared_identity():
    c = _comment("accounts")
    assert c is not None
    assert "COMPARTIDA" in c
    assert "participations" in c


def test_counterparties_comment_marks_shared_identity():
    assert "compartida" in (_comment("counterparties") or "").lower()


def test_fact_tables_comment_account_scoped():
    for t in ("trades", "closed_lots", "transfers"):
        c = _comment(t)
        assert c is not None and "account-scoped" in c.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_table_comments.py -v`
Expected: FAIL (comments son `None`).

- [ ] **Step 3: Add comments**

`accounts.py` — agregar `__table_args__`:
```python
class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = {
        "comment": (
            "Identidad COMPARTIDA. Una fila por cuenta IBKR; sin user_id a "
            "propósito — la propiedad se modela en participations (la conjunta "
            "es 50/50). ibkr_account_id UNIQUE global es correcto."
        )
    }
```

`counterparties.py`:
```python
class Counterparty(Base):
    __tablename__ = "counterparties"
    __table_args__ = {
        "comment": "Identidad compartida externa (espeja accounts). Sin user_id."
    }
```

`flex_raw.py` — agregar el dict de comment como ÚLTIMO elemento del tuple `__table_args__` de cada tabla de hechos. Ejemplo para `Trade`:
```python
class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        CheckConstraint("open_close IS NULL OR open_close IN ('O', 'C')", name="open_close"),
        CheckConstraint("buy_sell IN ('BUY', 'SELL')", name="buy_sell"),
        Index(None, "account_id", "symbol"),
        Index(None, "trade_date"),
        {"comment": (
            "Account-scoped. Visibilidad vía participations; sin user_id. "
            "transaction_id UNIQUE global correcto — un hecho pertenece a la "
            "cuenta, no al usuario."
        )},
    )
```
Aplicar el MISMO dict de comment (último elemento del tuple) a: `Trade`, `ClosedLot`, `OpenPositionLot`, `Transfer`, `CashTransaction`, `ChangeInDividendAccrual`, `OpenDividendAccrual`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_table_comments.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/ibkr_control/db/models/ backend/tests/test_table_comments.py
git commit -m "feat(db): explicit shared-identity invariant via table comments (H4)"
```

---

## Task 3: Modelo `DataAccessGrant` (G1)

**Files:**
- Create: `backend/src/ibkr_control/db/models/grants.py`
- Modify: `backend/src/ibkr_control/db/__init__.py`
- Test: `backend/tests/test_grants_model.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_grants_model.py
"""G1: data_access_grants — CHECKs de no-self-grant, rango válido, rol."""

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from ibkr_control.db.models.grants import DataAccessGrant


async def _two_users(db_session):
    from ibkr_control.auth.models import User

    a = User(email="grantor@t.com", hashed_password="x", is_active=True, name="Grantor")
    b = User(email="grantee@t.com", hashed_password="x", is_active=True, name="Grantee")
    db_session.add_all([a, b])
    await db_session.commit()
    await db_session.refresh(a)
    await db_session.refresh(b)
    return a, b


async def test_valid_grant_persists(db_session):
    a, b = await _two_users(db_session)
    g = DataAccessGrant(
        grantor_user_id=a.id, grantee_user_id=b.id, valid_from=date(2026, 1, 1)
    )
    db_session.add(g)
    await db_session.commit()
    assert g.role == "read_only"


async def test_self_grant_rejected(db_session):
    a, _ = await _two_users(db_session)
    db_session.add(
        DataAccessGrant(grantor_user_id=a.id, grantee_user_id=a.id, valid_from=date(2026, 1, 1))
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_invalid_range_rejected(db_session):
    a, b = await _two_users(db_session)
    db_session.add(
        DataAccessGrant(
            grantor_user_id=a.id,
            grantee_user_id=b.id,
            valid_from=date(2026, 6, 1),
            valid_to=date(2026, 1, 1),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_grants_model.py -v`
Expected: FAIL (`ModuleNotFoundError: ibkr_control.db.models.grants`).

- [ ] **Step 3: Create the model**

```python
# backend/src/ibkr_control/db/models/grants.py
"""Delegación de lectura: grantor permite a grantee leer SUS datos (vía las
participations del grantor). Read-only. Separada de participations: poder-leer
≠ poseer (mezclarlas forzaría un pct nullable sin sentido). El contador es el
caso de uso: lee la declaración del owner sin tener participación."""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    PrimaryKeyConstraint,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class DataAccessGrant(Base):
    __tablename__ = "data_access_grants"
    __table_args__ = (
        PrimaryKeyConstraint("grantor_user_id", "grantee_user_id", "valid_from"),
        CheckConstraint("role IN ('read_only')", name="role"),
        CheckConstraint("grantor_user_id <> grantee_user_id", name="no_self"),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="valid_range"),
        {
            "comment": (
                "Delegación de lectura read-only: grantor habilita a grantee a "
                "leer sus datos vía las participations del grantor. Separada de "
                "participations: poder-leer no es poseer."
            )
        },
    )

    grantor_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    grantee_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'read_only'"))
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
```

- [ ] **Step 4: Register in db/__init__.py**

En `backend/src/ibkr_control/db/__init__.py`, agregar el import + el `__all__`:
```python
from ibkr_control.db.models.grants import DataAccessGrant  # noqa: F401
```
Y `"DataAccessGrant",` a la lista `__all__`.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_grants_model.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/src/ibkr_control/db/models/grants.py backend/src/ibkr_control/db/__init__.py backend/tests/test_grants_model.py
git commit -m "feat(db): DataAccessGrant model (G1)"
```

---

## Task 4: Endurecer el drift test (H3)

**Files:**
- Modify: `backend/tests/test_migrations.py`

> Este test endurecido FALLARÁ contra las 18 migraciones viejas (que no tienen la convención, el grant table, ni los comments). Eso es el rojo de TDD. Task 5 (squash) lo pone en verde.

- [ ] **Step 1: Reescribir el test con compare_metadata**

Reemplazar el cuerpo de `test_migrations_apply_cleanly_and_match_metadata` (desde `command.upgrade(cfg, "head")` en adelante) por una comparación estructural completa:

```python
    command.upgrade(cfg, "head")

    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    engine = create_engine(sync_url)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn,
            opts={
                "compare_type": True,
                "compare_server_default": True,
                "target_metadata": Base.metadata,
            },
        )
        diffs = compare_metadata(ctx, Base.metadata)
    engine.dispose()

    # Filtrar diffs de tablas runtime que NO viven en migraciones (apscheduler crea
    # apscheduler_jobs al boot; alembic_version es interno).
    def _is_ignorable(diff):
        flat = diff if isinstance(diff, tuple) else (diff,)
        text = repr(flat)
        return "apscheduler_jobs" in text or "alembic_version" in text

    real = [d for d in diffs if not _is_ignorable(d)]
    assert not real, (
        "DRIFT entre migraciones y Base.metadata (columnas/tipos/índices/uniques/"
        f"FKs/server_defaults):\n" + "\n".join(repr(d) for d in real)
    )
```

> **Nota documentada:** `compare_metadata` NO compara CHECK constraints ni table comments de forma confiable (limitación conocida de Alembic). Esos quedan cubiertos por `test_naming_convention.py` (nombres de CHECK en metadata), `test_table_comments.py` (comments en metadata) y los tests de comportamiento que ejercitan los CHECK (ej. `test_grants_model.py`). El drift test cubre columnas, tipos, nullable, índices, unique constraints, FKs y server_defaults.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_migrations.py -v`
Expected: FAIL con un diff largo (las migraciones viejas no tienen `data_access_grants`, ni los nombres de convención, etc.).

- [ ] **Step 3: Commit (test rojo, intencional)**

```bash
git add backend/tests/test_migrations.py
git commit -m "test(db): harden drift test with compare_metadata (H3) [red until squash]"
```

---

## Task 5: Squash a baseline pristino (H2)

**Files:**
- Delete: `backend/alembic/versions/*.py` (las 18, preservar `.gitkeep`)
- Create: `backend/alembic/versions/<hash>_baseline_schema.py` (autogenerado)

- [ ] **Step 1: Borrar las 18 migraciones**

```bash
cd backend
find alembic/versions -name '*.py' ! -name '__init__.py' -delete
ls alembic/versions  # debe quedar solo .gitkeep (y __pycache__)
rm -rf alembic/versions/__pycache__
```

- [ ] **Step 2: Generar el baseline contra una DB vacía**

Levantar un Postgres limpio efímero y autogenerar. Usar el postgres del compose dev pero apuntando a una DB temporal vacía, o un container ad-hoc:

```bash
cd backend
docker run -d --rm --name squash_pg -e POSTGRES_USER=t -e POSTGRES_PASSWORD=t -e POSTGRES_DB=t -p 5599:5432 postgres:16-alpine
sleep 3
DATABASE_URL="postgresql+asyncpg://t:t@localhost:5599/t" JWT_SECRET="test-secret-32-chars-minimum-please-ok" \
  uv run alembic revision --autogenerate -m "baseline schema"
docker stop squash_pg
```

Expected: crea `alembic/versions/<hash>_baseline_schema.py` con `down_revision = None` y `op.create_table(...)` para las 19 tablas (18 actuales + `data_access_grants`).

- [ ] **Step 3: Revisar el baseline a mano**

Abrir el archivo generado y verificar que autogenerate NO omitió:
- `comment=` en `create_table` de `accounts`, `counterparties`, las 7 de hechos, `data_access_grants`, `trm_days` (todas las que tienen comment en el modelo).
- `server_default` en columnas que lo tienen (`status`, `year_status`, `currency`, `role`, `created_at`/`fetched_at` con `NOW()`, `raw_attrs` con `'{}'::jsonb`).
- Los CHECK constraints con sus nombres de convención (`ck_transfers_src_arc`, `ck_data_access_grants_no_self`, etc.).
- El índice con expresión `ix_ingest_log_user_id_started_at` (`started_at DESC`).
- `down_revision = None`.

Si falta algo, agregarlo a mano al baseline (autogenerate a veces omite comments y server_defaults de expresión). El drift test del Step 4 es el guard objetivo.

- [ ] **Step 4: Correr el drift test endurecido (debe pasar ahora)**

Run: `cd backend && uv run pytest tests/test_migrations.py -v`
Expected: PASS (el baseline reproduce exactamente `Base.metadata`).

- [ ] **Step 5: Correr la suite completa de schema/modelos**

Run: `cd backend && uv run pytest tests/test_naming_convention.py tests/test_table_comments.py tests/test_grants_model.py tests/test_migrations.py -v`
Expected: PASS todos.

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/
git commit -m "feat(db): squash 18 migrations into pristine baseline (H2)"
```

---

## Task 6: Resolver `visible_account_ids` (G2/G3)

**Files:**
- Create: `backend/src/ibkr_control/authz/__init__.py` (vacío)
- Create: `backend/src/ibkr_control/authz/scope.py`
- Test: `backend/tests/test_authz_scope.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_authz_scope.py
"""G2/G3: resolver de visibilidad por participations + grants."""

from datetime import date

import pytest

from ibkr_control.authz.scope import GrantRequiredError, visible_account_ids


async def _seed(db_session):
    from ibkr_control.auth.models import User
    from ibkr_control.db.models.accounts import Account
    from ibkr_control.db.models.participations import Participation
    from ibkr_control.db.models.grants import DataAccessGrant

    owner = User(email="owner@t.com", hashed_password="x", is_active=True, name="Owner")
    cont = User(email="cont@t.com", hashed_password="x", is_active=True, name="Contador")
    other = User(email="other@t.com", hashed_password="x", is_active=True, name="Other")
    acc = Account(ibkr_account_id="U11111111", alias="own", currency="USD")
    db_session.add_all([owner, cont, other, acc])
    await db_session.commit()
    for u in (owner, cont, other):
        await db_session.refresh(u)
    await db_session.refresh(acc)

    db_session.add(
        Participation(
            user_id=owner.id, account_id=acc.id, pct=1, valid_from=date(2020, 1, 1)
        )
    )
    db_session.add(
        DataAccessGrant(
            grantor_user_id=owner.id, grantee_user_id=cont.id, valid_from=date(2020, 1, 1)
        )
    )
    await db_session.commit()
    return owner, cont, other, acc


async def test_owner_sees_own_accounts(db_session):
    owner, _, _, acc = await _seed(db_session)
    assert await visible_account_ids(db_session, owner.id) == {acc.id}


async def test_contador_with_grant_sees_owner_accounts(db_session):
    owner, cont, _, acc = await _seed(db_session)
    got = await visible_account_ids(db_session, cont.id, on_behalf_of=owner.id)
    assert got == {acc.id}


async def test_contador_without_grant_is_403(db_session):
    owner, _, other, acc = await _seed(db_session)
    with pytest.raises(GrantRequiredError):
        await visible_account_ids(db_session, other.id, on_behalf_of=owner.id)


async def test_contador_omitting_context_sees_empty(db_session):
    _, cont, _, _ = await _seed(db_session)
    assert await visible_account_ids(db_session, cont.id) == set()


async def test_expired_grant_is_403(db_session):
    owner, cont, _, _ = await _seed(db_session)
    with pytest.raises(GrantRequiredError):
        await visible_account_ids(
            db_session, cont.id, on_behalf_of=owner.id, at=date(2019, 1, 1)
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_authz_scope.py -v`
Expected: FAIL (`ModuleNotFoundError: ibkr_control.authz.scope`).

- [ ] **Step 3: Create the resolver**

```python
# backend/src/ibkr_control/authz/__init__.py
```
(archivo vacío)

```python
# backend/src/ibkr_control/authz/scope.py
"""Resolver de visibilidad: qué account_ids puede ver un usuario.

Visibilidad propia = sus participations vigentes. Visibilidad delegada (contador)
= las participations del grantor, si existe un grant read-only vigente. G3: el
contexto es explícito vía on_behalf_of (stateless), nunca mergeado."""

from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.db.models.grants import DataAccessGrant
from ibkr_control.db.models.participations import Participation


class GrantRequiredError(Exception):
    """on_behalf_of seteado pero sin grant read-only vigente del grantor."""

    def __init__(self, grantor_user_id: int):
        self.grantor_user_id = grantor_user_id
        super().__init__(f"No hay grant de lectura vigente del usuario {grantor_user_id}")


async def _participation_account_ids(
    session: AsyncSession, user_id: int, at: date
) -> set[int]:
    rows = await session.scalars(
        select(Participation.account_id).where(
            Participation.user_id == user_id,
            Participation.valid_from <= at,
            or_(Participation.valid_to.is_(None), Participation.valid_to > at),
        )
    )
    return set(rows)


async def has_valid_grant(
    session: AsyncSession, grantor_user_id: int, grantee_user_id: int, at: date
) -> bool:
    g = await session.scalar(
        select(DataAccessGrant).where(
            DataAccessGrant.grantor_user_id == grantor_user_id,
            DataAccessGrant.grantee_user_id == grantee_user_id,
            DataAccessGrant.role == "read_only",
            DataAccessGrant.valid_from <= at,
            or_(DataAccessGrant.valid_to.is_(None), DataAccessGrant.valid_to > at),
        )
    )
    return g is not None


async def visible_account_ids(
    session: AsyncSession,
    user_id: int,
    on_behalf_of: int | None = None,
    at: date | None = None,
) -> set[int]:
    at = at or date.today()
    if on_behalf_of is None or on_behalf_of == user_id:
        return await _participation_account_ids(session, user_id, at)
    if not await has_valid_grant(session, on_behalf_of, user_id, at):
        raise GrantRequiredError(on_behalf_of)
    return await _participation_account_ids(session, on_behalf_of, at)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_authz_scope.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/ibkr_control/authz/ backend/tests/test_authz_scope.py
git commit -m "feat(authz): visible_account_ids resolver with on_behalf_of context (G2/G3)"
```

---

## Task 7: Dependency `require_account_scope` (G6)

**Files:**
- Create: `backend/src/ibkr_control/authz/dependencies.py`
- Test: `backend/tests/test_authz_dependency.py`

- [ ] **Step 1: Write the failing test**

El test monta una ruta temporal que usa la dependency, para validarla sin esperar a Phase 3.

```python
# backend/tests/test_authz_dependency.py
"""G6: require_account_scope traduce on_behalf_of a un set o 403."""

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def app_with_scope_route(app_with_db):
    from fastapi import Depends
    from ibkr_control.authz.dependencies import require_account_scope

    @app_with_db.get("/api/_test/scope")
    async def _scope_probe(ids: set[int] = Depends(require_account_scope)):
        return {"ids": sorted(ids)}

    return app_with_db


@pytest.fixture
async def scope_client(app_with_scope_route):
    transport = ASGITransport(app=app_with_scope_route)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_owner_scope_returns_own(scope_client, auth_headers):
    # auth_headers registró un user sin participations -> set vacío, 200.
    r = await scope_client.get("/api/_test/scope", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["ids"] == []


async def test_on_behalf_of_without_grant_is_403(scope_client, auth_headers):
    r = await scope_client.get(
        "/api/_test/scope", params={"on_behalf_of": 999999}, headers=auth_headers
    )
    assert r.status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_authz_dependency.py -v`
Expected: FAIL (`ModuleNotFoundError: ibkr_control.authz.dependencies`).

- [ ] **Step 3: Create the dependency**

```python
# backend/src/ibkr_control/authz/dependencies.py
"""Dependency FastAPI que envuelve el resolver. Es el contrato que Phase 3
consume: cada endpoint de lectura de hechos resuelve su scope con esto."""

from fastapi import Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.auth.backend import current_active_user
from ibkr_control.auth.models import User
from ibkr_control.authz.scope import GrantRequiredError, visible_account_ids
from ibkr_control.db.session import get_async_session


async def require_account_scope(
    on_behalf_of: int | None = Query(default=None),
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> set[int]:
    try:
        return await visible_account_ids(session, user.id, on_behalf_of=on_behalf_of)
    except GrantRequiredError as e:
        raise HTTPException(
            status_code=403, detail="No tenés acceso a los datos de ese usuario"
        ) from e
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_authz_dependency.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/ibkr_control/authz/dependencies.py backend/tests/test_authz_dependency.py
git commit -m "feat(authz): require_account_scope dependency (G6)"
```

---

## Task 8: CRUD de grants (G6)

**Files:**
- Modify: `backend/src/ibkr_control/api/_schemas.py`
- Create: `backend/src/ibkr_control/api/grants.py`
- Modify: `backend/src/ibkr_control/main.py`
- Test: `backend/tests/test_grants_api.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_grants_api.py
"""G6: CRUD de grants owner-only + isolation."""

import pytest
from httpx import AsyncClient


async def test_create_list_delete_grant(client: AsyncClient, auth_headers, second_auth_headers):
    # second_auth_headers registró api_test_2@test.com — es el grantee (contador).
    r = await client.post(
        "/api/grants",
        json={"grantee_email": "api_test_2@test.com", "valid_from": "2026-01-01"},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["grantee_email"] == "api_test_2@test.com"
    assert body["role"] == "read_only"

    r = await client.get("/api/grants", headers=auth_headers)
    assert r.status_code == 200
    assert len(r.json()["granted"]) == 1

    g = body
    r = await client.request(
        "DELETE",
        f"/api/grants/{g['grantee_user_id']}/{g['valid_from']}",
        headers=auth_headers,
    )
    assert r.status_code == 204
    r = await client.get("/api/grants", headers=auth_headers)
    assert r.json()["granted"] == []


async def test_grant_unknown_email_404(client: AsyncClient, auth_headers):
    r = await client.post(
        "/api/grants",
        json={"grantee_email": "nobody@nowhere.com", "valid_from": "2026-01-01"},
        headers=auth_headers,
    )
    assert r.status_code == 404


async def test_self_grant_400(client: AsyncClient, auth_headers):
    r = await client.post(
        "/api/grants",
        json={"grantee_email": "api_test@test.com", "valid_from": "2026-01-01"},
        headers=auth_headers,
    )
    assert r.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_grants_api.py -v`
Expected: FAIL (404 — ruta `/api/grants` no existe).

- [ ] **Step 3: Add schemas**

En `backend/src/ibkr_control/api/_schemas.py`, agregar:
```python
from datetime import date


class GrantCreate(BaseModel):
    grantee_email: str
    valid_from: date
    valid_to: date | None = None


class GrantRead(BaseModel):
    grantor_user_id: int
    grantee_user_id: int
    grantee_email: str
    role: str
    valid_from: date
    valid_to: date | None
```
(Si `BaseModel` no está importado en ese archivo, agregá `from pydantic import BaseModel`.)

- [ ] **Step 4: Create the router**

```python
# backend/src/ibkr_control/api/grants.py
"""CRUD de grants de lectura. Owner-only: cada user crea/revoca SUS grants."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.api._schemas import GrantCreate, GrantRead
from ibkr_control.auth.backend import current_active_user
from ibkr_control.auth.models import User
from ibkr_control.db.models.grants import DataAccessGrant
from ibkr_control.db.session import get_async_session

router = APIRouter(prefix="/grants", tags=["grants"])


async def _email(session: AsyncSession, user_id: int) -> str:
    return await session.scalar(select(User.email).where(User.id == user_id))


@router.post("", response_model=GrantRead, status_code=201)
async def create_grant(
    payload: GrantCreate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> GrantRead:
    grantee = await session.scalar(select(User).where(User.email == payload.grantee_email))
    if grantee is None:
        raise HTTPException(status_code=404, detail="No existe un usuario con ese email")
    if grantee.id == user.id:
        raise HTTPException(status_code=400, detail="No podés otorgarte acceso a vos mismo")
    grant = DataAccessGrant(
        grantor_user_id=user.id,
        grantee_user_id=grantee.id,
        valid_from=payload.valid_from,
        valid_to=payload.valid_to,
    )
    session.add(grant)
    try:
        await session.commit()
    except IntegrityError as e:
        raise HTTPException(status_code=409, detail="Ese grant ya existe") from e
    return GrantRead(
        grantor_user_id=user.id,
        grantee_user_id=grantee.id,
        grantee_email=grantee.email,
        role=grant.role,
        valid_from=grant.valid_from,
        valid_to=grant.valid_to,
    )


@router.get("")
async def list_grants(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    granted = (
        await session.scalars(
            select(DataAccessGrant).where(DataAccessGrant.grantor_user_id == user.id)
        )
    ).all()
    received = (
        await session.scalars(
            select(DataAccessGrant).where(DataAccessGrant.grantee_user_id == user.id)
        )
    ).all()

    async def _ser(g: DataAccessGrant) -> GrantRead:
        return GrantRead(
            grantor_user_id=g.grantor_user_id,
            grantee_user_id=g.grantee_user_id,
            grantee_email=await _email(session, g.grantee_user_id),
            role=g.role,
            valid_from=g.valid_from,
            valid_to=g.valid_to,
        )

    return {
        "granted": [await _ser(g) for g in granted],
        "received": [await _ser(g) for g in received],
    }


@router.delete("/{grantee_user_id}/{valid_from}", status_code=204)
async def revoke_grant(
    grantee_user_id: int,
    valid_from: date,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> None:
    grant = await session.get(
        DataAccessGrant, (user.id, grantee_user_id, valid_from)
    )
    if grant is None:
        raise HTTPException(status_code=404, detail="Grant no encontrado")
    await session.delete(grant)
    await session.commit()
```

- [ ] **Step 5: Register the router in main.py**

En `backend/src/ibkr_control/main.py`, junto a los otros `include_router`:
```python
from ibkr_control.api.grants import router as grants_router
...
app.include_router(grants_router, prefix="/api")
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_grants_api.py -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Commit**

```bash
git add backend/src/ibkr_control/api/grants.py backend/src/ibkr_control/api/_schemas.py backend/src/ibkr_control/main.py backend/tests/test_grants_api.py
git commit -m "feat(api): grants CRUD router, owner-only (G6)"
```

---

## Task 9: Tests de aislamiento end-to-end (G6)

**Files:**
- Modify: `backend/tests/test_grants_api.py`

- [ ] **Step 1: Write the isolation test**

Agregar a `backend/tests/test_grants_api.py` un test que ejercita el flujo completo contador vía HTTP con la ruta probe de scope:

```python
async def test_contador_isolation_end_to_end(app_with_db):
    """A otorga read a C; C ve los accounts de A vía on_behalf_of; sin grant, 403."""
    from datetime import date

    from fastapi import Depends
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import select

    from ibkr_control.authz.dependencies import require_account_scope
    from ibkr_control.db.models.accounts import Account
    from ibkr_control.db.models.participations import Participation
    from ibkr_control.auth.models import User
    from ibkr_control.db.session import get_async_session

    @app_with_db.get("/api/_test/scope")
    async def _scope_probe(ids: set[int] = Depends(require_account_scope)):
        return {"ids": sorted(ids)}

    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # Registrar owner (A) y contador (C)
        await c.post("/api/auth/register", json={"email": "a@t.com", "password": "supersecret123", "name": "A"})
        await c.post("/api/auth/register", json={"email": "c@t.com", "password": "supersecret123", "name": "C"})
        a_tok = (await c.post("/api/auth/jwt/login", data={"username": "a@t.com", "password": "supersecret123"})).json()["access_token"]
        c_tok = (await c.post("/api/auth/jwt/login", data={"username": "c@t.com", "password": "supersecret123"})).json()["access_token"]
        a_h = {"Authorization": f"Bearer {a_tok}"}
        c_h = {"Authorization": f"Bearer {c_tok}"}

        # Sembrar un account + participation de A directamente en DB
        session_gen = app_with_db.dependency_overrides[get_async_session]()
        session = await session_gen.__anext__()
        acc = Account(ibkr_account_id="U22222222", alias="a-acc", currency="USD")
        session.add(acc)
        await session.commit()
        await session.refresh(acc)
        a_id = await session.scalar(select(User.id).where(User.email == "a@t.com"))
        session.add(Participation(user_id=a_id, account_id=acc.id, pct=1, valid_from=date(2020, 1, 1)))
        await session.commit()

        # Sin grant: C con on_behalf_of=A -> 403
        r = await c.get("/api/_test/scope", params={"on_behalf_of": a_id}, headers=c_h)
        assert r.status_code == 403

        # A otorga grant a C
        r = await c.post("/api/grants", json={"grantee_email": "c@t.com", "valid_from": "2020-01-01"}, headers=a_h)
        assert r.status_code == 201

        # Ahora C ve el account de A
        r = await c.get("/api/_test/scope", params={"on_behalf_of": a_id}, headers=c_h)
        assert r.status_code == 200
        assert r.json()["ids"] == [acc.id]

        # C sin contexto -> set vacío (no ve nada propio)
        r = await c.get("/api/_test/scope", headers=c_h)
        assert r.json()["ids"] == []
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_grants_api.py::test_contador_isolation_end_to_end -v`
Expected: PASS.

- [ ] **Step 3: Run full suite**

Run: `cd backend && uv run pytest -q`
Expected: PASS (todos; ~314+ tests).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_grants_api.py
git commit -m "test(authz): contador isolation end-to-end (G6)"
```

---

## Task 10: Wipe DB dev + upgrade + smoke

**Files:** ninguno (operación de entorno).

- [ ] **Step 1: Verificar lint + format + suite completa**

```bash
cd backend
uv run ruff check . && uv run ruff format --check .
uv run pytest -q
```
Expected: ruff clean, 0 drift, suite verde.

- [ ] **Step 2: Wipe + recrear el schema dev desde el baseline**

```bash
cd /Users/owner/Development/ibkr-control
docker compose exec -T postgres psql -U ibkr -d ibkr_control -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
docker compose exec -T backend uv run alembic upgrade head
```
Expected: `upgrade head` aplica el baseline único sin error.

- [ ] **Step 3: Smoke — verificar tablas + comments + grant table**

```bash
docker compose exec -T postgres psql -U ibkr -d ibkr_control -c "\dt"
docker compose exec -T postgres psql -U ibkr -d ibkr_control -c "\d+ accounts" | grep -i compartida
docker compose exec -T postgres psql -U ibkr -d ibkr_control -c "\d data_access_grants"
```
Expected: 19 tablas (+apscheduler_jobs al boot del backend), comment de `accounts` visible, `data_access_grants` presente con sus CHECKs `ck_data_access_grants_*`.

- [ ] **Step 4: Verificar nombres de convención en la DB viva**

```bash
docker compose exec -T postgres psql -U ibkr -d ibkr_control -c "SELECT conname FROM pg_constraint WHERE conrelid='trades'::regclass ORDER BY conname;"
```
Expected: `fk_trades_account_id_accounts`, `uq_trades_transaction_id` (o el nombre de convención para el unique de columna), `ck_trades_buy_sell`, `ck_trades_open_close`, `pk_trades`.

> No hay commit en esta task (solo verificación de entorno). El usuario recargará los XMLs cuando quiera.

---

## Task 11: Actualizar CLAUDE.md (H4 docs + Phase 2.8)

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Corregir el misdiagnóstico de Phase 2.6**

En la sección "Lecciones de Phase 2.6", localizar el bullet que dice
`**accounts.ibkr_account_id es UNIQUE global, no per-user:**` y reemplazar su
contenido para retirar la recomendación equivocada ("necesita partitioning o un
UNIQUE per-user"). Nuevo texto:

```markdown
  - **accounts.ibkr_account_id es UNIQUE global — esto es CORRECTO, no un bug
    (corregido en Phase 2.8):** la lección original sugería "UNIQUE per-user o
    partitioning" para multi-user. Es un MISDIAGNÓSTICO. `accounts` es identidad
    COMPARTIDA (la conjunta es 50/50 Test Owner+Joint Holder, ambos loguean); meterle
    user_id forzaría filas duplicadas. Los hechos son account-scoped y se
    comparten vía `participations`. El multi-user real se resolvió con la
    primitiva de autorización de Phase 2.8 (grants + visible_account_ids), no
    tocando estas UNIQUE. Ver `docs/specs/2026-06-02-schema-hardening-multiuser-design.md`.
```

- [ ] **Step 2: Agregar la retrospectiva de Phase 2.8**

Agregar al final de la lista de retrospectivas (después de "Phase 2.7"):

```markdown
- **Phase 2.8 — Schema hardening + multi-user RBAC (2026-06-02, branch `phase28/schema-hardening`)** — cierre pre-Phase-3 de 3 items de auditoría + construcción de la primitiva de autorización multi-user. **H1:** `naming_convention` determinística en `Base.metadata` (CHECKs con sufijo semántico; natural keys con `uq_<table>_natural_key` explícito para evitar truncado a 63 chars). **H2:** squash de 18 migraciones en 1 baseline pristino (última ventana pre-deploy; datos dev descartables). **H3:** drift test endurecido con `compare_metadata` + `compare_server_default` (antes solo comparaba tablas — ese era el hueco). **H4:** invariante de identidad compartida explícita vía `comment=` en accounts/counterparties/hechos. **H5 (build):** descubrimiento clave — el "bug latente multi-user" de Phase 2.6 era misdiagnóstico; accounts/hechos son account-scoped + participations por diseño. El contador (leer-sin-poseer) se modeló con tabla `data_access_grants` + resolver `visible_account_ids(user, on_behalf_of)` (contexto stateless, nunca mergeado) + dependency `require_account_scope` (contrato que Phase 3 consume) + CRUD owner-only. Paquete nuevo `authz/` separado de `auth/` (authorization vs authentication). Spec: `docs/specs/2026-06-02-schema-hardening-multiuser-design.md` (H1-H5, G1-G6). Plan: `docs/plans/2026-06-02-schema-hardening-multiuser.md`.
```

- [ ] **Step 3: Actualizar la tabla "Estado actual"**

Agregar una fila después de la de Phase 2.7:
```markdown
| 2.8. Schema hardening + multi-user RBAC (naming_convention + squash + grants/authz) | ✅ completado · branch `phase28/schema-hardening` · cierra los 3 items de auditoría pre-Phase-3 + primitiva de authz | `docs/plans/2026-06-02-schema-hardening-multiuser.md` | `docs/specs/2026-06-02-schema-hardening-multiuser-design.md` (H1-H5, G1-G6) | `v0.2.6-schema-hardening` |
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): correct Phase 2.6 misdiagnosis + Phase 2.8 retrospective"
```

---

## Verificación final

- [ ] `cd backend && uv run pytest -q` → suite verde.
- [ ] `cd backend && uv run ruff check . && uv run ruff format --check .` → clean, 0 drift.
- [ ] `cd backend && uv run pytest tests/test_migrations.py -v` → drift test endurecido en verde (el guard del squash).
- [ ] `git log --oneline` muestra ~11 commits de la fase.
- [ ] DB dev recreada desde el baseline único, `\dt` muestra `data_access_grants`.
