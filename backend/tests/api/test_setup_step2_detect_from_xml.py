"""Tests for POST /api/setup/step2/detect_from_xml — IBKR-offline fallback.

Persists the uploaded XML as source='manual_upload' so step2/save can validate
detected accounts against the `accounts` table. The persister dedups by
SHA-256 → re-uploading the same XML is idempotent. F-shadow accounts are
filtered at the persister boundary.

The `set_token_key` autouse fixture is inherited from tests/api/conftest.py.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ibkr_control.config import get_settings
from ibkr_control.db.models.accounts import Account
from ibkr_control.db.models.flex_raw import FlexImport


# Valid Activity XML with one regular account + one F-shadow. Filter must
# strip the F account from the response AND from the persisted accounts table.
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
    assert body["parsed_only"] is False


async def test_detect_from_xml_persists_accounts_and_flex_import(
    client: AsyncClient, auth_headers: dict
):
    """Regression: detect_from_xml must persist so step2/save sees the accounts."""
    files = {"file": ("y.xml", _XML_OK, "application/xml")}
    r = await client.post(
        "/api/setup/step2/detect_from_xml", headers=auth_headers, files=files
    )
    assert r.status_code == 200

    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as s:
            accs = (await s.scalars(select(Account))).all()
            assert {a.ibkr_account_id for a in accs} == {"U99999999"}
            imports = (await s.scalars(select(FlexImport))).all()
            assert len(imports) == 1
            assert imports[0].source == "manual_upload"
    finally:
        await engine.dispose()


async def test_detect_from_xml_is_idempotent_on_same_sha(
    client: AsyncClient, auth_headers: dict
):
    """Re-uploading the same XML must not create duplicate flex_imports."""
    files = {"file": ("y.xml", _XML_OK, "application/xml")}
    r1 = await client.post(
        "/api/setup/step2/detect_from_xml", headers=auth_headers, files=files
    )
    assert r1.status_code == 200
    files2 = {"file": ("y.xml", _XML_OK, "application/xml")}
    r2 = await client.post(
        "/api/setup/step2/detect_from_xml", headers=auth_headers, files=files2
    )
    assert r2.status_code == 200

    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as s:
            imports = (await s.scalars(select(FlexImport))).all()
            assert len(imports) == 1
    finally:
        await engine.dispose()


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
