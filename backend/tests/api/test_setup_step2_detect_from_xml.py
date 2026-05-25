"""Tests for POST /api/setup/step2/detect_from_xml — parse uploaded XML, no persist.

Fallback path when IBKR Flex Web Service is unavailable. Endpoint reads the
file, runs the parser, filters F-shadow accounts, and returns the detected
list. Crucially, it does NOT call the persister and does NOT touch the DB.

The `set_token_key` autouse fixture is inherited from tests/api/conftest.py.
"""
import pytest
from httpx import AsyncClient


# Valid Activity XML with one regular account + one F-shadow. Filter must
# strip the F account from the response.
_XML_OK = b"""<?xml version="1.0"?>
<FlexQueryResponse>
  <FlexStatements>
    <FlexStatement accountId="U99999999" fromDate="20240101" toDate="20241231">
      <AccountInformation accountId="U99999999" accountAlias="Hist" accountType="Joint" name="X" currency="USD"/>
      <AccountInformation accountId="U99999999F" currency="USD"/>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>
"""


# XML where the only account is F-suffix. After filtering nothing remains
# so detected_accounts must be an empty list.
_XML_ONLY_F = b"""<?xml version="1.0"?>
<FlexQueryResponse>
  <FlexStatements>
    <FlexStatement accountId="U99999999F" fromDate="20240101" toDate="20241231">
      <AccountInformation accountId="U99999999F" currency="USD"/>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>
"""


async def test_detect_from_xml_returns_accounts_filtering_f(
    client: AsyncClient, auth_headers: dict
):
    files = {"file": ("y.xml", _XML_OK, "application/xml")}
    r = await client.post(
        "/api/setup/step2/detect_from_xml", headers=auth_headers, files=files
    )
    assert r.status_code == 200, r.text
    body = r.json()
    ids = [a["ibkr_account_id"] for a in body["detected_accounts"]]
    assert ids == ["U99999999"], f"F filtered out; got {ids}"
    assert body["parsed_only"] is True


async def test_detect_from_xml_only_f_returns_empty(
    client: AsyncClient, auth_headers: dict
):
    files = {"file": ("z.xml", _XML_ONLY_F, "application/xml")}
    r = await client.post(
        "/api/setup/step2/detect_from_xml", headers=auth_headers, files=files
    )
    assert r.status_code == 200, r.text
    assert r.json()["detected_accounts"] == []


async def test_detect_from_xml_malformed_returns_422(
    client: AsyncClient, auth_headers: dict
):
    files = {"file": ("bad.xml", b"<not xml", "application/xml")}
    r = await client.post(
        "/api/setup/step2/detect_from_xml", headers=auth_headers, files=files
    )
    assert r.status_code == 422
    body = r.json()
    assert body["detail"]["code"] == "PARSE_ERROR"
