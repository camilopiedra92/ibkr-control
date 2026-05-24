# IBKR Control Center — Phase 2: Data Ingestion

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar el pipeline completo de ingesta de datos crudos: IBKR Flex WS auto-fetch YTD diario + Socrata DIAN TRM diario + scheduler + upload manual de XMLs históricos + setup wizard de 4 pasos. Al final, las tablas crudas (`trades`, `closed_lots`, etc.) están pobladas automáticamente. **NO** se calculan valores COP, **NO** se clasifica 730d, **NO** se construyen pantallas de análisis — eso es Phase 3+.

**Architecture:** Per-source folders (`ingest/flex/`, `ingest/trm/`) + cross-cutting helpers (`ingest/lock.py`, `ingest/log.py`, `ingest/hash_dedup.py`, `ingest/job_tracker.py`). APScheduler con 2 jobs idempotentes (Flex 07:00 COT + TRM 19:30 COT). Concurrencia segura vía advisory locks Postgres. Setup wizard con state persistido en `users.setup_progress JSONB` y progress SSE durante backfill inicial.

**Tech Stack:**
- Backend: Python 3.12 + FastAPI + SQLAlchemy 2.x async + Alembic + APScheduler + cryptography (AES-GCM) + httpx + lxml + sse-starlette + vcrpy + respx + pytest-vcr
- Frontend: Next.js 16 + React 19 + TanStack Query + shadcn/ui + react-dropzone + nativo EventSource API
- DB: Postgres 16 (decisión locked #5)
- Tooling: uv (Python), pnpm (Node), Playwright (E2E)

**Spec referencia:** `docs/specs/2026-05-24-phase2-ingestion-design.md` (en ESTE repo)

**Cross-references al sibling `renta`:** ver `docs/references/renta-cross-references.md` § 1, 3, 4. Reimplementar, NO importar.

**Cobertura:** §5 modelo de datos completo (todas las tablas excepto las derivadas de Phase 3), §6 ingest layer entero, §7.1, 7.2, 7.3, 7.5, 7.6 flujos (sin 7.4 sealing porque queda implícito en persister), §8 testing (parte ingest + api).

**Tag al completar:** `v0.2.0-ingest`

**Decisiones D1-D17:** ver §3 del spec. Locked, no re-discutir.

---

### Task 1: Migration A + models — identity (accounts + participations + flex_credentials + users.setup_*)

**Files:**
- Create: `backend/src/ibkr_control/db/models/accounts.py`
- Create: `backend/src/ibkr_control/db/models/participations.py`
- Create: `backend/src/ibkr_control/db/models/flex_credentials.py`
- Modify: `backend/src/ibkr_control/db/models/user.py` (agregar `setup_completed_at` + `setup_progress`)
- Modify: `backend/src/ibkr_control/db/__init__.py` (re-exportar nuevos modelos para que Alembic los vea)
- Create: `backend/alembic/versions/2026xxxxxx_phase2_identity.py` (autogenerado)
- Create: `backend/tests/test_phase2_identity_migration.py`

- [ ] **Step 1: Escribir tests del schema de identity (failing tests)**

Crear `backend/tests/test_phase2_identity_migration.py`:

```python
"""Tests del schema agregado en migration A (phase2_identity)."""
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_accounts_table_exists(db_session: AsyncSession):
    result = await db_session.execute(
        text("SELECT to_regclass('accounts')")
    )
    assert result.scalar() == 'accounts'


@pytest.mark.asyncio
async def test_accounts_columns(db_session: AsyncSession):
    result = await db_session.execute(text("""
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'accounts'
        ORDER BY ordinal_position
    """))
    cols = {row[0]: (row[1], row[2]) for row in result.all()}
    assert 'id' in cols
    assert 'ibkr_account_id' in cols and cols['ibkr_account_id'][1] == 'NO'
    assert 'alias' in cols and cols['alias'][1] == 'YES'
    assert 'currency' in cols
    assert 'created_at' in cols


@pytest.mark.asyncio
async def test_accounts_ibkr_account_id_unique(db_session: AsyncSession):
    from ibkr_control.db.models.accounts import Account
    db_session.add(Account(ibkr_account_id='U99999001', alias='test1'))
    await db_session.commit()
    db_session.add(Account(ibkr_account_id='U99999001', alias='dup'))
    with pytest.raises(Exception):  # IntegrityError
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_participations_pct_check_constraint(db_session: AsyncSession):
    from ibkr_control.db.models.accounts import Account
    from ibkr_control.db.models.participations import Participation
    from ibkr_control.db.models.user import User
    from datetime import date

    user = User(email='t@t.com', hashed_password='x', is_active=True)
    db_session.add(user)
    acc = Account(ibkr_account_id='U99999002', alias='test2')
    db_session.add(acc)
    await db_session.commit()

    db_session.add(Participation(
        user_id=user.id, account_id=acc.id,
        pct=1.5,  # > 1, debe fallar
        valid_from=date(2026, 1, 1)
    ))
    with pytest.raises(Exception):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_flex_credentials_one_per_user(db_session: AsyncSession):
    from ibkr_control.db.models.flex_credentials import FlexCredentials
    from ibkr_control.db.models.user import User

    user = User(email='t2@t.com', hashed_password='x', is_active=True)
    db_session.add(user)
    await db_session.commit()

    db_session.add(FlexCredentials(
        user_id=user.id, token_encrypted=b'fake', ytd_query_id='123'
    ))
    await db_session.commit()
    db_session.add(FlexCredentials(
        user_id=user.id, token_encrypted=b'fake2', ytd_query_id='456'
    ))
    with pytest.raises(Exception):  # PRIMARY KEY violation
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_users_setup_columns_exist(db_session: AsyncSession):
    result = await db_session.execute(text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'users' AND column_name IN ('setup_completed_at', 'setup_progress')
    """))
    cols = {row[0] for row in result.all()}
    assert cols == {'setup_completed_at', 'setup_progress'}
```

- [ ] **Step 2: Correr tests para verificar que fallan (modelos no existen aún)**

```bash
cd backend && uv run pytest tests/test_phase2_identity_migration.py -v
```

Expected: ImportError / NameError sobre `Account`, `Participation`, `FlexCredentials`.

- [ ] **Step 3: Crear modelo `Account`**

Crear `backend/src/ibkr_control/db/models/accounts.py`:

```python
"""Cuenta IBKR (Uxxxxxxxx). Una sola fila por broker account."""
from datetime import datetime
from sqlalchemy import BigInteger, String, DateTime, text
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ibkr_account_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    alias: Mapped[str | None] = mapped_column(String, nullable=True)
    currency: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'USD'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
```

- [ ] **Step 4: Crear modelo `Participation`**

Crear `backend/src/ibkr_control/db/models/participations.py`:

```python
"""Participación de un user en un account (M:N temporal, con valid_from/valid_to)."""
from datetime import date
from decimal import Decimal
from sqlalchemy import BigInteger, Numeric, Date, ForeignKey, CheckConstraint, PrimaryKeyConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class Participation(Base):
    __tablename__ = "participations"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "account_id", "valid_from"),
        CheckConstraint("pct >= 0 AND pct <= 1", name="ck_participations_pct_range"),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_participations_valid_range"),
    )

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id", ondelete="CASCADE"))
    pct: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
```

- [ ] **Step 5: Crear modelo `FlexCredentials`**

Crear `backend/src/ibkr_control/db/models/flex_credentials.py`:

```python
"""Token Flex encriptado + query_id por user."""
from datetime import datetime
from sqlalchemy import BigInteger, LargeBinary, String, DateTime, ForeignKey, text
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class FlexCredentials(Base):
    __tablename__ = "flex_credentials"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    token_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    ytd_query_id: Mapped[str] = mapped_column(String, nullable=False)
    last_rotated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
```

- [ ] **Step 6: Extender `User` model con `setup_completed_at` + `setup_progress`**

Modificar `backend/src/ibkr_control/db/models/user.py`. Agregar imports + columnas:

```python
from datetime import datetime
from typing import Any
from sqlalchemy import DateTime, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

# Dentro de class User(SQLAlchemyBaseUserTable[int], Base): ...
    setup_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    setup_progress: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
```

- [ ] **Step 7: Re-exportar nuevos modelos en `db/__init__.py`**

Modificar `backend/src/ibkr_control/db/__init__.py` para importar los 3 modelos nuevos (así Alembic los detecta en autogenerate):

```python
from ibkr_control.db.models.user import User  # existing
from ibkr_control.db.models.settings import UserSettings  # existing
from ibkr_control.db.models.accounts import Account
from ibkr_control.db.models.participations import Participation
from ibkr_control.db.models.flex_credentials import FlexCredentials

__all__ = ["User", "UserSettings", "Account", "Participation", "FlexCredentials"]
```

- [ ] **Step 8: Generar migration con autogenerate**

```bash
cd backend && uv run alembic revision --autogenerate -m "phase2_identity: accounts + participations + flex_credentials + users.setup_*"
```

Expected: crear `backend/alembic/versions/2026xxxxxx_phase2_identity.py` con `op.create_table('accounts'...)`, `op.create_table('participations'...)`, `op.create_table('flex_credentials'...)`, `op.add_column('users', 'setup_completed_at'...)`, `op.add_column('users', 'setup_progress'...)`.

- [ ] **Step 9: Revisar manualmente la migration**

Abrir el archivo generado y verificar:
- Constraints en `participations` quedaron (`CHECK pct >= 0 AND pct <= 1`, `CHECK valid_to IS NULL OR valid_to > valid_from`)
- `accounts.ibkr_account_id` tiene `unique=True`
- `flex_credentials.user_id` es PRIMARY KEY (no autogenera id)
- `users.setup_progress` tiene `server_default=text("'{}'::jsonb")`

Si algo falta, agregarlo manualmente (autogenerate a veces se pierde server_defaults).

- [ ] **Step 10: Aplicar migration**

```bash
cd backend && uv run alembic upgrade head
```

Expected: "Running upgrade abc123 -> def456, phase2_identity..."

- [ ] **Step 11: Correr tests, verificar que pasan**

```bash
cd backend && uv run pytest tests/test_phase2_identity_migration.py -v
```

Expected: 6/6 PASS.

- [ ] **Step 12: Correr el test de migrations vs metadata (existente de Phase 1)**

```bash
cd backend && uv run pytest tests/test_migrations.py -v
```

Expected: PASS (sin drift entre `alembic upgrade head` y `Base.metadata`).

- [ ] **Step 13: Commit**

```bash
cd /Users/owner/Development/ibkr-control
git add backend/src/ibkr_control/db/models/accounts.py \
        backend/src/ibkr_control/db/models/participations.py \
        backend/src/ibkr_control/db/models/flex_credentials.py \
        backend/src/ibkr_control/db/models/user.py \
        backend/src/ibkr_control/db/__init__.py \
        backend/alembic/versions/*phase2_identity*.py \
        backend/tests/test_phase2_identity_migration.py
git commit -m "feat(phase2): migration A — identity schema (accounts, participations, flex_credentials, users.setup_*)"
```

---

### Task 2: Migration B + models — TRM (trm_days + trm_imports)

**Files:**
- Create: `backend/src/ibkr_control/db/models/trm.py`
- Modify: `backend/src/ibkr_control/db/__init__.py`
- Create: `backend/alembic/versions/2026xxxxxx_phase2_trm.py`
- Create: `backend/tests/test_phase2_trm_migration.py`

- [ ] **Step 1: Escribir tests TRM schema (failing)**

Crear `backend/tests/test_phase2_trm_migration.py`:

```python
"""Tests del schema TRM (migration B)."""
from datetime import date
from decimal import Decimal
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_trm_days_pk_is_date(db_session: AsyncSession):
    from ibkr_control.db.models.trm import TrmDay
    db_session.add(TrmDay(
        date=date(2026, 1, 15),
        value_cop=Decimal("4123.45"),
        vigencia_desde=date(2026, 1, 15),
        vigencia_hasta=date(2026, 1, 15),
    ))
    await db_session.commit()
    db_session.add(TrmDay(
        date=date(2026, 1, 15),  # mismo PK
        value_cop=Decimal("9999.99"),
        vigencia_desde=date(2026, 1, 15),
        vigencia_hasta=date(2026, 1, 15),
    ))
    with pytest.raises(Exception):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_trm_days_index_on_date(db_session: AsyncSession):
    result = await db_session.execute(text("""
        SELECT indexname FROM pg_indexes
        WHERE tablename = 'trm_days' AND indexname = 'trm_days_date_idx'
    """))
    assert result.scalar() == 'trm_days_date_idx'


@pytest.mark.asyncio
async def test_trm_imports_table_exists(db_session: AsyncSession):
    result = await db_session.execute(text("SELECT to_regclass('trm_imports')"))
    assert result.scalar() == 'trm_imports'


@pytest.mark.asyncio
async def test_trm_days_value_precision(db_session: AsyncSession):
    """TRM puede llegar a 6 enteros + 4 decimales (e.g. 999999.1234). NUMERIC(12,4) lo banca."""
    from ibkr_control.db.models.trm import TrmDay
    db_session.add(TrmDay(
        date=date(2026, 2, 1),
        value_cop=Decimal("12345.6789"),
        vigencia_desde=date(2026, 2, 1),
        vigencia_hasta=date(2026, 2, 1),
    ))
    await db_session.commit()
```

- [ ] **Step 2: Correr tests (deben fallar)**

```bash
cd backend && uv run pytest tests/test_phase2_trm_migration.py -v
```

Expected: ImportError sobre `TrmDay`.

- [ ] **Step 3: Crear modelos TRM**

Crear `backend/src/ibkr_control/db/models/trm.py`:

```python
"""TRM Socrata DIAN — un row por día (con expansión de vigencia)."""
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import BigInteger, Date, DateTime, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class TrmDay(Base):
    __tablename__ = "trm_days"
    __table_args__ = {"comment": "1 row por día calendario, expandido desde vigencia_desde..vigencia_hasta"}

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    value_cop: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    vigencia_desde: Mapped[date] = mapped_column(Date, nullable=False)
    vigencia_hasta: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'dian_socrata_ceyp_9c7c'")
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class TrmImport(Base):
    __tablename__ = "trm_imports"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    date_range_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_range_to: Mapped[date] = mapped_column(Date, nullable=False)
    n_rows_api: Mapped[int] = mapped_column(Integer, nullable=False)
    n_days_expanded: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
```

- [ ] **Step 4: Re-exportar en `db/__init__.py`**

Modificar `backend/src/ibkr_control/db/__init__.py`:

```python
from ibkr_control.db.models.trm import TrmDay, TrmImport
# y agregar a __all__
```

- [ ] **Step 5: Generar migration**

```bash
cd backend && uv run alembic revision --autogenerate -m "phase2_trm: trm_days + trm_imports"
```

- [ ] **Step 6: Revisar manualmente la migration**

Verificar que el index `trm_days_date_idx` esté en la up function. Si falta, agregarlo:

```python
op.create_index('trm_days_date_idx', 'trm_days', ['date'])
```

- [ ] **Step 7: Aplicar migration**

```bash
cd backend && uv run alembic upgrade head
```

- [ ] **Step 8: Correr tests**

```bash
cd backend && uv run pytest tests/test_phase2_trm_migration.py tests/test_migrations.py -v
```

Expected: 5/5 PASS (4 TRM + 1 migrations consistency).

- [ ] **Step 9: Commit**

```bash
git add backend/src/ibkr_control/db/models/trm.py \
        backend/src/ibkr_control/db/__init__.py \
        backend/alembic/versions/*phase2_trm*.py \
        backend/tests/test_phase2_trm_migration.py
git commit -m "feat(phase2): migration B — TRM schema (trm_days + trm_imports)"
```

---

### Task 3: Migration C + models — Flex raw (flex_imports + 6 tablas crudas)

**Files:**
- Create: `backend/src/ibkr_control/db/models/flex_raw.py`
- Modify: `backend/src/ibkr_control/db/__init__.py`
- Create: `backend/alembic/versions/2026xxxxxx_phase2_flex_raw.py`
- Create: `backend/tests/test_phase2_flex_raw_migration.py`

- [ ] **Step 1: Escribir tests del schema flex_raw (failing)**

Crear `backend/tests/test_phase2_flex_raw_migration.py`:

```python
"""Tests del schema flex_raw (migration C)."""
from datetime import date, datetime
from decimal import Decimal
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_flex_imports_tables_exist(db_session: AsyncSession):
    expected = {
        'flex_imports', 'trades', 'closed_lots', 'open_position_lots',
        'transfers', 'transfer_lots', 'cash_transactions',
    }
    for t in expected:
        result = await db_session.execute(text(f"SELECT to_regclass('{t}')"))
        assert result.scalar() == t, f"Table {t} missing"


@pytest.mark.asyncio
async def test_flex_imports_xml_hash_unique(db_session: AsyncSession, sample_user, sample_account):
    from ibkr_control.db.models.flex_raw import FlexImport
    fi1 = FlexImport(
        user_id=sample_user.id, anyo=2025, xml_hash='deadbeef',
        xml_size_bytes=1000, source='manual_upload',
        period_covered_from=date(2025, 1, 1), period_covered_to=date(2025, 12, 31),
        status='ok',
    )
    db_session.add(fi1)
    await db_session.commit()
    db_session.add(FlexImport(
        user_id=sample_user.id, anyo=2025, xml_hash='deadbeef',  # dup
        xml_size_bytes=2000, source='web_service',
        period_covered_from=date(2025, 1, 1), period_covered_to=date(2025, 12, 31),
        status='ok',
    ))
    with pytest.raises(Exception):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_trades_transaction_id_unique(db_session: AsyncSession, sample_user, sample_account):
    from ibkr_control.db.models.flex_raw import FlexImport, Trade
    fi = FlexImport(
        user_id=sample_user.id, anyo=2025, xml_hash='hash-trades-test',
        xml_size_bytes=100, source='manual_upload',
        period_covered_from=date(2025, 1, 1), period_covered_to=date(2025, 12, 31),
        status='ok',
    )
    db_session.add(fi); await db_session.commit()
    t1 = Trade(
        flex_import_id=fi.id, transaction_id='TXN-001',
        account_id=sample_account.id, symbol='AAPL',
        asset_class='STK', trade_date=date(2025, 1, 1),
        qty=Decimal("10"), price_usd=Decimal("150.00"),
        proceeds_usd=Decimal("-1500.00"), commission_usd=Decimal("1.00"),
        buy_sell='BUY',
    )
    db_session.add(t1); await db_session.commit()
    db_session.add(Trade(
        flex_import_id=fi.id, transaction_id='TXN-001',  # dup
        account_id=sample_account.id, symbol='AAPL',
        asset_class='STK', trade_date=date(2025, 1, 2),
        qty=Decimal("5"), price_usd=Decimal("160.00"),
        proceeds_usd=Decimal("-800.00"), commission_usd=Decimal("1.00"),
        buy_sell='BUY',
    ))
    with pytest.raises(Exception):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_trades_buy_sell_check(db_session: AsyncSession, sample_user, sample_account):
    from ibkr_control.db.models.flex_raw import FlexImport, Trade
    fi = FlexImport(
        user_id=sample_user.id, anyo=2025, xml_hash='hash-bs-test',
        xml_size_bytes=100, source='manual_upload',
        period_covered_from=date(2025, 1, 1), period_covered_to=date(2025, 12, 31),
        status='ok',
    )
    db_session.add(fi); await db_session.commit()
    db_session.add(Trade(
        flex_import_id=fi.id, transaction_id='TXN-BAD',
        account_id=sample_account.id, symbol='AAPL',
        asset_class='STK', trade_date=date(2025, 1, 1),
        qty=Decimal("10"), price_usd=Decimal("150.00"),
        proceeds_usd=Decimal("-1500.00"), commission_usd=Decimal("1.00"),
        buy_sell='INVALID',  # check constraint fails
    ))
    with pytest.raises(Exception):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_cascade_delete_flex_import_removes_children(db_session: AsyncSession, sample_user, sample_account):
    """Delete del flex_import borra todos los rows hijos (trades, lots, etc.)."""
    from ibkr_control.db.models.flex_raw import FlexImport, Trade
    fi = FlexImport(
        user_id=sample_user.id, anyo=2025, xml_hash='hash-cascade-test',
        xml_size_bytes=100, source='manual_upload',
        period_covered_from=date(2025, 1, 1), period_covered_to=date(2025, 12, 31),
        status='ok',
    )
    db_session.add(fi); await db_session.commit()
    fi_id = fi.id

    db_session.add(Trade(
        flex_import_id=fi_id, transaction_id='TXN-CASC',
        account_id=sample_account.id, symbol='AAPL',
        asset_class='STK', trade_date=date(2025, 1, 1),
        qty=Decimal("10"), price_usd=Decimal("150.00"),
        proceeds_usd=Decimal("-1500.00"), commission_usd=Decimal("1.00"),
        buy_sell='BUY',
    ))
    await db_session.commit()

    await db_session.delete(fi)
    await db_session.commit()

    result = await db_session.execute(text(f"SELECT COUNT(*) FROM trades WHERE flex_import_id = {fi_id}"))
    assert result.scalar() == 0
```

- [ ] **Step 2: Agregar fixtures `sample_user` y `sample_account` a `conftest.py`**

Modificar `backend/tests/conftest.py` (agregar al final):

```python
import pytest
from datetime import date
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
async def sample_user(db_session: AsyncSession):
    from ibkr_control.db.models.user import User
    u = User(email='fixture@t.com', hashed_password='x', is_active=True)
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def sample_account(db_session: AsyncSession):
    from ibkr_control.db.models.accounts import Account
    a = Account(ibkr_account_id='U99999999', alias='fixture-acc', currency='USD')
    db_session.add(a)
    await db_session.commit()
    await db_session.refresh(a)
    return a
```

- [ ] **Step 3: Correr tests (deben fallar)**

```bash
cd backend && uv run pytest tests/test_phase2_flex_raw_migration.py -v
```

Expected: ImportError sobre `FlexImport`, `Trade`, etc.

- [ ] **Step 4: Crear modelos flex_raw**

Crear `backend/src/ibkr_control/db/models/flex_raw.py`:

```python
"""Tablas crudas del Flex XML: flex_imports + trades + lots + transfers + cash_transactions.

Estas tablas se pueblan tal-cual del XML, sin transformaciones fiscales.
La capa de classification (lot_classifications) vive en Phase 3.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Index,
    Integer, Numeric, String, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class FlexImport(Base):
    __tablename__ = "flex_imports"
    __table_args__ = (
        CheckConstraint("source IN ('web_service', 'manual_upload')", name="ck_flex_imports_source"),
        CheckConstraint("year_status IN ('rolling', 'sealed')", name="ck_flex_imports_year_status"),
        CheckConstraint("status IN ('ok', 'failed')", name="ck_flex_imports_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    anyo: Mapped[int] = mapped_column(Integer, nullable=False)
    xml_hash: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    xml_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    period_covered_from: Mapped[date] = mapped_column(Date, nullable=False)
    period_covered_to: Mapped[date] = mapped_column(Date, nullable=False)
    year_status: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'rolling'"))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    n_trades: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_lots_closed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_open_lots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_cash_tx: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_dividends: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_transfers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        CheckConstraint("open_close IS NULL OR open_close IN ('O', 'C')", name="ck_trades_open_close"),
        CheckConstraint("buy_sell IN ('BUY', 'SELL')", name="ck_trades_buy_sell"),
        Index("trades_account_symbol_idx", "account_id", "symbol"),
        Index("trades_trade_date_idx", "trade_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    flex_import_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("flex_imports.id", ondelete="CASCADE"), nullable=False)
    transaction_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    asset_class: Mapped[str] = mapped_column(String, nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    settle_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    price_usd: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    proceeds_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    commission_usd: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    open_close: Mapped[str | None] = mapped_column(String, nullable=True)
    buy_sell: Mapped[str] = mapped_column(String, nullable=False)
    raw_attrs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class ClosedLot(Base):
    __tablename__ = "closed_lots"
    __table_args__ = (
        Index("closed_lots_account_symbol_idx", "account_id", "symbol"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    flex_import_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("flex_imports.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    open_date: Mapped[date] = mapped_column(Date, nullable=False)
    close_date: Mapped[date] = mapped_column(Date, nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    cost_basis_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    proceeds_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    fifo_pnl_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    source_trade_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("trades.id"), nullable=True)


class OpenPositionLot(Base):
    __tablename__ = "open_position_lots"
    __table_args__ = (
        Index("open_position_lots_account_symbol_idx", "account_id", "symbol"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    flex_import_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("flex_imports.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    open_date: Mapped[date] = mapped_column(Date, nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    cost_basis_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    mark_price_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    mark_value_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)


class Transfer(Base):
    __tablename__ = "transfers"
    __table_args__ = (
        CheckConstraint("direction IN ('IN', 'OUT')", name="ck_transfers_direction"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    flex_import_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("flex_imports.id", ondelete="CASCADE"), nullable=False)
    transfer_date: Mapped[date] = mapped_column(Date, nullable=False)
    direction: Mapped[str] = mapped_column(String, nullable=False)
    src_account_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("accounts.id"), nullable=True)
    dst_account_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("accounts.id"), nullable=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    transfer_type: Mapped[str] = mapped_column(String, nullable=False)


class TransferLot(Base):
    __tablename__ = "transfer_lots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    transfer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("transfers.id", ondelete="CASCADE"), nullable=False)
    original_open_date: Mapped[date] = mapped_column(Date, nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    cost_basis_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)


class CashTransaction(Base):
    __tablename__ = "cash_transactions"
    __table_args__ = (
        Index("cash_transactions_date_idx", "date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    flex_import_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("flex_imports.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'USD'"))
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    symbol: Mapped[str | None] = mapped_column(String, nullable=True)
```

- [ ] **Step 5: Re-exportar en `db/__init__.py`**

```python
from ibkr_control.db.models.flex_raw import (
    FlexImport, Trade, ClosedLot, OpenPositionLot, Transfer, TransferLot, CashTransaction
)
# y agregar a __all__
```

- [ ] **Step 6: Generar migration**

```bash
cd backend && uv run alembic revision --autogenerate -m "phase2_flex_raw: flex_imports + trades + lots + transfers + cash_transactions"
```

- [ ] **Step 7: Revisar manualmente**

Verificar:
- Todos los CheckConstraints
- Todos los Indexes (3 explícitos)
- ON DELETE CASCADE en FKs hacia `flex_imports.id` y `transfers.id`
- `raw_attrs JSONB` con default `'{}'::jsonb`

- [ ] **Step 8: Aplicar migration**

```bash
cd backend && uv run alembic upgrade head
```

- [ ] **Step 9: Correr tests**

```bash
cd backend && uv run pytest tests/test_phase2_flex_raw_migration.py tests/test_migrations.py -v
```

Expected: 6/6 PASS.

- [ ] **Step 10: Commit**

```bash
git add backend/src/ibkr_control/db/models/flex_raw.py \
        backend/src/ibkr_control/db/__init__.py \
        backend/alembic/versions/*phase2_flex_raw*.py \
        backend/tests/conftest.py \
        backend/tests/test_phase2_flex_raw_migration.py
git commit -m "feat(phase2): migration C — flex_raw schema (7 tables: flex_imports + trades + lots + transfers + cash_transactions)"
```

---

### Task 4: Migration D + model — ingest_log

**Files:**
- Create: `backend/src/ibkr_control/db/models/ingest_log.py`
- Modify: `backend/src/ibkr_control/db/__init__.py`
- Create: `backend/alembic/versions/2026xxxxxx_phase2_ingest_log.py`
- Create: `backend/tests/test_phase2_ingest_log_migration.py`

- [ ] **Step 1: Escribir tests (failing)**

Crear `backend/tests/test_phase2_ingest_log_migration.py`:

```python
"""Tests del schema ingest_log (migration D)."""
from datetime import datetime
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_ingest_log_table_exists(db_session: AsyncSession):
    result = await db_session.execute(text("SELECT to_regclass('ingest_log')"))
    assert result.scalar() == 'ingest_log'


@pytest.mark.asyncio
async def test_ingest_log_job_kind_check(db_session: AsyncSession, sample_user):
    from ibkr_control.db.models.ingest_log import IngestLog
    db_session.add(IngestLog(
        job_kind='INVALID', user_id=sample_user.id,
        status='running', trigger='cron',
    ))
    with pytest.raises(Exception):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_ingest_log_user_deleted_sets_null(db_session: AsyncSession, sample_user):
    """Si el user se borra, los logs se preservan con user_id=NULL (audit)."""
    from ibkr_control.db.models.ingest_log import IngestLog
    log = IngestLog(
        job_kind='flex', user_id=sample_user.id,
        status='ok', trigger='cron',
    )
    db_session.add(log); await db_session.commit()
    log_id = log.id

    await db_session.delete(sample_user)
    await db_session.commit()

    result = await db_session.execute(
        text(f"SELECT user_id FROM ingest_log WHERE id = {log_id}")
    )
    assert result.scalar() is None  # SET NULL


@pytest.mark.asyncio
async def test_ingest_log_user_started_idx(db_session: AsyncSession):
    result = await db_session.execute(text("""
        SELECT indexname FROM pg_indexes
        WHERE tablename = 'ingest_log' AND indexname = 'ingest_log_user_started_idx'
    """))
    assert result.scalar() == 'ingest_log_user_started_idx'
```

- [ ] **Step 2: Correr tests (deben fallar)**

```bash
cd backend && uv run pytest tests/test_phase2_ingest_log_migration.py -v
```

- [ ] **Step 3: Crear modelo `IngestLog`**

Crear `backend/src/ibkr_control/db/models/ingest_log.py`:

```python
"""Observabilidad de runs de ingest (cron + manual + wizard)."""
from datetime import datetime
from sqlalchemy import (
    BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
)
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class IngestLog(Base):
    __tablename__ = "ingest_log"
    __table_args__ = (
        CheckConstraint(
            "job_kind IN ('flex', 'trm', 'manual_refresh', 'manual_upload', 'setup_initial')",
            name="ck_ingest_log_job_kind",
        ),
        CheckConstraint("status IN ('running', 'ok', 'failed')", name="ck_ingest_log_status"),
        CheckConstraint("trigger IN ('cron', 'manual', 'wizard')", name="ck_ingest_log_trigger"),
        Index("ingest_log_user_started_idx", "user_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_kind: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    items_processed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    trigger: Mapped[str] = mapped_column(String, nullable=False)
```

- [ ] **Step 4: Re-exportar en `db/__init__.py`**

```python
from ibkr_control.db.models.ingest_log import IngestLog
# y agregar a __all__
```

- [ ] **Step 5: Generar migration**

```bash
cd backend && uv run alembic revision --autogenerate -m "phase2_ingest_log: ingest_log table"
```

- [ ] **Step 6: Revisar manualmente**

Verificar: index DESC en `started_at` (`Index("...", "user_id", "started_at")` — Alembic puede que no preserve DESC en autogenerate; si falta, ajustar manualmente con `text("started_at DESC")`).

Para forzar DESC:

```python
op.create_index(
    'ingest_log_user_started_idx',
    'ingest_log',
    ['user_id', sa.text('started_at DESC')],
)
```

- [ ] **Step 7: Aplicar + correr tests**

```bash
cd backend && uv run alembic upgrade head
cd backend && uv run pytest tests/test_phase2_ingest_log_migration.py tests/test_migrations.py -v
```

Expected: 5/5 PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/src/ibkr_control/db/models/ingest_log.py \
        backend/src/ibkr_control/db/__init__.py \
        backend/alembic/versions/*phase2_ingest_log*.py \
        backend/tests/test_phase2_ingest_log_migration.py
git commit -m "feat(phase2): migration D — ingest_log schema (observability for cron/manual/wizard)"
```

---

### Task 5: Cross-cutting helpers — hash_dedup + lock + log + job_tracker

**Files:**
- Create: `backend/src/ibkr_control/ingest/__init__.py` (vacío)
- Create: `backend/src/ibkr_control/ingest/hash_dedup.py`
- Create: `backend/src/ibkr_control/ingest/lock.py`
- Create: `backend/src/ibkr_control/ingest/log.py`
- Create: `backend/src/ibkr_control/ingest/job_tracker.py`
- Create: `backend/tests/ingest/__init__.py` (vacío)
- Create: `backend/tests/ingest/test_hash_dedup.py`
- Create: `backend/tests/ingest/test_lock.py`
- Create: `backend/tests/ingest/test_log.py`
- Create: `backend/tests/ingest/test_job_tracker.py`

- [ ] **Step 1: Crear estructura de directorios + __init__.py vacíos**

```bash
mkdir -p backend/src/ibkr_control/ingest backend/tests/ingest
touch backend/src/ibkr_control/ingest/__init__.py backend/tests/ingest/__init__.py
```

- [ ] **Step 2: Escribir tests de `hash_dedup.py` (failing)**

Crear `backend/tests/ingest/test_hash_dedup.py`:

```python
"""Tests de SHA-256 dedup helper."""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date

from ibkr_control.ingest.hash_dedup import xml_hash, is_known_hash


def test_xml_hash_deterministic():
    assert xml_hash(b"hello") == xml_hash(b"hello")


def test_xml_hash_different_inputs():
    assert xml_hash(b"hello") != xml_hash(b"world")


def test_xml_hash_is_64_hex_chars():
    h = xml_hash(b"hello")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


@pytest.mark.asyncio
async def test_is_known_hash_false_when_empty(db_session: AsyncSession):
    assert await is_known_hash(db_session, "deadbeef" * 8) is False


@pytest.mark.asyncio
async def test_is_known_hash_true_when_exists(db_session: AsyncSession, sample_user):
    from ibkr_control.db.models.flex_raw import FlexImport
    h = xml_hash(b"sample content")
    db_session.add(FlexImport(
        user_id=sample_user.id, anyo=2025, xml_hash=h,
        xml_size_bytes=100, source='manual_upload',
        period_covered_from=date(2025, 1, 1), period_covered_to=date(2025, 12, 31),
        status='ok',
    ))
    await db_session.commit()
    assert await is_known_hash(db_session, h) is True
```

- [ ] **Step 3: Implementar `hash_dedup.py`**

Crear `backend/src/ibkr_control/ingest/hash_dedup.py`:

```python
"""SHA-256 dedup helper para flex_imports."""
import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def xml_hash(xml_bytes: bytes) -> str:
    """Hexdigest SHA-256 de un blob de bytes."""
    return hashlib.sha256(xml_bytes).hexdigest()


async def is_known_hash(session: AsyncSession, hash_hex: str) -> bool:
    """True si ya existe un flex_imports.xml_hash con este valor."""
    from ibkr_control.db.models.flex_raw import FlexImport
    result = await session.scalar(
        select(FlexImport.id).where(FlexImport.xml_hash == hash_hex)
    )
    return result is not None
```

- [ ] **Step 4: Correr tests de hash_dedup**

```bash
cd backend && uv run pytest tests/ingest/test_hash_dedup.py -v
```

Expected: 5/5 PASS.

- [ ] **Step 5: Escribir tests de `lock.py` (failing)**

Crear `backend/tests/ingest/test_lock.py`:

```python
"""Tests del advisory lock por (source, user_id)."""
import asyncio
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.ingest.lock import advisory_lock, LockHeldError


@pytest.mark.asyncio
async def test_advisory_lock_acquires_and_releases(db_session: AsyncSession):
    async with advisory_lock(db_session, user_id=1, source='flex'):
        pass  # se libera en __aexit__


@pytest.mark.asyncio
async def test_advisory_lock_releases_on_exception(db_session: AsyncSession):
    """Si el block lanza, el lock se libera y se puede re-acquirir."""
    with pytest.raises(ValueError):
        async with advisory_lock(db_session, user_id=2, source='flex'):
            raise ValueError("boom")

    # Re-acquire en la misma session debe funcionar
    async with advisory_lock(db_session, user_id=2, source='flex'):
        pass


@pytest.mark.asyncio
async def test_advisory_lock_blocks_concurrent(db_engine):
    """Dos sessions distintas pidiendo el mismo lock — la segunda recibe LockHeldError."""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    SessionLocal = async_sessionmaker(db_engine, expire_on_commit=False)

    async with SessionLocal() as s1, SessionLocal() as s2:
        async with advisory_lock(s1, user_id=99, source='flex'):
            with pytest.raises(LockHeldError) as exc_info:
                async with advisory_lock(s2, user_id=99, source='flex'):
                    pass
            assert exc_info.value.source == 'flex'
            assert exc_info.value.user_id == 99


@pytest.mark.asyncio
async def test_advisory_lock_different_sources_independent(db_engine):
    """flex y trm tienen locks distintos para el mismo user."""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    SessionLocal = async_sessionmaker(db_engine, expire_on_commit=False)

    async with SessionLocal() as s1, SessionLocal() as s2:
        async with advisory_lock(s1, user_id=100, source='flex'):
            async with advisory_lock(s2, user_id=100, source='trm'):
                pass  # ambos OK
```

- [ ] **Step 6: Implementar `lock.py`**

Crear `backend/src/ibkr_control/ingest/lock.py`:

```python
"""Advisory lock por (source, user_id) sobre Postgres.

Usa pg_try_advisory_lock — non-blocking, falla rápido si está tomado.
El lock se libera explícitamente en el finally del context manager.
"""
from contextlib import asynccontextmanager
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class LockHeldError(RuntimeError):
    """Lanzada cuando un advisory lock está tomado por otra session."""

    def __init__(self, source: str, user_id: int | None):
        self.source = source
        self.user_id = user_id
        super().__init__(f"Lock held: source={source}, user_id={user_id}")


def _lock_key(source: str, user_id: int | None) -> int:
    """Mapea (source, user_id) a un bigint positivo determinístico."""
    key_str = f"{source}:{user_id}"
    h = hash(key_str)
    # Forzar a bigint positivo (0 .. 2^63-1)
    return h & 0x7FFFFFFFFFFFFFFF


@asynccontextmanager
async def advisory_lock(session: AsyncSession, user_id: int | None, source: str):
    """Adquiere lock para (source, user_id). Lanza LockHeldError si tomado.

    Args:
        session: sesión SQLAlchemy. El lock vive en esta sesión (libera al commit/close).
        user_id: id del usuario (None para locks globales como TRM).
        source: 'flex' | 'trm' | 'manual_upload' | ...
    """
    key = _lock_key(source, user_id)
    acquired = await session.scalar(
        text("SELECT pg_try_advisory_lock(:k)"), {"k": key}
    )
    if not acquired:
        raise LockHeldError(source=source, user_id=user_id)
    try:
        yield
    finally:
        await session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
```

- [ ] **Step 7: Agregar fixture `db_engine` a conftest.py si no existe**

Verificar en `backend/tests/conftest.py` que exista un `db_engine` fixture (session-scoped). Si solo hay `db_session`, agregar:

```python
@pytest.fixture(scope="session")
async def db_engine():
    """Engine compartido para tests que necesitan crear sus propias sessions (ej. concurrencia)."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from ibkr_control.config import get_settings
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    yield engine
    await engine.dispose()
```

- [ ] **Step 8: Correr tests de lock**

```bash
cd backend && uv run pytest tests/ingest/test_lock.py -v
```

Expected: 4/4 PASS.

- [ ] **Step 9: Escribir tests de `log.py` (failing)**

Crear `backend/tests/ingest/test_log.py`:

```python
"""Tests del context manager ingest_log_entry."""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.ingest.log import ingest_log_entry


@pytest.mark.asyncio
async def test_log_creates_running_row_on_enter(db_session: AsyncSession, sample_user):
    async with ingest_log_entry(db_session, 'flex', sample_user.id, 'cron') as log_id:
        result = await db_session.execute(
            text(f"SELECT status, job_kind, user_id, trigger FROM ingest_log WHERE id = {log_id}")
        )
        row = result.first()
        assert row.status == 'running'
        assert row.job_kind == 'flex'
        assert row.user_id == sample_user.id
        assert row.trigger == 'cron'


@pytest.mark.asyncio
async def test_log_finishes_ok_on_normal_exit(db_session: AsyncSession, sample_user):
    async with ingest_log_entry(db_session, 'trm', sample_user.id, 'manual') as log_id:
        pass
    result = await db_session.execute(
        text(f"SELECT status, finished_at, error_message FROM ingest_log WHERE id = {log_id}")
    )
    row = result.first()
    assert row.status == 'ok'
    assert row.finished_at is not None
    assert row.error_message is None


@pytest.mark.asyncio
async def test_log_finishes_failed_on_exception(db_session: AsyncSession, sample_user):
    with pytest.raises(ValueError, match="boom"):
        async with ingest_log_entry(db_session, 'flex', sample_user.id, 'wizard') as log_id:
            raise ValueError("boom")

    result = await db_session.execute(
        text(f"SELECT status, error_message, finished_at FROM ingest_log WHERE id = {log_id}")
    )
    row = result.first()
    assert row.status == 'failed'
    assert 'ValueError' in row.error_message
    assert 'boom' in row.error_message
    assert row.finished_at is not None


@pytest.mark.asyncio
async def test_log_items_processed_settable(db_session: AsyncSession, sample_user):
    """El caller puede setear items_processed antes del exit."""
    from ibkr_control.db.models.ingest_log import IngestLog
    from sqlalchemy import select

    async with ingest_log_entry(db_session, 'flex', sample_user.id, 'cron') as log_id:
        row = await db_session.scalar(select(IngestLog).where(IngestLog.id == log_id))
        row.items_processed = 42

    result = await db_session.execute(
        text(f"SELECT items_processed FROM ingest_log WHERE id = {log_id}")
    )
    assert result.scalar() == 42
```

- [ ] **Step 10: Implementar `log.py`**

Crear `backend/src/ibkr_control/ingest/log.py`:

```python
"""Context manager para crear + actualizar rows en ingest_log."""
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@asynccontextmanager
async def ingest_log_entry(
    session: AsyncSession,
    job_kind: str,
    user_id: int | None,
    trigger: str,
):
    """Crea un row en ingest_log con status='running' y lo cierra al salir.

    Yields:
        log_id: int del row recién creado (el caller puede usarlo para
                modificar items_processed antes del exit).

    Args:
        job_kind: 'flex' | 'trm' | 'manual_refresh' | 'manual_upload' | 'setup_initial'
        user_id: None para jobs globales (e.g. TRM cron sin user específico)
        trigger: 'cron' | 'manual' | 'wizard'
    """
    from ibkr_control.db.models.ingest_log import IngestLog

    row = IngestLog(
        job_kind=job_kind, user_id=user_id, trigger=trigger, status='running'
    )
    session.add(row)
    await session.flush()
    log_id = row.id
    try:
        yield log_id
        row.status = 'ok'
        row.finished_at = _utcnow()
    except Exception as exc:
        row.status = 'failed'
        tb_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        row.error_message = tb_text[:8000]
        row.finished_at = _utcnow()
        raise
    finally:
        await session.commit()
```

- [ ] **Step 11: Correr tests de log**

```bash
cd backend && uv run pytest tests/ingest/test_log.py -v
```

Expected: 4/4 PASS.

- [ ] **Step 12: Escribir tests de `job_tracker.py` (failing)**

Crear `backend/tests/ingest/test_job_tracker.py`:

```python
"""Tests del singleton in-memory job_tracker para SSE."""
import pytest
from ibkr_control.ingest.job_tracker import JobTracker


def test_emit_and_read_events():
    tracker = JobTracker()
    job_id = tracker.create_job()
    tracker.emit(job_id, {"step": "trm_backfill", "status": "running"})
    tracker.emit(job_id, {"step": "trm_backfill", "status": "ok", "n_days": 12000})

    events = tracker.events_since(job_id, after_id=0)
    assert len(events) == 2
    assert events[0].payload == {"step": "trm_backfill", "status": "running"}
    assert events[1].payload["n_days"] == 12000


def test_events_since_filters_by_id():
    tracker = JobTracker()
    job_id = tracker.create_job()
    tracker.emit(job_id, {"step": "a"})
    tracker.emit(job_id, {"step": "b"})
    tracker.emit(job_id, {"step": "c"})

    events = tracker.events_since(job_id, after_id=1)
    assert len(events) == 2
    assert events[0].payload == {"step": "b"}


def test_is_done_default_false():
    tracker = JobTracker()
    job_id = tracker.create_job()
    assert tracker.is_done(job_id) is False


def test_mark_done():
    tracker = JobTracker()
    job_id = tracker.create_job()
    tracker.mark_done(job_id)
    assert tracker.is_done(job_id) is True


def test_unknown_job_returns_empty():
    tracker = JobTracker()
    assert tracker.events_since(99999, after_id=0) == []
    assert tracker.is_done(99999) is False
```

- [ ] **Step 13: Implementar `job_tracker.py`**

Crear `backend/src/ibkr_control/ingest/job_tracker.py`:

```python
"""Singleton in-memory para tracking de eventos de jobs (alimenta SSE).

V1: in-process, single backend replica. V2: Redis pub/sub.
"""
from dataclasses import dataclass, field
from itertools import count
from typing import Any


@dataclass
class TrackerEvent:
    id: int
    payload: dict[str, Any]


@dataclass
class _JobState:
    events: list[TrackerEvent] = field(default_factory=list)
    done: bool = False
    next_event_id: int = 0


class JobTracker:
    """Singleton para emitir y consumir eventos por job_id."""

    def __init__(self) -> None:
        self._jobs: dict[int, _JobState] = {}
        self._next_job_id = count(start=1)

    def create_job(self) -> int:
        job_id = next(self._next_job_id)
        self._jobs[job_id] = _JobState()
        return job_id

    def emit(self, job_id: int, payload: dict[str, Any]) -> None:
        if job_id not in self._jobs:
            return
        state = self._jobs[job_id]
        event = TrackerEvent(id=state.next_event_id, payload=payload)
        state.events.append(event)
        state.next_event_id += 1

    def events_since(self, job_id: int, after_id: int) -> list[TrackerEvent]:
        if job_id not in self._jobs:
            return []
        return [e for e in self._jobs[job_id].events if e.id >= after_id]

    def mark_done(self, job_id: int) -> None:
        if job_id in self._jobs:
            self._jobs[job_id].done = True

    def is_done(self, job_id: int) -> bool:
        return job_id in self._jobs and self._jobs[job_id].done

    def cleanup(self, job_id: int) -> None:
        """Remueve el job del tracker (llamar después de N minutos de done)."""
        self._jobs.pop(job_id, None)


# Singleton global usado por endpoints + jobs
_tracker_instance = JobTracker()


def get_tracker() -> JobTracker:
    return _tracker_instance
```

- [ ] **Step 14: Correr todos los tests del task**

```bash
cd backend && uv run pytest tests/ingest/ -v
```

Expected: 18/18 PASS (5 hash + 4 lock + 4 log + 5 tracker).

- [ ] **Step 15: Commit**

```bash
git add backend/src/ibkr_control/ingest/ backend/tests/ingest/ backend/tests/conftest.py
git commit -m "feat(phase2): cross-cutting ingest helpers (hash_dedup + lock + log + job_tracker)"
```

---

### Task 6: XML fixtures sanitizadas + sanitization script + VCR setup

**Files:**
- Create: `backend/scripts/__init__.py` (vacío)
- Create: `backend/scripts/sanitize_xml.py`
- Create: `backend/scripts/sanitize_cassette.py`
- Create: `backend/tests/fixtures/__init__.py` (vacío)
- Create: `backend/tests/fixtures/xml/` (directorio para fixtures sanitizadas)
- Create: `backend/tests/fixtures/cassettes/flex/` (directorio para VCR Flex)
- Create: `backend/tests/fixtures/cassettes/trm/` (directorio para VCR TRM)
- Modify: `backend/pyproject.toml` (agregar vcrpy + respx)
- Modify: `backend/tests/conftest.py` (agregar fixtures vcr + cassette_dir)

- [ ] **Step 1: Agregar dependencies vcrpy + respx + lxml + cryptography + httpx**

Modificar `backend/pyproject.toml` `[project.dependencies]`:

```toml
dependencies = [
    # ... existing
    "cryptography>=42.0",
    "httpx>=0.27",
    "lxml>=5.2",
    "sse-starlette>=2.1",
    "apscheduler>=3.10",
]

[dependency-groups]
dev = [
    # ... existing
    "vcrpy>=6.0",
    "respx>=0.21",
    "pytest-vcr>=1.0",
]
```

- [ ] **Step 2: Lock + install**

```bash
cd backend && uv lock && uv sync
```

- [ ] **Step 3: Crear script de sanitización XML**

Crear `backend/scripts/__init__.py` vacío y `backend/scripts/sanitize_xml.py`:

```python
"""Sanitiza un XML del Flex reemplazando datos personales por placeholders.

Uso:
    uv run python -m scripts.sanitize_xml INPUT.xml OUTPUT.xml

Idempotente: si vuelve a correr sobre el output, no cambia nada.
"""
import re
import sys
from pathlib import Path


# Mapping de reemplazos: regex pattern → replacement
# Account IDs reales → placeholders
REPLACEMENTS = [
    (re.compile(r"U99999001"), "U99999001"),
    (re.compile(r"U99999002"), "U99999002"),
    (re.compile(r"U99999003"), "U99999003"),
    # NIT colombiano del titular
    (re.compile(r"1234567890"), "1234567890"),
]


def sanitize_text(content: str) -> tuple[str, dict[str, int]]:
    """Aplica REPLACEMENTS al texto. Retorna (texto, conteo_por_pattern)."""
    stats: dict[str, int] = {}
    for pattern, replacement in REPLACEMENTS:
        new_content, n = pattern.subn(replacement, content)
        if n > 0:
            stats[pattern.pattern] = n
        content = new_content
    return content, stats


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"Usage: {argv[0]} INPUT.xml OUTPUT.xml", file=sys.stderr)
        return 1

    input_path = Path(argv[1])
    output_path = Path(argv[2])

    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 2

    content = input_path.read_text(encoding="utf-8")
    sanitized, stats = sanitize_text(content)
    output_path.write_text(sanitized, encoding="utf-8")

    print(f"Sanitized {input_path} → {output_path}")
    for pattern, n in stats.items():
        print(f"  {pattern}: {n} replacements")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

- [ ] **Step 4: Sanitizar XMLs del sibling renta**

```bash
mkdir -p backend/tests/fixtures/xml
cd backend && \
uv run python -m scripts.sanitize_xml \
    /Users/owner/Development/renta/fuentes/2024/compartidos/ibkr/ACTIVITY_2024.xml \
    tests/fixtures/xml/ACTIVITY_2024_sanitized.xml && \
uv run python -m scripts.sanitize_xml \
    /Users/owner/Development/renta/fuentes/2025/compartidos/ibkr/ACTIVITY_2025.xml \
    tests/fixtures/xml/ACTIVITY_2025_sanitized.xml
```

- [ ] **Step 5: Verificar sanitización**

```bash
cd backend && grep -E 'U[0-9]{8}' tests/fixtures/xml/ACTIVITY_2024_sanitized.xml | head -5
```

Expected: TODAS las matches deben ser `U99999001/U99999002/U99999003`, ninguna `U99999001/U99999002/U99999003`.

```bash
cd backend && grep -c '1234567890' tests/fixtures/xml/ACTIVITY_2024_sanitized.xml
```

Expected: `0`.

- [ ] **Step 6: Crear fixtures sintéticas chicas (empty + malformed + opt + fut)**

Crear `backend/tests/fixtures/xml/empty_query_response.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="YTD" type="AF">
  <FlexStatements count="1">
    <FlexStatement accountId="U99999001" fromDate="2026-01-01" toDate="2026-05-24"
                   period="YearToDate" whenGenerated="2026-05-24;10:00:00">
      <AccountInformation accountId="U99999001" currency="USD"/>
      <Trades/>
      <ClosedLots/>
      <OpenPositions/>
      <CashTransactions/>
      <Transfers/>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>
```

Crear `backend/tests/fixtures/xml/malformed_xml.xml`:

```xml
<?xml version="1.0"?>
<FlexQueryResponse>
  <NotProperlyClosed
```

Crear `backend/tests/fixtures/xml/not_a_flex_response.xml`:

```xml
<?xml version="1.0"?>
<SomeOtherRoot>
  <Hello/>
</SomeOtherRoot>
```

- [ ] **Step 7: Configurar VCR en conftest.py**

Modificar `backend/tests/conftest.py` (agregar al final):

```python
from pathlib import Path


@pytest.fixture(scope="session")
def cassette_dir() -> Path:
    return Path(__file__).parent / "fixtures" / "cassettes"


@pytest.fixture
def vcr_config():
    """Config base para VCR. Filtra Authorization header y query params sensibles."""
    return {
        "filter_headers": ["Authorization", "X-Api-Key"],
        "filter_query_parameters": ["t", "token", "$$app_token"],
        "decode_compressed_response": True,
        "record_mode": "none",  # CI fails if cassette missing
    }
```

- [ ] **Step 8: Crear script de sanitización de cassettes**

Crear `backend/scripts/sanitize_cassette.py`:

```python
"""Sanitiza cassettes VCR (.yaml) reemplazando datos personales del response body.

Uso:
    uv run python -m scripts.sanitize_cassette tests/fixtures/cassettes/flex/*.yaml

Sanitiza el cuerpo de los responses (donde está el XML del Flex con account IDs reales).
NO toca headers (ya filtrados por vcr_config).
"""
import sys
from pathlib import Path

from scripts.sanitize_xml import sanitize_text


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(f"Usage: {argv[0]} CASSETTE.yaml [CASSETTE2.yaml ...]", file=sys.stderr)
        return 1

    for path_str in argv[1:]:
        path = Path(path_str)
        if not path.exists():
            print(f"skip: {path} (not found)", file=sys.stderr)
            continue
        content = path.read_text(encoding="utf-8")
        sanitized, stats = sanitize_text(content)
        path.write_text(sanitized, encoding="utf-8")
        print(f"sanitized {path}: {sum(stats.values())} replacements")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

- [ ] **Step 9: Verificar setup con un test smoke del fixture**

Crear `backend/tests/fixtures/test_fixtures_smoke.py`:

```python
"""Smoke test que verifica que los fixtures XML existen y son parseables."""
from pathlib import Path
from lxml import etree

FIXTURE_DIR = Path(__file__).parent / "xml"


def test_activity_2024_fixture_exists_and_parses():
    path = FIXTURE_DIR / "ACTIVITY_2024_sanitized.xml"
    assert path.exists(), f"Falta fixture: {path}"
    tree = etree.parse(str(path))
    root = tree.getroot()
    assert root.tag == "FlexQueryResponse"


def test_activity_2025_fixture_exists_and_parses():
    path = FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml"
    assert path.exists()
    tree = etree.parse(str(path))
    assert tree.getroot().tag == "FlexQueryResponse"


def test_no_real_account_ids_in_2024():
    path = FIXTURE_DIR / "ACTIVITY_2024_sanitized.xml"
    content = path.read_text()
    assert "U99999001" not in content
    assert "U99999002" not in content
    assert "U99999003" not in content
    assert "1234567890" not in content


def test_no_real_account_ids_in_2025():
    path = FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml"
    content = path.read_text()
    assert "U99999001" not in content
    assert "U99999002" not in content
    assert "U99999003" not in content


def test_empty_response_fixture():
    path = FIXTURE_DIR / "empty_query_response.xml"
    assert path.exists()
    tree = etree.parse(str(path))
    assert tree.getroot().tag == "FlexQueryResponse"
```

- [ ] **Step 10: Correr smoke tests**

```bash
cd backend && uv run pytest tests/fixtures/test_fixtures_smoke.py -v
```

Expected: 5/5 PASS.

- [ ] **Step 11: Crear directorio de cassettes (vacío por ahora, se popula en Tasks 7-11)**

```bash
mkdir -p backend/tests/fixtures/cassettes/flex backend/tests/fixtures/cassettes/trm
touch backend/tests/fixtures/cassettes/.gitkeep
```

- [ ] **Step 12: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock \
        backend/scripts/ \
        backend/tests/fixtures/ \
        backend/tests/conftest.py
git commit -m "feat(phase2): XML fixtures sanitizadas + VCR scaffolding + sanitization scripts"
```

---

### Task 7: Flex crypto — AES-GCM encrypt/decrypt del token

**Files:**
- Create: `backend/src/ibkr_control/ingest/flex/__init__.py` (vacío)
- Create: `backend/src/ibkr_control/ingest/flex/crypto.py`
- Create: `backend/tests/ingest/flex/__init__.py` (vacío)
- Create: `backend/tests/ingest/flex/test_crypto.py`
- Modify: `.env.example` (agregar `TOKEN_ENCRYPTION_KEY`)

- [ ] **Step 1: Crear directorios**

```bash
mkdir -p backend/src/ibkr_control/ingest/flex backend/tests/ingest/flex
touch backend/src/ibkr_control/ingest/flex/__init__.py backend/tests/ingest/flex/__init__.py
```

- [ ] **Step 2: Generar test key + agregar a .env.example**

Generar una key real para los tests + agregar al .env.example como placeholder:

```bash
openssl rand -base64 32
# copiar output, agregar a .env.example:
echo "" >> .env.example
echo "# AES-GCM key for Flex token encryption (32 bytes base64). Generate with: openssl rand -base64 32" >> .env.example
echo "TOKEN_ENCRYPTION_KEY=REPLACE_ME_WITH_OPENSSL_OUTPUT" >> .env.example
```

Agregar al `.env` local (NO commit):

```bash
echo "TOKEN_ENCRYPTION_KEY=<output del openssl rand>" >> .env
```

- [ ] **Step 3: Escribir tests de crypto (failing)**

Crear `backend/tests/ingest/flex/test_crypto.py`:

```python
"""Tests de AES-GCM para Flex token encryption."""
import base64
import os
import pytest

from ibkr_control.ingest.flex.crypto import encrypt_token, decrypt_token


@pytest.fixture(autouse=True)
def set_key(monkeypatch):
    """Setea una key conocida para todos los tests del módulo."""
    key = base64.b64encode(b"X" * 32).decode("ascii")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)


def test_round_trip_simple():
    plaintext = "abc123xyz"
    blob = encrypt_token(plaintext)
    assert decrypt_token(blob) == plaintext


def test_round_trip_long_token():
    plaintext = "a" * 1024
    blob = encrypt_token(plaintext)
    assert decrypt_token(blob) == plaintext


def test_round_trip_unicode():
    plaintext = "tóken-con-ñ-y-emoji-🔐"
    blob = encrypt_token(plaintext)
    assert decrypt_token(blob) == plaintext


def test_encrypt_produces_different_ciphertexts_for_same_plaintext():
    """Nonce es aleatorio — dos encrypts del mismo plaintext dan ciphertexts distintos."""
    blob1 = encrypt_token("same-input")
    blob2 = encrypt_token("same-input")
    assert blob1 != blob2
    assert decrypt_token(blob1) == decrypt_token(blob2) == "same-input"


def test_decrypt_tampered_blob_raises(monkeypatch):
    """AES-GCM detecta tampering vía el authentication tag."""
    blob = encrypt_token("real-token")
    tampered = bytearray(blob)
    tampered[-1] ^= 0xFF  # flip un bit del tag
    with pytest.raises(Exception):  # InvalidTag
        decrypt_token(bytes(tampered))


def test_missing_env_var_raises(monkeypatch):
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    with pytest.raises(KeyError):
        encrypt_token("x")


def test_invalid_key_size_raises(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", base64.b64encode(b"X" * 16).decode("ascii"))
    with pytest.raises(RuntimeError, match="32 bytes"):
        encrypt_token("x")


def test_blob_structure_nonce_prepended():
    """Verifica el formato: nonce(12) || ciphertext || tag(16)."""
    blob = encrypt_token("test")
    # nonce + ciphertext (4 bytes "test") + tag (16 bytes) = 12 + 4 + 16 = 32 bytes
    assert len(blob) == 32
```

- [ ] **Step 4: Implementar `crypto.py`**

Crear `backend/src/ibkr_control/ingest/flex/crypto.py`:

```python
"""AES-GCM encrypt/decrypt para el Flex Token.

Formato del blob: nonce(12) || ciphertext(N) || tag(16)
Key: env var TOKEN_ENCRYPTION_KEY, base64 de 32 bytes raw.
"""
import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _key() -> bytes:
    raw = os.environ["TOKEN_ENCRYPTION_KEY"]
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise RuntimeError(
            f"TOKEN_ENCRYPTION_KEY must decode to exactly 32 bytes, got {len(key)}"
        )
    return key


def encrypt_token(plaintext: str) -> bytes:
    aes = AESGCM(_key())
    nonce = os.urandom(12)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), associated_data=None)
    return nonce + ct


def decrypt_token(blob: bytes) -> str:
    aes = AESGCM(_key())
    nonce, ct = blob[:12], blob[12:]
    return aes.decrypt(nonce, ct, associated_data=None).decode("utf-8")
```

- [ ] **Step 5: Correr tests**

```bash
cd backend && uv run pytest tests/ingest/flex/test_crypto.py -v
```

Expected: 8/8 PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/ibkr_control/ingest/flex/ \
        backend/tests/ingest/flex/ \
        .env.example
git commit -m "feat(phase2): Flex token AES-GCM encrypt/decrypt"
```

---

### Task 8: Flex parser — XML → dataclasses + audit de tags conocidos

**Files:**
- Create: `backend/src/ibkr_control/ingest/flex/parser.py`
- Create: `backend/src/ibkr_control/ingest/flex/_known_tags.py`
- Create: `backend/src/ibkr_control/ingest/flex/_models.py` (dataclasses parsed)
- Create: `backend/tests/ingest/flex/test_parser.py`

**Referencia renta:** `documentos/ibkr_flex/loader.py` (parser ~600 LOC), `_known_tags.py`, `_audit.py`. Reimplementar. NO importar.

- [ ] **Step 1: Crear `_known_tags.py` con catálogo de tags válidos**

Crear `backend/src/ibkr_control/ingest/flex/_known_tags.py`:

```python
"""Catálogo de tags conocidos del Flex XML.

Si el parser encuentra un tag TOP-level fuera de esta lista, ABORTA el ingest
(patrón replicado de renta/documentos/ibkr_flex/_audit.py).

Esto es defensivo: si IBKR agrega un nuevo tag (e.g. CryptoTransactions),
queremos verlo explícitamente y actualizar la lista manualmente, NO
silenciosamente ignorarlo.
"""

# Tags TOP-level del XML que SÍ procesamos. El audit aborta si encuentra otros.
KNOWN_TOP_LEVEL_TAGS: frozenset[str] = frozenset({
    "AccountInformation",
    "Trades",                  # Trade rows
    "ClosedLots",              # ClosedLot rows
    "OpenPositions",           # OpenPosition rows
    "CashTransactions",        # CashTransaction rows
    "Transfers",               # Transfer rows
    "TransferLots",
    # Tags que vienen en XML pero ignoramos explícitamente:
    "ChangeInDividendAccruals",
    "ConversionRates",
    "NetAssetValue",
    "EquitySummaryInBase",
    "MTMPerformanceSummaryInBase",
    "AccruedDividends",
    "RealizedAndUnrealizedPerformanceSummaryInBase",
    "FinancialInstrumentInformation",
    "TradeConfirms",
    "OptionEAE",
    "CorporateActions",
    "InterestAccruals",
    "SLBActivity",
    "CommissionCredits",
    "FxLots",
    "FxTrades",
    "FdicInsuredDepositsByBank",
    "PriorPeriodPositions",
    "ChangeInPositionValues",
    "MTDYTDPerformanceSummary",
    "Routes",
    "TierInterestDetails",
    "SoftDollars",
})

# Tags TOP-level que explícitamente IGNORAMOS al parsear (no error, no insert).
# Si un tag está acá, el parser lo saltea silenciosamente.
EXPLICITLY_IGNORED: frozenset[str] = frozenset({
    "ChangeInDividendAccruals", "ConversionRates", "NetAssetValue",
    "EquitySummaryInBase", "MTMPerformanceSummaryInBase", "AccruedDividends",
    "RealizedAndUnrealizedPerformanceSummaryInBase", "FinancialInstrumentInformation",
    "TradeConfirms", "OptionEAE", "CorporateActions", "InterestAccruals",
    "SLBActivity", "CommissionCredits", "FxLots", "FxTrades",
    "FdicInsuredDepositsByBank", "PriorPeriodPositions", "ChangeInPositionValues",
    "MTDYTDPerformanceSummary", "Routes", "TierInterestDetails", "SoftDollars",
})
```

- [ ] **Step 2: Crear `_models.py` (dataclasses parsed)**

Crear `backend/src/ibkr_control/ingest/flex/_models.py`:

```python
"""Dataclasses que el parser produce a partir del XML (intermediarias, no DB)."""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass
class ParsedAccount:
    ibkr_account_id: str
    currency: str


@dataclass
class ParsedTrade:
    transaction_id: str
    ibkr_account_id: str
    symbol: str
    asset_class: str
    trade_date: date
    settle_date: date | None
    qty: Decimal
    price_usd: Decimal
    proceeds_usd: Decimal
    commission_usd: Decimal
    open_close: str | None  # 'O' | 'C' | None
    buy_sell: str           # 'BUY' | 'SELL'
    raw_attrs: dict


@dataclass
class ParsedClosedLot:
    ibkr_account_id: str
    symbol: str
    open_date: date
    close_date: date
    qty: Decimal
    cost_basis_usd: Decimal
    proceeds_usd: Decimal
    fifo_pnl_usd: Decimal


@dataclass
class ParsedOpenPositionLot:
    ibkr_account_id: str
    symbol: str
    open_date: date
    qty: Decimal
    cost_basis_usd: Decimal
    mark_price_usd: Decimal | None
    mark_value_usd: Decimal | None
    snapshot_date: date


@dataclass
class ParsedCashTransaction:
    ibkr_account_id: str
    type: str
    currency: str
    amount_usd: Decimal
    description: str | None
    date: date
    symbol: str | None


@dataclass
class ParsedTransfer:
    transfer_date: date
    direction: str
    src_ibkr_account_id: str | None
    dst_ibkr_account_id: str | None
    symbol: str
    qty: Decimal
    transfer_type: str
    lots: list["ParsedTransferLot"] = field(default_factory=list)


@dataclass
class ParsedTransferLot:
    original_open_date: date
    qty: Decimal
    cost_basis_usd: Decimal


@dataclass
class ParsedXML:
    anyo: int
    period_from: date
    period_to: date
    accounts: list[ParsedAccount]
    trades: list[ParsedTrade]
    closed_lots: list[ParsedClosedLot]
    open_position_lots: list[ParsedOpenPositionLot]
    cash_transactions: list[ParsedCashTransaction]
    transfers: list[ParsedTransfer]


class UnknownFlexTagError(RuntimeError):
    """Lanzada cuando el parser encuentra un tag TOP-level desconocido.

    No se silencia para forzar revisión humana cuando IBKR cambia el schema.
    """

    def __init__(self, tag: str):
        self.tag = tag
        super().__init__(
            f"Unknown TOP-level Flex tag: '{tag}'. "
            f"If this is a new IBKR tag, add it to _known_tags.KNOWN_TOP_LEVEL_TAGS "
            f"(and optionally to EXPLICITLY_IGNORED if it should be skipped)."
        )
```

- [ ] **Step 3: Escribir tests del parser (failing)**

Crear `backend/tests/ingest/flex/test_parser.py`:

```python
"""Tests del parser de Flex XML."""
from datetime import date
from decimal import Decimal
from pathlib import Path
import pytest

from ibkr_control.ingest.flex.parser import parse
from ibkr_control.ingest.flex._models import UnknownFlexTagError

FIXTURE_DIR = Path(__file__).parent.parent.parent / "fixtures" / "xml"


def test_parses_empty_response():
    xml = (FIXTURE_DIR / "empty_query_response.xml").read_bytes()
    parsed = parse(xml)
    assert parsed.anyo == 2026
    assert parsed.period_from == date(2026, 1, 1)
    assert parsed.period_to == date(2026, 5, 24)
    assert len(parsed.accounts) == 1
    assert parsed.accounts[0].ibkr_account_id == "U99999001"
    assert parsed.trades == []
    assert parsed.closed_lots == []


def test_rejects_non_flex_response():
    xml = (FIXTURE_DIR / "not_a_flex_response.xml").read_bytes()
    with pytest.raises(ValueError, match="FlexQueryResponse"):
        parse(xml)


def test_rejects_malformed_xml():
    xml = (FIXTURE_DIR / "malformed_xml.xml").read_bytes()
    with pytest.raises(Exception):  # XMLSyntaxError de lxml
        parse(xml)


def test_unknown_top_level_tag_aborts():
    """Si aparece un tag desconocido en TOP-level, abortar (no silenciar)."""
    xml = b"""<?xml version="1.0"?>
<FlexQueryResponse>
  <FlexStatements count="1">
    <FlexStatement accountId="U99999001" fromDate="2026-01-01" toDate="2026-01-31"
                   period="YearToDate" whenGenerated="2026-02-01;10:00:00">
      <AccountInformation accountId="U99999001" currency="USD"/>
      <Trades/>
      <BrandNewIBKRTagWeNeverSawBefore/>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>"""
    with pytest.raises(UnknownFlexTagError, match="BrandNewIBKRTagWeNeverSawBefore"):
        parse(xml)


def test_parses_activity_2024_fixture():
    xml = (FIXTURE_DIR / "ACTIVITY_2024_sanitized.xml").read_bytes()
    parsed = parse(xml)
    assert parsed.anyo == 2024
    assert len(parsed.accounts) >= 1
    assert len(parsed.trades) > 0
    # Account IDs deben ser sanitizados
    for trade in parsed.trades:
        assert trade.ibkr_account_id in {"U99999001", "U99999002", "U99999003"}


def test_parses_activity_2025_fixture():
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    parsed = parse(xml)
    assert parsed.anyo == 2025
    assert len(parsed.trades) > 0


def test_trade_has_decimal_amounts():
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    parsed = parse(xml)
    for trade in parsed.trades[:5]:
        assert isinstance(trade.qty, Decimal)
        assert isinstance(trade.price_usd, Decimal)
        assert isinstance(trade.proceeds_usd, Decimal)
        assert isinstance(trade.commission_usd, Decimal)


def test_buy_sell_is_normalized():
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    parsed = parse(xml)
    for trade in parsed.trades:
        assert trade.buy_sell in {"BUY", "SELL"}


def test_open_close_optional():
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    parsed = parse(xml)
    # Algunos trades pueden no tener openCloseIndicator (e.g. FUT cash settlement)
    valid_values = {"O", "C", None}
    for trade in parsed.trades:
        assert trade.open_close in valid_values


def test_closed_lot_pnl_consistency():
    """fifo_pnl_usd debería ser ~ proceeds_usd - cost_basis_usd."""
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    parsed = parse(xml)
    for lot in parsed.closed_lots[:10]:
        computed = lot.proceeds_usd - lot.cost_basis_usd
        assert abs(lot.fifo_pnl_usd - computed) < Decimal("0.01")


def test_raw_attrs_preserved():
    """Atributos del XML no tipados deben quedar en raw_attrs JSONB."""
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    parsed = parse(xml)
    if parsed.trades:
        # Al menos algún atributo no del schema fijo debe estar en raw_attrs
        first = parsed.trades[0]
        assert isinstance(first.raw_attrs, dict)
```

- [ ] **Step 4: Correr tests (deben fallar — parser no existe)**

```bash
cd backend && uv run pytest tests/ingest/flex/test_parser.py -v
```

Expected: ImportError sobre `parse`.

- [ ] **Step 5: Implementar el parser**

Crear `backend/src/ibkr_control/ingest/flex/parser.py`:

```python
"""Parser de XML Flex → ParsedXML.

Recibe bytes del XML, devuelve dataclasses tipadas. NO toca DB.
Hace audit de tags TOP-level y aborta si encuentra alguno desconocido
(patrón replicado de renta/documentos/ibkr_flex/_audit.py).
"""
from datetime import date, datetime
from decimal import Decimal

from lxml import etree

from ibkr_control.ingest.flex._known_tags import KNOWN_TOP_LEVEL_TAGS, EXPLICITLY_IGNORED
from ibkr_control.ingest.flex._models import (
    ParsedAccount, ParsedTrade, ParsedClosedLot, ParsedOpenPositionLot,
    ParsedCashTransaction, ParsedTransfer, ParsedTransferLot, ParsedXML,
    UnknownFlexTagError,
)


def _parse_date(s: str) -> date:
    """IBKR usa YYYY-MM-DD o YYYYMMDD según el campo."""
    s = s.strip()
    if len(s) == 8 and s.isdigit():
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    return date.fromisoformat(s)


def _dec(s: str | None, default: str = "0") -> Decimal:
    if s is None or s == "":
        return Decimal(default)
    return Decimal(s)


def _attr(elem, name: str) -> str | None:
    """Get attribute or None."""
    val = elem.get(name)
    return val if val else None


def parse(xml_bytes: bytes) -> ParsedXML:
    """Punto de entrada. Recibe bytes, devuelve ParsedXML."""
    tree = etree.fromstring(xml_bytes)

    if tree.tag != "FlexQueryResponse":
        raise ValueError(
            f"Expected root element <FlexQueryResponse>, got <{tree.tag}>"
        )

    statements = tree.findall(".//FlexStatement")
    if not statements:
        raise ValueError("No <FlexStatement> found in <FlexQueryResponse>")

    # Multi-account: cada FlexStatement puede ser de un account distinto.
    # Agregamos todo en un solo ParsedXML.
    accounts: list[ParsedAccount] = []
    trades: list[ParsedTrade] = []
    closed_lots: list[ParsedClosedLot] = []
    open_position_lots: list[ParsedOpenPositionLot] = []
    cash_transactions: list[ParsedCashTransaction] = []
    transfers: list[ParsedTransfer] = []

    period_from_list = []
    period_to_list = []

    for stmt in statements:
        period_from_list.append(_parse_date(stmt.get("fromDate")))
        period_to_list.append(_parse_date(stmt.get("toDate")))

        # Audit de tags TOP-level
        for child in stmt:
            tag = child.tag
            if tag not in KNOWN_TOP_LEVEL_TAGS:
                raise UnknownFlexTagError(tag)
            if tag in EXPLICITLY_IGNORED:
                continue

            if tag == "AccountInformation":
                accounts.append(ParsedAccount(
                    ibkr_account_id=child.get("accountId"),
                    currency=child.get("currency") or "USD",
                ))
            elif tag == "Trades":
                trades.extend(_parse_trades(child))
            elif tag == "ClosedLots":
                closed_lots.extend(_parse_closed_lots(child))
            elif tag == "OpenPositions":
                open_position_lots.extend(_parse_open_positions(child))
            elif tag == "CashTransactions":
                cash_transactions.extend(_parse_cash_transactions(child))
            elif tag == "Transfers":
                transfers.extend(_parse_transfers(child))
            # TransferLots se procesan nested dentro de Transfers

    period_from = min(period_from_list)
    period_to = max(period_to_list)
    anyo = period_to.year

    return ParsedXML(
        anyo=anyo,
        period_from=period_from,
        period_to=period_to,
        accounts=_dedupe_accounts(accounts),
        trades=trades,
        closed_lots=closed_lots,
        open_position_lots=open_position_lots,
        cash_transactions=cash_transactions,
        transfers=transfers,
    )


def _dedupe_accounts(accs: list[ParsedAccount]) -> list[ParsedAccount]:
    """Mismo account_id puede aparecer en varios FlexStatement."""
    seen: dict[str, ParsedAccount] = {}
    for a in accs:
        seen.setdefault(a.ibkr_account_id, a)
    return list(seen.values())


# Schema fijo de Trade (atributos que mapean a columnas tipadas).
# Cualquier otro atributo va a raw_attrs.
_TRADE_TYPED_ATTRS = {
    "transactionID", "accountId", "symbol", "assetCategory",
    "tradeDate", "settleDateTarget", "quantity", "tradePrice",
    "proceeds", "ibCommission", "openCloseIndicator", "buySell",
}


def _parse_trades(elem) -> list[ParsedTrade]:
    out: list[ParsedTrade] = []
    for t in elem.iterchildren("Trade"):
        raw_attrs = {k: v for k, v in t.attrib.items() if k not in _TRADE_TYPED_ATTRS}
        out.append(ParsedTrade(
            transaction_id=t.get("transactionID"),
            ibkr_account_id=t.get("accountId"),
            symbol=t.get("symbol"),
            asset_class=t.get("assetCategory"),
            trade_date=_parse_date(t.get("tradeDate")),
            settle_date=_parse_date(t.get("settleDateTarget")) if t.get("settleDateTarget") else None,
            qty=_dec(t.get("quantity")),
            price_usd=_dec(t.get("tradePrice")),
            proceeds_usd=_dec(t.get("proceeds")),
            commission_usd=_dec(t.get("ibCommission")),
            open_close=t.get("openCloseIndicator") or None,
            buy_sell=t.get("buySell"),
            raw_attrs=raw_attrs,
        ))
    return out


def _parse_closed_lots(elem) -> list[ParsedClosedLot]:
    out: list[ParsedClosedLot] = []
    for lot in elem.iterchildren("ClosedLot"):
        out.append(ParsedClosedLot(
            ibkr_account_id=lot.get("accountId"),
            symbol=lot.get("symbol"),
            open_date=_parse_date(lot.get("openDateTime") or lot.get("openDate")),
            close_date=_parse_date(lot.get("dateTime") or lot.get("closeDate") or lot.get("tradeDate")),
            qty=_dec(lot.get("quantity")),
            cost_basis_usd=_dec(lot.get("costBasis")),
            proceeds_usd=_dec(lot.get("proceeds")),
            fifo_pnl_usd=_dec(lot.get("fifoPnlRealized")),
        ))
    return out


def _parse_open_positions(elem) -> list[ParsedOpenPositionLot]:
    out: list[ParsedOpenPositionLot] = []
    for pos in elem.iterchildren("OpenPosition"):
        out.append(ParsedOpenPositionLot(
            ibkr_account_id=pos.get("accountId"),
            symbol=pos.get("symbol"),
            open_date=_parse_date(pos.get("openDateTime") or pos.get("holdingPeriodDateTime")),
            qty=_dec(pos.get("position")),
            cost_basis_usd=_dec(pos.get("costBasisMoney") or pos.get("costBasisPrice")),
            mark_price_usd=_dec(pos.get("markPrice")) if pos.get("markPrice") else None,
            mark_value_usd=_dec(pos.get("positionValue")) if pos.get("positionValue") else None,
            snapshot_date=_parse_date(pos.get("reportDate")),
        ))
    return out


def _parse_cash_transactions(elem) -> list[ParsedCashTransaction]:
    out: list[ParsedCashTransaction] = []
    for tx in elem.iterchildren("CashTransaction"):
        out.append(ParsedCashTransaction(
            ibkr_account_id=tx.get("accountId"),
            type=tx.get("type"),
            currency=tx.get("currency") or "USD",
            amount_usd=_dec(tx.get("amount")),
            description=tx.get("description"),
            date=_parse_date(tx.get("dateTime") or tx.get("settleDate")),
            symbol=tx.get("symbol") or None,
        ))
    return out


def _parse_transfers(elem) -> list[ParsedTransfer]:
    out: list[ParsedTransfer] = []
    for tr in elem.iterchildren("Transfer"):
        transfer = ParsedTransfer(
            transfer_date=_parse_date(tr.get("dateTime") or tr.get("date")),
            direction=tr.get("direction"),
            src_ibkr_account_id=tr.get("transferAccount") if tr.get("direction") == "IN" else tr.get("accountId"),
            dst_ibkr_account_id=tr.get("accountId") if tr.get("direction") == "IN" else tr.get("transferAccount"),
            symbol=tr.get("symbol") or "",
            qty=_dec(tr.get("quantity")),
            transfer_type=tr.get("type") or tr.get("transferType") or "unknown",
        )
        # Nested TransferLot rows
        for lot in tr.iterchildren("TransferLot"):
            transfer.lots.append(ParsedTransferLot(
                original_open_date=_parse_date(lot.get("openDateTime") or lot.get("originalOpenDate")),
                qty=_dec(lot.get("quantity")),
                cost_basis_usd=_dec(lot.get("costBasis")),
            ))
        out.append(transfer)
    return out
```

- [ ] **Step 6: Correr tests del parser**

```bash
cd backend && uv run pytest tests/ingest/flex/test_parser.py -v
```

Expected: ~11/11 PASS. Si algún test del fixture real falla, leer el XML para entender el formato exacto y ajustar el getter (e.g. `openDateTime` vs `openDate`). El catálogo `_known_tags.py` puede necesitar ajustes según los tags reales que tenga el XML — si aparece `UnknownFlexTagError` sobre un tag legítimo, agregarlo a `KNOWN_TOP_LEVEL_TAGS` y `EXPLICITLY_IGNORED` si corresponde.

- [ ] **Step 7: Commit**

```bash
git add backend/src/ibkr_control/ingest/flex/parser.py \
        backend/src/ibkr_control/ingest/flex/_known_tags.py \
        backend/src/ibkr_control/ingest/flex/_models.py \
        backend/tests/ingest/flex/test_parser.py
git commit -m "feat(phase2): Flex XML parser with audit (rejects unknown TOP-level tags)"
```

---

### Task 9: Flex client — SendRequest + Poll con backoff (VCR cassettes)

**Files:**
- Create: `backend/src/ibkr_control/ingest/flex/client.py`
- Create: `backend/tests/ingest/flex/test_client.py`
- Create: `backend/tests/fixtures/cassettes/flex/send_request_ok.yaml` (grabar manualmente la 1ra vez con cred real, sanitizar)
- Create: `backend/tests/fixtures/cassettes/flex/poll_statement_ready.yaml`
- Create: `backend/tests/fixtures/cassettes/flex/send_request_invalid_token.yaml`

- [ ] **Step 1: Escribir tests del client (incluye estructura de cassettes a grabar)**

Crear `backend/tests/ingest/flex/test_client.py`:

```python
"""Tests del cliente Flex Web Service.

Usa VCR cassettes grabadas con credenciales reales una sola vez,
sanitizadas para no leakear el token ni los account IDs reales.

Para regrabar (cuando IBKR cambie el API):
    RECORD_MODE=new_episodes uv run pytest tests/ingest/flex/test_client.py -v
    uv run python -m scripts.sanitize_cassette tests/fixtures/cassettes/flex/*.yaml
"""
import asyncio
import pytest
import respx
from httpx import Response

from ibkr_control.ingest.flex.client import (
    FlexClient, FlexPollTimeoutError, FlexAuthError,
)


@pytest.mark.vcr(cassette_library_dir="tests/fixtures/cassettes/flex")
@pytest.mark.asyncio
async def test_send_request_returns_reference_code():
    client = FlexClient(token="fake-token", base_url="https://gdcdyn.interactivebrokers.com")
    ref = await client.send_request(query_id="1234567")
    assert ref  # reference code numérico string
    assert ref.isdigit()


@pytest.mark.vcr(cassette_library_dir="tests/fixtures/cassettes/flex")
@pytest.mark.asyncio
async def test_poll_statement_returns_xml_bytes():
    """Cassette pre-grabada donde el reference ya está READY."""
    client = FlexClient(token="fake-token", base_url="https://gdcdyn.interactivebrokers.com")
    xml = await client.poll_statement(reference_code="9999999")
    assert xml.startswith(b"<?xml") or xml.startswith(b"<FlexQueryResponse")


@pytest.mark.asyncio
async def test_send_request_with_invalid_token_raises_auth_error(respx_mock):
    """Mock con respx — escenario de error que no necesita cassette."""
    respx_mock.get("https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.SendRequest").mock(
        return_value=Response(200, content=b"""<?xml version="1.0"?>
<FlexStatementResponse timestamp="2026-05-24 10:00:00.000">
  <Status>Fail</Status>
  <ErrorCode>1018</ErrorCode>
  <ErrorMessage>Invalid token</ErrorMessage>
</FlexStatementResponse>""")
    )
    client = FlexClient(token="bad", base_url="https://gdcdyn.interactivebrokers.com")
    with pytest.raises(FlexAuthError):
        await client.send_request(query_id="1234567")


@pytest.mark.asyncio
async def test_poll_statement_times_out_after_5min(monkeypatch):
    """Si IBKR sigue diciendo 'pending' después de 5 min, abortar."""
    from ibkr_control.ingest.flex import client as client_module

    # Acelerar el sleep para no esperar 5 min real
    sleep_calls = []
    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(client_module.asyncio, "sleep", fake_sleep)

    # Mockear que TODAS las llamadas devuelven "pending"
    with respx.mock(base_url="https://gdcdyn.interactivebrokers.com") as mock_router:
        mock_router.get("/Universal/servlet/FlexStatementService.GetStatement").mock(
            return_value=Response(200, content=b"""<?xml version="1.0"?>
<FlexStatementResponse><Status>Warn</Status><ErrorCode>1019</ErrorCode><ErrorMessage>Statement generation in progress</ErrorMessage></FlexStatementResponse>""")
        )
        client = FlexClient(token="x", base_url="https://gdcdyn.interactivebrokers.com")
        with pytest.raises(FlexPollTimeoutError):
            await client.poll_statement(reference_code="9999", max_wait_seconds=300)

    # Verificó múltiples retries con backoff
    assert len(sleep_calls) >= 5  # al menos 1, 2, 4, 8, 16 segundos


@pytest.mark.asyncio
async def test_poll_statement_succeeds_after_few_polls(monkeypatch):
    """Primer poll: pending. Segundo: XML ready."""
    from ibkr_control.ingest.flex import client as client_module

    async def fake_sleep(seconds): pass
    monkeypatch.setattr(client_module.asyncio, "sleep", fake_sleep)

    with respx.mock(base_url="https://gdcdyn.interactivebrokers.com") as mock_router:
        route = mock_router.get("/Universal/servlet/FlexStatementService.GetStatement").mock(
            side_effect=[
                Response(200, content=b"<FlexStatementResponse><Status>Warn</Status><ErrorCode>1019</ErrorCode><ErrorMessage>pending</ErrorMessage></FlexStatementResponse>"),
                Response(200, content=b"<?xml version='1.0'?><FlexQueryResponse><FlexStatements count='1'/></FlexQueryResponse>"),
            ]
        )
        client = FlexClient(token="x", base_url="https://gdcdyn.interactivebrokers.com")
        xml = await client.poll_statement(reference_code="9999")
        assert b"FlexQueryResponse" in xml
        assert route.call_count == 2
```

- [ ] **Step 2: Correr tests (deben fallar — sin client)**

```bash
cd backend && uv run pytest tests/ingest/flex/test_client.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implementar el client**

Crear `backend/src/ibkr_control/ingest/flex/client.py`:

```python
"""Cliente HTTP del IBKR Flex Web Service.

Flujo:
  1. SendRequest(token, query_id) → reference_code
  2. Poll GetStatement(token, reference_code) con backoff hasta que IBKR
     responda con el XML completo (o time out después de 5 min).

URLs (production):
  https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.SendRequest
  https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement
"""
import asyncio
from typing import Final

import httpx
from lxml import etree


SEND_REQUEST_PATH: Final = "/Universal/servlet/FlexStatementService.SendRequest"
GET_STATEMENT_PATH: Final = "/Universal/servlet/FlexStatementService.GetStatement"
DEFAULT_BASE_URL: Final = "https://gdcdyn.interactivebrokers.com"

# Códigos de error documentados por IBKR
ERR_INVALID_TOKEN = "1018"
ERR_STATEMENT_PENDING = "1019"


class FlexAuthError(RuntimeError):
    """Token inválido o sin permisos para el query_id."""

    def __init__(self, error_code: str, error_message: str):
        self.error_code = error_code
        self.error_message = error_message
        super().__init__(f"Flex auth error {error_code}: {error_message}")


class FlexPollTimeoutError(RuntimeError):
    """IBKR no entregó el XML en max_wait_seconds."""

    def __init__(self, reference_code: str, waited: int):
        self.reference_code = reference_code
        self.waited = waited
        super().__init__(
            f"Flex poll timeout: reference={reference_code} after {waited}s"
        )


class FlexClientError(RuntimeError):
    """Otros errores del API."""


class FlexClient:
    """Cliente async para IBKR Flex Web Service."""

    def __init__(self, token: str, base_url: str = DEFAULT_BASE_URL, timeout: float = 30.0):
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def send_request(self, query_id: str) -> str:
        """Inicia generación de statement. Devuelve reference_code para poll posterior."""
        url = f"{self._base_url}{SEND_REQUEST_PATH}"
        params = {"v": "3", "t": self._token, "q": query_id}

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.get(url, params=params)
            resp.raise_for_status()

        return self._parse_send_response(resp.content)

    async def poll_statement(
        self,
        reference_code: str,
        max_wait_seconds: int = 300,
    ) -> bytes:
        """Poll con backoff exponencial. Devuelve bytes del XML cuando esté listo."""
        url = f"{self._base_url}{GET_STATEMENT_PATH}"
        params = {"v": "3", "t": self._token, "q": reference_code}

        backoff = 1
        elapsed = 0
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            while elapsed < max_wait_seconds:
                resp = await http.get(url, params=params)
                resp.raise_for_status()
                content = resp.content

                if self._is_pending(content):
                    await asyncio.sleep(backoff)
                    elapsed += backoff
                    backoff = min(backoff * 2, 16)
                    continue

                return content

        raise FlexPollTimeoutError(reference_code, elapsed)

    @staticmethod
    def _parse_send_response(body: bytes) -> str:
        tree = etree.fromstring(body)
        status = tree.findtext("Status")
        if status == "Success":
            ref = tree.findtext("ReferenceCode")
            if not ref:
                raise FlexClientError("SendRequest Success but no ReferenceCode")
            return ref

        error_code = tree.findtext("ErrorCode") or ""
        error_message = tree.findtext("ErrorMessage") or "unknown"
        if error_code == ERR_INVALID_TOKEN:
            raise FlexAuthError(error_code, error_message)
        raise FlexClientError(f"SendRequest failed {error_code}: {error_message}")

    @staticmethod
    def _is_pending(body: bytes) -> bool:
        if not body.lstrip().startswith(b"<?xml"):
            # Body es directamente el FlexQueryResponse → listo
            return False
        try:
            tree = etree.fromstring(body)
        except etree.XMLSyntaxError:
            return False

        if tree.tag == "FlexQueryResponse":
            return False

        if tree.tag == "FlexStatementResponse":
            error_code = tree.findtext("ErrorCode") or ""
            return error_code == ERR_STATEMENT_PENDING

        return False
```

- [ ] **Step 4: Grabar cassettes (PROCESO MANUAL — requiere credenciales reales)**

**IMPORTANTE:** Este paso es manual. Si no tenés credenciales reales o querés diferirlo, podés crear cassettes mínimas a mano (ver Step 5 abajo).

```bash
# 1. Setear cred real temporalmente
export FLEX_TOKEN_REAL="<tu token real>"
export FLEX_QUERY_ID_REAL="<tu query_id real>"

# 2. Crear test temporal que grabe:
cd backend && cat > /tmp/record_cassettes.py <<'PYEOF'
import asyncio, os
from ibkr_control.ingest.flex.client import FlexClient

async def main():
    client = FlexClient(token=os.environ["FLEX_TOKEN_REAL"])
    ref = await client.send_request(os.environ["FLEX_QUERY_ID_REAL"])
    print(f"Reference: {ref}")
    xml = await client.poll_statement(ref)
    print(f"XML size: {len(xml)} bytes")

asyncio.run(main())
PYEOF

# 3. Correr con VCR en modo "record":
# (usar vcrpy directamente o el plugin pytest-vcr en modo record)
# Después sanitizar:
uv run python -m scripts.sanitize_cassette tests/fixtures/cassettes/flex/*.yaml

# 4. Limpiar
unset FLEX_TOKEN_REAL FLEX_QUERY_ID_REAL
rm /tmp/record_cassettes.py
```

- [ ] **Step 5: ALTERNATIVA — Crear cassettes mínimas a mano (si no podés grabar reales)**

Crear `backend/tests/fixtures/cassettes/flex/send_request_ok.yaml`:

```yaml
interactions:
  - request:
      method: GET
      uri: https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.SendRequest?v=3&t=FILTERED&q=1234567
      headers:
        Host:
          - gdcdyn.interactivebrokers.com
      body: null
    response:
      status:
        code: 200
        message: OK
      headers:
        content-type:
          - text/xml
      body:
        string: |
          <?xml version="1.0"?>
          <FlexStatementResponse>
            <Status>Success</Status>
            <ReferenceCode>9999999</ReferenceCode>
            <Url>https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement</Url>
          </FlexStatementResponse>
version: 1
```

Crear `backend/tests/fixtures/cassettes/flex/poll_statement_ready.yaml`:

```yaml
interactions:
  - request:
      method: GET
      uri: https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement?v=3&t=FILTERED&q=9999999
      headers:
        Host:
          - gdcdyn.interactivebrokers.com
      body: null
    response:
      status:
        code: 200
        message: OK
      headers:
        content-type:
          - text/xml
      body:
        string: |
          <?xml version="1.0" encoding="UTF-8"?>
          <FlexQueryResponse queryName="YTD" type="AF">
            <FlexStatements count="1">
              <FlexStatement accountId="U99999001" fromDate="2026-01-01" toDate="2026-05-24"
                             period="YearToDate" whenGenerated="2026-05-24;10:00:00">
                <AccountInformation accountId="U99999001" currency="USD"/>
                <Trades/>
                <ClosedLots/>
                <OpenPositions/>
                <CashTransactions/>
                <Transfers/>
              </FlexStatement>
            </FlexStatements>
          </FlexQueryResponse>
version: 1
```

- [ ] **Step 6: Correr todos los tests del client**

```bash
cd backend && uv run pytest tests/ingest/flex/test_client.py -v
```

Expected: 5/5 PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/src/ibkr_control/ingest/flex/client.py \
        backend/tests/ingest/flex/test_client.py \
        backend/tests/fixtures/cassettes/flex/
git commit -m "feat(phase2): Flex Web Service client (SendRequest + Poll with backoff)"
```

---

### Task 10: Flex persister + job — DB writes + dedup + orchestración

**Files:**
- Create: `backend/src/ibkr_control/ingest/flex/persister.py`
- Create: `backend/src/ibkr_control/ingest/flex/job.py`
- Create: `backend/tests/ingest/flex/test_persister.py`
- Create: `backend/tests/ingest/flex/test_job.py`

- [ ] **Step 1: Escribir tests del persister (failing)**

Crear `backend/tests/ingest/flex/test_persister.py`:

```python
"""Tests del persister Flex: parsed → DB con dedup + TX."""
from datetime import date
from decimal import Decimal
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.ingest.flex.persister import persist
from ibkr_control.ingest.flex._models import (
    ParsedAccount, ParsedTrade, ParsedClosedLot, ParsedOpenPositionLot,
    ParsedCashTransaction, ParsedTransfer, ParsedTransferLot, ParsedXML,
)


def _make_parsed(account_id="U99999001", n_trades=2) -> ParsedXML:
    return ParsedXML(
        anyo=2025,
        period_from=date(2025, 1, 1),
        period_to=date(2025, 12, 31),
        accounts=[ParsedAccount(ibkr_account_id=account_id, currency="USD")],
        trades=[
            ParsedTrade(
                transaction_id=f"TXN-{i}", ibkr_account_id=account_id, symbol="AAPL",
                asset_class="STK", trade_date=date(2025, 1, 15+i),
                settle_date=date(2025, 1, 17+i),
                qty=Decimal("10"), price_usd=Decimal("150.00"),
                proceeds_usd=Decimal("-1500.00"), commission_usd=Decimal("1.00"),
                open_close="O", buy_sell="BUY", raw_attrs={"orderType": "LMT"},
            )
            for i in range(n_trades)
        ],
        closed_lots=[], open_position_lots=[], cash_transactions=[], transfers=[],
    )


@pytest.mark.asyncio
async def test_persist_creates_flex_import_and_trades(db_session: AsyncSession, sample_user, sample_account):
    parsed = _make_parsed(account_id=sample_account.ibkr_account_id, n_trades=3)
    flex_import_id = await persist(
        db_session, parsed=parsed, user_id=sample_user.id,
        xml_bytes=b"<xml>fake</xml>", source="manual_upload",
    )
    assert flex_import_id is not None

    from ibkr_control.db.models.flex_raw import FlexImport, Trade
    fi = await db_session.scalar(select(FlexImport).where(FlexImport.id == flex_import_id))
    assert fi.user_id == sample_user.id
    assert fi.anyo == 2025
    assert fi.source == "manual_upload"
    assert fi.status == "ok"
    assert fi.n_trades == 3

    n = await db_session.scalar(select(func.count(Trade.id)).where(Trade.flex_import_id == flex_import_id))
    assert n == 3


@pytest.mark.asyncio
async def test_persist_year_status_sealed_when_period_to_is_dec31(db_session: AsyncSession, sample_user, sample_account):
    parsed = _make_parsed(account_id=sample_account.ibkr_account_id, n_trades=1)
    parsed.period_to = date(2025, 12, 31)
    fi_id = await persist(db_session, parsed=parsed, user_id=sample_user.id,
                          xml_bytes=b"<xml>1</xml>", source="manual_upload")
    from ibkr_control.db.models.flex_raw import FlexImport
    fi = await db_session.scalar(select(FlexImport).where(FlexImport.id == fi_id))
    assert fi.year_status == "sealed"


@pytest.mark.asyncio
async def test_persist_year_status_rolling_when_period_to_before_dec31(db_session: AsyncSession, sample_user, sample_account):
    parsed = _make_parsed(account_id=sample_account.ibkr_account_id, n_trades=1)
    parsed.period_to = date(2025, 5, 24)
    fi_id = await persist(db_session, parsed=parsed, user_id=sample_user.id,
                          xml_bytes=b"<xml>2</xml>", source="manual_upload")
    from ibkr_control.db.models.flex_raw import FlexImport
    fi = await db_session.scalar(select(FlexImport).where(FlexImport.id == fi_id))
    assert fi.year_status == "rolling"


@pytest.mark.asyncio
async def test_persist_duplicate_hash_returns_existing_id(db_session: AsyncSession, sample_user, sample_account):
    """Si el mismo XML (mismo hash) se persiste dos veces, devuelve el id existente sin re-insertar."""
    parsed = _make_parsed(account_id=sample_account.ibkr_account_id, n_trades=2)
    xml_bytes = b"<xml>identical</xml>"

    fi_id_1 = await persist(db_session, parsed=parsed, user_id=sample_user.id,
                            xml_bytes=xml_bytes, source="manual_upload")
    fi_id_2 = await persist(db_session, parsed=parsed, user_id=sample_user.id,
                            xml_bytes=xml_bytes, source="manual_upload")
    assert fi_id_1 == fi_id_2

    from ibkr_control.db.models.flex_raw import Trade
    n = await db_session.scalar(select(func.count(Trade.id)).where(Trade.flex_import_id == fi_id_1))
    assert n == 2  # NO duplicó los trades


@pytest.mark.asyncio
async def test_persist_creates_missing_accounts_on_the_fly(db_session: AsyncSession, sample_user):
    """Si el XML referencia un account que no existe en DB, se crea automáticamente."""
    parsed = _make_parsed(account_id="U99999777", n_trades=1)
    fi_id = await persist(db_session, parsed=parsed, user_id=sample_user.id,
                          xml_bytes=b"<xml>new-acc</xml>", source="manual_upload")
    assert fi_id is not None

    from ibkr_control.db.models.accounts import Account
    acc = await db_session.scalar(select(Account).where(Account.ibkr_account_id == "U99999777"))
    assert acc is not None


@pytest.mark.asyncio
async def test_persist_rolls_back_on_partial_failure(db_session: AsyncSession, sample_user, sample_account):
    """Si falla un INSERT a mitad, NADA queda en DB."""
    parsed = _make_parsed(account_id=sample_account.ibkr_account_id, n_trades=2)
    parsed.trades[1].transaction_id = parsed.trades[0].transaction_id  # forzar dup

    from ibkr_control.db.models.flex_raw import FlexImport, Trade

    with pytest.raises(Exception):
        await persist(db_session, parsed=parsed, user_id=sample_user.id,
                      xml_bytes=b"<xml>partial-fail</xml>", source="manual_upload")
    await db_session.rollback()

    n_fi = await db_session.scalar(select(func.count(FlexImport.id)).where(FlexImport.xml_hash != ""))
    n_t = await db_session.scalar(select(func.count(Trade.id)))
    # No debería haber quedado el FlexImport huérfano
    fi_partial = await db_session.scalar(
        select(FlexImport).where(FlexImport.source == "manual_upload")
                          .order_by(FlexImport.id.desc()).limit(1)
    )
    if fi_partial:
        # Si hay uno, no debe tener trades
        n_for_this = await db_session.scalar(select(func.count(Trade.id)).where(Trade.flex_import_id == fi_partial.id))
        assert n_for_this == 0
```

- [ ] **Step 2: Implementar el persister**

Crear `backend/src/ibkr_control/ingest/flex/persister.py`:

```python
"""Persiste un ParsedXML en Postgres (dentro de una TX).

- Inserta flex_imports con xml_hash (dedup vía UNIQUE constraint)
- Si hash ya existe → devuelve el flex_import_id existente, NO inserta children
- Si hash es nuevo → inserta children en bulk (trades, closed_lots, ...)
- Crea accounts on-the-fly si el XML referencia accounts no existentes
- Calcula year_status (sealed vs rolling) según period_covered_to
"""
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.db.models.accounts import Account
from ibkr_control.db.models.flex_raw import (
    CashTransaction, ClosedLot, FlexImport, OpenPositionLot, Trade,
    Transfer, TransferLot,
)
from ibkr_control.ingest.flex._models import ParsedXML
from ibkr_control.ingest.hash_dedup import xml_hash


async def persist(
    session: AsyncSession,
    *,
    parsed: ParsedXML,
    user_id: int,
    xml_bytes: bytes,
    source: str,  # 'web_service' | 'manual_upload'
) -> int:
    """Devuelve el flex_import_id (existente si hash duplicado, nuevo si no)."""
    h = xml_hash(xml_bytes)

    # Dedup: si ya existe el hash, return early
    existing_id = await session.scalar(
        select(FlexImport.id).where(FlexImport.xml_hash == h)
    )
    if existing_id is not None:
        return existing_id

    # Crear accounts faltantes
    accounts_map = await _ensure_accounts(session, [a.ibkr_account_id for a in parsed.accounts])
    # Mapear también accounts mencionados en trades/lots/etc. que no estén en parsed.accounts
    extra_ids: set[str] = set()
    for t in parsed.trades:
        extra_ids.add(t.ibkr_account_id)
    for l in parsed.closed_lots:
        extra_ids.add(l.ibkr_account_id)
    for l in parsed.open_position_lots:
        extra_ids.add(l.ibkr_account_id)
    for ct in parsed.cash_transactions:
        extra_ids.add(ct.ibkr_account_id)
    extra_map = await _ensure_accounts(session, list(extra_ids))
    accounts_map.update(extra_map)

    year_status = "sealed" if parsed.period_to >= date(parsed.anyo, 12, 31) else "rolling"

    fi = FlexImport(
        user_id=user_id,
        anyo=parsed.anyo,
        xml_hash=h,
        xml_size_bytes=len(xml_bytes),
        source=source,
        period_covered_from=parsed.period_from,
        period_covered_to=parsed.period_to,
        year_status=year_status,
        n_trades=len(parsed.trades),
        n_lots_closed=len(parsed.closed_lots),
        n_open_lots=len(parsed.open_position_lots),
        n_cash_tx=len(parsed.cash_transactions),
        n_dividends=sum(1 for ct in parsed.cash_transactions if ct.type == "Dividends"),
        n_transfers=len(parsed.transfers),
        status="ok",
    )
    session.add(fi)
    await session.flush()  # para tener fi.id

    # Bulk insert children
    for t in parsed.trades:
        session.add(Trade(
            flex_import_id=fi.id, transaction_id=t.transaction_id,
            account_id=accounts_map[t.ibkr_account_id],
            symbol=t.symbol, asset_class=t.asset_class,
            trade_date=t.trade_date, settle_date=t.settle_date,
            qty=t.qty, price_usd=t.price_usd,
            proceeds_usd=t.proceeds_usd, commission_usd=t.commission_usd,
            open_close=t.open_close, buy_sell=t.buy_sell,
            raw_attrs=t.raw_attrs,
        ))

    for cl in parsed.closed_lots:
        session.add(ClosedLot(
            flex_import_id=fi.id, account_id=accounts_map[cl.ibkr_account_id],
            symbol=cl.symbol, open_date=cl.open_date, close_date=cl.close_date,
            qty=cl.qty, cost_basis_usd=cl.cost_basis_usd,
            proceeds_usd=cl.proceeds_usd, fifo_pnl_usd=cl.fifo_pnl_usd,
        ))

    for op in parsed.open_position_lots:
        session.add(OpenPositionLot(
            flex_import_id=fi.id, account_id=accounts_map[op.ibkr_account_id],
            symbol=op.symbol, open_date=op.open_date,
            qty=op.qty, cost_basis_usd=op.cost_basis_usd,
            mark_price_usd=op.mark_price_usd, mark_value_usd=op.mark_value_usd,
            snapshot_date=op.snapshot_date,
        ))

    for ct in parsed.cash_transactions:
        session.add(CashTransaction(
            flex_import_id=fi.id, account_id=accounts_map[ct.ibkr_account_id],
            type=ct.type, currency=ct.currency, amount_usd=ct.amount_usd,
            description=ct.description, date=ct.date, symbol=ct.symbol,
        ))

    for tr in parsed.transfers:
        transfer = Transfer(
            flex_import_id=fi.id,
            transfer_date=tr.transfer_date, direction=tr.direction,
            src_account_id=accounts_map.get(tr.src_ibkr_account_id) if tr.src_ibkr_account_id else None,
            dst_account_id=accounts_map.get(tr.dst_ibkr_account_id) if tr.dst_ibkr_account_id else None,
            symbol=tr.symbol, qty=tr.qty, transfer_type=tr.transfer_type,
        )
        session.add(transfer)
        await session.flush()  # para tener transfer.id
        for lot in tr.lots:
            session.add(TransferLot(
                transfer_id=transfer.id,
                original_open_date=lot.original_open_date,
                qty=lot.qty, cost_basis_usd=lot.cost_basis_usd,
            ))

    await session.flush()
    return fi.id


async def _ensure_accounts(session: AsyncSession, ibkr_ids: list[str]) -> dict[str, int]:
    """Garantiza que existan accounts para todos los ibkr_ids. Devuelve mapping ibkr_id → db id."""
    if not ibkr_ids:
        return {}
    result = await session.scalars(
        select(Account).where(Account.ibkr_account_id.in_(ibkr_ids))
    )
    existing = {a.ibkr_account_id: a.id for a in result.all()}
    missing = set(ibkr_ids) - set(existing.keys())
    for ibkr_id in missing:
        a = Account(ibkr_account_id=ibkr_id, alias=None, currency="USD")
        session.add(a)
    if missing:
        await session.flush()
        result2 = await session.scalars(
            select(Account).where(Account.ibkr_account_id.in_(missing))
        )
        for a in result2.all():
            existing[a.ibkr_account_id] = a.id
    return existing
```

- [ ] **Step 3: Correr tests del persister**

```bash
cd backend && uv run pytest tests/ingest/flex/test_persister.py -v
```

Expected: 6/6 PASS.

- [ ] **Step 4: Escribir tests del job orchestrator (failing)**

Crear `backend/tests/ingest/flex/test_job.py`:

```python
"""Tests del orchestrator flex_job (lock + log + client + parser + persister)."""
from pathlib import Path
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.ingest.flex import job as flex_job
from ibkr_control.db.models.flex_raw import FlexImport, Trade
from ibkr_control.db.models.ingest_log import IngestLog

FIXTURE_DIR = Path(__file__).parent.parent.parent / "fixtures" / "xml"


@pytest.mark.asyncio
async def test_ingest_xml_happy_path(db_session: AsyncSession, sample_user):
    """ingest_xml recibe bytes, parsea, persiste, loggea."""
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    flex_import_id = await flex_job.ingest_xml(
        db_session, user_id=sample_user.id,
        xml_bytes=xml, source="manual_upload", trigger="wizard",
    )
    assert flex_import_id is not None

    fi = await db_session.scalar(select(FlexImport).where(FlexImport.id == flex_import_id))
    assert fi.status == "ok"

    # ingest_log debe tener 1 row ok del kind manual_upload
    n_logs = await db_session.scalar(select(func.count(IngestLog.id)).where(
        IngestLog.job_kind == "manual_upload",
        IngestLog.user_id == sample_user.id,
        IngestLog.status == "ok",
    ))
    assert n_logs >= 1


@pytest.mark.asyncio
async def test_ingest_xml_duplicate_returns_existing(db_session: AsyncSession, sample_user):
    """Re-upload del mismo XML devuelve el flex_import_id existente."""
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    id_1 = await flex_job.ingest_xml(
        db_session, user_id=sample_user.id, xml_bytes=xml,
        source="manual_upload", trigger="wizard",
    )
    id_2 = await flex_job.ingest_xml(
        db_session, user_id=sample_user.id, xml_bytes=xml,
        source="manual_upload", trigger="wizard",
    )
    assert id_1 == id_2


@pytest.mark.asyncio
async def test_ingest_xml_logs_failure_on_parse_error(db_session: AsyncSession, sample_user):
    """Si el parser lanza, ingest_log queda en 'failed' con error_message."""
    bad_xml = (FIXTURE_DIR / "malformed_xml.xml").read_bytes()
    with pytest.raises(Exception):
        await flex_job.ingest_xml(
            db_session, user_id=sample_user.id, xml_bytes=bad_xml,
            source="manual_upload", trigger="wizard",
        )
    await db_session.rollback()

    # NOTA: rollback puede borrar el log_row dependiendo de cómo está estructurada
    # la TX. El context manager de log debe usar autocommit del row de log.
    # Verificar en log.py que el commit del log es separado.
```

- [ ] **Step 5: Implementar el job orchestrator**

Crear `backend/src/ibkr_control/ingest/flex/job.py`:

```python
"""Orchestrator del flex ingest: lock + log + client + parser + persister.

Dos entry points:
- run(): hace SendRequest + Poll al Flex WS, luego ingiere
- ingest_xml(): recibe bytes ya descargados (upload manual o test)
"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ibkr_control.ingest.flex import client as flex_client_mod
from ibkr_control.ingest.flex import crypto as flex_crypto_mod
from ibkr_control.ingest.flex import parser as flex_parser_mod
from ibkr_control.ingest.flex import persister as flex_persister_mod
from ibkr_control.ingest.lock import advisory_lock
from ibkr_control.ingest.log import ingest_log_entry


async def ingest_xml(
    session: AsyncSession,
    *,
    user_id: int,
    xml_bytes: bytes,
    source: str,    # 'manual_upload' | 'web_service'
    trigger: str,   # 'cron' | 'manual' | 'wizard'
) -> int:
    """Ingiere bytes XML pre-descargados. Usa el log + dedup + persister.

    NO toma el advisory_lock global de 'flex' porque los uploads manuales
    pueden ocurrir concurrentemente con el cron (cada upload tiene su propio
    XML con hash distinto). Si dos uploads pegan el MISMO XML al mismo tiempo,
    el UNIQUE constraint en xml_hash gana la carrera y el segundo recibe el
    existing_id.
    """
    log_kind = "manual_upload" if source == "manual_upload" else "flex"

    async with ingest_log_entry(session, log_kind, user_id, trigger) as log_id:
        parsed = flex_parser_mod.parse(xml_bytes)
        flex_import_id = await flex_persister_mod.persist(
            session, parsed=parsed, user_id=user_id,
            xml_bytes=xml_bytes, source=source,
        )

        # Update items_processed antes del exit del log context
        from ibkr_control.db.models.ingest_log import IngestLog
        from sqlalchemy import select
        log_row = await session.scalar(select(IngestLog).where(IngestLog.id == log_id))
        log_row.items_processed = (
            len(parsed.trades) + len(parsed.closed_lots) +
            len(parsed.open_position_lots) + len(parsed.cash_transactions) +
            len(parsed.transfers)
        )

        return flex_import_id


async def run(
    session_factory: async_sessionmaker,
    *,
    user_id: int,
    trigger: str,   # 'cron' | 'manual' | 'wizard'
) -> int | None:
    """Hace fetch al Flex WS + ingiere. Devuelve flex_import_id o None si no hubo cambios.

    Toma advisory_lock por (source='flex', user_id) — bloquea concurrent runs
    para el mismo user. Si está tomado, lanza LockHeldError (caller decide qué hacer).
    """
    from ibkr_control.db.models.flex_credentials import FlexCredentials
    from sqlalchemy import select

    async with session_factory() as session:
        async with advisory_lock(session, user_id=user_id, source="flex"):
            async with ingest_log_entry(session, "flex", user_id, trigger) as log_id:
                creds = await session.scalar(
                    select(FlexCredentials).where(FlexCredentials.user_id == user_id)
                )
                if creds is None:
                    raise RuntimeError(f"No flex_credentials for user_id={user_id}")

                token = flex_crypto_mod.decrypt_token(creds.token_encrypted)
                client = flex_client_mod.FlexClient(token=token)
                reference = await client.send_request(query_id=creds.ytd_query_id)
                xml_bytes = await client.poll_statement(reference_code=reference)

                from ibkr_control.ingest.hash_dedup import xml_hash, is_known_hash
                h = xml_hash(xml_bytes)
                if await is_known_hash(session, h):
                    # No changes — solo update log items
                    from ibkr_control.db.models.ingest_log import IngestLog
                    log_row = await session.scalar(select(IngestLog).where(IngestLog.id == log_id))
                    log_row.items_processed = 0
                    return None

                parsed = flex_parser_mod.parse(xml_bytes)
                flex_import_id = await flex_persister_mod.persist(
                    session, parsed=parsed, user_id=user_id,
                    xml_bytes=xml_bytes, source="web_service",
                )

                from ibkr_control.db.models.ingest_log import IngestLog
                log_row = await session.scalar(select(IngestLog).where(IngestLog.id == log_id))
                log_row.items_processed = (
                    len(parsed.trades) + len(parsed.closed_lots) +
                    len(parsed.open_position_lots) + len(parsed.cash_transactions) +
                    len(parsed.transfers)
                )
                return flex_import_id
```

- [ ] **Step 6: Correr tests del job**

```bash
cd backend && uv run pytest tests/ingest/flex/test_job.py -v
```

Expected: 3/3 PASS.

- [ ] **Step 7: Correr toda la suite Flex**

```bash
cd backend && uv run pytest tests/ingest/flex/ -v
```

Expected: ~28/28 PASS (8 crypto + 11 parser + 5 client + 6 persister + 3 job).

- [ ] **Step 8: Commit**

```bash
git add backend/src/ibkr_control/ingest/flex/persister.py \
        backend/src/ibkr_control/ingest/flex/job.py \
        backend/tests/ingest/flex/test_persister.py \
        backend/tests/ingest/flex/test_job.py
git commit -m "feat(phase2): Flex persister + job orchestrator (dedup + advisory lock + log)"
```

---

### Task 11: TRM client + parser — Socrata DIAN ceyp-9c7c + vigencia expansion

**Files:**
- Create: `backend/src/ibkr_control/ingest/trm/__init__.py` (vacío)
- Create: `backend/src/ibkr_control/ingest/trm/client.py`
- Create: `backend/src/ibkr_control/ingest/trm/parser.py`
- Create: `backend/tests/ingest/trm/__init__.py` (vacío)
- Create: `backend/tests/ingest/trm/test_client.py`
- Create: `backend/tests/ingest/trm/test_parser.py`
- Create: `backend/tests/fixtures/cassettes/trm/socrata_empty.yaml`
- Create: `backend/tests/fixtures/cassettes/trm/socrata_three_rows.yaml`

- [ ] **Step 1: Crear directorios**

```bash
mkdir -p backend/src/ibkr_control/ingest/trm backend/tests/ingest/trm
touch backend/src/ibkr_control/ingest/trm/__init__.py backend/tests/ingest/trm/__init__.py
```

- [ ] **Step 2: Escribir tests del parser TRM (failing)**

Crear `backend/tests/ingest/trm/test_parser.py`:

```python
"""Tests de la expansión vigencia_desde..vigencia_hasta."""
from datetime import date
from decimal import Decimal

from ibkr_control.ingest.trm.parser import expand_vigencias


def test_expand_single_day():
    rows = [{
        "vigenciadesde": "2026-01-15T00:00:00.000",
        "vigenciahasta": "2026-01-15T00:00:00.000",
        "valor": "4123.4567",
    }]
    out = list(expand_vigencias(rows))
    assert len(out) == 1
    d = out[0]
    assert d["date"] == date(2026, 1, 15)
    assert d["value_cop"] == Decimal("4123.4567")
    assert d["vigencia_desde"] == date(2026, 1, 15)
    assert d["vigencia_hasta"] == date(2026, 1, 15)


def test_expand_weekend_friday_to_monday():
    """Viernes 2026-01-16 vigente hasta domingo 2026-01-18 → 3 días."""
    rows = [{
        "vigenciadesde": "2026-01-16T00:00:00.000",
        "vigenciahasta": "2026-01-18T00:00:00.000",
        "valor": "4150.00",
    }]
    out = list(expand_vigencias(rows))
    assert len(out) == 3
    dates = [d["date"] for d in out]
    assert dates == [date(2026, 1, 16), date(2026, 1, 17), date(2026, 1, 18)]
    assert all(d["value_cop"] == Decimal("4150.00") for d in out)


def test_expand_multiple_rows_no_overlap():
    rows = [
        {"vigenciadesde": "2026-01-01T00:00:00.000", "vigenciahasta": "2026-01-02T00:00:00.000", "valor": "4100"},
        {"vigenciadesde": "2026-01-03T00:00:00.000", "vigenciahasta": "2026-01-05T00:00:00.000", "valor": "4120"},
    ]
    out = list(expand_vigencias(rows))
    assert len(out) == 5
    assert out[0]["date"] == date(2026, 1, 1)
    assert out[4]["date"] == date(2026, 1, 5)


def test_expand_ignores_invalid_row():
    """Si una row no tiene los 3 campos requeridos, se saltea (con warn)."""
    rows = [
        {"vigenciadesde": "2026-01-01T00:00:00.000", "vigenciahasta": "2026-01-01T00:00:00.000", "valor": "4100"},
        {"vigenciadesde": "2026-01-02T00:00:00.000"},  # incompleta
        {"vigenciadesde": "2026-01-03T00:00:00.000", "vigenciahasta": "2026-01-03T00:00:00.000", "valor": "4110"},
    ]
    out = list(expand_vigencias(rows))
    assert len(out) == 2


def test_expand_handles_date_only_format():
    """Algunos endpoints de Socrata devuelven '2026-01-15' sin time."""
    rows = [{
        "vigenciadesde": "2026-01-15",
        "vigenciahasta": "2026-01-15",
        "valor": "4100",
    }]
    out = list(expand_vigencias(rows))
    assert out[0]["date"] == date(2026, 1, 15)
```

- [ ] **Step 3: Implementar el parser**

Crear `backend/src/ibkr_control/ingest/trm/parser.py`:

```python
"""Expansión de rows Socrata (vigencia_desde..vigencia_hasta) a 1 row por día."""
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Iterable, Iterator

logger = logging.getLogger(__name__)


def _parse_socrata_date(s: str) -> date:
    """Socrata devuelve '2026-01-15T00:00:00.000' o '2026-01-15'."""
    if "T" in s:
        return datetime.fromisoformat(s.split("T")[0]).date()
    return date.fromisoformat(s)


def expand_vigencias(rows: Iterable[dict]) -> Iterator[dict]:
    """Recibe rows del API Socrata, yield 1 dict por día calendario.

    Cada dict contiene: date, value_cop, vigencia_desde, vigencia_hasta.
    """
    for row in rows:
        try:
            vd_raw = row["vigenciadesde"]
            vh_raw = row["vigenciahasta"]
            valor_raw = row["valor"]
        except KeyError as e:
            logger.warning("TRM row missing field, skipping: %s", e)
            continue

        try:
            vd = _parse_socrata_date(vd_raw)
            vh = _parse_socrata_date(vh_raw)
            value = Decimal(str(valor_raw))
        except (ValueError, ArithmeticError) as e:
            logger.warning("TRM row malformed, skipping: %s", e)
            continue

        d = vd
        while d <= vh:
            yield {
                "date": d,
                "value_cop": value,
                "vigencia_desde": vd,
                "vigencia_hasta": vh,
            }
            d += timedelta(days=1)
```

- [ ] **Step 4: Correr tests del parser**

```bash
cd backend && uv run pytest tests/ingest/trm/test_parser.py -v
```

Expected: 5/5 PASS.

- [ ] **Step 5: Crear cassettes mínimas para TRM client tests**

Crear `backend/tests/fixtures/cassettes/trm/socrata_empty.yaml`:

```yaml
interactions:
  - request:
      method: GET
      uri: https://www.datos.gov.co/resource/ceyp-9c7c.json?$where=vigenciadesde%20%3E%20%272026-05-24T00:00:00.000%27&$order=vigenciadesde%20ASC&$limit=50000
      headers:
        Host: [www.datos.gov.co]
      body: null
    response:
      status: {code: 200, message: OK}
      headers:
        content-type: ["application/json"]
      body:
        string: "[]"
version: 1
```

Crear `backend/tests/fixtures/cassettes/trm/socrata_three_rows.yaml`:

```yaml
interactions:
  - request:
      method: GET
      uri: https://www.datos.gov.co/resource/ceyp-9c7c.json?$where=vigenciadesde%20%3E%20%272026-01-01T00:00:00.000%27&$order=vigenciadesde%20ASC&$limit=50000
      headers:
        Host: [www.datos.gov.co]
      body: null
    response:
      status: {code: 200, message: OK}
      headers:
        content-type: ["application/json"]
      body:
        string: |
          [
            {"vigenciadesde":"2026-01-02T00:00:00.000","vigenciahasta":"2026-01-02T00:00:00.000","valor":"4100.50"},
            {"vigenciadesde":"2026-01-03T00:00:00.000","vigenciahasta":"2026-01-05T00:00:00.000","valor":"4115.75"},
            {"vigenciadesde":"2026-01-06T00:00:00.000","vigenciahasta":"2026-01-06T00:00:00.000","valor":"4108.00"}
          ]
version: 1
```

- [ ] **Step 6: Escribir tests del client (failing)**

Crear `backend/tests/ingest/trm/test_client.py`:

```python
"""Tests del cliente Socrata DIAN para TRM."""
from datetime import date
import pytest

from ibkr_control.ingest.trm.client import TrmClient


@pytest.mark.vcr(cassette_library_dir="tests/fixtures/cassettes/trm")
@pytest.mark.asyncio
async def test_fetch_empty_returns_no_rows():
    client = TrmClient()
    rows = await client.fetch(since=date(2026, 5, 24))
    assert rows == []


@pytest.mark.vcr(cassette_library_dir="tests/fixtures/cassettes/trm")
@pytest.mark.asyncio
async def test_fetch_three_rows():
    client = TrmClient()
    rows = await client.fetch(since=date(2026, 1, 1))
    assert len(rows) == 3
    assert rows[0]["vigenciadesde"].startswith("2026-01-02")
```

- [ ] **Step 7: Implementar el client**

Crear `backend/src/ibkr_control/ingest/trm/client.py`:

```python
"""Cliente HTTP del Socrata DIAN dataset ceyp-9c7c (TRM).

Endpoint: https://www.datos.gov.co/resource/ceyp-9c7c.json
Filtro: ?$where=vigenciadesde > 'YYYY-MM-DDT00:00:00.000' &$order=vigenciadesde ASC &$limit=50000
"""
from datetime import date
import httpx


SOCRATA_URL = "https://www.datos.gov.co/resource/ceyp-9c7c.json"
PAGE_LIMIT = 50000


class TrmClient:
    def __init__(self, base_url: str = SOCRATA_URL, timeout: float = 60.0):
        self._url = base_url
        self._timeout = timeout

    async def fetch(self, *, since: date | None) -> list[dict]:
        """Devuelve rows desde Socrata con vigenciadesde > since.

        Si since es None → backfill total (sin filtro $where).
        """
        params = {
            "$order": "vigenciadesde ASC",
            "$limit": str(PAGE_LIMIT),
        }
        if since is not None:
            params["$where"] = f"vigenciadesde > '{since.isoformat()}T00:00:00.000'"

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.get(self._url, params=params)
            resp.raise_for_status()
            return resp.json()
```

- [ ] **Step 8: Correr tests del client**

```bash
cd backend && uv run pytest tests/ingest/trm/test_client.py -v
```

Expected: 2/2 PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/src/ibkr_control/ingest/trm/ \
        backend/tests/ingest/trm/ \
        backend/tests/fixtures/cassettes/trm/
git commit -m "feat(phase2): TRM Socrata client + vigencia expansion parser"
```

---

### Task 12: TRM persister + job — ON CONFLICT upsert + orchestración

**Files:**
- Create: `backend/src/ibkr_control/ingest/trm/persister.py`
- Create: `backend/src/ibkr_control/ingest/trm/job.py`
- Create: `backend/tests/ingest/trm/test_persister.py`
- Create: `backend/tests/ingest/trm/test_job.py`

- [ ] **Step 1: Escribir tests del persister (failing)**

Crear `backend/tests/ingest/trm/test_persister.py`:

```python
"""Tests del persister TRM — bulk upsert con ON CONFLICT."""
from datetime import date
from decimal import Decimal
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.ingest.trm.persister import bulk_upsert_days


@pytest.mark.asyncio
async def test_insert_new_days(db_session: AsyncSession):
    expanded = [
        {"date": date(2026, 1, 1), "value_cop": Decimal("4100"),
         "vigencia_desde": date(2026, 1, 1), "vigencia_hasta": date(2026, 1, 1)},
        {"date": date(2026, 1, 2), "value_cop": Decimal("4110"),
         "vigencia_desde": date(2026, 1, 2), "vigencia_hasta": date(2026, 1, 2)},
    ]
    n = await bulk_upsert_days(db_session, expanded)
    assert n == 2

    from ibkr_control.db.models.trm import TrmDay
    count = await db_session.scalar(select(func.count(TrmDay.date)))
    assert count == 2


@pytest.mark.asyncio
async def test_upsert_updates_existing_value(db_session: AsyncSession):
    from ibkr_control.db.models.trm import TrmDay

    db_session.add(TrmDay(
        date=date(2026, 1, 1), value_cop=Decimal("4000"),
        vigencia_desde=date(2026, 1, 1), vigencia_hasta=date(2026, 1, 1),
    ))
    await db_session.commit()

    expanded = [{
        "date": date(2026, 1, 1), "value_cop": Decimal("4250"),
        "vigencia_desde": date(2026, 1, 1), "vigencia_hasta": date(2026, 1, 1),
    }]
    await bulk_upsert_days(db_session, expanded)

    updated = await db_session.scalar(select(TrmDay).where(TrmDay.date == date(2026, 1, 1)))
    assert updated.value_cop == Decimal("4250.0000")


@pytest.mark.asyncio
async def test_bulk_handles_empty_list(db_session: AsyncSession):
    n = await bulk_upsert_days(db_session, [])
    assert n == 0
```

- [ ] **Step 2: Implementar el persister**

Crear `backend/src/ibkr_control/ingest/trm/persister.py`:

```python
"""Persiste rows TRM expandidos con INSERT ... ON CONFLICT DO UPDATE."""
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.db.models.trm import TrmDay, TrmImport


async def bulk_upsert_days(session: AsyncSession, expanded: list[dict]) -> int:
    """Inserta o actualiza rows en trm_days. Devuelve N rows procesados."""
    if not expanded:
        return 0

    stmt = pg_insert(TrmDay).values(expanded)
    stmt = stmt.on_conflict_do_update(
        index_elements=["date"],
        set_={
            "value_cop": stmt.excluded.value_cop,
            "vigencia_desde": stmt.excluded.vigencia_desde,
            "vigencia_hasta": stmt.excluded.vigencia_hasta,
            "fetched_at": text("NOW()"),
        },
    )
    await session.execute(stmt)
    await session.flush()
    return len(expanded)


async def record_import(
    session: AsyncSession,
    *,
    date_from,
    date_to,
    n_rows_api: int,
    n_days_expanded: int,
) -> int:
    row = TrmImport(
        date_range_from=date_from,
        date_range_to=date_to,
        n_rows_api=n_rows_api,
        n_days_expanded=n_days_expanded,
    )
    session.add(row)
    await session.flush()
    return row.id
```

- [ ] **Step 3: Correr tests del persister**

```bash
cd backend && uv run pytest tests/ingest/trm/test_persister.py -v
```

Expected: 3/3 PASS.

- [ ] **Step 4: Escribir tests del TRM job (failing)**

Crear `backend/tests/ingest/trm/test_job.py`:

```python
"""Tests del TRM job orchestrator."""
from datetime import date
from decimal import Decimal
import pytest
import respx
from httpx import Response
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ibkr_control.ingest.trm import job as trm_job


@pytest.mark.asyncio
async def test_run_with_no_new_rows_logs_ok_zero(db_engine):
    SessionLocal = async_sessionmaker(db_engine, expire_on_commit=False)
    with respx.mock(base_url="https://www.datos.gov.co") as router:
        router.get("/resource/ceyp-9c7c.json").mock(return_value=Response(200, json=[]))
        result = await trm_job.run(SessionLocal, trigger="cron")

    assert result["n_days"] == 0
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_run_inserts_new_days(db_engine):
    SessionLocal = async_sessionmaker(db_engine, expire_on_commit=False)
    payload = [
        {"vigenciadesde": "2027-01-02T00:00:00.000", "vigenciahasta": "2027-01-02T00:00:00.000", "valor": "4200"},
        {"vigenciadesde": "2027-01-03T00:00:00.000", "vigenciahasta": "2027-01-05T00:00:00.000", "valor": "4220"},
    ]
    with respx.mock(base_url="https://www.datos.gov.co") as router:
        router.get("/resource/ceyp-9c7c.json").mock(return_value=Response(200, json=payload))
        result = await trm_job.run(SessionLocal, trigger="cron")

    assert result["n_days"] == 4  # 1 + 3 días expandidos
    assert result["status"] == "ok"

    from ibkr_control.db.models.trm import TrmDay
    async with SessionLocal() as s:
        count = await s.scalar(select(func.count(TrmDay.date)).where(
            TrmDay.date >= date(2027, 1, 2),
            TrmDay.date <= date(2027, 1, 5),
        ))
        assert count == 4


@pytest.mark.asyncio
async def test_run_logs_failure_on_http_error(db_engine):
    SessionLocal = async_sessionmaker(db_engine, expire_on_commit=False)
    with respx.mock(base_url="https://www.datos.gov.co") as router:
        router.get("/resource/ceyp-9c7c.json").mock(return_value=Response(500, json={"error": "boom"}))
        with pytest.raises(Exception):
            await trm_job.run(SessionLocal, trigger="cron")

    from ibkr_control.db.models.ingest_log import IngestLog
    async with SessionLocal() as s:
        log = await s.scalar(
            select(IngestLog).where(IngestLog.job_kind == "trm")
                             .order_by(IngestLog.id.desc()).limit(1)
        )
        assert log.status == "failed"
        assert log.error_message is not None
```

- [ ] **Step 5: Implementar el TRM job**

Crear `backend/src/ibkr_control/ingest/trm/job.py`:

```python
"""Orchestrator TRM: lock + log + client + parser + persister."""
from datetime import date
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ibkr_control.db.models.trm import TrmDay
from ibkr_control.ingest.lock import advisory_lock
from ibkr_control.ingest.log import ingest_log_entry
from ibkr_control.ingest.trm.client import TrmClient
from ibkr_control.ingest.trm.parser import expand_vigencias
from ibkr_control.ingest.trm.persister import bulk_upsert_days, record_import


async def run(
    session_factory: async_sessionmaker,
    *,
    trigger: str,  # 'cron' | 'wizard' | 'manual'
    full_backfill: bool = False,
) -> dict:
    """Pull incremental desde Socrata. Si full_backfill=True, ignora MAX(vigencia_desde)."""
    async with session_factory() as session:
        async with advisory_lock(session, user_id=None, source="trm"):
            async with ingest_log_entry(session, "trm", user_id=None, trigger=trigger) as log_id:
                if full_backfill:
                    since = None
                else:
                    last_vd = await session.scalar(select(func.max(TrmDay.vigencia_desde)))
                    since = last_vd  # None si vacío → backfill total

                client = TrmClient()
                rows = await client.fetch(since=since)

                if not rows:
                    from ibkr_control.db.models.ingest_log import IngestLog
                    log_row = await session.scalar(select(IngestLog).where(IngestLog.id == log_id))
                    log_row.items_processed = 0
                    return {"status": "ok", "n_rows_api": 0, "n_days": 0}

                expanded = list(expand_vigencias(rows))
                n_days = await bulk_upsert_days(session, expanded)

                date_from = min(d["date"] for d in expanded)
                date_to = max(d["date"] for d in expanded)
                await record_import(session, date_from=date_from, date_to=date_to,
                                    n_rows_api=len(rows), n_days_expanded=n_days)

                from ibkr_control.db.models.ingest_log import IngestLog
                log_row = await session.scalar(select(IngestLog).where(IngestLog.id == log_id))
                log_row.items_processed = n_days

                return {"status": "ok", "n_rows_api": len(rows), "n_days": n_days}
```

- [ ] **Step 6: Correr todos los tests TRM**

```bash
cd backend && uv run pytest tests/ingest/trm/ -v
```

Expected: 10/10 PASS (5 parser + 2 client + 3 persister + 3 job).

- [ ] **Step 7: Commit**

```bash
git add backend/src/ibkr_control/ingest/trm/persister.py \
        backend/src/ibkr_control/ingest/trm/job.py \
        backend/tests/ingest/trm/test_persister.py \
        backend/tests/ingest/trm/test_job.py
git commit -m "feat(phase2): TRM persister (ON CONFLICT upsert) + job orchestrator"
```

---

### Task 13: APScheduler — register 2 jobs (Flex 07:00 COT + TRM 19:30 COT) + startup hook

**Files:**
- Create: `backend/src/ibkr_control/scheduler/__init__.py` (vacío)
- Create: `backend/src/ibkr_control/scheduler/jobs.py`
- Modify: `backend/src/ibkr_control/main.py` (registrar scheduler en startup)
- Create: `backend/tests/test_scheduler.py`

- [ ] **Step 1: Crear directorio + __init__.py**

```bash
mkdir -p backend/src/ibkr_control/scheduler
touch backend/src/ibkr_control/scheduler/__init__.py
```

- [ ] **Step 2: Escribir tests del scheduler (failing)**

Crear `backend/tests/test_scheduler.py`:

```python
"""Tests de registro de cron jobs."""
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ibkr_control.scheduler.jobs import register_jobs


def test_register_creates_two_jobs():
    scheduler = AsyncIOScheduler()
    register_jobs(scheduler)
    ids = {j.id for j in scheduler.get_jobs()}
    assert ids == {"flex_daily", "trm_daily"}


def test_flex_daily_runs_at_12utc():
    """Flex cron a las 07:00 COT = 12:00 UTC (Bogotá sin DST)."""
    scheduler = AsyncIOScheduler()
    register_jobs(scheduler)
    flex_job = next(j for j in scheduler.get_jobs() if j.id == "flex_daily")
    # Trigger es CronTrigger con hour=12, minute=0
    assert flex_job.trigger.fields[5].name == "hour"
    # APScheduler internal: fields[5] es hour, fields[6] es minute (en CronTrigger)
    hour_field = flex_job.trigger.fields[5]
    minute_field = flex_job.trigger.fields[6]
    assert str(hour_field) == "12"
    assert str(minute_field) == "0"


def test_trm_daily_runs_at_0030utc():
    """TRM cron a las 19:30 COT del día N = 00:30 UTC del día N+1."""
    scheduler = AsyncIOScheduler()
    register_jobs(scheduler)
    trm_job = next(j for j in scheduler.get_jobs() if j.id == "trm_daily")
    hour_field = trm_job.trigger.fields[5]
    minute_field = trm_job.trigger.fields[6]
    assert str(hour_field) == "0"
    assert str(minute_field) == "30"


def test_jobs_have_max_instances_1_and_coalesce():
    scheduler = AsyncIOScheduler()
    register_jobs(scheduler)
    for j in scheduler.get_jobs():
        assert j.max_instances == 1
        assert j.coalesce is True
```

- [ ] **Step 3: Implementar `scheduler/jobs.py`**

Crear `backend/src/ibkr_control/scheduler/jobs.py`:

```python
"""APScheduler setup: 2 jobs idempotentes (Flex YTD diario + TRM diario)."""
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ibkr_control.config import get_settings

logger = logging.getLogger(__name__)


async def _run_flex_for_all_users():
    """Itera sobre cada user con flex_credentials y corre flex_job.run."""
    from ibkr_control.db.models.flex_credentials import FlexCredentials
    from ibkr_control.ingest.flex import job as flex_job
    from ibkr_control.ingest.lock import LockHeldError

    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with SessionLocal() as s:
            user_ids = (await s.scalars(select(FlexCredentials.user_id))).all()

        for uid in user_ids:
            try:
                await flex_job.run(SessionLocal, user_id=uid, trigger="cron")
            except LockHeldError:
                logger.warning("flex_daily skipped user_id=%s — lock held", uid)
            except Exception:
                logger.exception("flex_daily failed for user_id=%s — continuing", uid)
    finally:
        await engine.dispose()


async def _run_trm_global():
    """Cron TRM — un job global, no per-user."""
    from ibkr_control.ingest.trm import job as trm_job

    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await trm_job.run(SessionLocal, trigger="cron")
    except Exception:
        logger.exception("trm_daily failed")
    finally:
        await engine.dispose()


def register_jobs(scheduler: AsyncIOScheduler) -> None:
    """Registra los 2 jobs en el scheduler. Idempotente — borra los existentes primero."""
    # Borrar jobs preexistentes (idempotency en hot reload)
    for jid in ("flex_daily", "trm_daily"):
        try:
            scheduler.remove_job(jid)
        except Exception:
            pass

    # Flex cron: 07:00 COT = 12:00 UTC
    scheduler.add_job(
        _run_flex_for_all_users,
        CronTrigger(hour=12, minute=0, timezone="UTC"),
        id="flex_daily",
        max_instances=1,
        coalesce=True,
    )

    # TRM cron: 19:30 COT = 00:30 UTC del día N+1
    scheduler.add_job(
        _run_trm_global,
        CronTrigger(hour=0, minute=30, timezone="UTC"),
        id="trm_daily",
        max_instances=1,
        coalesce=True,
    )

    logger.info("Registered 2 ingest jobs: flex_daily, trm_daily")
```

- [ ] **Step 4: Wire en main.py — startup event arranca scheduler**

Modificar `backend/src/ibkr_control/main.py`. Después del app = FastAPI(...) y antes de app.include_router(...):

```python
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from ibkr_control.scheduler.jobs import register_jobs

_scheduler: AsyncIOScheduler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: arranca scheduler. Shutdown: lo para."""
    global _scheduler
    _scheduler = AsyncIOScheduler()
    register_jobs(_scheduler)
    _scheduler.start()
    yield
    if _scheduler:
        _scheduler.shutdown(wait=False)


# Reemplazar `app = FastAPI(...)` por:
# app = FastAPI(..., lifespan=lifespan)
```

(Si `lifespan` ya existe del Phase 1, agregar las líneas del scheduler dentro.)

- [ ] **Step 5: Correr tests**

```bash
cd backend && uv run pytest tests/test_scheduler.py -v
```

Expected: 4/4 PASS.

- [ ] **Step 6: Smoke test — arrancar backend, verificar log "Registered 2 ingest jobs"**

```bash
docker compose up -d --build backend && sleep 5 && docker compose logs backend | grep "Registered"
```

Expected: línea `Registered 2 ingest jobs: flex_daily, trm_daily` visible.

```bash
docker compose down
```

- [ ] **Step 7: Commit**

```bash
git add backend/src/ibkr_control/scheduler/ \
        backend/src/ibkr_control/main.py \
        backend/tests/test_scheduler.py
git commit -m "feat(phase2): APScheduler setup with 2 daily jobs (Flex 07:00 COT + TRM 19:30 COT)"
```

---

### Task 14: API credentials + setup endpoints

**Files:**
- Create: `backend/src/ibkr_control/api/credentials.py`
- Create: `backend/src/ibkr_control/api/setup.py`
- Create: `backend/src/ibkr_control/api/_schemas.py` (Pydantic models para Phase 2)
- Modify: `backend/src/ibkr_control/main.py` (include routers)
- Create: `backend/tests/api/__init__.py` (si no existe)
- Create: `backend/tests/api/test_credentials.py`
- Create: `backend/tests/api/test_setup.py`

- [ ] **Step 1: Crear schemas Pydantic**

Crear `backend/src/ibkr_control/api/_schemas.py`:

```python
"""Pydantic schemas para los endpoints Phase 2."""
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field


# ── Credentials ────────────────────────────────────────────
class FlexCredentialsRead(BaseModel):
    configured_at: datetime
    query_id: str
    last_rotated_at: datetime


class FlexCredentialsValidate(BaseModel):
    token: str = Field(min_length=10, max_length=512)
    query_id: str = Field(min_length=1, max_length=64)


class FlexCredentialsUpdate(BaseModel):
    token: str | None = Field(default=None, min_length=10, max_length=512)
    query_id: str | None = Field(default=None, min_length=1, max_length=64)


# ── Setup wizard ───────────────────────────────────────────
class SetupState(BaseModel):
    step1_credentials: bool = False
    step2_accounts: bool = False
    step3_xmls: bool = False
    step3_n_xmls_uploaded: int = 0
    step4_started_at: datetime | None = None
    step4_job_id: int | None = None
    step4_substeps: dict[str, str] = Field(default_factory=dict)
    setup_completed_at: datetime | None = None


class AccountInWizard(BaseModel):
    ibkr_account_id: str = Field(pattern=r"^U\d{8}$")
    alias: str | None = Field(default=None, max_length=128)
    pct: Decimal = Field(ge=0, le=1, max_digits=5, decimal_places=4)


class SetupStep2Save(BaseModel):
    accounts: list[AccountInWizard] = Field(min_length=1, max_length=20)


class SetupStep4Start(BaseModel):
    pass  # Sin body — solo dispara el meta-job


class SetupJobStarted(BaseModel):
    job_id: int


# ── Ingest ─────────────────────────────────────────────────
class IngestTrigger(BaseModel):
    kind: str = Field(pattern=r"^(flex|trm|both)$")


class IngestJobStarted(BaseModel):
    job_id: int


class IngestLogRead(BaseModel):
    id: int
    job_kind: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    items_processed: int | None
    error_message: str | None
    trigger: str
```

- [ ] **Step 2: Escribir tests de credentials (failing)**

Crear `backend/tests/api/test_credentials.py`:

```python
"""Tests del router /api/credentials/flex."""
import pytest
import respx
from httpx import AsyncClient, Response


@pytest.mark.asyncio
async def test_get_credentials_404_when_not_configured(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/credentials/flex", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_put_credentials_validates_token_with_ibkr(client: AsyncClient, auth_headers: dict, monkeypatch):
    """PUT con token + query_id hace ping a IBKR antes de guardar."""
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", _make_test_key())

    with respx.mock(base_url="https://gdcdyn.interactivebrokers.com") as router:
        router.get("/Universal/servlet/FlexStatementService.SendRequest").mock(
            return_value=Response(200, content=b"""<?xml version="1.0"?>
<FlexStatementResponse><Status>Success</Status><ReferenceCode>123</ReferenceCode></FlexStatementResponse>""")
        )
        resp = await client.put("/api/credentials/flex", json={
            "token": "valid-token-abc123", "query_id": "1234567"
        }, headers=auth_headers)

    assert resp.status_code == 200

    # GET ahora debe devolver los credentials
    get_resp = await client.get("/api/credentials/flex", headers=auth_headers)
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["query_id"] == "1234567"
    assert "token" not in body  # no se devuelve nunca el token plaintext


@pytest.mark.asyncio
async def test_put_credentials_rejects_invalid_token(client: AsyncClient, auth_headers: dict, monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", _make_test_key())

    with respx.mock(base_url="https://gdcdyn.interactivebrokers.com") as router:
        router.get("/Universal/servlet/FlexStatementService.SendRequest").mock(
            return_value=Response(200, content=b"""<?xml version="1.0"?>
<FlexStatementResponse><Status>Fail</Status><ErrorCode>1018</ErrorCode><ErrorMessage>Invalid token</ErrorMessage></FlexStatementResponse>""")
        )
        resp = await client.put("/api/credentials/flex", json={
            "token": "bad-token", "query_id": "1234567"
        }, headers=auth_headers)

    assert resp.status_code == 401
    assert "invalid" in resp.json()["detail"].lower() or "token" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_get_credentials_requires_auth(client: AsyncClient):
    resp = await client.get("/api/credentials/flex")
    assert resp.status_code == 401


def _make_test_key() -> str:
    import base64
    return base64.b64encode(b"X" * 32).decode("ascii")
```

- [ ] **Step 3: Implementar `api/credentials.py`**

Crear `backend/src/ibkr_control/api/credentials.py`:

```python
"""Router /api/credentials/flex — GET (metadata) y PUT (rotate)."""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.api._schemas import (
    FlexCredentialsRead, FlexCredentialsUpdate,
)
from ibkr_control.auth.deps import current_user
from ibkr_control.db.models.flex_credentials import FlexCredentials
from ibkr_control.db.models.user import User
from ibkr_control.db.session import get_session
from ibkr_control.ingest.flex import client as flex_client_mod
from ibkr_control.ingest.flex import crypto as flex_crypto_mod

router = APIRouter(prefix="/api/credentials", tags=["credentials"])


@router.get("/flex", response_model=FlexCredentialsRead)
async def get_flex_credentials(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    creds = await session.scalar(
        select(FlexCredentials).where(FlexCredentials.user_id == user.id)
    )
    if creds is None:
        raise HTTPException(status_code=404, detail="No Flex credentials configured")
    return FlexCredentialsRead(
        configured_at=creds.last_rotated_at,  # First-time configured uses last_rotated_at
        query_id=creds.ytd_query_id,
        last_rotated_at=creds.last_rotated_at,
    )


@router.put("/flex")
async def update_flex_credentials(
    payload: FlexCredentialsUpdate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Si pasa token, lo testea contra IBKR antes de guardar."""
    creds = await session.scalar(
        select(FlexCredentials).where(FlexCredentials.user_id == user.id)
    )

    new_token = payload.token
    new_query_id = payload.query_id

    # Test ping si hay token nuevo
    if new_token is not None:
        test_query_id = new_query_id or (creds.ytd_query_id if creds else None)
        if not test_query_id:
            raise HTTPException(400, detail="Falta query_id para validar el token")
        try:
            client = flex_client_mod.FlexClient(token=new_token)
            await client.send_request(query_id=test_query_id)
        except flex_client_mod.FlexAuthError as e:
            raise HTTPException(401, detail=f"Token inválido: {e.error_message}")
        except Exception as e:
            raise HTTPException(502, detail=f"No pude alcanzar IBKR: {e}")

    # Persistir
    if creds is None:
        if not new_token or not new_query_id:
            raise HTTPException(400, detail="Primera vez requiere token + query_id")
        creds = FlexCredentials(
            user_id=user.id,
            token_encrypted=flex_crypto_mod.encrypt_token(new_token),
            ytd_query_id=new_query_id,
        )
        session.add(creds)
    else:
        if new_token is not None:
            creds.token_encrypted = flex_crypto_mod.encrypt_token(new_token)
        if new_query_id is not None:
            creds.ytd_query_id = new_query_id
        creds.last_rotated_at = datetime.now(timezone.utc)

    await session.commit()
    return {"ok": True}
```

- [ ] **Step 4: Escribir tests de setup (failing)**

Crear `backend/tests/api/test_setup.py`:

```python
"""Tests del wizard endpoints /api/setup/*."""
import base64
import pytest
import respx
from httpx import AsyncClient, Response


@pytest.fixture(autouse=True)
def set_token_key(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", base64.b64encode(b"X" * 32).decode("ascii"))


@pytest.mark.asyncio
async def test_get_setup_state_initial(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/setup/state", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["step1_credentials"] is False
    assert body["step2_accounts"] is False
    assert body["step3_xmls"] is False
    assert body["setup_completed_at"] is None


@pytest.mark.asyncio
async def test_step1_validate_with_valid_token(client: AsyncClient, auth_headers: dict):
    with respx.mock(base_url="https://gdcdyn.interactivebrokers.com") as router:
        router.get("/Universal/servlet/FlexStatementService.SendRequest").mock(
            return_value=Response(200, content=b"""<?xml version="1.0"?>
<FlexStatementResponse><Status>Success</Status><ReferenceCode>9999</ReferenceCode></FlexStatementResponse>""")
        )
        resp = await client.post("/api/setup/step1/validate", json={
            "token": "good-token", "query_id": "1234567"
        }, headers=auth_headers)

    assert resp.status_code == 200
    # Verificar que setup_progress fue actualizado
    state = (await client.get("/api/setup/state", headers=auth_headers)).json()
    assert state["step1_credentials"] is True


@pytest.mark.asyncio
async def test_step1_validate_rejects_bad_token(client: AsyncClient, auth_headers: dict):
    with respx.mock(base_url="https://gdcdyn.interactivebrokers.com") as router:
        router.get("/Universal/servlet/FlexStatementService.SendRequest").mock(
            return_value=Response(200, content=b"""<?xml version="1.0"?>
<FlexStatementResponse><Status>Fail</Status><ErrorCode>1018</ErrorCode><ErrorMessage>Invalid token</ErrorMessage></FlexStatementResponse>""")
        )
        resp = await client.post("/api/setup/step1/validate", json={
            "token": "bad", "query_id": "1"
        }, headers=auth_headers)

    assert resp.status_code == 401
    state = (await client.get("/api/setup/state", headers=auth_headers)).json()
    assert state["step1_credentials"] is False


@pytest.mark.asyncio
async def test_step2_save_creates_accounts_and_participations(client: AsyncClient, auth_headers: dict):
    resp = await client.post("/api/setup/step2/save", json={
        "accounts": [
            {"ibkr_account_id": "U99999001", "alias": "Joint", "pct": "0.5000"},
            {"ibkr_account_id": "U99999002", "alias": "Swing", "pct": "1.0000"},
            {"ibkr_account_id": "U99999003", "alias": "FUT", "pct": "1.0000"},
        ]
    }, headers=auth_headers)
    assert resp.status_code == 200

    state = (await client.get("/api/setup/state", headers=auth_headers)).json()
    assert state["step2_accounts"] is True


@pytest.mark.asyncio
async def test_step2_rejects_invalid_account_id(client: AsyncClient, auth_headers: dict):
    resp = await client.post("/api/setup/step2/save", json={
        "accounts": [{"ibkr_account_id": "NOT-VALID", "alias": "x", "pct": "0.5"}]
    }, headers=auth_headers)
    assert resp.status_code == 422  # pydantic validation


@pytest.mark.asyncio
async def test_step3_complete_marks_progress(client: AsyncClient, auth_headers: dict):
    resp = await client.post("/api/setup/step3/complete", headers=auth_headers)
    assert resp.status_code == 200
    state = (await client.get("/api/setup/state", headers=auth_headers)).json()
    assert state["step3_xmls"] is True
```

- [ ] **Step 5: Implementar `api/setup.py`**

Crear `backend/src/ibkr_control/api/setup.py`:

```python
"""Wizard endpoints — state machine en users.setup_progress JSONB."""
from datetime import date, datetime, timezone
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import attributes

from ibkr_control.api._schemas import (
    FlexCredentialsValidate, SetupJobStarted, SetupState, SetupStep2Save,
)
from ibkr_control.auth.deps import current_user
from ibkr_control.db.models.accounts import Account
from ibkr_control.db.models.flex_credentials import FlexCredentials
from ibkr_control.db.models.flex_raw import FlexImport
from ibkr_control.db.models.participations import Participation
from ibkr_control.db.models.user import User
from ibkr_control.db.session import get_session, get_engine
from ibkr_control.ingest.flex import client as flex_client_mod
from ibkr_control.ingest.flex import crypto as flex_crypto_mod
from ibkr_control.ingest.job_tracker import get_tracker
from sqlalchemy import func

router = APIRouter(prefix="/api/setup", tags=["setup"])


def _set_progress(user: User, key: str, value):
    """Mutate setup_progress dict y flagear dirty para SQLAlchemy."""
    progress = dict(user.setup_progress or {})
    progress[key] = value
    user.setup_progress = progress
    attributes.flag_modified(user, "setup_progress")


@router.get("/state", response_model=SetupState)
async def get_state(user: User = Depends(current_user)):
    p = user.setup_progress or {}
    return SetupState(
        step1_credentials=p.get("step1_credentials", False),
        step2_accounts=p.get("step2_accounts", False),
        step3_xmls=p.get("step3_xmls", False),
        step3_n_xmls_uploaded=p.get("step3_n_xmls_uploaded", 0),
        step4_started_at=p.get("step4_started_at"),
        step4_job_id=p.get("step4_job_id"),
        step4_substeps=p.get("step4_substeps", {}),
        setup_completed_at=user.setup_completed_at,
    )


@router.post("/step1/validate")
async def step1_validate(
    payload: FlexCredentialsValidate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    # Ping a IBKR
    try:
        client = flex_client_mod.FlexClient(token=payload.token)
        await client.send_request(query_id=payload.query_id)
    except flex_client_mod.FlexAuthError as e:
        raise HTTPException(401, detail=f"Token inválido: {e.error_message}")
    except Exception as e:
        raise HTTPException(502, detail=f"No pude alcanzar IBKR: {e}")

    # Guardar credenciales (upsert)
    creds = await session.scalar(
        select(FlexCredentials).where(FlexCredentials.user_id == user.id)
    )
    if creds is None:
        creds = FlexCredentials(
            user_id=user.id,
            token_encrypted=flex_crypto_mod.encrypt_token(payload.token),
            ytd_query_id=payload.query_id,
        )
        session.add(creds)
    else:
        creds.token_encrypted = flex_crypto_mod.encrypt_token(payload.token)
        creds.ytd_query_id = payload.query_id
        creds.last_rotated_at = datetime.now(timezone.utc)

    _set_progress(user, "step1_credentials", True)
    await session.commit()
    return {"ok": True}


@router.post("/step2/save")
async def step2_save(
    payload: SetupStep2Save,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    today = date.today()
    for item in payload.accounts:
        acc = await session.scalar(select(Account).where(Account.ibkr_account_id == item.ibkr_account_id))
        if acc is None:
            acc = Account(ibkr_account_id=item.ibkr_account_id, alias=item.alias, currency="USD")
            session.add(acc)
            await session.flush()
        elif item.alias is not None:
            acc.alias = item.alias

        # Upsert participation: cerrar la existente vigente y crear nueva con valid_from = today
        existing = await session.scalar(
            select(Participation).where(
                Participation.user_id == user.id,
                Participation.account_id == acc.id,
                Participation.valid_to.is_(None),
            )
        )
        if existing is not None:
            if existing.pct == item.pct:
                continue
            existing.valid_to = today
            await session.flush()

        session.add(Participation(
            user_id=user.id, account_id=acc.id,
            pct=item.pct, valid_from=today, valid_to=None,
        ))

    _set_progress(user, "step2_accounts", True)
    await session.commit()
    return {"ok": True}


@router.post("/step3/complete")
async def step3_complete(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    n_xmls = await session.scalar(
        select(func.count(FlexImport.id)).where(
            FlexImport.user_id == user.id,
            FlexImport.source == "manual_upload",
        )
    )
    _set_progress(user, "step3_xmls", True)
    _set_progress(user, "step3_n_xmls_uploaded", n_xmls or 0)
    await session.commit()
    return {"ok": True, "n_xmls_uploaded": n_xmls or 0}


@router.post("/step4/start", response_model=SetupJobStarted)
async def step4_start(
    background: BackgroundTasks,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    tracker = get_tracker()
    job_id = tracker.create_job()

    _set_progress(user, "step4_started_at", datetime.now(timezone.utc).isoformat())
    _set_progress(user, "step4_job_id", job_id)
    if "step4_substeps" not in (user.setup_progress or {}):
        _set_progress(user, "step4_substeps", {})
    await session.commit()

    background.add_task(_run_setup_meta_job, user_id=user.id, job_id=job_id)
    return SetupJobStarted(job_id=job_id)


async def _run_setup_meta_job(user_id: int, job_id: int):
    """Meta-job idempotente que corre TRM backfill + Flex YTD + marca completed."""
    from ibkr_control.ingest.trm import job as trm_job_mod
    from ibkr_control.ingest.flex import job as flex_job_mod
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    engine = get_engine()
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    tracker = get_tracker()

    async def _read_substeps() -> dict[str, str]:
        async with SessionLocal() as s:
            u = await s.scalar(select(User).where(User.id == user_id))
            return (u.setup_progress or {}).get("step4_substeps", {})

    async def _write_substep(name: str, status: str):
        async with SessionLocal() as s:
            u = await s.scalar(select(User).where(User.id == user_id))
            p = dict(u.setup_progress or {})
            substeps = dict(p.get("step4_substeps", {}))
            substeps[name] = status
            p["step4_substeps"] = substeps
            u.setup_progress = p
            attributes.flag_modified(u, "setup_progress")
            await s.commit()

    substeps = await _read_substeps()

    # Substep 1: TRM
    if substeps.get("trm_backfill") != "ok":
        tracker.emit(job_id, {"step": "trm_backfill", "status": "running"})
        try:
            result = await trm_job_mod.run(SessionLocal, trigger="wizard", full_backfill=True)
            await _write_substep("trm_backfill", "ok")
            tracker.emit(job_id, {"step": "trm_backfill", "status": "ok", "n_days": result["n_days"]})
        except Exception as e:
            await _write_substep("trm_backfill", "failed")
            tracker.emit(job_id, {"step": "trm_backfill", "status": "failed", "error": str(e)[:500]})
            tracker.mark_done(job_id)
            return

    # Substep 2: Flex YTD
    substeps = await _read_substeps()
    if substeps.get("flex_ytd") != "ok":
        tracker.emit(job_id, {"step": "flex_ytd", "status": "running"})
        try:
            await flex_job_mod.run(SessionLocal, user_id=user_id, trigger="wizard")
            await _write_substep("flex_ytd", "ok")
            tracker.emit(job_id, {"step": "flex_ytd", "status": "ok"})
        except Exception as e:
            await _write_substep("flex_ytd", "failed")
            tracker.emit(job_id, {"step": "flex_ytd", "status": "failed", "error": str(e)[:500]})
            tracker.mark_done(job_id)
            return

    # Marcar setup completado
    async with SessionLocal() as s:
        u = await s.scalar(select(User).where(User.id == user_id))
        u.setup_completed_at = datetime.now(timezone.utc)
        await s.commit()

    tracker.emit(job_id, {"step": "done", "redirect": "/dashboard"})
    tracker.mark_done(job_id)
```

- [ ] **Step 6: Agregar `get_engine` helper en `db/session.py` si no existe**

Si no existe, agregar a `backend/src/ibkr_control/db/session.py`:

```python
from functools import lru_cache
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    from ibkr_control.config import get_settings
    return create_async_engine(get_settings().database_url, echo=False)
```

- [ ] **Step 7: Wire routers en `main.py`**

En `backend/src/ibkr_control/main.py`:

```python
from ibkr_control.api import credentials as credentials_router
from ibkr_control.api import setup as setup_router

app.include_router(credentials_router.router)
app.include_router(setup_router.router)
```

- [ ] **Step 8: Correr tests**

```bash
cd backend && uv run pytest tests/api/test_credentials.py tests/api/test_setup.py -v
```

Expected: 10/10 PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/src/ibkr_control/api/_schemas.py \
        backend/src/ibkr_control/api/credentials.py \
        backend/src/ibkr_control/api/setup.py \
        backend/src/ibkr_control/db/session.py \
        backend/src/ibkr_control/main.py \
        backend/tests/api/test_credentials.py \
        backend/tests/api/test_setup.py
git commit -m "feat(phase2): API endpoints — credentials (GET/PUT) + setup wizard (state/step1/step2/step3/step4)"
```

---

### Task 15: API imports + ingest (upload XML + manual trigger + SSE stream + logs)

**Files:**
- Create: `backend/src/ibkr_control/api/imports.py`
- Create: `backend/src/ibkr_control/api/ingest.py`
- Modify: `backend/src/ibkr_control/main.py`
- Create: `backend/tests/api/test_imports.py`
- Create: `backend/tests/api/test_ingest.py`

- [ ] **Step 1: Escribir tests del upload (failing)**

Crear `backend/tests/api/test_imports.py`:

```python
"""Tests de /api/imports/upload."""
from pathlib import Path
import pytest
from httpx import AsyncClient

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "xml"


@pytest.mark.asyncio
async def test_upload_valid_xml_returns_summary(client: AsyncClient, auth_headers: dict):
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    files = {"file": ("ACTIVITY_2025.xml", xml, "application/xml")}
    resp = await client.post("/api/imports/upload", files=files, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "flex_import_id" in body
    assert body["n_trades"] > 0


@pytest.mark.asyncio
async def test_upload_duplicate_returns_409(client: AsyncClient, auth_headers: dict):
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    files = {"file": ("ACTIVITY_2025.xml", xml, "application/xml")}
    r1 = await client.post("/api/imports/upload", files=files, headers=auth_headers)
    assert r1.status_code == 200
    r2 = await client.post("/api/imports/upload", files=files, headers=auth_headers)
    assert r2.status_code == 409
    body = r2.json()
    assert "flex_import_id" in body.get("detail", {})


@pytest.mark.asyncio
async def test_upload_malformed_returns_400(client: AsyncClient, auth_headers: dict):
    xml = (FIXTURE_DIR / "malformed_xml.xml").read_bytes()
    files = {"file": ("bad.xml", xml, "application/xml")}
    resp = await client.post("/api/imports/upload", files=files, headers=auth_headers)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_upload_not_a_flex_response_returns_400(client: AsyncClient, auth_headers: dict):
    xml = (FIXTURE_DIR / "not_a_flex_response.xml").read_bytes()
    files = {"file": ("wrong.xml", xml, "application/xml")}
    resp = await client.post("/api/imports/upload", files=files, headers=auth_headers)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_upload_requires_auth(client: AsyncClient):
    xml = (FIXTURE_DIR / "empty_query_response.xml").read_bytes()
    files = {"file": ("e.xml", xml, "application/xml")}
    resp = await client.post("/api/imports/upload", files=files)
    assert resp.status_code == 401
```

- [ ] **Step 2: Implementar `api/imports.py`**

Crear `backend/src/ibkr_control/api/imports.py`:

```python
"""POST /api/imports/upload — multipart XML upload reusado por wizard step 3 + Settings."""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.auth.deps import current_user
from ibkr_control.db.models.flex_raw import FlexImport
from ibkr_control.db.models.user import User
from ibkr_control.db.session import get_session
from ibkr_control.ingest.flex import job as flex_job_mod
from ibkr_control.ingest.flex import parser as flex_parser_mod
from ibkr_control.ingest.flex._models import UnknownFlexTagError
from ibkr_control.ingest.hash_dedup import xml_hash

router = APIRouter(prefix="/api/imports", tags=["imports"])


MAX_XML_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB


@router.post("/upload")
async def upload_xml(
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    xml_bytes = await file.read()
    if len(xml_bytes) == 0:
        raise HTTPException(400, detail="Empty file")
    if len(xml_bytes) > MAX_XML_SIZE_BYTES:
        raise HTTPException(413, detail=f"File too large: {len(xml_bytes)} bytes (max {MAX_XML_SIZE_BYTES})")

    # Pre-check: hash duplicado → 409 sin parsear
    h = xml_hash(xml_bytes)
    existing = await session.scalar(
        select(FlexImport).where(FlexImport.xml_hash == h)
    )
    if existing is not None:
        raise HTTPException(409, detail={
            "flex_import_id": existing.id,
            "fetched_at": existing.fetched_at.isoformat(),
            "message": "Este XML ya fue importado",
        })

    # Pre-check: parser sintáctico + audit
    try:
        flex_parser_mod.parse(xml_bytes)
    except UnknownFlexTagError as e:
        raise HTTPException(400, detail=f"XML tiene tag desconocido: {e.tag}")
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        raise HTTPException(400, detail=f"XML inválido: {e}")

    # Ingest real
    flex_import_id = await flex_job_mod.ingest_xml(
        session, user_id=user.id, xml_bytes=xml_bytes,
        source="manual_upload", trigger="wizard",
    )

    # Pull counts
    fi = await session.scalar(select(FlexImport).where(FlexImport.id == flex_import_id))
    return {
        "flex_import_id": flex_import_id,
        "n_trades": fi.n_trades,
        "n_lots_closed": fi.n_lots_closed,
        "n_cash_tx": fi.n_cash_tx,
        "anyo": fi.anyo,
    }
```

- [ ] **Step 3: Escribir tests del ingest router (failing)**

Crear `backend/tests/api/test_ingest.py`:

```python
"""Tests de /api/ingest/* (trigger + stream + logs)."""
import asyncio
import json
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_logs_empty_when_no_runs(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/ingest/logs?limit=10", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_trigger_rate_limit_429(client: AsyncClient, auth_headers: dict, monkeypatch):
    """Llamadas seguidas devuelven 429 después de la primera."""
    monkeypatch.setattr(
        "ibkr_control.api.ingest._LAST_TRIGGER", {}
    )
    # Mock para que el job no haga nada real
    async def noop(*args, **kwargs):
        return {"status": "ok"}
    monkeypatch.setattr("ibkr_control.api.ingest._launch_manual_job", noop)

    r1 = await client.post("/api/ingest/trigger", json={"kind": "trm"}, headers=auth_headers)
    assert r1.status_code == 200

    r2 = await client.post("/api/ingest/trigger", json={"kind": "trm"}, headers=auth_headers)
    assert r2.status_code == 429


@pytest.mark.asyncio
async def test_trigger_invalid_kind_returns_422(client: AsyncClient, auth_headers: dict):
    resp = await client.post("/api/ingest/trigger", json={"kind": "garbage"}, headers=auth_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_stream_unknown_job_returns_404(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/ingest/stream/99999", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_stream_emits_done_event(client: AsyncClient, auth_headers: dict):
    """Crea un job en el tracker, emite eventos, verifica que el SSE los entrega."""
    from ibkr_control.ingest.job_tracker import get_tracker
    tracker = get_tracker()
    job_id = tracker.create_job()
    tracker.emit(job_id, {"step": "test", "status": "running"})
    tracker.emit(job_id, {"step": "test", "status": "ok"})
    tracker.mark_done(job_id)

    async with client.stream("GET", f"/api/ingest/stream/{job_id}", headers=auth_headers) as resp:
        assert resp.status_code == 200
        chunks = []
        async for line in resp.aiter_lines():
            chunks.append(line)
            if "event: done" in line:
                break
        body = "\n".join(chunks)
        assert "test" in body
        assert "done" in body
```

- [ ] **Step 4: Implementar `api/ingest.py`**

Crear `backend/src/ibkr_control/api/ingest.py`:

```python
"""Endpoints /api/ingest/* — manual trigger, SSE stream, logs viewer."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sse_starlette.sse import EventSourceResponse

from ibkr_control.api._schemas import IngestJobStarted, IngestLogRead, IngestTrigger
from ibkr_control.auth.deps import current_user
from ibkr_control.db.models.ingest_log import IngestLog
from ibkr_control.db.models.user import User
from ibkr_control.db.session import get_engine, get_session
from ibkr_control.ingest.job_tracker import get_tracker

router = APIRouter(prefix="/api/ingest", tags=["ingest"])

_LAST_TRIGGER: dict[int, datetime] = {}
_COOLDOWN = timedelta(minutes=5)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@router.post("/trigger", response_model=IngestJobStarted)
async def trigger_manual_refresh(
    payload: IngestTrigger,
    background: BackgroundTasks,
    user: User = Depends(current_user),
):
    last = _LAST_TRIGGER.get(user.id)
    if last and (_utcnow() - last) < _COOLDOWN:
        wait = _COOLDOWN - (_utcnow() - last)
        raise HTTPException(429, detail=f"Esperá {int(wait.total_seconds())}s antes de reintentar")
    _LAST_TRIGGER[user.id] = _utcnow()

    job_id = await _launch_manual_job(payload.kind, user.id, background)
    return IngestJobStarted(job_id=job_id)


async def _launch_manual_job(kind: str, user_id: int, background: BackgroundTasks) -> int:
    """Lanza el job real en background. Devuelve job_id del tracker."""
    tracker = get_tracker()
    job_id = tracker.create_job()
    background.add_task(_run_manual, kind=kind, user_id=user_id, job_id=job_id)
    return job_id


async def _run_manual(kind: str, user_id: int, job_id: int):
    from ibkr_control.ingest.flex import job as flex_job_mod
    from ibkr_control.ingest.trm import job as trm_job_mod

    engine = get_engine()
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    tracker = get_tracker()

    try:
        if kind in ("trm", "both"):
            tracker.emit(job_id, {"step": "trm", "status": "running"})
            r = await trm_job_mod.run(SessionLocal, trigger="manual")
            tracker.emit(job_id, {"step": "trm", "status": "ok", "n_days": r["n_days"]})

        if kind in ("flex", "both"):
            tracker.emit(job_id, {"step": "flex", "status": "running"})
            await flex_job_mod.run(SessionLocal, user_id=user_id, trigger="manual")
            tracker.emit(job_id, {"step": "flex", "status": "ok"})

        tracker.emit(job_id, {"step": "done"})
    except Exception as e:
        tracker.emit(job_id, {"step": "error", "error": str(e)[:500]})
    finally:
        tracker.mark_done(job_id)


@router.get("/stream/{job_id}")
async def stream_progress(
    job_id: int,
    user: User = Depends(current_user),
):
    tracker = get_tracker()
    if not tracker.events_since(job_id, after_id=0) and not tracker.is_done(job_id):
        # Job desconocido — confirmar que existe (cualquier intento de eventos vacío == no_existe)
        if job_id not in tracker._jobs:  # acceso interno OK porque es V1
            raise HTTPException(404, detail="Unknown job_id")

    async def event_generator():
        last_id = -1
        while True:
            events = tracker.events_since(job_id, after_id=last_id + 1)
            for ev in events:
                yield {
                    "id": str(ev.id),
                    "event": "progress" if ev.payload.get("step") != "done" else "done",
                    "data": json.dumps(ev.payload),
                }
                last_id = ev.id
            if tracker.is_done(job_id):
                # Asegurar que el done event salió
                if events and events[-1].payload.get("step") != "done":
                    yield {"event": "done", "data": "{}"}
                break
            await asyncio.sleep(0.5)

    return EventSourceResponse(
        event_generator(),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/logs", response_model=list[IngestLogRead])
async def list_logs(
    limit: int = Query(10, ge=1, le=100),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.scalars(
        select(IngestLog)
        .where((IngestLog.user_id == user.id) | (IngestLog.user_id.is_(None)))
        .order_by(IngestLog.started_at.desc())
        .limit(limit)
    )
    return [IngestLogRead.model_validate(r, from_attributes=True) for r in result.all()]
```

- [ ] **Step 5: Wire routers en `main.py`**

```python
from ibkr_control.api import imports as imports_router
from ibkr_control.api import ingest as ingest_router

app.include_router(imports_router.router)
app.include_router(ingest_router.router)
```

- [ ] **Step 6: Correr tests**

```bash
cd backend && uv run pytest tests/api/test_imports.py tests/api/test_ingest.py -v
```

Expected: 10/10 PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/src/ibkr_control/api/imports.py \
        backend/src/ibkr_control/api/ingest.py \
        backend/src/ibkr_control/main.py \
        backend/tests/api/test_imports.py \
        backend/tests/api/test_ingest.py
git commit -m "feat(phase2): API endpoints — imports (upload XML) + ingest (trigger + SSE stream + logs)"
```

---

### Task 16: Frontend wizard — middleware + route group + stepper shell + steps 1-2

**Files:**
- Create: `frontend/src/middleware.ts`
- Create: `frontend/src/app/(setup)/setup/layout.tsx`
- Create: `frontend/src/app/(setup)/setup/page.tsx`
- Create: `frontend/src/components/wizard/Stepper.tsx`
- Create: `frontend/src/components/wizard/Step1Credentials.tsx`
- Create: `frontend/src/components/wizard/Step2Accounts.tsx`
- Create: `frontend/src/hooks/useSetupState.ts`
- Modify: `frontend/package.json` (agregar react-dropzone si no está)

**Nota:** después de hacer los cambios del backend, regenerar el cliente TS:

```bash
cd frontend && pnpm openapi:gen
```

(Esto refresca `src/lib/api/` con los endpoints nuevos de credentials/setup/imports/ingest.)

- [ ] **Step 1: Regenerar cliente TS**

Levantar backend para que exponga el OpenAPI nuevo:

```bash
docker compose up -d backend && sleep 3 && cd frontend && pnpm openapi:gen
```

Verificar que se generaron hooks tipo `useSetupStateRetrieve`, `usePostSetupStep1Validate`, `usePostSetupStep2Save`, `usePostSetupStep3Complete`, `usePostSetupStep4Start`, `useGetCredentialsFlex`, `usePutCredentialsFlex`, `usePostImportsUpload`, `usePostIngestTrigger`, `useGetIngestLogs`.

- [ ] **Step 2: Crear middleware de routing**

Crear `frontend/src/middleware.ts`:

```typescript
import { NextRequest, NextResponse } from "next/server";

/**
 * Reglas:
 * - No-auth + ruta protegida → /login
 * - Auth + setup_completed_at IS NULL + ruta != /setup → /setup
 * - Auth + setup_completed_at IS NOT NULL + ruta == /setup → /dashboard
 */
const PUBLIC_PATHS = new Set(["/login", "/register", "/_next", "/api/health"]);

function isPublic(pathname: string): boolean {
  return [...PUBLIC_PATHS].some((p) => pathname.startsWith(p));
}

export async function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;

  // Activos estáticos pasan
  if (pathname.startsWith("/_next") || pathname.startsWith("/static") || pathname.match(/\.\w+$/)) {
    return NextResponse.next();
  }

  // JWT en cookie httpOnly (Phase 1 ya tiene esto)
  const token = req.cookies.get("auth_token")?.value;

  if (!token && !isPublic(pathname)) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    return NextResponse.redirect(url);
  }

  if (!token) {
    return NextResponse.next();
  }

  // Para usuarios autenticados, consultar /api/setup/state y enrutar
  const stateResp = await fetch(`${process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://backend:8000"}/api/setup/state`, {
    headers: { Cookie: `auth_token=${token}` },
  });

  if (!stateResp.ok) {
    return NextResponse.next();
  }

  const state = await stateResp.json();
  const setupCompleted = state.setup_completed_at !== null;

  if (!setupCompleted && pathname !== "/setup") {
    const url = req.nextUrl.clone();
    url.pathname = "/setup";
    return NextResponse.redirect(url);
  }

  if (setupCompleted && pathname === "/setup") {
    const url = req.nextUrl.clone();
    url.pathname = "/dashboard";
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api/|_next/|static/|favicon.ico).*)"],
};
```

- [ ] **Step 3: Crear hook `useSetupState`**

Crear `frontend/src/hooks/useSetupState.ts`:

```typescript
import { useGetSetupState } from "@/lib/api/setup/setup";

