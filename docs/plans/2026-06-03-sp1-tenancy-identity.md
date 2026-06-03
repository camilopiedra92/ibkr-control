# SP1 — Tenancy & Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the data model as a multi-tenant SaaS foundation — Organizations + Parties + Memberships + party-scoped access grants, with `organization_id` on every tenant table enforced by Postgres Row-Level Security, on a fresh squashed Alembic baseline (DB wipe).

**Architecture:** Tenancy = `organizations` (personal|firm). Ownership = `account ↔ party` (party = fiscal person, separate from `users` = login). Isolation = `organization_id` denormalized on every tenant table + RLS policies keyed on a per-transaction `SET LOCAL app.current_org`; the app connects as a non-bypass DB role with `FORCE ROW LEVEL SECURITY`. Identity tables (users/organizations/memberships) sit *above* org-RLS (read during context resolution). TRM is global. Cross-org accountant access is data-shaped here (the `access_grants` table) but enforced in SP2.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x async, asyncpg, Alembic, Postgres 16 RLS, FastAPI, pytest + testcontainers.

**Spec:** `docs/specs/2026-06-03-sp1-tenancy-identity-design.md` (read it first).

---

## ⏯ Execution status (paused 2026-06-03 — branch `saas/sp1-tenancy-identity`, 22 commits)

**Foundation DONE + reviewed + drift-clean + RLS isolation PROVEN.** Paused before the (large, mechanical) app-layer green-up — to be resumed in a fresh session for context quality.

**Done (✅):**
- **Tasks 1-7** — identity models (Organization, Membership, Party, AccessGrant) + refactors (participations party-anchored, flex_credentials per-org, `organization_id` + index on all 15 org-scoped tables). Review fixes applied: `AccessGrant.role` server_default; `counterparties` UNIQUE → `(organization_id, external_id)` (per-org, not global); org_id index on parties/participations.
- **Old authz/grants layer removed** (was planned as Task 19 Step 1, pulled forward right after Task 4 to avoid carrying `= None` stubs): deleted `authz/` package, `api/grants.py`, the `main.py` mount, and the 4 authz/grants test files. Nothing kept used `require_account_scope`/`visible_account_ids` (S9 confirmed).
- **Tasks 8-9** — squashed baseline `05943d9efcdb` (`down_revision=None`, 22 tables, apscheduler false-positive avoided) + RLS: `db/rls.py` SSOT (15-table list + policy builders), `app_rls` non-bypass login role, 16 policies + `FORCE` on every org-scoped table + `access_grants` special policy. Review fix: `NULLIF(current_setting(...,true),'')::bigint` so unset context → clean default-deny (0 rows) instead of 22P02 error; explicit `WITH CHECK` on access_grants.
- **Task 10** — structural tests (migrations/naming/table_comments) target the new baseline (drift test green; `test_migrations` uses dynamic head + ignores apscheduler).
- **Task 11** — RLS smoke suite (`tests/test_rls.py`, 4 tests, `rls_session_factory` connecting as `app_rls`): default-deny, org A ≠ org B isolation, no-bypass, access_grants grantor/grantee visibility. **Cross-tenant isolation proven.**
- **Tasks 12-13** — `api/_context.py`: `apply_org_context` (SET LOCAL, pooling-safe), `resolve_current_org_id` (membership-based, default-deny 403), `org_context` FastAPI dependency.
- **Task 15** — persister stamps `organization_id` on FlexImport + all facts + flex_import_accounts + ensure_accounts/counterparties; dedup + R1 cleanup scope by org. Follow-up fix: `flex_imports` dedup UNIQUE → `(organization_id, xml_hash)`, `user_id` nullable audit (per-org idempotency); `flex_import_accounts` comment de-staled.
- **Task 17** — `scripts/provision_org.py` (`provision_org()` + CLI): org + user (PasswordHelper hash) + UserSettings + Membership(owner) + Party.

**✅ CONVERGENCE COMPLETE (2026-06-03) — SP1 done, suite 337 green under RLS, ready to finish branch.** All convergence tasks landed + each two-stage reviewed (spec + code quality) + a final holistic review (all 6 SP1 invariants HOLD). Commits on `saas/sp1-tenancy-identity`:
- ✅ **Task 18** (`676f552`) — `conftest.py` fixtures around org/party (`sample_org`/`sample_party`/`auth_headers_with_org`; `sample_user` faithful identity incl. UserSettings; DRY DSN/alembic helpers). 22 fixture errors → 0.
- ✅ **Task 16** (`f5b56b3`) — wizard org-context + party-anchored participations; setup-state User→Organization; `_founding_party_id`. `tests/api/test_setup_*` green.
- ✅ **Task 19a** (`caa1c8d`) — ingest write-path org-aware (`ingest/log.py`, `hash_dedup.py`, `flex/job.py`; lock param `user_id`→`scope_id`).
- ✅ **Task 19b** (`51fb293`) — TRM control-plane out of `ingest_log` (D-CONV-1, plain-tx + failure-path test) + Flex cron per-org (D-CONV-2).
- ✅ **Task 19c** (`55d582a`) — endpoints org-aware (credentials/imports/ingest/health; health TRM←`trm_imports`, Flex←`ingest_log`).
- ✅ **Org-pure purge / 19c.1** (`9884d90`, D-CONV-3) — dropped `flex_imports.user_id` + `ingest_log.user_id`(+index); throttle `users`→`organizations`; explicit org selection (400 `ORG_SELECTION_REQUIRED`); `user_id` audit threading removed from ingest path. Baseline EDITED in place (drift-clean).
- ✅ **Task 19d** (`e83cfb1`) — full suite green: adapted persister/ephemeral tests (+org_id), retired obsolete migration-mechanics (Phase 2.8 triage), boot smoke OK.
- ✅ **Task 14a** (`a2e0453`) — endpoint suite runs UNDER RLS as `app_rls` (per-test migrated DB). Surfaced+fixed a real latent bug: `imports.py` re-pull after `ingest_xml`'s internal commit ran context-less → default-deny → fixed by re-applying context. `alembic/env.py` logger fix.
- ✅ **Task 14b** (`a4ffdb5`) — `flex_job.run` self-sets org RLS context (manual-bg path was default-denied under `app_rls`); `apply_org_context` handles no-user system context (`''` not `'None'`, closes a 22P02 landmine); relocated to `db/rls.py` (fixed the only `ingest/`→`api/` import).
- ✅ **Final-review cleanups** (`3b86ad3`) — honest doc of the cron org-enumeration RLS gap (SP7) + dropped a dead re-export.

**Known documented gaps (NOT bugs — explicit SP boundaries):** (1) the cron's cross-tenant org-enumeration default-denies under `app_rls` → needs a system/bypass connection = **SP7** (the per-org `flex_job.run` IS RLS-correct, proven by `test_job_rls.py`); (2) `accounts.ibkr_account_id` global-unique → a broker account is single-org by design (cross-org ingest fails on integrity without existence leak; graceful generic error = SP7); (3) grant enforcement = SP2. Per-test migrated containers make the RLS endpoint suite ~210s (correctness over speed; optional future optimization).

