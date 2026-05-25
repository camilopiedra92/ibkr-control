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

import pytest
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

    dispatched: list[int] = []

    async def fake_trm(user_id: int) -> None:
        dispatched.append(user_id)

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
    assert isinstance(dispatched[0], int)
