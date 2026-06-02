# Persister Cleanup — Counterparties + transfer_lots drop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restaurar el invariante "`accounts` = solo cuentas propias" modelando counterparties externos como entidad de primera clase (#6), y eliminar la tabla `transfer_lots` impoblable (#7) — ambos en un branch pre-Phase-3.

**Architecture:** Tabla `counterparties` + exclusive arc (`CHECK` exactly-one por lado) en `transfers`. El persister deja de crear `Account` para peers de transfers; los no-propios van a `counterparties`. `transfer_lots` se borra (schema muerto: Activity Flex `<TransferLot>` es sibling no-anidado y carece de cost_basis/open_date). Todo el schema change en una Alembic revision atómica con data-migration.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x async, Alembic, Postgres 16, pytest + testcontainers-postgres. Tests arman el schema vía `Base.metadata.create_all` (modelos) excepto los de migración que corren `alembic upgrade head` en un container fresco.

**Spec:** `docs/specs/2026-06-02-persister-counterparties-cleanup-design.md`

**Branch:** `fix/persister-counterparties` (ya creado desde `main`).

---

## File Structure

**Crear:**
- `backend/src/ibkr_control/db/models/counterparties.py` — modelo `Counterparty`
- `backend/alembic/versions/<rev>_counterparties_and_drop_transfer_lots.py` — migración atómica
- `backend/tests/fixtures/xml/ACTIVITY_2026_FOP_sanitized.xml` — fixture sanitizado con FOP externo + INTERNAL
- `backend/tests/test_counterparties_migration.py` — test de migración (testcontainers)

**Modificar:**
- `backend/src/ibkr_control/db/models/flex_raw.py` — `Transfer` (+2 cols, +2 CHECK), borrar `TransferLot`
- `backend/src/ibkr_control/db/__init__.py` — exports (+`Counterparty`, −`TransferLot`)
- `backend/src/ibkr_control/ingest/flex/_models.py` — borrar `ParsedTransferLot` + campo `lots`
- `backend/src/ibkr_control/ingest/flex/parser.py` — borrar loop de lots + import; corregir comentario `TransferLots`
- `backend/src/ibkr_control/ingest/flex/persister.py` — `_ensure_counterparties`, allowlist sin transfers, resolución de peer, borrar bloque de lots
- `backend/src/ibkr_control/ingest/flex/_known_tags.py` — comentario de `TransferLots`
- `backend/tests/ingest/flex/test_persister.py` — quitar import `ParsedTransferLot`
- `backend/tests/ingest/flex/test_persister_idempotent.py` — reemplazar `test_transfer_lots_not_duplicated_on_reingest`
- `backend/tests/test_phase2_flex_raw_migration.py` — quitar `transfer_lots` del set esperado

> **Nota de orden:** el drift test `test_migrations_apply_cleanly_and_match_metadata` queda **rojo entre Task 1 y Task 3** (los modelos cambian antes de existir la migración). Cada task corre solo sus tests específicos; el drift test vuelve verde en Task 3. Esto es esperado y se nota explícitamente en cada paso.

> **Comandos:** todos los `pytest`/`alembic` corren dentro del container backend con uv. Prefijo estándar: `docker compose exec -T backend sh -c "cd /app && <cmd>"`. Si preferís local: `cd backend && uv run <cmd>` (requiere DB accesible). El plan usa el prefijo de container.

---

## Task 1: #7 — Eliminar transfer_lots (modelo + parser + persister + tests)

**Files:**
- Modify: `backend/src/ibkr_control/db/models/flex_raw.py:190-199` (borrar `class TransferLot`)
- Modify: `backend/src/ibkr_control/db/__init__.py:9,18` (quitar `TransferLot`)
- Modify: `backend/src/ibkr_control/ingest/flex/_models.py:87,91-95` (borrar campo `lots` + `ParsedTransferLot`)
- Modify: `backend/src/ibkr_control/ingest/flex/parser.py:24,153-155,542-549`
- Modify: `backend/src/ibkr_control/ingest/flex/persister.py:7-17,47,323-389`
- Modify: `backend/src/ibkr_control/ingest/flex/_known_tags.py:21`
- Modify: `backend/tests/ingest/flex/test_persister.py:11`
- Test: `backend/tests/ingest/flex/test_persister_idempotent.py:222-269`

- [ ] **Step 1: Reescribir el test de idempotencia de transfers (sin lots)**

En `backend/tests/ingest/flex/test_persister_idempotent.py`, reemplazar la función `test_transfer_lots_not_duplicated_on_reingest` (líneas ~222-269) por:

```python
@pytest.mark.asyncio
async def test_transfer_not_duplicated_on_reingest(
    db_session: AsyncSession, sample_user, sample_account
):
    """Re-ingest del mismo transfer NO lo duplica (ON CONFLICT transaction_id
    DO NOTHING). transfer_lots fue eliminado (impoblable desde Activity Flex,
    spec 2026-06-02)."""
    transfer = ParsedTransfer(
        transaction_id="XFER-IDEMP-1",
        transfer_date=date(2026, 4, 30),
        direction="IN",
        src_ibkr_account_id=None,
        dst_ibkr_account_id=sample_account.ibkr_account_id,
        symbol="GLOB",
        qty=Decimal("94"),
        transfer_type="FOP",
    )
    p1 = _minimal_parsed(n_trades=0, account_id=sample_account.ibkr_account_id)
    p1.transfers = [transfer]
    await persist(
        db_session, parsed=p1, user_id=sample_user.id,
        xml_bytes=b"<xfer v1/>", source="web_service",
    )
    await db_session.commit()

    p2 = _minimal_parsed(n_trades=0, account_id=sample_account.ibkr_account_id)
    p2.transfers = [transfer]
    await persist(
        db_session, parsed=p2, user_id=sample_user.id,
        xml_bytes=b"<xfer v2/>", source="web_service",
    )

    n_transfers = await db_session.scalar(
        select(func.count()).select_from(Transfer)
    )
    assert n_transfers == 1
```

En el bloque de imports de ese archivo (línea ~27-34), quitar `TransferLot` y `ParsedTransferLot`:

```python
# ANTES (ejemplo):
#   from ibkr_control.db.models.flex_raw import (..., Transfer, TransferLot, ...)
#   from ibkr_control.ingest.flex._models import (..., ParsedTransfer, ParsedTransferLot, ...)
# DESPUÉS: borrar TransferLot de la primera y ParsedTransferLot de la segunda.
```

