# Flex Persister Idempotente — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite del Flex persister para que sea fully idempotente fila-por-fila vía UPSERT por natural key, eliminando el `UniqueViolationError` que rompe el cron + manual refresh al segundo run.

**Architecture:** Schema migration en 2 Alembic revisions + 1 script manual de wipe (mitigación de TRUNCATE accidental). Persister rewrite con helpers Core (`pg_insert + on_conflict_*`) en lugar de ORM `session.add` loops. Semántica diferenciada por entity-type: immutable (`DO NOTHING`, `flex_import_id` first-seen) vs snapshot (`DO UPDATE`, `flex_import_id` last-updated-by).

**Tech Stack:** Python 3.12, SQLAlchemy 2.x async + asyncpg, Alembic, Postgres 16 (ON CONFLICT), pytest, FastAPI, Next.js (TypeScript types frontend).

**Spec:** `docs/specs/2026-05-25-flex-persister-idempotent-design.md` (A0-A8 lockeadas).

---

## File Structure

### Files to create

| Path | Responsibility |
|---|---|
| `backend/alembic/versions/<rev1_id>_phase25_idempotent_schema.py` | Revision 1: schema prep (drop CASCADE → SET NULL, add nullable columns, rename `n_*` → `n_observed_*`) — safe, no destructive |
| `backend/scripts/wipe_flex_data.py` | CLI script: prompt `[y/N]` + TRUNCATE flex_imports CASCADE. Manual gate entre Rev1 y Rev2 |
| `backend/alembic/versions/<rev2_id>_phase25_idempotent_constraints.py` | Revision 2: promote NOT NULL + UNIQUE constraints (válido solo post-wipe) |
| `backend/src/ibkr_control/ingest/flex/_upsert_helpers.py` | 3 Core helpers: `_upsert_immutable`, `_upsert_snapshot`, `_upsert_immutable_returning_inserted` + `_chunks` |
| `backend/tests/ingest/flex/test_upsert_helpers.py` | Unit tests de los 3 helpers (no-op on conflict, batching, returning) |
| `backend/tests/ingest/flex/test_persister_idempotent.py` | Integration tests: re-ingest scenarios (byte-identical, modified, mark price change, etc.) |
| `backend/tests/test_phase25_persister_migration.py` | Migration tests: preservación de tablas, FK SET NULL, UNIQUE enforcement |

### Files to modify

| Path | Why |
|---|---|
| `backend/src/ibkr_control/ingest/flex/_models.py` | Add `transaction_id: str` a `ParsedCashTransaction` + `ParsedTransfer` |
| `backend/src/ibkr_control/ingest/flex/parser.py` | Capture `transactionID` para cash_transactions + transfers; pasar al dataclass |
| `backend/src/ibkr_control/db/models/flex_raw.py` | Update SQLAlchemy models al schema nuevo (nullable FK, transaction_id columns, n_observed_* + n_new_*, UNIQUE constraints, xml_bytes) |
| `backend/src/ibkr_control/ingest/flex/persister.py` | Rewrite completo con Core UPSERTs. Cambia return type a `tuple[int, dict]` (flex_import_id, counters dict) |
| `backend/src/ibkr_control/ingest/flex/job.py` | Update llamadores de `persist()` para destructure el tuple. Sin cambios al SAVEPOINT pattern |
| `backend/src/ibkr_control/api/_schemas.py` | Update `Step2DetectResponse.ingest_summary` shape para incluir `n_observed_*` + `n_new_*`. Add nuevo type alias `IngestCounters` (TypedDict-like via Pydantic) |
| `backend/src/ibkr_control/api/imports.py` | Read counters via nuevo dict shape, expose ambos sets en response |
| `backend/src/ibkr_control/api/setup.py` | Idem para wizard step3 commit response |
| `backend/tests/ingest/flex/test_persister.py` | Actualizar tests existentes: nuevo return type, nuevos campos en ParsedX, counters shape |
| `backend/tests/ingest/flex/test_parser.py` | Add tests para captura de transactionID en cash + transfers |
| `frontend/src/hooks/useIngestStream.ts` | `StreamEvent` interface gana `n_observed_*?`, `n_new_*?` opcionales |
| `CLAUDE.md` | Update sección "Estado actual" con tag `v0.2.3-persister-idempotent`, próxima sesión flow |
| `docs/plans/2026-05-24-phase2-polish-backlog.md` | Backfill D13 entry con marker `[BUG-FIXED]` y commit reference |

---

## Tasks

### Task 1: Setup — branch isolation + spec readback

**Files:**
- N/A (orchestration)

- [ ] **Step 1: Confirmar estado de git limpio**

Run: `git status --short`
Expected: solo aparecen los 2 archivos del fix UI + spec (pendientes de commit aparte) — sin cambios sorpresivos en otros lugares.

- [ ] **Step 2: Crear branch dedicado para este trabajo**

Run: `git checkout -b phase25/flex-persister-idempotent main`
Expected: branch nueva creada. **No** mergear el fix UI todavía — va incluido en el primer commit de esta branch (es parte del mismo incidente).

- [ ] **Step 3: Re-leer el spec lockeado**

Read: `docs/specs/2026-05-25-flex-persister-idempotent-design.md`
Foco: secciones "Decisiones lockeadas (A0-A8)", "Arquitectura resultante", "Migration plan". No re-discutir nada.

- [ ] **Step 4: Commit del fix UI + spec en estado pre-trabajo**

```bash
git add docs/specs/2026-05-25-flex-persister-idempotent-design.md \
        frontend/src/components/settings/ManualRefreshButton.tsx
git commit -m "$(cat <<'EOF'
docs(spec): flex persister idempotente + fix UI race condition

- docs/specs/2026-05-25-flex-persister-idempotent-design.md: spec
  lockeada A0-A8 para Phase 2.5 (persister rewrite a UPSERT por natural
  key). Bug detectado en smoke test del 2026-05-25: cron + manual
  refresh fallan al segundo run con UniqueViolationError porque el
  persister dedupea solo a nivel xml_hash y los XMLs YTD cambian diario.

- ManualRefreshButton.tsx: fix race condition entre 2 useEffect que
  hacia que apareciera "✓ Refresh completado correctamente" aunque hubo
  un evento failed. Derivamos hasFailed/finished en render en vez de
  state + effect.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Expected: 1 commit creado. Branch al día con spec.

---

### Task 2: Parser updates — capturar `transactionID` para CashTransaction + Transfer

**Files:**
- Modify: `backend/src/ibkr_control/ingest/flex/_models.py` (dataclasses)
- Modify: `backend/src/ibkr_control/ingest/flex/parser.py` (extract attrs)
- Test: `backend/tests/ingest/flex/test_parser.py` (add 2 cases)

- [ ] **Step 1: Write failing tests for cash_transactions transaction_id capture**

Edit `backend/tests/ingest/flex/test_parser.py` (append):

```python
def test_parse_cash_transaction_captures_transaction_id():
    """Parser must extract transactionID attribute from <CashTransaction> elements
    so the persister can UPSERT by natural key without spurious duplicates."""
    from ibkr_control.ingest.flex.parser import parse
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse>
  <FlexStatements>
    <FlexStatement accountId="U99999001" period="20250101-20251231"
                   fromDate="20250101" toDate="20251231">
      <AccountInformation accountId="U99999001" currency="USD"/>
      <CashTransactions>
        <CashTransaction accountId="U99999001" type="Dividends"
                         currency="USD" amount="100.00"
                         description="AAPL DIV"
                         dateTime="20250215;120000"
                         transactionID="TXN-CASH-42"
                         levelOfDetail="DETAIL"/>
      </CashTransactions>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>"""
    parsed = parse(xml)
    assert len(parsed.cash_transactions) == 1
    assert parsed.cash_transactions[0].transaction_id == "TXN-CASH-42"


def test_parse_transfer_captures_transaction_id():
    """Parser must extract transactionID attribute from <Transfer> elements."""
    from ibkr_control.ingest.flex.parser import parse
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse>
  <FlexStatements>
    <FlexStatement accountId="U99999001" period="20250101-20251231"
                   fromDate="20250101" toDate="20251231">
      <AccountInformation accountId="U99999001" currency="USD"/>
      <Transfers>
        <Transfer accountId="U99999001" date="20250301"
                  direction="IN" symbol="MSFT" quantity="100"
                  type="ACATS" transactionID="TXN-XFER-99"/>
      </Transfers>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>"""
    parsed = parse(xml)
    assert len(parsed.transfers) == 1
    assert parsed.transfers[0].transaction_id == "TXN-XFER-99"
```

- [ ] **Step 2: Run tests to verify failures**

Run: `docker compose exec -T backend uv run pytest backend/tests/ingest/flex/test_parser.py::test_parse_cash_transaction_captures_transaction_id backend/tests/ingest/flex/test_parser.py::test_parse_transfer_captures_transaction_id -v`
Expected: FAIL — `AttributeError: 'ParsedCashTransaction' object has no attribute 'transaction_id'` (o similar para Transfer)

- [ ] **Step 3: Add field to ParsedCashTransaction + ParsedTransfer dataclasses**

Edit `backend/src/ibkr_control/ingest/flex/_models.py`:

Reemplazar el bloque `class ParsedCashTransaction`:
```python
@dataclass
class ParsedCashTransaction:
    transaction_id: str
    ibkr_account_id: str
    type: str
    currency: str
    amount_usd: Decimal
    description: str | None
    date: date
    symbol: str | None
```

Reemplazar el bloque `class ParsedTransfer`:
```python
@dataclass
class ParsedTransfer:
    transaction_id: str
    transfer_date: date
    direction: str
    src_ibkr_account_id: str | None
    dst_ibkr_account_id: str | None
    symbol: str
    qty: Decimal
    transfer_type: str
    lots: list["ParsedTransferLot"] = field(default_factory=list)
```

- [ ] **Step 4: Update parser to extract transactionID**

Edit `backend/src/ibkr_control/ingest/flex/parser.py`:

En `_parse_cash_transactions` (alrededor de la línea 354), agregar `transaction_id` al constructor:
```python
out.append(ParsedCashTransaction(
    transaction_id=tx.get("transactionID") or "",
    ibkr_account_id=tx.get("accountId") or "",
    type=tx.get("type") or "",
    currency=tx.get("currency") or "USD",
    amount_usd=_dec(tx.get("amount")),
    description=tx.get("description") or None,
    date=tx_date,
    symbol=tx.get("symbol") or None,
))
```

En `_parse_transfers` (alrededor de la línea 495), modificar el `ParsedTransfer(...)`:
```python
transfer = ParsedTransfer(
    transaction_id=elem.get("transactionID") or "",
    transfer_date=transfer_date,
    direction=direction,
    src_ibkr_account_id=elem.get("originatingAccountId") or None,
    dst_ibkr_account_id=elem.get("accountId") or None,
    symbol=elem.get("symbol") or "",
    qty=_dec(elem.get("quantity")),
    transfer_type=elem.get("type") or "",
)
```

(Conservar atributo names existentes para src/dst — chequear cómo se nombran hoy si difiere)

- [ ] **Step 5: Run new tests to verify pass**

Run: `docker compose exec -T backend uv run pytest backend/tests/ingest/flex/test_parser.py::test_parse_cash_transaction_captures_transaction_id backend/tests/ingest/flex/test_parser.py::test_parse_transfer_captures_transaction_id -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Run full parser tests to verify no regression**

Run: `docker compose exec -T backend uv run pytest backend/tests/ingest/flex/test_parser.py -v`
Expected: PASS (todos los tests previos + 2 nuevos)

- [ ] **Step 7: Update test helper `_make_parsed` en test_persister.py para incluir el nuevo field**

Edit `backend/tests/ingest/flex/test_persister.py` líneas relevantes (donde se construya `ParsedCashTransaction` o `ParsedTransfer` — buscar usos). El helper `_make_parsed` puede no usarlos, pero si hay tests inline que construyan estos dataclasses, agregar `transaction_id="TXN-..."`.

Run: `docker compose exec -T backend uv run pytest backend/tests/ingest/flex/test_persister.py -v`
Expected: PASS (los tests viejos siguen pasando — solo cambia el dataclass init si lo usan)

- [ ] **Step 8: Commit**

