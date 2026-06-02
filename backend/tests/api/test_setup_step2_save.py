"""Tests for POST /api/setup/step2/save — persist accounts + participations.

The endpoint runs after step2/detect has populated `flex_imports` and
`accounts` tables. step2/save then:
  - validates every incoming ibkr_account_id was actually detected,
  - rejects F-shadow IDs (defense in depth on top of Pydantic regex),
  - sets/updates Participation rows for the current user,
  - schedules a TRM backfill as a FastAPI BackgroundTask (per D6).

The `set_token_key` autouse fixture is inherited from tests/api/conftest.py.
"""
from decimal import Decimal
from unittest.mock import AsyncMock

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ibkr_control.config import get_settings
from ibkr_control.db.models.accounts import Account
from ibkr_control.db.models.participations import Participation
from ibkr_control.ingest.flex import client as flex_client_mod


# Minimal Activity XML with a single non-F account so step2/detect populates
# `accounts` with U99999999 only. Step2/save then validates against this set.
_FAKE_XML = b"""<?xml version="1.0"?>
<FlexQueryResponse>
  <FlexStatements>
    <FlexStatement accountId="U99999999" fromDate="20260101" toDate="20260524">
      <AccountInformation accountId="U99999999" accountAlias="A" accountType="Joint" name="X" currency="USD"/>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>
"""


async def _seed_detect(client: AsyncClient, auth_headers: dict, monkeypatch) -> None:
    """Run step1/save + step2/detect to populate accounts + flex_imports."""
    r = await client.post(
        "/api/setup/step1/save",
        headers=auth_headers,
        json={"token": "tok_12345_seed", "query_id": "999"},
    )
    assert r.status_code == 200, r.text
    monkeypatch.setattr(
        flex_client_mod.FlexClient, "send_request", AsyncMock(return_value="ref")
    )
    monkeypatch.setattr(
        flex_client_mod.FlexClient,
        "get_statement",
        AsyncMock(return_value=_FAKE_XML),
    )
    r = await client.post("/api/setup/step2/detect", headers=auth_headers)
    assert r.status_code == 200, r.text


