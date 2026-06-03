# Lot `asset_class` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store `asset_class` directly on `closed_lots` and `open_position_lots` (captured from the XML `assetCategory`), so the fiscal-regime discriminator is a first-class non-null fact instead of being inferred through the nullable `closed_lots.source_trade_id` join (already broken for FOP-acquired lots).

**Architecture:** Capture the raw `assetCategory` at parse time into the `ParsedClosedLot`/`ParsedOpenPositionLot` dataclasses (fail-loud if absent), add a `NOT NULL` `asset_class` column to both lot tables, and map it through the persister. `source_trade_id` stays for lineage only. Fiscal-regime derivation remains Phase 3 domain work. Rollout is wipe & reload (dev data is disposable, pre-deploy) — no backfill script.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x async, Alembic, lxml, pytest + testcontainers-postgres.

**Spec:** `docs/specs/2026-06-02-lot-asset-class-design.md` (D1–D5).

**Branch:** `fix/lot-asset-class` (already created off `main`; spec committed at `b385a98`). Stay on this branch — do not create new branches.

---

## File Structure

- `backend/src/ibkr_control/ingest/flex/_models.py` — add `asset_class: str` to `ParsedClosedLot` and `ParsedOpenPositionLot` (Task 1).
- `backend/src/ibkr_control/ingest/flex/parser.py` — add `_require_asset_class()` helper + wire into 3 lot-construction sites (Task 1).
- `backend/tests/ingest/flex/test_persister_idempotent.py` — add `asset_class="STK"` to existing direct `ParsedOpenPositionLot` constructions so the file stays green (Task 1).
- `backend/tests/ingest/flex/test_parser.py` — new parser tests (Task 1).
- `backend/src/ibkr_control/db/models/flex_raw.py` — add `asset_class` column to `ClosedLot` and `OpenPositionLot` (Task 2).
- `backend/alembic/versions/<rev>_add_lot_asset_class.py` — new migration (Task 2).
- `backend/src/ibkr_control/ingest/flex/persister.py` — map `asset_class` into `closed_rows` + `open_rows` insert dicts + open snapshot update_cols (Task 2).
- `backend/tests/ingest/flex/test_persister.py` — persister persistence test + FOP regression test (Task 2, Task 3).

All commands run from `backend/` (testcontainers run from the host, not inside the backend container):
```bash
cd /Users/owner/Development/ibkr-control/backend
```

---

### Task 1: Parser captures `asset_class` (fail-loud)

**Files:**
- Modify: `backend/src/ibkr_control/ingest/flex/_models.py`
- Modify: `backend/src/ibkr_control/ingest/flex/parser.py`
- Modify: `backend/tests/ingest/flex/test_persister_idempotent.py`
- Test: `backend/tests/ingest/flex/test_parser.py`

- [ ] **Step 1: Add `asset_class` to the parsed dataclasses**

In `backend/src/ibkr_control/ingest/flex/_models.py`, add `asset_class: str` right after `symbol` in both dataclasses.

`ParsedClosedLot`:
```python
@dataclass
class ParsedClosedLot:
    ibkr_account_id: str
    symbol: str
    asset_class: str  # XML assetCategory (STK/FUT/OPT...); raw fact, fiscal regime derived in Phase 3 domain
    open_date: date
    close_date: date
    close_datetime: (
        datetime  # A3 amendment #3: per-execution timestamp, discriminator for natural key
    )
    qty: Decimal
    cost_basis_usd: Decimal
    proceeds_usd: Decimal
    fifo_pnl_usd: Decimal
    transaction_id: str | None  # Links to Trade via raw XML transactionID; None if not captured
```

`ParsedOpenPositionLot`:
```python
@dataclass
class ParsedOpenPositionLot:
    ibkr_account_id: str
    symbol: str
    asset_class: str  # XML assetCategory; raw fact, fiscal regime derived in Phase 3 domain
    open_date: date
    qty: Decimal
    cost_basis_usd: Decimal
    mark_price_usd: Decimal | None
    mark_value_usd: Decimal | None
    snapshot_date: date
    originating_transaction_id: str
```

