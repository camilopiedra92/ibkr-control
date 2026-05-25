"""Tests for POST /api/setup/step3/* — upload + commit historical XMLs.

step3/upload stashes a parsed XML in process memory and surfaces any
accounts not yet configured so the frontend can collect %s for them. The
upload path also dedups by SHA-256 against both the DB (committed imports)
and the in-memory stash.

step3/commit drains a list of temp_ids and persists each XML in one
transaction; if any non-shadow account referenced by a stashed XML is not
configured, the call fails with UNRESOLVED_NEW_ACCOUNTS without touching
the DB.

The `set_token_key` autouse fixture is inherited from tests/api/conftest.py.
"""
import pytest
from httpx import AsyncClient


# Activity XML for a brand-new account: not present in DB at first upload.
# Used by happy-path + dup + unresolved tests.
_XML_NEW_ACCT = b"""<?xml version="1.0"?>
<FlexQueryResponse>
  <FlexStatements>
    <FlexStatement accountId="U88888888" fromDate="20240101" toDate="20241231">
      <AccountInformation accountId="U88888888" accountAlias="Hist" accountType="Individual" name="X" currency="USD"/>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>
"""


async def test_step3_upload_stashes_xml_and_returns_new_accounts(
    client: AsyncClient, auth_headers: dict
):
    """Upload returns a fresh temp_id, the parsed year, and the new-account list."""
    files = {"file": ("h.xml", _XML_NEW_ACCT, "application/xml")}
    r = await client.post(
        "/api/setup/step3/upload", headers=auth_headers, files=files
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["flex_import_temp_id"]
    assert body["anyo"] == 2024
    ids = [a["ibkr_account_id"] for a in body["new_accounts"]]
    assert ids == ["U88888888"]
    # period serialization
    assert body["period"]["from"] == "2024-01-01"
    assert body["period"]["to"] == "2024-12-31"


async def test_step3_upload_409_on_duplicate_in_stash(
    client: AsyncClient, auth_headers: dict
):
    """Same bytes uploaded twice → second upload returns 409 DUPLICATE_XML_STASHED.

    Both posts use the same auth_headers value (function-scoped fixture
    resolved once per test), so the stash sees the same user_id for both
    requests.
    """
    files = {"file": ("h.xml", _XML_NEW_ACCT, "application/xml")}
    r1 = await client.post(
        "/api/setup/step3/upload", headers=auth_headers, files=files
    )
    assert r1.status_code == 200, r1.text
    # Re-create the multipart dict because httpx consumes the iterator.
    files2 = {"file": ("h.xml", _XML_NEW_ACCT, "application/xml")}
    r2 = await client.post(
        "/api/setup/step3/upload", headers=auth_headers, files=files2
    )
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "DUPLICATE_XML_STASHED"


async def test_step3_commit_empty_marks_step3_xmls(
    client: AsyncClient, auth_headers: dict
):
    """Empty temp_ids list = explicit `skip historicos`. Sets the step3_xmls flag.

    Returns 0 ids/rows since nothing was persisted; the side effect we care
    about is the user.setup_progress["step3_xmls"] flag flipping to True,
    visible through GET /api/setup/state.
    """
    r = await client.post(
        "/api/setup/step3/commit", headers=auth_headers, json={"temp_ids": []}
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"flex_import_ids": [], "total_rows_inserted": 0}
    state = await client.get("/api/setup/state", headers=auth_headers)
    assert state.status_code == 200
    assert state.json()["step3_xmls"] is True


async def test_step3_commit_rejects_unresolved_new_accounts(
    client: AsyncClient, auth_headers: dict
):
    """Stashed XML references U88888888 but it was never saved → 400."""
    files = {"file": ("h.xml", _XML_NEW_ACCT, "application/xml")}
    r = await client.post(
        "/api/setup/step3/upload", headers=auth_headers, files=files
    )
    assert r.status_code == 200, r.text
    temp_id = r.json()["flex_import_temp_id"]

    r2 = await client.post(
        "/api/setup/step3/commit",
        headers=auth_headers,
        json={"temp_ids": [temp_id]},
    )
    assert r2.status_code == 400
    assert r2.json()["detail"]["code"] == "UNRESOLVED_NEW_ACCOUNTS"


async def test_step3_commit_expired_temp_id_returns_410(
    client: AsyncClient, auth_headers: dict
):
    """temp_id absent from stash (expired or invented) → 410 TEMP_ID_EXPIRED."""
    r = await client.post(
        "/api/setup/step3/commit",
        headers=auth_headers,
        json={"temp_ids": ["nonexistent-uuid"]},
    )
    assert r.status_code == 410
    assert r.json()["detail"]["code"] == "TEMP_ID_EXPIRED"