> Nota: `src_ibkr_account_id=None` con `dst` = cuenta propia hace que el lado src quede sin peer. Tras Task 2/3 el exclusive arc exige exactamente uno por lado; este test usa el persister actual (pre-arc). Se ajusta en Task 3 (ver Step de ese task). Por ahora valida solo el dedup de transfers.

- [ ] **Step 2: Correr el test, verificar que falla (ParsedTransfer aún tiene `lots`, TransferLot import roto)**

Run: `docker compose exec -T backend sh -c "cd /app && uv run pytest tests/ingest/flex/test_persister_idempotent.py::test_transfer_not_duplicated_on_reingest -q"`
Expected: FAIL (ImportError de `TransferLot`/`ParsedTransferLot` que aún existen, o el test viejo aún referenciado).

- [ ] **Step 3: Borrar `class TransferLot` del modelo**

En `backend/src/ibkr_control/db/models/flex_raw.py`, borrar el bloque completo (líneas ~190-199):

```python
class TransferLot(Base):
    __tablename__ = "transfer_lots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    transfer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("transfers.id", ondelete="CASCADE"), nullable=False
    )
    original_open_date: Mapped[date] = mapped_column(Date, nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    cost_basis_usd: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
```

- [ ] **Step 4: Quitar `TransferLot` de los exports**

En `backend/src/ibkr_control/db/__init__.py`:
- Línea 9: quitar `TransferLot,` del import desde `flex_raw`.
- Línea 18: quitar `"TransferLot",` de `__all__`.

- [ ] **Step 5: Borrar `ParsedTransferLot` y el campo `lots`**

En `backend/src/ibkr_control/ingest/flex/_models.py`:
- En `class ParsedTransfer`, borrar la línea `lots: list["ParsedTransferLot"] = field(default_factory=list)` (línea 87).
- Borrar la dataclass completa `ParsedTransferLot` (líneas 91-95):

```python
@dataclass
class ParsedTransferLot:
    original_open_date: date
    qty: Decimal
    cost_basis_usd: Decimal
```

- [ ] **Step 6: Limpiar el parser**

En `backend/src/ibkr_control/ingest/flex/parser.py`:
- Línea 24: quitar `ParsedTransferLot,` del import.
- Líneas 542-549: borrar el loop muerto de lots dentro de `_parse_transfers`:

```python
        # Nested TransferLot rows (present in some query types)
        for lot in tr.iterchildren("TransferLot"):
            open_date = _parse_date(
                lot.get("openDateTime") or lot.get("originalOpenDate")
            )
            if open_date is None:
                continue
            transfer.lots.append(ParsedTransferLot(
                original_open_date=open_date,
                qty=_dec(lot.get("quantity")),
                cost_basis_usd=_dec(lot.get("costBasis")),
            ))
```

(El `out.append(transfer)` que sigue se conserva.)

- Líneas 153-155: corregir el comentario del branch `TransferLots`:

```python
            elif tag == "TransferLots":
                # Ignorado deliberadamente: <TransferLot> es sibling de <Transfer>
                # (no anidado) y carece de cost_basis/open_date → impoblable desde
                # Activity Flex. Tabla transfer_lots eliminada (spec 2026-06-02).
                pass
```

- [ ] **Step 7: Limpiar el persister (borrar bloque de lots, simplificar transfers a `_upsert_immutable`)**

En `backend/src/ibkr_control/ingest/flex/persister.py`:
- Línea 47: quitar `TransferLot,` del import desde `flex_raw`.
- Docstring (líneas 12-17): borrar el bullet "Transfers + TransferLot: ..." y reemplazar por:

```python
# - Transfers: immutable por transaction_id (ON CONFLICT DO NOTHING).
```

- Reemplazar TODO el bloque de transfers + lots (líneas ~323-389, desde `# === Transfers + TransferLots` hasta justo antes de `# === OpenPositionLots`) por (la lógica de counterparty se agrega en Task 3; por ahora solo se elimina lots y se simplifica):

```python
    # === Transfers (immutable por transaction_id) ===
    transfer_rows: list[dict] = []
    for tr in parsed.transfers:
        src_shadow = tr.src_ibkr_account_id and _is_shadow_account(
            tr.src_ibkr_account_id
        )
        dst_shadow = tr.dst_ibkr_account_id and _is_shadow_account(
            tr.dst_ibkr_account_id
        )
        if src_shadow or dst_shadow:
            continue
        transfer_rows.append(
            {
                "flex_import_id": fi.id,
                "transaction_id": tr.transaction_id,
                "transfer_date": tr.transfer_date,
                "direction": tr.direction,
                "src_account_id": (
                    accounts_map.get(tr.src_ibkr_account_id)
                    if tr.src_ibkr_account_id
                    else None
                ),
                "dst_account_id": (
                    accounts_map.get(tr.dst_ibkr_account_id)
                    if tr.dst_ibkr_account_id
                    else None
                ),
                "symbol": tr.symbol,
                "qty": tr.qty,
                "transfer_type": tr.transfer_type,
            }
        )

    n_new_transfers = await _upsert_immutable(
        session, Transfer.__table__, transfer_rows, ["transaction_id"]
    )
```

> Esto elimina `_upsert_immutable_returning_inserted` del path de transfers (ya no necesitamos saber cuáles son nuevos para insertar children). Verificar si ese helper tiene otros callers: `grep -rn "_upsert_immutable_returning_inserted" src`. Si no tiene otros, dejarlo en `_upsert_helpers.py` (no borrar en este task — YAGNI inverso: borrar código no usado es válido, pero hacerlo en task separado evita ruido). Anotar como follow-up opcional.

- [ ] **Step 8: Actualizar comentario de `_known_tags.py`**

En `backend/src/ibkr_control/ingest/flex/_known_tags.py:21`:

```python
    "TransferLots",                 # ignored — unpopulatable from Activity Flex (spec 2026-06-02)
```

- [ ] **Step 9: Quitar import `ParsedTransferLot` de test_persister.py**

En `backend/tests/ingest/flex/test_persister.py:11`, quitar `ParsedTransferLot,` del import (verificar antes con `grep -n "ParsedTransferLot" tests/ingest/flex/test_persister.py` que no se use en el cuerpo — confirmado: solo el import).