- [ ] **Step 2: Add the fail-loud helper and wire it into the 3 lot-construction sites**

In `backend/src/ibkr_control/ingest/flex/parser.py`, add this helper just above `_parse_lot_as_closed_lot` (line ~286):

```python
def _require_asset_class(elem, context: str) -> str:
    """Extract assetCategory, failing loud if absent.

    asset_class is the fiscal-regime discriminator (STK -> Art.288 + 730d;
    FUT/OPT -> Decreto 1797). It must never be silently empty. Verified 100%
    present on real <Lot CLOSED_LOT>/<OpenPosition> rows; this guard catches
    future drift (consistent with the _known_tags fail-loud philosophy).
    """
    asset_class = elem.get("assetCategory")
    if not asset_class:
        raise ValueError(f"{context} missing required assetCategory attribute")
    return asset_class
```

In `_parse_lot_as_closed_lot`, add to the `ParsedClosedLot(...)` return (after `symbol=...`, line ~319):
```python
        symbol=elem.get("symbol") or "",
        asset_class=_require_asset_class(elem, "<Lot CLOSED_LOT>"),
        open_date=open_date,
```

In `_parse_closed_lots_wrapper`, add to the `ParsedClosedLot(...)` append (after `symbol=...`, line ~355):
```python
                symbol=lot.get("symbol") or "",
                asset_class=_require_asset_class(lot, "<ClosedLot>"),
                open_date=open_date,
```

In `_parse_open_positions`, add to the `ParsedOpenPositionLot(...)` append (after `symbol=...`, line ~386):
```python
                symbol=pos.get("symbol") or "",
                asset_class=_require_asset_class(pos, "<OpenPosition>"),
                open_date=open_date,
```

- [ ] **Step 3: Keep `test_persister_idempotent.py` green (existing direct constructions)**

In `backend/tests/ingest/flex/test_persister_idempotent.py`, add `asset_class="STK"` to the two `base_args` dicts that feed `ParsedOpenPositionLot(...)` (the dict ending around line 150 with `originating_transaction_id="OTID-LOT-1"`, and the dict at line ~201 with `originating_transaction_id="OTID-LOT-A"`). Add the key next to `symbol="MSFT"`:
```python
    base_args = dict(
        ibkr_account_id=sample_account.ibkr_account_id,
        symbol="MSFT",
        asset_class="STK",
        open_date=date(2026, 1, 15),
        ...
    )
```

- [ ] **Step 4: Write the failing parser tests**

Append to `backend/tests/ingest/flex/test_parser.py`:
```python
def test_parse_closed_lots_carry_asset_class():
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    parsed = parse(xml)
    assert parsed.closed_lots, "fixture should yield closed lots"
    classes = {cl.asset_class for cl in parsed.closed_lots}
    # 2025 fixture has both equities and the MES future as CLOSED_LOT rows
    assert "STK" in classes
    assert "FUT" in classes
    assert all(cl.asset_class for cl in parsed.closed_lots)  # never empty


def test_parse_open_lots_carry_asset_class():
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    parsed = parse(xml)
    assert parsed.open_position_lots, "fixture should yield open lots"
    assert all(op.asset_class == "STK" for op in parsed.open_position_lots)


def test_require_asset_class_fails_loud_when_missing():
    from lxml import etree

    from ibkr_control.ingest.flex.parser import _require_asset_class

    elem = etree.fromstring(b'<Lot symbol="GLOB" quantity="10"/>')
    with pytest.raises(ValueError, match="assetCategory"):
        _require_asset_class(elem, "<Lot CLOSED_LOT>")
```

Verify the file already imports `parse`, `FIXTURE_DIR`, and `pytest`; if `FIXTURE_DIR` is named differently in this file, reuse the existing fixture-path constant. (`pytest` and `parse` are already imported in `test_parser.py`.)

- [ ] **Step 5: Run the new parser tests**

Run: `uv run pytest tests/ingest/flex/test_parser.py -k "asset_class" -v`
Expected: PASS (3 tests). Note: because the dataclass field (Step 1) and its parser population (Step 2) are coupled, these go green together rather than through a separate red phase — the fail-loud test (`test_require_asset_class_fails_loud_when_missing`) is the one that meaningfully exercises a failure path. If any assertion fails, the Step 2 wiring is incomplete — fix it before continuing.

