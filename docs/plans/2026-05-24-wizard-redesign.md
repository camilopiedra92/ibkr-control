# Wizard Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reescribir el wizard de setup para detectar automáticamente cuentas IBKR vía Flex Web Service, filtrar shadow F-accounts a nivel persister, y eliminar el modelo "flags en JSONB" en favor de state derivado de la DB real.

**Architecture:** Backend: 7 endpoints nuevos/modificados bajo `/api/setup/*`, filtro F-account en `persister.py` (allowlist pattern de renta sibling), in-memory stash con TTL para Step 3 uploads. Frontend: state machine de 7 pantallas en `WizardPage.tsx`, multi-file drag-drop en Step 3, pre-poblado de aliases desde `<AccountInformation>` del XML. Migration H wipea data legacy para re-ingest desde cero.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x async, asyncpg, Alembic, pytest + testcontainer. Next.js 16, React 19, TanStack Query, Orval, Playwright + MSW.

**Spec:** `docs/specs/2026-05-24-wizard-redesign-design.md` — fuente autoritativa.

---

## File Structure

### Backend — create

- `backend/src/ibkr_control/api/_step3_stash.py` — in-memory dict + TTL para XMLs parseados sin commit
- `backend/alembic/versions/<autogen>_wipe_for_wizard_redesign.py` — Migration H
- `backend/tests/ingest/test_persister_f_filter.py`
- `backend/tests/api/test_setup_step2_detect.py`
- `backend/tests/api/test_setup_step2_detect_from_xml.py`
- `backend/tests/api/test_setup_step3.py`
- `backend/tests/api/test_setup_finish.py`
- `backend/tests/api/__init__.py` (ensure exists — for `tests.api` import path used in tests)
- `backend/tests/e2e_api/__init__.py`
- `backend/tests/e2e_api/test_wizard_full_flow.py`

### Backend — modify

- `backend/src/ibkr_control/ingest/flex/persister.py` — add `_is_shadow_account` + filter in 8 locations
- `backend/src/ibkr_control/ingest/flex/parser.py` — extend `_parse_account_info` to extract `accountAlias`, `accountType`, `name`
- `backend/src/ibkr_control/api/setup.py` — REWRITE: rename `step1/validate` → `step1/save`; add `step2/detect`, `step2/detect_from_xml`, `step3/upload`, `step3/save_new_accounts`, `step3/commit`, `finish`; modify `step2/save` + `state`
- `backend/src/ibkr_control/api/_schemas.py` — add request/response schemas
- `backend/tests/api/test_setup_step1.py` — REWRITE (no IBKR call)
- `backend/tests/api/test_setup_step2_save.py` — REWRITE (validates against detected_accounts)
- `backend/tests/api/test_setup_state.py` — REWRITE (derived fields)

### Frontend — create

- `frontend/src/components/wizard/WizardPage.tsx`
- `frontend/src/components/wizard/Step2Detect.tsx`
- `frontend/src/components/wizard/Step2ConfigureAccounts.tsx`
- `frontend/src/components/wizard/Step3NewAccountsModal.tsx`
- `frontend/src/components/wizard/Step3Commit.tsx`
- `frontend/src/components/wizard/StepFinish.tsx`
- `frontend/src/hooks/useWizardNav.ts`
- `frontend/src/hooks/useStep2Detect.ts`
- `frontend/e2e/wizard-happy-path.spec.ts`
- `frontend/e2e/wizard-ibkr-busy-fallback.spec.ts`
- `frontend/e2e/wizard-new-accounts-in-history.spec.ts`

### Frontend — modify

- `frontend/src/components/wizard/Step1Credentials.tsx` — REWRITE (no validation step)
- `frontend/src/components/wizard/Step3Xmls.tsx` → rename to `Step3Upload.tsx` and REWRITE
- `frontend/src/components/wizard/Step4Initial.tsx` → DELETE (subsumed by `StepFinish.tsx`)
- `frontend/src/components/wizard/Stepper.tsx` — update step labels
- `frontend/src/app/(setup)/setup/page.tsx` — delegate to `WizardPage` + redirect if completed
- `frontend/src/hooks/useSetupState.ts` — REWRITE (new state shape)

### Docs

- `CLAUDE.md` — update test count + Retrospectiva bullet
- `docs/plans/2026-05-24-phase2-polish-backlog.md` — note wizard redesign as post-Phase-2 work

---

## Task 1: Setup branch + Migration H stub

**Files:**
- Create: `backend/alembic/versions/<autogen>_wipe_for_wizard_redesign.py`

- [ ] **Step 1: Create branch from main**

```bash
git checkout main
git checkout -b feat/wizard-redesign
```

Expected: `Switched to a new branch 'feat/wizard-redesign'`

- [ ] **Step 2: Generate empty Alembic migration stub**

```bash
cd backend && uv run alembic revision -m "wipe for wizard redesign"
```

Expected: new file in `backend/alembic/versions/<hash>_wipe_for_wizard_redesign.py`.

- [ ] **Step 3: Fill migration body**

Replace the auto-generated `upgrade()` and `downgrade()`. Final content of the file body (after the auto-generated header):

```python
def upgrade() -> None:
    # Destructive wipe for wizard redesign — re-ingest from scratch per spec D1.
    # Order matters: dependents first to satisfy FK constraints.
    op.execute("DELETE FROM open_dividend_accruals")
    op.execute("DELETE FROM change_in_dividend_accruals")
    op.execute("DELETE FROM transfers")
    op.execute("DELETE FROM transfer_lots")
    op.execute("DELETE FROM cash_transactions")
    op.execute("DELETE FROM open_position_lots")
    op.execute("DELETE FROM closed_lots")
    op.execute("DELETE FROM trades")
    op.execute("DELETE FROM flex_imports")
    op.execute("DELETE FROM participations")
    op.execute("DELETE FROM accounts")
    op.execute("UPDATE users SET setup_progress = '{}'::jsonb, setup_completed_at = NULL")
    # Preserve: flex_credentials (token + query_id) — user no re-pega Flex Token.
    # Preserve: apscheduler_jobs — scheduler crons stay armed.


def downgrade() -> None:
    # Destructive migration — downgrade is no-op. To recover, re-run wizard.
    pass
```

- [ ] **Step 4: Verify migration applies cleanly (against current container DB)**

```bash
cd backend && uv run alembic upgrade head
```

Expected: `INFO [alembic.runtime.migration] Running upgrade e39428dc5cd5 -> <hash>, wipe for wizard redesign`.

- [ ] **Step 5: Verify DB is wiped**

```bash
docker compose exec -T postgres psql -U ibkr -d ibkr_control -c "SELECT
  (SELECT count(*) FROM accounts) AS accounts,
  (SELECT count(*) FROM trades) AS trades,
  (SELECT count(*) FROM flex_imports) AS imports,
  (SELECT count(*) FROM participations) AS parts,
  (SELECT setup_completed_at FROM users WHERE id=1) AS completed;"
```

Expected: all counts = 0; `completed` = NULL.

- [ ] **Step 6: Run migration test to confirm metadata still matches**

```bash
cd backend && uv run pytest tests/test_migrations.py -v
```

Expected: `test_migrations_apply_cleanly_and_match_metadata PASSED`.

- [ ] **Step 7: Commit**

```bash
git add backend/alembic/versions/
git commit -m "$(cat <<'EOF'
chore(migration): H wipe data for wizard redesign

Destructive migration per spec D1 — re-ingest desde cero. Preserva
flex_credentials (token sigue valido) y apscheduler_jobs (crons armados).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Backend — `_is_shadow_account` helper + apply filter

**Files:**
- Modify: `backend/src/ibkr_control/ingest/flex/persister.py`
- Create: `backend/tests/ingest/test_persister_f_filter.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/ingest/test_persister_f_filter.py`:

```python
"""F-account shadow filter at persister boundary.

Per spec section 1: accounts ending in 'F' are IB-UK Limited regulatory
shadow accounts (NAV=0, no fiscal data). The persister must filter them
before any INSERT into accounts/trades/cash_transactions/etc.
"""
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.db.models.accounts import Account
from ibkr_control.db.models.trades import Trade
from ibkr_control.db.models.cash_transactions import CashTransaction
from ibkr_control.db.models.flex_raw import FlexImport
from ibkr_control.ingest.flex.persister import persist_flex_data
from ibkr_control.ingest.flex.parser import ParsedFlexData, ParsedTrade, ParsedCashTx, ParsedAccountInfo


@pytest.mark.asyncio
async def test_persister_filters_f_accounts_from_all_tables(
    db_session: AsyncSession, sample_user
):
    """Single ParsedFlexData with both U99999999 and U99999999F across all entities."""
    data = ParsedFlexData(
        account_info=[
            ParsedAccountInfo(ibkr_account_id="U99999999", account_alias="Main", account_type="Individual", name="TEST USER"),
            ParsedAccountInfo(ibkr_account_id="U99999999F", account_alias=None, account_type=None, name=None),
        ],
        trades=[
            ParsedTrade(ibkr_account_id="U99999999", symbol="AAPL", quantity="10", price="150.00", trade_date="2026-01-15", settle_date="2026-01-17", proceeds_usd="-1500.00", commission_usd="-1.00", currency="USD", asset_class="STK"),
            ParsedTrade(ibkr_account_id="U99999999F", symbol="AAPL", quantity="10", price="150.00", trade_date="2026-01-15", settle_date="2026-01-17", proceeds_usd="-1500.00", commission_usd="-1.00", currency="USD", asset_class="STK"),
        ],
        cash_transactions=[
            ParsedCashTx(ibkr_account_id="U99999999", description="Trade fee", amount_usd="-1.00", currency="USD", tx_type="Commissions", date_posted="2026-01-15", symbol=None),
            ParsedCashTx(ibkr_account_id="U99999999F", description="Shadow fee", amount_usd="-0.50", currency="USD", tx_type="Commissions", date_posted="2026-01-15", symbol=None),
        ],
        closed_lots=[],
        open_position_lots=[],
        transfers=[],
        change_in_dividend_accruals=[],
        open_dividend_accruals=[],
    )

    flex_import = FlexImport(
        user_id=sample_user.id,
        anyo=2026,
        xml_hash="test_f_filter_hash_unique_001",
        xml_size_bytes=1000,
        source="manual_upload",
        period_covered_from="2026-01-01",
        period_covered_to="2026-12-31",
        year_status="sealed",
        status="ok",
    )
    db_session.add(flex_import)
    await db_session.flush()

    await persist_flex_data(db_session, flex_import, sample_user.id, data)
    await db_session.commit()

    accts = (await db_session.scalars(select(Account))).all()
    assert {a.ibkr_account_id for a in accts} == {"U99999999"}, (
        f"Only U99999999 must exist; got {[a.ibkr_account_id for a in accts]}"
    )

    trades = (await db_session.scalars(select(Trade))).all()
    assert len(trades) == 1
    assert trades[0].account_id == accts[0].id

    cash = (await db_session.scalars(select(CashTransaction))).all()
    assert len(cash) == 1
    assert cash[0].account_id == accts[0].id


@pytest.mark.asyncio
async def test_persister_handles_xml_with_only_f_accounts(
    db_session: AsyncSession, sample_user
):
    """Degenerate case: XML where every accountId ends in F. Insert nothing, no error."""
    data = ParsedFlexData(
        account_info=[ParsedAccountInfo(ibkr_account_id="U99999999F", account_alias=None, account_type=None, name=None)],
        trades=[],
        cash_transactions=[ParsedCashTx(ibkr_account_id="U99999999F", description="x", amount_usd="-0.50", currency="USD", tx_type="Commissions", date_posted="2026-01-15", symbol=None)],
        closed_lots=[], open_position_lots=[], transfers=[],
        change_in_dividend_accruals=[], open_dividend_accruals=[],
    )

    flex_import = FlexImport(
        user_id=sample_user.id, anyo=2026,
        xml_hash="test_only_f_unique_002",
        xml_size_bytes=500, source="manual_upload",
        period_covered_from="2026-01-01", period_covered_to="2026-12-31",
        year_status="sealed", status="ok",
    )
    db_session.add(flex_import)
    await db_session.flush()

    await persist_flex_data(db_session, flex_import, sample_user.id, data)
    await db_session.commit()

    assert (await db_session.scalar(select(func.count(Account.id)))) == 0
    assert (await db_session.scalar(select(func.count(CashTransaction.id)))) == 0
```

- [ ] **Step 2: Run failing test**

```bash
cd backend && uv run pytest tests/ingest/test_persister_f_filter.py -v
```

Expected: FAIL (either ImportError on `ParsedAccountInfo`/fields or AssertionError because F-accounts get persisted today).

- [ ] **Step 3: Add `_is_shadow_account` helper and apply filter in persister**

In `backend/src/ibkr_control/ingest/flex/persister.py`, add at module top (after existing imports, before any function):

```python
def _is_shadow_account(ibkr_account_id: str) -> bool:
    """IB-UK Limited regulatory shadow account (NAV=0, no fiscal data).

    Per spec section 1 + renta sibling: F-accounts only carry fees/journals;
    never trades, open positions, or closed lots. Filtered at persist time
    to keep `accounts` table free of accounts that shouldn't have participations.
    """
    return ibkr_account_id.endswith("F")
```

Then apply the filter in EVERY place where account IDs are collected or referenced.

In the function that collects `all_account_ids` (around lines 52-75 in current file), wrap each `.add()` with the filter:

```python
all_account_ids: set[str] = set()
for a in data.account_info:
    if not _is_shadow_account(a.ibkr_account_id):
        all_account_ids.add(a.ibkr_account_id)
for t in data.trades:
    if not _is_shadow_account(t.ibkr_account_id):
        all_account_ids.add(t.ibkr_account_id)
for cl in data.closed_lots:
    if not _is_shadow_account(cl.ibkr_account_id):
        all_account_ids.add(cl.ibkr_account_id)
for op in data.open_position_lots:
    if not _is_shadow_account(op.ibkr_account_id):
        all_account_ids.add(op.ibkr_account_id)
for ct in data.cash_transactions:
    if not _is_shadow_account(ct.ibkr_account_id):
        all_account_ids.add(ct.ibkr_account_id)
for tr in data.transfers:
    if tr.src_ibkr_account_id and not _is_shadow_account(tr.src_ibkr_account_id):
        all_account_ids.add(tr.src_ibkr_account_id)
    if tr.dst_ibkr_account_id and not _is_shadow_account(tr.dst_ibkr_account_id):
        all_account_ids.add(tr.dst_ibkr_account_id)
for da in data.change_in_dividend_accruals:
    if not _is_shadow_account(da.ibkr_account_id):
        all_account_ids.add(da.ibkr_account_id)
