"""E2E: full wizard flow from credentials to finish, asserting invariants."""

from unittest.mock import AsyncMock

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ibkr_control.config import get_settings
from ibkr_control.db.models.accounts import Account
from ibkr_control.db.models.flex_raw import FlexImport
from ibkr_control.db.models.participations import Participation
from ibkr_control.ingest.flex import client as flex_client_mod


_YTD_XML = b"""<?xml version="1.0"?>
<FlexQueryResponse><FlexStatements>
  <FlexStatement accountId="U99999999" fromDate="20260101" toDate="20260524">
    <AccountInformation accountId="U99999999" accountAlias="Joint" accountType="Joint" name="X" currency="USD"/>
    <AccountInformation accountId="U88888888" accountAlias="Solo" accountType="Individual" name="X" currency="USD"/>
    <AccountInformation accountId="U99999999F" currency="USD"/>
    <AccountInformation accountId="U88888888F" currency="USD"/>
  </FlexStatement>
</FlexStatements></FlexQueryResponse>
"""

_HIST_XML = b"""<?xml version="1.0"?>
<FlexQueryResponse><FlexStatements>
  <FlexStatement accountId="U99999999" fromDate="20240101" toDate="20241231">
    <AccountInformation accountId="U99999999" accountAlias="Joint" accountType="Joint" name="X" currency="USD"/>
    <AccountInformation accountId="U88888888" accountAlias="Solo" accountType="Individual" name="X" currency="USD"/>
  </FlexStatement>
</FlexStatements></FlexQueryResponse>
"""


async def test_full_wizard_flow_no_f_in_db(client: AsyncClient, auth_headers: dict, monkeypatch):
    monkeypatch.setattr("ibkr_control.api.setup._trm_backfill_background", AsyncMock())

    # Step 1
    r = await client.post(
        "/api/setup/step1/save",
        headers=auth_headers,
        json={"token": "tok_full_flow_12345", "query_id": "999"},
    )
    assert r.status_code == 200

    # Step 2 detect
    monkeypatch.setattr(flex_client_mod.FlexClient, "send_request", AsyncMock(return_value="r"))
    monkeypatch.setattr(
        flex_client_mod.FlexClient, "get_statement", AsyncMock(return_value=_YTD_XML)
    )
    r = await client.post("/api/setup/step2/detect", headers=auth_headers)
    assert r.status_code == 200, r.text
    detected = r.json()["detected_accounts"]
    assert {a["ibkr_account_id"] for a in detected} == {"U99999999", "U88888888"}

    # Step 2 save
    r = await client.post(
        "/api/setup/step2/save",
        headers=auth_headers,
        json={
            "accounts": [
                {"ibkr_account_id": "U99999999", "alias": "Joint", "pct": "0.5000"},
                {"ibkr_account_id": "U88888888", "alias": "Solo", "pct": "1.0000"},
            ]
        },
    )
    assert r.status_code == 200

    # Step 3 upload + commit
    r = await client.post(
        "/api/setup/step3/upload",
        headers=auth_headers,
        files={"file": ("hist.xml", _HIST_XML, "application/xml")},
    )
    assert r.status_code == 200
    temp_id = r.json()["flex_import_temp_id"]
    r = await client.post(
        "/api/setup/step3/commit", headers=auth_headers, json={"temp_ids": [temp_id]}
    )
    assert r.status_code == 200

    # Finish
    r = await client.post("/api/setup/finish", headers=auth_headers)
    assert r.status_code == 200

    # Assertions: no F-accounts anywhere
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as s:
            accs = (await s.scalars(select(Account))).all()
            ids = {a.ibkr_account_id for a in accs}
            assert ids == {"U99999999", "U88888888"}, ids
            assert not any(i.endswith("F") for i in ids)

            parts = (await s.scalars(select(Participation))).all()
            assert len(parts) == 2

            imports = (await s.scalars(select(FlexImport))).all()
            sources = sorted(i.source for i in imports)
            assert sources == ["manual_upload", "web_service"]
    finally:
        await engine.dispose()
