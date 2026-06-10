"""Tests for POST /api/setup/step2/detect — mock FlexClient end-to-end.

step2/detect now iterates ALL active ibkr_flex connections of the org (W1),
accumulating detected accounts deduped by ibkr_account_id. Per connection:
- FlexAuthError/FlexQueryNotFoundError → mark_auth_failed (reauth_required) + continue
- FlexBusyError → server-side retry [5,15,30]s, then surfaces as transient

Failure aggregation: SOME succeed → return accumulated accounts (partial OK);
ALL fail → best error (any auth-class → 401; else all-busy → 503 IBKR_BUSY).

Covers happy path (F-shadow filtered), the retry loop on 1001 busy responses,
retry exhaustion, auth errors, multi-connection union, partial success, and the
no-connection short-circuit. The `set_token_key` autouse fixture is inherited
from tests/api/conftest.py.
"""

from unittest.mock import AsyncMock

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ibkr_control.ingest.flex import client as flex_client_mod


# Minimal valid Activity XML: two AccountInformation rows — one regular (U99999999),
# one F-shadow (U99999999F). The persister/endpoint must surface only the regular
# one. Dates use YYYYMMDD; period_to.year = 2026.
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

# A second org's account, used to prove the multi-connection union dedupes by id.
_FAKE_XML_B = b"""<?xml version="1.0"?>
<FlexQueryResponse>
  <FlexStatements>
    <FlexStatement accountId="U88888888" fromDate="20260101" toDate="20260524">
      <AccountInformation accountId="U88888888" accountAlias="Second" accountType="Individual" name="TEST USER" currency="USD"/>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>
"""


async def _seed_creds(client: AsyncClient, auth_headers_with_org: dict, *, query_id="999") -> None:
    """Step1/save must run first so detect has a connection to iterate."""
    r = await client.post(
        "/api/setup/step1/save",
        headers=auth_headers_with_org,
        json={"token": "tok_value_12345", "query_id": query_id},
    )
    assert r.status_code == 200, r.text


async def _seed_second_connection(app_owner_engine, *, query_id="QID-2") -> int:
    """Add a SECOND ibkr_flex connection to the same org (owner-side, bypasses RLS).

    Used to exercise the multi-connection detect loop. Returns its id.
    """
    from ibkr_control.ingest.flex.crypto import encrypt_token

    session_maker = async_sessionmaker(
        app_owner_engine, expire_on_commit=False, class_=AsyncSession
    )
    async with session_maker() as session:
        org_id = await session.scalar(
            text("SELECT id FROM organizations WHERE name = 'Org Owner Household'")
        )
        inst_id = await session.scalar(text("SELECT id FROM institutions WHERE code = 'ibkr'"))
        await session.execute(
            text("SELECT set_config('app.current_org', :o, true)").bindparams(o=str(org_id))
        )
        conn_id = await session.scalar(
            text(
                "INSERT INTO connections (organization_id, institution_id, provider_type, status) "
                "VALUES (:o, :i, 'ibkr_flex', 'active') RETURNING id"
            ).bindparams(o=org_id, i=inst_id)
        )
        await session.execute(
            text(
                "INSERT INTO connection_ibkr_flex "
                "(connection_id, organization_id, token_encrypted, query_id) "
                "VALUES (:c, :o, :t, :q)"
            ).bindparams(
                c=conn_id, o=org_id, t=encrypt_token("second-token-1234567890"), q=query_id
            )
        )
        await session.commit()
    return conn_id


async def _connection_statuses(app_owner_engine) -> list[str]:
    session_maker = async_sessionmaker(
        app_owner_engine, expire_on_commit=False, class_=AsyncSession
    )
    async with session_maker() as session:
        rows = (
            await session.execute(
                text("SELECT status FROM connections WHERE provider_type='ibkr_flex' ORDER BY id")
            )
        ).all()
    return [r[0] for r in rows]


async def test_step2_detect_happy_path_returns_accounts_filtering_f(
    client: AsyncClient, auth_headers_with_org: dict, monkeypatch
):
    """Single send_request + get_statement → parsed accounts, F filtered out."""
    await _seed_creds(client, auth_headers_with_org)

    monkeypatch.setattr(
        flex_client_mod.FlexClient,
        "send_request",
        AsyncMock(return_value="ref-001"),
    )
    monkeypatch.setattr(
        flex_client_mod.FlexClient,
        "get_statement",
        AsyncMock(return_value=_FAKE_XML),
    )

    r = await client.post("/api/setup/step2/detect", headers=auth_headers_with_org)
    assert r.status_code == 200, r.text
    body = r.json()
    ids = [a["ibkr_account_id"] for a in body["detected_accounts"]]
    assert ids == ["U99999999"], f"F filtered out; got {ids}"
    assert body["detected_accounts"][0]["suggested_alias"] == "Main"
    assert body["detected_accounts"][0]["account_type"] == "Individual"
    assert body["flex_import_id"] > 0