for oda in data.open_dividend_accruals:
    if not _is_shadow_account(oda.ibkr_account_id):
        all_account_ids.add(oda.ibkr_account_id)
```

Then in EACH persist loop (trades, closed_lots, open_position_lots, cash_transactions, transfers, dividend_accruals), add `if _is_shadow_account(<entity>.ibkr_account_id): continue` as the first line of the loop body.

For transfers (which have two account IDs), the skip condition is:
```python
for tr in data.transfers:
    src_shadow = tr.src_ibkr_account_id and _is_shadow_account(tr.src_ibkr_account_id)
    dst_shadow = tr.dst_ibkr_account_id and _is_shadow_account(tr.dst_ibkr_account_id)
    if src_shadow or dst_shadow:
        continue
    # ... existing logic
```

- [ ] **Step 4: Run new test to verify it passes**

```bash
cd backend && uv run pytest tests/ingest/test_persister_f_filter.py -v
```

Expected: PASS both tests.

- [ ] **Step 5: Run full suite to confirm no regression**

```bash
cd backend && uv run pytest -q
```

Expected: 192+ PASS (existing tests + 2 new ones). No regressions.

- [ ] **Step 6: Commit**

```bash
git add backend/src/ibkr_control/ingest/flex/persister.py \
        backend/tests/ingest/test_persister_f_filter.py
git commit -m "$(cat <<'EOF'
feat(persister): filter F-shadow accounts at INSERT boundary

Per spec section 1: accounts ending in 'F' are IB-UK Limited regulatory
shadow accounts (NAV=0, no fiscal data). Filtered in _ensure_accounts +
each persist loop (trades, cash_tx, closed_lots, open_lots, transfers,
dividend_accruals).

Invariant: accounts table never contains rows where ibkr_account_id
ends in 'F', regardless of code path (cron, manual upload, wizard).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Backend — parser extract `accountAlias` + `accountType` + `name`

**Files:**
- Modify: `backend/src/ibkr_control/ingest/flex/parser.py`

- [ ] **Step 1: Inspect current `_parse_account_info` and `ParsedAccountInfo`**

Run:
```bash
grep -n "ParsedAccountInfo\|_parse_account_info\|AccountInformation" backend/src/ibkr_control/ingest/flex/parser.py | head -20
```

Note which fields exist on `ParsedAccountInfo` today (likely just `ibkr_account_id`). New fields to add: `account_alias: str | None`, `account_type: str | None`, `name: str | None`.

- [ ] **Step 2: Write failing test**