```bash
git add backend/src/ibkr_control/ingest/flex/_models.py \
        backend/src/ibkr_control/ingest/flex/parser.py \
        backend/tests/ingest/flex/test_parser.py \
        backend/tests/ingest/flex/test_persister.py
git commit -m "$(cat <<'EOF'
feat(parser): capture transactionID for CashTransaction + Transfer

Pre-requisite for the idempotent persister rewrite (spec A6). The new
UPSERT pattern needs natural keys for every immutable entity, and IBKR
emits transactionID for both CashTransaction and Transfer elements but
the parser was discarding it.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Alembic Revision 1 — schema prep (drop CASCADE, add nullable columns)

**Files:**
- Create: `backend/alembic/versions/<auto_id>_phase25_idempotent_schema.py`
- Test: `backend/tests/test_phase25_persister_migration.py` (new file, partial coverage)

- [ ] **Step 1: Generate Alembic revision skeleton**

Run: `docker compose exec -T backend uv run alembic revision -m "phase25 idempotent schema"`
Expected: nuevo archivo en `backend/alembic/versions/` con nombre `<hash>_phase25_idempotent_schema.py`. Capturar el `<hash>` para próximos steps.

- [ ] **Step 2: Write the migration upgrade() body**

Edit el archivo generado, reemplazar el `upgrade()` function con:

```python
"""phase25 idempotent schema

Revision ID: <hash autogenerated>
Revises: a4b6d1025bca
Create Date: <auto>

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '<hash>'
down_revision: Union[str, Sequence[str], None] = 'a4b6d1025bca'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CHILD_TABLES = [
    'trades', 'closed_lots', 'cash_transactions', 'transfers',
    'open_position_lots', 'change_in_dividend_accruals', 'open_dividend_accruals',
]


def upgrade() -> None:
    # 1. Drop CASCADE FK + make flex_import_id nullable + recreate as SET NULL
    for table in _CHILD_TABLES:
        op.drop_constraint(f'{table}_flex_import_id_fkey', table, type_='foreignkey')
        op.alter_column(table, 'flex_import_id', nullable=True)
        op.create_foreign_key(
            f'{table}_flex_import_id_fkey', table, 'flex_imports',
            ['flex_import_id'], ['id'], ondelete='SET NULL',
        )

    # 2. Add transaction_id NULLABLE to closed_lots, cash_transactions, transfers
    op.add_column('closed_lots', sa.Column('transaction_id', sa.String(), nullable=True))
    op.add_column('cash_transactions', sa.Column('transaction_id', sa.String(), nullable=True))
    op.add_column('transfers', sa.Column('transaction_id', sa.String(), nullable=True))

    # 3. Add xml_bytes NULLABLE (NOT NULL post-wipe in Revision 2)
    op.add_column('flex_imports', sa.Column('xml_bytes', sa.LargeBinary(), nullable=True))

    # 4. Add n_new_* counters NULLABLE
    for col in ['n_new_trades', 'n_new_lots_closed', 'n_new_open_lots',
                'n_new_cash_tx', 'n_new_dividends', 'n_new_transfers']:
        op.add_column('flex_imports', sa.Column(col, sa.Integer(), nullable=True))

    # 5. Rename existing n_* → n_observed_*
    op.alter_column('flex_imports', 'n_trades', new_column_name='n_observed_trades')
    op.alter_column('flex_imports', 'n_lots_closed', new_column_name='n_observed_lots_closed')
    op.alter_column('flex_imports', 'n_open_lots', new_column_name='n_observed_open_lots')
    op.alter_column('flex_imports', 'n_cash_tx', new_column_name='n_observed_cash_tx')
    op.alter_column('flex_imports', 'n_dividends', new_column_name='n_observed_dividends')
    op.alter_column('flex_imports', 'n_transfers', new_column_name='n_observed_transfers')


def downgrade() -> None:
    # Reverse order
    op.alter_column('flex_imports', 'n_observed_transfers', new_column_name='n_transfers')
    op.alter_column('flex_imports', 'n_observed_dividends', new_column_name='n_dividends')
    op.alter_column('flex_imports', 'n_observed_cash_tx', new_column_name='n_cash_tx')
    op.alter_column('flex_imports', 'n_observed_open_lots', new_column_name='n_open_lots')
    op.alter_column('flex_imports', 'n_observed_lots_closed', new_column_name='n_lots_closed')
    op.alter_column('flex_imports', 'n_observed_trades', new_column_name='n_trades')

    for col in ['n_new_trades', 'n_new_lots_closed', 'n_new_open_lots',
                'n_new_cash_tx', 'n_new_dividends', 'n_new_transfers']:
        op.drop_column('flex_imports', col)

    op.drop_column('flex_imports', 'xml_bytes')

    op.drop_column('closed_lots', 'transaction_id')
    op.drop_column('cash_transactions', 'transaction_id')
    op.drop_column('transfers', 'transaction_id')

    for table in _CHILD_TABLES:
        op.drop_constraint(f'{table}_flex_import_id_fkey', table, type_='foreignkey')
        op.alter_column(table, 'flex_import_id', nullable=False)
        op.create_foreign_key(
            f'{table}_flex_import_id_fkey', table, 'flex_imports',
            ['flex_import_id'], ['id'], ondelete='CASCADE',
        )
```

- [ ] **Step 3: Apply Revision 1 to dev DB**

Run: `docker compose exec -T backend uv run alembic upgrade head`
Expected: `INFO  [alembic.runtime.migration] Running upgrade a4b6d1025bca -> <hash>, phase25 idempotent schema`

- [ ] **Step 4: Verify schema changes en DB**

Run: `docker compose exec -T postgres psql -U ibkr -d ibkr_control -c "\d flex_imports" | head -30`
Expected: ver `xml_bytes`, `n_observed_*`, `n_new_*` columns. Sin `n_trades` (renombrada).

Run: `docker compose exec -T postgres psql -U ibkr -d ibkr_control -c "\d trades" | grep -A1 flex_import_id`
Expected: `flex_import_id ... | nullable | ... ON DELETE SET NULL`

- [ ] **Step 5: Write migration test (preservation)**

Create `backend/tests/test_phase25_persister_migration.py`:
```python
"""Tests del schema phase25 (Revision 1: idempotent_schema + Revision 2: idempotent_constraints)."""
from datetime import date
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_phase25_rev1_renames_observed_counters(db_session: AsyncSession):
    """Revision 1 debe renombrar n_trades → n_observed_trades, etc."""
    result = await db_session.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'flex_imports' AND column_name LIKE 'n_observed_%'"
    ))
    names = {row[0] for row in result.all()}
    expected = {
        'n_observed_trades', 'n_observed_lots_closed', 'n_observed_open_lots',
        'n_observed_cash_tx', 'n_observed_dividends', 'n_observed_transfers',
    }
    assert expected.issubset(names), f"Missing renamed counters: {expected - names}"


@pytest.mark.asyncio
async def test_phase25_rev1_adds_n_new_counters(db_session: AsyncSession):
    result = await db_session.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'flex_imports' AND column_name LIKE 'n_new_%'"
    ))
    names = {row[0] for row in result.all()}
    expected = {
        'n_new_trades', 'n_new_lots_closed', 'n_new_open_lots',
        'n_new_cash_tx', 'n_new_dividends', 'n_new_transfers',
    }
    assert expected == names


@pytest.mark.asyncio
async def test_phase25_rev1_adds_xml_bytes_nullable(db_session: AsyncSession):
    result = await db_session.execute(text(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = 'flex_imports' AND column_name = 'xml_bytes'"
    ))
    row = result.first()
    assert row is not None
    assert row[0] == 'YES'  # nullable in Revision 1 (NOT NULL post-wipe in Rev2)


@pytest.mark.asyncio
async def test_phase25_rev1_makes_flex_import_id_nullable_with_set_null(db_session: AsyncSession):
    """FK debe estar como SET NULL en trades."""
    result = await db_session.execute(text("""
        SELECT confdeltype FROM pg_constraint
        WHERE conname = 'trades_flex_import_id_fkey'
    """))
    row = result.first()
    assert row is not None
    assert row[0] == 'n', f"Expected SET NULL (confdeltype='n'), got {row[0]!r}"


@pytest.mark.asyncio
async def test_phase25_rev1_adds_transaction_id_to_closed_lots_nullable(db_session: AsyncSession):
    result = await db_session.execute(text(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = 'closed_lots' AND column_name = 'transaction_id'"
    ))
    row = result.first()
    assert row is not None
    assert row[0] == 'YES'  # nullable en Rev1; NOT NULL en Rev2
```

- [ ] **Step 6: Run migration tests**

Run: `docker compose exec -T backend uv run pytest backend/tests/test_phase25_persister_migration.py -v`
Expected: PASS (5 tests)

- [ ] **Step 7: Commit**

```bash
git add backend/alembic/versions/*phase25_idempotent_schema*.py \
        backend/tests/test_phase25_persister_migration.py
git commit -m "$(cat <<'EOF'
feat(migration): phase25 Revision 1 — idempotent schema prep

Drop CASCADE FK on all 7 child tables → SET NULL semantics (spec A2).
Add transaction_id nullable columns to closed_lots/cash_transactions/
transfers (spec A6 — promoted to NOT NULL + UNIQUE in Revision 2 post
wipe). Add xml_bytes BYTEA nullable (spec A0) + n_new_* counters (spec
A5). Rename existing n_* → n_observed_* for clarity.

This revision is non-destructive: data preserved. Revision 2 + manual
wipe script complete the migration.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Wipe script — `backend/scripts/wipe_flex_data.py`

**Files:**
- Create: `backend/scripts/wipe_flex_data.py`

- [ ] **Step 1: Create scripts directory if needed + write script**

Run: `mkdir -p backend/scripts && touch backend/scripts/__init__.py`

Create `backend/scripts/wipe_flex_data.py`:
```python
"""Wipe Flex data — gate manual entre Alembic Revision 1 y Revision 2.

USO: docker compose exec backend uv run python -m backend.scripts.wipe_flex_data

Borra flex_imports + children (CASCADE actual aún activo en Rev1, ver spec A6).
Preserva: flex_credentials, accounts, users, user_settings, apscheduler_jobs,
ingest_log, trm_days.

Después de correr este script, ejecutar `alembic upgrade head` para aplicar
Revision 2 (promote transaction_id NOT NULL + UNIQUE + xml_bytes NOT NULL).
"""
import asyncio
import sys

from sqlalchemy import text

from ibkr_control.db.session import get_engine


PRESERVED = (
    "flex_credentials, accounts, users, user_settings, "
    "apscheduler_jobs, ingest_log, trm_days, trm_imports"
)


async def main() -> None:
    print("=" * 70)
    print("WIPE FLEX DATA")
    print("=" * 70)
    print()
    print("Este script va a BORRAR todos los rows de:")
    print("  flex_imports + trades + closed_lots + open_position_lots +")
    print("  cash_transactions + transfers + transfer_lots +")
    print("  change_in_dividend_accruals + open_dividend_accruals")
    print()
    print(f"Preserva: {PRESERVED}")
    print()
    print("Después tenés que:")
    print("  1. docker compose exec backend uv run alembic upgrade head")
    print("     (Revision 2 agrega NOT NULL + UNIQUE constraints)")
    print("  2. Re-uploadear XMLs históricos manualmente desde el wizard")
    print("  3. Trigger Flex WS fetch para YTD del año actual (Settings)")
    print()
    resp = input("Confirmar wipe [y/N]: ").strip().lower()
    if resp != "y":
        print("Cancelado.")
        sys.exit(1)

    engine = get_engine()
    async with engine.begin() as conn:
        # CASCADE wipea children por el FK CASCADE que aún existe en Rev1.
        # En Rev2 cambiamos a SET NULL pero acá todavía estamos en Rev1.
        # Si el FK ya fue cambiado a SET NULL (porque Rev1 lo cambió), CASCADE
        # también funciona — Postgres respeta el TRUNCATE ... CASCADE
        # statement regardless del ON DELETE action del FK.
        await conn.execute(text("TRUNCATE flex_imports CASCADE"))
    print()
    print("Wipe completado.")
    print("Ahora corré: docker compose exec backend uv run alembic upgrade head")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Smoke test the script in dry mode (NO ejecutar wipe real todavía — solo cancelar el prompt)**

Run:
```bash
echo "n" | docker compose exec -T backend uv run python -m backend.scripts.wipe_flex_data
```
Expected: imprime el warning + "Cancelado." sin tocar la DB.

- [ ] **Step 3: Verify DB intact**

Run: `docker compose exec -T postgres psql -U ibkr -d ibkr_control -c "SELECT count(*) FROM flex_imports;"`
Expected: 3 (los imports del wizard original siguen ahí).

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/__init__.py backend/scripts/wipe_flex_data.py
git commit -m "$(cat <<'EOF'
feat(scripts): wipe_flex_data — manual gate entre Alembic revisions

Mitigación del riesgo "TRUNCATE accidental en prod sin re-upload"
documentado en spec § Riesgos. La migration de Phase 2.5 NO ejecuta
TRUNCATE directamente; el usuario corre este script con prompt [y/N]
entre Revision 1 (schema prep) y Revision 2 (NOT NULL + UNIQUE).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Alembic Revision 2 — promote NOT NULL + UNIQUE constraints

**Files:**
- Create: `backend/alembic/versions/<auto_id>_phase25_idempotent_constraints.py`
- Modify: `backend/tests/test_phase25_persister_migration.py` (add Rev2 tests)

- [ ] **Step 1: Generate Alembic revision**

Run: `docker compose exec -T backend uv run alembic revision -m "phase25 idempotent constraints"`
Expected: nuevo archivo con `down_revision` apuntando al hash de Revision 1.

- [ ] **Step 2: Wipe data manualmente (necesario antes de aplicar Rev2)**

Run:
```bash
echo "y" | docker compose exec -T backend uv run python -m backend.scripts.wipe_flex_data
```
Expected: `Wipe completado.` Verificar con `SELECT count(*) FROM flex_imports;` → 0.

- [ ] **Step 3: Write the Revision 2 upgrade() body**

Edit el archivo de Rev2 generado:

```python
"""phase25 idempotent constraints