**Next:** `superpowers:finishing-a-development-branch`.

---

## Key implementation decisions (read before starting)

- **Squashed baseline.** Delete the 3 existing migrations (`cbeaac94933d`, `eb5ef6d36e06`, `8d69e795a517`). Generate ONE new baseline (`down_revision=None`) from the final models, then hand-add RLS/roles DDL. DB wipe is authorized — no reversibility, no data preservation.
- **Two DB roles.** Migrations/DDL run as the Postgres superuser (the container/Coolify owner). The *app* connects as a dedicated **non-superuser, non-owner** role `app_rls` so RLS actually applies. The baseline migration creates `app_rls`, grants it CRUD on tenant tables, and sets `FORCE ROW LEVEL SECURITY` so even table owners are subject to policies.
- **`SET LOCAL` per transaction.** The session-context dependency emits `SET LOCAL app.current_org` + `SET LOCAL app.current_user` inside each request's transaction. `LOCAL` auto-resets on commit/rollback — mandatory with pooling.
- **Test infra runs under RLS (world-class).** `app_with_db` switches from `Base.metadata.create_all` to `alembic upgrade head` + connecting as `app_rls` + auto-setting org context, so the whole endpoint suite exercises RLS. A new `rls_session` fixture connects as `app_rls` for direct RLS smoke tests. The `db_session` fixture (model-level DDL checks) stays on the owner connection + `create_all` for tests that don't need RLS.
- **Org-RLS table set** (these get `organization_id` NOT NULL + standard RLS policy): `accounts`, `parties`, `participations`, `flex_credentials`, `counterparties`, `flex_imports`, `flex_import_accounts`, `trades`, `closed_lots`, `open_position_lots`, `transfers`, `cash_transactions`, `change_in_dividend_accruals`, `open_dividend_accruals`, `ingest_log`.
- **Special policy:** `access_grants` (grantor-org OR grantee org/user can see it).
- **Above org-RLS** (identity/context-resolution, app-enforced + optional current_user policy): `users`, `organizations`, `memberships`.
- **Global, no RLS:** `trm_days`, `trm_imports`. **Unchanged, user-scoped:** `user_settings`.
- **Generate migrations canonically inside the backend container** (`docker compose exec backend ...`); the host can't reach postgres. Remove the `apscheduler_jobs` autogenerate false-positive (Phase 2.9 lesson).
- The old Phase 2.8 `data_access_grants` model + `authz/` resolver are **removed** (superseded by `access_grants` + SP2). The H1 anti-IDOR scoping in `api/setup.py` is **replaced** by RLS + party-anchored ownership.

---

## File structure

**Create:**
- `backend/src/ibkr_control/db/models/organizations.py` — `Organization`
- `backend/src/ibkr_control/db/models/memberships.py` — `Membership`
- `backend/src/ibkr_control/db/models/parties.py` — `Party`
- `backend/src/ibkr_control/db/models/access_grants.py` — `AccessGrant` (replaces `grants.py`)
- `backend/src/ibkr_control/db/rls.py` — central list of org-scoped tables + policy SQL builders (single source of truth, reused by migration + tests)
- `backend/src/ibkr_control/api/_context.py` — `set_org_context` request dependency (`SET LOCAL`)
- `backend/scripts/provision_org.py` — bootstrap an org + founding user + party + membership
- `backend/alembic/versions/<rev>_saas_baseline.py` — the squashed baseline (generated + hand-edited)
- `backend/tests/test_rls.py` — RLS smoke tests
- `backend/tests/test_provision_org.py` — provisioning script test

**Modify:**
- `backend/src/ibkr_control/db/models/accounts.py` — + `organization_id`
- `backend/src/ibkr_control/db/models/participations.py` — `user_id` → `party_id`, + `organization_id`
- `backend/src/ibkr_control/db/models/flex_credentials.py` — per-user → per-org (surrogate PK + `organization_id`)
- `backend/src/ibkr_control/db/models/flex_raw.py` — + `organization_id` on all 9 tables
- `backend/src/ibkr_control/db/models/counterparties.py`, `ingest_log.py` — + `organization_id`
- `backend/src/ibkr_control/auth/models.py` — `User` loses `setup_progress`/`setup_completed_at`
- `backend/src/ibkr_control/db/__init__.py` — register new models, drop `DataAccessGrant`
- `backend/src/ibkr_control/ingest/flex/persister.py` — stamp `organization_id`
- `backend/src/ibkr_control/api/setup.py` — org context + party-anchored participations
- `backend/tests/conftest.py` — fixtures rebuilt around org/party + `app_with_db` under RLS
- `backend/tests/conftest_ephemeral_db.py` — add an `app_rls`-connecting variant

**Delete:**
- `backend/src/ibkr_control/db/models/grants.py`, `backend/src/ibkr_control/authz/` (whole package)
- `backend/alembic/versions/cbeaac94933d_*.py`, `eb5ef6d36e06_*.py`, `8d69e795a517_*.py`
- Tests bound to the removed authz/grants + old migration revisions (enumerated in Task 19)

---

## Wave 0 — Identity models

### Task 1: `Organization` model

**Files:**
- Create: `backend/src/ibkr_control/db/models/organizations.py`
- Modify: `backend/src/ibkr_control/db/__init__.py`
- Test: `backend/tests/test_identity_models.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_identity_models.py
import pytest
from sqlalchemy import select
from ibkr_control.db.models.organizations import Organization


@pytest.mark.asyncio
async def test_organization_persists_with_type(db_session):
    org = Organization(type="personal", name="Hogar Test")
    db_session.add(org)
    await db_session.flush()
    got = await db_session.scalar(select(Organization).where(Organization.id == org.id))
    assert got.type == "personal"
    assert got.name == "Hogar Test"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_identity_models.py::test_organization_persists_with_type -q`
Expected: FAIL — `ModuleNotFoundError: ibkr_control.db.models.organizations`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/ibkr_control/db/models/organizations.py
"""Organization = tenant boundary (personal household | accounting firm)."""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint("type IN ('personal', 'firm')", name="type"),
        {"comment": "Tenant boundary. type personal=hogar, firm=estudio contable."},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # Setup/wizard state moved here from users (setup is per-org).
    setup_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    setup_progress: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