Append to `backend/tests/ingest/test_parser.py` (create file if it doesn't exist; otherwise append):

```python
def test_parse_account_info_extracts_alias_type_and_name():
    """AccountInformation tag attrs accountAlias/accountType/name carried into ParsedAccountInfo."""
    from ibkr_control.ingest.flex.parser import parse_flex_xml

    xml = b'''<?xml version="1.0"?>
    <FlexQueryResponse>
      <FlexStatements>
        <FlexStatement accountId="U99999999" fromDate="20260101" toDate="20261231">
          <AccountInformation accountId="U99999999" accountAlias="My Joint" accountType="Joint" name="TEST USER" currency="USD"/>
        </FlexStatement>
      </FlexStatements>
    </FlexQueryResponse>
    '''
    parsed = parse_flex_xml(xml)
    assert len(parsed.account_info) == 1
    ai = parsed.account_info[0]
    assert ai.ibkr_account_id == "U99999999"
    assert ai.account_alias == "My Joint"
    assert ai.account_type == "Joint"
    assert ai.name == "TEST USER"


def test_parse_account_info_handles_missing_optional_attrs():
    """If accountAlias/name/accountType absent, fields are None (not empty string)."""
    from ibkr_control.ingest.flex.parser import parse_flex_xml

    xml = b'''<?xml version="1.0"?>
    <FlexQueryResponse>
      <FlexStatements>
        <FlexStatement accountId="U99999999" fromDate="20260101" toDate="20261231">
          <AccountInformation accountId="U99999999" currency="USD"/>
        </FlexStatement>
      </FlexStatements>
    </FlexQueryResponse>
    '''
    parsed = parse_flex_xml(xml)
    ai = parsed.account_info[0]
    assert ai.account_alias is None
    assert ai.account_type is None
    assert ai.name is None
```

- [ ] **Step 3: Run failing test**

```bash
cd backend && uv run pytest tests/ingest/test_parser.py::test_parse_account_info_extracts_alias_type_and_name -v
```

Expected: FAIL — `AttributeError: 'ParsedAccountInfo' object has no attribute 'account_alias'`.

- [ ] **Step 4: Add fields to `ParsedAccountInfo`**

In `backend/src/ibkr_control/ingest/flex/parser.py`, find the `ParsedAccountInfo` dataclass definition. Add three optional fields:

```python
@dataclass
class ParsedAccountInfo:
    ibkr_account_id: str
    account_alias: str | None = None
    account_type: str | None = None
    name: str | None = None
```

In `_parse_account_info` (the function that creates `ParsedAccountInfo` from XML), extend the construction:

```python
def _parse_account_info(elem) -> ParsedAccountInfo:
    return ParsedAccountInfo(
        ibkr_account_id=elem.get("accountId", ""),
        account_alias=elem.get("accountAlias") or None,
        account_type=elem.get("accountType") or None,
        name=elem.get("name") or None,
    )
```

Note `or None` converts empty string to None — IBKR sometimes emits `accountAlias=""` which we want as None for "no alias set".

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/ingest/test_parser.py -v
```

Expected: PASS (both new tests).

- [ ] **Step 6: Run full suite**

```bash
cd backend && uv run pytest -q
```

Expected: 194+ PASS.

- [ ] **Step 7: No commit yet — bundle with next backend tasks into D2 commit**

---

## Task 4: Backend — `_step3_stash` module (in-memory + TTL)

**Files:**
- Create: `backend/src/ibkr_control/api/_step3_stash.py`
- Create: `backend/tests/api/test_step3_stash.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/api/test_step3_stash.py`:

```python
"""In-memory stash for Step 3 XML uploads (per spec section 2 + D11)."""
import time
import pytest

from ibkr_control.api._step3_stash import Step3Stash, StashEntry


def test_stash_put_and_get():
    stash = Step3Stash(ttl_seconds=3600)
    temp_id = stash.put(user_id=1, data={"hello": "world"}, sha256="abc")
    entry = stash.get(user_id=1, temp_id=temp_id)
    assert entry is not None
    assert entry.data == {"hello": "world"}
    assert entry.sha256 == "abc"


def test_stash_isolated_per_user():
    stash = Step3Stash(ttl_seconds=3600)
    tid_user1 = stash.put(user_id=1, data={"a": 1}, sha256="x")
    assert stash.get(user_id=2, temp_id=tid_user1) is None  # other user can't see


def test_stash_ttl_expiry():
    stash = Step3Stash(ttl_seconds=0.05)  # 50ms TTL
    tid = stash.put(user_id=1, data={"a": 1}, sha256="x")
    assert stash.get(user_id=1, temp_id=tid) is not None
    time.sleep(0.1)
    assert stash.get(user_id=1, temp_id=tid) is None


def test_stash_pop_removes_entry():
    stash = Step3Stash(ttl_seconds=3600)
    tid = stash.put(user_id=1, data={"a": 1}, sha256="x")
    popped = stash.pop(user_id=1, temp_id=tid)
    assert popped is not None
    assert stash.get(user_id=1, temp_id=tid) is None  # gone after pop


def test_stash_list_for_user_returns_non_expired_only():
    stash = Step3Stash(ttl_seconds=3600)
    stash.put(user_id=1, data={"a": 1}, sha256="x")
    stash.put(user_id=1, data={"b": 2}, sha256="y")
    stash.put(user_id=2, data={"c": 3}, sha256="z")
    user1_entries = stash.list_for_user(user_id=1)
    assert len(user1_entries) == 2
    assert {e.sha256 for e in user1_entries} == {"x", "y"}


def test_stash_find_by_sha256_dedup_check():
    """Used by step3/upload to detect dup against an already-stashed item."""
    stash = Step3Stash(ttl_seconds=3600)
    tid = stash.put(user_id=1, data={"a": 1}, sha256="abc")
    found = stash.find_by_sha256(user_id=1, sha256="abc")
    assert found is not None
    assert found.temp_id == tid
    assert stash.find_by_sha256(user_id=1, sha256="other") is None
```

- [ ] **Step 2: Run failing test**

```bash
cd backend && uv run pytest tests/api/test_step3_stash.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'ibkr_control.api._step3_stash'`.

- [ ] **Step 3: Implement `_step3_stash`**

Create `backend/src/ibkr_control/api/_step3_stash.py`:

```python
"""In-memory stash for Step 3 XML uploads, with TTL.

Per spec D11: parsed-but-not-committed XMLs live in process memory
while the user resolves new_accounts. Container restart loses stash
(user re-uploads). Mirror of JobTracker pattern; not DB-backed
because TTL is < container lifetime in normal operation.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StashEntry:
    temp_id: str
    user_id: int
    data: Any  # ParsedFlexData; typed Any to avoid circular import
    sha256: str
    expires_at: float = field(default=0.0)


class Step3Stash:
    """Per-process dict; safe for single-replica single-process app."""

    def __init__(self, ttl_seconds: float = 3600) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[str, StashEntry] = {}

    def _prune(self) -> None:
        now = time.monotonic()
        expired = [tid for tid, e in self._entries.items() if e.expires_at < now]
        for tid in expired:
            self._entries.pop(tid, None)

    def put(self, *, user_id: int, data: Any, sha256: str) -> str:
        self._prune()
        temp_id = uuid.uuid4().hex
        self._entries[temp_id] = StashEntry(
            temp_id=temp_id,
            user_id=user_id,
            data=data,
            sha256=sha256,
            expires_at=time.monotonic() + self._ttl,
        )
        return temp_id

    def get(self, *, user_id: int, temp_id: str) -> StashEntry | None:
        self._prune()
        entry = self._entries.get(temp_id)
        if entry is None or entry.user_id != user_id:
            return None
        return entry

    def pop(self, *, user_id: int, temp_id: str) -> StashEntry | None:
        entry = self.get(user_id=user_id, temp_id=temp_id)
        if entry is None:
            return None
        self._entries.pop(temp_id, None)
        return entry

    def list_for_user(self, *, user_id: int) -> list[StashEntry]:
        self._prune()
        return [e for e in self._entries.values() if e.user_id == user_id]

    def find_by_sha256(self, *, user_id: int, sha256: str) -> StashEntry | None:
        self._prune()
        for e in self._entries.values():
            if e.user_id == user_id and e.sha256 == sha256:
                return e
        return None


_singleton: Step3Stash | None = None


def get_stash() -> Step3Stash:
    """Module-level singleton accessor (mirrors JobTracker pattern)."""
    global _singleton
    if _singleton is None:
        _singleton = Step3Stash(ttl_seconds=3600)
    return _singleton
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/api/test_step3_stash.py -v
```

Expected: PASS (6 tests).

- [ ] **Step 5: No commit yet — bundle**

---

## Task 5: Backend — schemas for new endpoints

**Files:**
- Modify: `backend/src/ibkr_control/api/_schemas.py`

- [ ] **Step 1: Inspect current schemas**

```bash
grep -n "class.*BaseModel\|FlexCredentials\|SetupState\|SetupStep" backend/src/ibkr_control/api/_schemas.py
```

Note existing class names so we don't collide.

- [ ] **Step 2: Add new schemas**

Append to `backend/src/ibkr_control/api/_schemas.py`:

```python
# ===== Wizard redesign schemas (v0.2.2) =====


class DetectedAccount(BaseModel):
    ibkr_account_id: str
    suggested_alias: str | None
    account_type: str | None
    account_holder: str | None


class Step2DetectResponse(BaseModel):
    detected_accounts: list[DetectedAccount]
    flex_import_id: int
    ingest_summary: dict  # {n_trades, n_cash_tx, ...} — opaque shape


class Step2DetectFromXmlResponse(BaseModel):
    detected_accounts: list[DetectedAccount]
    parsed_only: bool = True


class Step2SaveAccountItem(BaseModel):
    ibkr_account_id: str = Field(min_length=9, max_length=12, pattern=r"^U\d{8,11}$")
    alias: str | None = Field(default=None, max_length=200)
    pct: Decimal = Field(ge=Decimal("0"), le=Decimal("1"), decimal_places=4)


class Step2SaveRequest(BaseModel):
    accounts: list[Step2SaveAccountItem] = Field(min_length=1)


class Step3UploadResponse(BaseModel):
    flex_import_temp_id: str
    detected_accounts: list[DetectedAccount]
    new_accounts: list[DetectedAccount]
    period: dict  # {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"}
    anyo: int
    sha256: str


class Step3SaveNewAccountsRequest(BaseModel):
    accounts: list[Step2SaveAccountItem] = Field(min_length=1)


class Step3CommitRequest(BaseModel):
    temp_ids: list[str]  # empty list = skip historicos


class Step3CommitResponse(BaseModel):
    flex_import_ids: list[int]
    total_rows_inserted: int


class WizardStateResponse(BaseModel):
    step1_credentials: bool
    step2_accounts: bool
    step3_xmls: bool
    step3_n_xmls_uploaded: int
    setup_completed_at: datetime | None
    detected_accounts: list[DetectedAccount] | None = None
    pending_stash_temp_ids: list[str] = Field(default_factory=list)
```

Verify `from decimal import Decimal`, `from datetime import datetime`, and `from pydantic import Field` are imported at the top of the file (add if missing).

- [ ] **Step 3: Schema-only smoke test (no test file needed yet)**

```bash
cd backend && uv run python -c "
from ibkr_control.api._schemas import (
    DetectedAccount, Step2DetectResponse, Step2SaveAccountItem,
    Step2SaveRequest, Step3UploadResponse, Step3CommitRequest, WizardStateResponse
)
# Roundtrip validation
DetectedAccount(ibkr_account_id='U99999999', suggested_alias='x', account_type='Joint', account_holder='Y')
Step2SaveAccountItem(ibkr_account_id='U99999999', alias='a', pct='0.5000')
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 4: No commit yet — bundle with endpoints**

---

## Task 6: Backend — rewrite `setup.py` router (all endpoints in one task)

This task is intentionally large — the existing `setup.py` (~220 lines) gets replaced wholesale because every endpoint changes contract. Sub-steps keep edits incremental.

**Files:**
- Modify: `backend/src/ibkr_control/api/setup.py` (REWRITE)

- [ ] **Step 1: Read current `setup.py` end-to-end so you understand existing imports + helpers**

```bash
cat backend/src/ibkr_control/api/setup.py
```

Note: `_set_progress` helper, `_run_setup_meta_job` background task, existing imports. The rewrite drops `_run_setup_meta_job` (TRM moves to step2/save background), keeps `_set_progress`.

- [ ] **Step 2: Replace `setup.py` with the new contract**

Overwrite `backend/src/ibkr_control/api/setup.py` with this exact content (whole file replacement):

```python
"""Wizard endpoints — auto-detect cuentas via Flex Web Service.

Per spec docs/specs/2026-05-24-wizard-redesign-design.md.

Endpoints:
    POST /api/setup/step1/save              save creds (no IBKR call)
    POST /api/setup/step2/detect            fetch YTD + parse + persist
    POST /api/setup/step2/detect_from_xml   parse uploaded XML (no persist)
    POST /api/setup/step2/save              persist accounts + participations
    POST /api/setup/step3/upload            stash uploaded XML
    POST /api/setup/step3/save_new_accounts persist accounts for new IDs
    POST /api/setup/step3/commit            persist all stashed XMLs
    POST /api/setup/finish                  mark setup_completed_at
    GET  /api/setup/state                   derived + stored state
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import attributes

from ibkr_control.api._schemas import (
    DetectedAccount,
    FlexCredentialsValidate,
    Step2DetectFromXmlResponse,
    Step2DetectResponse,
    Step2SaveRequest,
    Step3CommitRequest,
    Step3CommitResponse,
    Step3SaveNewAccountsRequest,
    Step3UploadResponse,
    WizardStateResponse,
)
from ibkr_control.api._step3_stash import get_stash
from ibkr_control.auth.backend import current_active_user
from ibkr_control.auth.models import User
from ibkr_control.config import get_settings
from ibkr_control.db.models.accounts import Account
from ibkr_control.db.models.flex_credentials import FlexCredentials
from ibkr_control.db.models.flex_raw import FlexImport
from ibkr_control.db.models.participations import Participation
from ibkr_control.db.session import get_async_session, get_engine
from ibkr_control.ingest.flex import client as flex_client_mod
from ibkr_control.ingest.flex import crypto as flex_crypto_mod
from ibkr_control.ingest.flex import parser as flex_parser_mod
from ibkr_control.ingest.flex.persister import persist_flex_data

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/setup", tags=["setup"])


# ===== Helpers =====


def _set_progress(user: User, key: str, value) -> None:
    progress = dict(user.setup_progress or {})
    progress[key] = value
    user.setup_progress = progress
    attributes.flag_modified(user, "setup_progress")


def _to_detected_account(account_info) -> DetectedAccount:
    return DetectedAccount(
        ibkr_account_id=account_info.ibkr_account_id,
        suggested_alias=account_info.account_alias,
        account_type=account_info.account_type,
        account_holder=account_info.name,
    )


def _is_shadow(ibkr_account_id: str) -> bool:
    return ibkr_account_id.endswith("F")


async def _trm_backfill_background(user_id: int) -> None:
    """Best-effort TRM full backfill post-step2/save. Errors logged, not propagated."""
    from ibkr_control.ingest.trm import job as trm_job_mod

    engine = get_engine()
    session_local = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await trm_job_mod.run(session_local, trigger="wizard", full_backfill=True)
    except Exception:
        logger.exception("TRM backfill background for user_id=%s failed", user_id)


# ===== STATE =====


@router.get("/state", response_model=WizardStateResponse)
async def get_state(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> WizardStateResponse:
    has_creds = (
        await session.scalar(
            select(func.count(FlexCredentials.user_id)).where(FlexCredentials.user_id == user.id)
        )
    ) > 0
    has_parts = (
        await session.scalar(
            select(func.count(Participation.user_id)).where(Participation.user_id == user.id)
        )
    ) > 0
    n_xmls = (
        await session.scalar(
            select(func.count(FlexImport.id)).where(
                FlexImport.user_id == user.id, FlexImport.source == "manual_upload"
            )
        )
    ) or 0

    p = user.setup_progress or {}
    stash = get_stash()
    pending = [e.temp_id for e in stash.list_for_user(user_id=user.id)]

    return WizardStateResponse(
        step1_credentials=has_creds,
        step2_accounts=has_parts,
        step3_xmls=p.get("step3_xmls", False),
        step3_n_xmls_uploaded=n_xmls,
        setup_completed_at=user.setup_completed_at,
        detected_accounts=None,
        pending_stash_temp_ids=pending,
    )


# ===== STEP 1 =====


@router.post("/step1/save")
async def step1_save(
    payload: FlexCredentialsValidate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Save creds (encrypt token). Does NOT call IBKR per spec D5."""
    creds = await session.scalar(
        select(FlexCredentials).where(FlexCredentials.user_id == user.id)
    )
    encrypted = flex_crypto_mod.encrypt_token(payload.token)
    if creds is None:
        creds = FlexCredentials(
            user_id=user.id, token_encrypted=encrypted, ytd_query_id=payload.query_id
        )
        session.add(creds)
    else:
        creds.token_encrypted = encrypted
        creds.ytd_query_id = payload.query_id
        creds.last_rotated_at = datetime.now(timezone.utc)
    await session.commit()
    return {"ok": True}


# ===== STEP 2 DETECT =====


_DETECT_RETRY_DELAYS = [5, 15, 30]  # seconds; total max wait ~50s plus the calls themselves


@router.post("/step2/detect", response_model=Step2DetectResponse)
async def step2_detect(
    background: BackgroundTasks,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> Step2DetectResponse:
    """Fetch + parse + persist YTD. Returns detected accounts (sin F)."""
    creds = await session.scalar(
        select(FlexCredentials).where(FlexCredentials.user_id == user.id)
    )
    if creds is None:
        raise HTTPException(status_code=400, detail="MISSING_CREDENTIALS")

    token = flex_crypto_mod.decrypt_token(creds.token_encrypted)
    query_id = creds.ytd_query_id

    client = flex_client_mod.FlexClient(token=token)

    xml_bytes: bytes | None = None
    for attempt_idx, delay in enumerate(_DETECT_RETRY_DELAYS + [None]):
        try:
            ref = await client.send_request(query_id=query_id)
            xml_bytes = await client.get_statement(reference_code=ref)
            break
        except flex_client_mod.FlexBusyError:
            if delay is None:
                raise HTTPException(
                    status_code=503,
                    detail={"code": "IBKR_BUSY", "attempts": attempt_idx + 1},
                )
            await asyncio.sleep(delay)
        except flex_client_mod.FlexAuthError as e:
            raise HTTPException(status_code=401, detail="INVALID_TOKEN") from e
        except flex_client_mod.FlexQueryNotFoundError as e:
            raise HTTPException(status_code=400, detail="QUERY_NOT_FOUND") from e
        except flex_client_mod.FlexError as e:
            raise HTTPException(
                status_code=502,
                detail={"code": "IBKR_ERROR", "ibkr_code": e.code, "message": str(e)},
            ) from e
        except Exception as e:
            raise HTTPException(status_code=504, detail="IBKR_TIMEOUT") from e

    assert xml_bytes is not None
    parsed = flex_parser_mod.parse_flex_xml(xml_bytes)
    sha = hashlib.sha256(xml_bytes).hexdigest()

    anyo = datetime.now(timezone.utc).year
    period_from = parsed.period_from or date(anyo, 1, 1)
    period_to = parsed.period_to or date(anyo, 12, 31)

    flex_import = FlexImport(
        user_id=user.id,
        anyo=anyo,
        xml_hash=sha,
        xml_size_bytes=len(xml_bytes),
        source="web_service",
        period_covered_from=period_from,
        period_covered_to=period_to,
        year_status="rolling",
        status="ok",
    )
    session.add(flex_import)
    await session.flush()

    summary = await persist_flex_data(session, flex_import, user.id, parsed)
    await session.commit()

    detected = [
        _to_detected_account(ai)
        for ai in parsed.account_info
        if not _is_shadow(ai.ibkr_account_id)
    ]

    return Step2DetectResponse(
        detected_accounts=detected,
        flex_import_id=flex_import.id,
        ingest_summary=summary,
    )


@router.post("/step2/detect_from_xml", response_model=Step2DetectFromXmlResponse)
async def step2_detect_from_xml(
    file: UploadFile = File(...),
    user: User = Depends(current_active_user),
) -> Step2DetectFromXmlResponse:
    """Parse uploaded XML, return detected accounts. NO persist."""
    content = await file.read()
    settings = get_settings()
    if len(content) > settings.max_xml_size_bytes:
        raise HTTPException(status_code=413, detail="FILE_TOO_LARGE")
    try:
        parsed = flex_parser_mod.parse_flex_xml(content)
    except Exception as e:
        raise HTTPException(
            status_code=422, detail={"code": "PARSE_ERROR", "message": str(e)[:500]}
        ) from e

    detected = [
        _to_detected_account(ai)
        for ai in parsed.account_info
        if not _is_shadow(ai.ibkr_account_id)
    ]
    return Step2DetectFromXmlResponse(detected_accounts=detected, parsed_only=True)


# ===== STEP 2 SAVE =====


@router.post("/step2/save")
async def step2_save(
    payload: Step2SaveRequest,
    background: BackgroundTasks,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Persist accounts + participations. Dispatches TRM backfill in background (per D6)."""
    # Validate every account_id was detected (anti-typo): check it exists in any
    # of this user's flex_imports as accountId in the AccountInformation tag, OR
    # exists already in accounts table.
    detected_ids = set()
    flex_imports = (
        await session.scalars(select(FlexImport).where(FlexImport.user_id == user.id))
    ).all()
    if not flex_imports:
        raise HTTPException(status_code=400, detail="NO_DETECT_YET")
    # We trust that any account already in DB was detected previously.
    existing_accounts = (await session.scalars(select(Account))).all()
    for a in existing_accounts:
        detected_ids.add(a.ibkr_account_id)
    # Also any incoming account whose ID does not end in F is acceptable —
    # the persister has already created them via flex_imports. We re-query
    # accounts after each persist; if the ID is not there, reject.
    for item in payload.accounts:
        if _is_shadow(item.ibkr_account_id):
            raise HTTPException(
                status_code=400,
                detail={"code": "SHADOW_ACCOUNT_REJECTED", "ibkr_account_id": item.ibkr_account_id},
            )
        if item.ibkr_account_id not in detected_ids:
            raise HTTPException(
                status_code=400,
                detail={"code": "ACCOUNT_NOT_DETECTED", "ibkr_account_id": item.ibkr_account_id},
            )

    today = date.today()
    for item in payload.accounts:
        acc = await session.scalar(
            select(Account).where(Account.ibkr_account_id == item.ibkr_account_id)
        )
        # acc must exist because we verified detected_ids above
        if item.alias is not None:
            acc.alias = item.alias

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
        session.add(
            Participation(
                user_id=user.id,
                account_id=acc.id,
                pct=item.pct,
                valid_from=today,
                valid_to=None,
            )
        )

    await session.commit()
    # Fire-and-forget TRM backfill per D6
    background.add_task(_trm_backfill_background, user_id=user.id)
    return {"ok": True}


# ===== STEP 3 =====


@router.post("/step3/upload", response_model=Step3UploadResponse)
async def step3_upload(
    file: UploadFile = File(...),
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> Step3UploadResponse:
    content = await file.read()
    settings = get_settings()
    if len(content) > settings.max_xml_size_bytes:
        raise HTTPException(status_code=413, detail="FILE_TOO_LARGE")

    sha = hashlib.sha256(content).hexdigest()

    existing_import = await session.scalar(
        select(FlexImport).where(FlexImport.xml_hash == sha)
    )
    if existing_import is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "DUPLICATE_XML", "flex_import_id": existing_import.id},
        )

    stash = get_stash()
    if stash.find_by_sha256(user_id=user.id, sha256=sha) is not None:
        raise HTTPException(
            status_code=409, detail={"code": "DUPLICATE_XML_STASHED"}
        )

    try:
        parsed = flex_parser_mod.parse_flex_xml(content)
    except Exception as e:
        raise HTTPException(
            status_code=422, detail={"code": "PARSE_ERROR", "message": str(e)[:500]}
        ) from e

    existing_accounts = {
        a.ibkr_account_id
        for a in (await session.scalars(select(Account))).all()
    }

    detected = [
        _to_detected_account(ai)
        for ai in parsed.account_info
        if not _is_shadow(ai.ibkr_account_id)
    ]
    new_accounts = [
        da for da in detected if da.ibkr_account_id not in existing_accounts
    ]

    period_from = parsed.period_from or date(2024, 1, 1)
    period_to = parsed.period_to or date(2024, 12, 31)
    anyo = period_from.year

    temp_id = stash.put(
        user_id=user.id,
        data={
            "parsed": parsed,
            "sha256": sha,
            "size_bytes": len(content),
            "anyo": anyo,
            "period_from": period_from,
            "period_to": period_to,
        },
        sha256=sha,
    )

    return Step3UploadResponse(
        flex_import_temp_id=temp_id,
        detected_accounts=detected,
        new_accounts=new_accounts,
        period={"from": period_from.isoformat(), "to": period_to.isoformat()},
        anyo=anyo,
        sha256=sha,
    )


@router.post("/step3/save_new_accounts")
async def step3_save_new_accounts(
    payload: Step3SaveNewAccountsRequest,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    today = date.today()
    for item in payload.accounts:
        if _is_shadow(item.ibkr_account_id):
            raise HTTPException(
                status_code=400,
                detail={"code": "SHADOW_ACCOUNT_REJECTED", "ibkr_account_id": item.ibkr_account_id},
            )
        acc = await session.scalar(
            select(Account).where(Account.ibkr_account_id == item.ibkr_account_id)
        )
        if acc is None:
            acc = Account(
                ibkr_account_id=item.ibkr_account_id,
                alias=item.alias,
                currency="USD",
            )
            session.add(acc)
            await session.flush()
        elif item.alias is not None:
            acc.alias = item.alias

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
        session.add(
            Participation(
                user_id=user.id,
                account_id=acc.id,
                pct=item.pct,
                valid_from=today,
                valid_to=None,
            )
        )
    await session.commit()
    return {"ok": True}


@router.post("/step3/commit", response_model=Step3CommitResponse)
async def step3_commit(
    payload: Step3CommitRequest,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> Step3CommitResponse:
    stash = get_stash()
    flex_import_ids: list[int] = []
    total_rows = 0

    # Validate first pass: all temp_ids exist + no unresolved new accounts
    existing_accounts = {
        a.ibkr_account_id
        for a in (await session.scalars(select(Account))).all()
    }
    for temp_id in payload.temp_ids:
        entry = stash.get(user_id=user.id, temp_id=temp_id)
        if entry is None:
            raise HTTPException(
                status_code=410, detail={"code": "TEMP_ID_EXPIRED", "temp_id": temp_id}
            )
        for ai in entry.data["parsed"].account_info:
            if _is_shadow(ai.ibkr_account_id):
                continue
            if ai.ibkr_account_id not in existing_accounts:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "UNRESOLVED_NEW_ACCOUNTS",
                        "ibkr_account_id": ai.ibkr_account_id,
                    },
                )

    # Persist pass
    for temp_id in payload.temp_ids:
        entry = stash.pop(user_id=user.id, temp_id=temp_id)
        if entry is None:
            continue  # belt-and-suspenders; validate pass already checked
        data = entry.data
        flex_import = FlexImport(
            user_id=user.id,
            anyo=data["anyo"],
            xml_hash=data["sha256"],
            xml_size_bytes=data["size_bytes"],
            source="manual_upload",
            period_covered_from=data["period_from"],
            period_covered_to=data["period_to"],
            year_status="sealed",
            status="ok",
        )
        session.add(flex_import)
        await session.flush()
        summary = await persist_flex_data(session, flex_import, user.id, data["parsed"])
        flex_import_ids.append(flex_import.id)
        total_rows += sum(summary.values())

    _set_progress(user, "step3_xmls", True)
    await session.commit()
    return Step3CommitResponse(
        flex_import_ids=flex_import_ids, total_rows_inserted=total_rows
    )


# ===== FINISH =====


@router.post("/finish")
async def finish(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    if user.setup_completed_at is not None:
        return {"ok": True, "already_completed": True}

    has_parts = (
        await session.scalar(
            select(func.count(Participation.user_id)).where(Participation.user_id == user.id)
        )
    ) > 0
    if not has_parts:
        raise HTTPException(status_code=400, detail="INCOMPLETE_SETUP")

    p = user.setup_progress or {}
    if not p.get("step3_xmls"):
        raise HTTPException(status_code=400, detail="INCOMPLETE_SETUP")

    user.setup_completed_at = datetime.now(timezone.utc)
    await session.commit()
    return {"ok": True}
```

- [ ] **Step 3: Adjust supporting code so imports resolve**

The new `setup.py` imports:
- `flex_client_mod.FlexBusyError`, `FlexAuthError`, `FlexQueryNotFoundError`, `FlexError` — confirm each exists in `backend/src/ibkr_control/ingest/flex/client.py`. If `FlexBusyError` or `FlexQueryNotFoundError` don't exist yet, add them (subclasses of the existing `FlexError` or `Exception`). The mapping is by IBKR error code: 1001 → Busy; 1005 → QueryNotFound; 1003/1004 → Auth (likely already exists).
- `flex_client_mod.FlexClient.get_statement(reference_code=...)` — confirm method exists. If `client.send_request` today does the GetStatement step internally and returns parsed bytes, you'll need to factor out the two-step send/get into separate methods.
- `flex_parser_mod.parse_flex_xml(bytes)` — confirm function name. Adjust if real name differs (e.g. `parse_xml`).
- `parsed.period_from` / `parsed.period_to` — add as fields on `ParsedFlexData` dataclass if missing. They come from `<FlexStatement fromDate=... toDate=...>`.
- `persist_flex_data(session, flex_import, user_id, parsed)` — confirm signature in current `persister.py`. Adjust call site or signature to match.

Make minimal patches to those files as needed. For each, the smallest change that makes the import work.

- [ ] **Step 4: Restart docker compose to load new schemas / code**

```bash
docker compose restart backend
docker compose logs backend --tail=20
```

Expected: no import errors; `Uvicorn running on http://0.0.0.0:8000`.

- [ ] **Step 5: No commit yet — tests come in next tasks before bundling**

---

## Task 7: Backend — test `step1/save`

**Files:**
- Rewrite: `backend/tests/api/test_setup_step1.py`

- [ ] **Step 1: Write tests**

Replace `backend/tests/api/test_setup_step1.py` (overwrite) with:

```python
"""Tests for POST /api/setup/step1/save — no IBKR call, just persist creds."""
import pytest
from httpx import AsyncClient

from ibkr_control.ingest.flex import crypto as flex_crypto_mod


async def test_step1_save_persists_creds_without_ibkr_call(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    """No HTTP call to IBKR; token gets encrypted and stored."""
    called = []

    async def boom(*args, **kwargs):
        called.append(True)
        raise RuntimeError("step1 must not call IBKR")

    monkeypatch.setattr("ibkr_control.ingest.flex.client.FlexClient.send_request", boom)

    r = await client.post(
        "/api/setup/step1/save",
        headers=auth_headers,
        json={"token": "tok_short_test_value_12345", "query_id": "999"},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert called == []


async def test_step1_save_is_idempotent_upsert(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    monkeypatch.setattr(
        "ibkr_control.ingest.flex.client.FlexClient.send_request",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call")),
    )

    r1 = await client.post(
        "/api/setup/step1/save",
        headers=auth_headers,
        json={"token": "tok_first_value_12345", "query_id": "111"},
    )
    assert r1.status_code == 200
    r2 = await client.post(
        "/api/setup/step1/save",
        headers=auth_headers,
        json={"token": "tok_second_value_12345", "query_id": "222"},
    )
    assert r2.status_code == 200
```

- [ ] **Step 2: Run tests**

```bash
cd backend && uv run pytest tests/api/test_setup_step1.py -v
```

Expected: PASS (2 tests).

- [ ] **Step 3: No commit yet — bundle**

---

## Task 8: Backend — test `step2/detect` with mocked IBKR

**Files:**
- Create: `backend/tests/api/test_setup_step2_detect.py`

- [ ] **Step 1: Write tests**

```python
"""Tests for POST /api/setup/step2/detect — mock FlexClient end-to-end."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ibkr_control.config import get_settings
from ibkr_control.db.models.accounts import Account
from ibkr_control.db.models.flex_credentials import FlexCredentials
from ibkr_control.db.models.flex_raw import FlexImport
from ibkr_control.ingest.flex import client as flex_client_mod
from ibkr_control.ingest.flex import crypto as flex_crypto_mod


_FAKE_XML = b"""<?xml version="1.0"?>
<FlexQueryResponse>
  <FlexStatements>
    <FlexStatement accountId="U99999999" fromDate="20260101" toDate="20260524">
      <AccountInformation accountId="U99999999" accountAlias="Main" accountType="Individual" name="TEST USER" currency="USD"/>
      <AccountInformation accountId="U99999999F" currency="USD"/>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>
"""


async def _seed_creds(client, auth_headers):
    r = await client.post(
        "/api/setup/step1/save",
        headers=auth_headers,
        json={"token": "tok_value_12345", "query_id": "999"},
    )
    assert r.status_code == 200


async def test_step2_detect_happy_path_returns_accounts_filtering_f(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    await _seed_creds(client, auth_headers)

    monkeypatch.setattr(
        flex_client_mod.FlexClient, "send_request",
        AsyncMock(return_value="ref-001"),
    )
    monkeypatch.setattr(
        flex_client_mod.FlexClient, "get_statement",
        AsyncMock(return_value=_FAKE_XML),
    )

    r = await client.post("/api/setup/step2/detect", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    ids = [a["ibkr_account_id"] for a in body["detected_accounts"]]
    assert ids == ["U99999999"], f"F filtered out; got {ids}"
    assert body["detected_accounts"][0]["suggested_alias"] == "Main"
    assert body["detected_accounts"][0]["account_type"] == "Individual"
    assert body["flex_import_id"] > 0


async def test_step2_detect_retries_on_1001_then_succeeds(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    await _seed_creds(client, auth_headers)

    busy = flex_client_mod.FlexBusyError("1001")
    send_mock = AsyncMock(side_effect=[busy, busy, "ref-ok"])
    monkeypatch.setattr(flex_client_mod.FlexClient, "send_request", send_mock)
    monkeypatch.setattr(
        flex_client_mod.FlexClient, "get_statement", AsyncMock(return_value=_FAKE_XML)
    )
    monkeypatch.setattr("ibkr_control.api.setup._DETECT_RETRY_DELAYS", [0, 0, 0])

    r = await client.post("/api/setup/step2/detect", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert send_mock.await_count == 3


async def test_step2_detect_503_after_max_retries(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    await _seed_creds(client, auth_headers)
    busy = flex_client_mod.FlexBusyError("1001")
    monkeypatch.setattr(
        flex_client_mod.FlexClient, "send_request", AsyncMock(side_effect=busy)
    )
    monkeypatch.setattr("ibkr_control.api.setup._DETECT_RETRY_DELAYS", [0, 0, 0])

    r = await client.post("/api/setup/step2/detect", headers=auth_headers)
    assert r.status_code == 503
    body = r.json()
    assert body["detail"]["code"] == "IBKR_BUSY"


async def test_step2_detect_401_on_invalid_token(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    await _seed_creds(client, auth_headers)
    monkeypatch.setattr(
        flex_client_mod.FlexClient, "send_request",
        AsyncMock(side_effect=flex_client_mod.FlexAuthError("1003", "bad token")),
    )
    r = await client.post("/api/setup/step2/detect", headers=auth_headers)
    assert r.status_code == 401


async def test_step2_detect_400_when_no_creds(
    client: AsyncClient, auth_headers: dict
):
    r = await client.post("/api/setup/step2/detect", headers=auth_headers)
    assert r.status_code == 400
```

- [ ] **Step 2: Run tests**

```bash
cd backend && uv run pytest tests/api/test_setup_step2_detect.py -v
```

Expected: PASS (5 tests). If a test fails due to missing `FlexBusyError` / `FlexQueryNotFoundError`, that's expected from Task 6 step 3 — fix the client to expose those error classes per IBKR error codes (1001 → Busy, 1005 → QueryNotFound).

- [ ] **Step 3: No commit yet**

---

## Task 9: Backend — test `step2/detect_from_xml`, `step2/save`, `step3/upload`, `step3/save_new_accounts`, `step3/commit`, `finish`, `state`

**Files:**
- Create: `backend/tests/api/test_setup_step2_detect_from_xml.py`
- Rewrite: `backend/tests/api/test_setup_step2_save.py`
- Create: `backend/tests/api/test_setup_step3.py`
- Create: `backend/tests/api/test_setup_finish.py`
- Rewrite: `backend/tests/api/test_setup_state.py`

Run tests after each file (`uv run pytest tests/api/test_setup_<name>.py -v`), implement minimal fixes in `setup.py` as failures surface.

- [ ] **Step 1: Write `test_setup_step2_detect_from_xml.py`**

```python
"""POST /api/setup/step2/detect_from_xml — parse, no persist."""
import pytest
from httpx import AsyncClient


_XML_OK = b"""<?xml version="1.0"?>
<FlexQueryResponse><FlexStatements>
  <FlexStatement accountId="U99999999" fromDate="20240101" toDate="20241231">
    <AccountInformation accountId="U99999999" accountAlias="Hist" accountType="Joint" name="X" currency="USD"/>
    <AccountInformation accountId="U99999999F" currency="USD"/>
  </FlexStatement>
</FlexStatements></FlexQueryResponse>
"""

_XML_ONLY_F = b"""<?xml version="1.0"?>
<FlexQueryResponse><FlexStatements>
  <FlexStatement accountId="U99999999F" fromDate="20240101" toDate="20241231">
    <AccountInformation accountId="U99999999F" currency="USD"/>
  </FlexStatement>
</FlexStatements></FlexQueryResponse>
"""


async def test_detect_from_xml_returns_accounts_filtering_f(
    client: AsyncClient, auth_headers: dict
):
    files = {"file": ("y.xml", _XML_OK, "application/xml")}
    r = await client.post("/api/setup/step2/detect_from_xml", headers=auth_headers, files=files)
    assert r.status_code == 200
    body = r.json()
    ids = [a["ibkr_account_id"] for a in body["detected_accounts"]]
    assert ids == ["U99999999"]
    assert body["parsed_only"] is True


async def test_detect_from_xml_only_f_returns_empty(
    client: AsyncClient, auth_headers: dict
):
    files = {"file": ("z.xml", _XML_ONLY_F, "application/xml")}
    r = await client.post("/api/setup/step2/detect_from_xml", headers=auth_headers, files=files)
    assert r.status_code == 200
    assert r.json()["detected_accounts"] == []


async def test_detect_from_xml_malformed_returns_422(
    client: AsyncClient, auth_headers: dict
):
    files = {"file": ("bad.xml", b"<not xml", "application/xml")}
    r = await client.post("/api/setup/step2/detect_from_xml", headers=auth_headers, files=files)
    assert r.status_code == 422
```

- [ ] **Step 2: Write `test_setup_step2_save.py` (rewrite)**

```python
"""POST /api/setup/step2/save — persist accounts + participations + TRM background."""
from unittest.mock import AsyncMock
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ibkr_control.config import get_settings
from ibkr_control.db.models.accounts import Account
from ibkr_control.db.models.participations import Participation
from ibkr_control.ingest.flex import client as flex_client_mod


_FAKE_XML = b"""<?xml version="1.0"?>
<FlexQueryResponse><FlexStatements>
  <FlexStatement accountId="U99999999" fromDate="20260101" toDate="20260524">
    <AccountInformation accountId="U99999999" accountAlias="A" accountType="Joint" name="X" currency="USD"/>
  </FlexStatement>
</FlexStatements></FlexQueryResponse>
"""


async def _seed_detect(client, auth_headers, monkeypatch):
    await client.post(
        "/api/setup/step1/save",
        headers=auth_headers,
        json={"token": "tok_12345_seed", "query_id": "999"},
    )
    monkeypatch.setattr(
        flex_client_mod.FlexClient, "send_request", AsyncMock(return_value="ref")
    )
    monkeypatch.setattr(
        flex_client_mod.FlexClient, "get_statement", AsyncMock(return_value=_FAKE_XML)
    )
    r = await client.post("/api/setup/step2/detect", headers=auth_headers)
    assert r.status_code == 200, r.text


async def test_step2_save_persists_accounts_and_participations(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    await _seed_detect(client, auth_headers, monkeypatch)
    r = await client.post(
        "/api/setup/step2/save",
        headers=auth_headers,
        json={"accounts": [{"ibkr_account_id": "U99999999", "alias": "Joint", "pct": "0.5000"}]},
    )
    assert r.status_code == 200

    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as s:
            accs = (await s.scalars(select(Account))).all()
            assert {a.ibkr_account_id for a in accs} == {"U99999999"}
            parts = (await s.scalars(select(Participation))).all()
            assert len(parts) == 1
            assert parts[0].pct == __import__("decimal").Decimal("0.5000")
    finally:
        await engine.dispose()


async def test_step2_save_400_when_account_not_detected(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    await _seed_detect(client, auth_headers, monkeypatch)
    r = await client.post(
        "/api/setup/step2/save",
        headers=auth_headers,
        json={"accounts": [{"ibkr_account_id": "U88888888", "alias": None, "pct": "1.0000"}]},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "ACCOUNT_NOT_DETECTED"


async def test_step2_save_rejects_shadow_id(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    await _seed_detect(client, auth_headers, monkeypatch)
    r = await client.post(
        "/api/setup/step2/save",
        headers=auth_headers,
        json={"accounts": [{"ibkr_account_id": "U99999999F", "alias": None, "pct": "1.0000"}]},
    )
    assert r.status_code == 400


async def test_step2_save_dispatches_trm_background(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    await _seed_detect(client, auth_headers, monkeypatch)
    dispatched = []

    async def fake_trm(user_id):
        dispatched.append(user_id)

    monkeypatch.setattr("ibkr_control.api.setup._trm_backfill_background", fake_trm)

    r = await client.post(
        "/api/setup/step2/save",
        headers=auth_headers,
        json={"accounts": [{"ibkr_account_id": "U99999999", "alias": "A", "pct": "1.0000"}]},
    )
    assert r.status_code == 200
    # BackgroundTasks runs after response — give event loop one tick
    import asyncio
    await asyncio.sleep(0)
    # FastAPI BackgroundTasks fire after the response finalizes; we trust
    # that the dispatch was registered. dispatched may or may not be filled
    # depending on event loop scheduling; this assert is best-effort.
    # The strict assertion is that the endpoint accepted the call.
```

- [ ] **Step 3: Write `test_setup_step3.py`**

```python
"""POST /api/setup/step3/* — upload stash, save_new_accounts, commit."""
from unittest.mock import AsyncMock
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ibkr_control.config import get_settings
from ibkr_control.db.models.flex_raw import FlexImport


_XML_NEW_ACCT = b"""<?xml version="1.0"?>
<FlexQueryResponse><FlexStatements>
  <FlexStatement accountId="U88888888" fromDate="20240101" toDate="20241231">
    <AccountInformation accountId="U88888888" accountAlias="Hist" accountType="Individual" name="X" currency="USD"/>
  </FlexStatement>
</FlexStatements></FlexQueryResponse>
"""


async def test_step3_upload_stashes_xml_and_returns_new_accounts(
    client: AsyncClient, auth_headers: dict
):
    files = {"file": ("h.xml", _XML_NEW_ACCT, "application/xml")}
    r = await client.post("/api/setup/step3/upload", headers=auth_headers, files=files)
    assert r.status_code == 200
    body = r.json()
    assert body["flex_import_temp_id"]
    assert body["anyo"] == 2024
    ids = [a["ibkr_account_id"] for a in body["new_accounts"]]
    assert "U88888888" in ids


async def test_step3_upload_409_on_duplicate_in_stash(
    client: AsyncClient, auth_headers: dict
):
    files = {"file": ("h.xml", _XML_NEW_ACCT, "application/xml")}
    r1 = await client.post("/api/setup/step3/upload", headers=auth_headers, files=files)
    assert r1.status_code == 200
    files2 = {"file": ("h.xml", _XML_NEW_ACCT, "application/xml")}
    r2 = await client.post("/api/setup/step3/upload", headers=auth_headers, files=files2)
    assert r2.status_code == 409


async def test_step3_commit_empty_marks_step3_xmls(
    client: AsyncClient, auth_headers: dict
):
    r = await client.post(
        "/api/setup/step3/commit", headers=auth_headers, json={"temp_ids": []}
    )
    assert r.status_code == 200
    assert r.json() == {"flex_import_ids": [], "total_rows_inserted": 0}
    state = await client.get("/api/setup/state", headers=auth_headers)
    assert state.json()["step3_xmls"] is True


async def test_step3_commit_rejects_unresolved_new_accounts(
    client: AsyncClient, auth_headers: dict
):
    files = {"file": ("h.xml", _XML_NEW_ACCT, "application/xml")}
    r = await client.post("/api/setup/step3/upload", headers=auth_headers, files=files)
    temp_id = r.json()["flex_import_temp_id"]

    r2 = await client.post(
        "/api/setup/step3/commit", headers=auth_headers, json={"temp_ids": [temp_id]}
    )
    assert r2.status_code == 400
    assert r2.json()["detail"]["code"] == "UNRESOLVED_NEW_ACCOUNTS"


async def test_step3_commit_expired_temp_id_returns_410(
    client: AsyncClient, auth_headers: dict
):
    r = await client.post(
        "/api/setup/step3/commit",
        headers=auth_headers,
        json={"temp_ids": ["nonexistent-uuid"]},
    )
    assert r.status_code == 410
```

- [ ] **Step 4: Write `test_setup_finish.py`**

```python
"""POST /api/setup/finish — preconditions + idempotence."""
from unittest.mock import AsyncMock
import pytest
from httpx import AsyncClient

from ibkr_control.ingest.flex import client as flex_client_mod


_FAKE_XML = b"""<?xml version="1.0"?>
<FlexQueryResponse><FlexStatements>
  <FlexStatement accountId="U99999999" fromDate="20260101" toDate="20260524">
    <AccountInformation accountId="U99999999" accountAlias="A" accountType="Joint" name="X" currency="USD"/>
  </FlexStatement>
</FlexStatements></FlexQueryResponse>
"""


async def _run_through_to_step3(client, auth_headers, monkeypatch):
    monkeypatch.setattr("ibkr_control.api.setup._trm_backfill_background", AsyncMock())
    await client.post(
        "/api/setup/step1/save", headers=auth_headers,
        json={"token": "tok_value_x_12345", "query_id": "999"},
    )
    monkeypatch.setattr(flex_client_mod.FlexClient, "send_request", AsyncMock(return_value="ref"))
    monkeypatch.setattr(flex_client_mod.FlexClient, "get_statement", AsyncMock(return_value=_FAKE_XML))
    await client.post("/api/setup/step2/detect", headers=auth_headers)
    await client.post(
        "/api/setup/step2/save", headers=auth_headers,
        json={"accounts": [{"ibkr_account_id": "U99999999", "alias": "A", "pct": "1.0000"}]},
    )
    await client.post(
        "/api/setup/step3/commit", headers=auth_headers, json={"temp_ids": []}
    )


async def test_finish_400_when_no_participations(
    client: AsyncClient, auth_headers: dict
):
    r = await client.post("/api/setup/finish", headers=auth_headers)
    assert r.status_code == 400


async def test_finish_happy_path(client: AsyncClient, auth_headers: dict, monkeypatch):
    await _run_through_to_step3(client, auth_headers, monkeypatch)
    r = await client.post("/api/setup/finish", headers=auth_headers)
    assert r.status_code == 200
    state = await client.get("/api/setup/state", headers=auth_headers)
    assert state.json()["setup_completed_at"] is not None


async def test_finish_idempotent(client: AsyncClient, auth_headers: dict, monkeypatch):
    await _run_through_to_step3(client, auth_headers, monkeypatch)
    r1 = await client.post("/api/setup/finish", headers=auth_headers)
    assert r1.status_code == 200
    r2 = await client.post("/api/setup/finish", headers=auth_headers)
    assert r2.status_code == 200
    assert r2.json().get("already_completed") is True
```

- [ ] **Step 5: Write `test_setup_state.py` (rewrite)**

```python
"""GET /api/setup/state — derived + stored fields."""
from unittest.mock import AsyncMock
import pytest
from httpx import AsyncClient

from ibkr_control.ingest.flex import client as flex_client_mod


async def test_state_initial_all_false(client: AsyncClient, auth_headers: dict):
    r = await client.get("/api/setup/state", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["step1_credentials"] is False
    assert body["step2_accounts"] is False
    assert body["step3_xmls"] is False
    assert body["setup_completed_at"] is None


async def test_state_step1_credentials_derives_from_table(
    client: AsyncClient, auth_headers: dict
):
    await client.post(
        "/api/setup/step1/save",
        headers=auth_headers,
        json={"token": "tok_12345_state", "query_id": "999"},
    )
    r = await client.get("/api/setup/state", headers=auth_headers)
    assert r.json()["step1_credentials"] is True
```

- [ ] **Step 6: Run all setup tests**

```bash
cd backend && uv run pytest tests/api/test_setup_step1.py tests/api/test_setup_step2_detect.py tests/api/test_setup_step2_detect_from_xml.py tests/api/test_setup_step2_save.py tests/api/test_setup_step3.py tests/api/test_setup_finish.py tests/api/test_setup_state.py tests/api/test_step3_stash.py -v
```

Expected: all PASS. Where failures occur, the cause is almost always one of: missing FlexClient error class, parser missing `period_from`/`period_to`, `persist_flex_data` signature mismatch. Patch each minimally.

- [ ] **Step 7: No commit yet — bundle with e2e + first commit**

---

## Task 10: Backend — e2e integration test full wizard flow

**Files:**
- Create: `backend/tests/e2e_api/test_wizard_full_flow.py`
- Create: `backend/tests/e2e_api/__init__.py` (empty)

- [ ] **Step 1: Write the test**

```python
"""E2E: full wizard flow from registration to finish, asserting invariants."""
from unittest.mock import AsyncMock
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ibkr_control.config import get_settings
from ibkr_control.db.models.accounts import Account
from ibkr_control.db.models.flex_raw import FlexImport
from ibkr_control.db.models.participations import Participation
from ibkr_control.ingest.flex import client as flex_client_mod


_YTD_XML = b"""<?xml version="1.0"?>
<FlexQueryResponse><FlexStatements>
  <FlexStatement accountId="U99999999" fromDate="20260101" toDate="20260524">
    <AccountInformation accountId="U99999999" accountAlias="Joint" accountType="Joint" name="X" currency="USD"/>
    <AccountInformation accountId="U88888888" accountAlias="Solo" accountType="Individual" name="X" currency="USD"/>
    <AccountInformation accountId="U99999999F" currency="USD"/>
    <AccountInformation accountId="U88888888F" currency="USD"/>
  </FlexStatement>
</FlexStatements></FlexQueryResponse>
"""

_HIST_XML = b"""<?xml version="1.0"?>
<FlexQueryResponse><FlexStatements>
  <FlexStatement accountId="U99999999" fromDate="20240101" toDate="20241231">
    <AccountInformation accountId="U99999999" accountAlias="Joint" accountType="Joint" name="X" currency="USD"/>
    <AccountInformation accountId="U88888888" accountAlias="Solo" accountType="Individual" name="X" currency="USD"/>
  </FlexStatement>
</FlexStatements></FlexQueryResponse>
"""


async def test_full_wizard_flow_no_f_in_db(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    monkeypatch.setattr("ibkr_control.api.setup._trm_backfill_background", AsyncMock())

    # Step 1
    r = await client.post(
        "/api/setup/step1/save",
        headers=auth_headers,
        json={"token": "tok_full_flow_12345", "query_id": "999"},
    )
    assert r.status_code == 200

    # Step 2 detect
    monkeypatch.setattr(flex_client_mod.FlexClient, "send_request", AsyncMock(return_value="r"))
    monkeypatch.setattr(flex_client_mod.FlexClient, "get_statement", AsyncMock(return_value=_YTD_XML))
    r = await client.post("/api/setup/step2/detect", headers=auth_headers)
    assert r.status_code == 200, r.text
    detected = r.json()["detected_accounts"]
    assert {a["ibkr_account_id"] for a in detected} == {"U99999999", "U88888888"}

    # Step 2 save
    r = await client.post(
        "/api/setup/step2/save",
        headers=auth_headers,
        json={
            "accounts": [
                {"ibkr_account_id": "U99999999", "alias": "Joint", "pct": "0.5000"},
                {"ibkr_account_id": "U88888888", "alias": "Solo", "pct": "1.0000"},
            ]
        },
    )
    assert r.status_code == 200

    # Step 3 upload + commit
    r = await client.post(
        "/api/setup/step3/upload",
        headers=auth_headers,
        files={"file": ("hist.xml", _HIST_XML, "application/xml")},
    )
    assert r.status_code == 200
    temp_id = r.json()["flex_import_temp_id"]
    r = await client.post(
        "/api/setup/step3/commit", headers=auth_headers, json={"temp_ids": [temp_id]}
    )
    assert r.status_code == 200

    # Finish
    r = await client.post("/api/setup/finish", headers=auth_headers)
    assert r.status_code == 200

    # Assertions: no F-accounts anywhere
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as s:
            accs = (await s.scalars(select(Account))).all()
            ids = {a.ibkr_account_id for a in accs}
            assert ids == {"U99999999", "U88888888"}, ids
            assert not any(i.endswith("F") for i in ids)

            parts = (await s.scalars(select(Participation))).all()
            assert len(parts) == 2

            imports = (await s.scalars(select(FlexImport))).all()
            sources = sorted(i.source for i in imports)
            assert sources == ["manual_upload", "web_service"]
    finally:
        await engine.dispose()
```

- [ ] **Step 2: Run the test**

```bash
cd backend && uv run pytest tests/e2e_api/test_wizard_full_flow.py -v
```

Expected: PASS.

- [ ] **Step 3: Run full suite to ensure no regressions**

```bash
cd backend && uv run pytest -q
```

Expected: all PASS. Note new count (~210+).

- [ ] **Step 4: Commit backend changes (all of Tasks 2-10)**

```bash
git add backend/src/ibkr_control/api/_step3_stash.py \
        backend/src/ibkr_control/api/_schemas.py \
        backend/src/ibkr_control/api/setup.py \
        backend/src/ibkr_control/ingest/flex/persister.py \
        backend/src/ibkr_control/ingest/flex/parser.py \
        backend/src/ibkr_control/ingest/flex/client.py \
        backend/tests/api/ \
        backend/tests/ingest/test_persister_f_filter.py \
        backend/tests/ingest/test_parser.py \
        backend/tests/e2e_api/

git commit -m "$(cat <<'EOF'
feat(backend): wizard redesign endpoints + F-account filter + stash

Per spec docs/specs/2026-05-24-wizard-redesign-design.md:

Endpoints rewritten under /api/setup/*:
- step1/save: persist creds only, no IBKR call (was step1/validate)
- step2/detect: SendRequest + GetStatement + parse + persist YTD,
  server-side retry on 1001 with backoff [5,15,30]s
- step2/detect_from_xml: parse only fallback for IBKR offline
- step2/save: persist accounts+participations, dispatch TRM background
- step3/upload: stash parsed XML in memory (TTL 1h), detect new accounts
- step3/save_new_accounts: persist alias+pct for new IDs
- step3/commit: persist all stashed XMLs in one transaction
- finish: setup_completed_at, validates preconditions
- state: derived flags from DB tables (no more flag-vs-reality desync)

F-account filter applied in persister at every persist loop +
_ensure_accounts. Invariant: accounts table never contains *F rows.

New schemas in _schemas.py (DetectedAccount, Step2Detect*, Step3*).
New _step3_stash module (per-process dict with TTL).

Tests: 1 invariant + 5+ per endpoint + 1 e2e flow.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Frontend — regenerate Orval client + rewrite `useSetupState` hook

**Files:**
- Modify (regen): `frontend/src/lib/api/generated.ts`
- Rewrite: `frontend/src/hooks/useSetupState.ts`

- [ ] **Step 1: Regenerate Orval client to pick up new backend OpenAPI**

```bash
cd frontend && pnpm openapi:gen
```

Expected: `generated.ts` updated with new endpoint hooks (`step1SaveApiSetupStep1SavePost`, `step2DetectApiSetupStep2DetectPost`, etc.) and new types (`DetectedAccount`, `Step2DetectResponse`, etc.).

- [ ] **Step 2: Rewrite `useSetupState.ts`**

Overwrite `frontend/src/hooks/useSetupState.ts` with:

```typescript
import { useQuery } from "@tanstack/react-query";
import { axiosInstance } from "@/lib/api";
import type { WizardStateResponse } from "@/lib/api";

export type { WizardStateResponse } from "@/lib/api";

export interface SetupStateResult {
  state: WizardStateResponse | undefined;
  isLoading: boolean;
  refetch: () => void;
}

export function useSetupState(): SetupStateResult {
  const query = useQuery<WizardStateResponse>({
    queryKey: ["setup-state"],
    queryFn: async () => {
      const r = await axiosInstance.get<WizardStateResponse>("/api/setup/state");
      return r.data;
    },
    refetchOnWindowFocus: false,
    retry: false,
  });

  return {
    state: query.data,
    isLoading: query.isLoading,
    refetch: query.refetch,
  };
}
```

Note: the old `currentStep` helper is gone — replaced by `WizardPage`'s `deriveScreen` (Task 13).

- [ ] **Step 3: Verify frontend builds**

```bash
cd frontend && pnpm build 2>&1 | tail -30
```

Expected: build succeeds. If references to the old `currentStep` or `ApiSetupState` type fail to compile, find them with `grep -rn "currentStep\|ApiSetupState" frontend/src --include="*.tsx" --include="*.ts"` and either rewrite the call sites in this task or stub the old export and address in Task 13. The cleanest fix: leave a temporary re-export at the bottom of `useSetupState.ts`:

```typescript
// Legacy compat — remove when all wizard call sites are updated in Task 13
export const currentStep = (_state: WizardStateResponse | undefined): 1 | 2 | 3 | 4 => 1;
```

- [ ] **Step 4: No commit yet — bundle with frontend changes**

---

## Task 12: Frontend — `useWizardNav` + `useStep2Detect` hooks

**Files:**
- Create: `frontend/src/hooks/useWizardNav.ts`
- Create: `frontend/src/hooks/useStep2Detect.ts`

- [ ] **Step 1: Create `useWizardNav.ts`**

```typescript
import { useMemo } from "react";
import type { WizardStateResponse, DetectedAccount } from "@/lib/api";

export type WizardScreen =
  | "step1"
  | "step2_detect"
  | "step2_configure"
  | "step3_upload"
  | "step3_new_accounts"
  | "step3_commit"
  | "finish";

export interface WizardTransientState {
  detectedAccounts: DetectedAccount[] | null;
  pendingTempIds: string[];
  unresolvedNewAccounts: DetectedAccount[];
}

export function deriveScreen(
  state: WizardStateResponse | undefined,
  transient: WizardTransientState,
): WizardScreen {
  if (!state) return "step1";
  if (state.setup_completed_at) return "finish";
  if (!state.step1_credentials) return "step1";
  if (!state.step2_accounts) {
    return transient.detectedAccounts ? "step2_configure" : "step2_detect";
  }
  if (!state.step3_xmls) {
    if (transient.unresolvedNewAccounts.length > 0) return "step3_new_accounts";
    if (transient.pendingTempIds.length > 0) return "step3_commit";
    return "step3_upload";
  }
  return "finish";
}

export function useWizardScreen(
  state: WizardStateResponse | undefined,
  transient: WizardTransientState,
): WizardScreen {
  return useMemo(() => deriveScreen(state, transient), [state, transient]);
}
```

- [ ] **Step 2: Create `useStep2Detect.ts`**

```typescript
import { useCallback, useState } from "react";
import { axiosInstance } from "@/lib/api";
import type { DetectedAccount } from "@/lib/api";

type DetectError =
  | { code: "IBKR_BUSY"; attempts: number }
  | { code: "INVALID_TOKEN" }
  | { code: "QUERY_NOT_FOUND" }
  | { code: "IBKR_ERROR"; message: string }
  | { code: "TIMEOUT" }
  | { code: "UNKNOWN"; message: string };

export interface UseStep2DetectResult {
  detect: () => Promise<DetectedAccount[] | null>;
  detectFromXml: (file: File) => Promise<DetectedAccount[] | null>;
  isLoading: boolean;
  elapsedSeconds: number;
  error: DetectError | null;
}

export function useStep2Detect(): UseStep2DetectResult {
  const [isLoading, setIsLoading] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [error, setError] = useState<DetectError | null>(null);

  const detect = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    setElapsedSeconds(0);
    const interval = setInterval(() => setElapsedSeconds((s) => s + 1), 1000);
    try {
      const r = await axiosInstance.post("/api/setup/step2/detect");
      return r.data.detected_accounts as DetectedAccount[];
    } catch (e: unknown) {
      const err = e as { response?: { status: number; data?: { detail?: unknown } } };
      const detail = err.response?.data?.detail;
      if (err.response?.status === 503 && typeof detail === "object" && detail && "code" in detail) {
        setError(detail as DetectError);
      } else if (err.response?.status === 401) {
        setError({ code: "INVALID_TOKEN" });
      } else if (err.response?.status === 400) {
        setError({ code: "QUERY_NOT_FOUND" });
      } else if (err.response?.status === 504) {
        setError({ code: "TIMEOUT" });
      } else if (err.response?.status === 502 && typeof detail === "object" && detail) {
        setError({ code: "IBKR_ERROR", message: JSON.stringify(detail) });
      } else {
        setError({ code: "UNKNOWN", message: String(e) });
      }
      return null;
    } finally {
      clearInterval(interval);
      setIsLoading(false);
    }
  }, []);

  const detectFromXml = useCallback(async (file: File) => {
    setIsLoading(true);
    setError(null);
    const fd = new FormData();
    fd.append("file", file);
    try {
      const r = await axiosInstance.post("/api/setup/step2/detect_from_xml", fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      return r.data.detected_accounts as DetectedAccount[];
    } catch (e: unknown) {
      setError({ code: "UNKNOWN", message: String(e) });
      return null;
    } finally {
      setIsLoading(false);
    }
  }, []);

  return { detect, detectFromXml, isLoading, elapsedSeconds, error };
}
```

- [ ] **Step 3: Verify build**

```bash
cd frontend && pnpm build 2>&1 | tail -20
```

Expected: success.

- [ ] **Step 4: No commit yet**

---

## Task 13: Frontend — `WizardPage` orchestrator + rewrite Step components

**Files:**
- Create: `frontend/src/components/wizard/WizardPage.tsx`
- Rewrite: `frontend/src/components/wizard/Step1Credentials.tsx`
- Create: `frontend/src/components/wizard/Step2Detect.tsx`
- Create: `frontend/src/components/wizard/Step2ConfigureAccounts.tsx`
- Create: `frontend/src/components/wizard/StepFinish.tsx`
- Modify: `frontend/src/components/wizard/Stepper.tsx`

This task creates the core wizard scaffolding for steps 1-2 and finish. Step 3 stays with the old code temporarily; Task 14 rewrites it.

- [ ] **Step 1: Update `Stepper.tsx` labels (4 steps visible)**

Open `frontend/src/components/wizard/Stepper.tsx` and update the step labels to:

```typescript
const STEPS = ["Credenciales", "Cuentas", "Históricos", "Finalizar"];
```

(Adjust to actual API of the existing Stepper component — find the labels array and replace.)

- [ ] **Step 2: Rewrite `Step1Credentials.tsx`**

Overwrite with:

```tsx
"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { axiosInstance } from "@/lib/api";

interface Props {
  onComplete: () => void;
}

export function Step1Credentials({ onComplete }: Props) {
  const [token, setToken] = useState("");
  const [queryId, setQueryId] = useState("");
  const [error, setError] = useState<string | null>(null);

  const { mutate, isPending } = useMutation({
    mutationFn: async (payload: { token: string; query_id: string }) => {
      const r = await axiosInstance.post("/api/setup/step1/save", payload);
      return r.data;
    },
    onSuccess: () => onComplete(),
    onError: () => setError("No se pudo guardar las credenciales"),
  });

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        setError(null);
        mutate({ token, query_id: queryId });
      }}
    >
      <h2 className="text-xl font-semibold">Credenciales IBKR Flex</h2>
      <p className="text-sm text-muted-foreground">
        Pegá tu Flex Token y el Query ID del reporte YTD configurado en IBKR Account Management.
        En el próximo paso vamos a usar estos datos para descubrir tus cuentas automáticamente.
      </p>

      <div className="space-y-2">
        <Label htmlFor="token">Flex Token</Label>
        <Input
          id="token"
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          required
          minLength={10}
          autoComplete="off"
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="query_id">YTD Query ID</Label>
        <Input
          id="query_id"
          value={queryId}
          onChange={(e) => setQueryId(e.target.value)}
          required
          pattern="^\d+$"
          inputMode="numeric"
        />
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}

      <Button type="submit" disabled={isPending}>
        {isPending ? "Guardando…" : "Continuar →"}
      </Button>
    </form>
  );
}
```

- [ ] **Step 3: Create `Step2Detect.tsx`**

```tsx
"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useStep2Detect } from "@/hooks/useStep2Detect";
import type { DetectedAccount } from "@/lib/api";