- [ ] **Step 10: Quitar `transfer_lots` del test de tablas esperadas**

En `backend/tests/test_phase2_flex_raw_migration.py:12-15`, quitar `'transfer_lots',` del set `expected`:

```python
    expected = {
        'flex_imports', 'trades', 'closed_lots', 'open_position_lots',
        'transfers', 'cash_transactions',
    }
```

- [ ] **Step 11: Correr los tests afectados, verificar verde**

Run: `docker compose exec -T backend sh -c "cd /app && uv run pytest tests/ingest/flex/test_persister.py tests/ingest/flex/test_persister_idempotent.py tests/test_phase2_flex_raw_migration.py::test_flex_imports_tables_exist -q"`
Expected: PASS. (El drift test `test_migrations.py` quedará rojo — esperado hasta Task 3, no correrlo todavía.)

- [ ] **Step 12: Commit**

```bash
git add backend/src/ibkr_control backend/tests
git commit -m "refactor(persister): drop transfer_lots (unpopulatable from Activity Flex) [#7]"
```

---

## Task 2: #6 — Modelo Counterparty + columnas counterparty en Transfer + exclusive arc CHECK

**Files:**
- Create: `backend/src/ibkr_control/db/models/counterparties.py`
- Modify: `backend/src/ibkr_control/db/models/flex_raw.py:166-187` (Transfer)
- Modify: `backend/src/ibkr_control/db/__init__.py` (export `Counterparty`)
- Test: `backend/tests/ingest/flex/test_counterparties_model.py` (nuevo)

- [ ] **Step 1: Escribir el test del exclusive arc (falla)**

Crear `backend/tests/ingest/flex/test_counterparties_model.py`:

```python
"""Tests del modelo Counterparty + exclusive arc en transfers (spec 2026-06-02)."""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_counterparties_table_exists(db_session: AsyncSession):
    result = await db_session.execute(text("SELECT to_regclass('counterparties')"))
    assert result.scalar() == "counterparties"


@pytest.mark.asyncio
async def test_counterparty_external_id_unique(db_session: AsyncSession):
    from ibkr_control.db.models.counterparties import Counterparty
    db_session.add(Counterparty(external_id="CS-999999-99"))
    await db_session.commit()
    db_session.add(Counterparty(external_id="CS-999999-99"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def _seed_account_and_cp(db_session):
    from ibkr_control.db.models.accounts import Account
    from ibkr_control.db.models.counterparties import Counterparty
    a = Account(ibkr_account_id="U99999001", currency="USD")
    cp = Counterparty(external_id="CS-999999-99")
    db_session.add_all([a, cp])
    await db_session.commit()
    await db_session.refresh(a)
    await db_session.refresh(cp)
    return a, cp


async def _insert_transfer(db_session, **overrides):
    cols = {
        "transaction_id": "T-ARC-1",
        "transfer_date": date(2026, 4, 30),
        "direction": "IN",
        "src_account_id": None,
        "src_counterparty_id": None,
        "dst_account_id": None,
        "dst_counterparty_id": None,
        "symbol": "GLOB",
        "qty": Decimal("94"),
        "transfer_type": "FOP",
    }
    cols.update(overrides)
    await db_session.execute(text(
        """INSERT INTO transfers
           (transaction_id, transfer_date, direction, src_account_id,
            src_counterparty_id, dst_account_id, dst_counterparty_id, symbol, qty, transfer_type)
           VALUES (:transaction_id, :transfer_date, :direction, :src_account_id,
            :src_counterparty_id, :dst_account_id, :dst_counterparty_id, :symbol, :qty, :transfer_type)"""
    ), cols)


@pytest.mark.asyncio
async def test_exclusive_arc_accepts_exactly_one_per_side(db_session: AsyncSession):
    a, cp = await _seed_account_and_cp(db_session)
    # src = counterparty (externo), dst = account propio → válido
    await _insert_transfer(db_session, src_counterparty_id=cp.id, dst_account_id=a.id)
    await db_session.commit()  # no debe rebotar


@pytest.mark.asyncio
async def test_exclusive_arc_rejects_both_null(db_session: AsyncSession):
    a, cp = await _seed_account_and_cp(db_session)
    # src vacío (both null) → rebota
    await _insert_transfer(db_session, src_account_id=None, src_counterparty_id=None,
                           dst_account_id=a.id)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_exclusive_arc_rejects_both_set(db_session: AsyncSession):
    a, cp = await _seed_account_and_cp(db_session)
    # src = account Y counterparty a la vez → rebota
    await _insert_transfer(db_session, src_account_id=a.id, src_counterparty_id=cp.id,
                           dst_account_id=a.id)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()
```

- [ ] **Step 2: Correr el test, verificar que falla**

Run: `docker compose exec -T backend sh -c "cd /app && uv run pytest tests/ingest/flex/test_counterparties_model.py -q"`
Expected: FAIL ("counterparties" no existe / columnas counterparty no existen).

- [ ] **Step 3: Crear el modelo Counterparty**

Crear `backend/src/ibkr_control/db/models/counterparties.py`:

```python
"""Counterparty externo (broker no-IBKR) referenciado en <Transfer> tags.

Ej. Shareworks/Solium/Morgan Stanley StockPlan (external_id 'CS-YYMMDD-NN').
Espeja a Account: global single-user, sin user_id. El particionado per-user
multi-user aplica a accounts + counterparties juntas (item futuro)."""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, text
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class Counterparty(Base):
    __tablename__ = "counterparties"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    source_label: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
```

- [ ] **Step 4: Agregar columnas + CHECK al modelo Transfer**

En `backend/src/ibkr_control/db/models/flex_raw.py`, en `class Transfer`:
- Ampliar `__table_args__` (actualmente solo el CHECK de direction):

```python
    __table_args__ = (
        CheckConstraint("direction IN ('IN', 'OUT')", name="ck_transfers_direction"),
        CheckConstraint(
            "(src_account_id IS NOT NULL) <> (src_counterparty_id IS NOT NULL)",
            name="ck_transfers_src_arc",
        ),
        CheckConstraint(
            "(dst_account_id IS NOT NULL) <> (dst_counterparty_id IS NOT NULL)",
            name="ck_transfers_dst_arc",
        ),
    )
```

