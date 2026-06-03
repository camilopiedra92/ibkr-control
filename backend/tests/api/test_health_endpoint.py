"""Tests del endpoint /api/health/ingest (R4 backend + R6 API).

Org-aware (SP1 19c, D-CONV-1):
- Flex health reads ingest_log scoped by organization_id.
- TRM health reads trm_imports (GLOBAL); last_success_at = max(fetched_at).
"""

from datetime import date, datetime, timedelta, timezone

from httpx import AsyncClient
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ibkr_control.auth.models import User
from ibkr_control.db.models.ingest_log import IngestLog
from ibkr_control.db.models.memberships import Membership
from ibkr_control.db.models.trm import TrmImport


async def _org_id_by_email(url: str, email: str) -> int:
    """Resolve the org_id of the (single) membership for a user by email."""
    engine = create_async_engine(url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            user = await session.scalar(select(User).where(User.email == email))
            return await session.scalar(
                select(Membership.organization_id).where(Membership.user_id == user.id)
            )
    finally:
        await engine.dispose()


async def _seed_log(url: str, **values) -> None:
    """Seed an ingest_log row via a fresh engine connection to the test DB."""
    engine = create_async_engine(url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await session.execute(insert(IngestLog).values(**values))
            await session.commit()
    finally:
        await engine.dispose()


async def _seed_trm_import(url: str, **values) -> None:
    """Seed a trm_imports row (global TRM record) via a fresh engine connection."""
    engine = create_async_engine(url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await session.execute(insert(TrmImport).values(**values))
            await session.commit()
    finally:
        await engine.dispose()


async def test_health_endpoint_returns_no_runs_when_log_empty(
    client: AsyncClient,
    auth_headers_with_org: dict,
):
    resp = await client.get("/api/health/ingest", headers=auth_headers_with_org)
    assert resp.status_code == 200
    data = resp.json()
    assert "sources" in data
    flex = next(s for s in data["sources"] if s["source"] == "flex")
    trm = next(s for s in data["sources"] if s["source"] == "trm")
    assert flex["last_success_at"] is None
    assert flex["consecutive_failures"] == 0
    assert trm["last_success_at"] is None


async def test_health_endpoint_requires_org(client: AsyncClient, auth_headers: dict):
    """A user with no org membership cannot resolve org_context → 403."""
    resp = await client.get("/api/health/ingest", headers=auth_headers)
    assert resp.status_code == 403


async def test_health_endpoint_reports_flex_last_success(
    client: AsyncClient,
    auth_headers_with_org: dict,
):
    """Flex health reports last_success_at for an org-scoped job_kind='flex' 'ok' row."""
    from ibkr_control.config import get_settings

    url = get_settings().database_url
    org_id = await _org_id_by_email(url, "org_owner@test.com")

    now = datetime.now(timezone.utc)
    await _seed_log(
        url,
        organization_id=org_id,
        job_kind="flex",
        trigger="cron",
        started_at=now - timedelta(hours=1),
        finished_at=now - timedelta(minutes=58),
        status="ok",
        items_processed=100,
    )

    resp = await client.get("/api/health/ingest", headers=auth_headers_with_org)
    data = resp.json()
    flex = next(s for s in data["sources"] if s["source"] == "flex")
    assert flex["last_success_at"] is not None
    assert flex["consecutive_failures"] == 0


async def test_health_endpoint_reports_trm_last_success_from_trm_imports(
    client: AsyncClient,
    auth_headers_with_org: dict,
):
    """TRM health reads trm_imports (GLOBAL): last_success_at = max(fetched_at),
    no failure record (last_failure_at None, consecutive_failures 0)."""
    from ibkr_control.config import get_settings

    url = get_settings().database_url
    now = datetime.now(timezone.utc)

    # Two TRM imports; max(fetched_at) is the more recent one.
    await _seed_trm_import(
        url,
        date_range_from=date(2026, 1, 1),
        date_range_to=date(2026, 1, 31),
        n_rows_api=20,
        n_days_expanded=31,
        fetched_at=now - timedelta(days=2),
    )
    await _seed_trm_import(
        url,
        date_range_from=date(2026, 2, 1),
        date_range_to=date(2026, 2, 28),
        n_rows_api=18,
        n_days_expanded=28,
        fetched_at=now - timedelta(hours=1),
    )

    resp = await client.get("/api/health/ingest", headers=auth_headers_with_org)
    data = resp.json()
    trm = next(s for s in data["sources"] if s["source"] == "trm")
    assert trm["last_success_at"] is not None
    # Reflects the most recent fetched_at (the 1-hour-ago row, not 2-days-ago).
    parsed = datetime.fromisoformat(trm["last_success_at"])
    assert parsed > now - timedelta(hours=2)
    assert trm["last_failure_at"] is None
    assert trm["consecutive_failures"] == 0
    assert trm["last_error"] is None


async def test_health_endpoint_counts_consecutive_failures(
    client: AsyncClient,
    auth_headers_with_org: dict,
):
    """3 flex failure rows since last success → consecutive_failures = 3."""
    from ibkr_control.config import get_settings

    url = get_settings().database_url
    org_id = await _org_id_by_email(url, "org_owner@test.com")

    now = datetime.now(timezone.utc)
    for i in range(3):
        await _seed_log(
            url,
            organization_id=org_id,
            job_kind="flex",
            trigger="cron",
            started_at=now - timedelta(hours=i + 1),
            finished_at=now - timedelta(hours=i, minutes=59),
            status="failed",
            items_processed=0,
            error_message="forced failure",
        )

    resp = await client.get("/api/health/ingest", headers=auth_headers_with_org)
    data = resp.json()
    flex = next(s for s in data["sources"] if s["source"] == "flex")
    assert flex["consecutive_failures"] == 3
    assert flex["last_error"] == "forced failure"


async def test_health_endpoint_flex_scoped_per_org(
    client: AsyncClient,
    auth_headers_with_org: dict,
    second_auth_headers_with_org: dict,
):
    """R6 API: org B's flex logs do not leak to org A's response."""
    from ibkr_control.config import get_settings

    url = get_settings().database_url
    org_b_id = await _org_id_by_email(url, "org_owner_2@test.com")

    now = datetime.now(timezone.utc)
    await _seed_log(
        url,
        organization_id=org_b_id,
        job_kind="flex",
        trigger="cron",
        started_at=now - timedelta(hours=1),
        finished_at=now - timedelta(minutes=58),
        status="ok",
        items_processed=200,
    )

    # Org A has no logs → sees nothing
    resp_a = await client.get("/api/health/ingest", headers=auth_headers_with_org)
    flex_a = next(s for s in resp_a.json()["sources"] if s["source"] == "flex")
    assert flex_a["last_success_at"] is None

    # Org B sees its own log
    resp_b = await client.get("/api/health/ingest", headers=second_auth_headers_with_org)
    flex_b = next(s for s in resp_b.json()["sources"] if s["source"] == "flex")
    assert flex_b["last_success_at"] is not None


async def test_health_endpoint_requires_auth(client: AsyncClient):
    """Endpoint requires authentication — 401 without headers."""
    resp = await client.get("/api/health/ingest")
    assert resp.status_code == 401