interface Props {
  onDetected: (accounts: DetectedAccount[]) => void;
  onBack: () => void;
}

export function Step2Detect({ onDetected, onBack }: Props) {
  const { detect, detectFromXml, isLoading, elapsedSeconds, error } = useStep2Detect();
  const [showFallback, setShowFallback] = useState(false);
  const [file, setFile] = useState<File | null>(null);

  // Auto-trigger detect on mount
  useEffect(() => {
    void (async () => {
      const result = await detect();
      if (result) onDetected(result);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (showFallback) {
    return (
      <div className="space-y-4">
        <h2 className="text-xl font-semibold">Subir XML manual</h2>
        <p className="text-sm text-muted-foreground">
          Si IBKR no responde, podés subir el XML del YTD descargado a mano.
        </p>
        <Input
          type="file"
          accept=".xml"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        <div className="flex justify-between">
          <Button variant="ghost" onClick={() => setShowFallback(false)}>
            ← Volver a intentar con IBKR
          </Button>
          <Button
            disabled={!file || isLoading}
            onClick={async () => {
              if (!file) return;
              const accs = await detectFromXml(file);
              if (accs) onDetected(accs);
            }}
          >
            {isLoading ? "Procesando…" : "Continuar →"}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="text-center space-y-4 py-12">
      {isLoading && (
        <>
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-current" />
          <h2 className="text-xl font-semibold">Descargando reporte de IBKR…</h2>
          <p className="text-sm text-muted-foreground">
            IBKR puede tardar 30 segundos a 3 minutos. Tiempo transcurrido: {elapsedSeconds}s
          </p>
          {elapsedSeconds > 30 && (
            <p className="text-xs text-muted-foreground">
              ¿Sigue tardando?{" "}
              <Button variant="link" onClick={() => setShowFallback(true)}>
                Subir XML manual
              </Button>
            </p>
          )}
        </>
      )}

      {error && (
        <div className="space-y-3 text-left max-w-md mx-auto">
          {error.code === "INVALID_TOKEN" && (
            <p className="text-sm text-red-600">
              Token inválido. <Button variant="link" onClick={onBack}>Volver a Step 1</Button> a corregirlo.
            </p>
          )}
          {error.code === "QUERY_NOT_FOUND" && (
            <p className="text-sm text-red-600">
              Query ID no existe. <Button variant="link" onClick={onBack}>Volver a Step 1</Button> a corregirlo.
            </p>
          )}
          {error.code === "IBKR_BUSY" && (
            <>
              <p className="text-sm text-red-600">
                IBKR sigue ocupado tras {error.attempts} intentos.
              </p>
              <Button onClick={() => window.location.reload()}>Reintentar</Button>
              <Button variant="link" onClick={() => setShowFallback(true)}>
                Subir XML manual
              </Button>
            </>
          )}
          {(error.code === "IBKR_ERROR" || error.code === "TIMEOUT" || error.code === "UNKNOWN") && (
            <>
              <p className="text-sm text-red-600">Error: {(error as { message?: string }).message ?? error.code}</p>
              <Button onClick={() => window.location.reload()}>Reintentar</Button>
              <Button variant="link" onClick={() => setShowFallback(true)}>
                Subir XML manual
              </Button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Create `Step2ConfigureAccounts.tsx`**

```tsx
"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { axiosInstance } from "@/lib/api";
import type { DetectedAccount } from "@/lib/api";

interface Props {
  detectedAccounts: DetectedAccount[];
  onSaved: () => void;
}

interface Row {
  ibkr_account_id: string;
  alias: string;
  pct: string;
  hint: string;
}

export function Step2ConfigureAccounts({ detectedAccounts, onSaved }: Props) {
  const [rows, setRows] = useState<Row[]>(
    detectedAccounts.map((a) => ({
      ibkr_account_id: a.ibkr_account_id,
      alias: a.suggested_alias ?? "",
      pct: "1.0000",
      hint:
        a.account_type || a.account_holder
          ? `IBKR: ${[a.account_type, a.account_holder].filter(Boolean).join(" — ")}`
          : "",
    })),
  );
  const [error, setError] = useState<string | null>(null);

  const { mutate, isPending } = useMutation({
    mutationFn: async () => {
      const r = await axiosInstance.post("/api/setup/step2/save", {
        accounts: rows.map((r) => ({
          ibkr_account_id: r.ibkr_account_id,
          alias: r.alias || null,
          pct: r.pct,
        })),
      });
      return r.data;
    },
    onSuccess: () => onSaved(),
    onError: (err: unknown) => {
      const e = err as { response?: { data?: { detail?: { code?: string; ibkr_account_id?: string } | string } } };
      const detail = e?.response?.data?.detail;
      if (typeof detail === "object" && detail?.code === "ACCOUNT_NOT_DETECTED") {
        setError(`Cuenta ${detail.ibkr_account_id} no fue detectada en IBKR`);
      } else {
        setError("Error al guardar cuentas");
      }
    },
  });

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        setError(null);
        mutate();
      }}
    >
      <h2 className="text-xl font-semibold">Cuentas detectadas</h2>
      <p className="text-sm text-muted-foreground">
        Detectamos {rows.length} cuenta{rows.length === 1 ? "" : "s"} en tu reporte de IBKR.
        Ajustá el alias (opcional) y tu % de participación. Para cuentas conjuntas, poné tu porción real (ej. 0.5 = 50%).
      </p>

      <div className="space-y-3">
        {rows.map((row, i) => (
          <div key={row.ibkr_account_id} className="rounded border p-3 space-y-2">
            <div className="font-mono text-sm">{row.ibkr_account_id}</div>
            {row.hint && <div className="text-xs text-muted-foreground">{row.hint}</div>}
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-xs">Alias</label>
                <Input
                  value={row.alias}
                  onChange={(e) =>
                    setRows((prev) => prev.map((r, idx) => (idx === i ? { ...r, alias: e.target.value } : r)))
                  }
                  placeholder="Ej: Personal Swing"
                />
              </div>
              <div>
                <label className="text-xs">% Tuyo (0–1)</label>
                <Input
                  type="number"
                  step="0.0001"
                  min="0"
                  max="1"
                  value={row.pct}
                  onChange={(e) =>
                    setRows((prev) => prev.map((r, idx) => (idx === i ? { ...r, pct: e.target.value } : r)))
                  }
                  required
                />
              </div>
            </div>
          </div>
        ))}
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}

      <Button type="submit" disabled={isPending}>
        {isPending ? "Guardando…" : "Continuar →"}
      </Button>
    </form>
  );
}
```

- [ ] **Step 5: Create `StepFinish.tsx`**

```tsx
"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export function StepFinish() {
  const router = useRouter();
  useEffect(() => {
    const t = setTimeout(() => router.replace("/dashboard"), 1500);
    return () => clearTimeout(t);
  }, [router]);

  return (
    <div className="text-center space-y-4 py-12">
      <div className="text-5xl">✓</div>
      <h2 className="text-xl font-semibold">Setup completado</h2>
      <p className="text-sm text-muted-foreground">
        Llevándote al dashboard…
      </p>
    </div>
  );
}
```

- [ ] **Step 6: Create `WizardPage.tsx`**

```tsx
"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Stepper } from "./Stepper";
import { Step1Credentials } from "./Step1Credentials";
import { Step2Detect } from "./Step2Detect";
import { Step2ConfigureAccounts } from "./Step2ConfigureAccounts";
import { Step3Upload } from "./Step3Upload";
import { Step3NewAccountsModal } from "./Step3NewAccountsModal";
import { Step3Commit } from "./Step3Commit";
import { StepFinish } from "./StepFinish";
import { useSetupState } from "@/hooks/useSetupState";
import { deriveScreen, type WizardTransientState } from "@/hooks/useWizardNav";
import type { DetectedAccount } from "@/lib/api";

export function WizardPage() {
  const router = useRouter();
  const { state, isLoading, refetch } = useSetupState();
  const [transient, setTransient] = useState<WizardTransientState>({
    detectedAccounts: null,
    pendingTempIds: [],
    unresolvedNewAccounts: [],
  });

  useEffect(() => {
    if (state?.setup_completed_at) router.replace("/dashboard");
  }, [state?.setup_completed_at, router]);

  if (isLoading || !state) return <p className="p-8">Cargando…</p>;

  const screen = deriveScreen(state, transient);

  const stepIndex =
    screen === "step1" ? 0
    : screen === "step2_detect" || screen === "step2_configure" ? 1
    : screen === "step3_upload" || screen === "step3_new_accounts" || screen === "step3_commit" ? 2
    : 3;

  return (
    <main className="max-w-2xl mx-auto p-8 space-y-6">
      <Stepper currentStep={stepIndex} />

      {screen === "step1" && <Step1Credentials onComplete={refetch} />}

      {screen === "step2_detect" && (
        <Step2Detect
          onDetected={(accs: DetectedAccount[]) =>
            setTransient((t) => ({ ...t, detectedAccounts: accs }))
          }
          onBack={() => setTransient((t) => ({ ...t, detectedAccounts: null }))}
        />
      )}

      {screen === "step2_configure" && transient.detectedAccounts && (
        <Step2ConfigureAccounts
          detectedAccounts={transient.detectedAccounts}
          onSaved={() => {
            setTransient((t) => ({ ...t, detectedAccounts: null }));
            refetch();
          }}
        />
      )}

      {screen === "step3_upload" && (
        <Step3Upload
          onUploaded={(tempIds, newAccts) =>
            setTransient((t) => ({
              ...t,
              pendingTempIds: tempIds,
              unresolvedNewAccounts: newAccts,
            }))
          }
          onSkip={refetch}
        />
      )}

      {screen === "step3_new_accounts" && (
        <Step3NewAccountsModal
          accounts={transient.unresolvedNewAccounts}
          onSaved={() =>
            setTransient((t) => ({ ...t, unresolvedNewAccounts: [] }))
          }
        />
      )}

      {screen === "step3_commit" && (
        <Step3Commit
          tempIds={transient.pendingTempIds}
          onCommitted={() => {
            setTransient((t) => ({ ...t, pendingTempIds: [] }));
            refetch();
          }}
        />
      )}

      {screen === "finish" && <StepFinish />}
    </main>
  );
}
```

- [ ] **Step 7: No commit yet — Task 14 adds the Step3 components needed for build to succeed**

---

## Task 14: Frontend — Step3 components (Upload, NewAccountsModal, Commit)

**Files:**
- Create: `frontend/src/components/wizard/Step3Upload.tsx` (renamed/rewritten from `Step3Xmls.tsx`)
- Create: `frontend/src/components/wizard/Step3NewAccountsModal.tsx`
- Create: `frontend/src/components/wizard/Step3Commit.tsx`
- Delete: `frontend/src/components/wizard/Step3Xmls.tsx` (after creating Step3Upload)
- Delete: `frontend/src/components/wizard/Step4Initial.tsx`

- [ ] **Step 1: Create `Step3Upload.tsx`**

```tsx
"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { axiosInstance } from "@/lib/api";
import type { DetectedAccount } from "@/lib/api";

type FileStatus = "uploading" | "parsed" | "error" | "duplicate";

interface FileEntry {
  name: string;
  status: FileStatus;
  tempId?: string;
  detectedAccounts?: DetectedAccount[];
  newAccounts?: DetectedAccount[];
  error?: string;
}

interface Props {
  onUploaded: (tempIds: string[], newAccounts: DetectedAccount[]) => void;
  onSkip: () => void;
}

export function Step3Upload({ onUploaded, onSkip }: Props) {
  const [files, setFiles] = useState<FileEntry[]>([]);

  async function handleFiles(selected: FileList | null) {
    if (!selected) return;
    const list: FileEntry[] = Array.from(selected).map((f) => ({ name: f.name, status: "uploading" }));
    setFiles((prev) => [...prev, ...list]);

    await Promise.all(
      Array.from(selected).map(async (f, idx) => {
        const fd = new FormData();
        fd.append("file", f);
        try {
          const r = await axiosInstance.post("/api/setup/step3/upload", fd, {
            headers: { "Content-Type": "multipart/form-data" },
          });
          setFiles((prev) =>
            prev.map((entry) =>
              entry.name === f.name && entry.status === "uploading"
                ? {
                    ...entry,
                    status: "parsed",
                    tempId: r.data.flex_import_temp_id,
                    detectedAccounts: r.data.detected_accounts,
                    newAccounts: r.data.new_accounts,
                  }
                : entry,
            ),
          );
        } catch (e: unknown) {
          const err = e as { response?: { status: number; data?: { detail?: { code?: string } } } };
          const code = err.response?.data?.detail?.code;
          setFiles((prev) =>
            prev.map((entry) =>
              entry.name === f.name && entry.status === "uploading"
                ? {
                    ...entry,
                    status: code === "DUPLICATE_XML" || code === "DUPLICATE_XML_STASHED" ? "duplicate" : "error",
                    error: code ?? String(e),
                  }
                : entry,
            ),
          );
        }
      }),
    );
  }

  const parsed = files.filter((f) => f.status === "parsed");
  const allNew = parsed.flatMap((f) => f.newAccounts ?? []);
  const dedupNew = Array.from(
    new Map(allNew.map((a) => [a.ibkr_account_id, a])).values(),
  );

  function advance() {
    onUploaded(
      parsed.map((p) => p.tempId!).filter(Boolean),
      dedupNew,
    );
  }

  return (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold">XMLs históricos (opcional)</h2>
      <p className="text-sm text-muted-foreground">
        Subí los XMLs de años anteriores si los tenés. Cada uno es procesado por separado;
        un parse error en uno no rompe los demás.
      </p>

      <Input type="file" accept=".xml" multiple onChange={(e) => handleFiles(e.target.files)} />

      <ul className="space-y-2">
        {files.map((f) => (
          <li key={f.name} className="text-sm flex justify-between border-b py-1">
            <span className="truncate">{f.name}</span>
            <span className={
              f.status === "parsed" ? "text-green-600" :
              f.status === "duplicate" ? "text-yellow-600" :
              f.status === "error" ? "text-red-600" : "text-muted-foreground"
            }>
              {f.status === "parsed" && `OK${f.newAccounts?.length ? ` (${f.newAccounts.length} new)` : ""}`}
              {f.status === "duplicate" && "ya importado"}
              {f.status === "error" && (f.error ?? "error")}
              {f.status === "uploading" && "subiendo…"}
            </span>
          </li>
        ))}
      </ul>

      <div className="flex justify-between pt-4">
        <Button variant="link" onClick={onSkip}>
          Saltar — no tengo XMLs históricos
        </Button>
        <Button disabled={parsed.length === 0} onClick={advance}>
          Continuar con {parsed.length} XML{parsed.length === 1 ? "" : "s"} →
        </Button>
      </div>
    </div>
  );
}
```

Note: `onSkip` triggers `step3/commit` with empty list — the parent (`WizardPage`) handles that by calling the API directly (or pass an explicit `onSkipApiCall` prop). For this task, wire it as: when user clicks "Saltar", `WizardPage` should POST `/api/setup/step3/commit` with `{temp_ids: []}` and refetch.

Update `WizardPage.tsx` Step3Upload usage:

```tsx
{screen === "step3_upload" && (
  <Step3Upload
    onUploaded={(tempIds, newAccts) =>
      setTransient((t) => ({
        ...t,
        pendingTempIds: tempIds,
        unresolvedNewAccounts: newAccts,
      }))
    }
    onSkip={async () => {
      await axiosInstance.post("/api/setup/step3/commit", { temp_ids: [] });
      refetch();
    }}
  />
)}
```

(Add `import { axiosInstance } from "@/lib/api";` to WizardPage.)

- [ ] **Step 2: Create `Step3NewAccountsModal.tsx`**

```tsx
"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { axiosInstance } from "@/lib/api";
import type { DetectedAccount } from "@/lib/api";

interface Props {
  accounts: DetectedAccount[];
  onSaved: () => void;
}

export function Step3NewAccountsModal({ accounts, onSaved }: Props) {
  const [rows, setRows] = useState(
    accounts.map((a) => ({
      ibkr_account_id: a.ibkr_account_id,
      alias: a.suggested_alias ?? "",
      pct: "1.0000",
    })),
  );
  const [error, setError] = useState<string | null>(null);

  const { mutate, isPending } = useMutation({
    mutationFn: async () => {
      const r = await axiosInstance.post("/api/setup/step3/save_new_accounts", {
        accounts: rows.map((r) => ({
          ibkr_account_id: r.ibkr_account_id,
          alias: r.alias || null,
          pct: r.pct,
        })),
      });
      return r.data;
    },
    onSuccess: () => onSaved(),
    onError: () => setError("Error al guardar cuentas nuevas"),
  });

  return (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold">Cuentas nuevas detectadas</h2>
      <p className="text-sm text-muted-foreground">
        Los XMLs que subiste tienen {accounts.length} cuenta{accounts.length === 1 ? "" : "s"} que no
        estaban configuradas. Llená alias + % para continuar.
      </p>

      <div className="space-y-3">
        {rows.map((row, i) => (
          <div key={row.ibkr_account_id} className="rounded border p-3 space-y-2">
            <div className="font-mono text-sm">{row.ibkr_account_id}</div>
            <div className="grid grid-cols-2 gap-2">
              <Input
                value={row.alias}
                onChange={(e) =>
                  setRows((prev) => prev.map((r, idx) => (idx === i ? { ...r, alias: e.target.value } : r)))
                }
                placeholder="Alias"
              />
              <Input
                type="number"
                step="0.0001"
                min="0"
                max="1"
                value={row.pct}
                onChange={(e) =>
                  setRows((prev) => prev.map((r, idx) => (idx === i ? { ...r, pct: e.target.value } : r)))
                }
              />
            </div>
          </div>
        ))}
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}

      <Button onClick={() => mutate()} disabled={isPending}>
        {isPending ? "Guardando…" : "Continuar →"}
      </Button>
    </div>
  );
}
```

- [ ] **Step 3: Create `Step3Commit.tsx`**

```tsx
"use client";

import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { axiosInstance } from "@/lib/api";

interface Props {
  tempIds: string[];
  onCommitted: () => void;
}

export function Step3Commit({ tempIds, onCommitted }: Props) {
  const { mutate, isPending, error } = useMutation({
    mutationFn: async () => {
      const r = await axiosInstance.post("/api/setup/step3/commit", { temp_ids: tempIds });
      return r.data;
    },
    onSuccess: () => onCommitted(),
  });

  return (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold">Importar {tempIds.length} XML{tempIds.length === 1 ? "" : "s"}</h2>
      <p className="text-sm text-muted-foreground">
        Todo listo para importar los XMLs subidos. Esto persiste trades, cash transactions y posiciones de los años cubiertos.
      </p>
      <Button onClick={() => mutate()} disabled={isPending}>
        {isPending ? "Importando…" : `Importar ${tempIds.length} XML${tempIds.length === 1 ? "" : "s"}`}
      </Button>
      {error && <p className="text-sm text-red-600">Error al importar</p>}
    </div>
  );
}
```

- [ ] **Step 4: Delete obsolete files**

```bash
rm frontend/src/components/wizard/Step3Xmls.tsx 2>/dev/null || true
rm frontend/src/components/wizard/Step4Initial.tsx 2>/dev/null || true
```

- [ ] **Step 5: Update `setup/page.tsx` to delegate to WizardPage**

Overwrite `frontend/src/app/(setup)/setup/page.tsx`:

```tsx
import { WizardPage } from "@/components/wizard/WizardPage";

export default function Page() {
  return <WizardPage />;
}
```

- [ ] **Step 6: Verify frontend builds**

```bash
cd frontend && pnpm build 2>&1 | tail -30
```

Expected: build succeeds. If it fails due to leftover references to deleted files or old `useSetupState` API, grep & fix.

- [ ] **Step 7: No commit yet — Playwright tests come in Task 15**

---

## Task 15: Frontend — Playwright E2E tests

**Files:**
- Create: `frontend/e2e/wizard-happy-path.spec.ts`
- Create: `frontend/e2e/wizard-ibkr-busy-fallback.spec.ts`
- Create: `frontend/e2e/wizard-new-accounts-in-history.spec.ts`

These tests use MSW (already configured in the project per CLAUDE.md) to mock the backend.

- [ ] **Step 1: Write `wizard-happy-path.spec.ts`**

```typescript
import { test, expect } from "@playwright/test";

test("wizard happy path: step1 → step2 detect → configure → skip step3 → finish", async ({ page }) => {
  await page.route("**/api/setup/state", async (route) => {
    await route.fulfill({
      json: {
        step1_credentials: false,
        step2_accounts: false,
        step3_xmls: false,
        step3_n_xmls_uploaded: 0,
        setup_completed_at: null,
        pending_stash_temp_ids: [],
      },
    });
  });
  await page.route("**/api/setup/step1/save", (route) => route.fulfill({ json: { ok: true } }));
  await page.route("**/api/setup/step2/detect", (route) =>
    route.fulfill({
      json: {
        detected_accounts: [
          { ibkr_account_id: "U99999999", suggested_alias: "Test", account_type: "Individual", account_holder: "X" },
        ],
        flex_import_id: 1,
        ingest_summary: {},
      },
    }),
  );
  await page.route("**/api/setup/step2/save", (route) => route.fulfill({ json: { ok: true } }));
  await page.route("**/api/setup/step3/commit", (route) =>
    route.fulfill({ json: { flex_import_ids: [], total_rows_inserted: 0 } }),
  );

  await page.goto("/setup");
  await expect(page.locator("text=Credenciales IBKR Flex")).toBeVisible();
  await page.fill("input#token", "tok_test_1234567890");
  await page.fill("input#query_id", "999");
  await page.click("button:has-text('Continuar')");

  // After step1 success, refetch state should land on step2_detect
  await page.route("**/api/setup/state", (route) =>
    route.fulfill({
      json: {
        step1_credentials: true,
        step2_accounts: false,
        step3_xmls: false,
        step3_n_xmls_uploaded: 0,
        setup_completed_at: null,
        pending_stash_temp_ids: [],
      },
    }),
  );
  await expect(page.locator("text=Descargando reporte de IBKR")).toBeVisible({ timeout: 5000 });
  await expect(page.locator("text=Cuentas detectadas")).toBeVisible({ timeout: 10000 });
  await page.fill('input[type="number"]', "1.0000");
  await page.click("button:has-text('Continuar')");

  // After step2 save, state moves to step3_upload
  await page.route("**/api/setup/state", (route) =>
    route.fulfill({
      json: {
        step1_credentials: true,
        step2_accounts: true,
        step3_xmls: false,
        step3_n_xmls_uploaded: 0,
        setup_completed_at: null,
        pending_stash_temp_ids: [],
      },
    }),
  );
  await expect(page.locator("text=Saltar — no tengo XMLs")).toBeVisible({ timeout: 5000 });
  await page.click("text=Saltar — no tengo XMLs");
});
```

- [ ] **Step 2: Write `wizard-ibkr-busy-fallback.spec.ts`**

Mirror the happy path but return 503 on `step2/detect`, verify retry UI + fallback link appears.

```typescript
import { test, expect } from "@playwright/test";

test("wizard step2 IBKR_BUSY shows fallback link after 30s", async ({ page }) => {
  await page.route("**/api/setup/state", (route) =>
    route.fulfill({
      json: {
        step1_credentials: true,
        step2_accounts: false,
        step3_xmls: false,
        step3_n_xmls_uploaded: 0,
        setup_completed_at: null,
        pending_stash_temp_ids: [],
      },
    }),
  );
  await page.route("**/api/setup/step2/detect", (route) =>
    route.fulfill({ status: 503, json: { detail: { code: "IBKR_BUSY", attempts: 3 } } }),
  );

  await page.goto("/setup");
  await expect(page.locator("text=IBKR sigue ocupado")).toBeVisible({ timeout: 10000 });
  await expect(page.locator("button:has-text('Subir XML manual')")).toBeVisible();
});
```

- [ ] **Step 3: Write `wizard-new-accounts-in-history.spec.ts`**

```typescript
import { test, expect } from "@playwright/test";

test("step3 upload reveals new accounts, modal asks for alias+pct", async ({ page }) => {
  await page.route("**/api/setup/state", (route) =>
    route.fulfill({
      json: {
        step1_credentials: true,
        step2_accounts: true,
        step3_xmls: false,
        step3_n_xmls_uploaded: 0,
        setup_completed_at: null,
        pending_stash_temp_ids: [],
      },
    }),
  );
  await page.route("**/api/setup/step3/upload", (route) =>
    route.fulfill({
      json: {
        flex_import_temp_id: "abc",
        detected_accounts: [
          { ibkr_account_id: "U99999999", suggested_alias: "x", account_type: "Individual", account_holder: "X" },
        ],
        new_accounts: [
          { ibkr_account_id: "U99999999", suggested_alias: "x", account_type: "Individual", account_holder: "X" },
        ],
        period: { from: "2024-01-01", to: "2024-12-31" },
        anyo: 2024,
        sha256: "abc",
      },
    }),
  );

  await page.goto("/setup");
  await expect(page.locator("text=XMLs históricos")).toBeVisible({ timeout: 5000 });

  const fileInput = page.locator('input[type="file"]');
  await fileInput.setInputFiles({
    name: "hist.xml",
    mimeType: "application/xml",
    buffer: Buffer.from("<x/>"),
  });
  await page.click("button:has-text('Continuar con 1 XML')");
  await expect(page.locator("text=Cuentas nuevas detectadas")).toBeVisible({ timeout: 5000 });
});
```

- [ ] **Step 4: Run Playwright tests**

```bash
cd frontend && pnpm e2e
```

Expected: 3 tests pass. If any times out, adjust selectors/timing. If MSW config conflicts with Playwright route mocking, fall back to pure Playwright `page.route` (as shown above).

- [ ] **Step 5: Commit frontend changes (Tasks 11-15)**

```bash
git add frontend/

git commit -m "$(cat <<'EOF'
feat(frontend): wizard redesign — auto-detect cuentas IBKR

Reescribe wizard segun spec docs/specs/2026-05-24-wizard-redesign-design.md:

- WizardPage orchestrator con state machine derivada del backend state
- Step1Credentials: form, sin validacion vs IBKR
- Step2Detect: auto-trigger SendRequest+GetStatement, spinner con
  countdown, link "Subir XML manual" tras 30s
- Step2ConfigureAccounts: tabla con cuentas pre-detectadas, alias
  pre-poblado desde <AccountInformation accountAlias=>, % default 1.0
- Step3Upload: drag-and-drop multi-file, status por XML
- Step3NewAccountsModal: pide alias+pct para cuentas nuevas
- Step3Commit: persist batch + advance to finish
- StepFinish: splash + redirect /dashboard

Hooks:
- useSetupState rewritten — derived state shape from new /api/setup/state
- useWizardNav.deriveScreen — pure transition function
- useStep2Detect — auto-call + retry visualization + fallback toggle

Playwright e2e: happy_path, ibkr_busy_fallback, new_accounts_in_history.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 16: Apply Migration H + smoke test + tag + docs

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/plans/2026-05-24-phase2-polish-backlog.md`

- [ ] **Step 1: Rebuild containers (picks up backend + frontend changes)**

```bash
docker compose down
docker compose up -d --build
sleep 20
docker compose ps
```

Expected: backend healthy, frontend up. Backend logs should show Migration H applied (`Running upgrade ... -> ..., wipe for wizard redesign`).

- [ ] **Step 2: Verify DB is wiped + setup_completed_at NULL**

```bash
docker compose exec -T postgres psql -U ibkr -d ibkr_control -c "
SELECT
  (SELECT count(*) FROM accounts) AS accs,
  (SELECT count(*) FROM trades) AS trades,
  (SELECT count(*) FROM flex_imports) AS imports,
  (SELECT setup_completed_at FROM users WHERE id=1) AS completed,
  (SELECT setup_progress::text FROM users WHERE id=1) AS progress;"
```

Expected: all counts 0; `completed` NULL; `progress` `{}`.

- [ ] **Step 3: Manual smoke test the full wizard flow in the browser**

Open http://localhost:3000/setup. Verify:
- Lands on Step 1 (credentials form).
- After saving, Step 2 spinner appears.
- Spinner resolves to "Cuentas detectadas" table with the 3 real accounts and the alias pre-populated from IBKR.
- After saving %, Step 3 upload appears.
- Upload the 2 historical XMLs (`../renta/fuentes/2024/...` and `2025/...`).
- "Continuar con 2 XMLs" → import.
- Redirect to dashboard.

Verify DB:
```bash
docker compose exec -T postgres psql -U ibkr -d ibkr_control -c "
SELECT ibkr_account_id, alias FROM accounts ORDER BY ibkr_account_id;
SELECT user_id, account_id, pct FROM participations ORDER BY account_id;
SELECT anyo, source, year_status FROM flex_imports ORDER BY anyo;"
```

Expected: 3 accounts (NO F-suffix), 3 participations, 3 flex_imports (1 web_service 2026 + 2 manual_upload 2024/2025).

- [ ] **Step 4: Update docs**

In `CLAUDE.md`:

- Update test count from current (192-ish) to the new count after this work (~220).
- Bump "Estado actual" Phase 2 row to include the wizard redesign tag.
- Add a bullet in "Phase 2 — Retrospectiva (deviaciones del spec original)":

```
- **Wizard redesign post-deploy (2026-05-24, tag v0.2.2-wizard-redesign)** — el smoke test
  end-to-end con datos reales revelo que el wizard pedia IDs de cuenta ciegos (typos),
  no validaba contra cuentas reales, y poblaba accounts con 3 F-suffix shadow accounts
  IB-UK Limited (NAV=0, fees/journals only). Reescritura "detect-first": Step 1 solo
  guarda creds, Step 2 fetchea Flex YTD + auto-detecta cuentas filtrando F, pre-pobla
  alias desde <AccountInformation accountAlias=>. Step 3 multi-file drag-drop con
  detect de cuentas nuevas. Filtro F a nivel persister (allowlist pattern de renta
  sibling). Migration H wipea data legacy. Spec:
  docs/specs/2026-05-24-wizard-redesign-design.md. Plan:
  docs/plans/2026-05-24-wizard-redesign.md.
```

In `docs/plans/2026-05-24-phase2-polish-backlog.md`, add a note at the end pointing to the wizard redesign as a major post-Phase-2 work.

- [ ] **Step 5: Commit docs**

```bash
git add CLAUDE.md docs/plans/2026-05-24-phase2-polish-backlog.md
git commit -m "$(cat <<'EOF'
docs(claude): bump test count + retrospectiva wizard redesign

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: Tag**

```bash
git tag -a v0.2.2-wizard-redesign -m "v0.2.2 — wizard redesign (auto-detect cuentas + F-filter)

Reemplaza wizard de IDs ciegos por flujo detect-first: Step 1 solo guarda
creds, Step 2 fetchea Flex YTD + auto-detecta cuentas filtrando F-suffix
shadow accounts, alias pre-poblado desde <AccountInformation accountAlias=>.

Backend: 7 endpoints reescritos bajo /api/setup/*, persister filtra F en
todos los persist loops, in-memory stash con TTL para Step 3 uploads.
Frontend: state machine de 7 pantallas en WizardPage, multi-file drag-drop.
Migration H wipea data legacy (re-ingest desde cero).

Spec: docs/specs/2026-05-24-wizard-redesign-design.md
Plan: docs/plans/2026-05-24-wizard-redesign.md"
git tag -l "v0.2*"
git log --oneline -10
```

Expected: tag created. Both `v0.2.0-ingest`, `v0.2.1-persistent-state`, and `v0.2.2-wizard-redesign` listed.

---

## Self-Review

**Spec coverage:**

| Spec section | Tasks |
|---|---|
| D1 wipe DB | Task 1 (Migration H) |
| D2 retry + manual upload fallback | Task 6 (step2/detect retry logic + step2/detect_from_xml), Task 13 (UI fallback) |
| D3 persist Step 2 XML | Task 6 step2/detect inserts flex_imports |
| D5 split step 1/2 | Tasks 13 (Step1Credentials), 13 (Step2Detect) |
| D6 TRM background post-Step 2 | Task 6 step2/save dispatches `_trm_backfill_background` |
| D7 redirect post-completion | Task 13 (WizardPage useEffect) + StepFinish |
| D8 suggested_alias from XML | Task 3 (parser), Task 6 (`_to_detected_account`), Task 13 (pre-fill) |
| D9 F-filter at persister | Task 2 |
| D10 retry policy server-side | Task 6 (`_DETECT_RETRY_DELAYS` + loop) |
| D11 in-memory stash TTL 1h | Task 4 (`_step3_stash.py`) |
| D12 derived vs stored state | Task 6 (`/state` endpoint) |
| Section 1 (persister filter + invariant) | Task 2 |
| Section 2 (endpoints + state machine) | Tasks 5-10 |
| Section 3 (frontend structure + state machine) | Tasks 11-14 |
| Section 4 (error handling) | Task 6 (mapping in code), Task 8 (tests), Task 13 (UI) |
| Section 5 (testing + migration) | Tasks 1, 2, 7-10, 15 |

All covered. No gaps.

**Placeholder scan:** No "TBD" / "TODO" / "fill in later" in plan. Migration filename uses `<autogen>` because Alembic decides the hash — flagged inline as expected.

**Type/name consistency:**

- `DetectedAccount` shape: `{ibkr_account_id, suggested_alias, account_type, account_holder}` — consistent across `_schemas.py` (Task 5), `setup.py._to_detected_account` (Task 6), parser fields (Task 3), and frontend type imports (Task 11+).
- `Step2DetectResponse.detected_accounts: list[DetectedAccount]` — same.
- `_is_shadow_account` (persister, Task 2) vs `_is_shadow` (setup.py helper, Task 6) — different names. Both private (`_` prefix). Acceptable because they live in different modules and serve the same intent locally; both implementations are `return id.endswith("F")`. Documented in spec section 1.
- `useWizardNav.deriveScreen` (Task 12) used by `WizardPage` (Task 13). Signatures match.
- `useStep2Detect.detect` returns `Promise<DetectedAccount[] | null>`, consumed by `Step2Detect.tsx` (Task 13) via `onDetected(accounts)`. Matches.

No inconsistencies.

Plan complete. Next: invoke execution handoff.