- Agregar las 2 columnas justo después de `dst_account_id` (línea ~184):

```python
    src_counterparty_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("counterparties.id"), nullable=True
    )
    dst_counterparty_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("counterparties.id"), nullable=True
    )
```

(`CheckConstraint`, `ForeignKey`, `BigInteger` ya están importados en `flex_raw.py`.)

- [ ] **Step 5: Exportar Counterparty**

En `backend/src/ibkr_control/db/__init__.py`:
- Agregar import: `from ibkr_control.db.models.counterparties import Counterparty`
- Agregar `"Counterparty",` a `__all__`.

- [ ] **Step 6: Correr el test, verificar verde**

Run: `docker compose exec -T backend sh -c "cd /app && uv run pytest tests/ingest/flex/test_counterparties_model.py -q"`
Expected: PASS (5 tests).

- [ ] **Step 7: Commit**

```bash
git add backend/src/ibkr_control backend/tests/ingest/flex/test_counterparties_model.py
git commit -m "feat(model): Counterparty + exclusive arc on transfers [#6]"
```

---

## Task 3: Migración atómica (counterparties + arc + drop transfer_lots) + test de migración

**Files:**
- Create: `backend/alembic/versions/<rev>_counterparties_and_drop_transfer_lots.py`
- Create: `backend/tests/test_counterparties_migration.py`

- [ ] **Step 1: Generar el esqueleto de la revision**

Run: `docker compose exec -T backend sh -c "cd /app && uv run alembic revision -m 'counterparties and drop transfer_lots'"`
Expected: crea un archivo nuevo en `alembic/versions/` con `down_revision = '2b0b2863c6e9'` autopopulado (es el head actual). Verificar el header.

- [ ] **Step 2: Escribir `upgrade()` y `downgrade()`**

Reemplazar el cuerpo del archivo generado por:

```python
"""counterparties + exclusive arc on transfers; drop transfer_lots

Spec docs/specs/2026-06-02-persister-counterparties-cleanup-design.md
- #6: counterparties table + src/dst_counterparty_id FK + exclusive-arc CHECK.
  Reconcilia accounts huérfanos (counterparties externos mal creados como
  Account) moviéndolos a counterparties.
- #7: drop transfer_lots (impoblable desde Activity Flex).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "<rev>"  # el que generó alembic
down_revision: Union[str, Sequence[str], None] = "2b0b2863c6e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. counterparties
    op.create_table(
        "counterparties",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("source_label", sa.String(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("external_id", name="uq_counterparties_external_id"),
    )

    # 2. transfers: columnas counterparty (FK nullable)
    op.add_column("transfers", sa.Column("src_counterparty_id", sa.BigInteger(), nullable=True))
    op.add_column("transfers", sa.Column("dst_counterparty_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_transfers_src_counterparty", "transfers", "counterparties",
        ["src_counterparty_id"], ["id"],
    )
    op.create_foreign_key(
        "fk_transfers_dst_counterparty", "transfers", "counterparties",
        ["dst_counterparty_id"], ["id"],
    )

    # 3. data-migration: reconciliar accounts huérfanos (counterparties externos).
    #    Huérfano = account referenciado por transfers, sin participation y sin
    #    aparecer en ninguna tabla de hechos (trades/lots/cash/accruals).
    conn = op.get_bind()
    orphans = conn.execute(sa.text("""
        SELECT a.id, a.ibkr_account_id FROM accounts a
        WHERE a.id IN (
            SELECT src_account_id FROM transfers WHERE src_account_id IS NOT NULL
            UNION SELECT dst_account_id FROM transfers WHERE dst_account_id IS NOT NULL
        )
        AND a.id NOT IN (SELECT account_id FROM participations)
        AND a.id NOT IN (SELECT account_id FROM trades)
        AND a.id NOT IN (SELECT account_id FROM closed_lots)
        AND a.id NOT IN (SELECT account_id FROM open_position_lots)
        AND a.id NOT IN (SELECT account_id FROM cash_transactions)
        AND a.id NOT IN (SELECT account_id FROM change_in_dividend_accruals)
        AND a.id NOT IN (SELECT account_id FROM open_dividend_accruals)
    """)).fetchall()

    for acct_id, ext_id in orphans:
        cp_id = conn.execute(sa.text("""
            INSERT INTO counterparties (external_id) VALUES (:ext)
            ON CONFLICT (external_id) DO UPDATE SET external_id = EXCLUDED.external_id
            RETURNING id
        """), {"ext": ext_id}).scalar()
        conn.execute(sa.text(
            "UPDATE transfers SET src_counterparty_id=:cp, src_account_id=NULL "
            "WHERE src_account_id=:a"
        ), {"cp": cp_id, "a": acct_id})
        conn.execute(sa.text(
            "UPDATE transfers SET dst_counterparty_id=:cp, dst_account_id=NULL "
            "WHERE dst_account_id=:a"
        ), {"cp": cp_id, "a": acct_id})
        conn.execute(sa.text("DELETE FROM accounts WHERE id=:a"), {"a": acct_id})

    # 4. assert precondición del exclusive arc (fail-loud antes de crear el CHECK)
    bad = conn.execute(sa.text("""
        SELECT count(*) FROM transfers
        WHERE ((src_account_id IS NOT NULL)::int + (src_counterparty_id IS NOT NULL)::int) <> 1
           OR ((dst_account_id IS NOT NULL)::int + (dst_counterparty_id IS NOT NULL)::int) <> 1
    """)).scalar()
    if bad:
        raise RuntimeError(
            f"{bad} transfers violate exclusive-arc precondition (a side with "
            f"zero or two endpoints); cannot add CHECK. Investigate before migrating."
        )

    # 5. CHECK constraints (exclusive arc, exactly-one por lado)
    op.create_check_constraint(
        "ck_transfers_src_arc", "transfers",
        "(src_account_id IS NOT NULL) <> (src_counterparty_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_transfers_dst_arc", "transfers",
        "(dst_account_id IS NOT NULL) <> (dst_counterparty_id IS NOT NULL)",
    )

    # 6. drop transfer_lots (impoblable, #7)
    op.drop_table("transfer_lots")


def downgrade() -> None:
    # 1. recrear transfer_lots
    op.create_table(
        "transfer_lots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("transfer_id", sa.BigInteger(),
                  sa.ForeignKey("transfers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("original_open_date", sa.Date(), nullable=False),
        sa.Column("qty", sa.Numeric(20, 8), nullable=False),
        sa.Column("cost_basis_usd", sa.Numeric(20, 4), nullable=False),
    )

    # 2. drop CHECK
    op.drop_constraint("ck_transfers_src_arc", "transfers", type_="check")
    op.drop_constraint("ck_transfers_dst_arc", "transfers", type_="check")

    # 3. reverse data-migration: re-crear Account desde counterparties + re-apuntar.
    #    Restaura el estado buggy a propósito (reversibilidad).
    conn = op.get_bind()
    cps = conn.execute(sa.text("SELECT id, external_id FROM counterparties")).fetchall()
    for cp_id, ext_id in cps:
        acct_id = conn.execute(sa.text("""
            INSERT INTO accounts (ibkr_account_id, currency) VALUES (:ext, 'USD')
            ON CONFLICT (ibkr_account_id) DO UPDATE SET ibkr_account_id = EXCLUDED.ibkr_account_id
            RETURNING id
        """), {"ext": ext_id}).scalar()
        conn.execute(sa.text(
            "UPDATE transfers SET src_account_id=:a, src_counterparty_id=NULL "
            "WHERE src_counterparty_id=:cp"
        ), {"a": acct_id, "cp": cp_id})
        conn.execute(sa.text(
            "UPDATE transfers SET dst_account_id=:a, dst_counterparty_id=NULL "
            "WHERE dst_counterparty_id=:cp"
        ), {"a": acct_id, "cp": cp_id})

    # 4. drop columnas counterparty
    op.drop_constraint("fk_transfers_src_counterparty", "transfers", type_="foreignkey")
    op.drop_constraint("fk_transfers_dst_counterparty", "transfers", type_="foreignkey")
    op.drop_column("transfers", "src_counterparty_id")
    op.drop_column("transfers", "dst_counterparty_id")

    # 5. drop counterparties
    op.drop_table("counterparties")
```

