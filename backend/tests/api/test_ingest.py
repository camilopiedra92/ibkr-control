"""Tests de /api/ingest/* (trigger + stream + logs)."""

import asyncio

from httpx import AsyncClient


async def _user_id_by_email(email: str) -> int:
    """Look up a registered user's id via a fresh engine on the same test DB.

    Same pattern as test_trigger_persists_timestamp_in_user_row — needed because
    job ownership (tracker) is keyed by the user's DB id, which we can't know
    from the JWT headers alone.
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from ibkr_control.auth.models import User
    from ibkr_control.config import get_settings

    engine = create_async_engine(get_settings().database_url, echo=False)
    try:
        session_local = async_sessionmaker(engine, expire_on_commit=False)
        async with session_local() as s:
            return (await s.scalars(select(User).where(User.email == email))).one().id
    finally:
        await engine.dispose()


async def test_get_logs_empty_when_no_runs(client: AsyncClient, auth_headers_with_org: dict):
    resp = await client.get("/api/ingest/logs?limit=10", headers=auth_headers_with_org)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_logs_requires_org(client: AsyncClient, auth_headers: dict):
    """A user with no org membership cannot resolve org_context → 403."""
    resp = await client.get("/api/ingest/logs?limit=10", headers=auth_headers)
    assert resp.status_code == 403


async def test_trigger_rate_limit_429(
    client: AsyncClient, auth_headers_with_org: dict, monkeypatch
):
    """Llamadas seguidas devuelven 429. Estado vive en DB (users.last_ingest_trigger_at)."""

    async def noop(*args, **kwargs) -> int:
        return 999

    monkeypatch.setattr("ibkr_control.api.ingest._launch_manual_job", noop)

    r1 = await client.post(
        "/api/ingest/trigger", json={"kind": "trm"}, headers=auth_headers_with_org
    )
    assert r1.status_code == 200

    r2 = await client.post(
        "/api/ingest/trigger", json={"kind": "trm"}, headers=auth_headers_with_org
    )
    assert r2.status_code == 429


async def test_trigger_invalid_kind_returns_422(client: AsyncClient, auth_headers_with_org: dict):
    resp = await client.post(
        "/api/ingest/trigger", json={"kind": "garbage"}, headers=auth_headers_with_org
    )
    assert resp.status_code == 422


async def test_stream_unknown_job_returns_404(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/ingest/stream/99999", headers=auth_headers)
    assert resp.status_code == 404


async def test_stream_emits_done_event(client: AsyncClient, auth_headers: dict):
    """El dueño del job SÍ puede stremearlo: crea un job propiedad del usuario
    autenticado, emite eventos, verifica que el SSE los entrega."""
    from ibkr_control.ingest.job_tracker import get_tracker

    owner_id = await _user_id_by_email("api_test@test.com")
    tracker = get_tracker()
    job_id = tracker.create_job(user_id=owner_id)
    tracker.emit(job_id, {"step": "test", "status": "running"})
    tracker.emit(job_id, {"step": "test", "status": "ok"})
    tracker.mark_done(job_id)

    async def _consume_stream() -> str:
        async with client.stream(
            "GET", f"/api/ingest/stream/{job_id}", headers=auth_headers
        ) as resp:
            assert resp.status_code == 200
            chunks = []
            async for line in resp.aiter_lines():
                chunks.append(line)
                if "event: done" in line:
                    break
            return "\n".join(chunks)

    body = await asyncio.wait_for(_consume_stream(), timeout=10.0)
    assert "test" in body
    assert "done" in body


async def test_stream_rejects_other_users_job_with_404(
    client: AsyncClient, auth_headers: dict, second_auth_headers: dict
):
    """D1: un usuario autenticado NO puede stremear el job de otro usuario.

    El owner es quien dispara el job; pasar el job_id de otro devuelve 404
    (no 403 — no filtramos la existencia del job a un tercero). Sin este check,
    cualquier usuario podía espiar el progreso del ingest de cualquier otro.
    """
    from ibkr_control.ingest.job_tracker import get_tracker

    owner_id = await _user_id_by_email("api_test@test.com")
    tracker = get_tracker()
    # Job owned by user 1, marked done so the (pre-fix) stream would terminate
    # instead of hanging — isolates the ownership check as the thing under test.
    job_id = tracker.create_job(user_id=owner_id)
    tracker.emit(job_id, {"step": "done"})
    tracker.mark_done(job_id)

    # user 2 (second_auth_headers) tries to read user 1's job
    resp = await client.get(f"/api/ingest/stream/{job_id}", headers=second_auth_headers)
    assert resp.status_code == 404


async def test_run_manual_emits_substep_keys_matching_frontend(monkeypatch):
    """Contract lock with ManualRefreshButton.tsx: step must be 'trm_backfill' /
    'flex_ytd' (not the bare 'trm'/'flex'), and ok payloads carry n_days so the
    UI can render '7 dias'.
    """
    from ibkr_control.api import ingest as ingest_mod
    from ibkr_control.ingest.job_tracker import get_tracker

    flex_kwargs: dict = {}

    async def fake_trm_run(*_a, **_kw):
        return {"status": "ok", "n_rows_api": 1, "n_days": 7}

    async def fake_flex_run(*_a, **kw):
        flex_kwargs.update(kw)
        return None

    monkeypatch.setattr("ibkr_control.ingest.trm.job.run", fake_trm_run)
    monkeypatch.setattr("ibkr_control.ingest.flex.job.run", fake_flex_run)
    monkeypatch.setattr(ingest_mod, "get_engine", lambda: object())

    tracker = get_tracker()
    job_id = tracker.create_job(user_id=1)
    await ingest_mod._run_manual(kind="both", user_id=1, org_id=42, job_id=job_id)

    # The flex run must be invoked with the org scope (per-org ingest) + user audit.
    assert flex_kwargs.get("organization_id") == 42
    assert flex_kwargs.get("user_id") == 1
    assert flex_kwargs.get("trigger") == "manual"

    events = [e.payload for e in tracker.events_since(job_id, after_id=-1)]
    assert [(e["step"], e.get("status")) for e in events] == [
        ("trm_backfill", "running"),
        ("trm_backfill", "ok"),
        ("flex_ytd", "running"),
        ("flex_ytd", "ok"),
        ("done", None),
    ]
    trm_ok = next(e for e in events if e["step"] == "trm_backfill" and e.get("status") == "ok")
    assert trm_ok["n_days"] == 7


async def test_run_manual_marks_failing_substep_as_failed(monkeypatch):
    """When a substep raises, the tracker event must carry status='failed' AND
    keep step='<substep>' so the UI can red-flag the right row. The previous
    contract emitted step='error' which the frontend silently dropped.
    """
    from ibkr_control.api import ingest as ingest_mod
    from ibkr_control.ingest.job_tracker import get_tracker

    async def fake_trm_run(*_a, **_kw):
        raise RuntimeError("boom from socrata")

    monkeypatch.setattr("ibkr_control.ingest.trm.job.run", fake_trm_run)
    monkeypatch.setattr(ingest_mod, "get_engine", lambda: object())

    tracker = get_tracker()
    job_id = tracker.create_job(user_id=1)
    await ingest_mod._run_manual(kind="trm", user_id=1, org_id=1, job_id=job_id)

    events = [e.payload for e in tracker.events_since(job_id, after_id=-1)]
    failed = next(e for e in events if e.get("status") == "failed")
    assert failed["step"] == "trm_backfill"
    assert "boom" in failed["error"]


async def test_trigger_persists_timestamp_in_user_row(
    client: AsyncClient, auth_headers_with_org: dict, monkeypatch
):
    """POST /api/ingest/trigger debe actualizar users.last_ingest_trigger_at.

    The rate-limit throttle stays user-scoped (per-user column on `users`) even
    though the ingest itself is org-scoped."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from ibkr_control.auth.models import User
    from ibkr_control.config import get_settings

    async def noop(*args, **kwargs) -> int:
        return 999

    monkeypatch.setattr("ibkr_control.api.ingest._launch_manual_job", noop)

    r = await client.post(
        "/api/ingest/trigger", json={"kind": "trm"}, headers=auth_headers_with_org
    )
    assert r.status_code == 200

    # Verify the column was updated using a fresh engine on the same DB
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    session_local = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_local() as s:
            user = (await s.scalars(select(User).where(User.email == "org_owner@test.com"))).one()
            assert user.last_ingest_trigger_at is not None, (
                "trigger endpoint did not persist last_ingest_trigger_at"
            )
    finally:
        await engine.dispose()