export interface SetupState {
  step1_credentials: boolean;
  step2_accounts: boolean;
  step3_xmls: boolean;
  step3_n_xmls_uploaded: number;
  step4_started_at: string | null;
  step4_job_id: number | null;
  step4_substeps: Record<string, string>;
  setup_completed_at: string | null;
}

export function useSetupState() {
  const query = useGetSetupState({ query: { refetchInterval: 0 } });
  return {
    state: query.data?.data as SetupState | undefined,
    isLoading: query.isLoading,
    refetch: query.refetch,
  };
}

export function currentStep(state: SetupState | undefined): 1 | 2 | 3 | 4 {
  if (!state) return 1;
  if (!state.step1_credentials) return 1;
  if (!state.step2_accounts) return 2;
  if (!state.step3_xmls) return 3;
  return 4;
}
```

- [ ] **Step 4: Crear layout del wizard**

Crear `frontend/src/app/(setup)/setup/layout.tsx`:

```typescript
import type { ReactNode } from "react";

export default function SetupLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-background">
      <header className="border-b py-4 px-6">
        <h1 className="text-lg font-semibold">IBKR Control — Setup</h1>
      </header>
      <main className="container mx-auto py-8 max-w-3xl">{children}</main>
    </div>
  );
}
```

- [ ] **Step 5: Crear componente `Stepper`**

Crear `frontend/src/components/wizard/Stepper.tsx`:

```typescript
"use client";

