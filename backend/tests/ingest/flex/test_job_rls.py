"""RLS correctness for flex_job.run (SP1, Task 14b).

flex_job.run opens its OWN session from the passed session_factory — it runs
OUTSIDE a request, so nothing set app.current_org for it. In production the app
connects as the non-bypass app_rls role, so without run() establishing its own
org RLS context every org-scoped read/write (creds lookup, flex_imports dedup,
persister writes) would be DEFAULT-DENIED → the manual-refresh background task
and per-org cron job would silently find no credentials / write nothing.

This test exercises run() with a session_factory that connects as app_rls (the
non-bypass role, via swap_dsn_credentials). It would FAIL without the
apply_org_context call inside run() (default-deny: no creds found → RuntimeError,
or no rows written). With the fix it SUCCEEDS and writes only org A's data.
"""

import base64
from pathlib import Path

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ibkr_control.db.rls import app_rls_password
from ibkr_control.ingest.flex import client as client_mod
from ibkr_control.ingest.flex import crypto as crypto_mod
from ibkr_control.ingest.flex import job as flex_job_mod
from tests.conftest_ephemeral_db import swap_dsn_credentials

FIXTURE_DIR = Path(__file__).parent.parent.parent / "fixtures" / "xml"


@pytest_asyncio.fixture
async def rls_job_env(ephemeral_session_factory, ephemeral_db_url, monkeypatch):
    """(app_factory, seed_org_with_creds) for flex_job.run RLS tests.

    - app_factory: async_sessionmaker connecting as the non-bypass ``app_rls``
      login role. This is the session_factory we pass to flex_job.run — exactly
      mirroring production (the app's engine is app_rls).
    - seed_org_with_creds(name, query_id) -> org_id: as the container OWNER,
      creates an Organization + its FlexCredentials. Sets app.current_org to the
      new org before inserting FlexCredentials so the FORCE'd WITH CHECK passes
      (same pattern as rls_session_factory's seed).
    """
    from ibkr_control.db.models.flex_credentials import FlexCredentials
    from ibkr_control.db.models.organizations import Organization

    # Encryption key for FlexCredentials token round-trip.
    test_key = base64.b64encode(b"R" * 32).decode("ascii")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", test_key)

    owner_factory = ephemeral_session_factory

    async def seed_org_with_creds(name: str, query_id: str) -> int:
        async with owner_factory() as s:
            org = Organization(type="personal", name=name)
            s.add(org)
            await s.flush()
            # FORCE RLS applies to the owner too -> set context so the
            # flex_credentials WITH CHECK (organization_id = current_org) passes.
            await s.execute(
                text("SELECT set_config('app.current_org', :o, true)").bindparams(o=str(org.id))
            )
            s.add(
                FlexCredentials(
                    organization_id=org.id,
                    token_encrypted=crypto_mod.encrypt_token("real-token"),
                    ytd_query_id=query_id,
                )
            )
            await s.commit()
            return org.id

    app_dsn = swap_dsn_credentials(ephemeral_db_url, "app_rls", app_rls_password())
    app_engine = create_async_engine(app_dsn, echo=False)
    app_factory = async_sessionmaker(app_engine, expire_on_commit=False)
    try:
        yield app_factory, seed_org_with_creds
    finally:
        await app_engine.dispose()


async def test_run_succeeds_under_app_rls_and_isolates_org(rls_job_env, monkeypatch):
    """run() under app_rls finds org A's creds, ingests, writes only org A's rows.

    Without apply_org_context inside run(), the app_rls session has no
    app.current_org → the FlexCredentials lookup default-denies → run() raises
    RuntimeError("No flex_credentials ..."). With the fix it finds the creds and
    persists, and the data is visible only under current_org=A.
    """
    from ibkr_control.db.models.flex_raw import FlexImport

    app_factory, seed = rls_job_env
    org_a = await seed("Org A", "QUERY-A")
    org_b = await seed("Org B", "QUERY-B")

    # Mock the IBKR client so no network: real 2025 fixture has trades + transfers.
    xml_bytes = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()

    async def fake_send_request(self, query_id):
        assert query_id == "QUERY-A"
        return "REF-A"

    async def fake_poll_statement(self, reference_code, max_wait_seconds=300):
        return xml_bytes

    monkeypatch.setattr(client_mod.FlexClient, "send_request", fake_send_request)
    monkeypatch.setattr(client_mod.FlexClient, "poll_statement", fake_poll_statement)

    # Run as app_rls for org A — must succeed (creds found, persisted).
    flex_import_id = await flex_job_mod.run(app_factory, organization_id=org_a, trigger="cron")
    assert flex_import_id is not None

    # Org A sees its FlexImport + an 'ok' ingest_log; org B sees neither.
    async with app_factory() as s:
        await s.execute(
            text("SELECT set_config('app.current_org', :o, true)").bindparams(o=str(org_a))
        )
        fi = await s.get(FlexImport, flex_import_id)
        assert fi is not None
        assert fi.organization_id == org_a
        assert fi.status == "ok"
        n_logs_a = (
            await s.execute(
                text(
                    "SELECT count(*) FROM ingest_log "
                    "WHERE job_kind='flex' AND status='ok' AND trigger='cron'"
                )
            )
        ).scalar_one()
        assert n_logs_a == 1

    async with app_factory() as s:
        await s.execute(
            text("SELECT set_config('app.current_org', :o, true)").bindparams(o=str(org_b))
        )
        # Org B is fully isolated: it sees NONE of org A's ingest output.
        n_fi_b = (await s.execute(text("SELECT count(*) FROM flex_imports"))).scalar_one()
        assert n_fi_b == 0
        n_logs_b = (await s.execute(text("SELECT count(*) FROM ingest_log"))).scalar_one()
        assert n_logs_b == 0
        n_trades_b = (await s.execute(text("SELECT count(*) FROM trades"))).scalar_one()
        assert n_trades_b == 0