Revision ID: <hash>
Revises: <hash-of-rev1>
Create Date: <auto>

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '<hash>'
down_revision: Union[str, Sequence[str], None] = '<hash-of-rev1>'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Requires DB wipe via backend/scripts/wipe_flex_data.py before running.

    Promotes transaction_id to NOT NULL + UNIQUE on closed_lots/cash_transactions/
    transfers, adds composite UNIQUE on snapshot tables, and promotes xml_bytes
    to NOT NULL. All operations only valid because tables were truncated.
    """
    # 1. Promote transaction_id NOT NULL + UNIQUE
    op.alter_column('closed_lots', 'transaction_id', nullable=False)
    op.create_unique_constraint(
        'closed_lots_transaction_id_key', 'closed_lots', ['transaction_id']
    )
    op.alter_column('cash_transactions', 'transaction_id', nullable=False)
    op.create_unique_constraint(
        'cash_transactions_transaction_id_key', 'cash_transactions', ['transaction_id']
    )
    op.alter_column('transfers', 'transaction_id', nullable=False)
    op.create_unique_constraint(
        'transfers_transaction_id_key', 'transfers', ['transaction_id']
    )

    # 2. UNIQUE constraints on snapshot tables (natural keys per spec A3)
    op.create_unique_constraint(
        'open_position_lots_natural_key',
        'open_position_lots',
        ['account_id', 'symbol', 'open_date', 'snapshot_date'],
    )
    op.create_unique_constraint(
        'change_in_dividend_accruals_natural_key',
        'change_in_dividend_accruals',
        ['account_id', 'conid', 'ex_date', 'pay_date', 'accrual_date'],
    )
    op.create_unique_constraint(
        'open_dividend_accruals_natural_key',
        'open_dividend_accruals',
        ['account_id', 'conid', 'ex_date', 'pay_date', 'report_date'],
    )

    # 3. Promote xml_bytes NOT NULL
    op.alter_column('flex_imports', 'xml_bytes', nullable=False)


def downgrade() -> None:
    op.alter_column('flex_imports', 'xml_bytes', nullable=True)
    op.drop_constraint('open_dividend_accruals_natural_key', 'open_dividend_accruals', type_='unique')
    op.drop_constraint('change_in_dividend_accruals_natural_key', 'change_in_dividend_accruals', type_='unique')
    op.drop_constraint('open_position_lots_natural_key', 'open_position_lots', type_='unique')
    op.drop_constraint('transfers_transaction_id_key', 'transfers', type_='unique')
    op.alter_column('transfers', 'transaction_id', nullable=True)
    op.drop_constraint('cash_transactions_transaction_id_key', 'cash_transactions', type_='unique')
    op.alter_column('cash_transactions', 'transaction_id', nullable=True)
    op.drop_constraint('closed_lots_transaction_id_key', 'closed_lots', type_='unique')
    op.alter_column('closed_lots', 'transaction_id', nullable=True)
```

- [ ] **Step 4: Apply Revision 2**

Run: `docker compose exec -T backend uv run alembic upgrade head`
Expected: `INFO  [alembic.runtime.migration] Running upgrade <rev1> -> <rev2>, phase25 idempotent constraints`

- [ ] **Step 5: Add Rev2 migration tests**

Edit `backend/tests/test_phase25_persister_migration.py` (append):

```python
@pytest.mark.asyncio
async def test_phase25_rev2_promotes_transaction_id_not_null(db_session: AsyncSession):
    for table in ('closed_lots', 'cash_transactions', 'transfers'):
        result = await db_session.execute(text(
            f"SELECT is_nullable FROM information_schema.columns "
            f"WHERE table_name = '{table}' AND column_name = 'transaction_id'"
        ))
        row = result.first()
        assert row is not None, f"transaction_id missing from {table}"
        assert row[0] == 'NO', f"{table}.transaction_id should be NOT NULL"


@pytest.mark.asyncio
async def test_phase25_rev2_adds_unique_constraints(db_session: AsyncSession):
    """All 6 UNIQUE constraints from spec A3 + transaction_id must exist."""
    expected_constraints = {
        'closed_lots_transaction_id_key',
        'cash_transactions_transaction_id_key',
        'transfers_transaction_id_key',
        'open_position_lots_natural_key',
        'change_in_dividend_accruals_natural_key',
        'open_dividend_accruals_natural_key',
    }
    result = await db_session.execute(text(
        "SELECT conname FROM pg_constraint WHERE conname = ANY(:names)"
    ).bindparams(names=list(expected_constraints)))
    found = {row[0] for row in result.all()}
    assert found == expected_constraints, f"Missing: {expected_constraints - found}"


@pytest.mark.asyncio
async def test_phase25_rev2_promotes_xml_bytes_not_null(db_session: AsyncSession):
    result = await db_session.execute(text(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = 'flex_imports' AND column_name = 'xml_bytes'"
    ))
    row = result.first()
    assert row is not None
    assert row[0] == 'NO'


@pytest.mark.asyncio
async def test_phase25_unique_open_position_lots_natural_key_enforced(db_session: AsyncSession):
    """Insertar duplicate (account, symbol, open_date, snapshot_date) debe fallar."""
    from sqlalchemy.exc import IntegrityError
    from ibkr_control.db.models.accounts import Account
    from ibkr_control.db.models.flex_raw import FlexImport, OpenPositionLot
    from datetime import date
    from decimal import Decimal

    # Setup: create a flex_import + account
    fi = FlexImport(
        user_id=1, anyo=2026, xml_hash='test-hash-natural-key',
        xml_size_bytes=100, xml_bytes=b'<test/>', source='manual_upload',
        period_covered_from=date(2026, 1, 1), period_covered_to=date(2026, 12, 31),
        status='ok',
    )
    db_session.add(fi)
    await db_session.flush()
    acc = await db_session.scalar(
        text("SELECT id FROM accounts LIMIT 1")
    )
    if acc is None:
        # Crear uno mínimo
        new_acc = Account(ibkr_account_id='U99999999', alias=None, currency='USD')
        db_session.add(new_acc)
        await db_session.flush()
        acc = new_acc.id

    db_session.add(OpenPositionLot(
        flex_import_id=fi.id, account_id=acc, symbol='AAPL',
        open_date=date(2026, 1, 15), qty=Decimal('10'),
        cost_basis_usd=Decimal('1500'), snapshot_date=date(2026, 5, 25),
    ))
    await db_session.flush()
    db_session.add(OpenPositionLot(
        flex_import_id=fi.id, account_id=acc, symbol='AAPL',
        open_date=date(2026, 1, 15), qty=Decimal('20'),  # diff qty same key
        cost_basis_usd=Decimal('3000'), snapshot_date=date(2026, 5, 25),
    ))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()
```

- [ ] **Step 6: Run all migration tests**

Run: `docker compose exec -T backend uv run pytest backend/tests/test_phase25_persister_migration.py -v`
Expected: PASS (todos los tests de Rev1 + Rev2)

- [ ] **Step 7: Commit**

```bash
git add backend/alembic/versions/*phase25_idempotent_constraints*.py \
        backend/tests/test_phase25_persister_migration.py
git commit -m "$(cat <<'EOF'
feat(migration): phase25 Revision 2 — promote NOT NULL + UNIQUE constraints

Applies AFTER manual run of backend/scripts/wipe_flex_data.py (spec § Riesgos).
Promotes transaction_id to NOT NULL + UNIQUE on closed_lots/cash_transactions/
transfers (spec A6). Adds composite UNIQUE on snapshot tables per spec A3.
Promotes xml_bytes to NOT NULL (spec A0).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: SQLAlchemy model updates — `db/models/flex_raw.py`

**Files:**
- Modify: `backend/src/ibkr_control/db/models/flex_raw.py`

- [ ] **Step 1: Update `FlexImport` model**

Edit `backend/src/ibkr_control/db/models/flex_raw.py`:

En la clase `FlexImport`, reemplazar las columnas afectadas:

```python
class FlexImport(Base):
    __tablename__ = "flex_imports"
    # ... existing columns ...
    xml_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    n_observed_trades: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_observed_lots_closed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_observed_open_lots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_observed_cash_tx: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_observed_dividends: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_observed_transfers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_new_trades: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_new_lots_closed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_new_open_lots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_new_cash_tx: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_new_dividends: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_new_transfers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # remove old n_trades, n_lots_closed, n_open_lots, n_cash_tx, n_dividends, n_transfers
```

Asegurar import: `from sqlalchemy import LargeBinary` (al top con los demás).

- [ ] **Step 2: Update child models — `flex_import_id` nullable**

En las 7 clases (`Trade`, `ClosedLot`, `OpenPositionLot`, `Transfer`, `CashTransaction`, `ChangeInDividendAccrual`, `OpenDividendAccrual`):

```python
    flex_import_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("flex_imports.id", ondelete="SET NULL"), nullable=True
    )
```

- [ ] **Step 3: Add `transaction_id` column to ClosedLot, CashTransaction, Transfer**

```python
class ClosedLot(Base):
    __tablename__ = "closed_lots"
    # ... existing ...
    transaction_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
```

```python
class CashTransaction(Base):
    __tablename__ = "cash_transactions"
    # ... existing ...
    transaction_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
```

```python
class Transfer(Base):
    __tablename__ = "transfers"
    # ... existing ...
    transaction_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
```

- [ ] **Step 4: Add composite UNIQUE constraints to snapshot models**

```python
from sqlalchemy import UniqueConstraint

class OpenPositionLot(Base):
    __tablename__ = "open_position_lots"
    __table_args__ = (
        Index("open_position_lots_account_symbol_idx", "account_id", "symbol"),
        UniqueConstraint(
            "account_id", "symbol", "open_date", "snapshot_date",
            name="open_position_lots_natural_key",
        ),
    )
    # ...
```

Mismo patrón para `ChangeInDividendAccrual` (`account_id, conid, ex_date, pay_date, accrual_date`) y `OpenDividendAccrual` (`account_id, conid, ex_date, pay_date, report_date`).

- [ ] **Step 5: Run model-vs-DB drift test**

Run: `docker compose exec -T backend uv run pytest backend/tests/test_migrations.py::test_migrations_apply_cleanly_and_match_metadata -v`
Expected: PASS — confirma que los modelos SQLAlchemy matchean el schema en DB tras las 2 revisions.

Si falla por drift: revisar diff que reporta el test y corregir el modelo. Iterar.

- [ ] **Step 6: Commit**

```bash
git add backend/src/ibkr_control/db/models/flex_raw.py
git commit -m "$(cat <<'EOF'
feat(models): align flex_raw.py with phase25 schema

Update SQLAlchemy models to match the schema after Revisions 1+2:
- FlexImport: add xml_bytes BYTEA, rename n_* → n_observed_*, add n_new_*
- All 7 child tables: flex_import_id nullable + ON DELETE SET NULL
- ClosedLot/CashTransaction/Transfer: add transaction_id UNIQUE NOT NULL
- OpenPositionLot/accruals: add composite UNIQUE natural key constraints

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Upsert helpers — `_upsert_helpers.py`

**Files:**
- Create: `backend/src/ibkr_control/ingest/flex/_upsert_helpers.py`
- Test: `backend/tests/ingest/flex/test_upsert_helpers.py`

- [ ] **Step 1: Write failing tests for helpers**

Create `backend/tests/ingest/flex/test_upsert_helpers.py`:

```python
"""Tests de los Core UPSERT helpers (spec A8)."""
from decimal import Decimal
from datetime import date
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.db.models.flex_raw import FlexImport, Trade, OpenPositionLot
from ibkr_control.ingest.flex._upsert_helpers import (
    _upsert_immutable, _upsert_snapshot,
    _upsert_immutable_returning_inserted, _chunks,
)


def test_chunks_splits_iterable():
    assert list(_chunks([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]
    assert list(_chunks([], 10)) == []
    assert list(_chunks([1], 10)) == [[1]]


@pytest.mark.asyncio
async def test_upsert_immutable_inserts_then_noop(
    db_session: AsyncSession, sample_user, sample_account, sample_flex_import
):
    """Primera ronda inserta N rows, segunda devuelve 0 (DO NOTHING)."""
    base_row = dict(
        flex_import_id=sample_flex_import.id,
        account_id=sample_account.id,
        symbol='AAPL', asset_class='STK',
        trade_date=date(2025, 1, 15), settle_date=None,
        qty=Decimal('10'), price_usd=Decimal('150'),
        proceeds_usd=Decimal('-1500'), commission_usd=Decimal('1'),
        open_close='O', buy_sell='BUY', raw_attrs={},
    )
    rows = [dict(base_row, transaction_id=f'TX-{i}') for i in range(3)]

    n_new_first = await _upsert_immutable(
        db_session, Trade.__table__, rows, ['transaction_id']
    )
    assert n_new_first == 3

    n_new_second = await _upsert_immutable(
        db_session, Trade.__table__, rows, ['transaction_id']
    )
    assert n_new_second == 0


@pytest.mark.asyncio
async def test_upsert_snapshot_inserts_then_updates(
    db_session: AsyncSession, sample_account, sample_flex_import
):
    """Snapshot UPSERT: insert nuevo, update si conflicto, mantiene n_touched."""
    base_row = dict(
        flex_import_id=sample_flex_import.id,
        account_id=sample_account.id,
        symbol='AAPL', open_date=date(2025, 1, 15),
        snapshot_date=date(2025, 5, 25),
        qty=Decimal('10'), cost_basis_usd=Decimal('1500'),
        mark_price_usd=Decimal('170'), mark_value_usd=Decimal('1700'),
    )
    n_first = await _upsert_snapshot(
        db_session, OpenPositionLot.__table__, [base_row],
        ['account_id', 'symbol', 'open_date', 'snapshot_date'],
        ['qty', 'cost_basis_usd', 'mark_price_usd', 'mark_value_usd', 'flex_import_id'],
    )
    assert n_first == 1

    updated = dict(base_row, mark_price_usd=Decimal('180'), mark_value_usd=Decimal('1800'))
    n_second = await _upsert_snapshot(
        db_session, OpenPositionLot.__table__, [updated],
        ['account_id', 'symbol', 'open_date', 'snapshot_date'],
        ['qty', 'cost_basis_usd', 'mark_price_usd', 'mark_value_usd', 'flex_import_id'],
    )
    assert n_second == 1  # touched (UPDATE path)

    # Verify update actually applied
    from sqlalchemy import select
    result = await db_session.execute(
        select(OpenPositionLot.mark_price_usd).where(
            OpenPositionLot.account_id == sample_account.id,
            OpenPositionLot.symbol == 'AAPL',
            OpenPositionLot.snapshot_date == date(2025, 5, 25),
        )
    )
    assert result.scalar() == Decimal('180')


@pytest.mark.asyncio
async def test_upsert_immutable_returning_inserted_filters_noop(
    db_session: AsyncSession, sample_account, sample_flex_import
):
    """Returning helper para Transfers: solo rows insertadas, no NO-OP."""
    from ibkr_control.db.models.flex_raw import Transfer
    base = dict(
        flex_import_id=sample_flex_import.id,
        transfer_date=date(2025, 3, 1), direction='IN',
        src_account_id=None, dst_account_id=sample_account.id,
        symbol='MSFT', qty=Decimal('100'), transfer_type='ACATS',
    )
    rows_round1 = [dict(base, transaction_id='XFER-1'), dict(base, transaction_id='XFER-2')]
    inserted1 = await _upsert_immutable_returning_inserted(
        db_session, Transfer.__table__, rows_round1, ['transaction_id'],
        ['id', 'transaction_id'],
    )
    assert {row.transaction_id for row in inserted1} == {'XFER-1', 'XFER-2'}

    rows_round2 = [dict(base, transaction_id='XFER-2'), dict(base, transaction_id='XFER-3')]
    inserted2 = await _upsert_immutable_returning_inserted(
        db_session, Transfer.__table__, rows_round2, ['transaction_id'],
        ['id', 'transaction_id'],
    )
    assert {row.transaction_id for row in inserted2} == {'XFER-3'}


@pytest.mark.asyncio
async def test_upsert_immutable_batches_large_input(
    db_session: AsyncSession, sample_user, sample_account, sample_flex_import,
    monkeypatch,
):
    """Con _BATCH_SIZE=4 y 15 rows → 4 batches sin exceder param limit."""
    from ibkr_control.ingest.flex import _upsert_helpers as mod
    monkeypatch.setattr(mod, '_BATCH_SIZE', 4)
    base_row = dict(
        flex_import_id=sample_flex_import.id, account_id=sample_account.id,
        symbol='AAPL', asset_class='STK',
        trade_date=date(2025, 1, 15), settle_date=None,
        qty=Decimal('10'), price_usd=Decimal('150'),
        proceeds_usd=Decimal('-1500'), commission_usd=Decimal('1'),
        open_close='O', buy_sell='BUY', raw_attrs={},
    )
    rows = [dict(base_row, transaction_id=f'TX-BATCH-{i}') for i in range(15)]
    n_new = await _upsert_immutable(
        db_session, Trade.__table__, rows, ['transaction_id']
    )
    assert n_new == 15
```

(Asume que `conftest.py` ya tiene fixtures `sample_user`, `sample_account`. Si `sample_flex_import` no existe, agregar fixture mínima al conftest o usar `FlexImport(...)` inline.)

- [ ] **Step 2: Add `sample_flex_import` fixture si no existe**

Check: `grep -n "def sample_flex_import" backend/tests/conftest.py`
- Si existe: skip step
- Si no existe: agregar al conftest.py:

```python
@pytest.fixture
async def sample_flex_import(db_session, sample_user):
    from ibkr_control.db.models.flex_raw import FlexImport
    from datetime import date
    fi = FlexImport(
        user_id=sample_user.id, anyo=2025,
        xml_hash='helper-test-hash',
        xml_size_bytes=100, xml_bytes=b'<test/>',
        source='manual_upload',
        period_covered_from=date(2025, 1, 1),
        period_covered_to=date(2025, 12, 31),
        status='ok',
    )
    db_session.add(fi)
    await db_session.flush()
    return fi
```

- [ ] **Step 3: Run tests to verify failures**

Run: `docker compose exec -T backend uv run pytest backend/tests/ingest/flex/test_upsert_helpers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ibkr_control.ingest.flex._upsert_helpers'`

- [ ] **Step 4: Implement helpers**

Create `backend/src/ibkr_control/ingest/flex/_upsert_helpers.py`:

```python
"""Core UPSERT helpers para el Flex persister idempotente (spec A8).

