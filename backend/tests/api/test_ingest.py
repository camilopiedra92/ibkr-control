"""Tests de /api/ingest/* (trigger + stream + logs)."""
import asyncio

import pytest
from httpx import AsyncClient


async def test_get_logs_empty_when_no_runs(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/ingest/logs?limit=10", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_trigger_rate_limit_429(client: AsyncClient, auth_headers: dict, monkeypatch):
    """Llamadas seguidas devuelven 429 despues de la primera."""
    monkeypatch.setattr("ibkr_control.api.ingest._LAST_TRIGGER", {})

    async def noop(*args, **kwargs) -> int:
        return 999

    monkeypatch.setattr("ibkr_control.api.ingest._launch_manual_job", noop)

    r1 = await client.post("/api/ingest/trigger", json={"kind": "trm"}, headers=auth_headers)
    assert r1.status_code == 200

    r2 = await client.post("/api/ingest/trigger", json={"kind": "trm"}, headers=auth_headers)
    assert r2.status_code == 429


async def test_trigger_invalid_kind_returns_422(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/api/ingest/trigger", json={"kind": "garbage"}, headers=auth_headers
    )
    assert resp.status_code == 422


async def test_stream_unknown_job_returns_404(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/ingest/stream/99999", headers=auth_headers)
    assert resp.status_code == 404


async def test_stream_emits_done_event(client: AsyncClient, auth_headers: dict):
    """Crea un job en el tracker, emite eventos, verifica que el SSE los entrega."""
    from ibkr_control.ingest.job_tracker import get_tracker

    tracker = get_tracker()
    job_id = tracker.create_job()
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