```

Then add to `backend/src/ibkr_control/db/__init__.py` (import + `__all__`):
```python
from ibkr_control.db.models.organizations import Organization  # noqa: F401
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_identity_models.py::test_organization_persists_with_type -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ibkr_control/db/models/organizations.py backend/src/ibkr_control/db/__init__.py backend/tests/test_identity_models.py
git commit -m "feat(sp1): Organization model (tenant boundary)"
```

### Task 2: `Membership` model

**Files:**
- Create: `backend/src/ibkr_control/db/models/memberships.py`
- Modify: `backend/src/ibkr_control/db/__init__.py`
- Test: `backend/tests/test_identity_models.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_membership_links_user_to_org(db_session):
    from ibkr_control.auth.models import User
    from ibkr_control.db.models.memberships import Membership
    from ibkr_control.db.models.organizations import Organization

    u = User(email="m@t.com", hashed_password="x", is_active=True, name="M")
    org = Organization(type="personal", name="H")
    db_session.add_all([u, org])
    await db_session.flush()
    db_session.add(Membership(user_id=u.id, organization_id=org.id, role="owner"))
    await db_session.flush()
    got = await db_session.scalar(
        select(Membership).where(Membership.user_id == u.id, Membership.organization_id == org.id)
    )
    assert got.role == "owner"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_identity_models.py::test_membership_links_user_to_org -q`
Expected: FAIL — `ModuleNotFoundError: ibkr_control.db.models.memberships`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/ibkr_control/db/models/memberships.py
"""Membership = user belongs to an organization with a role."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, PrimaryKeyConstraint, String, text
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "organization_id"),
        CheckConstraint("role IN ('owner', 'admin', 'member')", name="role"),
        {"comment": "User<->org con rol. Identidad; sin org-RLS (se lee para resolver contexto)."},
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
```

Add to `db/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_identity_models.py::test_membership_links_user_to_org -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ibkr_control/db/models/memberships.py backend/src/ibkr_control/db/__init__.py backend/tests/test_identity_models.py
git commit -m "feat(sp1): Membership model (user<->org role)"
```

### Task 3: `Party` model

**Files:**
- Create: `backend/src/ibkr_control/db/models/parties.py`
- Modify: `backend/src/ibkr_control/db/__init__.py`
- Test: `backend/tests/test_identity_models.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_party_optionally_links_to_user(db_session):
    from ibkr_control.db.models.organizations import Organization
    from ibkr_control.db.models.parties import Party

    org = Organization(type="personal", name="H")
    db_session.add(org)
    await db_session.flush()
    # Party with NO user (the non-login spouse / firm client).
    p = Party(organization_id=org.id, display_name="Cónyuge", tax_id="999", user_id=None)
    db_session.add(p)
    await db_session.flush()
    got = await db_session.scalar(select(Party).where(Party.id == p.id))
    assert got.user_id is None
    assert got.display_name == "Cónyuge"
    assert got.organization_id == org.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_identity_models.py::test_party_optionally_links_to_user -q`
Expected: FAIL — `ModuleNotFoundError: ibkr_control.db.models.parties`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/ibkr_control/db/models/parties.py
"""Party = fiscal/legal person (taxpayer). Owns accounts. Separate from User (login)."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class Party(Base):
    __tablename__ = "parties"
    __table_args__ = {
        "comment": (
            "Persona fiscal (contribuyente). Duena de cuentas via participations. "
            "Separada de User: puede no tener login (conyuge, cliente del estudio). "
            "Org-scoped (organization_id, RLS)."
        )
    }

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    tax_id: Mapped[str | None] = mapped_column(String, nullable=True)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
```

Add to `db/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_identity_models.py::test_party_optionally_links_to_user -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ibkr_control/db/models/parties.py backend/src/ibkr_control/db/__init__.py backend/tests/test_identity_models.py
git commit -m "feat(sp1): Party model (fiscal person, 0..1 user link)"
```

### Task 4: `AccessGrant` model (replaces `grants.py`)

**Files:**
- Create: `backend/src/ibkr_control/db/models/access_grants.py`
- Delete: `backend/src/ibkr_control/db/models/grants.py`
- Modify: `backend/src/ibkr_control/db/__init__.py` (drop `DataAccessGrant`, add `AccessGrant`)
- Test: `backend/tests/test_identity_models.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_access_grant_exclusive_grantee_arc(db_session):
    from sqlalchemy.exc import IntegrityError
    from ibkr_control.db.models.access_grants import AccessGrant
    from ibkr_control.db.models.organizations import Organization
    from ibkr_control.db.models.parties import Party

    org = Organization(type="personal", name="H")
    firm = Organization(type="firm", name="Estudio")
    db_session.add_all([org, firm])
    await db_session.flush()
    p = Party(organization_id=org.id, display_name="Owner")
    db_session.add(p)
    await db_session.flush()

    # Both grantees NULL violates the exclusive arc.
    db_session.add(
        AccessGrant(
            grantor_party_id=p.id,
            grantee_organization_id=None,
            grantee_user_id=None,
            organization_id=org.id,
            role="read_only",
            valid_from=__import__("datetime").date(2026, 1, 1),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_identity_models.py::test_access_grant_exclusive_grantee_arc -q`
Expected: FAIL — `ModuleNotFoundError: ibkr_control.db.models.access_grants`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/ibkr_control/db/models/access_grants.py
"""Cross-org delegated read: a Party shares its fiscal data with a firm/user.