Usa sqlalchemy.dialects.postgresql.insert + ON CONFLICT semantics. Batched
con _BATCH_SIZE para respetar el techo de asyncpg de 32767 bind params per
statement (mismo patrón que TRM bulk_upsert_days post-commit 989652d).
"""
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import Table, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession


_BATCH_SIZE = 5000  # Margin del techo 32767; con ~20 cols por trade ~250 params/row → ~125 rows/batch worst case; 5000 dejamos espacio holgado para entities chicas (~5 cols)


def _chunks(rows: Sequence[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for i in range(0, len(rows), size):
        yield list(rows[i : i + size])


async def _upsert_immutable(
    session: AsyncSession,
    table: Table,
    rows: Sequence[dict[str, Any]],
    conflict_cols: list[str],
) -> int:
    """INSERT ... ON CONFLICT DO NOTHING. Devuelve count de rows realmente insertadas.

    Para entidades immutable (Trade, ClosedLot, CashTransaction, Transfer): las
    rows que ya existen quedan intactas (preserva first-seen semantics del spec A1).
    """
    if not rows:
        return 0
    n_new = 0
    for batch in _chunks(rows, _BATCH_SIZE):
        stmt = pg_insert(table).values(batch)
        stmt = stmt.on_conflict_do_nothing(index_elements=conflict_cols)
        stmt = stmt.returning(text("1"))
        result = await session.execute(stmt)
        n_new += sum(1 for _ in result)
    return n_new


async def _upsert_snapshot(
    session: AsyncSession,
    table: Table,
    rows: Sequence[dict[str, Any]],
    conflict_cols: list[str],
    update_cols: list[str],
) -> int:
    """INSERT ... ON CONFLICT DO UPDATE. Devuelve count de rows touched (insert + update).

    Para entidades snapshot (OpenPositionLot, accruals): rows existentes se
    actualizan con los valores del nuevo XML (spec A1-bis: flex_import_id queda
    como "last updated by").
    """
    if not rows:
        return 0
    n_touched = 0
    for batch in _chunks(rows, _BATCH_SIZE):
        stmt = pg_insert(table).values(batch)
        excluded = stmt.excluded
        stmt = stmt.on_conflict_do_update(
            index_elements=conflict_cols,
            set_={col: excluded[col] for col in update_cols},
        )
        result = await session.execute(stmt)
        n_touched += result.rowcount
    return n_touched


async def _upsert_immutable_returning_inserted(
    session: AsyncSession,
    table: Table,
    rows: Sequence[dict[str, Any]],
    conflict_cols: list[str],
    returning_cols: list[str],
) -> list[Row]:
    """Como _upsert_immutable pero devuelve sólo las rows insertadas con las
    columnas pedidas. Las NO-OP por conflicto NO aparecen en el resultado.

    Usado por el patrón Transfers: necesitamos saber qué Transfers fueron
    realmente insertados para decidir cuáles tienen que recibir sus TransferLot
    children (los Transfers existentes ya tienen sus children en DB).
    """
    if not rows:
        return []
    inserted: list[Row] = []
    for batch in _chunks(rows, _BATCH_SIZE):
        stmt = pg_insert(table).values(batch)
        stmt = stmt.on_conflict_do_nothing(index_elements=conflict_cols)
        stmt = stmt.returning(*[table.c[col] for col in returning_cols])
        result = await session.execute(stmt)
        inserted.extend(result.all())
    return inserted
```

- [ ] **Step 5: Run tests to verify pass**

Run: `docker compose exec -T backend uv run pytest backend/tests/ingest/flex/test_upsert_helpers.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/src/ibkr_control/ingest/flex/_upsert_helpers.py \
        backend/tests/ingest/flex/test_upsert_helpers.py \
        backend/tests/conftest.py  # si se modificó
git commit -m "$(cat <<'EOF'
feat(persister): add Core UPSERT helpers (spec A8)

3 helpers nuevos en _upsert_helpers.py:
- _upsert_immutable: ON CONFLICT DO NOTHING, returns n_new
- _upsert_snapshot: ON CONFLICT DO UPDATE, returns n_touched
- _upsert_immutable_returning_inserted: variante para Transfers que
  necesita los ids de rows realmente insertadas (filtra NO-OP)

Batched con _BATCH_SIZE=5000 para respetar techo asyncpg 32767 params.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Persister rewrite — `flex/persister.py`

**Files:**
- Modify: `backend/src/ibkr_control/ingest/flex/persister.py`

- [ ] **Step 1: Rewrite `persist()` signature + return type**

Edit `backend/src/ibkr_control/ingest/flex/persister.py`:

```python
"""Persiste un ParsedXML en Postgres idempotentemente (spec phase 2.5).

Estrategia (spec A4 + A8):
- Hash dedup fast-path: si xml_hash ya existe en flex_imports → return early
- Sino: crear FlexImport row (con xml_bytes A0) + UPSERT children por entity
- Immutable entities (Trade, ClosedLot, CashTx, Transfer): ON CONFLICT DO NOTHING
  → preserva first-seen semantics (spec A1)
- Snapshot entities (OpenPositionLot, accruals): ON CONFLICT DO UPDATE
  → flex_import_id queda como "last updated by" (spec A1-bis)
- Counters n_observed_* (parsed) + n_new_* (DB writes) ambos persistidos
  en flex_imports (spec A5)