async def test_step2_save_persists_accounts_and_participations(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    """Happy path: U99999999 is detected, alias overrides, Participation row created."""
    # Avoid kicking off the real TRM backfill background task.
    monkeypatch.setattr(
        "ibkr_control.api.setup._trm_backfill_background", AsyncMock()
    )
    await _seed_detect(client, auth_headers, monkeypatch)

    r = await client.post(
        "/api/setup/step2/save",
        headers=auth_headers,
        json={
            "accounts": [
                {"ibkr_account_id": "U99999999", "alias": "Joint", "pct": "0.5000"}
            ]
        },
    )
    assert r.status_code == 200, r.text

    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as s:
            accs = (await s.scalars(select(Account))).all()
            assert {a.ibkr_account_id for a in accs} == {"U99999999"}
            # alias overridden from incoming payload
            assert next(a.alias for a in accs if a.ibkr_account_id == "U99999999") == "Joint"
            parts = (await s.scalars(select(Participation))).all()
            assert len(parts) == 1
            assert parts[0].pct == Decimal("0.5000")
            assert parts[0].valid_to is None
    finally:
        await engine.dispose()


async def test_step2_save_400_when_account_not_detected(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    """Account ID well-formed but never detected → 400 ACCOUNT_NOT_DETECTED."""
    monkeypatch.setattr(
        "ibkr_control.api.setup._trm_backfill_background", AsyncMock()
    )
    await _seed_detect(client, auth_headers, monkeypatch)

    r = await client.post(
        "/api/setup/step2/save",
        headers=auth_headers,
        json={
            "accounts": [
                {"ibkr_account_id": "U88888888", "alias": None, "pct": "1.0000"}
            ]
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "ACCOUNT_NOT_DETECTED"


async def test_step2_save_rejects_shadow_id(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    """Shadow IDs (F-suffix) are rejected at Pydantic validation level → 422.

    The schema regex `^U\\d{8,11}$` forbids non-digit characters after `U`,
    so "U99999999F" never reaches the endpoint body. Either way the contract
    is "no shadow IDs accepted"; this asserts the actual status code returned.
    """
    monkeypatch.setattr(
        "ibkr_control.api.setup._trm_backfill_background", AsyncMock()
    )
    await _seed_detect(client, auth_headers, monkeypatch)

    r = await client.post(
        "/api/setup/step2/save",
        headers=auth_headers,
        json={
            "accounts": [
                {"ibkr_account_id": "U99999999F", "alias": None, "pct": "1.0000"}
            ]
        },
    )
    # Pydantic regex rejects the F-suffix at validation; the spec-text-suggested
    # 400 path is unreachable through the public API. Both reject — the contract
    # is preserved.
    assert r.status_code == 422


async def test_step2_save_dispatches_trm_background(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    """BackgroundTasks must enqueue _trm_backfill_background after a successful save."""
    await _seed_detect(client, auth_headers, monkeypatch)

    dispatched: list[tuple[int, int]] = []

    async def fake_trm(user_id: int, job_id: int) -> None:
        dispatched.append((user_id, job_id))

    monkeypatch.setattr(
        "ibkr_control.api.setup._trm_backfill_background", fake_trm
    )

    r = await client.post(
        "/api/setup/step2/save",
        headers=auth_headers,
        json={
            "accounts": [
                {"ibkr_account_id": "U99999999", "alias": "A", "pct": "1.0000"}
            ]
        },
    )
    assert r.status_code == 200, r.text
    # FastAPI BackgroundTasks run after the response is sent. httpx's ASGI
    # transport awaits the lifespan such that the background task is executed
    # before the response is fully returned to the caller — so by the time we
    # see 200, the task has run.
    assert len(dispatched) == 1
    user_id, job_id = dispatched[0]
    assert isinstance(user_id, int)
    # job_id must match the one returned to the client so the wizard can
    # subscribe to /api/ingest/stream/{job_id} for live progress.
    assert r.json()["trm_backfill_job_id"] == job_id


async def test_step2_save_response_includes_trm_backfill_job_id(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    """step2/save must register a JobTracker job for the TRM backfill BEFORE
    scheduling the BackgroundTask and return its id. The frontend wizard
    consumes /api/ingest/stream/{job_id} to render progress; without the id,
    a Socrata or persister failure stays invisible (D12 root cause).
    """
    monkeypatch.setattr(
        "ibkr_control.api.setup._trm_backfill_background", AsyncMock()
    )
    await _seed_detect(client, auth_headers, monkeypatch)

    r = await client.post(
        "/api/setup/step2/save",
        headers=auth_headers,
        json={
            "accounts": [
                {"ibkr_account_id": "U99999999", "alias": "A", "pct": "1.0000"}
            ]
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "trm_backfill_job_id" in body
    assert isinstance(body["trm_backfill_job_id"], int)

    # The job must be registered in the tracker by the time the response
    # returns, otherwise the frontend can race the SSE subscribe before
    # create_job runs and get a 404 on /api/ingest/stream/{job_id}.
    from ibkr_control.ingest.job_tracker import get_tracker
    assert get_tracker().has_job(body["trm_backfill_job_id"])


async def test_trm_backfill_background_emits_progress_events(monkeypatch):
    """Contract lock with the wizard's TrmBackfillBanner: background fn must
    emit running → ok(n_days) → done so the banner can render a progress
    state. Uses the same step name ('trm_backfill') as _run_manual so a
    future consolidation can share the SSE parser.
    """
    from ibkr_control.api import setup as setup_mod
    from ibkr_control.ingest.job_tracker import get_tracker

    async def fake_trm_run(*_a, **_kw):
        return {"status": "ok", "n_rows_api": 5, "n_days": 12}

    monkeypatch.setattr("ibkr_control.ingest.trm.job.run", fake_trm_run)
    monkeypatch.setattr(setup_mod, "get_engine", lambda: object())

    tracker = get_tracker()
    job_id = tracker.create_job()
    await setup_mod._trm_backfill_background(user_id=1, job_id=job_id)

    events = [e.payload for e in tracker.events_since(job_id, after_id=-1)]
    assert [(e["step"], e.get("status")) for e in events] == [
        ("trm_backfill", "running"),
        ("trm_backfill", "ok"),
        ("done", None),
    ]
    trm_ok = next(
        e for e in events if e["step"] == "trm_backfill" and e.get("status") == "ok"
    )
    assert trm_ok["n_days"] == 12
    assert tracker.is_done(job_id)


async def test_trm_backfill_background_marks_failure_on_exception(monkeypatch):
    """When the TRM job raises, tracker must surface status='failed' on the
    trm_backfill substep (not a generic 'error') AND still mark_done so the
    SSE stream closes cleanly. This is the fix for the D12 silent failure
    where Socrata errors only showed up in server logs.
    """
    from ibkr_control.api import setup as setup_mod
    from ibkr_control.ingest.job_tracker import get_tracker

    async def fake_trm_run(*_a, **_kw):
        raise RuntimeError("boom from socrata")

    monkeypatch.setattr("ibkr_control.ingest.trm.job.run", fake_trm_run)
    monkeypatch.setattr(setup_mod, "get_engine", lambda: object())

    tracker = get_tracker()
    job_id = tracker.create_job()
    await setup_mod._trm_backfill_background(user_id=1, job_id=job_id)

    events = [e.payload for e in tracker.events_since(job_id, after_id=-1)]
    failed = next(e for e in events if e.get("status") == "failed")
    assert failed["step"] == "trm_backfill"
    assert "boom" in failed["error"]
    assert tracker.is_done(job_id)