Party-scoped (grantor_party_id). Grantee is exactly one of org|user (exclusive
arc). organization_id = the grantor party's org (the household that granted).
RLS on this table is SPECIAL (grantor-org OR grantee can see it) — see db/rls.py.
Enforcement of the grant (who may switch into whose org) is SP2.
"""

from datetime import date, datetime

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class AccessGrant(Base):
    __tablename__ = "access_grants"
    __table_args__ = (
        CheckConstraint(
            "(grantee_organization_id IS NOT NULL) <> (grantee_user_id IS NOT NULL)",
            name="grantee_arc",
        ),
        CheckConstraint("role IN ('read_only')", name="role"),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="valid_range"),
        {"comment": "Grant cross-org party-scoped. RLS especial (grantor-org OR grantee)."},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    grantor_party_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("parties.id", ondelete="CASCADE"), nullable=False
    )
    grantee_organization_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    grantee_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String, nullable=False, default="read_only")
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
```

Delete `grants.py`; in `db/__init__.py` remove the `DataAccessGrant` import + `__all__` entry and add `AccessGrant`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_identity_models.py::test_access_grant_exclusive_grantee_arc -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A backend/src/ibkr_control/db/models/ backend/src/ibkr_control/db/__init__.py backend/tests/test_identity_models.py
git commit -m "feat(sp1): AccessGrant model (party-scoped); drop DataAccessGrant"
```

---

## Wave 1 — Refactor existing models

### Task 5: `participations` party-anchored + move setup state off `User`

**Files:**
- Modify: `backend/src/ibkr_control/db/models/participations.py`, `backend/src/ibkr_control/auth/models.py`
- Test: `backend/tests/test_identity_models.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_participation_is_party_anchored(db_session):
    from datetime import date
    from decimal import Decimal
    from ibkr_control.db.models.accounts import Account
    from ibkr_control.db.models.organizations import Organization
    from ibkr_control.db.models.parties import Party
    from ibkr_control.db.models.participations import Participation

    org = Organization(type="personal", name="H")
    db_session.add(org)
    await db_session.flush()
    party = Party(organization_id=org.id, display_name="Owner")
    acc = Account(ibkr_account_id="U99999001", organization_id=org.id, currency="USD")
    db_session.add_all([party, acc])
    await db_session.flush()
    db_session.add(
        Participation(
            party_id=party.id, account_id=acc.id, organization_id=org.id,
            pct=Decimal("0.5000"), valid_from=date(2026, 1, 1), valid_to=None,
        )
    )
    await db_session.flush()
    got = await db_session.scalar(select(Participation).where(Participation.party_id == party.id))
    assert got.pct == Decimal("0.5000")
    assert not hasattr(got, "user_id")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_identity_models.py::test_participation_is_party_anchored -q`
Expected: FAIL — `TypeError: 'organization_id' is an invalid keyword argument for Participation` (or `party_id` invalid)

- [ ] **Step 3: Write minimal implementation**

Rewrite `participations.py`:
```python
"""Participacion de un Party en un Account (M:N temporal SCD-2)."""

from datetime import date
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Date, ForeignKey, Numeric, PrimaryKeyConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class Participation(Base):
    __tablename__ = "participations"
    __table_args__ = (
        PrimaryKeyConstraint("party_id", "account_id", "valid_from"),
        CheckConstraint("pct >= 0 AND pct <= 1", name="pct_range"),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="valid_range"),
        {"comment": "Propiedad fiscal: Party posee Account con pct (SCD-2). Org-scoped."},
    )

    party_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("parties.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    pct: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
```

In `auth/models.py`, remove the `setup_completed_at` and `setup_progress` columns from `User` (they now live on `Organization`). Leave the rest of `User` intact.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_identity_models.py::test_participation_is_party_anchored -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ibkr_control/db/models/participations.py backend/src/ibkr_control/auth/models.py backend/tests/test_identity_models.py
git commit -m "feat(sp1): participations party-anchored; move setup state to Organization"
```

### Task 6: `flex_credentials` per-org

**Files:**
- Modify: `backend/src/ibkr_control/db/models/flex_credentials.py`
- Test: `backend/tests/test_identity_models.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_flex_credentials_are_org_scoped(db_session):
    from ibkr_control.db.models.flex_credentials import FlexCredentials
    from ibkr_control.db.models.organizations import Organization

    org = Organization(type="personal", name="H")
    db_session.add(org)
    await db_session.flush()
    c = FlexCredentials(organization_id=org.id, token_encrypted=b"x", ytd_query_id="999")
    db_session.add(c)
    await db_session.flush()
    got = await db_session.scalar(select(FlexCredentials).where(FlexCredentials.id == c.id))
    assert got.organization_id == org.id
    assert not hasattr(got, "user_id")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_identity_models.py::test_flex_credentials_are_org_scoped -q`
Expected: FAIL — `'user_id' invalid` / `'organization_id' invalid` mismatch.

- [ ] **Step 3: Write minimal implementation**

Read the current `flex_credentials.py` first, then change the PK from `user_id` to a surrogate `id` + `organization_id` FK (keep `token_encrypted`, `ytd_query_id`, `last_rotated_at`, timestamps). Example shape:
```python
class FlexCredentials(Base):
    __tablename__ = "flex_credentials"
    __table_args__ = {"comment": "Flex token del org (no del user). Org-scoped, RLS."}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    token_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    ytd_query_id: Mapped[str] = mapped_column(String, nullable=False)
    last_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # ...preserve any other existing columns...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_identity_models.py::test_flex_credentials_are_org_scoped -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ibkr_control/db/models/flex_credentials.py backend/tests/test_identity_models.py
git commit -m "feat(sp1): flex_credentials per-org (surrogate PK + organization_id)"
```

### Task 7: Add `organization_id` to accounts + all facts + counterparties + ingest_log

**Files:**
- Modify: `accounts.py`, `counterparties.py`, `ingest_log.py`, `flex_raw.py` (9 tables: `FlexImport`, `FlexImportAccount`, `Trade`, `ClosedLot`, `OpenPositionLot`, `Transfer`, `CashTransaction`, `ChangeInDividendAccrual`, `OpenDividendAccrual`)
- Test: `backend/tests/test_identity_models.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_all_tenant_tables_have_organization_id(db_session):
    from ibkr_control.db.base import Base
    from ibkr_control.db.rls import ORG_SCOPED_TABLES  # created in Task 9 prep

    for table_name in ORG_SCOPED_TABLES:
        table = Base.metadata.tables[table_name]
        assert "organization_id" in table.columns, f"{table_name} missing organization_id"
        assert not table.columns["organization_id"].nullable, f"{table_name}.organization_id nullable"
```

(If `db/rls.py` doesn't exist yet, create it first with just the list — see Task 9 Step 3; the constant is the single source of truth shared by models-check, migration, and tests.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_identity_models.py::test_all_tenant_tables_have_organization_id -q`
Expected: FAIL — first table missing `organization_id`.

- [ ] **Step 3: Write minimal implementation**

Create `backend/src/ibkr_control/db/rls.py` with the table list (full content in Task 9). Then add this column to EACH of the org-scoped tables (accounts, counterparties, ingest_log, and the 9 in flex_raw.py) — the pattern is identical:

```python
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
```

Place it right after the `id` PK column in each model. Add an index on `(organization_id)` to each via `Index(None, "organization_id")` in `__table_args__` (RLS filters every query by it). Do NOT touch the existing natural keys / fact columns.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_identity_models.py::test_all_tenant_tables_have_organization_id -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ibkr_control/db/models/ backend/src/ibkr_control/db/rls.py backend/tests/test_identity_models.py
git commit -m "feat(sp1): organization_id on accounts + facts + counterparties + ingest_log"
```

---

## Wave 2 — Squashed baseline migration + RLS

### Task 8: Generate the squashed baseline

**Files:**
- Delete: `backend/alembic/versions/cbeaac94933d_*.py`, `eb5ef6d36e06_*.py`, `8d69e795a517_*.py`
- Create: `backend/alembic/versions/<rev>_saas_baseline.py`

- [ ] **Step 1: Delete the old chain + generate fresh baseline in the container**

```bash
rm backend/alembic/versions/cbeaac94933d_*.py \
   backend/alembic/versions/eb5ef6d36e06_*.py \
   backend/alembic/versions/8d69e795a517_*.py
docker compose cp backend/src backend:/app/src
docker compose cp backend/alembic/versions backend:/app/alembic/versions
docker compose exec backend sh -c "cd /app && psql \$DATABASE_URL_SYNC -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;' 2>/dev/null; uv run alembic revision --autogenerate -m 'saas baseline'"
docker compose cp "backend:/app/alembic/versions/." backend/alembic/versions/
```

(If `DATABASE_URL_SYNC` isn't set, drop/recreate via the asyncpg URL with a one-off python `-c`, or just `alembic downgrade base` first. The goal: an empty DB so autogenerate emits a from-scratch baseline.)

- [ ] **Step 2: Hand-edit the generated baseline**

Set `down_revision = None`. Remove the `apscheduler_jobs` drop/create false-positive (runtime table, not in `Base.metadata`). Verify every table + the `participations` party PK + `organization_id` columns are present. Leave a `# RLS policies + app_rls role added in Task 9` marker at the end of `upgrade()`.

- [ ] **Step 3: Apply + drift-check in the container**

Run:
```bash
docker compose cp backend/alembic/versions backend:/app/alembic/versions
docker compose exec backend sh -c "cd /app && uv run alembic upgrade head && uv run alembic check 2>&1 | tail -3"
```
Expected: upgrade runs; `alembic check` shows only the `apscheduler_jobs` false-positive (no new-table drift).

- [ ] **Step 4: Commit**

```bash
git add backend/alembic/versions/
git commit -m "feat(sp1): squashed SaaS baseline migration (down_revision=None)"
```

### Task 9: RLS policies + `app_rls` role in the baseline

**Files:**
- Create: `backend/src/ibkr_control/db/rls.py` (finalize)
- Modify: `backend/alembic/versions/<rev>_saas_baseline.py`
- Test: `backend/tests/test_rls.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rls.py
import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_app_role_cannot_bypass_rls(rls_session_factory):
    """With FORCE RLS and no current_org set, the app role sees zero rows."""
    factory, seed = rls_session_factory
    # seed() inserts an Organization + Account as the owner (bypasses RLS).
    org_id, acc_ibkr = await seed()
    async with factory() as s:
        # No SET LOCAL app.current_org -> default-deny.
        n = await s.scalar(text("SELECT count(*) FROM accounts"))
        assert n == 0
        # With the right org context, the row is visible.
        await s.execute(text("SET LOCAL app.current_org = :o").bindparams(o=str(org_id)))
        n2 = await s.scalar(text("SELECT count(*) FROM accounts"))
        assert n2 == 1
```

(The `rls_session_factory` fixture — Task 11 — migrates a fresh DB, creates the `app_rls` role via the migration, seeds rows as owner, and yields a factory that connects as `app_rls`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_rls.py::test_app_role_cannot_bypass_rls -q`
Expected: FAIL — fixture missing / no RLS (sees 1 row without context).

- [ ] **Step 3: Finalize `db/rls.py`**

```python
# backend/src/ibkr_control/db/rls.py
"""Single source of truth for RLS: which tables are org-scoped + policy SQL.