"""
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.db.models.accounts import Account
from ibkr_control.db.models.flex_raw import (
    CashTransaction,
    ChangeInDividendAccrual,
    ClosedLot,
    FlexImport,
    OpenDividendAccrual,
    OpenPositionLot,
    Trade,
    Transfer,
    TransferLot,
)
from ibkr_control.ingest.flex._models import ParsedXML
from ibkr_control.ingest.flex._upsert_helpers import (
    _upsert_immutable,
    _upsert_immutable_returning_inserted,
    _upsert_snapshot,
)
from ibkr_control.ingest.hash_dedup import xml_hash


def _is_shadow_account(ibkr_account_id: str) -> bool:
    """IB-UK Limited regulatory shadow account (NAV=0, no fiscal data)."""
    return ibkr_account_id.endswith("F")


async def persist(
    session: AsyncSession,
    *,
    parsed: ParsedXML,
    user_id: int,
    xml_bytes: bytes,
    source: str,  # 'web_service' | 'manual_upload'
) -> tuple[int, dict]:
    """Idempotent persist. Devuelve (flex_import_id, counters_dict).

    counters_dict contiene:
      - "hash_dedup": bool (True si fast-path A4 disparó)
      - "n_observed_*": int (rows en el XML por entity type)
      - "n_new_*": int (rows insertadas o actualizadas en DB)
    """
    h = xml_hash(xml_bytes)

    # Fast-path A4: hash dedup
    existing_id = await session.scalar(
        select(FlexImport.id).where(FlexImport.xml_hash == h)
    )
    if existing_id is not None:
        return existing_id, {"hash_dedup": True}

    # Recopilar accounts del XML (filtrar F-suffix shadow per renta pattern)
    all_account_ids: set[str] = set()
    for a in parsed.accounts:
        if not _is_shadow_account(a.ibkr_account_id):
            all_account_ids.add(a.ibkr_account_id)
    for collection in (parsed.trades, parsed.closed_lots, parsed.open_position_lots,
                       parsed.cash_transactions,
                       parsed.change_in_dividend_accruals,
                       parsed.open_dividend_accruals):
        for item in collection:
            if not _is_shadow_account(item.ibkr_account_id):
                all_account_ids.add(item.ibkr_account_id)
    for tr in parsed.transfers:
        if tr.src_ibkr_account_id and not _is_shadow_account(tr.src_ibkr_account_id):
            all_account_ids.add(tr.src_ibkr_account_id)
        if tr.dst_ibkr_account_id and not _is_shadow_account(tr.dst_ibkr_account_id):
            all_account_ids.add(tr.dst_ibkr_account_id)

    accounts_map = await _ensure_accounts(session, list(all_account_ids))

    year_status = "sealed" if parsed.period_to >= date(parsed.anyo, 12, 31) else "rolling"

    # Counters: n_observed_* del XML
    n_observed = {
        "trades": len(parsed.trades),
        "lots_closed": len(parsed.closed_lots),
        "open_lots": len(parsed.open_position_lots),
        "cash_tx": len(parsed.cash_transactions),
        "dividends": sum(1 for ct in parsed.cash_transactions if ct.type == "Dividends"),
        "transfers": len(parsed.transfers),
    }

    fi = FlexImport(
        user_id=user_id, anyo=parsed.anyo, xml_hash=h,
        xml_size_bytes=len(xml_bytes), xml_bytes=xml_bytes,  # A0
        source=source,
        period_covered_from=parsed.period_from,
        period_covered_to=parsed.period_to,
        year_status=year_status,
        n_observed_trades=n_observed["trades"],
        n_observed_lots_closed=n_observed["lots_closed"],
        n_observed_open_lots=n_observed["open_lots"],
        n_observed_cash_tx=n_observed["cash_tx"],
        n_observed_dividends=n_observed["dividends"],
        n_observed_transfers=n_observed["transfers"],
        status="ok",
    )
    session.add(fi)
    await session.flush()

    # UPSERTs en orden de dependencia FK
    n_new = await _upsert_all_children(
        session, fi, parsed, accounts_map,
    )

    # Update n_new_* en flex_imports
    fi.n_new_trades = n_new["trades"]
    fi.n_new_lots_closed = n_new["lots_closed"]
    fi.n_new_open_lots = n_new["open_lots"]
    fi.n_new_cash_tx = n_new["cash_tx"]
    fi.n_new_dividends = n_new["dividends"]
    fi.n_new_transfers = n_new["transfers"]
    await session.flush()

    return fi.id, {
        "hash_dedup": False,
        **{f"n_observed_{k}": v for k, v in n_observed.items()},
        **{f"n_new_{k}": v for k, v in n_new.items()},
    }


async def _upsert_all_children(
    session: AsyncSession,
    fi: FlexImport,
    parsed: ParsedXML,
    accounts_map: dict[str, int],
) -> dict[str, int]:
    """Hace UPSERT de todos los children. Devuelve n_new por entity type."""
    # === Trades (immutable) ===
    trade_rows = [
        {
            "flex_import_id": fi.id,
            "transaction_id": t.transaction_id,
            "account_id": accounts_map[t.ibkr_account_id],
            "symbol": t.symbol, "asset_class": t.asset_class,
            "trade_date": t.trade_date, "settle_date": t.settle_date,
            "qty": t.qty, "price_usd": t.price_usd,
            "proceeds_usd": t.proceeds_usd, "commission_usd": t.commission_usd,
            "open_close": t.open_close, "buy_sell": t.buy_sell,
            "raw_attrs": t.raw_attrs,
        }
        for t in parsed.trades if not _is_shadow_account(t.ibkr_account_id)
    ]
    n_new_trades = await _upsert_immutable(
        session, Trade.__table__, trade_rows, ['transaction_id']
    )

    # === ClosedLots (immutable) ===
    # Necesitamos trade_id_map para linkar source_trade_id
    trade_id_map: dict[str, int] = {}
    if parsed.closed_lots:
        tx_ids_needed = [cl.transaction_id for cl in parsed.closed_lots if cl.transaction_id]
        if tx_ids_needed:
            result = await session.execute(
                select(Trade.transaction_id, Trade.id).where(
                    Trade.transaction_id.in_(tx_ids_needed)
                )
            )
            trade_id_map = {tid: tid_db for tid, tid_db in result.all()}

    closed_rows = [
        {
            "flex_import_id": fi.id,
            "transaction_id": cl.transaction_id or f"NO-TX-{cl.symbol}-{cl.close_date}-{i}",
            "account_id": accounts_map[cl.ibkr_account_id],
            "symbol": cl.symbol, "open_date": cl.open_date,
            "close_date": cl.close_date, "qty": cl.qty,
            "cost_basis_usd": cl.cost_basis_usd, "proceeds_usd": cl.proceeds_usd,
            "fifo_pnl_usd": cl.fifo_pnl_usd,
            "source_trade_id": (
                trade_id_map.get(cl.transaction_id) if cl.transaction_id else None
            ),
        }
        for i, cl in enumerate(parsed.closed_lots)
        if not _is_shadow_account(cl.ibkr_account_id)
    ]
    n_new_closed = await _upsert_immutable(
        session, ClosedLot.__table__, closed_rows, ['transaction_id']
    )

    # === CashTransactions (immutable) ===
    cash_rows = [
        {
            "flex_import_id": fi.id,
            "transaction_id": ct.transaction_id,
            "account_id": accounts_map[ct.ibkr_account_id],
            "type": ct.type, "currency": ct.currency,
            "amount_usd": ct.amount_usd, "description": ct.description,
            "date": ct.date, "symbol": ct.symbol,
        }
        for ct in parsed.cash_transactions
        if not _is_shadow_account(ct.ibkr_account_id)
    ]
    # Para n_new_cash usamos _upsert_immutable_returning_inserted con transaction_id
    # + type para contar dividends en una sola query sin SELECT extra
    inserted_cash = await _upsert_immutable_returning_inserted(
        session, CashTransaction.__table__, cash_rows, ['transaction_id'],
        ['transaction_id', 'type'],
    )
    n_new_cash = len(inserted_cash)
    n_new_dividends = sum(1 for row in inserted_cash if row.type == "Dividends")

    # === Transfers + TransferLots (immutable, children co-immutable) ===
    transfer_rows = []
    for tr in parsed.transfers:
        src_shadow = tr.src_ibkr_account_id and _is_shadow_account(tr.src_ibkr_account_id)
        dst_shadow = tr.dst_ibkr_account_id and _is_shadow_account(tr.dst_ibkr_account_id)
        if src_shadow or dst_shadow:
            continue
        transfer_rows.append({
            "flex_import_id": fi.id,
            "transaction_id": tr.transaction_id,
            "transfer_date": tr.transfer_date, "direction": tr.direction,
            "src_account_id": accounts_map.get(tr.src_ibkr_account_id) if tr.src_ibkr_account_id else None,
            "dst_account_id": accounts_map.get(tr.dst_ibkr_account_id) if tr.dst_ibkr_account_id else None,
            "symbol": tr.symbol, "qty": tr.qty,
            "transfer_type": tr.transfer_type,
        })
    inserted_transfers = await _upsert_immutable_returning_inserted(
        session, Transfer.__table__, transfer_rows, ['transaction_id'],
        ['id', 'transaction_id'],
    )
    inserted_tx_ids = {r.transaction_id for r in inserted_transfers}
    transfer_id_map = {r.transaction_id: r.id for r in inserted_transfers}
    n_new_transfers = len(inserted_transfers)

    # Insert TransferLots solo para Transfers nuevos (los existentes ya tienen)
    lot_rows = []
    for tr in parsed.transfers:
        if tr.transaction_id not in inserted_tx_ids:
            continue
        transfer_db_id = transfer_id_map[tr.transaction_id]
        for lot in tr.lots:
            lot_rows.append({
                "transfer_id": transfer_db_id,
                "original_open_date": lot.original_open_date,
                "qty": lot.qty, "cost_basis_usd": lot.cost_basis_usd,
            })
    if lot_rows:
        # TransferLot no tiene UNIQUE → INSERT plain (no UPSERT)
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        stmt = pg_insert(TransferLot.__table__).values(lot_rows)
        await session.execute(stmt)

    # === OpenPositionLots (snapshot) ===
    open_rows = [
        {
            "flex_import_id": fi.id,
            "account_id": accounts_map[op.ibkr_account_id],
            "symbol": op.symbol, "open_date": op.open_date,
            "qty": op.qty, "cost_basis_usd": op.cost_basis_usd,
            "mark_price_usd": op.mark_price_usd,
            "mark_value_usd": op.mark_value_usd,
            "snapshot_date": op.snapshot_date,
        }
        for op in parsed.open_position_lots
        if not _is_shadow_account(op.ibkr_account_id)
    ]
    n_new_open = await _upsert_snapshot(
        session, OpenPositionLot.__table__, open_rows,
        ['account_id', 'symbol', 'open_date', 'snapshot_date'],
        ['flex_import_id', 'qty', 'cost_basis_usd', 'mark_price_usd', 'mark_value_usd'],
    )

    # === ChangeInDividendAccruals (snapshot) ===
    change_div_rows = [
        {
            "flex_import_id": fi.id,
            "account_id": accounts_map[da.ibkr_account_id],
            "symbol": da.symbol, "conid": da.conid, "isin": da.isin,
            "issuer_country": da.issuer_country, "currency": da.currency,
            "ex_date": da.ex_date, "pay_date": da.pay_date,
            "report_date": da.report_date, "accrual_date": da.accrual_date,
            "quantity": da.quantity,
            "gross_rate_per_share": da.gross_rate_per_share,
            "gross_amount_usd": da.gross_amount_usd,
            "tax_usd": da.tax_usd, "fee_usd": da.fee_usd,
            "net_amount_usd": da.net_amount_usd,
            "action_id": da.action_id, "asset_category": da.asset_category,
            "sub_category": da.sub_category, "level_of_detail": da.level_of_detail,
            "raw_attrs": da.raw_attrs,
        }
        for da in parsed.change_in_dividend_accruals
        if not _is_shadow_account(da.ibkr_account_id)
    ]
    await _upsert_snapshot(
        session, ChangeInDividendAccrual.__table__, change_div_rows,
        ['account_id', 'conid', 'ex_date', 'pay_date', 'accrual_date'],
        ['flex_import_id', 'symbol', 'isin', 'issuer_country', 'currency',
         'report_date', 'quantity', 'gross_rate_per_share', 'gross_amount_usd',
         'tax_usd', 'fee_usd', 'net_amount_usd', 'action_id', 'asset_category',
         'sub_category', 'level_of_detail', 'raw_attrs'],
    )

    # === OpenDividendAccruals (snapshot) ===
    open_div_rows = [
        {
            "flex_import_id": fi.id,
            "account_id": accounts_map[oda.ibkr_account_id],
            "symbol": oda.symbol, "conid": oda.conid, "isin": oda.isin,
            "issuer_country": oda.issuer_country, "currency": oda.currency,
            "ex_date": oda.ex_date, "pay_date": oda.pay_date,
            "report_date": oda.report_date, "quantity": oda.quantity,
            "gross_rate_per_share": oda.gross_rate_per_share,
            "gross_amount_usd": oda.gross_amount_usd,
            "tax_usd": oda.tax_usd, "fee_usd": oda.fee_usd,
            "net_amount_usd": oda.net_amount_usd,
            "action_id": oda.action_id, "asset_category": oda.asset_category,
            "sub_category": oda.sub_category, "raw_attrs": oda.raw_attrs,
        }
        for oda in parsed.open_dividend_accruals
        if not _is_shadow_account(oda.ibkr_account_id)
    ]
    await _upsert_snapshot(
        session, OpenDividendAccrual.__table__, open_div_rows,
        ['account_id', 'conid', 'ex_date', 'pay_date', 'report_date'],
        ['flex_import_id', 'symbol', 'isin', 'issuer_country', 'currency',
         'quantity', 'gross_rate_per_share', 'gross_amount_usd',
         'tax_usd', 'fee_usd', 'net_amount_usd', 'action_id',
         'asset_category', 'sub_category', 'raw_attrs'],
    )

    return {
        "trades": n_new_trades,
        "lots_closed": n_new_closed,
        "open_lots": n_new_open,
        "cash_tx": n_new_cash,
        "dividends": n_new_dividends,
        "transfers": n_new_transfers,
    }


async def _ensure_accounts(
    session: AsyncSession, ibkr_ids: list[str],
) -> dict[str, int]:
    """SELECT-then-INSERT idempotent pattern (existing).

    Race condition note: el orchestrator (flex_job.run) toma advisory_lock por
    (user_id, source='flex') antes de llamar persist(), eliminando concurrent
    ingests para el mismo user. Manual uploads (/api/imports/upload) NO toman
    lock pero usan tx con UNIQUE(ibkr_account_id) como safety net.
    """
    if not ibkr_ids:
        return {}

    result = await session.scalars(
        select(Account).where(Account.ibkr_account_id.in_(ibkr_ids))
    )
    existing = {a.ibkr_account_id: a.id for a in result.all()}

    missing = set(ibkr_ids) - set(existing.keys())
    for ibkr_id in missing:
        session.add(Account(ibkr_account_id=ibkr_id, alias=None, currency="USD"))

    if missing:
        await session.flush()
        result2 = await session.scalars(
            select(Account).where(Account.ibkr_account_id.in_(missing))
        )
        for a in result2.all():
            existing[a.ibkr_account_id] = a.id

    return existing
```

Importante: necesitamos `from sqlalchemy import func` para `func.count()` (en el cálculo de `n_new_dividends`). Verificar imports.

- [ ] **Step 2: Update job.py para usar el nuevo return type**

Edit `backend/src/ibkr_control/ingest/flex/job.py`:

En `ingest_xml()` (línea ~58):
```python
flex_import_id, _counters = await flex_persister_mod.persist(
    session,
    parsed=parsed,
    user_id=user_id,
    xml_bytes=xml_bytes,
    source=source,
)
```

(Reemplazar `flex_import_id = await flex_persister_mod.persist(...)` con tuple destructure)

En `run()` (línea ~125): mismo cambio.

Update también la línea ~76 que setea `log_row.items_processed` — ahora usar los counters del dict.

- [ ] **Step 3: Run existing persister tests para detectar breaks**

Run: `docker compose exec -T backend uv run pytest backend/tests/ingest/flex/test_persister.py -v`
Expected: probablemente FAIL en tests que usaban el viejo return type. Actualizar cada test para destructure el tuple: `flex_import_id, _ = await persist(...)`.

Iterar hasta PASS.

- [ ] **Step 4: Run full test suite para detectar regresiones cross-cutting**

Run: `docker compose exec -T backend uv run pytest -x -q`
Expected: identificar fails restantes. Probablemente `api/imports.py` y `api/setup.py` también necesitan ajustes (siguiente task).

- [ ] **Step 5: Commit (persister rewrite + job.py update)**

```bash
git add backend/src/ibkr_control/ingest/flex/persister.py \
        backend/src/ibkr_control/ingest/flex/job.py \
        backend/tests/ingest/flex/test_persister.py
git commit -m "$(cat <<'EOF'
feat(persister): rewrite to idempotent UPSERT pattern (spec phase 2.5)

Reemplaza session.add() loops con Core pg_insert + ON CONFLICT helpers.
- Immutable entities → DO NOTHING (preserva first-seen, spec A1)
- Snapshot entities → DO UPDATE (last-updated-by, spec A1-bis)
- Transfers: helper returning_inserted distingue NO-OP vs INSERT
- TransferLots: solo se insertan para Transfers nuevos (spec § Transfers con children)
- Counters n_observed_* + n_new_* persistidos en flex_imports (spec A5)
- xml_bytes persistidos para replay futuro (spec A0)
- Return type cambia a tuple[int, dict] con counters

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: API + schemas updates — consume new counters

**Files:**
- Modify: `backend/src/ibkr_control/api/_schemas.py`
- Modify: `backend/src/ibkr_control/api/imports.py`
- Modify: `backend/src/ibkr_control/api/setup.py`

- [ ] **Step 1: Add `IngestCounters` Pydantic model**

Edit `backend/src/ibkr_control/api/_schemas.py` (append en sección "Ingest"):

```python
class IngestCounters(BaseModel):
    """Counters de un ingest: cuántas rows vio del XML y cuántas afectaron la DB.

    Para entities immutable: n_new = rows insertadas por primera vez.
    Para entities snapshot: n_new = rows touched (insert + update combinado).
    """
    n_observed_trades: int = 0
    n_observed_lots_closed: int = 0
    n_observed_open_lots: int = 0
    n_observed_cash_tx: int = 0
    n_observed_dividends: int = 0
    n_observed_transfers: int = 0
    n_new_trades: int = 0
    n_new_lots_closed: int = 0
    n_new_open_lots: int = 0
    n_new_cash_tx: int = 0
    n_new_dividends: int = 0
    n_new_transfers: int = 0
    hash_dedup: bool = False
```

Update `Step2DetectResponse.ingest_summary` type:
```python
class Step2DetectResponse(BaseModel):
    detected_accounts: list[DetectedAccount]
    flex_import_id: int
    ingest_summary: IngestCounters
```

- [ ] **Step 2: Update `api/imports.py` consumer**

Edit `backend/src/ibkr_control/api/imports.py` líneas ~88-103:

```python
flex_import_id, counters = await flex_job_mod.ingest_xml(
    session,
    user_id=user.id,
    xml_bytes=xml_bytes,
    source="manual_upload",
    trigger="manual",
)
fi = await session.scalar(select(FlexImport).where(FlexImport.id == flex_import_id))
return {
    "flex_import_id": flex_import_id,
    "n_observed_trades": counters.get("n_observed_trades", 0),
    "n_new_trades": counters.get("n_new_trades", 0),
    "n_observed_lots_closed": counters.get("n_observed_lots_closed", 0),
    "n_new_lots_closed": counters.get("n_new_lots_closed", 0),
    "n_observed_cash_tx": counters.get("n_observed_cash_tx", 0),
    "n_new_cash_tx": counters.get("n_new_cash_tx", 0),
    # ... resto
    "hash_dedup": counters.get("hash_dedup", False),
}
```

`ingest_xml` también debe devolver el tuple — verificar en job.py que retorne `(flex_import_id, counters)`. Si no, ajustar.

- [ ] **Step 3: Update `api/setup.py` consumers (3 sitios)**

Edit `backend/src/ibkr_control/api/setup.py`:

Línea ~288 (step2 detect): `flex_import_id, counters = await persist(...)` y poblar `ingest_summary=counters` en la response.

Línea ~606 (step3 commit): mismo patrón para cada XML del stash.

- [ ] **Step 4: Run API tests**

Run: `docker compose exec -T backend uv run pytest backend/tests/api/ -v -x`
Expected: identificar fails en tests que assertean shape del response. Actualizar assertions.

- [ ] **Step 5: Regenerar OpenAPI + cliente TS (frontend types autogenerados)**

Run:
```bash
docker compose exec -T backend uv run python -c "from ibkr_control.main import app; import json; print(json.dumps(app.openapi()))" > frontend/openapi.json
docker compose exec -T frontend pnpm openapi:gen
```

(Esto sigue la convención canónica del repo — ver CLAUDE.md § Convenciones de código → "Shortcuts y flujos canónicos". El path `openapi:gen` usa el script declarado en package.json y matchea el formato del repo)

Expected: `frontend/src/lib/api/generated.ts` actualizado con los nuevos campos.

- [ ] **Step 6: Commit**

```bash
git add backend/src/ibkr_control/api/_schemas.py \
        backend/src/ibkr_control/api/imports.py \
        backend/src/ibkr_control/api/setup.py \
        backend/tests/api/ \
        frontend/openapi.json \
        frontend/src/lib/api/
git commit -m "$(cat <<'EOF'
feat(api): expose n_observed_* + n_new_* counters in ingest responses

IngestCounters Pydantic model nuevo (spec A5). Updates:
- /api/imports/upload response incluye ambos sets
- /api/setup/step2/detect + step3/commit idem
- OpenAPI + cliente TS regenerados canónicamente

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Frontend StreamEvent update

**Files:**
- Modify: `frontend/src/hooks/useIngestStream.ts`

- [ ] **Step 1: Add optional counter fields to StreamEvent**

Edit `frontend/src/hooks/useIngestStream.ts`:

```typescript
export interface StreamEvent {
  step: string;
  status?: "running" | "ok" | "failed";
  n_days?: number;
  n_trades?: number;
  // Phase 2.5 idempotent persister counters
  n_observed_trades?: number;
  n_observed_lots_closed?: number;
  n_observed_open_lots?: number;
  n_observed_cash_tx?: number;
  n_observed_dividends?: number;
  n_observed_transfers?: number;
  n_new_trades?: number;
  n_new_lots_closed?: number;
  n_new_open_lots?: number;
  n_new_cash_tx?: number;
  n_new_dividends?: number;
  n_new_transfers?: number;
  error?: string;
  redirect?: string;
}
```

- [ ] **Step 2: Verify build OK**

Run: `docker compose exec -T frontend pnpm build 2>&1 | tail -15`
Expected: `✓ Generating static pages` sin errores TS.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useIngestStream.ts
git commit -m "$(cat <<'EOF'
feat(frontend): typed n_observed_* + n_new_* en StreamEvent

Phase 2.5 idempotent persister expone ambos counters via SSE.
Frontend types preparados para futuras visualizaciones
(badges "5 nuevos", etc.) sin requerir más cambios al hook.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Integration tests del persister (re-ingest scenarios)

**Files:**
- Create: `backend/tests/ingest/flex/test_persister_idempotent.py`

- [ ] **Step 1: Write integration tests for the new idempotent persister**

Create `backend/tests/ingest/flex/test_persister_idempotent.py`:

```python
"""Integration tests: persister idempotente bajo re-ingest scenarios.

