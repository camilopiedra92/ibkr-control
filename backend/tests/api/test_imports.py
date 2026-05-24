"""Tests de /api/imports/upload."""
import base64
from pathlib import Path

import pytest
from httpx import AsyncClient

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "xml"


@pytest.fixture(autouse=True)
def set_token_key(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", base64.b64encode(b"X" * 32).decode("ascii"))


async def test_upload_valid_xml_returns_summary(client: AsyncClient, auth_headers: dict):
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    files = {"file": ("ACTIVITY_2025.xml", xml, "application/xml")}
    resp = await client.post("/api/imports/upload", files=files, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "flex_import_id" in body
    assert body["n_trades"] > 0


async def test_upload_duplicate_returns_409(client: AsyncClient, auth_headers: dict):
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    files = {"file": ("ACTIVITY_2025.xml", xml, "application/xml")}
    r1 = await client.post("/api/imports/upload", files=files, headers=auth_headers)
    assert r1.status_code == 200
    r2 = await client.post("/api/imports/upload", files=files, headers=auth_headers)
    assert r2.status_code == 409
    body = r2.json()
    assert "flex_import_id" in body.get("detail", {})


async def test_upload_malformed_returns_400(client: AsyncClient, auth_headers: dict):
    xml = (FIXTURE_DIR / "malformed_xml.xml").read_bytes()
    files = {"file": ("bad.xml", xml, "application/xml")}
    resp = await client.post("/api/imports/upload", files=files, headers=auth_headers)
    assert resp.status_code == 400


async def test_upload_not_a_flex_response_returns_400(client: AsyncClient, auth_headers: dict):
    xml = (FIXTURE_DIR / "not_a_flex_response.xml").read_bytes()
    files = {"file": ("wrong.xml", xml, "application/xml")}
    resp = await client.post("/api/imports/upload", files=files, headers=auth_headers)
    assert resp.status_code == 400


async def test_upload_requires_auth(client: AsyncClient):
    xml = (FIXTURE_DIR / "empty_query_response.xml").read_bytes()
    files = {"file": ("e.xml", xml, "application/xml")}
    resp = await client.post("/api/imports/upload", files=files)
    assert resp.status_code == 401


def test_upload_size_limit_default_is_50mb():
    """Default upload size cap is 50 MB (from settings)."""
    from ibkr_control.config import get_settings
    settings = get_settings()
    assert settings.max_xml_size_bytes == 50 * 1024 * 1024


async def test_upload_rejects_oversize_via_content_length(
    client: AsyncClient, auth_headers: dict
):
    """File declared at 51 MB via Content-Length is rejected with 413 before streaming."""
    # 51 MB > 50 MB default limit.
    # httpx sends Content-Length from the bytes size, so file.size will be set.
    large_content = b"X" * (51 * 1024 * 1024)
    files = {"file": ("big.xml", large_content, "application/xml")}
    resp = await client.post("/api/imports/upload", files=files, headers=auth_headers)
    assert resp.status_code == 413