Reused by the baseline migration (to create policies) and by tests (to assert
coverage). Keeps the table list from drifting between schema, migration, tests.
"""

# Tenant tables: organization_id NOT NULL + standard single-org policy.
ORG_SCOPED_TABLES = [
    "accounts",
    "parties",
    "participations",
    "flex_credentials",
    "counterparties",
    "flex_imports",
    "flex_import_accounts",
    "trades",
    "closed_lots",
    "open_position_lots",
    "transfers",
    "cash_transactions",
    "change_in_dividend_accruals",
    "open_dividend_accruals",
    "ingest_log",
]

APP_ROLE = "app_rls"


def standard_policy_sql(table: str) -> list[str]:
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"""CREATE POLICY org_isolation ON {table}
            USING (organization_id = current_setting('app.current_org', true)::bigint)
            WITH CHECK (organization_id = current_setting('app.current_org', true)::bigint)""",
    ]


def access_grants_policy_sql() -> list[str]:
    return [
        "ALTER TABLE access_grants ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE access_grants FORCE ROW LEVEL SECURITY",
        """CREATE POLICY grant_visibility ON access_grants
            USING (
              organization_id = current_setting('app.current_org', true)::bigint
              OR grantee_organization_id = current_setting('app.current_org', true)::bigint
              OR grantee_user_id = current_setting('app.current_user', true)::bigint
            )""",
    ]


def app_role_grants_sql() -> list[str]:
    # app_rls: non-superuser, non-owner. Migrations run as owner; app connects as this.
    return [
        f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{APP_ROLE}') "
        f"THEN CREATE ROLE {APP_ROLE} LOGIN PASSWORD 'app_rls_pw'; END IF; END $$",
        f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}",
    ]
```

(`current_setting(..., true)` = missing-ok → returns NULL when unset → `organization_id = NULL` is never true → default-deny. The `app_rls` password is dev/test only; prod injects a real one — note for SP4/deploy.)

- [ ] **Step 4: Add the DDL to the baseline migration**

At the END of `upgrade()` in the baseline:
```python
    from ibkr_control.db.rls import (
        ORG_SCOPED_TABLES, standard_policy_sql, access_grants_policy_sql, app_role_grants_sql,
    )
    for stmt in app_role_grants_sql():
        op.execute(stmt)
    for table in ORG_SCOPED_TABLES:
        for stmt in standard_policy_sql(table):
            op.execute(stmt)
    for stmt in access_grants_policy_sql():
        op.execute(stmt)
```
(`downgrade()` may stay a no-op `op.drop_table` chain for the wipe baseline; policies drop with their tables.)

- [ ] **Step 5: Run the test (after Task 11 fixture exists, re-run here)**

Run: `cd backend && uv run pytest tests/test_rls.py::test_app_role_cannot_bypass_rls -q`
Expected: PASS once Task 11's fixture is in place. (Tasks 9 and 11 are co-dependent; implement 11's fixture, then this passes. Commit Task 9 DDL first; the test goes green at Task 11.)

- [ ] **Step 6: Commit**

```bash
git add backend/src/ibkr_control/db/rls.py backend/alembic/versions/ backend/tests/test_rls.py
git commit -m "feat(sp1): RLS policies + FORCE + app_rls non-bypass role in baseline"
```

### Task 10: Harden structural tests for the new schema

**Files:**
- Modify: `backend/tests/test_migrations.py`, `backend/tests/test_naming_convention.py`, `backend/tests/test_table_comments.py`

- [ ] **Step 1: Run the existing structural suite, watch it fail**

Run: `cd backend && uv run pytest tests/test_migrations.py tests/test_naming_convention.py tests/test_table_comments.py -q`
Expected: FAIL — old revision references / missing comments on new tables.

- [ ] **Step 2: Update**

Point any hard-coded revision ids at the new baseline. Add the new tables (`organizations`, `memberships`, `parties`, `access_grants`) to the table-comments expectations. Naming-convention test should pass unchanged (convention applies to new FKs/PKs automatically).

- [ ] **Step 3: Run to verify pass**

Run: `cd backend && uv run pytest tests/test_migrations.py tests/test_naming_convention.py tests/test_table_comments.py -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_migrations.py backend/tests/test_naming_convention.py backend/tests/test_table_comments.py
git commit -m "test(sp1): structural tests target SaaS baseline + new tables"
```

---

## Wave 3 — RLS enforcement plumbing

### Task 11: `rls_session_factory` test fixture (connects as `app_rls`)

**Files:**
- Modify: `backend/tests/conftest_ephemeral_db.py`
- Test: re-run `backend/tests/test_rls.py::test_app_role_cannot_bypass_rls`

- [ ] **Step 1: Add the fixture**

```python
# in conftest_ephemeral_db.py
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def rls_session_factory(ephemeral_session_factory, ephemeral_db_url):
    """(factory_as_app_rls, seed) — migrated DB; factory connects as the
    non-bypass app_rls role so RLS applies. seed() inserts a baseline org+account
    as the OWNER (bypasses RLS) and returns (org_id, ibkr_account_id)."""
    owner_factory = ephemeral_session_factory  # owner connection (migrations + seed)

    async def seed():
        from ibkr_control.db.models.accounts import Account
        from ibkr_control.db.models.organizations import Organization

        async with owner_factory() as s:
            org = Organization(type="personal", name="Seed")
            s.add(org)
            await s.flush()
            s.add(Account(ibkr_account_id="U99999001", organization_id=org.id, currency="USD"))
            await s.commit()
            return org.id, "U99999001"

    # Reconnect as app_rls (same DB, different user). asyncpg DSN swap.
    app_url = ephemeral_db_url.split("://", 1)[1]
    creds, hostpart = app_url.split("@", 1)
    app_dsn = f"postgresql+asyncpg://app_rls:app_rls_pw@{hostpart}"
    app_engine = create_async_engine(app_dsn, echo=False)
    app_factory = async_sessionmaker(app_engine, expire_on_commit=False)
    try:
        yield app_factory, seed
    finally:
        await app_engine.dispose()
```

- [ ] **Step 2: Run the Task 9 RLS test, verify it now passes**

Run: `cd backend && uv run pytest tests/test_rls.py -q`
Expected: PASS (default-deny without context; visible with `SET LOCAL app.current_org`).

- [ ] **Step 3: Add cross-org isolation + access_grants policy tests**

```python
@pytest.mark.asyncio
async def test_org_a_cannot_see_org_b_rows(rls_session_factory):
    factory, _seed = rls_session_factory
    # seed two orgs+accounts via owner; assert A's context sees only A.
    # (use ephemeral_session_factory directly inside this test to insert org B)
    ...
```
(Write a concrete two-org seed asserting `current_org=A` returns A's account only and not B's. Mirror for `participations` and one fact table. Add an `access_grants` test: grantor-org and grantee see the grant, a third org does not.)

- [ ] **Step 4: Run, verify pass**

Run: `cd backend && uv run pytest tests/test_rls.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/tests/conftest_ephemeral_db.py backend/tests/test_rls.py
git commit -m "test(sp1): RLS smoke suite — isolation, default-deny, FORCE, access_grants policy"
```

### Task 12: `set_org_context` request dependency

**Files:**
- Create: `backend/src/ibkr_control/api/_context.py`
- Test: `backend/tests/api/test_org_context.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/api/test_org_context.py
import pytest
from sqlalchemy import text
from ibkr_control.api._context import apply_org_context


@pytest.mark.asyncio
async def test_apply_org_context_sets_local_guc(db_session):
    await apply_org_context(db_session, org_id=42, user_id=7)
    org = await db_session.scalar(text("SELECT current_setting('app.current_org', true)"))
    usr = await db_session.scalar(text("SELECT current_setting('app.current_user', true)"))
    assert org == "42"
    assert usr == "7"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/api/test_org_context.py -q`
Expected: FAIL — `ModuleNotFoundError: ibkr_control.api._context`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/ibkr_control/api/_context.py
"""Per-request RLS context. SET LOCAL (transaction-scoped, pooling-safe)."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def apply_org_context(session: AsyncSession, *, org_id: int, user_id: int) -> None:
    # set_config(key, value, is_local=true) == SET LOCAL; parameterized (no SQL injection).
    await session.execute(
        text("SELECT set_config('app.current_org', :o, true), "
             "set_config('app.current_user', :u, true)").bindparams(o=str(org_id), u=str(user_id))
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/api/test_org_context.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ibkr_control/api/_context.py backend/tests/api/test_org_context.py
git commit -m "feat(sp1): apply_org_context — SET LOCAL RLS context (pooling-safe)"
```

### Task 13: Resolve current org from the authenticated user + wire into a dependency

**Files:**
- Modify: `backend/src/ibkr_control/api/_context.py` (add the FastAPI dependency)
- Test: `backend/tests/api/test_org_context.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_current_org_resolves_single_membership(db_session):
    from ibkr_control.api._context import resolve_current_org_id
    from ibkr_control.auth.models import User
    from ibkr_control.db.models.memberships import Membership
    from ibkr_control.db.models.organizations import Organization

    u = User(email="r@t.com", hashed_password="x", is_active=True, name="R")
    org = Organization(type="personal", name="H")
    db_session.add_all([u, org]); await db_session.flush()
    db_session.add(Membership(user_id=u.id, organization_id=org.id, role="owner"))
    await db_session.flush()
    got = await resolve_current_org_id(db_session, user_id=u.id, requested_org_id=None)
    assert got == org.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/api/test_org_context.py::test_current_org_resolves_single_membership -q`
Expected: FAIL — `resolve_current_org_id` undefined.

- [ ] **Step 3: Write minimal implementation**

```python
# add to _context.py
from fastapi import Depends, HTTPException
from sqlalchemy import select
from ibkr_control.auth.backend import current_active_user
from ibkr_control.auth.models import User
from ibkr_control.db.models.memberships import Membership
from ibkr_control.db.session import get_async_session


async def resolve_current_org_id(session, *, user_id: int, requested_org_id: int | None) -> int:
    """The org the request acts in. Default: the user's membership. A requested
    org must be one the user is a member of (cross-org grants = SP2; here we
    only honor own memberships)."""
    rows = (await session.scalars(
        select(Membership.organization_id).where(Membership.user_id == user_id)
    )).all()
    if not rows:
        raise HTTPException(status_code=403, detail="NO_ORG_MEMBERSHIP")
    if requested_org_id is None:
        return rows[0]
    if requested_org_id not in rows:
        raise HTTPException(status_code=403, detail="NOT_A_MEMBER")
    return requested_org_id


async def org_context(
    user: User = Depends(current_active_user),
    session=Depends(get_async_session),
) -> int:
    """FastAPI dependency: resolve org, SET LOCAL the RLS context, return org_id."""
    org_id = await resolve_current_org_id(session, user_id=user.id, requested_org_id=None)
    await apply_org_context(session, org_id=org_id, user_id=user.id)
    return org_id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/api/test_org_context.py::test_current_org_resolves_single_membership -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ibkr_control/api/_context.py backend/tests/api/test_org_context.py
git commit -m "feat(sp1): resolve_current_org_id + org_context dependency"
```

### Task 14: Run `app_with_db` (endpoint suite) under RLS

**Files:**
- Modify: `backend/tests/conftest.py`

- [ ] **Step 1: Write a failing test asserting RLS is active in the endpoint app**

```python
# backend/tests/test_app_runs_under_rls.py
import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_app_session_is_app_rls_role(app_rls_db_session):
    role = await app_rls_db_session.scalar(text("SELECT current_user"))
    assert role == "app_rls"
```

- [ ] **Step 2: Run, verify fail**

Run: `cd backend && uv run pytest tests/test_app_runs_under_rls.py -q`
Expected: FAIL — fixture `app_rls_db_session` missing.

- [ ] **Step 3: Refactor `app_with_db`**

Change `app_with_db` to: (a) run `alembic upgrade head` (not `create_all`) so RLS + `app_rls` exist; (b) build the app's session engine connecting as `app_rls`; (c) override `get_async_session` with a session that, per request, applies org context (for endpoint tests, a fixture seeds an org+membership and the override calls `apply_org_context`). Add an `app_rls_db_session` fixture connecting as `app_rls` for direct assertions. Reuse the DSN-swap helper from Task 11 (extract it to a small module-level function in conftest to stay DRY).

Key change in `app_with_db`:
```python
    # was: async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    await asyncio.to_thread(command.upgrade, alembic_cfg(url), "head")
    app_engine = create_async_engine(app_rls_dsn(url))
    session_maker = async_sessionmaker(app_engine, expire_on_commit=False)
```

- [ ] **Step 4: Run, verify pass**

Run: `cd backend && uv run pytest tests/test_app_runs_under_rls.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/tests/conftest.py backend/tests/test_app_runs_under_rls.py
git commit -m "test(sp1): endpoint suite runs under RLS as app_rls (was create_all/owner)"
```

---

## Wave 4 — Write-paths org/party-aware

### Task 15: Persister stamps `organization_id`

**Files:**
- Modify: `backend/src/ibkr_control/ingest/flex/persister.py`
- Test: `backend/tests/ingest/flex/test_persister.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_persist_stamps_organization_id_on_all_rows(db_session):
    from ibkr_control.db.models.organizations import Organization
    from ibkr_control.db.models.flex_raw import FlexImport, Trade
    from ibkr_control.ingest.flex.persister import persist

    org = Organization(type="personal", name="H")
    db_session.add(org); await db_session.flush()
    parsed = _make_parsed(account_id="U99999001", n_trades=2)  # helper in this file
    fi_id, _ = await persist(
        db_session, parsed=parsed, organization_id=org.id,
        xml_bytes=b"<x/>", source="manual_upload",
    )
    fi = await db_session.scalar(select(FlexImport).where(FlexImport.id == fi_id))
    assert fi.organization_id == org.id
    t = await db_session.scalar(select(Trade).where(Trade.flex_import_id == fi_id))
    assert t.organization_id == org.id
```

- [ ] **Step 2: Run, verify fail**

Run: `cd backend && uv run pytest tests/ingest/flex/test_persister.py::test_persist_stamps_organization_id_on_all_rows -q`
Expected: FAIL — `persist()` has no `organization_id` param / NOT NULL violation.

- [ ] **Step 3: Implement**

Change `persist()` signature from `user_id: int` to `organization_id: int`. Stamp `organization_id` on the `FlexImport`, on every child row dict in `_upsert_all_children` (add `"organization_id": organization_id` to each row builder), on `flex_import_accounts` inserts, and on `_ensure_accounts`/`_ensure_counterparties` (new rows get the org). The H1 hash-dedup fast-path and R1 cleanup queries scope by `organization_id` instead of `user_id`. Remove the now-redundant `flex_import_accounts` anti-IDOR rationale comment (RLS supersedes it) but keep the provenance write.

- [ ] **Step 4: Run, verify pass**

Run: `cd backend && uv run pytest tests/ingest/flex/test_persister.py::test_persist_stamps_organization_id_on_all_rows -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ibkr_control/ingest/flex/persister.py backend/tests/ingest/flex/test_persister.py
git commit -m "feat(sp1): persister stamps organization_id (was user_id-scoped)"
```

### Task 16: Wizard endpoints — org context + party-anchored participations

**Files:**
- Modify: `backend/src/ibkr_control/api/setup.py`
- Test: `backend/tests/api/test_setup_step2_save.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_step2_save_creates_party_anchored_participation(client, auth_headers_with_org, monkeypatch):
    """After save, a Participation exists for the founding party in the user's org."""
    # auth_headers_with_org seeds user+org+membership+founding party (Task 18 fixture).
    ...  # run step1/save + step2/detect (mocked) + step2/save, then assert:
    #     a Participation row with party_id == founding party, organization_id == org
```

- [ ] **Step 2: Run, verify fail**

Run: `cd backend && uv run pytest tests/api/test_setup_step2_save.py::test_step2_save_creates_party_anchored_participation -q`
Expected: FAIL — endpoint still writes `user_id` participations.

- [ ] **Step 3: Implement**

Add `org_id: int = Depends(org_context)` to the wizard endpoints. Replace `user_id`-scoping with `org_id` everywhere (the H1 `_user_imported_account_ids` / `_user_stashed_account_ids` / `_user_configured_account_ids` helpers become org/party-scoped or are dropped where RLS now covers them). Participations write with `party_id` = the org's founding party (SP1 minimal: one party per founding user; multi-party UI = SP3) + `organization_id`. Persist calls pass `organization_id=org_id`. Remove the H1 provenance-based existence scoping that RLS + ownership now supersede; keep validation that the account was detected in this org's imports (now a plain org-scoped query under RLS).

- [ ] **Step 4: Run, verify pass**

Run: `cd backend && uv run pytest tests/api/test_setup_step2_save.py::test_step2_save_creates_party_anchored_participation -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ibkr_control/api/setup.py backend/tests/api/test_setup_step2_save.py
git commit -m "feat(sp1): wizard org-context + party-anchored participations"
```

### Task 17: `provision_org.py` bootstrap script

**Files:**
- Create: `backend/scripts/provision_org.py`
- Test: `backend/tests/test_provision_org.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_provision_org.py
import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_provision_org_creates_org_user_party_membership(db_session):
    from ibkr_control.scripts.provision_org import provision_org
    from ibkr_control.db.models.organizations import Organization
    from ibkr_control.db.models.memberships import Membership
    from ibkr_control.db.models.parties import Party

    result = await provision_org(
        db_session, org_name="Hogar", org_type="personal",
        user_email="owner@t.com", user_password="supersecret123", party_name="Owner",
    )
    org = await db_session.scalar(select(Organization).where(Organization.id == result.org_id))
    assert org.name == "Hogar"
    m = await db_session.scalar(select(Membership).where(Membership.organization_id == org.id))
    assert m.role == "owner"
    p = await db_session.scalar(select(Party).where(Party.organization_id == org.id))
    assert p.user_id == result.user_id
```

- [ ] **Step 2: Run, verify fail**

Run: `cd backend && uv run pytest tests/test_provision_org.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

`provision_org(session, *, org_name, org_type, user_email, user_password, party_name) -> ProvisionResult` (a small dataclass with `org_id`, `user_id`, `party_id`). Creates `Organization`, `User` (password hashed via the existing fastapi-users password helper), `Membership(role="owner")`, `Party(user_id=user.id)`. Add a `__main__` CLI wrapper (argparse) so it runs as `uv run python -m ibkr_control.scripts.provision_org ...`. Place under `src/ibkr_control/scripts/` so it's importable (add `__init__.py`); the `backend/scripts/provision_org.py` thin wrapper just calls it.

- [ ] **Step 4: Run, verify pass**

Run: `cd backend && uv run pytest tests/test_provision_org.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ibkr_control/scripts/ backend/scripts/provision_org.py backend/tests/test_provision_org.py
git commit -m "feat(sp1): provision_org bootstrap (org + user + party + membership)"
```

---

## Wave 5 — Test infra + green suite

### Task 18: Rebuild shared fixtures around org/party

**Files:**
- Modify: `backend/tests/conftest.py`

- [ ] **Step 1: Replace identity fixtures**

Rewrite `sample_user` to also create an org + membership + founding party. Add `sample_org`, `sample_party`, `auth_headers_with_org` (registers a user, provisions an org+membership+party for them, returns JWT headers; the app's `org_context` resolves their single membership). Update `sample_account`, `sample_flex_import` to take an `organization_id`. Extract the DSN-swap + alembic-config helpers used by `app_with_db` and `rls_session_factory` into shared module-level functions (DRY).

- [ ] **Step 2: Run the identity + setup + persister suites**

Run: `cd backend && uv run pytest tests/test_identity_models.py tests/api/test_setup_step2_save.py tests/ingest/flex/test_persister.py -q`
Expected: PASS (fixtures now provide org/party context).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/conftest.py
git commit -m "test(sp1): shared fixtures rebuilt around org/party/membership"
```

### Task 19 (RE-SCOPED 2026-06-03): App-layer org-aware convergence — full green

> **Why re-scoped:** investigating the 106 failures showed the remaining work is an org-aware conversion of ~11 source modules (not "adapt tests"), plus the two convergence decisions D-CONV-1 (TRM control-plane) + D-CONV-2 (cron org-loop) now in the spec. The old `authz/`/`grants` layer was **already removed** earlier (see Execution status), so that step is done. Split into 4 reviewed sub-tasks, executed 19a → 19b → 19c → 19d (19b/19c depend on 19a's job signatures).

#### Task 19a — Ingest write-path core org-aware
**Source:** `ingest/log.py` (`ingest_log_entry` gains `organization_id`, stamps it on the row), `ingest/hash_dedup.py` (`check_hash_status(session, organization_id, hash)` — dedup key `(organization_id, xml_hash)`), `ingest/flex/job.py` (`run`/`ingest_xml`/`_insert_poison_row` take `organization_id`; `FlexImport` write + poison row stamp org; `on_conflict` index → `(organization_id, xml_hash)`; `persist(organization_id=...)`; `FlexCredentials`/`FlexImport` queries org-scoped). Legitimate-unchanged: advisory `lock.py` granularity, `job_tracker` ownership.
**Tests:** `tests/ingest/flex/test_job.py`, `tests/ingest/test_hash_dedup.py`, `tests/ingest/test_log.py`, poison/isolation/replay suites → pass `organization_id=sample_org.id`.

#### Task 19b — Jobs/cron layer (D-CONV-1 + D-CONV-2)
**Source:** `ingest/trm/job.py` — TRM stops calling `ingest_log_entry` entirely (control plane; its record is `trm_imports`). `scheduler/jobs.py` — `_run_flex_for_all_users` → `_run_flex_for_all_orgs` iterating `distinct FlexCredentials.organization_id`, `flex_job.run(organization_id=...)`, `SET LOCAL app.current_org` per iteration; TRM cron unchanged (global).
**Tests:** `tests/ingest/trm/*`, scheduler tests adapted; assert TRM writes `trm_imports` not `ingest_log`.

#### Task 19c — Endpoints org-aware
**Source:** `api/credentials.py`, `api/imports.py`, `api/ingest.py`, `api/health.py` take `org_id = Depends(org_context)`; all `FlexCredentials`/`FlexImport`/`IngestLog` queries org-scoped; `_launch_manual_job`/`flex_job.run`/`ingest_xml` calls pass `organization_id`. `health.py`: **Flex** freshness ← `ingest_log` (org-scoped), **TRM** freshness ← `trm_imports` (global, D-CONV-1). `api/ingest.py` `_run_manual` TRM path keeps SSE via JobTracker but no `ingest_log` for TRM. Legitimate-unchanged: `users.last_ingest_trigger_at` rate-limit, `job_tracker.create_job(user_id=...)`.
**Tests:** `tests/api/test_credentials.py`, `test_imports.py`, `test_ingest.py`, `test_health_endpoint.py` → `auth_headers_with_org` + org assertions.

#### Task 19d — Cleanup + full green + boot smoke
- Delete obsolete migration-mechanics tests targeting squashed-away revisions: `test_phase2_*_migration.py`, `test_counterparties_migration.py`, `test_phase25_persister_migration.py` (structure now covered by Task 10 drift test + RLS suite; behavior by fact tests).
- Fix any residual user-centric test usages across the suite.
- `cd backend && uv run pytest -q` → all green; `uv run ruff check . && uv run ruff format --check .` → clean.
- Container boot smoke:
```bash
docker compose -f compose.yaml -f compose.dev.yaml up -d --build backend
docker compose logs backend --since 30s | grep -iE "upgrade|startup complete|error|traceback"
curl -s localhost:8000/health
```
Expected: baseline applied, `Application startup complete`, `{"status":"ok"}`, no tracebacks.
- Commit per sub-task (`feat(sp1): ...`); final `test(sp1): app-layer org-aware convergence — full green`.

---

## Self-review checklist (run before execution)

- **Spec coverage:** orgs/parties/memberships/access_grants (Tasks 1-4) · participations party-anchored + setup-state move (Task 5) · flex_credentials per-org (Task 6) · org_id everywhere (Task 7) · squashed baseline (Task 8) · RLS policies + roles + FORCE + default-deny (Task 9) · structural tests (Task 10) · RLS smoke as app_rls (Tasks 11, 14) · SET LOCAL context dependency (Tasks 12-13) · persister org stamping (Task 15) · wizard party-anchored (Task 16) · provisioning (Task 17) · fixtures + green (Tasks 18-19) · TRM stays global (no task touches it = correct). ✔ All spec sections mapped.
- **Deferred correctly:** grant enforcement/CRUD → SP2; onboarding/MFA/multi-party UI → SP3; KMS → SP4; cron org-iteration → SP7. Tasks 16/19 only remove the old layer, don't build the new enforcement.
- **Type consistency:** `apply_org_context`, `resolve_current_org_id`, `org_context`, `provision_org`, `ProvisionResult`, `ORG_SCOPED_TABLES`, `APP_ROLE`, `standard_policy_sql` used consistently across tasks.