async def test_step2_detect_unions_accounts_across_connections(
    client: AsyncClient, auth_headers_with_org: dict, app_owner_engine, monkeypatch
):
    """Two active connections with different accounts → union, deduped by id."""
    await _seed_creds(client, auth_headers_with_org)
    await _seed_second_connection(app_owner_engine)

    monkeypatch.setattr(flex_client_mod.FlexClient, "send_request", AsyncMock(return_value="ref"))
    # First connection returns XML A, second returns XML B (distinct accounts ->
    # distinct hashes -> both persist).
    monkeypatch.setattr(
        flex_client_mod.FlexClient,
        "get_statement",
        AsyncMock(side_effect=[_FAKE_XML, _FAKE_XML_B]),
    )

    r = await client.post("/api/setup/step2/detect", headers=auth_headers_with_org)
    assert r.status_code == 200, r.text
    ids = sorted(a["ibkr_account_id"] for a in r.json()["detected_accounts"])
    assert ids == ["U88888888", "U99999999"]


async def test_step2_detect_partial_success_marks_failed_conn_and_returns(
    client: AsyncClient, auth_headers_with_org: dict, app_owner_engine, monkeypatch
):
    """One conn auth-fails, the other succeeds → 200 with the successful
    accounts; the failing connection transitions to reauth_required."""
    await _seed_creds(client, auth_headers_with_org)
    await _seed_second_connection(app_owner_engine)

    # First connection: auth fail. Second: success.
    monkeypatch.setattr(
        flex_client_mod.FlexClient,
        "send_request",
        AsyncMock(side_effect=[flex_client_mod.FlexAuthError("1003", "bad"), "ref-ok"]),
    )
    monkeypatch.setattr(
        flex_client_mod.FlexClient,
        "get_statement",
        AsyncMock(return_value=_FAKE_XML_B),
    )

    r = await client.post("/api/setup/step2/detect", headers=auth_headers_with_org)
    assert r.status_code == 200, r.text
    ids = [a["ibkr_account_id"] for a in r.json()["detected_accounts"]]
    assert ids == ["U88888888"]

    statuses = await _connection_statuses(app_owner_engine)
    assert statuses == ["reauth_required", "active"]


async def test_step2_detect_retries_on_1001_then_succeeds(
    client: AsyncClient, auth_headers_with_org: dict, monkeypatch
):
    """Two 1001 busy responses then success → send_request awaited 3 times."""
    await _seed_creds(client, auth_headers_with_org)

    busy = flex_client_mod.FlexBusyError("1001")
    send_mock = AsyncMock(side_effect=[busy, busy, "ref-ok"])
    monkeypatch.setattr(flex_client_mod.FlexClient, "send_request", send_mock)
    monkeypatch.setattr(
        flex_client_mod.FlexClient,
        "get_statement",
        AsyncMock(return_value=_FAKE_XML),
    )
    # Zero out the retry delays so this test doesn't actually sleep [5,15,30]s.
    monkeypatch.setattr("ibkr_control.api.setup._DETECT_RETRY_DELAYS", [0, 0, 0])

    r = await client.post("/api/setup/step2/detect", headers=auth_headers_with_org)
    assert r.status_code == 200, r.text
    assert send_mock.await_count == 3


async def test_step2_detect_503_after_max_retries(
    client: AsyncClient, auth_headers_with_org: dict, monkeypatch
):
    """Four consecutive 1001s (only connection) → 503 IBKR_BUSY."""
    await _seed_creds(client, auth_headers_with_org)
    busy = flex_client_mod.FlexBusyError("1001")
    monkeypatch.setattr(
        flex_client_mod.FlexClient,
        "send_request",
        AsyncMock(side_effect=busy),
    )
    monkeypatch.setattr("ibkr_control.api.setup._DETECT_RETRY_DELAYS", [0, 0, 0])

    r = await client.post("/api/setup/step2/detect", headers=auth_headers_with_org)
    assert r.status_code == 503
    body = r.json()
    assert body["detail"]["code"] == "IBKR_BUSY"


async def test_step2_detect_401_on_invalid_token(
    client: AsyncClient, auth_headers_with_org: dict, app_owner_engine, monkeypatch
):
    """FlexAuthError on the only connection → 401 INVALID_TOKEN + reauth_required."""
    await _seed_creds(client, auth_headers_with_org)
    monkeypatch.setattr(
        flex_client_mod.FlexClient,
        "send_request",
        AsyncMock(side_effect=flex_client_mod.FlexAuthError("1003", "bad token")),
    )
    r = await client.post("/api/setup/step2/detect", headers=auth_headers_with_org)
    assert r.status_code == 401
    assert r.json()["detail"] == "INVALID_TOKEN"

    assert await _connection_statuses(app_owner_engine) == ["reauth_required"]


async def test_step2_detect_400_when_no_connection(
    client: AsyncClient, auth_headers_with_org: dict
):
    """No ibkr_flex connection → 400 MISSING_CREDENTIALS (short-circuit before IBKR)."""
    r = await client.post("/api/setup/step2/detect", headers=auth_headers_with_org)
    assert r.status_code == 400
    assert r.json()["detail"] == "MISSING_CREDENTIALS"