Reemplazar `"<rev>"` por el ID que alembic generó en Step 1.

- [ ] **Step 3: Escribir el test de migración (testcontainers)**

Crear `backend/tests/test_counterparties_migration.py`. Reusar el patrón exacto de `tests/test_phase25_persister_migration.py`: container dedicado + `command.upgrade(cfg, rev)` corrido en un thread (el `env.py` invoca `asyncio.run` adentro, no puede correr en el loop de pytest-asyncio). La diferencia clave: subir a **`2b0b2863c6e9`** (down_revision), sembrar el huérfano, y recién entonces `upgrade head`.

```python
"""Migración counterparties + drop transfer_lots (spec 2026-06-02)."""
import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer

DOWN_REV = "2b0b2863c6e9"


def _alembic_cfg(async_url: str) -> Config:
    import os
    os.environ["DATABASE_URL"] = async_url
    os.environ["JWT_SECRET"] = "test-secret-32-chars-minimum-please-ok"
    from ibkr_control.config import get_settings
    get_settings.cache_clear()
    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    return cfg


@pytest.mark.asyncio
async def test_upgrade_reconciles_orphan_and_drops_transfer_lots():
    with PostgresContainer("postgres:16-alpine") as pg:
        sync_url = pg.get_connection_url()  # postgresql+psycopg2://...
        async_url = sync_url.replace("psycopg2", "asyncpg")
        cfg = _alembic_cfg(async_url)

        # (1) subir hasta down_revision (schema previo a este cambio)
        await asyncio.to_thread(command.upgrade, cfg, DOWN_REV)

        # (2) seed: cuenta propia U1, account huérfano CSX, transfer FOP IN
        eng = create_async_engine(async_url)
        async with eng.begin() as conn:
            u1 = await conn.scalar(text(
                "INSERT INTO accounts (ibkr_account_id, currency) "
                "VALUES ('U99999001','USD') RETURNING id"))
            csx = await conn.scalar(text(
                "INSERT INTO accounts (ibkr_account_id, currency) "
                "VALUES ('CS-999999-99','USD') RETURNING id"))
            await conn.execute(text(
                "INSERT INTO transfers (transaction_id, transfer_date, direction, "
                "src_account_id, dst_account_id, symbol, qty, transfer_type) "
                "VALUES ('T-FOP-1','2026-04-30','IN', :csx, :u1, 'GLOB', 94, 'FOP')"
            ), {"csx": csx, "u1": u1})

        # (3) upgrade head (aplica la revision nueva)
        await asyncio.to_thread(command.upgrade, cfg, "head")

        async with eng.begin() as conn:
            cps = [r[0] for r in await conn.execute(text("SELECT external_id FROM counterparties"))]
            assert "CS-999999-99" in cps
            row = (await conn.execute(text(
                "SELECT src_account_id, src_counterparty_id FROM transfers "
                "WHERE transaction_id='T-FOP-1'"))).first()
            assert row[0] is None and row[1] is not None  # src re-apuntado a counterparty
            n_orphan = await conn.scalar(text(
                "SELECT count(*) FROM accounts WHERE ibkr_account_id='CS-999999-99'"))
            assert n_orphan == 0
            assert await conn.scalar(text("SELECT to_regclass('transfer_lots')")) is None
        await eng.dispose()


@pytest.mark.asyncio
async def test_downgrade_reverts_cleanly():
    with PostgresContainer("postgres:16-alpine") as pg:
        async_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        cfg = _alembic_cfg(async_url)
        await asyncio.to_thread(command.upgrade, cfg, "head")
        await asyncio.to_thread(command.downgrade, cfg, DOWN_REV)
        eng = create_async_engine(async_url)
        async with eng.begin() as conn:
            assert await conn.scalar(text("SELECT to_regclass('transfer_lots')")) is not None
            assert await conn.scalar(text("SELECT to_regclass('counterparties')")) is None
        await eng.dispose()
```

> Verificar el formato exacto de `pg.get_connection_url()` y el reemplazo de driver contra `test_phase25_persister_migration.py` (puede usar `psycopg2`→`asyncpg` o `psycopg`→`asyncpg`). Copiar el helper de URL de ese archivo si difiere.

- [ ] **Step 4: Correr el test de migración, iterar hasta verde**

Run: `docker compose exec -T backend sh -c "cd /app && uv run pytest tests/test_counterparties_migration.py -q"`
Expected: PASS (2 tests).

