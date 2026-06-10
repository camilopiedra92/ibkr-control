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


async def test_upload_same_xml_two_orgs_isolated_universes(
    client: AsyncClient,
    app_owner_engine,
    auth_headers_with_org: dict,
    second_auth_headers_with_org: dict,
):
    """Multi-home (spec 2026-06-10, supersedes H2): la misma cuenta broker puede
    existir en N orgs — dos orgs subiendo el MISMO XML obtienen, cada uno, su
    copia independiente.

    Pre-multi-home esto era un 409 de cuenta reclamada (la cuenta era UNIQUE
    global y el segundo org colisionaba bajo RLS). Ahora la unicidad es per-org
    (``uq_accounts_org_ibkr_account_id`` + ``uq_flex_imports_org_xml_hash``):

    1. Dedup sigue per-org → el mismo XML en otro org NO es un 409 duplicado.
    2. _ensure_accounts inserta la cuenta en el org B sin chocar con la del org A.

    Resultado: ambos uploads = 200, DOS flex_imports para el mismo hash (uno por
    org), DOS filas de accounts para la misma ibkr_account_id (una por org).
    """
    from ibkr_control.db.models.accounts import Account
    from ibkr_control.db.models.flex_raw import FlexImport
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    files = {"file": ("ACTIVITY_2025.xml", xml, "application/xml")}

    r_a = await client.post("/api/imports/upload", files=files, headers=auth_headers_with_org)
    assert r_a.status_code == 200

    # Mismos bytes, otro org: pasa el gate de hash org-scoped Y _ensure_accounts
    # inserta la cuenta en el org B (per-org unique). Sin colisión cross-org.
    r_b = await client.post(
        "/api/imports/upload", files=files, headers=second_auth_headers_with_org
    )
    assert r_b.status_code == 200

    # Verificación cross-org vía owner engine (bypassea RLS): cada org tiene SU
    # copia del import y de la cuenta para el mismo hash / mismo ibkr_account_id.
    from ibkr_control.ingest.hash_dedup import xml_hash

    session_maker = async_sessionmaker(
        app_owner_engine, expire_on_commit=False, class_=AsyncSession
    )
    h = xml_hash(xml)
    async with session_maker() as s:
        n_imports_for_hash = await s.scalar(
            select(func.count(FlexImport.id)).where(FlexImport.xml_hash == h)
        )
        assert n_imports_for_hash == 2  # una copia por org, universos aislados
        org_ids = (
            await s.scalars(select(FlexImport.organization_id).where(FlexImport.xml_hash == h))
        ).all()
        assert len(set(org_ids)) == 2  # dos orgs distintos

        # accounts: la misma ibkr_account_id (la conjunta U99999001) existe en
        # los dos orgs — una fila por org, multi-home por diseño.
        n_shared_orgs = await s.scalar(
            select(func.count(func.distinct(Account.organization_id))).where(
                Account.ibkr_account_id == "U99999001"
            )
        )
        assert n_shared_orgs == 2


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
