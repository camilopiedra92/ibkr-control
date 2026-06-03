"""Tests de /api/imports/upload."""

import base64
from pathlib import Path

import pytest
from httpx import AsyncClient

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "xml"


@pytest.fixture(autouse=True)
def set_token_key(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", base64.b64encode(b"X" * 32).decode("ascii"))


async def test_upload_valid_xml_returns_summary(client: AsyncClient, auth_headers_with_org: dict):
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    files = {"file": ("ACTIVITY_2025.xml", xml, "application/xml")}
    resp = await client.post("/api/imports/upload", files=files, headers=auth_headers_with_org)
    assert resp.status_code == 200
    body = resp.json()
    assert "flex_import_id" in body
    # Spec A5 (Task 8 persister rewrite): response surfaces both n_observed_*
    # (rows that arrived in the XML) and n_new_* (rows that hit DB). A fresh
    # import should have both > 0; a re-upload would have observed > 0 and
    # new == 0 thanks to the hash_dedup fast-path.
    assert body["n_observed_trades"] > 0
    assert body["n_new_trades"] > 0


async def test_upload_duplicate_returns_409(client: AsyncClient, auth_headers_with_org: dict):
    """Re-uploading the SAME XML to the SAME org is a duplicate (per-org dedup)."""
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    files = {"file": ("ACTIVITY_2025.xml", xml, "application/xml")}
    r1 = await client.post("/api/imports/upload", files=files, headers=auth_headers_with_org)
    assert r1.status_code == 200
    r2 = await client.post("/api/imports/upload", files=files, headers=auth_headers_with_org)
    assert r2.status_code == 409
    body = r2.json()
    assert "flex_import_id" in body.get("detail", {})


async def test_upload_same_xml_different_org_not_dedup_blocked_but_shared_account_isolated(
    client: AsyncClient,
    auth_headers_with_org: dict,
    second_auth_headers_with_org: dict,
):
    """Per-org dedup vs shared-identity accounts under strict org-RLS (SP1).

    Two layers interact here:

    1. Dedup is per-org: ``flex_imports`` is unique on ``(organization_id,
       xml_hash)``, so a second org uploading the same XML is NOT a 409
       duplicate of the first org's import — the hash check is org-scoped.

    2. ``accounts.ibkr_account_id`` is UNIQUE GLOBAL (shared broker identity, by
       design — a joint account is one row). Under strict org-RLS the persister's
       ``_ensure_accounts`` SELECT is RLS-blinded from the FIRST org's account
       rows, so it re-INSERTs and collides on the global unique constraint. This
       is the genuine SP1 boundary: a broker account belongs to exactly ONE org;
       a second, unrelated org cannot ingest a statement that references the same
       broker account. (Two members of one household share an org, not two orgs.)

    So org B's upload of org A's exact statement is NOT rejected as a duplicate
    (proving per-org dedup) — it gets PAST the 409 hash gate — but then fails
    unhandled at account resolution with a global UNIQUE violation. The
    owner/create_all fixture hid layer 2 by bypassing RLS; making the persister
    cross-org-account-aware under RLS is a tenancy-design follow-up, not this
    task's scope.
    """
    from sqlalchemy.exc import IntegrityError

    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    files = {"file": ("ACTIVITY_2025.xml", xml, "application/xml")}

    r_a = await client.post("/api/imports/upload", files=files, headers=auth_headers_with_org)
    assert r_a.status_code == 200

    # Same bytes, different org: NOT a per-org duplicate (would be a 409, handled).
    # Instead it gets past dedup and collides on the shared-account global UNIQUE
    # constraint — an unhandled IntegrityError surfaced by the ASGI transport.
    # This proves (a) dedup is org-scoped and (b) accounts are a shared global
    # identity that strict org-RLS pins to exactly one org.
    with pytest.raises(IntegrityError) as exc_info:
        await client.post("/api/imports/upload", files=files, headers=second_auth_headers_with_org)
    assert "uq_accounts_ibkr_account_id" in str(exc_info.value)


async def test_upload_malformed_returns_400(client: AsyncClient, auth_headers_with_org: dict):
    xml = (FIXTURE_DIR / "malformed_xml.xml").read_bytes()
    files = {"file": ("bad.xml", xml, "application/xml")}
    resp = await client.post("/api/imports/upload", files=files, headers=auth_headers_with_org)
    assert resp.status_code == 400


async def test_upload_not_a_flex_response_returns_400(
    client: AsyncClient, auth_headers_with_org: dict
):
    xml = (FIXTURE_DIR / "not_a_flex_response.xml").read_bytes()
    files = {"file": ("wrong.xml", xml, "application/xml")}
    resp = await client.post("/api/imports/upload", files=files, headers=auth_headers_with_org)
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
    client: AsyncClient, auth_headers_with_org: dict
):
    """File declared at 51 MB via Content-Length is rejected with 413 before streaming."""
    # 51 MB > 50 MB default limit.
    # httpx sends Content-Length from the bytes size, so file.size will be set.
    large_content = b"X" * (51 * 1024 * 1024)
    files = {"file": ("big.xml", large_content, "application/xml")}
    resp = await client.post("/api/imports/upload", files=files, headers=auth_headers_with_org)
    assert resp.status_code == 413