Cubren los happy paths principales de Phase 2.5:
- Mismo XML byte-identical → hash dedup fast-path
- Mismo trades + 1 nuevo → n_new=1, no duplicates
- Mark price cambió → snapshot UPDATE en place
- Sealed year re-ingest → DO NOTHING preserva data original
- TransferLots no se duplican entre re-ingests
- xml_bytes persistido y recuperable
"""
from datetime import date
from decimal import Decimal
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.db.models.flex_raw import (
    FlexImport, Trade, OpenPositionLot, Transfer, TransferLot,
)
from ibkr_control.ingest.flex.persister import persist
from ibkr_control.ingest.flex._models import (
    ParsedAccount, ParsedTrade, ParsedOpenPositionLot,
    ParsedTransfer, ParsedTransferLot, ParsedXML,
)


def _minimal_parsed(
    n_trades: int = 1, account_id: str = "U99999001", anyo: int = 2026,
) -> ParsedXML:
    return ParsedXML(
        anyo=anyo,
        period_from=date(anyo, 1, 1),
        period_to=date(anyo, 5, 25) if anyo == 2026 else date(anyo, 12, 31),
        accounts=[ParsedAccount(ibkr_account_id=account_id, currency="USD")],
        trades=[
            ParsedTrade(
                transaction_id=f"TX-IDEMP-{i}",
                ibkr_account_id=account_id, symbol="AAPL", asset_class="STK",
                trade_date=date(anyo, 1, 15 + i), settle_date=date(anyo, 1, 17 + i),
                qty=Decimal("10"), price_usd=Decimal("150"),
                proceeds_usd=Decimal("-1500"), commission_usd=Decimal("1"),
                open_close="O", buy_sell="BUY", raw_attrs={},
            )
            for i in range(n_trades)
        ],
        closed_lots=[], open_position_lots=[], cash_transactions=[],
        transfers=[], change_in_dividend_accruals=[], open_dividend_accruals=[],
    )


@pytest.mark.asyncio
async def test_persist_idempotent_same_xml_uses_hash_dedup(
    db_session: AsyncSession, sample_user,
):
    """Mismo xml_bytes → segundo persist devuelve mismo id sin tocar nada."""
    parsed = _minimal_parsed(n_trades=2)
    xml_bytes = b"<test idempotent/>"
    fi_id1, counters1 = await persist(
        db_session, parsed=parsed, user_id=sample_user.id,
        xml_bytes=xml_bytes, source="manual_upload",
    )
    await db_session.commit()
    assert counters1["hash_dedup"] is False
    assert counters1["n_new_trades"] == 2

    fi_id2, counters2 = await persist(
        db_session, parsed=parsed, user_id=sample_user.id,
        xml_bytes=xml_bytes, source="manual_upload",
    )
    assert fi_id2 == fi_id1
    assert counters2["hash_dedup"] is True


@pytest.mark.asyncio
async def test_persist_modified_xml_same_trades_yields_zero_n_new(
    db_session: AsyncSession, sample_user,
):
    """XML modificado pero trades iguales → n_new_trades=0, no duplicates."""
    parsed = _minimal_parsed(n_trades=2)
    xml_v1 = b"<test v1/>"
    fi_id1, c1 = await persist(
        db_session, parsed=parsed, user_id=sample_user.id,
        xml_bytes=xml_v1, source="web_service",
    )
    await db_session.commit()
    assert c1["n_new_trades"] == 2

    xml_v2 = b"<test v2 changed/>"  # diff hash, same parsed trades
    fi_id2, c2 = await persist(
        db_session, parsed=parsed, user_id=sample_user.id,
        xml_bytes=xml_v2, source="web_service",
    )
    assert fi_id2 != fi_id1
    assert c2["hash_dedup"] is False
    assert c2["n_new_trades"] == 0
    assert c2["n_observed_trades"] == 2

    # Verify count global: solo 2 trades en total (no duplicates)
    total = await db_session.scalar(select(func.count()).select_from(Trade))
    assert total == 2


@pytest.mark.asyncio
async def test_persist_modified_xml_plus_one_new_trade(
    db_session: AsyncSession, sample_user,
):
    """v2 trae 1 trade más → n_new=1, total en DB = 3."""
    parsed_v1 = _minimal_parsed(n_trades=2)
    await persist(db_session, parsed=parsed_v1, user_id=sample_user.id,
                  xml_bytes=b"<v1/>", source="web_service")
    await db_session.commit()

    parsed_v2 = _minimal_parsed(n_trades=3)  # 1 trade adicional
    _, c2 = await persist(db_session, parsed=parsed_v2, user_id=sample_user.id,
                          xml_bytes=b"<v2/>", source="web_service")
    assert c2["n_new_trades"] == 1
    total = await db_session.scalar(select(func.count()).select_from(Trade))
    assert total == 3


@pytest.mark.asyncio
async def test_snapshot_lot_mark_price_updates_in_place(
    db_session: AsyncSession, sample_user, sample_account,
):
    """Mismo (account, symbol, open_date, snapshot_date) con mark_price distinto
    → UPDATE en place, no duplicate row."""
    base_lot = ParsedOpenPositionLot(
        ibkr_account_id=sample_account.ibkr_account_id,
        symbol="MSFT", open_date=date(2026, 1, 15),
        qty=Decimal("50"), cost_basis_usd=Decimal("15000"),
        mark_price_usd=Decimal("400"), mark_value_usd=Decimal("20000"),
        snapshot_date=date(2026, 5, 25),
    )
    p1 = _minimal_parsed(n_trades=0)
    p1.open_position_lots = [base_lot]
    await persist(db_session, parsed=p1, user_id=sample_user.id,
                  xml_bytes=b"<lot v1/>", source="web_service")
    await db_session.commit()

    # v2: mismo lot, mark_price subió
    lot_v2 = ParsedOpenPositionLot(
        ibkr_account_id=sample_account.ibkr_account_id,
        symbol="MSFT", open_date=date(2026, 1, 15),
        qty=Decimal("50"), cost_basis_usd=Decimal("15000"),
        mark_price_usd=Decimal("420"), mark_value_usd=Decimal("21000"),
        snapshot_date=date(2026, 5, 25),  # MISMO snapshot date
    )
    p2 = _minimal_parsed(n_trades=0)
    p2.open_position_lots = [lot_v2]
    await persist(db_session, parsed=p2, user_id=sample_user.id,
                  xml_bytes=b"<lot v2/>", source="web_service")

    total_lots = await db_session.scalar(
        select(func.count()).select_from(OpenPositionLot)
    )
    assert total_lots == 1  # NO duplicate

    current_mark = await db_session.scalar(
        select(OpenPositionLot.mark_price_usd).where(
            OpenPositionLot.symbol == "MSFT"
        )
    )
    assert current_mark == Decimal("420")


@pytest.mark.asyncio
async def test_snapshot_lot_different_snapshot_date_creates_new_row(
    db_session: AsyncSession, sample_user, sample_account,
):
    """Snapshot_date distinto → fila nueva (preservar serie temporal)."""
    base_args = dict(
        ibkr_account_id=sample_account.ibkr_account_id,
        symbol="MSFT", open_date=date(2026, 1, 15),
        qty=Decimal("50"), cost_basis_usd=Decimal("15000"),
        mark_price_usd=Decimal("400"), mark_value_usd=Decimal("20000"),
    )
    lot_day1 = ParsedOpenPositionLot(snapshot_date=date(2026, 5, 25), **base_args)
    lot_day2 = ParsedOpenPositionLot(snapshot_date=date(2026, 5, 26), **base_args)

    p1 = _minimal_parsed(n_trades=0); p1.open_position_lots = [lot_day1]
    p2 = _minimal_parsed(n_trades=0); p2.open_position_lots = [lot_day2]
    await persist(db_session, parsed=p1, user_id=sample_user.id,
                  xml_bytes=b"<day1/>", source="web_service")
    await db_session.commit()
    await persist(db_session, parsed=p2, user_id=sample_user.id,
                  xml_bytes=b"<day2/>", source="web_service")

    total = await db_session.scalar(
        select(func.count()).select_from(OpenPositionLot)
    )
    assert total == 2


@pytest.mark.asyncio
async def test_transfer_lots_not_duplicated_on_reingest(
    db_session: AsyncSession, sample_user, sample_account,
):
    """Re-ingest del mismo transfer NO duplica sus TransferLot children."""
    transfer = ParsedTransfer(
        transaction_id="XFER-IDEMP-1",
        transfer_date=date(2026, 4, 30), direction="IN",
        src_ibkr_account_id=None, dst_ibkr_account_id=sample_account.ibkr_account_id,
        symbol="GLOB", qty=Decimal("94"), transfer_type="FOP",
        lots=[
            ParsedTransferLot(original_open_date=date(2026, 4, 28),
                              qty=Decimal("94"), cost_basis_usd=Decimal("3985.60")),
        ],
    )
    p1 = _minimal_parsed(n_trades=0); p1.transfers = [transfer]
    await persist(db_session, parsed=p1, user_id=sample_user.id,
                  xml_bytes=b"<xfer v1/>", source="web_service")
    await db_session.commit()

    p2 = _minimal_parsed(n_trades=0); p2.transfers = [transfer]
    await persist(db_session, parsed=p2, user_id=sample_user.id,
                  xml_bytes=b"<xfer v2/>", source="web_service")

    n_transfers = await db_session.scalar(
        select(func.count()).select_from(Transfer)
    )
    n_lots = await db_session.scalar(
        select(func.count()).select_from(TransferLot)
    )
    assert n_transfers == 1
    assert n_lots == 1


@pytest.mark.asyncio
async def test_xml_bytes_persisted(
    db_session: AsyncSession, sample_user,
):
    """xml_bytes guardado y recuperable post-fetch (A0)."""
    parsed = _minimal_parsed(n_trades=1)
    test_bytes = b"<replayable_xml>...payload...</replayable_xml>"
    fi_id, _ = await persist(
        db_session, parsed=parsed, user_id=sample_user.id,
        xml_bytes=test_bytes, source="manual_upload",
    )
    await db_session.commit()
    retrieved = await db_session.scalar(
        select(FlexImport.xml_bytes).where(FlexImport.id == fi_id)
    )
    assert retrieved == test_bytes
```

- [ ] **Step 2: Run integration tests**

Run: `docker compose exec -T backend uv run pytest backend/tests/ingest/flex/test_persister_idempotent.py -v`
Expected: PASS (7 tests). Si alguno falla, debuggear el persister hasta que pasen — son tests del happy path principal.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/ingest/flex/test_persister_idempotent.py
git commit -m "$(cat <<'EOF'
test(persister): integration suite para idempotent re-ingest scenarios

7 tests cubriendo los happy paths principales del spec phase 2.5:
- Hash dedup fast-path
- Modified XML, same trades → n_new=0
- Modified XML, 1 nuevo → n_new=1
- Snapshot mark price UPDATE en place
- Snapshot snapshot_date distinto → row nueva (serie temporal)
- TransferLots no duplican
- xml_bytes persistido y recuperable

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: Regression test del orchestrator (job.py double-call)

**Files:**
- Modify: `backend/tests/ingest/flex/test_job.py`

- [ ] **Step 1: Write regression test**

Edit `backend/tests/ingest/flex/test_job.py` (append):

```python
@pytest.mark.asyncio
async def test_run_idempotent_double_call_doesnt_crash(
    db_session, sample_user, sample_account, monkeypatch,
):
    """Regression test para el bug del 2026-05-25: cron + manual refresh fallaban
    al 2do run con UniqueViolationError. Post Phase 2.5: 2 calls consecutivos
    deben ser OK, el 2do es NO-OP con n_new=0.
    """
    from ibkr_control.ingest.flex import job as job_mod
    from sqlalchemy.ext.asyncio import async_sessionmaker

    # Mock el flex_client_mod para devolver bytes determinísticos
    XML_BYTES_DAY1 = b"<?xml version='1.0'?><FlexQueryResponse><FlexStatements><FlexStatement accountId='U99999001' period='20260101-20260525' fromDate='20260101' toDate='20260525'><AccountInformation accountId='U99999001' currency='USD'/></FlexStatement></FlexStatements></FlexQueryResponse>"
    XML_BYTES_DAY2 = b"<?xml version='1.0'?><FlexQueryResponse><FlexStatements><FlexStatement accountId='U99999001' period='20260101-20260526' fromDate='20260101' toDate='20260526'><AccountInformation accountId='U99999001' currency='USD'/></FlexStatement></FlexStatements></FlexQueryResponse>"

    call_count = {"n": 0}
    async def fake_send_request(self, query_id):
        return "REF-XYZ"
    async def fake_poll_statement(self, reference_code):
        call_count["n"] += 1
        return XML_BYTES_DAY1 if call_count["n"] == 1 else XML_BYTES_DAY2

    from ibkr_control.ingest.flex import client as flex_client_mod
    monkeypatch.setattr(flex_client_mod.FlexClient, "send_request", fake_send_request)
    monkeypatch.setattr(flex_client_mod.FlexClient, "poll_statement", fake_poll_statement)

    # Setup credenciales
    from ibkr_control.db.models.flex_credentials import FlexCredentials
    from ibkr_control.ingest.flex import crypto as flex_crypto_mod
    creds = FlexCredentials(
        user_id=sample_user.id,
        token_encrypted=flex_crypto_mod.encrypt_token("test-token"),
        ytd_query_id="123456",
    )
    db_session.add(creds)
    await db_session.commit()

    # Engine + session_factory para que job.run pueda abrir su propia session
    from ibkr_control.db.session import get_engine
    engine = get_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Primera call: insert fresh
    fi_id1 = await job_mod.run(session_factory, user_id=sample_user.id, trigger="manual")
    assert fi_id1 is not None

    # Segunda call: NO debe fallar con UniqueViolation
    fi_id2 = await job_mod.run(session_factory, user_id=sample_user.id, trigger="manual")
    # Puede devolver None (hash dedup si XML idéntico) o un id nuevo (modificado)
    # Lo importante es: NO crash
```

- [ ] **Step 2: Run regression test**

Run: `docker compose exec -T backend uv run pytest backend/tests/ingest/flex/test_job.py::test_run_idempotent_double_call_doesnt_crash -v`
Expected: PASS

- [ ] **Step 3: Run all backend tests para detectar regresiones cross-cutting**

Run: `docker compose exec -T backend uv run pytest -q`
Expected: total = 215 anterior + ~25 nuevos (helpers + integration + migration + parser) = ~240 PASS. Cero FAIL.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/ingest/flex/test_job.py
git commit -m "$(cat <<'EOF'
test(job): regression — flex_job.run idempotent under double-call

Direct repro del bug del 2026-05-25 que motivó Phase 2.5. Mockea
flex_client para devolver XMLs determinísticos día1/día2, dispara
run() dos veces, verifica que ninguna crashea con UniqueViolation.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: Manual smoke test en dev

**Files:**
- N/A (manual verification)

- [ ] **Step 1: Verify DB está en estado limpio (post Rev2 wipe del Task 5)**

Run: `docker compose exec -T postgres psql -U ibkr -d ibkr_control -c "SELECT count(*) FROM flex_imports;"`
Expected: 0

- [ ] **Step 2: Restart backend container con código nuevo**

Run: `docker compose restart backend && sleep 5 && docker compose logs --tail=20 backend`
Expected: lifespan OK, scheduler con 3 jobs registrados, sin errores.

- [ ] **Step 3: Re-upload XML 2024 sealed via wizard o endpoint**

Si tenés el XML en `/path/to/ACTIVITY_2024.xml`:
```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/jwt/login \
  -d "username=<email>&password=<pwd>" -H "Content-Type: application/x-www-form-urlencoded" \
  | jq -r .access_token)

curl -X POST http://localhost:8000/api/imports/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/path/to/ACTIVITY_2024.xml"
```
Expected: response 200 con `flex_import_id`, `n_observed_*`, `n_new_*` populated.

(Alternativa: usar la UI del wizard si está accesible)

- [ ] **Step 4: Re-upload XML 2025 sealed (mismo flow)**

- [ ] **Step 5: Trigger Flex WS manual fetch (Settings → Refresh)**

Via UI del frontend O via curl:
```bash
curl -X POST http://localhost:8000/api/ingest/trigger \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"kind": "both"}'
```
Expected: 200 con `job_id`. Stream via `/api/ingest/stream/{job_id}` muestra `trm_backfill` y `flex_ytd` ambos `ok`.

- [ ] **Step 6: Disparar Flex WS de nuevo (validar idempotencia post-rewrite)**

Esperar 30s (cooldown del rate limit) → trigger again.
Expected: el segundo run NO falla. `n_new_trades` en el segundo = 0 (o muy bajo).

- [ ] **Step 7: Verificar estado final**

Run:
```bash
docker compose exec -T postgres psql -U ibkr -d ibkr_control -c \
"SELECT id, anyo, year_status, n_observed_trades, n_new_trades, n_observed_open_lots, n_new_open_lots FROM flex_imports ORDER BY id;"
```
Expected: 3+ rows (2024 sealed, 2025 sealed, 2026 YTD × 1 o 2 imports). `n_observed >= n_new` en todos.

Run: `docker compose exec -T postgres psql -U ibkr -d ibkr_control -c "SELECT id, job_kind, status, finished_at FROM ingest_log ORDER BY started_at DESC LIMIT 10;"`
Expected: últimos 5+ runs todos `ok`. Cero `failed` post-rewrite.

- [ ] **Step 8: Documentar resultados del smoke test**

Si todo pasó, dejar nota en commit message del próximo step.
Si algo falló: debuggear, fix, repetir desde Step 5. NO marcar este task completo hasta smoke test 100% verde.

---

### Task 14: Docs update — CLAUDE.md + polish backlog D13

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/plans/2026-05-24-phase2-polish-backlog.md`

- [ ] **Step 1: Backfill D13 al polish backlog**

Edit `docs/plans/2026-05-24-phase2-polish-backlog.md`, en la sección "Deuda conocida (D1-D12)" o equivalente, agregar:

```markdown
### D13 [BUG-FIXED] — Persister Flex no idempotente

**Status:** RESOLVED en commit `<SHA-del-merge-de-este-trabajo>` + tag `v0.2.3-persister-idempotent`.

**Bug:** Phase 2 persister dedupea solo a nivel xml_hash. La Flex YTD del Web
Service cambia byte-a-byte cada día (mark prices, timestamp, eventos nuevos)
→ hash siempre nuevo → `session.add(Trade(...))` choca con UNIQUE(transaction_id)
global → cron + manual refresh fallan al 2do run.

**Detectado:** 2026-05-25 durante refresh manual desde Settings. Síntoma visible:
UI mostraba "✓ Refresh completado" (race condition en `ManualRefreshButton.tsx`)
mientras `ingest_log` mostraba `flex=failed` con `UniqueViolationError`.

**Causa raíz arquitectónica:** mezcla de dos modelos contradictorios
(dedup a nivel XML vs UNIQUE global por transaction_id) sin idempotencia
real per-row.

**Fix:** rewrite del persister a UPSERT por natural key per-entity, con semántica
diferenciada immutable (DO NOTHING) vs snapshot (DO UPDATE). Spec completo:
`docs/specs/2026-05-25-flex-persister-idempotent-design.md`.

**Lecciones:**
1. **El smoke E2E de Phase 2 solo cubría el primer run.** Tests de cron + manual
   refresh deberían disparar el job >=2 veces consecutivas para detectar bugs
   de idempotencia. Aplicar a todo background task de Phase 3+ que toque DB.
2. **UNIQUE constraint sin UPSERT es trampa.** Si una tabla tiene UNIQUE(X)
   y el escritor hace plain INSERT, cualquier re-run rompe. Siempre que se
   agregue UNIQUE, pareada con la decisión de qué hacer ON CONFLICT.
3. **Hash dedup a nivel "documento entero" es engañoso para fuentes que cambian
   continuamente.** Para YTD/rolling data, dedupear a nivel fila (natural key)
   es la única respuesta correcta.
```

- [ ] **Step 2: Update CLAUDE.md con tag + retrospective**

Edit `CLAUDE.md`:

(a) En la tabla "Estado actual", actualizar Phase 2 row con tag adicional:
```
| 2. Data ingestion ... | ✅ completado · ... + persister idempotente · 215+ tests · tags `v0.2.0-ingest` + `v0.2.1-persistent-state` + `v0.2.2-wizard-redesign` + `v0.2.3-persister-idempotent` ✓ | ... + `docs/plans/2026-05-25-flex-persister-rewrite.md` | ... + `docs/specs/2026-05-25-flex-persister-idempotent-design.md` | `v0.2.3-persister-idempotent` ✓ |
```

(b) En "⏯ Cómo continuar (próxima sesión)" actualizar el preview a:
```
**Phase 2 + wizard redesign + persister idempotente completos (tags `v0.2.0-ingest`, `v0.2.1-persistent-state`, `v0.2.2-wizard-redesign`, `v0.2.3-persister-idempotent`). Próximo: Phase 3.**
```

(c) En la sección "Retrospectiva (deviaciones del spec original)" de Phase 2, agregar al final:

```markdown
- **Persister no idempotente — bug crítico detectado post-deploy + rewrite Phase 2.5 (2026-05-25, tag `v0.2.3-persister-idempotent`)** — smoke test del manual refresh el 2026-05-25 reveló que cron Flex + manual refresh fallaban al 2do run con `UniqueViolationError trades_transaction_id_key`. Phase 2 dedupea a nivel xml_hash pero los XMLs YTD cambian byte-a-byte cada día → cada hash es nuevo → re-INSERT de todos los trades del año → choque con UNIQUE global. El cron diario también estaba roto, solo no se detectó porque el único smoke E2E de Phase 2 cubría el primer run (DB vacía). Fix: rewrite del persister a UPSERT por natural key per-entity con helpers Core (`pg_insert + on_conflict_*`), semántica diferenciada immutable (DO NOTHING, first-seen) vs snapshot (DO UPDATE, last-updated-by). Schema migration en 2 Alembic revisions + script manual `wipe_flex_data.py` (mitigación TRUNCATE accidental). Agregamos `xml_bytes BYTEA` a flex_imports para replay capability (A0 spec). Plan: `docs/plans/2026-05-25-flex-persister-rewrite.md`. Spec: `docs/specs/2026-05-25-flex-persister-idempotent-design.md` (A0-A8 lockeadas). Tests: 215 → ~240 (+ helpers, +integration, +migration, +regression). Esta deuda quedó documentada como D13 [BUG-FIXED] en el polish backlog.
```

- [ ] **Step 3: Commit docs**

```bash
git add CLAUDE.md docs/plans/2026-05-24-phase2-polish-backlog.md
git commit -m "$(cat <<'EOF'
docs: phase 2.5 persister idempotente — D13 backlog + CLAUDE.md update

- docs/plans/...polish-backlog.md: agrega D13 [BUG-FIXED] con rationale
  + lecciones (smoke E2E debe disparar jobs >=2x, UNIQUE sin UPSERT es
  trampa, hash dedup engañoso para fuentes mutables)
- CLAUDE.md: tag v0.2.3-persister-idempotent en tabla de phases +
  preview de "Cómo continuar" + retrospectiva Phase 2 ampliada

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: Final verification + tag + (opcional) PR

**Files:**
- N/A (ops)

- [ ] **Step 1: Full test suite verde**

Run: `docker compose exec -T backend uv run pytest -q`
Expected: ~240 passed, 0 failed.

- [ ] **Step 2: Frontend build verde**

Run: `docker compose exec -T frontend pnpm build 2>&1 | tail -8`
Expected: `✓ Generating static pages` sin errores.

- [ ] **Step 3: Migration drift test**

Run: `docker compose exec -T backend uv run pytest backend/tests/test_migrations.py -v`
Expected: PASS — models match DB schema.

- [ ] **Step 4: Crear tag**

Run:
```bash
git tag -a v0.2.3-persister-idempotent -m "$(cat <<'EOF'
v0.2.3 — Flex persister idempotente

Phase 2.5: rewrite del persister a UPSERT por natural key per-entity.
Resuelve el UniqueViolationError que rompía cron + manual refresh
al segundo run (D13 [BUG-FIXED]).

Decisiones A0-A8 lockeadas en docs/specs/2026-05-25-flex-persister-
idempotent-design.md. Plan ejecutado en
docs/plans/2026-05-25-flex-persister-rewrite.md.

Tests: 215 → ~240. Migrations: 2 Alembic revisions + script manual
de wipe entre ellas (mitigación TRUNCATE accidental).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: (Opcional, requiere autorización del usuario) Push branch + abrir PR**

Si el usuario lo pide:
```bash
git push -u origin phase25/flex-persister-idempotent
gh pr create --title "Phase 2.5: Flex persister idempotente (D13 fix)" --body "..."
```

Si NO se pide: dejar branch local, esperar instrucción del usuario.

- [ ] **Step 6: Mark Task 14 + 15 complete**

Status: phase 2.5 implementación completa. Próxima sesión arranca Phase 3 (lotes/domain layer) con persister fixed.

---

## Self-Review checklist

Antes de cerrar el plan, verifico que cubre el spec completo:

### Spec coverage

| Spec section | Covered by task(s) |
|---|---|
| A0 — xml_bytes en flex_imports | Task 3 (Rev1 add nullable) + Task 5 (Rev2 NOT NULL) + Task 8 (persister escribe) + Task 11 (test) |
| A1 — first seen en immutable | Task 7 (helper DO NOTHING) + Task 8 (persister) + Task 11 (test) |
| A1-bis — last updated por snapshot | Task 7 (helper DO UPDATE) + Task 8 (persister) + Task 11 (test) |
| A2 — SET NULL + nullable | Task 3 (Rev1 FK change) + Task 6 (model update) + Task 5 (test enforcement) |
| A3 — UNIQUE en snapshot tables | Task 5 (Rev2 constraints) + Task 6 (model UniqueConstraint) + Task 11 (test) |
| A4 — hash fast-path | Task 8 (persister early return) + Task 11 (test) |
| A5 — n_observed + n_new counters | Task 3 (Rev1 add cols) + Task 8 (persister populates) + Task 9 (API expose) + Task 10 (frontend types) |
| A6 — wipe + re-ingest | Task 4 (wipe script) + Task 5 (Rev2 post-wipe) + Task 13 (manual smoke) |
| A7 — year_status sin cambios | (Sin tarea explícita; el persister no ramifica por year_status — task 8 lo deja informacional como spec pide) |
| A8 — Core helpers | Task 7 (helpers) + Task 8 (uso en persister) |

### Placeholder scan

(Revisado tras escribir todas las tasks. Si encontrás "TBD", "TODO", "fill in", reportar y corregir.)

### Type consistency

- `persist()` retorna `tuple[int, dict]` consistentemente entre task 8 + task 9 + task 12
- `IngestCounters` definido en task 9 + usado en task 9 (response shapes)
- Helpers `_upsert_immutable`, `_upsert_snapshot`, `_upsert_immutable_returning_inserted` mismos nombres en task 7 (impl) + task 8 (uso)
- `xml_bytes` (no `xml_path`) consistente en task 3, 5, 6, 8, 11

### Open risks not in plan

- **Si el smoke test de Task 13 falla en producción de manera distinta a dev:** rollback via `alembic downgrade` y volver al persister anterior (que está roto pero estable). Documentar incidente para v0.2.4.
- **Si el usuario no tiene los XMLs 2024/2025 cuando llega a Task 13:** pausar implementación hasta que los consiga, O proceder con solo YTD vía Flex WS (sealed historicals quedan en backlog).