- [ ] **Step 5: Correr el drift test (ahora debe estar VERDE)**

Run: `docker compose exec -T backend sh -c "cd /app && uv run pytest tests/test_migrations.py -q"`
Expected: PASS — `Base.metadata` (counterparties + cols + sin transfer_lots) coincide con `alembic upgrade head`.

- [ ] **Step 6: Aplicar la migración a la DB de dev**

Run: `docker compose exec -T backend sh -c "cd /app && uv run alembic upgrade head"`
Expected: aplica sin error; el huérfano `CS-999999-99` real queda migrado a `counterparties`.
Verificar: `docker compose exec -T postgres psql -U ibkr -d ibkr_control -c "SELECT external_id FROM counterparties; SELECT to_regclass('transfer_lots');"`
Expected: `CS-999999-99` listado; `transfer_lots` → NULL (no existe).

- [ ] **Step 7: Commit**

```bash
git add backend/alembic/versions backend/tests/test_counterparties_migration.py
git commit -m "feat(migration): counterparties + exclusive arc; drop transfer_lots [#6][#7]"
```

---

## Task 4: Persister #6 — _ensure_counterparties + allowlist sin transfers + resolución de peer

**Files:**
- Modify: `backend/src/ibkr_control/ingest/flex/persister.py` (allowlist ~108-137, bloque transfers de Task 1, nuevo helper al final)
- Test: `backend/tests/ingest/flex/test_persister.py` (nuevo test)

- [ ] **Step 1: Escribir el test de resolución de counterparty (falla)**

En `backend/tests/ingest/flex/test_persister.py`, agregar (usa los helpers/fixtures ya presentes en el archivo; `_minimal_parsed` y `ParsedTransfer` ya están importados):

```python
@pytest.mark.asyncio
async def test_external_transfer_peer_becomes_counterparty_not_account(
    db_session: AsyncSession, sample_user
):
    """Un <Transfer> FOP IN desde un broker externo (CS-...) crea fila en
    counterparties, NO en accounts; el transfer queda con src_counterparty_id
    set y src_account_id NULL (exclusive arc)."""
    from ibkr_control.db.models.accounts import Account
    from ibkr_control.db.models.counterparties import Counterparty
    from sqlalchemy import func, select

    OWN = "U99999002"
    EXT = "CS-999999-99"
    transfer = ParsedTransfer(
        transaction_id="XFER-FOP-1",
        transfer_date=date(2026, 4, 30),
        direction="IN",
        src_ibkr_account_id=EXT,        # peer externo
        dst_ibkr_account_id=OWN,        # cuenta propia
        symbol="GLOB",
        qty=Decimal("94"),
        transfer_type="FOP",
    )
    p = _minimal_parsed(n_trades=0, account_id=OWN)  # OWN va en <AccountInformation>
    p.transfers = [transfer]
    await persist(
        db_session, parsed=p, user_id=sample_user.id,
        xml_bytes=b"<fop/>", source="web_service",
    )
    await db_session.commit()

    # EXT NO está en accounts
    n_ext_acct = await db_session.scalar(
        select(func.count()).select_from(Account).where(Account.ibkr_account_id == EXT)
    )
    assert n_ext_acct == 0
    # EXT SÍ está en counterparties
    cp = await db_session.scalar(
        select(Counterparty).where(Counterparty.external_id == EXT)
    )
    assert cp is not None
    # el transfer apunta src→counterparty, dst→account propio
    row = (await db_session.execute(text(
        "SELECT src_account_id, src_counterparty_id, dst_account_id, dst_counterparty_id "
        "FROM transfers WHERE transaction_id='XFER-FOP-1'"
    ))).first()
    assert row[0] is None and row[1] == cp.id        # src = counterparty
    assert row[2] is not None and row[3] is None     # dst = account propio
```

(Verificar que `_minimal_parsed` pone `account_id` en `parsed.accounts` como `ParsedAccount` — si no, ajustar el helper o construir `p.accounts` explícitamente con `ParsedAccount(ibkr_account_id=OWN, ...)`.)

- [ ] **Step 2: Correr el test, verificar que falla**

Run: `docker compose exec -T backend sh -c "cd /app && uv run pytest tests/ingest/flex/test_persister.py::test_external_transfer_peer_becomes_counterparty_not_account -q"`
Expected: FAIL — hoy el peer externo se crea como Account (o el transfer viola el arc al quedar src_account_id apuntando a un account que no se creó).

- [ ] **Step 3: Quitar transfers de la recolección de `all_account_ids`**

En `backend/src/ibkr_control/ingest/flex/persister.py`, borrar el bloque de transfers de la recolección (líneas ~124-129):

```python
    # Transfers reference accounts via src/dst fields
    for tr in parsed.transfers:
        if tr.src_ibkr_account_id and not _is_shadow_account(tr.src_ibkr_account_id):
            all_account_ids.add(tr.src_ibkr_account_id)
        if tr.dst_ibkr_account_id and not _is_shadow_account(tr.dst_ibkr_account_id):
            all_account_ids.add(tr.dst_ibkr_account_id)
```

> Las demás fuentes (accounts/trades/lots/cash/accruals) se conservan: siempre referencian cuentas propias (en `<AccountInformation>` por estructura de Activity Flex). Solo los peers de `<Transfer>` pueden ser externos, por eso es la única fuente que se excluye. Esto restaura el invariante "accounts = solo cuentas propias".

- [ ] **Step 4: Agregar el helper `_ensure_counterparties`**

En `backend/src/ibkr_control/ingest/flex/persister.py`, agregar el import de `Counterparty` (junto al de `Account`):

```python
from ibkr_control.db.models.counterparties import Counterparty
```

Y al final del archivo (junto a `_ensure_accounts`):

```python
async def _ensure_counterparties(
    session: AsyncSession,
    external_ids: list[str],
) -> dict[str, int]:
    """Ensure counterparties rows exist for external (non-own) transfer peers.
    Returns external_id -> db id map. Mirrors _ensure_accounts (SELECT-then-INSERT
    bajo el mismo advisory lock; UNIQUE(external_id) backstops manual-upload races)."""
    if not external_ids:
        return {}

    result = await session.scalars(
        select(Counterparty).where(Counterparty.external_id.in_(external_ids))
    )
    existing: dict[str, int] = {c.external_id: c.id for c in result.all()}

    missing = set(external_ids) - set(existing.keys())
    for ext_id in missing:
        session.add(Counterparty(external_id=ext_id))

    if missing:
        await session.flush()
        result2 = await session.scalars(
            select(Counterparty).where(Counterparty.external_id.in_(missing))
        )
        for c in result2.all():
            existing[c.external_id] = c.id

    return existing
```