- [ ] **Step 6: Run the full parser + idempotent suite to confirm green**

Run: `uv run pytest tests/ingest/flex/test_parser.py tests/ingest/flex/test_persister_idempotent.py -q`
Expected: PASS (all).

- [ ] **Step 7: Commit**

```bash
git add src/ibkr_control/ingest/flex/_models.py src/ibkr_control/ingest/flex/parser.py tests/ingest/flex/test_parser.py tests/ingest/flex/test_persister_idempotent.py
git commit -m "feat(parser): capture asset_class from XML on closed/open lots (fail-loud)"
```

---

### Task 2: Persist `asset_class` on both lot tables + migration

**Files:**
- Modify: `backend/src/ibkr_control/db/models/flex_raw.py`
- Modify: `backend/src/ibkr_control/ingest/flex/persister.py`
- Create: `backend/alembic/versions/<rev>_add_lot_asset_class.py`
- Test: `backend/tests/ingest/flex/test_persister.py`, `backend/tests/test_migrations.py`

- [ ] **Step 1: Write the failing persister persistence test**

Append to `backend/tests/ingest/flex/test_persister.py`:
```python
async def test_persist_stores_asset_class_on_both_lot_tables(db_session: AsyncSession, sample_user):
    from sqlalchemy import distinct, select

    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    parsed = parse(xml)
    await persist(db_session, parsed=parsed, user_id=sample_user.id, xml_bytes=xml, source="manual_upload")
    await db_session.commit()

    closed_classes = set(
        (await db_session.execute(select(distinct(ClosedLot.asset_class)))).scalars().all()
    )
    open_classes = set(
        (await db_session.execute(select(distinct(OpenPositionLot.asset_class)))).scalars().all()
    )
    assert "STK" in closed_classes and "FUT" in closed_classes
    assert open_classes == {"STK"}
    # no NULLs slipped through
    n_null = await db_session.scalar(
        select(func.count()).select_from(ClosedLot).where(ClosedLot.asset_class.is_(None))
    )
    assert n_null == 0
```
Confirm `ClosedLot`, `OpenPositionLot`, `func`, `parse`, `FIXTURE_DIR`, `persist` are imported at the top of the file (they are, per existing tests).

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/ingest/flex/test_persister.py::test_persist_stores_asset_class_on_both_lot_tables -v`
Expected: FAIL — `AttributeError: type object 'ClosedLot' has no attribute 'asset_class'`.

- [ ] **Step 3: Add the `asset_class` column to both models**

In `backend/src/ibkr_control/db/models/flex_raw.py`, in `ClosedLot`, add after the `symbol` column (line ~139):
```python
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    asset_class: Mapped[str] = mapped_column(String, nullable=False)
```
In `OpenPositionLot`, add after its `symbol` column the same line:
```python
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    asset_class: Mapped[str] = mapped_column(String, nullable=False)
```
No CHECK, no comment — identical to `trades.asset_class` (D3).

- [ ] **Step 4: Map `asset_class` through the persister**

In `backend/src/ibkr_control/ingest/flex/persister.py`, in the `closed_rows` list comprehension (line ~249), add after `"symbol": cl.symbol,`:
```python
            "symbol": cl.symbol,
            "asset_class": cl.asset_class,
```
In the `open_rows` list comprehension (line ~361), add after `"symbol": op_lot.symbol,`:
```python
            "symbol": op_lot.symbol,
            "asset_class": op_lot.asset_class,
```
In the `_upsert_snapshot` call for open positions, add `"asset_class"` to the update-columns list (line ~382–388), so a re-import refreshes it (snapshot last-write-wins; it is stable per symbol so this is a no-op in practice):
```python
        [
            "flex_import_id",
            "qty",
            "cost_basis_usd",
            "mark_price_usd",
            "mark_value_usd",
            "asset_class",
        ],
