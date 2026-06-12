"""Tests de GET /api/ingest/restatements (W3 — restatement log surfacing).

El endpoint lista las filas de restatement_log scopeadas al org (RLS), paginadas,
filtrables por flex_import_id / table_name / sealed_only, ordenadas por
detected_at DESC, id DESC (determinista).
"""

from datetime import datetime, timezone

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def _seed_restatement(owner_engine, *, org_name: str, **values) -> int:
    """Seed a restatement_log row (owner-side) for the org named ``org_name``.

    restatement_log is org-scoped under FORCE RLS; set app.current_org so the
    WITH CHECK passes (same pattern as _seed_connection). Returns the new id.
    """
    session_maker = async_sessionmaker(owner_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        org_id = await session.scalar(
            text("SELECT id FROM organizations WHERE name = :n").bindparams(n=org_name)
        )
        await session.execute(
            text("SELECT set_config('app.current_org', :o, true)").bindparams(o=str(org_id))
        )
        # restatement_log.account_id is NOT NULL FK → accounts (SP2-D9). Seed (or
        # reuse) an account in this org so the FK + NOT NULL are satisfied.
        account_id = await session.scalar(
            text(
                "INSERT INTO accounts (organization_id, ibkr_account_id) "
                "VALUES (:o, :a) "
                "ON CONFLICT (organization_id, ibkr_account_id) DO UPDATE "
                "SET ibkr_account_id = EXCLUDED.ibkr_account_id "
                "RETURNING id"
            ).bindparams(o=org_id, a="U99999001")
        )
        cols = {
            "organization_id": org_id,
            "account_id": account_id,
            "table_name": "open_position_lots",
            "natural_key": "{}",
            "column_name": "qty",
            "kind": "value_update",
            **values,
        }
        # natural_key is JSONB → bind as JSON text and cast.
        natural_key_val = cols.pop("natural_key")
        col_names = ", ".join([*cols, "natural_key"])
        placeholders = ", ".join([*(f":{c}" for c in cols), "CAST(:natural_key AS JSONB)"])
        new_id = await session.scalar(
            text(
                f"INSERT INTO restatement_log ({col_names}) VALUES ({placeholders}) RETURNING id"
            ).bindparams(natural_key=natural_key_val, **cols)
        )
        await session.commit()
    return new_id


async def test_restatements_empty_when_none(client: AsyncClient, auth_headers_with_org: dict):
    resp = await client.get("/api/ingest/restatements", headers=auth_headers_with_org)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_restatements_requires_org(client: AsyncClient, auth_headers: dict):
    """A user with no org membership cannot resolve an authz context (require_scope) → 403."""
    resp = await client.get("/api/ingest/restatements", headers=auth_headers)
    assert resp.status_code == 403


async def test_restatements_requires_auth(client: AsyncClient):
    resp = await client.get("/api/ingest/restatements")
    assert resp.status_code == 401


async def test_restatements_returns_seeded_rows(
    client: AsyncClient, auth_headers_with_org: dict, owner_engine
):
    rid = await _seed_restatement(
        owner_engine,
        org_name="Org Owner Household",
        table_name="open_position_lots",
        natural_key='{"account_id": 1, "symbol": "ICSH", "open_date": "2025-01-02"}',
        column_name="cost_basis_usd",
        old_value="100.00",
        new_value="101.50",
        kind="value_update",
        sealed_year=False,
    )

    resp = await client.get("/api/ingest/restatements", headers=auth_headers_with_org)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == rid
    assert row["table_name"] == "open_position_lots"
    assert row["natural_key"] == {"account_id": 1, "symbol": "ICSH", "open_date": "2025-01-02"}
    assert row["column_name"] == "cost_basis_usd"
    assert row["old_value"] == "100.00"
    assert row["new_value"] == "101.50"
    assert row["kind"] == "value_update"
    assert row["sealed_year"] is False
    assert "detected_at" in row


async def test_restatements_order_detected_at_desc(
    client: AsyncClient, auth_headers_with_org: dict, owner_engine
):
    """Order is detected_at DESC, id DESC (deterministic)."""
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    older = await _seed_restatement(
        owner_engine,
        org_name="Org Owner Household",
        detected_at=now.replace(hour=10),
        column_name="qty",
    )
    newer = await _seed_restatement(
        owner_engine,
        org_name="Org Owner Household",
        detected_at=now.replace(hour=11),
        column_name="cost_basis_usd",
    )

    resp = await client.get("/api/ingest/restatements", headers=auth_headers_with_org)
    ids = [r["id"] for r in resp.json()]
    assert ids == [newer, older]


async def test_restatements_pagination(
    client: AsyncClient, auth_headers_with_org: dict, owner_engine
):
    """limit/offset paginate; default limit 50, max 200."""
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    ids = []
    for i in range(5):
        ids.append(
            await _seed_restatement(
                owner_engine,
                org_name="Org Owner Household",
                detected_at=now.replace(minute=i),
            )
        )
    # Newest-first order → ids reversed.
    expected_desc = list(reversed(ids))

    resp = await client.get(
        "/api/ingest/restatements?limit=2&offset=0", headers=auth_headers_with_org
    )
    page1 = [r["id"] for r in resp.json()]
    assert page1 == expected_desc[:2]

    resp = await client.get(
        "/api/ingest/restatements?limit=2&offset=2", headers=auth_headers_with_org
    )
    page2 = [r["id"] for r in resp.json()]
    assert page2 == expected_desc[2:4]


async def test_restatements_limit_validation(client: AsyncClient, auth_headers_with_org: dict):
    """limit max is 200 → 201 returns 422."""
    resp = await client.get("/api/ingest/restatements?limit=201", headers=auth_headers_with_org)
    assert resp.status_code == 422


async def test_restatements_filter_by_table_name(
    client: AsyncClient, auth_headers_with_org: dict, owner_engine
):
    await _seed_restatement(
        owner_engine, org_name="Org Owner Household", table_name="open_position_lots"
    )
    target = await _seed_restatement(
        owner_engine,
        org_name="Org Owner Household",
        table_name="change_in_dividend_accruals",
    )

    resp = await client.get(
        "/api/ingest/restatements?table_name=change_in_dividend_accruals",
        headers=auth_headers_with_org,
    )
    rows = resp.json()
    assert [r["id"] for r in rows] == [target]
    assert rows[0]["table_name"] == "change_in_dividend_accruals"


async def test_restatements_filter_by_flex_import_id(
    client: AsyncClient, auth_headers_with_org: dict, owner_engine
):
    """Seed two rows with distinct flex_import_id; filter returns only the match.

    flex_import_id is a real FK → seed flex_imports rows first (owner-side, RLS).
    """
    session_maker = async_sessionmaker(owner_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        org_id = await session.scalar(
            text("SELECT id FROM organizations WHERE name = 'Org Owner Household'")
        )
        await session.execute(
            text("SELECT set_config('app.current_org', :o, true)").bindparams(o=str(org_id))
        )
        import_ids = []
        for _ in range(2):
            fid = await session.scalar(
                text(
                    "INSERT INTO flex_imports "
                    "(organization_id, xml_hash, xml_bytes, xml_size_bytes, anyo, "
                    " source, year_status, period_covered_from, period_covered_to, "
                    " status, fetched_at) "
                    "VALUES (:o, :h, :b, :s, 2025, 'web_service', 'rolling', "
                    " '2025-01-01', '2025-12-31', 'ok', NOW()) RETURNING id"
                ).bindparams(o=org_id, h=f"hash-{_}-{org_id}", b=b"x", s=1)
            )
            import_ids.append(fid)
        await session.commit()

    await _seed_restatement(
        owner_engine, org_name="Org Owner Household", flex_import_id=import_ids[0]
    )
    target = await _seed_restatement(
        owner_engine, org_name="Org Owner Household", flex_import_id=import_ids[1]
    )

    resp = await client.get(
        f"/api/ingest/restatements?flex_import_id={import_ids[1]}",
        headers=auth_headers_with_org,
    )
    rows = resp.json()
    assert [r["id"] for r in rows] == [target]
    assert rows[0]["flex_import_id"] == import_ids[1]


async def test_restatements_filter_sealed_only(
    client: AsyncClient, auth_headers_with_org: dict, owner_engine
):
    await _seed_restatement(owner_engine, org_name="Org Owner Household", sealed_year=False)
    sealed = await _seed_restatement(owner_engine, org_name="Org Owner Household", sealed_year=True)

    resp = await client.get(
        "/api/ingest/restatements?sealed_only=true", headers=auth_headers_with_org
    )
    rows = resp.json()
    assert [r["id"] for r in rows] == [sealed]
    assert rows[0]["sealed_year"] is True

    # sealed_only=false (default) returns both.
    resp_all = await client.get("/api/ingest/restatements", headers=auth_headers_with_org)
    assert len(resp_all.json()) == 2


async def test_restatements_scoped_per_org(
    client: AsyncClient,
    auth_headers_with_org: dict,
    second_auth_headers_with_org: dict,
    owner_engine,
):
    """RLS: org B's restatements do not leak into org A's response."""
    await _seed_restatement(owner_engine, org_name="Org Owner 2 Household", column_name="qty")

    # Org A has no restatements → empty.
    resp_a = await client.get("/api/ingest/restatements", headers=auth_headers_with_org)
    assert resp_a.json() == []

    # Org B sees its own.
    resp_b = await client.get("/api/ingest/restatements", headers=second_auth_headers_with_org)
    assert len(resp_b.json()) == 1
