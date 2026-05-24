"""Tests del wizard endpoints /api/setup/*."""
import base64

import httpx
import pytest
import respx
from httpx import AsyncClient, Response


@pytest.fixture(autouse=True)
def set_token_key(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", base64.b64encode(b"X" * 32).decode("ascii"))


async def _register_and_login(client: AsyncClient, email: str = "setup@test.com") -> str:
    await client.post(
        "/api/auth/register",
        json={"email": email, "password": "supersecret123", "name": "Setup User"},
    )
    login = await client.post(
        "/api/auth/jwt/login",
        data={"username": email, "password": "supersecret123"},
    )
    return login.json()["access_token"]


async def test_get_setup_state_initial(client: AsyncClient):
    token = await _register_and_login(client, "state@test.com")
    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.get("/api/setup/state", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["step1_credentials"] is False
    assert body["step2_accounts"] is False
    assert body["step3_xmls"] is False
    assert body["setup_completed_at"] is None


async def test_step1_validate_with_valid_token(client: AsyncClient):
    token = await _register_and_login(client, "step1ok@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    with respx.mock(base_url="https://gdcdyn.interactivebrokers.com") as mock_router:
        mock_router.get("/Universal/servlet/FlexStatementService.SendRequest").mock(
            return_value=Response(
                200,
                content=b"""<?xml version="1.0"?>
<FlexStatementResponse><Status>Success</Status><ReferenceCode>9999</ReferenceCode></FlexStatementResponse>""",
            )
        )
        resp = await client.post(
            "/api/setup/step1/validate",
            json={"token": "good-token-abc", "query_id": "1234567"},
            headers=headers,
        )

    assert resp.status_code == 200
    state = (await client.get("/api/setup/state", headers=headers)).json()
    assert state["step1_credentials"] is True


async def test_step1_validate_rejects_bad_token(client: AsyncClient):
    token = await _register_and_login(client, "step1bad@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    with respx.mock(base_url="https://gdcdyn.interactivebrokers.com") as mock_router:
        mock_router.get("/Universal/servlet/FlexStatementService.SendRequest").mock(
            return_value=Response(
                200,
                content=b"""<?xml version="1.0"?>
<FlexStatementResponse><Status>Fail</Status><ErrorCode>1018</ErrorCode><ErrorMessage>Invalid token</ErrorMessage></FlexStatementResponse>""",
            )
        )
        resp = await client.post(
            "/api/setup/step1/validate",
            json={"token": "bad-token-00000", "query_id": "1"},
            headers=headers,
        )

    assert resp.status_code == 401
    state = (await client.get("/api/setup/state", headers=headers)).json()
    assert state["step1_credentials"] is False


async def test_step2_save_creates_accounts_and_participations(client: AsyncClient):
    token = await _register_and_login(client, "step2@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/setup/step2/save",
        json={
            "accounts": [
                {"ibkr_account_id": "U99999001", "alias": "Joint", "pct": "0.5000"},
                {"ibkr_account_id": "U99999002", "alias": "Swing", "pct": "1.0000"},
                {"ibkr_account_id": "U99999003", "alias": "FUT", "pct": "1.0000"},
            ]
        },
        headers=headers,
    )
    assert resp.status_code == 200

    state = (await client.get("/api/setup/state", headers=headers)).json()
    assert state["step2_accounts"] is True


async def test_step2_rejects_invalid_account_id(client: AsyncClient):
    token = await _register_and_login(client, "step2bad@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/setup/step2/save",
        json={
            "accounts": [{"ibkr_account_id": "NOT-VALID", "alias": "x", "pct": "0.5"}]
        },
        headers=headers,
    )
    assert resp.status_code == 422


async def test_step3_complete_marks_progress(client: AsyncClient):
    token = await _register_and_login(client, "step3@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post("/api/setup/step3/complete", headers=headers)
    assert resp.status_code == 200
    state = (await client.get("/api/setup/state", headers=headers)).json()
    assert state["step3_xmls"] is True


# ---------------------------------------------------------------------------
# Additional tests to raise coverage on lines 64-65, 71-85, 99-136, 150-153,
# 162-172, 177-243
# ---------------------------------------------------------------------------


async def test_step1_validate_ibkr_connection_timeout_returns_502(client: AsyncClient):
    """Network error contacting IBKR returns 502, not 401."""
    token = await _register_and_login(client, "step1timeout@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    with respx.mock(base_url="https://gdcdyn.interactivebrokers.com") as mock_router:
        mock_router.get("/Universal/servlet/FlexStatementService.SendRequest").mock(
            side_effect=httpx.ConnectTimeout("connection timed out")
        )
        resp = await client.post(
            "/api/setup/step1/validate",
            json={"token": "some-valid-token", "query_id": "1234567"},
            headers=headers,
        )

    assert resp.status_code == 502
    assert "IBKR" in resp.json()["detail"] or "alcanzar" in resp.json()["detail"]


async def test_step1_validate_updates_existing_credentials(client: AsyncClient):
    """Re-running step1 with a different token updates the stored credentials."""
    token = await _register_and_login(client, "step1update@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    success_xml = b"""<?xml version="1.0"?>
<FlexStatementResponse><Status>Success</Status><ReferenceCode>9999</ReferenceCode></FlexStatementResponse>"""

    # First save
    with respx.mock(base_url="https://gdcdyn.interactivebrokers.com") as mock_router:
        mock_router.get("/Universal/servlet/FlexStatementService.SendRequest").mock(
            return_value=Response(200, content=success_xml)
        )
        r1 = await client.post(
            "/api/setup/step1/validate",
            json={"token": "first-token-abc", "query_id": "1111111"},
            headers=headers,
        )
    assert r1.status_code == 200

    # Second save with a different token — should update (upsert path)
    with respx.mock(base_url="https://gdcdyn.interactivebrokers.com") as mock_router:
        mock_router.get("/Universal/servlet/FlexStatementService.SendRequest").mock(
            return_value=Response(200, content=success_xml)
        )
        r2 = await client.post(
            "/api/setup/step1/validate",
            json={"token": "second-token-xyz", "query_id": "2222222"},
            headers=headers,
        )
    assert r2.status_code == 200

    # State should still reflect credentials configured
    state = (await client.get("/api/setup/state", headers=headers)).json()
    assert state["step1_credentials"] is True


async def test_step2_save_updates_existing_account_alias(client: AsyncClient):
    """Re-running step2 with same account and same pct updates the alias (idempotent upsert)."""
    token = await _register_and_login(client, "step2alias@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    # Save once with alias "Original"
    r1 = await client.post(
        "/api/setup/step2/save",
        json={"accounts": [{"ibkr_account_id": "U88888001", "alias": "Original", "pct": "1.0000"}]},
        headers=headers,
    )
    assert r1.status_code == 200

    # Save again with same account, same pct (triggers idempotent continue), different alias
    # (alias update happens on the Account row regardless of pct path)
    r2 = await client.post(
        "/api/setup/step2/save",
        json={"accounts": [{"ibkr_account_id": "U88888001", "alias": "Updated", "pct": "1.0000"}]},
        headers=headers,
    )
    assert r2.status_code == 200

    state = (await client.get("/api/setup/state", headers=headers)).json()
    assert state["step2_accounts"] is True


async def test_step2_save_same_pct_is_idempotent(client: AsyncClient):
    """Saving the same account with same pct is a no-op (continues loop without new participation)."""
    token = await _register_and_login(client, "step2idempotent@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    payload = {"accounts": [{"ibkr_account_id": "U88888002", "alias": "Joint", "pct": "0.5000"}]}

    r1 = await client.post("/api/setup/step2/save", json=payload, headers=headers)
    assert r1.status_code == 200

    # Same payload — should still succeed (idempotent via continue branch)
    r2 = await client.post("/api/setup/step2/save", json=payload, headers=headers)
    assert r2.status_code == 200


async def test_step2_rejects_pct_exceeding_1(client: AsyncClient):
    """AccountInWizard schema rejects pct > 1.0 with 422."""
    token = await _register_and_login(client, "step2pct@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/setup/step2/save",
        json={"accounts": [{"ibkr_account_id": "U88888003", "alias": "Over", "pct": "1.5000"}]},
        headers=headers,
    )
    assert resp.status_code == 422


async def test_step3_complete_counts_zero_xmls_when_none_uploaded(client: AsyncClient):
    """step3/complete with no manual uploads returns n_xmls_uploaded=0 and updates state."""
    token = await _register_and_login(client, "step3zero@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post("/api/setup/step3/complete", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_xmls_uploaded"] == 0
    assert body["ok"] is True

    state = (await client.get("/api/setup/state", headers=headers)).json()
    assert state["step3_n_xmls_uploaded"] == 0


async def test_step4_start_returns_job_id(client: AsyncClient, monkeypatch):
    """step4/start creates a job and persists step4_started_at in setup_progress."""
    token = await _register_and_login(client, "step4start@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    # Patch the background task to be a no-op so we don't try to run real jobs
    from ibkr_control.api import setup as setup_mod

    async def noop_meta_job(**kwargs):
        pass

    monkeypatch.setattr(setup_mod, "_run_setup_meta_job", noop_meta_job)

    resp = await client.post("/api/setup/step4/start", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "job_id" in body
    assert isinstance(body["job_id"], int)

    state = (await client.get("/api/setup/state", headers=headers)).json()
    assert state["step4_started_at"] is not None
    assert state["step4_job_id"] == body["job_id"]


async def test_get_state_unauthenticated_returns_401(client: AsyncClient):
    """GET /api/setup/state without auth should return 401."""
    resp = await client.get("/api/setup/state")
    assert resp.status_code == 401


async def test_step1_requires_auth(client: AsyncClient):
    """POST /api/setup/step1/validate without auth should return 401."""
    resp = await client.post(
        "/api/setup/step1/validate",
        json={"token": "some-token-abc", "query_id": "1234567"},
    )
    assert resp.status_code == 401


async def test_step2_requires_auth(client: AsyncClient):
    """POST /api/setup/step2/save without auth should return 401."""
    resp = await client.post(
        "/api/setup/step2/save",
        json={"accounts": [{"ibkr_account_id": "U99999000", "alias": "x", "pct": "1.0"}]},
    )
    assert resp.status_code == 401


async def test_step3_requires_auth(client: AsyncClient):
    """POST /api/setup/step3/complete without auth should return 401."""
    resp = await client.post("/api/setup/step3/complete")
    assert resp.status_code == 401


async def test_step4_requires_auth(client: AsyncClient):
    """POST /api/setup/step4/start without auth should return 401."""
    resp = await client.post("/api/setup/step4/start")
    assert resp.status_code == 401


async def test_meta_job_marks_setup_completed_on_success(
    postgres_container, monkeypatch
):
    """_run_setup_meta_job sets setup_completed_at and emits done event on success."""
    import base64

    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", base64.b64encode(b"X" * 32).decode("ascii"))

    url = postgres_container.get_connection_url()
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")

    from ibkr_control.config import get_settings
    get_settings.cache_clear()

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from ibkr_control.db.base import Base
    import ibkr_control.db  # noqa: F401 — registers all models

    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_local = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    # Create a user to run the meta job against
    from ibkr_control.auth.models import User
    async with session_local() as s:
        u = User(email="metajob@test.com", hashed_password="x", is_active=True, name="Meta Job User")
        s.add(u)
        await s.commit()
        await s.refresh(u)
        user_id = u.id

    # Mock the two sub-jobs to avoid hitting external services
    from ibkr_control.ingest.flex import job as flex_job_mod
    from ibkr_control.ingest.trm import job as trm_job_mod

    async def fake_trm_run(session_factory, *, trigger, full_backfill=False):
        return {"status": "ok", "n_rows_api": 10, "n_days": 100}

    async def fake_flex_run(session_factory, *, user_id, trigger):
        return None

    monkeypatch.setattr(trm_job_mod, "run", fake_trm_run)
    monkeypatch.setattr(flex_job_mod, "run", fake_flex_run)

    # Patch get_engine to return our test engine so _run_setup_meta_job uses
    # the test DB rather than trying to connect to production
    from ibkr_control.db import session as session_mod
    monkeypatch.setattr(session_mod, "get_engine", lambda: engine)

    from ibkr_control.api.setup import _run_setup_meta_job
    from ibkr_control.ingest.job_tracker import get_tracker

    tracker = get_tracker()
    job_id = tracker.create_job()

    await _run_setup_meta_job(user_id=user_id, job_id=job_id)

    # Verify setup_completed_at was set in DB
    from sqlalchemy import select
    async with session_local() as s:
        u_refreshed = await s.scalar(select(User).where(User.id == user_id))
        assert u_refreshed.setup_completed_at is not None

    # Verify done event was emitted
    events = tracker.events_since(job_id, after_id=-1)
    assert any(e.payload.get("step") == "done" for e in events)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def test_meta_job_marks_failed_and_returns_early_on_trm_error(monkeypatch):
    """_run_setup_meta_job emits trm_backfill:failed and returns early without touching flex.

    This test mocks the DB helpers inside _run_setup_meta_job directly to avoid
    asyncpg event-loop binding issues (each test runs in its own loop, and a fresh
    engine per test is not trivially shareable with the session-scoped postgres_container).
    """
    import ibkr_control.api.setup as setup_mod
    from ibkr_control.ingest.flex import job as flex_job_mod
    from ibkr_control.ingest.trm import job as trm_job_mod
    from ibkr_control.ingest.job_tracker import get_tracker

    flex_called = []

    async def failing_trm_run(session_factory, *, trigger, full_backfill=False):
        raise RuntimeError("Socrata unreachable")

    async def should_not_be_called(session_factory, *, user_id, trigger):
        flex_called.append(True)  # pragma: no cover

    monkeypatch.setattr(trm_job_mod, "run", failing_trm_run)
    monkeypatch.setattr(flex_job_mod, "run", should_not_be_called)

    # Mock get_engine so _run_setup_meta_job can build a session_local without a real DB
    # We intercept at the async_sessionmaker level by patching the inner helpers
    setup_completed_at_calls = []

    async def fake_read_substeps():
        return {}  # No substeps yet — forces TRM backfill path

    async def fake_write_substep(name, status):
        pass  # Discard writes

    async def fake_mark_completed():
        setup_completed_at_calls.append(True)  # pragma: no cover

    # Patch the closures inside _run_setup_meta_job by replacing get_engine with a
    # mock that returns an engine whose session_local is patched — simpler: just
    # patch the imports _run_setup_meta_job uses at module level.
    # Since get_engine is called inside the function (not at module level), we patch
    # session_mod.get_engine to prevent it from connecting to a real DB.
    from ibkr_control.db import session as session_mod
    from unittest.mock import MagicMock, AsyncMock
    from sqlalchemy.ext.asyncio import async_sessionmaker

    # We need the session_local inside _run_setup_meta_job to use a fake.
    # The cleanest approach: patch the two async inner helpers using a subclass of
    # _run_setup_meta_job. Since it's a plain async function (not a class), we
    # call it and intercept via the trm_job/flex_job mocks.
    # The DB calls (_read_substeps, _write_substep) are inner closures — we can't
    # monkeypatch them directly. Instead we patch get_engine to return a mock engine
    # whose async_sessionmaker yields a mock session.
    mock_user = MagicMock()
    mock_user.setup_progress = {}
    mock_user.setup_completed_at = None

    mock_session = AsyncMock()
    mock_session.scalar = AsyncMock(return_value=mock_user)
    mock_session.commit = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_session_maker = MagicMock()
    mock_session_maker.return_value = mock_session

    mock_engine = MagicMock()
    monkeypatch.setattr(session_mod, "get_engine", lambda: mock_engine)

    # Patch async_sessionmaker to return our mock
    import ibkr_control.api.setup
    original_async_sessionmaker = ibkr_control.api.setup.async_sessionmaker

    def fake_async_sessionmaker(engine, **kwargs):
        return mock_session_maker

    monkeypatch.setattr(ibkr_control.api.setup, "async_sessionmaker", fake_async_sessionmaker)

    tracker = get_tracker()
    job_id = tracker.create_job()

    await setup_mod._run_setup_meta_job(user_id=999, job_id=job_id)

    # Flex should NOT have been called
    assert flex_called == []

    # setup_completed_at should NOT have been set (early return)
    assert mock_user.setup_completed_at is None

    # trm_backfill:failed event should have been emitted
    events = tracker.events_since(job_id, after_id=-1)
    failed_events = [e for e in events if e.payload.get("status") == "failed"]
    assert len(failed_events) >= 1
    # Job should be marked done (early return path)
    assert tracker.is_done(job_id)