```
(`closed_rows` uses an immutable DO NOTHING upsert — no update list to touch.)

- [ ] **Step 5: Run the persister test to verify it passes**

Run: `uv run pytest tests/ingest/flex/test_persister.py::test_persist_stores_asset_class_on_both_lot_tables -v`
Expected: PASS.

- [ ] **Step 6: Generate the migration and verify it matches the models**

Run:
```bash
uv run alembic revision --autogenerate -m "add asset_class to closed_lots and open_position_lots"
```
Open the generated file in `backend/alembic/versions/`. It must contain exactly two `add_column` ops (and their `drop_column` inverses) and nothing else. Confirm `down_revision = "cbeaac94933d"`. Edit so `upgrade()` / `downgrade()` read exactly:
```python
def upgrade() -> None:
    op.add_column("closed_lots", sa.Column("asset_class", sa.String(), nullable=False))
    op.add_column("open_position_lots", sa.Column("asset_class", sa.String(), nullable=False))


def downgrade() -> None:
    op.drop_column("open_position_lots", "asset_class")
    op.drop_column("closed_lots", "asset_class")
```
If autogenerate emitted any unrelated op (spurious index/constraint rename), delete it — the Phase 2.8 naming convention should produce a clean diff.

- [ ] **Step 7: Run the migration drift test to confirm schema parity**

Run: `uv run pytest tests/test_migrations.py -v`
Expected: PASS — `alembic upgrade head` against a fresh container matches `Base.metadata` (Phase 2.8 hardened `compare_metadata`). A NOT NULL `add_column` against the empty fresh DB succeeds.

- [ ] **Step 8: Commit**

```bash
git add src/ibkr_control/db/models/flex_raw.py src/ibkr_control/ingest/flex/persister.py alembic/versions/*_add_asset_class*.py tests/ingest/flex/test_persister.py
git commit -m "feat(lots): persist asset_class on closed_lots + open_position_lots + migration"
```

---

### Task 3: FOP regression — `asset_class` survives a NULL `source_trade_id`

**Files:**
- Test: `backend/tests/ingest/flex/test_persister.py`

This is the bug that motivated the change: FOP-acquired lots (e.g. the Globant RSU GLOB lots) have no opening BUY trade, so `transaction_id` is `None` and `source_trade_id` resolves to NULL. The lot must still get its `asset_class` from the XML, not from the (absent) trade.

- [ ] **Step 1: Write the failing regression test**

Append to `backend/tests/ingest/flex/test_persister.py`:
```python
async def test_fop_closed_lot_gets_asset_class_without_source_trade(db_session: AsyncSession, sample_user):
    """A FOP-acquired closed lot (no opening trade -> transaction_id None ->
    source_trade_id NULL) must still carry asset_class from the XML."""
    from datetime import date, datetime
    from decimal import Decimal

    from sqlalchemy import select

    from ibkr_control.ingest.flex._models import ParsedClosedLot

    parsed = _minimal_parsed()  # helper already used in this file; transfers/lots empty
    parsed.closed_lots = [
        ParsedClosedLot(
            ibkr_account_id="U99999002",
            symbol="GLOB",
            asset_class="STK",
            open_date=date(2026, 4, 28),
            close_date=date(2026, 5, 1),
            close_datetime=datetime(2026, 5, 1, 10, 0, 0),
            qty=Decimal("74"),
            cost_basis_usd=Decimal("3137.60"),
            proceeds_usd=Decimal("3141.0924"),
            fifo_pnl_usd=Decimal("3.4924"),
            transaction_id=None,  # FOP: no opening BUY trade to link
        )
    ]
    await persist(db_session, parsed=parsed, user_id=sample_user.id, xml_bytes=b"<fop/>", source="manual_upload")
    await db_session.commit()

    row = (
        await db_session.execute(
            select(ClosedLot.asset_class, ClosedLot.source_trade_id).where(ClosedLot.symbol == "GLOB")
        )
    ).first()
    assert row is not None
    assert row[0] == "STK"          # asset_class captured from the XML
    assert row[1] is None           # source_trade_id NULL — the old inference path would have failed
```
Check the exact name of the minimal-parsed helper in this file (it builds a `ParsedFlexStatement` with empty lists — used by `test_persist_*` tests, e.g. the one near line 44). If it takes required args, pass the same ones the neighboring tests pass. If no such helper exists, build the `ParsedFlexStatement` inline the same way the nearest existing test does.

- [ ] **Step 2: Run it to verify it passes (behavior already implemented in Task 2)**

Run: `uv run pytest tests/ingest/flex/test_persister.py::test_fop_closed_lot_gets_asset_class_without_source_trade -v`
Expected: PASS — Task 2 already wired `asset_class`; this test locks the FOP regression specifically. (If it fails because the helper signature differs, fix the construction, not the assertions.)

- [ ] **Step 3: Run the entire backend suite + lint**

Run:
```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```
Expected: all tests PASS (304 baseline + new tests), ruff clean, 0 format drift.

- [ ] **Step 4: Commit**

```bash
git add tests/ingest/flex/test_persister.py
git commit -m "test(lots): regression — FOP closed lot keeps asset_class with NULL source_trade_id"
```

---

### Task 4: Rollout & verification (operational — not TDD)

**This task reloads real data; the user performs the XML re-upload via the wizard.**

- [ ] **Step 1: Wipe the dev flex data**

Run: `docker compose exec backend uv run python -m scripts.wipe_flex_data`
Expected: flex data tables emptied (credentials + apscheduler_jobs preserved, per the script).

- [ ] **Step 2: Apply the migration to the dev DB**

Run: `docker compose exec backend uv run alembic upgrade head`
Expected: revision `<rev>_add_lot_asset_class` applied; `alembic current` shows the new head.

- [ ] **Step 3: User re-imports the three XMLs via the wizard**

Hand off to the user: open `http://localhost:3000`, run the setup wizard, load `ACTIVITY_2024.xml`, `ACTIVITY_2025.xml`, and fetch 2026 YTD (or upload the 2026 XML). Same flow as before.

- [ ] **Step 4: Verify against real data**

Run:
```bash
docker compose exec -T postgres psql -U ibkr -d ibkr_control -c "
SELECT 'closed_lots' t, asset_class, count(*) FROM closed_lots GROUP BY 1,2
UNION ALL SELECT 'open_position_lots', asset_class, count(*) FROM open_position_lots GROUP BY 1,2
ORDER BY 1,2;"
docker compose exec -T postgres psql -U ibkr -d ibkr_control -c "
SELECT symbol, asset_class, source_trade_id FROM closed_lots WHERE symbol='GLOB';"
```
Expected: no NULL `asset_class`; the GLOB closed lots show `asset_class='STK'` with `source_trade_id` NULL; the MES future closed lot shows `asset_class='FUT'`.

- [ ] **Step 5: Update project docs + finish the branch**

- Update `CLAUDE.md` "⏯ Cómo continuar" + "Estado actual" with a short retrospective entry for this cleanup.
- Use `superpowers:finishing-a-development-branch` to merge `fix/lot-asset-class` into `main` and tag if appropriate.

---

## Self-Review

**Spec coverage:** D1 (both tables) → Task 2 Step 3. D2 (raw asset_class) → Task 1 Step 1. D3 (String NOT NULL, no CHECK, mirror trades) → Task 2 Step 3. D4 (new migration + wipe & reload, no backfill) → Task 2 Step 6 + Task 4. D5 (fail-loud parser) → Task 1 Step 2 + test in Step 4. FOP regression (problem statement) → Task 3. Drift test → Task 2 Step 7. Full suite green → Task 3 Step 3.

**Placeholder scan:** `<rev>` is the alembic-generated revision id (unknowable until `alembic revision` runs in Task 2 Step 6) — not a plan placeholder. No TBD/TODO/"handle edge cases" remain.

**Type consistency:** `asset_class: str` on both dataclasses (Task 1) ↔ `asset_class` kwarg in parser constructions (Task 1) ↔ `"asset_class"` insert-dict keys (Task 2) ↔ `asset_class` column on both models (Task 2) ↔ asserted in tests (Tasks 1–3). `_require_asset_class(elem, context)` signature consistent across all 3 call sites.