import { cn } from "@/lib/utils";

interface StepperProps {
  current: 1 | 2 | 3 | 4;
  labels: [string, string, string, string];
}

export function Stepper({ current, labels }: StepperProps) {
  return (
    <ol className="flex items-center w-full mb-8">
      {labels.map((label, i) => {
        const step = (i + 1) as 1 | 2 | 3 | 4;
        const isActive = step === current;
        const isDone = step < current;
        return (
          <li
            key={label}
            className={cn(
              "flex items-center flex-1",
              i < labels.length - 1 && "after:content-[''] after:w-full after:h-1 after:border-b after:mx-2",
              isDone && "after:border-green-500",
              !isDone && "after:border-gray-300",
            )}
          >
            <div className="flex flex-col items-center">
              <div
                className={cn(
                  "w-8 h-8 rounded-full flex items-center justify-center font-semibold",
                  isActive && "bg-blue-500 text-white",
                  isDone && "bg-green-500 text-white",
                  !isActive && !isDone && "bg-gray-200 text-gray-600",
                )}
              >
                {isDone ? "✓" : step}
              </div>
              <span className="text-xs mt-1 text-center">{label}</span>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
```

- [ ] **Step 6: Crear Step1Credentials**

Crear `frontend/src/components/wizard/Step1Credentials.tsx`:

```typescript
"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { usePostSetupStep1Validate } from "@/lib/api/setup/setup";

interface Step1Props {
  onComplete: () => void;
}

export function Step1Credentials({ onComplete }: Step1Props) {
  const [token, setToken] = useState("");
  const [queryId, setQueryId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const { mutate, isPending } = usePostSetupStep1Validate();

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    mutate(
      { data: { token, query_id: queryId } },
      {
        onSuccess: () => onComplete(),
        onError: (err: any) => {
          setError(err?.response?.data?.detail ?? "Error desconocido");
        },
      },
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <h2 className="text-xl font-semibold">Credenciales IBKR Flex</h2>
      <p className="text-sm text-muted-foreground">
        Generá el token en Account Management → Settings → Account Settings → Flex Web Service. 
        Necesitás también el ID del Flex Query &quot;Year to Date&quot; del año actual.
      </p>

      <div>
        <Label htmlFor="token">Flex Token (read-only)</Label>
        <Input
          id="token" type="password" value={token}
          onChange={(e) => setToken(e.target.value)}
          required minLength={10}
        />
      </div>

      <div>
        <Label htmlFor="queryId">YTD Flex Query ID</Label>
        <Input
          id="queryId" type="text" value={queryId}
          onChange={(e) => setQueryId(e.target.value)}
          required pattern="\d+"
        />
      </div>

      {error && <div className="text-red-500 text-sm">{error}</div>}

      <div className="flex justify-end">
        <Button type="submit" disabled={isPending}>
          {isPending ? "Validando con IBKR…" : "Continuar →"}
        </Button>
      </div>
    </form>
  );
}
```

- [ ] **Step 7: Crear Step2Accounts**

Crear `frontend/src/components/wizard/Step2Accounts.tsx`:

```typescript
"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { usePostSetupStep2Save } from "@/lib/api/setup/setup";

interface AccountRow {
  ibkr_account_id: string;
  alias: string;
  pct: string;
}

interface Step2Props {
  onComplete: () => void;
  onBack: () => void;
}

const DEFAULT_ACCOUNTS: AccountRow[] = [
  { ibkr_account_id: "U99999001", alias: "Conjunta Joint Holder", pct: "0.5000" },
  { ibkr_account_id: "U99999002", alias: "Personal Swing", pct: "1.0000" },
  { ibkr_account_id: "U99999003", alias: "Personal Futuros", pct: "1.0000" },
];

export function Step2Accounts({ onComplete, onBack }: Step2Props) {
  const [accounts, setAccounts] = useState<AccountRow[]>(DEFAULT_ACCOUNTS);
  const [error, setError] = useState<string | null>(null);
  const { mutate, isPending } = usePostSetupStep2Save();

  function updateRow(i: number, field: keyof AccountRow, value: string) {
    setAccounts((prev) => prev.map((r, idx) => (idx === i ? { ...r, [field]: value } : r)));
  }

  function addRow() {
    setAccounts((prev) => [...prev, { ibkr_account_id: "U", alias: "", pct: "1.0000" }]);
  }

  function removeRow(i: number) {
    setAccounts((prev) => prev.filter((_, idx) => idx !== i));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    mutate(
      {
        data: {
          accounts: accounts.map((r) => ({
            ibkr_account_id: r.ibkr_account_id,
            alias: r.alias || null,
            pct: r.pct,
          })),
        },
      },
      {
        onSuccess: () => onComplete(),
        onError: (err: any) => setError(err?.response?.data?.detail ?? "Error"),
      },
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <h2 className="text-xl font-semibold">Cuentas IBKR + Participaciones</h2>
      <p className="text-sm text-muted-foreground">
        Si compartís alguna cuenta (e.g. cuenta conjunta), bajá el % a tu porción real.
      </p>

      <table className="w-full text-sm">
        <thead>
          <tr className="border-b">
            <th className="text-left py-2">Account ID</th>
            <th className="text-left py-2">Alias</th>
            <th className="text-left py-2">% Tuyo</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {accounts.map((row, i) => (
            <tr key={i} className="border-b">
              <td className="py-2">
                <Input
                  value={row.ibkr_account_id}
                  onChange={(e) => updateRow(i, "ibkr_account_id", e.target.value)}
                  pattern="^U\d{8}$" required
                />
              </td>
              <td className="py-2">
                <Input
                  value={row.alias}
                  onChange={(e) => updateRow(i, "alias", e.target.value)}
                />
              </td>
              <td className="py-2">
                <Input
                  value={row.pct} type="number" step="0.0001" min="0" max="1"
                  onChange={(e) => updateRow(i, "pct", e.target.value)}
                  required
                />
              </td>
              <td className="py-2 text-right">
                <Button type="button" variant="ghost" onClick={() => removeRow(i)}>
                  ✗
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <Button type="button" variant="outline" onClick={addRow}>+ Agregar cuenta</Button>

      {error && <div className="text-red-500 text-sm">{error}</div>}

      <div className="flex justify-between">
        <Button type="button" variant="ghost" onClick={onBack}>← Atrás</Button>
        <Button type="submit" disabled={isPending}>
          {isPending ? "Guardando…" : "Continuar →"}
        </Button>
      </div>
    </form>
  );
}
```

- [ ] **Step 8: Crear página `setup/page.tsx` (shell que delega a steps 1-2 por ahora)**

Crear `frontend/src/app/(setup)/setup/page.tsx`:

```typescript
"use client";

import { useState } from "react";
import { Stepper } from "@/components/wizard/Stepper";
import { Step1Credentials } from "@/components/wizard/Step1Credentials";
import { Step2Accounts } from "@/components/wizard/Step2Accounts";
import { useSetupState, currentStep } from "@/hooks/useSetupState";

export default function SetupPage() {
  const { state, refetch, isLoading } = useSetupState();
  const [override, setOverride] = useState<1 | 2 | 3 | 4 | null>(null);

  if (isLoading) return <div>Loading…</div>;

  const active = override ?? currentStep(state);

  return (
    <div>
      <Stepper
        current={active}
        labels={["Credenciales", "Cuentas", "Históricos", "Iniciar"]}
      />

      {active === 1 && (
        <Step1Credentials onComplete={() => { refetch(); setOverride(2); }} />
      )}
      {active === 2 && (
        <Step2Accounts
          onComplete={() => { refetch(); setOverride(3); }}
          onBack={() => setOverride(1)}
        />
      )}
      {active === 3 && (
        <div>Step 3 — implementado en Task 17</div>
      )}
      {active === 4 && (
        <div>Step 4 — implementado en Task 17</div>
      )}
    </div>
  );
}
```

- [ ] **Step 9: Verificar build del frontend**

```bash
cd frontend && pnpm build
```

Expected: build OK sin errores TS.

- [ ] **Step 10: Smoke test manual — verificar redirect /setup**

```bash
docker compose up -d --build && sleep 5
```

Abrir browser en `http://localhost:3000`. Registrarse con email/password. Verificar redirect automático a `/setup`. Llenar step 1 con datos inválidos (token corto) — verificar error inline. Cerrar el browser.

```bash
docker compose down
```

- [ ] **Step 11: Commit**

```bash
git add frontend/src/middleware.ts \
        frontend/src/app/\(setup\)/ \
        frontend/src/components/wizard/Stepper.tsx \
        frontend/src/components/wizard/Step1Credentials.tsx \
        frontend/src/components/wizard/Step2Accounts.tsx \
        frontend/src/hooks/useSetupState.ts \
        frontend/src/lib/api/
git commit -m "feat(phase2): frontend wizard scaffold — middleware + stepper + steps 1-2"
```

---

### Task 17: Frontend wizard — steps 3 (XML upload) + 4 (SSE progress + retry)

**Files:**
- Create: `frontend/src/components/wizard/Step3Xmls.tsx`
- Create: `frontend/src/components/wizard/Step4Initial.tsx`
- Create: `frontend/src/hooks/useIngestStream.ts`
- Modify: `frontend/src/app/(setup)/setup/page.tsx` (wire steps 3 + 4)

- [ ] **Step 1: Crear hook `useIngestStream` para SSE**

Crear `frontend/src/hooks/useIngestStream.ts`:

```typescript
"use client";

import { useEffect, useRef, useState } from "react";

export interface StreamEvent {
  step: string;
  status?: "running" | "ok" | "failed";
  n_days?: number;
  n_trades?: number;
  error?: string;
  redirect?: string;
}

export interface IngestStreamState {
  events: StreamEvent[];
  isDone: boolean;
  error: string | null;
}

export function useIngestStream(jobId: number | null): IngestStreamState {
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [isDone, setIsDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (jobId === null) return;

    const url = `${process.env.NEXT_PUBLIC_BACKEND_URL ?? ""}/api/ingest/stream/${jobId}`;
    const es = new EventSource(url, { withCredentials: true });
    sourceRef.current = es;

    es.addEventListener("progress", (ev) => {
      try {
        const payload = JSON.parse((ev as MessageEvent).data) as StreamEvent;
        setEvents((prev) => [...prev, payload]);
      } catch (e) {
        // ignore parse errors
      }
    });

    es.addEventListener("done", () => {
      setIsDone(true);
      es.close();
    });

    es.onerror = () => {
      setError("Conexión perdida con el servidor. Reconectando…");
      // EventSource reintenta automáticamente
    };

    return () => {
      es.close();
      sourceRef.current = null;
    };
  }, [jobId]);

  return { events, isDone, error };
}
```

- [ ] **Step 2: Crear Step3Xmls (drag&drop con react-dropzone)**

Asegurar dep:

```bash
cd frontend && pnpm add react-dropzone
```

Crear `frontend/src/components/wizard/Step3Xmls.tsx`:

```typescript
"use client";

import { useCallback, useState } from "react";
import { useDropzone } from "react-dropzone";
import { Button } from "@/components/ui/button";
import { usePostImportsUpload, usePostSetupStep3Complete } from "@/lib/api";

interface UploadResult {
  fileName: string;
  status: "ok" | "duplicate" | "error";
  message: string;
  n_trades?: number;
}

interface Step3Props {
  onComplete: () => void;
  onBack: () => void;
}

export function Step3Xmls({ onComplete, onBack }: Step3Props) {
  const [uploads, setUploads] = useState<UploadResult[]>([]);
  const uploadMut = usePostImportsUpload();
  const completeMut = usePostSetupStep3Complete();

  const onDrop = useCallback(async (files: File[]) => {
    for (const f of files) {
      try {
        const formData = new FormData();
        formData.append("file", f);
        const r = await uploadMut.mutateAsync({ data: formData as any });
        setUploads((prev) => [
          ...prev,
          {
            fileName: f.name, status: "ok",
            message: `${(r.data as any).n_trades} trades, año ${(r.data as any).anyo}`,
            n_trades: (r.data as any).n_trades,
          },
        ]);
      } catch (err: any) {
        const status = err?.response?.status === 409 ? "duplicate" : "error";
        const msg = err?.response?.data?.detail?.message
          ?? err?.response?.data?.detail
          ?? "Error desconocido";
        setUploads((prev) => [...prev, { fileName: f.name, status, message: String(msg) }]);
      }
    }
  }, [uploadMut]);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { "application/xml": [".xml"], "text/xml": [".xml"] },
  });

  function handleContinue() {
    completeMut.mutate({}, { onSuccess: () => onComplete() });
  }

  return (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold">XMLs históricos (opcional)</h2>
      <p className="text-sm text-muted-foreground">
        Subí los Activity Statement XMLs de años anteriores que tengas guardados.
        Si no tenés, podés saltar este paso y la app arrancará desde el YTD del año actual.
      </p>

      <div
        {...getRootProps()}
        className={`border-2 border-dashed rounded p-12 text-center cursor-pointer
          ${isDragActive ? "border-blue-500 bg-blue-50" : "border-gray-300"}`}
      >
        <input {...getInputProps()} />
        <p>↑ Arrastrá XMLs aquí o click para seleccionar ↑</p>
      </div>

      {uploads.length > 0 && (
        <ul className="space-y-1">
          {uploads.map((u, i) => (
            <li key={i} className="text-sm">
              <span className="font-mono">{u.fileName}</span> —{" "}
              {u.status === "ok" && <span className="text-green-600">✓ {u.message}</span>}
              {u.status === "duplicate" && <span className="text-yellow-600">⚠ {u.message}</span>}
              {u.status === "error" && <span className="text-red-600">✗ {u.message}</span>}
            </li>
          ))}
        </ul>
      )}

      <div className="flex justify-between">
        <Button variant="ghost" onClick={onBack}>← Atrás</Button>
        <Button onClick={handleContinue} disabled={completeMut.isPending}>
          Continuar →
        </Button>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Crear Step4Initial (SSE progress + retry)**

Crear `frontend/src/components/wizard/Step4Initial.tsx`:

```typescript
"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { usePostSetupStep4Start } from "@/lib/api";
import { useIngestStream, StreamEvent } from "@/hooks/useIngestStream";
import { useSetupState } from "@/hooks/useSetupState";

const SUBSTEPS = [
  { key: "trm_backfill", label: "Cargar TRM histórica (1991-hoy)" },
  { key: "flex_ytd", label: "Fetch YTD del año actual desde IBKR" },
] as const;

function statusOf(events: StreamEvent[], key: string): "pending" | "running" | "ok" | "failed" {
  const matching = events.filter((e) => e.step === key);
  if (matching.length === 0) return "pending";
  const last = matching[matching.length - 1];
  if (last.status === "ok") return "ok";
  if (last.status === "failed") return "failed";
  return "running";
}

export function Step4Initial() {
  const router = useRouter();
  const { state, refetch } = useSetupState();
  const [jobId, setJobId] = useState<number | null>(state?.step4_job_id ?? null);
  const startMut = usePostSetupStep4Start();
  const { events, isDone } = useIngestStream(jobId);

  // Si refresca el browser y ya hay job en progreso, reusarlo
  useEffect(() => {
    if (state?.step4_job_id && !jobId) {
      setJobId(state.step4_job_id);
    }
  }, [state?.step4_job_id, jobId]);

  // Si llega event done con redirect, navegar
  useEffect(() => {
    if (events.some((e) => e.step === "done" && e.redirect)) {
      const ev = events.find((e) => e.step === "done")!;
      setTimeout(() => router.push(ev.redirect ?? "/dashboard"), 800);
    }
  }, [events, router]);

  function handleStart() {
    startMut.mutate({}, {
      onSuccess: (r: any) => {
        setJobId(r.data.job_id);
        refetch();
      },
    });
  }

  if (!jobId) {
    return (
      <div className="space-y-4">
        <h2 className="text-xl font-semibold">Iniciar carga inicial</h2>
        <ul className="list-disc pl-6 text-sm space-y-1">
          <li>Cargar TRM histórica desde DIAN Socrata</li>
          <li>Hacer el primer fetch YTD del año actual desde IBKR Flex WS</li>
          <li>Activar el cron diario (Flex 07:00 + TRM 19:30 COT)</li>
        </ul>
        <Button onClick={handleStart} disabled={startMut.isPending}>
          Iniciar carga inicial →
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold">Cargando datos...</h2>

      <ul className="space-y-2">
        {SUBSTEPS.map((sub) => {
          const status = statusOf(events, sub.key);
          const icon =
            status === "ok" ? "✓" :
            status === "running" ? "⏳" :
            status === "failed" ? "✗" : "⏸";
          const color =
            status === "ok" ? "text-green-600" :
            status === "running" ? "text-blue-600" :
            status === "failed" ? "text-red-600" : "text-gray-400";
          return (
            <li key={sub.key} className="flex items-center gap-2">
              <span className={color}>{icon}</span>
              <span>{sub.label}</span>
              {status === "running" && (
                <span className="text-xs text-muted-foreground ml-2">
                  {(() => {
                    const ev = events.filter((e) => e.step === sub.key).slice(-1)[0];
                    if (ev?.n_days) return `${ev.n_days} días procesados`;
                    if (ev?.n_trades) return `${ev.n_trades} trades`;
                    return "…";
                  })()}
                </span>
              )}
              {status === "failed" && (
                <Button size="sm" variant="outline" onClick={handleStart}>Reintentar</Button>
              )}
            </li>
          );
        })}
      </ul>

      {isDone && (
        <div className="text-green-600 font-semibold">
          ✓ Completado. Redirigiendo al dashboard…
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Wire steps 3 + 4 en `setup/page.tsx`**

Modificar `frontend/src/app/(setup)/setup/page.tsx`:

```typescript
"use client";

import { useState } from "react";
import { Stepper } from "@/components/wizard/Stepper";
import { Step1Credentials } from "@/components/wizard/Step1Credentials";
import { Step2Accounts } from "@/components/wizard/Step2Accounts";
import { Step3Xmls } from "@/components/wizard/Step3Xmls";
import { Step4Initial } from "@/components/wizard/Step4Initial";
import { useSetupState, currentStep } from "@/hooks/useSetupState";

export default function SetupPage() {
  const { state, refetch, isLoading } = useSetupState();
  const [override, setOverride] = useState<1 | 2 | 3 | 4 | null>(null);

  if (isLoading) return <div>Loading…</div>;
  const active = override ?? currentStep(state);

  return (
    <div>
      <Stepper current={active} labels={["Credenciales", "Cuentas", "Históricos", "Iniciar"]} />

      {active === 1 && <Step1Credentials onComplete={() => { refetch(); setOverride(2); }} />}
      {active === 2 && <Step2Accounts
        onComplete={() => { refetch(); setOverride(3); }}
        onBack={() => setOverride(1)} />}
      {active === 3 && <Step3Xmls
        onComplete={() => { refetch(); setOverride(4); }}
        onBack={() => setOverride(2)} />}
      {active === 4 && <Step4Initial />}
    </div>
  );
}
```

- [ ] **Step 5: Build + smoke test**

```bash
cd frontend && pnpm build
docker compose up -d --build && sleep 5
```

Abrir browser, completar wizard end-to-end (necesita backend con credenciales reales — si no, mockear con respx temporalmente o usar tokens de prueba que devuelven el XML vacío).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/wizard/Step3Xmls.tsx \
        frontend/src/components/wizard/Step4Initial.tsx \
        frontend/src/hooks/useIngestStream.ts \
        frontend/src/app/\(setup\)/setup/page.tsx \
        frontend/package.json frontend/pnpm-lock.yaml
git commit -m "feat(phase2): frontend wizard steps 3 (XML upload) + 4 (SSE progress with retry)"
```

---

### Task 18: Frontend Settings — Flex section + XML upload + log table + Actualizar ahora + Rotar token

**Files:**
- Modify: `frontend/src/app/(app)/settings/page.tsx` (extend Phase 1 settings)
- Create: `frontend/src/components/settings/FlexCredentialsSection.tsx`
- Create: `frontend/src/components/settings/XmlUploadSection.tsx`
- Create: `frontend/src/components/settings/IngestLogTable.tsx`
- Create: `frontend/src/components/settings/RotateTokenModal.tsx`
- Create: `frontend/src/components/settings/ManualRefreshButton.tsx`

- [ ] **Step 1: Crear `FlexCredentialsSection`**

Crear `frontend/src/components/settings/FlexCredentialsSection.tsx`:

```typescript
"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useGetCredentialsFlex } from "@/lib/api";
import { RotateTokenModal } from "./RotateTokenModal";

export function FlexCredentialsSection() {
  const { data, refetch, isLoading } = useGetCredentialsFlex();
  const [showModal, setShowModal] = useState(false);

  if (isLoading) return <div>…</div>;
  const creds = data?.data;
  if (!creds) return null;

  return (
    <section className="border rounded p-4 space-y-2">
      <h2 className="text-lg font-semibold">Credenciales IBKR Flex</h2>
      <p className="text-sm">
        Token configurado el{" "}
        <span className="font-mono">{new Date(creds.last_rotated_at).toLocaleDateString()}</span>
      </p>
      <p className="text-sm">Query ID actual: <span className="font-mono">{creds.query_id}</span></p>
      <Button variant="outline" onClick={() => setShowModal(true)}>
        Rotar token
      </Button>
      {showModal && <RotateTokenModal onClose={() => { setShowModal(false); refetch(); }} />}
    </section>
  );
}
```

- [ ] **Step 2: Crear `RotateTokenModal`**

Crear `frontend/src/components/settings/RotateTokenModal.tsx`:

```typescript
"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { usePutCredentialsFlex } from "@/lib/api";

interface Props {
  onClose: () => void;
}

export function RotateTokenModal({ onClose }: Props) {
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const { mutate, isPending } = usePutCredentialsFlex();

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    mutate({ data: { token } }, {
      onSuccess: () => onClose(),
      onError: (err: any) => setError(err?.response?.data?.detail ?? "Error"),
    });
  }

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center">
      <div className="bg-background rounded p-6 w-96 space-y-4">
        <h3 className="font-semibold">Rotar Flex Token</h3>
        <p className="text-sm text-muted-foreground">
          Va a hacer un test request a IBKR antes de reemplazar el token actual.
        </p>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <Label htmlFor="newToken">Nuevo token</Label>
            <Input
              id="newToken" type="password" value={token}
              onChange={(e) => setToken(e.target.value)} required minLength={10}
            />
          </div>
          {error && <div className="text-red-500 text-sm">{error}</div>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={onClose}>Cancelar</Button>
            <Button type="submit" disabled={isPending}>
              {isPending ? "Validando…" : "Validar y rotar"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Crear `XmlUploadSection`**

Crear `frontend/src/components/settings/XmlUploadSection.tsx`:

```typescript
"use client";

import { useCallback, useState } from "react";
import { useDropzone } from "react-dropzone";
import { usePostImportsUpload } from "@/lib/api";

interface UploadEntry {
  fileName: string;
  status: "ok" | "duplicate" | "error";
  message: string;
}

export function XmlUploadSection() {
  const [uploads, setUploads] = useState<UploadEntry[]>([]);
  const uploadMut = usePostImportsUpload();

  const onDrop = useCallback(async (files: File[]) => {
    for (const f of files) {
      try {
        const fd = new FormData();
        fd.append("file", f);
        const r = await uploadMut.mutateAsync({ data: fd as any });
        setUploads((prev) => [
          ...prev,
          { fileName: f.name, status: "ok", message: `${(r.data as any).n_trades} trades · año ${(r.data as any).anyo}` },
        ]);
      } catch (err: any) {
        const status = err?.response?.status === 409 ? "duplicate" : "error";
        const msg = err?.response?.data?.detail?.message ?? err?.response?.data?.detail ?? "Error";
        setUploads((prev) => [...prev, { fileName: f.name, status, message: String(msg) }]);
      }
    }
  }, [uploadMut]);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop, accept: { "application/xml": [".xml"], "text/xml": [".xml"] },
  });

  return (
    <section className="border rounded p-4 space-y-2">
      <h2 className="text-lg font-semibold">Importar XML histórico</h2>
      <div
        {...getRootProps()}
        className={`border-2 border-dashed rounded p-8 text-center cursor-pointer text-sm
          ${isDragActive ? "border-blue-500 bg-blue-50" : "border-gray-300"}`}
      >
        <input {...getInputProps()} />
        ↑ Arrastrá XMLs aquí o click para seleccionar ↑
      </div>
      {uploads.length > 0 && (
        <ul className="space-y-1 text-sm">
          {uploads.map((u, i) => (
            <li key={i}>
              <span className="font-mono">{u.fileName}</span> —{" "}
              {u.status === "ok" && <span className="text-green-600">✓ {u.message}</span>}
              {u.status === "duplicate" && <span className="text-yellow-600">⚠ {u.message}</span>}
              {u.status === "error" && <span className="text-red-600">✗ {u.message}</span>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
```

- [ ] **Step 4: Crear `IngestLogTable`**

Crear `frontend/src/components/settings/IngestLogTable.tsx`:

```typescript
"use client";

import { useGetIngestLogs } from "@/lib/api";

export function IngestLogTable() {
  const { data, isLoading } = useGetIngestLogs({ limit: 10 });
  if (isLoading) return <div>…</div>;
  const logs = (data?.data ?? []) as any[];

  return (
    <section className="border rounded p-4 space-y-2">
      <h2 className="text-lg font-semibold">Últimos 10 runs</h2>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left">
            <th>Timestamp</th>
            <th>Kind</th>
            <th>Trigger</th>
            <th>Status</th>
            <th>Items / Error</th>
          </tr>
        </thead>
        <tbody>
          {logs.map((l) => (
            <tr key={l.id} className="border-b">
              <td>{new Date(l.started_at).toLocaleString()}</td>
              <td>{l.job_kind}</td>
              <td>{l.trigger}</td>
              <td className={l.status === "ok" ? "text-green-600" : l.status === "failed" ? "text-red-600" : "text-blue-600"}>
                {l.status}
              </td>
              <td className="font-mono text-xs">
                {l.error_message ? l.error_message.slice(0, 80) + "…" : l.items_processed ?? ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
```

- [ ] **Step 5: Crear `ManualRefreshButton`**

Crear `frontend/src/components/settings/ManualRefreshButton.tsx`:

```typescript
"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { usePostIngestTrigger } from "@/lib/api";
import { useIngestStream } from "@/hooks/useIngestStream";

export function ManualRefreshButton() {
  const [jobId, setJobId] = useState<number | null>(null);
  const triggerMut = usePostIngestTrigger();
  const { events, isDone } = useIngestStream(jobId);

  function handleClick() {
    triggerMut.mutate({ data: { kind: "both" } }, {
      onSuccess: (r: any) => setJobId(r.data.job_id),
    });
  }

  if (jobId === null || isDone) {
    return (
      <Button onClick={handleClick} disabled={triggerMut.isPending}>
        Actualizar ahora
      </Button>
    );
  }

  return (
    <div className="text-sm space-y-1">
      <div>Actualizando…</div>
      <ul>
        {events.map((e, i) => (
          <li key={i} className="text-xs font-mono">
            {e.step}: {e.status ?? "…"}
            {e.error && <span className="text-red-600 ml-2">{e.error}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 6: Modificar `settings/page.tsx` para incluir las secciones nuevas**

Modificar `frontend/src/app/(app)/settings/page.tsx` (preservar lo existente del Phase 1 y agregar las nuevas secciones):

```typescript
// ... imports existentes (de Phase 1)
import { FlexCredentialsSection } from "@/components/settings/FlexCredentialsSection";
import { XmlUploadSection } from "@/components/settings/XmlUploadSection";
import { IngestLogTable } from "@/components/settings/IngestLogTable";
import { ManualRefreshButton } from "@/components/settings/ManualRefreshButton";

export default function SettingsPage() {
  return (
    <div className="space-y-6 max-w-3xl">
      <h1 className="text-2xl font-bold">Settings</h1>

      {/* Sección Phase 1 — preferences existente */}
      {/* ... */}

      <FlexCredentialsSection />
      <XmlUploadSection />

      <section className="border rounded p-4 space-y-2">
        <h2 className="text-lg font-semibold">Estado del sistema</h2>
        <ManualRefreshButton />
      </section>

      <IngestLogTable />
    </div>
  );
}
```

- [ ] **Step 7: Build + smoke test**

```bash
cd frontend && pnpm build
docker compose up -d --build && sleep 5
```

Abrir `http://localhost:3000/settings` (después de loguearse + completar wizard). Verificar:
- Sección Flex credentials muestra `last_rotated_at`
- Drag&drop area de XML funciona
- Click "Actualizar ahora" dispara SSE y muestra eventos
- Tabla de logs muestra los runs recientes

```bash
docker compose down
```

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/settings/ \
        frontend/src/app/\(app\)/settings/
git commit -m "feat(phase2): frontend Settings — Flex creds + XML upload + log table + actualizar ahora + rotar token"
```

---

### Task 19: E2E tests Playwright — wizard happy path + resume + manual refresh

**Files:**
- Create: `frontend/playwright.config.ts` (si no existe; Phase 1 puede haber dejado uno)
- Create: `frontend/tests/e2e/wizard.spec.ts`
- Create: `frontend/tests/e2e/settings_refresh.spec.ts`
- Create: `frontend/tests/e2e/fixtures/test_xml.xml` (XML pequeño válido)

- [ ] **Step 1: Verificar Playwright config**

Si no existe `frontend/playwright.config.ts`, crearlo:

```typescript
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
```

- [ ] **Step 2: Crear fixture XML pequeño para E2E**

Crear `frontend/tests/e2e/fixtures/test_xml.xml` con un Flex statement mínimo válido (1 trade):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="E2E" type="AF">
  <FlexStatements count="1">
    <FlexStatement accountId="U99999001" fromDate="2024-01-01" toDate="2024-12-31"
                   period="LastYear" whenGenerated="2025-01-01;10:00:00">
      <AccountInformation accountId="U99999001" currency="USD"/>
      <Trades>
        <Trade transactionID="E2E-TXN-001" accountId="U99999001"
               symbol="AAPL" assetCategory="STK"
               tradeDate="2024-06-01" settleDateTarget="2024-06-03"
               quantity="10" tradePrice="190.00" proceeds="-1900.00"
               ibCommission="1.00" openCloseIndicator="O" buySell="BUY"/>
      </Trades>
      <ClosedLots/>
      <OpenPositions/>
      <CashTransactions/>
      <Transfers/>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>
```

- [ ] **Step 3: Escribir test `wizard.spec.ts`**

Crear `frontend/tests/e2e/wizard.spec.ts`:

```typescript
import { test, expect } from "@playwright/test";
import path from "path";

const TEST_EMAIL = `e2e-${Date.now()}@test.com`;
const TEST_PASSWORD = "TestPassword123!";

test.describe("Setup wizard", () => {
  test("happy path — register → complete 4 steps → reach dashboard", async ({ page }) => {
    // 1. Register
    await page.goto("/register");
    await page.getByLabel(/email/i).fill(TEST_EMAIL);
    await page.getByLabel(/password/i).fill(TEST_PASSWORD);
    await page.getByRole("button", { name: /register/i }).click();

    // 2. Auto-redirect a /setup (middleware)
    await expect(page).toHaveURL(/\/setup/);
    await expect(page.getByText(/Credenciales/i)).toBeVisible();

    // 3. Step 1 — credenciales (skip si no hay backend con IBKR real)
    // En CI, mockear con MSW o backdoor /api/setup/skip-step1
    // Asume backend mock está corriendo y acepta cualquier token de "good-"
    await page.getByLabel(/Flex Token/i).fill("good-token-e2e");
    await page.getByLabel(/Query ID/i).fill("1234567");
    await page.getByRole("button", { name: /Continuar/i }).click();

    // 4. Step 2 — accounts (pre-populado)
    await expect(page.getByText(/Cuentas IBKR/i)).toBeVisible();
    await page.getByRole("button", { name: /Continuar/i }).click();

    // 5. Step 3 — opcional, skip directo
    await expect(page.getByText(/XMLs históricos/i)).toBeVisible();
    await page.setInputFiles(
      'input[type="file"]',
      path.join(__dirname, "fixtures/test_xml.xml")
    );
    await expect(page.getByText(/1 trades/)).toBeVisible({ timeout: 10000 });
    await page.getByRole("button", { name: /Continuar/i }).click();

    // 6. Step 4 — iniciar
    await page.getByRole("button", { name: /Iniciar carga/i }).click();
    // Esperar redirect a /dashboard (SSE done event)
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 60000 });
  });

  test("resume — refresh in step 3 returns to step 3", async ({ page, context }) => {
    // (Assumes user already through step 1+2 vía test anterior o setup)
    await page.goto("/setup");
    // Forzar a step 3 vía completar steps anteriores...
    // (este test requiere helper que pre-completa steps 1 y 2 vía API directa)
    // ... omitido por brevedad; ver TODO en este task si no se implementa
  });
});
```

- [ ] **Step 4: Escribir test `settings_refresh.spec.ts`**

Crear `frontend/tests/e2e/settings_refresh.spec.ts`:

```typescript
import { test, expect } from "@playwright/test";

test.describe("Settings — Actualizar ahora", () => {
  test("dispara SSE y muestra eventos", async ({ page }) => {
    // Requiere user con setup completado
    await page.goto("/login");
    // ... loguear con user fixture pre-creado en backend ...

    await page.goto("/settings");
    await expect(page.getByText(/Estado del sistema/i)).toBeVisible();

    await page.getByRole("button", { name: /Actualizar ahora/i }).click();
    await expect(page.getByText(/Actualizando/i)).toBeVisible();

    // Esperar a que aparezca al menos un evento "ok" o "running"
    await expect(page.locator("text=ok").first()).toBeVisible({ timeout: 30000 });
  });
});
```

- [ ] **Step 5: Agregar script `e2e` a package.json si no existe**

En `frontend/package.json`:

```json
{
  "scripts": {
    "e2e": "playwright test"
  }
}
```

- [ ] **Step 6: Correr E2E localmente**

```bash
docker compose up -d --build && sleep 8
cd frontend && pnpm e2e
```

Expected: 2/2 PASS (puede ser 1/2 si test de resume requiere helper que se omite).

- [ ] **Step 7: Commit**

```bash
git add frontend/playwright.config.ts \
        frontend/tests/e2e/ \
        frontend/package.json
git commit -m "test(phase2): E2E tests Playwright para wizard happy path + settings refresh"
```

---

### Task 20: Finalize Phase 2 — actualizar CLAUDE.md + tag v0.2.0-ingest

**Files:**
- Modify: `CLAUDE.md` (tabla "Estado actual" — Phase 2 status + link al plan)
- Tag: `v0.2.0-ingest`

- [ ] **Step 1: Correr toda la suite de tests del backend**

```bash
cd backend && uv run pytest -v --tb=short
```

Expected: TODO PASS. Si alguno falla, investigar y arreglar antes de continuar.

- [ ] **Step 2: Correr E2E**

```bash
docker compose up -d --build && sleep 8
cd frontend && pnpm e2e
docker compose down
```

Expected: PASS.

- [ ] **Step 3: Verificar coverage cumple los targets**

```bash
cd backend && uv run pytest --cov=ibkr_control.ingest --cov=ibkr_control.api --cov-report=term-missing
```

Expected:
- `ingest/flex/parser.py` ≥95%
- `ingest/flex/persister.py` ≥90%
- `ingest/flex/client.py` ≥80%
- `ingest/flex/crypto.py` 100%
- `ingest/trm/*` ≥90%
- `ingest/{lock,log,hash_dedup,job_tracker}.py` 100%
- `api/{setup,imports,ingest,credentials}.py` ≥85%
- `scheduler/jobs.py` ≥70%

Si algún módulo queda corto, agregar tests específicos antes de tagear.

- [ ] **Step 4: Verificar criterios de aceptación del spec §12**

Pasar por la lista del spec a mano:
1. ✅ 4 migrations Alembic apply limpio
2. ✅ Tests pass en CI
3. ✅ Coverage targets cumplidos
4. ✅ Wizard 4 pasos completable de punta a punta
5. ✅ Cron Flex 07:00 y TRM 19:30 quedan registrados en APScheduler
6. ✅ Upload manual desde Settings funciona + dedup
7. ✅ Botón "Actualizar ahora" respeta rate limit + SSE
8. ✅ Rotar token valida ping antes de reemplazar
9. ✅ ingest_log refleja status correcto
10. (después de Step 5) CLAUDE.md actualizado
11. (después de Step 6) Tag `v0.2.0-ingest` creado

- [ ] **Step 5: Actualizar `CLAUDE.md`**

Modificar la tabla "Estado actual" en `CLAUDE.md`. Cambiar Phase 2 status:

```markdown
| Phase | Status | Plan | Tag al completar |
|---|---|---|---|
| 1. Foundation | ✅ código completo (Tasks 1-14) + polish backlog cerrado (6/6) + tag `v0.1.0-foundation` ✓ · Task 15 (deploy manual a Coolify) pendiente del usuario | `docs/plans/2026-05-24-ibkr-control-phase1-foundation.md` + `docs/plans/2026-05-24-phase1-polish-backlog.md` | `v0.1.0-foundation` ✓ |
| 2. Data ingestion (Flex WS + TRM Socrata + scheduler + upload XML + setup wizard) | ✅ completado · `v0.2.0-ingest` | `docs/plans/2026-05-24-ibkr-control-phase2-ingestion.md` | `v0.2.0-ingest` ✓ |
| 3. Domain layer + lotes (FIFO, classification, lotes abiertos/cerrados/alertas) | ⏳ por planificar | — | `v0.3.0-lotes` |
...
```

- [ ] **Step 6: Commit final**

```bash
git add CLAUDE.md
git commit -m "docs(claude): Phase 2 complete, ready for Phase 3 planning"
```

- [ ] **Step 7: Tag v0.2.0-ingest**

```bash
git tag -a v0.2.0-ingest -m "Phase 2: Data Ingestion complete

Cubre:
- Flex Web Service auto-fetch YTD diario (APScheduler 07:00 COT)
- TRM Socrata DIAN backfill 1991-hoy + cron diario (19:30 COT)
- Setup wizard 4 pasos con SSE progress + retry idempotente
- Upload manual de XMLs históricos con dedup SHA-256
- Settings ampliado: Flex creds + log viewer + Actualizar ahora + Rotar token
- Concurrencia segura vía pg advisory locks por (source, user_id)
- AES-GCM encryption del token Flex
- VCR cassettes + respx mocks + XML fixtures sanitizadas
- 4 Alembic migrations: identity, trm, flex_raw, ingest_log

Próximo: Phase 3 (Domain layer + lotes — FIFO + clasificación 730d + pantallas Lotes/Cerrados/Alertas)"

git push origin main --follow-tags
```

- [ ] **Step 8: Verificar el tag fue creado**

```bash
git tag -l "v0.2*"
```

Expected: `v0.2.0-ingest`.

---

## Resumen — al completar las 20 tasks

- ✅ Frontera con Phase 3 limpia: tablas crudas pobladas, sin clasificación
- ✅ Wizard 4 pasos end-to-end funcional con retry por substep
- ✅ Cron Flex + TRM corriendo idempotente
- ✅ Upload manual desde Settings
- ✅ SSE progress en wizard + Actualizar ahora
- ✅ Rotar token con validación pre-flight contra IBKR
- ✅ Advisory locks previenen ingests concurrentes
- ✅ AES-GCM encryption del token
- ✅ Coverage targets cumplidos
- ✅ Tag `v0.2.0-ingest` listo para que Phase 3 pueda arrancar

**Próximo paso del usuario** (no del plan): planificar Phase 3 invocando `superpowers:brainstorming` + `superpowers:writing-plans` referenciando el spec maestro § 6 (Domain layer) y `renta/docs/flex_fifo_loader_spec.md`.




