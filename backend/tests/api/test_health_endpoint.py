"""Tests del endpoint /api/health/ingest (R4 backend + R6 API)."""

from datetime import datetime, timedelta, timezone

from httpx import AsyncClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ibkr_control.db.models.ingest_log import IngestLog


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


async def test_health_endpoint_returns_no_runs_when_log_empty(
    client: AsyncClient,
    auth_headers: dict,
):
    resp = await client.get("/api/health/ingest", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "sources" in data
    flex = next(s for s in data["sources"] if s["source"] == "flex")
    trm = next(s for s in data["sources"] if s["source"] == "trm")
    assert flex["last_success_at"] is None
    assert flex["consecutive_failures"] == 0
    assert trm["last_success_at"] is None


async def test_health_endpoint_reports_last_success(
    client: AsyncClient,
    auth_headers: dict,
):
    """Health endpoint reports last_success_at for a job_kind='flex' 'ok' log row."""
    from ibkr_control.config import get_settings

    me = await client.get("/api/users/me", headers=auth_headers)
    user_id = me.json()["id"]

    now = datetime.now(timezone.utc)
    url = get_settings().database_url
    await _seed_log(
        url,
        user_id=user_id,
        job_kind="flex",
        trigger="cron",
        started_at=now - timedelta(hours=1),
        finished_at=now - timedelta(minutes=58),
        status="ok",
        items_processed=100,
    )

    resp = await client.get("/api/health/ingest", headers=auth_headers)
    data = resp.json()
    flex = next(s for s in data["sources"] if s["source"] == "flex")
    assert flex["last_success_at"] is not None
    assert flex["consecutive_failures"] == 0


async def test_health_endpoint_counts_consecutive_failures(
    client: AsyncClient,
    auth_headers: dict,
):
    """3 failure rows since last success → consecutive_failures = 3."""
    from ibkr_control.config import get_settings

    me = await client.get("/api/users/me", headers=auth_headers)
    user_id = me.json()["id"]

    now = datetime.now(timezone.utc)
    url = get_settings().database_url
    for i in range(3):
        await _seed_log(
            url,
            user_id=user_id,
            job_kind="flex",
            trigger="cron",
            started_at=now - timedelta(hours=i + 1),
            finished_at=now - timedelta(hours=i, minutes=59),
            status="failed",
            items_processed=0,
            error_message="forced failure",
        )

    resp = await client.get("/api/health/ingest", headers=auth_headers)
    data = resp.json()
    flex = next(s for s in data["sources"] if s["source"] == "flex")
    assert flex["consecutive_failures"] == 3
    assert flex["last_error"] == "forced failure"


async def test_health_endpoint_scoped_per_user(
    client: AsyncClient,
    auth_headers: dict,
    second_auth_headers: dict,
):
    """R6 API: user B's logs do not leak to user A's response."""
    from ibkr_control.config import get_settings

    me_b = await client.get("/api/users/me", headers=second_auth_headers)
    user_b_id = me_b.json()["id"]

    now = datetime.now(timezone.utc)
    url = get_settings().database_url
    await _seed_log(
        url,
        user_id=user_b_id,
        job_kind="flex",
        trigger="cron",
        started_at=now - timedelta(hours=1),
        finished_at=now - timedelta(minutes=58),
        status="ok",
        items_processed=200,
    )

    # User A has no logs → sees nothing
    resp_a = await client.get("/api/health/ingest", headers=auth_headers)
    data_a = resp_a.json()
    flex_a = next(s for s in data_a["sources"] if s["source"] == "flex")
    assert flex_a["last_success_at"] is None

    # User B sees its own log
    resp_b = await client.get("/api/health/ingest", headers=second_auth_headers)
    data_b = resp_b.json()
    flex_b = next(s for s in data_b["sources"] if s["source"] == "flex")
    assert flex_b["last_success_at"] is not None


async def test_health_endpoint_requires_auth(client: AsyncClient):
    """Endpoint requires authentication — 401 without headers."""
    resp = await client.get("/api/health/ingest")
    assert resp.status_code == 401
