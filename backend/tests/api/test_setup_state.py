"""Tests for GET /api/setup/state — derived + stored wizard state.

The state endpoint is the single source of truth for the frontend wizard
to decide which step to show. It mixes derived booleans (computed from
table counts) with stored fields (setup_progress JSONB + setup_completed_at).

The `set_token_key` autouse fixture is inherited from tests/api/conftest.py.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from httpx import AsyncClient


async def _connection_rows(app_owner_engine) -> list[dict]:
    """Read all ibkr_flex connections + their detail (owner-side, bypasses RLS)."""
    session_maker = async_sessionmaker(
        app_owner_engine, expire_on_commit=False, class_=AsyncSession
    )
    async with session_maker() as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT c.id, c.status, d.query_id, d.token_encrypted, d.last_rotated_at "
                        "FROM connections c JOIN connection_ibkr_flex d ON d.connection_id = c.id "
                        "WHERE c.provider_type = 'ibkr_flex' ORDER BY c.id"
                    )
                )
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


async def test_state_initial_all_false(client: AsyncClient, auth_headers_with_org: dict):
    """Fresh user: no connection, no participations, no setup_progress flags."""
    r = await client.get("/api/setup/state", headers=auth_headers_with_org)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["step1_credentials"] is False
    assert body["step2_accounts"] is False
    assert body["step3_xmls"] is False
    assert body["step3_n_xmls_uploaded"] == 0
    assert body["setup_completed_at"] is None
    assert body["pending_stash_temp_ids"] == []


async def test_state_step1_credentials_derives_from_connection(
    client: AsyncClient, auth_headers_with_org: dict
):
    """After step1/save, step1_credentials flips True — derived from the
    org having >=1 ibkr_flex connection."""
    r = await client.post(
        "/api/setup/step1/save",
        headers=auth_headers_with_org,
        json={"token": "tok_12345_state", "query_id": "999"},
    )
    assert r.status_code == 200, r.text

    r = await client.get("/api/setup/state", headers=auth_headers_with_org)
    assert r.status_code == 200, r.text
    assert r.json()["step1_credentials"] is True


async def test_step1_save_creates_connection_active(
    client: AsyncClient, auth_headers_with_org: dict, app_owner_engine
):
    """No connection yet → step1/save creates Connection (status='active') + detail."""
    r = await client.post(
        "/api/setup/step1/save",
        headers=auth_headers_with_org,
        json={"token": "tok_first_save_1", "query_id": "QID-1"},
    )
    assert r.status_code == 200, r.text

    rows = await _connection_rows(app_owner_engine)
    assert len(rows) == 1
    assert rows[0]["status"] == "active"
    assert rows[0]["query_id"] == "QID-1"


async def test_step1_save_idempotent_updates_first_and_rotates(
    client: AsyncClient, auth_headers_with_org: dict, app_owner_engine
):
    """Re-save updates the FIRST connection's detail (token/query) + mark_rotated;
    never creates a duplicate. Seed in reauth_required → must return to active."""
    r = await client.post(
        "/api/setup/step1/save",
        headers=auth_headers_with_org,
        json={"token": "tok_initial_save", "query_id": "QID-OLD"},
    )
    assert r.status_code == 200, r.text
    before = await _connection_rows(app_owner_engine)
    assert len(before) == 1
    conn_id = before[0]["id"]
    old_token = before[0]["token_encrypted"]
    old_rotated = before[0]["last_rotated_at"]

    # Force the connection into reauth_required (owner-side) to prove mark_rotated
    # clears it back to active.
    session_maker = async_sessionmaker(
        app_owner_engine, expire_on_commit=False, class_=AsyncSession
    )
    async with session_maker() as session:
        await session.execute(
            text("UPDATE connections SET status='reauth_required' WHERE id=:i").bindparams(
                i=conn_id
            )
        )
        await session.commit()

    r = await client.post(
        "/api/setup/step1/save",
        headers=auth_headers_with_org,
        json={"token": "tok_rotated_save", "query_id": "QID-NEW"},
    )
    assert r.status_code == 200, r.text

    after = await _connection_rows(app_owner_engine)
    assert len(after) == 1, "no duplicate connection created"
    assert after[0]["id"] == conn_id
    assert after[0]["status"] == "active", "mark_rotated cleared reauth_required"
    assert after[0]["query_id"] == "QID-NEW"
    assert after[0]["token_encrypted"] != old_token, "token re-encrypted on update"
    assert after[0]["last_rotated_at"] > old_rotated, "last_rotated_at advanced"