- [ ] **Step 5: Resolver peer por lado en el bloque de transfers**

Reemplazar el bloque de transfers (el que dejó Task 1, `# === Transfers (immutable...`) por:

```python
    # === Transfers (immutable por transaction_id) ===
    # Resolución de peer por lado: si el ibkr_account_id es una cuenta propia
    # (en accounts_map) → FK account; si no → counterparty externo (spec #6).
    external_ids: set[str] = set()
    for tr in parsed.transfers:
        for peer in (tr.src_ibkr_account_id, tr.dst_ibkr_account_id):
            if peer and not _is_shadow_account(peer) and peer not in accounts_map:
                external_ids.add(peer)
    counterparties_map = await _ensure_counterparties(session, list(external_ids))

    def _resolve_side(peer: str | None) -> tuple[int | None, int | None]:
        """Returns (account_id, counterparty_id) — exactamente uno non-None,
        o (None, None) si no hay peer (rebota contra el exclusive arc CHECK)."""
        if not peer:
            return (None, None)
        if peer in accounts_map:
            return (accounts_map[peer], None)
        return (None, counterparties_map[peer])

    transfer_rows: list[dict] = []
    for tr in parsed.transfers:
        src_shadow = tr.src_ibkr_account_id and _is_shadow_account(tr.src_ibkr_account_id)
        dst_shadow = tr.dst_ibkr_account_id and _is_shadow_account(tr.dst_ibkr_account_id)
        if src_shadow or dst_shadow:
            continue
        src_acct, src_cp = _resolve_side(tr.src_ibkr_account_id)
        dst_acct, dst_cp = _resolve_side(tr.dst_ibkr_account_id)
        transfer_rows.append(
            {
                "flex_import_id": fi.id,
                "transaction_id": tr.transaction_id,
                "transfer_date": tr.transfer_date,
                "direction": tr.direction,
                "src_account_id": src_acct,
                "src_counterparty_id": src_cp,
                "dst_account_id": dst_acct,
                "dst_counterparty_id": dst_cp,
                "symbol": tr.symbol,
                "qty": tr.qty,
                "transfer_type": tr.transfer_type,
            }
        )

    n_new_transfers = await _upsert_immutable(
        session, Transfer.__table__, transfer_rows, ["transaction_id"]
    )
```

- [ ] **Step 6: Correr el test nuevo + el de idempotencia, verificar verde**

Run: `docker compose exec -T backend sh -c "cd /app && uv run pytest tests/ingest/flex/test_persister.py tests/ingest/flex/test_persister_idempotent.py -q"`
Expected: PASS.

> Si `test_transfer_not_duplicated_on_reingest` (Task 1) rebota ahora por el exclusive arc (usa `src_ibkr_account_id=None`, dejando el lado src sin endpoint), ajustarlo: poner `src_ibkr_account_id="CS-TEST-1"` (peer externo) para que el lado src resuelva a counterparty y el arc se cumpla. Re-correr.

- [ ] **Step 7: Commit**

```bash
git add backend/src/ibkr_control/ingest/flex/persister.py backend/tests/ingest/flex/test_persister.py backend/tests/ingest/flex/test_persister_idempotent.py
git commit -m "feat(persister): route external transfer peers to counterparties [#6]"
```

---

## Task 5: Fixture sanitizado FOP + test de integración parse→persist

**Files:**
- Create: `backend/tests/fixtures/xml/ACTIVITY_2026_FOP_sanitized.xml`
- Test: `backend/tests/ingest/flex/test_persister.py` (test de integración)

- [ ] **Step 1: Crear el fixture sanitizado**

Crear `backend/tests/fixtures/xml/ACTIVITY_2026_FOP_sanitized.xml` con la estructura real observada (IDs sanitizados — NO usar IDs reales). Un `<AccountInformation>` para la cuenta propia + un `<Transfers>` con un FOP IN externo + un INTERNAL entre cuentas propias:

```xml
<FlexQueryResponse queryName="Activity" type="AF">
  <FlexStatements count="1">
    <FlexStatement accountId="U99999001" fromDate="20260101" toDate="20260522" period="YTD">
      <AccountInformation accountId="U99999001" currency="USD" accountAlias="Personal - Swing" />
      <Transfers>
        <Transfer accountId="U99999001" assetCategory="STK" symbol="GLOB"
          description="GLOBANT SA" conid="160756766" isin="LU0974299876"
          reportDate="20260430" date="20260430" dateTime="20260430" settleDate="20260430"
          type="FOP" direction="IN" account="CS-999999-99" deliveringBroker="0015"
          quantity="94" transferPrice="0" positionAmount="3822.04"
          cashTransfer="0" code="" transactionID="39584831194" levelOfDetail="TRANSFER" />
        <Transfer accountId="U99999001" assetCategory="STK" symbol="GLOB"
          description="GLOBANT SA" conid="160756766" isin="LU0974299876"
          reportDate="20260501" date="20260501" dateTime="20260501" settleDate="20260501"
          type="INTERNAL" direction="OUT" account="U99999002" deliveringBroker=""
          quantity="-94" transferPrice="0" cashTransfer="0" code=""
          transactionID="39601540411" levelOfDetail="TRANSFER" />
      </Transfers>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>
```

> Verificar que el parser real acepte esta forma mínima (los tests existentes parsean `ACTIVITY_2025_sanitized.xml`; comparar atributos requeridos). Si `_parse_transfers` necesita más atributos, copiarlos de la forma real documentada en el spec. NO incluir `<TransferLot>` (irrelevante — tabla eliminada).

- [ ] **Step 2: Escribir el test de integración (falla si el fixture/parse no encaja)**

En `backend/tests/ingest/flex/test_persister.py`:

```python
@pytest.mark.asyncio
async def test_fop_fixture_creates_counterparty_no_orphan_account(
    db_session: AsyncSession, sample_user
):
    """Parse + persist del fixture FOP sanitizado: el peer externo CS-999999-99
    queda en counterparties; las cuentas propias (U99999001/002) en accounts;
    cero accounts huérfanos."""
    from pathlib import Path
    from ibkr_control.db.models.accounts import Account
    from ibkr_control.db.models.counterparties import Counterparty
    from sqlalchemy import func, select

    from ibkr_control.ingest.flex.parser import parse
    xml = Path("tests/fixtures/xml/ACTIVITY_2026_FOP_sanitized.xml").read_bytes()
    parsed = parse(xml)   # fn top-level de parsing (parser.py:99)
    await persist(
        db_session, parsed=parsed, user_id=sample_user.id,
        xml_bytes=xml, source="manual_upload",
    )
    await db_session.commit()

    cp = await db_session.scalar(
        select(Counterparty).where(Counterparty.external_id == "CS-999999-99"))
    assert cp is not None
    n_cp_in_accounts = await db_session.scalar(
        select(func.count()).select_from(Account)
        .where(Account.ibkr_account_id == "CS-999999-99"))
    assert n_cp_in_accounts == 0
    # cuentas propias sí existen
    own = await db_session.scalar(
        select(func.count()).select_from(Account)
        .where(Account.ibkr_account_id.in_(["U99999001", "U99999002"])))
    assert own == 2
```

> Reemplazar `parse_flex_xml` por el nombre real de la función de parsing top-level (verificar con `grep -n "^def parse" backend/src/ibkr_control/ingest/flex/parser.py`).

- [ ] **Step 3: Correr el test, iterar hasta verde**

Run: `docker compose exec -T backend sh -c "cd /app && uv run pytest tests/ingest/flex/test_persister.py::test_fop_fixture_creates_counterparty_no_orphan_account -q"`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/fixtures/xml/ACTIVITY_2026_FOP_sanitized.xml backend/tests/ingest/flex/test_persister.py
git commit -m "test(persister): sanitized FOP fixture + integration test [#6]"
```

---

## Task 6: Verificación final — suite completa + frontend + smoke

**Files:** ninguno (verificación).

- [ ] **Step 1: Suite backend completa**

Run: `docker compose exec -T backend sh -c "cd /app && uv run pytest -q"`
Expected: PASS, 0 failures. (Baseline 290; el neto cambia: −1 test viejo de transfer_lots, +5 modelo counterparty, +2 migración, +2 persister/integración → ~298. Confirmar el número y que no haya regresiones.)

- [ ] **Step 2: Confirmar que no quedan referencias colgantes a transfer_lots/TransferLot**

Run: `cd backend && grep -rn "TransferLot\|transfer_lots" src tests`
Expected: solo el comentario en `_known_tags.py` y, si quedó, en el downgrade de la migración (que recrea la tabla — esperado). Ninguna referencia en código de producción activo (`src/.../persister.py`, `parser.py`, `_models.py`, `db/__init__.py`).

- [ ] **Step 3: Build del frontend (regenerar cliente OpenAPI solo si cambió un schema expuesto)**

`transfers`/`counterparties` no se exponen aún por API → el OpenAPI no debería cambiar. Verificar igual:

Run: `make prod-local` (o `cd frontend && pnpm build` con nvm cargado: `export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"`)
Expected: exit 0. Si el backend expone algún schema nuevo (no debería), correr `cd frontend && pnpm openapi:gen` por el flujo canónico (container up) — ver CLAUDE.md §Shortcuts.

- [ ] **Step 4: Smoke test de idempotencia (manual, opcional pero recomendado)**

Con la DB de dev (migración ya aplicada en Task 3 Step 6), re-disparar un refresh manual del Flex real 2x desde Settings → Refresh manual (o re-ingestar el fixture 2x). Verificar:
- `SELECT count(*) FROM counterparties;` → no crece en el 2do run (UPSERT por external_id).
- `SELECT count(*) FROM accounts WHERE ibkr_account_id LIKE 'CS-%';` → 0 (sin huérfanos nuevos).
- 2do run sin errores (idempotente).

- [ ] **Step 5: Actualizar CLAUDE.md (estado + tag)**

- En la tabla "Estado actual", agregar fila Phase 2.7 (o nota) reflejando el cleanup.
- Actualizar el conteo de tests.
- Anotar que el tag `v0.2.5-persister-cleanup` se crea **post-merge** (convención Phase 2.5).

```bash
git add CLAUDE.md
git commit -m "docs(claude): persister counterparties cleanup retrospective + status"
```

- [ ] **Step 6: Finalizar el branch**

Usar `superpowers:finishing-a-development-branch` para decidir merge/PR. Tag `v0.2.5-persister-cleanup` SOLO post-merge a `main`.

---

## Self-Review (cobertura del spec)

- **#6 counterparties + exclusive arc** → Tasks 2 (modelo), 3 (migración), 4 (persister), 5 (integración). ✓
- **#6 invariante accounts = own only** → Task 4 Step 3 (excluir transfers de all_account_ids). ✓
- **#6 reconciliación huérfano CS-999999-99** → Task 3 Step 2 (data-migration) + Step 6 (aplicar a dev). ✓
- **#6 CHECK validado contra data real / fail-loud** → Task 3 Step 2 (assert precondición antes del CHECK). ✓
- **#7 drop transfer_lots (modelo+parser+persister+tests)** → Task 1. ✓
- **#7 migración drop** → Task 3 (paso 6 del upgrade). ✓
- **C4 una sola revision atómica** → Task 3. ✓
- **Testing: fixture sanitizado + testcontainers migración** → Tasks 5 + 3. ✓
- **Reversibilidad downgrade** → Task 3 Step 2 (downgrade) + test Task 3 Step 3. ✓

Sin placeholders de implementación pendiente. Los dos puntos que dependían de detalles del repo quedaron resueltos con código concreto: el patrón de migración usa `command.upgrade(cfg, rev)` en thread + testcontainers (copiado de `test_phase25_persister_migration.py`, Task 3 Step 3), y la función top-level de parsing es `parse(xml_bytes)` (`parser.py:99`, Task 5 Step 2). Quedan 2 verificaciones menores in-situ marcadas con `>`: el formato exacto del driver en `pg.get_connection_url()` y que `_minimal_parsed` pueble `parsed.accounts` — ambas con instrucción de cómo confirmarlas.
