"""Tests del orchestrator flex_job (lock + log + parser + persister)."""

import logging
from datetime import date as _date
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from lxml.etree import XMLSyntaxError

from ibkr_control.ingest.flex import job as flex_job
from ibkr_control.db.models.flex_raw import FlexImport
from ibkr_control.db.models.ingest_log import IngestLog

FIXTURE_DIR = Path(__file__).parent.parent.parent / "fixtures" / "xml"


@pytest.mark.asyncio
async def test_ingest_xml_happy_path(db_session: AsyncSession, sample_org):
    """ingest_xml recibe bytes, parsea, persiste, loggea."""
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    flex_import_id = await flex_job.ingest_xml(
        db_session,
        organization_id=sample_org.id,
        xml_bytes=xml,
        source="manual_upload",
        trigger="wizard",
    )
    assert flex_import_id is not None

    fi = await db_session.scalar(select(FlexImport).where(FlexImport.id == flex_import_id))
    assert fi.status == "ok"
    assert fi.organization_id == sample_org.id

    # ingest_log debe tener 1 row ok del kind manual_upload
    n_logs = await db_session.scalar(
        select(func.count(IngestLog.id)).where(
            IngestLog.job_kind == "manual_upload",
            IngestLog.organization_id == sample_org.id,
            IngestLog.status == "ok",
        )
    )
    assert n_logs >= 1


@pytest.mark.asyncio
async def test_ingest_xml_duplicate_returns_existing(db_session: AsyncSession, sample_org):
    """Re-upload del mismo XML devuelve el flex_import_id existente."""
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    id_1 = await flex_job.ingest_xml(
        db_session,
        organization_id=sample_org.id,
        xml_bytes=xml,
        source="manual_upload",
        trigger="wizard",
    )
    id_2 = await flex_job.ingest_xml(
        db_session,
        organization_id=sample_org.id,
        xml_bytes=xml,
        source="manual_upload",
        trigger="wizard",
    )
    assert id_1 == id_2


@pytest.mark.asyncio
async def test_ingest_xml_logs_failure_on_parse_error(
    db_session: AsyncSession, db_engine, sample_org
):
    """Si el parser lanza XMLSyntaxError, ingest_log queda en 'failed' con error_message."""
    bad_xml = (FIXTURE_DIR / "malformed_xml.xml").read_bytes()
    with pytest.raises(XMLSyntaxError):
        await flex_job.ingest_xml(
            db_session,
            organization_id=sample_org.id,
            xml_bytes=bad_xml,
            source="manual_upload",
            trigger="wizard",
        )

    # El log row debe haber quedado con status='failed'.
    # El context manager de log hace commit propio (via ingest_log_entry finally),
    # asi que aunque la sesion quede en estado de error, el log row esta committed.
    # Abrimos una sesion nueva para verificar (la sesion principal puede estar
    # en estado de error post-excepcion).
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession as AS2

    maker2 = async_sessionmaker(db_engine, expire_on_commit=False, class_=AS2)
    async with maker2() as s2:
        row = await s2.scalar(
            select(IngestLog)
            .where(
                IngestLog.organization_id == sample_org.id,
                IngestLog.status == "failed",
            )
            .order_by(IngestLog.id.desc())
            .limit(1)
        )
        assert row is not None, "Expected a failed ingest_log row"
        assert row.status == "failed"
        assert row.error_message is not None


@pytest.mark.asyncio
async def test_ingest_xml_rolls_back_persister_on_failure(
    db_session: AsyncSession, db_engine, sample_org
):
    """Si persist() falla, el SAVEPOINT del job revierte writes parciales del
    persister pero el ingest_log 'failed' persiste.

    Post phase 2.5 rewrite: el persister es idempotent y absorbe intra-batch
    duplicates de transaction_id via ON CONFLICT DO NOTHING — ya no levanta
    IntegrityError en ese caso. Para ejercitar la ruta de error, forzamos un
    fallo "real" pasando un FK inválido (account_id que no existe) en un row
    de cash_transactions construido a mano via patch del persister.
    """
    from datetime import date
    from decimal import Decimal
    from unittest.mock import patch
    from ibkr_control.ingest.flex._models import (
        ParsedAccount,
        ParsedCashTransaction,
        ParsedXML,
    )

    account_id = "U99999042"

    def _parsed_with_bad_account() -> "ParsedXML":
        return ParsedXML(
            anyo=2025,
            period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31),
            accounts=[ParsedAccount(ibkr_account_id=account_id, currency="USD")],
            trades=[],
            closed_lots=[],
            open_position_lots=[],
            cash_transactions=[
                ParsedCashTransaction(
                    transaction_id="TXN-CASH-001",
                    ibkr_account_id=account_id,
                    type="Dividends",
                    currency="USD",
                    amount_usd=Decimal("100"),
                    description="ok",
                    date=date(2025, 3, 1),
                    symbol=None,
                ),
            ],
            transfers=[],
            change_in_dividend_accruals=[],
            open_dividend_accruals=[],
        )

    async def fake_ensure_accounts(session, ibkr_ids, *, organization_id):
        # Devuelve un mapping con un account_id inválido (FK violation al INSERT cash_tx)
        return {account_id: 999_999_999}

    with (
        patch(
            "ibkr_control.ingest.flex.job.flex_parser_mod.parse",
            return_value=_parsed_with_bad_account(),
        ),
        patch(
            "ibkr_control.ingest.flex.persister._ensure_accounts",
            side_effect=fake_ensure_accounts,
        ),
    ):
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            await flex_job.ingest_xml(
                db_session,
                organization_id=sample_org.id,
                xml_bytes=b"<xml>bad-fk</xml>",
                source="manual_upload",
                trigger="wizard",
            )

    # Verify via a fresh session (db_session may be in error state after exception)
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession as AS2

    maker2 = async_sessionmaker(db_engine, expire_on_commit=False, class_=AS2)
    async with maker2() as s2:
        # Post-Task-7: a poison FlexImport row must exist (R2 contract).
        # The SAVEPOINT rollback still reverts partial persister writes (e.g.
        # no CashTransaction rows), but the poison row itself is written outside
        # the savepoint and committed by ingest_log_entry's finally clause.
        from ibkr_control.ingest.hash_dedup import xml_hash
        from ibkr_control.db.models.flex_raw import CashTransaction

        bad_hash = xml_hash(b"<xml>bad-fk</xml>")
        fi = await s2.scalar(select(FlexImport).where(FlexImport.xml_hash == bad_hash))
        assert fi is not None, "Poison FlexImport row must exist after persist failure"
        assert fi.status == "poison", f"FlexImport should be status='poison', got {fi.status!r}"

        # SAVEPOINT rollback was effective: no partial cash_transactions from the bad insert
        n_cash = await s2.scalar(select(func.count(CashTransaction.id)))
        assert n_cash == 0, "CashTransaction writes should have been rolled back by SAVEPOINT"

        # The ingest_log 'failed' row must persist
        log_row = await s2.scalar(
            select(IngestLog)
            .where(
                IngestLog.organization_id == sample_org.id,
                IngestLog.status == "failed",
            )
            .order_by(IngestLog.id.desc())
            .limit(1)
        )
        assert log_row is not None, "Expected a failed ingest_log row to persist"


# ---------------------------------------------------------------------------
# Tests for run() — the cron entry point (W1: iterates connections)
# ---------------------------------------------------------------------------
#
# run() now returns dict[connection_id, flex_import_id | None] and iterates ALL
# active ibkr_flex connections of the org, isolating per-connection failures
# (each transitions its own connection_state + writes its own ingest_log row).


def _set_token_key(monkeypatch, seed: bytes = b"Y") -> None:
    import base64

    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", base64.b64encode(seed * 32).decode("ascii"))


@pytest.mark.asyncio
async def test_run_happy_path_with_mocked_flex_client(
    monkeypatch, db_session: AsyncSession, db_engine, sample_org
):
    """run() fetches from Flex WS (mocked) for the org's single connection,
    persists XML, marks log ok, and returns {connection_id: flex_import_id}."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from ibkr_control.db.models.flex_raw import FlexImport
    from ibkr_control.db.models.ingest_log import IngestLog
    from ibkr_control.ingest.flex import client as client_mod
    from ibkr_control.ingest.flex import job as flex_job_mod

    from tests.ingest.flex.conftest import _seed_connection

    _set_token_key(monkeypatch)
    conn_id = await _seed_connection(db_session, sample_org.id, query_id="QUERY-123")

    xml_bytes = (FIXTURE_DIR / "empty_query_response.xml").read_bytes()

    async def fake_send_request(self, query_id):
        assert query_id == "QUERY-123"
        return "REF-999"

    async def fake_poll_statement(self, reference_code, max_wait_seconds=300):
        assert reference_code == "REF-999"
        return xml_bytes

    monkeypatch.setattr(client_mod.FlexClient, "send_request", fake_send_request)
    monkeypatch.setattr(client_mod.FlexClient, "poll_statement", fake_poll_statement)

    SessionLocal = async_sessionmaker(db_engine, expire_on_commit=False)

    results = await flex_job_mod.run(SessionLocal, organization_id=sample_org.id, trigger="cron")
    assert set(results.keys()) == {conn_id}
    flex_import_id = results[conn_id]
    assert flex_import_id is not None

    async with SessionLocal() as s2:
        fi = await s2.get(FlexImport, flex_import_id)
        assert fi is not None
        assert fi.organization_id == sample_org.id
        assert fi.source == "web_service"
        assert fi.status == "ok"
        assert fi.connection_id == conn_id

        n_logs = await s2.scalar(
            select(func.count(IngestLog.id)).where(
                IngestLog.job_kind == "flex",
                IngestLog.organization_id == sample_org.id,
                IngestLog.status == "ok",
                IngestLog.trigger == "cron",
                IngestLog.connection_id == conn_id,
            )
        )
        assert n_logs >= 1

        # Connection ended active + last_sync_status='ok'.
        from ibkr_control.db.models.connections import Connection

        conn = await s2.get(Connection, conn_id)
        assert conn.status == "active"
        assert conn.last_sync_status == "ok"


@pytest.mark.asyncio
async def test_run_iterates_all_active_connections(
    monkeypatch, db_session: AsyncSession, db_engine, sample_org
):
    """Two active connections in the org -> two flex_imports (each stamped with
    its own connection_id), two ok ingest_log rows, both connections active."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from ibkr_control.db.models.connections import Connection
    from ibkr_control.db.models.flex_raw import FlexImport
    from ibkr_control.db.models.ingest_log import IngestLog
    from ibkr_control.ingest.flex import client as client_mod
    from ibkr_control.ingest.flex import job as flex_job_mod

    from tests.ingest.flex.conftest import _seed_connection

    _set_token_key(monkeypatch)
    conn_a = await _seed_connection(db_session, sample_org.id, query_id="Q-A")
    conn_b = await _seed_connection(db_session, sample_org.id, query_id="Q-B")

    # DIFFERENT fixtures (distinct anyo) per connection so each produces its own
    # import: distinct bytes escape the per-org hash dedup
    # (uq_flex_imports_org_xml_hash), and distinct anyo escapes the R1 latest-1
    # rolling cleanup (keyed on (org, anyo, source)) — otherwise the second
    # persist would evict the first.
    xml_by_query = {
        "Q-A": (FIXTURE_DIR / "ACTIVITY_2024_sanitized.xml").read_bytes(),
        "Q-B": (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes(),
    }
    seen_query = {"current": None}

    async def fake_send_request(self, query_id):
        seen_query["current"] = query_id
        return f"REF-{query_id}"

    async def fake_poll_statement(self, reference_code, max_wait_seconds=300):
        return xml_by_query[seen_query["current"]]

    monkeypatch.setattr(client_mod.FlexClient, "send_request", fake_send_request)
    monkeypatch.setattr(client_mod.FlexClient, "poll_statement", fake_poll_statement)

    SessionLocal = async_sessionmaker(db_engine, expire_on_commit=False)
    results = await flex_job_mod.run(SessionLocal, organization_id=sample_org.id, trigger="cron")

    assert set(results.keys()) == {conn_a, conn_b}
    assert all(v is not None for v in results.values())
    assert results[conn_a] != results[conn_b]

    async with SessionLocal() as s2:
        n_fi = await s2.scalar(
            select(func.count(FlexImport.id)).where(FlexImport.organization_id == sample_org.id)
        )
        assert n_fi == 2
        # Each import stamped with the right connection_id.
        fi_a = await s2.get(FlexImport, results[conn_a])
        fi_b = await s2.get(FlexImport, results[conn_b])
        assert fi_a.connection_id == conn_a
        assert fi_b.connection_id == conn_b

        n_logs = await s2.scalar(
            select(func.count(IngestLog.id)).where(
                IngestLog.job_kind == "flex",
                IngestLog.organization_id == sample_org.id,
                IngestLog.status == "ok",
            )
        )
        assert n_logs == 2

        for cid in (conn_a, conn_b):
            conn = await s2.get(Connection, cid)
            assert conn.status == "active"
            assert conn.last_sync_status == "ok"


@pytest.mark.asyncio
async def test_run_skips_disabled_connections(
    monkeypatch, db_session: AsyncSession, db_engine, sample_org
):
    """A disabled connection is not iterated; only the active one produces an
    import, and the disabled one is left untouched (no sync timestamp)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from ibkr_control.db.models.connections import Connection
    from ibkr_control.db.models.flex_raw import FlexImport
    from ibkr_control.ingest.flex import client as client_mod
    from ibkr_control.ingest.flex import job as flex_job_mod

    from tests.ingest.flex.conftest import _seed_connection

    _set_token_key(monkeypatch)
    conn_active = await _seed_connection(db_session, sample_org.id, query_id="Q-ON")
    conn_off = await _seed_connection(
        db_session, sample_org.id, query_id="Q-OFF", status="disabled"
    )

    xml_bytes = (FIXTURE_DIR / "empty_query_response.xml").read_bytes()

    async def fake_send_request(self, query_id):
        assert query_id == "Q-ON", "disabled connection must not be fetched"
        return "REF-ON"

    async def fake_poll_statement(self, reference_code, max_wait_seconds=300):
        return xml_bytes

    monkeypatch.setattr(client_mod.FlexClient, "send_request", fake_send_request)
    monkeypatch.setattr(client_mod.FlexClient, "poll_statement", fake_poll_statement)

    SessionLocal = async_sessionmaker(db_engine, expire_on_commit=False)
    results = await flex_job_mod.run(SessionLocal, organization_id=sample_org.id, trigger="cron")

    assert set(results.keys()) == {conn_active}

    async with SessionLocal() as s2:
        n_fi = await s2.scalar(
            select(func.count(FlexImport.id)).where(FlexImport.organization_id == sample_org.id)
        )
        assert n_fi == 1
        off = await s2.get(Connection, conn_off)
        assert off.status == "disabled"
        assert off.last_sync_at is None


@pytest.mark.asyncio
async def test_run_auth_error_transitions_reauth_required(
    monkeypatch, db_session: AsyncSession, db_engine, sample_org
):
    """Connection A raises FlexAuthError -> reauth_required + its ingest_log row
    failed; connection B still succeeds. run() does NOT raise (>=1 success)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from ibkr_control.db.models.connections import Connection
    from ibkr_control.db.models.flex_raw import FlexImport
    from ibkr_control.db.models.ingest_log import IngestLog
    from ibkr_control.ingest.flex import client as client_mod
    from ibkr_control.ingest.flex import job as flex_job_mod

    from tests.ingest.flex.conftest import _seed_connection

    _set_token_key(monkeypatch)
    conn_a = await _seed_connection(db_session, sample_org.id, query_id="Q-BAD")
    conn_b = await _seed_connection(db_session, sample_org.id, query_id="Q-GOOD")

    xml_bytes = (FIXTURE_DIR / "empty_query_response.xml").read_bytes()
    seen_query = {"current": None}

    async def fake_send_request(self, query_id):
        seen_query["current"] = query_id
        if query_id == "Q-BAD":
            raise client_mod.FlexAuthError("1018", "bad token")
        return "REF-GOOD"

    async def fake_poll_statement(self, reference_code, max_wait_seconds=300):
        return xml_bytes

    monkeypatch.setattr(client_mod.FlexClient, "send_request", fake_send_request)
    monkeypatch.setattr(client_mod.FlexClient, "poll_statement", fake_poll_statement)

    SessionLocal = async_sessionmaker(db_engine, expire_on_commit=False)
    results = await flex_job_mod.run(SessionLocal, organization_id=sample_org.id, trigger="cron")

    # B succeeded; A is absent from results (it failed before persist).
    assert conn_b in results and results[conn_b] is not None
    assert conn_a not in results

    async with SessionLocal() as s2:
        a = await s2.get(Connection, conn_a)
        assert a.status == "reauth_required"
        assert "1018" in (a.status_reason or "")
        assert a.last_sync_status == "failed"

        b = await s2.get(Connection, conn_b)
        assert b.status == "active"
        assert b.last_sync_status == "ok"

        # A's ingest_log row is failed and carries A's connection_id.
        n_failed_a = await s2.scalar(
            select(func.count(IngestLog.id)).where(
                IngestLog.organization_id == sample_org.id,
                IngestLog.status == "failed",
                IngestLog.connection_id == conn_a,
            )
        )
        assert n_failed_a == 1

        # B produced exactly one ok import.
        n_fi = await s2.scalar(
            select(func.count(FlexImport.id)).where(FlexImport.connection_id == conn_b)
        )
        assert n_fi == 1


@pytest.mark.asyncio
async def test_run_raises_when_all_connections_fail(
    monkeypatch, db_session: AsyncSession, db_engine, sample_org
):
    """All connections raise FlexAuthError -> run() re-raises the last exception
    (the _run_manual/SSE contract: a fully-failed run must surface)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from ibkr_control.ingest.flex import client as client_mod
    from ibkr_control.ingest.flex import job as flex_job_mod

    from tests.ingest.flex.conftest import _seed_connection

    _set_token_key(monkeypatch)
    await _seed_connection(db_session, sample_org.id, query_id="Q-1")
    await _seed_connection(db_session, sample_org.id, query_id="Q-2")

    async def fake_send_request(self, query_id):
        raise client_mod.FlexAuthError("1018", f"bad {query_id}")

    async def fake_poll_statement(self, reference_code, max_wait_seconds=300):
        raise AssertionError("poll should not be reached")

    monkeypatch.setattr(client_mod.FlexClient, "send_request", fake_send_request)
    monkeypatch.setattr(client_mod.FlexClient, "poll_statement", fake_poll_statement)

    SessionLocal = async_sessionmaker(db_engine, expire_on_commit=False)
    with pytest.raises(client_mod.FlexAuthError):
        await flex_job_mod.run(SessionLocal, organization_id=sample_org.id, trigger="cron")


@pytest.mark.asyncio
async def test_run_no_active_connections_raises(
    monkeypatch, db_session: AsyncSession, db_engine, sample_org
):
    """An org with no active connections (all disabled) -> RuntimeError with a
    clear message (nothing to fetch)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from ibkr_control.ingest.flex import job as flex_job_mod

    from tests.ingest.flex.conftest import _seed_connection

    _set_token_key(monkeypatch)
    await _seed_connection(db_session, sample_org.id, query_id="Q-OFF", status="disabled")

    SessionLocal = async_sessionmaker(db_engine, expire_on_commit=False)
    with pytest.raises(RuntimeError, match="No active ibkr_flex connections"):
        await flex_job_mod.run(SessionLocal, organization_id=sample_org.id, trigger="cron")


@pytest.mark.asyncio
async def test_run_idempotent_across_different_xmls_with_overlapping_trades(
    monkeypatch, db_session: AsyncSession, db_engine, sample_org
):
    """Regression test para D13 [BUG-FIXED] (2026-05-25).

    Post Phase 2.5: el persister es idempotent fila por fila via UPSERT por
    natural key. Dos runs con XMLs distintos (hashes distintos) pero trades
    overlapping deben ambos terminar OK, sin duplicados en DB. W1: la misma
    connection corre dos veces.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from ibkr_control.db.models.flex_raw import FlexImport, Trade
    from ibkr_control.db.models.ingest_log import IngestLog
    from ibkr_control.ingest.flex import client as client_mod
    from ibkr_control.ingest.flex import job as flex_job_mod

    from tests.ingest.flex.conftest import _seed_connection

    _set_token_key(monkeypatch, seed=b"D")
    conn_id = await _seed_connection(db_session, sample_org.id, query_id="QUERY-D13")

    xml_day1 = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    xml_day2 = xml_day1 + b"\n<!-- regenerated -->\n"

    call_count = {"n": 0}

    async def fake_send_request(self, query_id):
        return f"REF-D13-{call_count['n']}"

    async def fake_poll_statement(self, reference_code, max_wait_seconds=300):
        call_count["n"] += 1
        return xml_day1 if call_count["n"] == 1 else xml_day2

    monkeypatch.setattr(client_mod.FlexClient, "send_request", fake_send_request)
    monkeypatch.setattr(client_mod.FlexClient, "poll_statement", fake_poll_statement)

    SessionLocal = async_sessionmaker(db_engine, expire_on_commit=False)

    res_1 = await flex_job_mod.run(SessionLocal, organization_id=sample_org.id, trigger="cron")
    fi_id_1 = res_1[conn_id]
    assert fi_id_1 is not None

    res_2 = await flex_job_mod.run(SessionLocal, organization_id=sample_org.id, trigger="cron")
    fi_id_2 = res_2[conn_id]
    assert fi_id_2 is not None
    assert fi_id_2 != fi_id_1, "Different XML bytes should create a new FlexImport"

    async with SessionLocal() as s2:
        n_ok = await s2.scalar(
            select(func.count(IngestLog.id)).where(
                IngestLog.job_kind == "flex",
                IngestLog.organization_id == sample_org.id,
                IngestLog.status == "ok",
                IngestLog.trigger == "cron",
            )
        )
        assert n_ok == 2

        n_failed = await s2.scalar(
            select(func.count(IngestLog.id)).where(
                IngestLog.job_kind == "flex",
                IngestLog.organization_id == sample_org.id,
                IngestLog.status == "failed",
            )
        )
        assert n_failed == 0

        n_fi = await s2.scalar(
            select(func.count(FlexImport.id)).where(FlexImport.organization_id == sample_org.id)
        )
        assert n_fi == 2

        n_trades = await s2.scalar(select(func.count(Trade.id)))
        n_distinct_tx = await s2.scalar(select(func.count(func.distinct(Trade.transaction_id))))
        assert n_trades == n_distinct_tx

        fi_2 = await s2.get(FlexImport, fi_id_2)
        assert fi_2.n_new_trades == 0


@pytest.mark.asyncio
async def test_run_returns_none_if_hash_already_known(
    monkeypatch, db_session: AsyncSession, db_engine, sample_org
):
    """If the fetched XML hash is already known, run() returns None for that
    connection and items_processed=0."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from ibkr_control.db.models.flex_raw import FlexImport
    from ibkr_control.db.models.ingest_log import IngestLog
    from ibkr_control.ingest.flex import client as client_mod
    from ibkr_control.ingest.flex import job as flex_job_mod

    from tests.ingest.flex.conftest import _seed_connection

    _set_token_key(monkeypatch, seed=b"Z")
    conn_id = await _seed_connection(db_session, sample_org.id, query_id="QUERY-456")

    xml_bytes = (FIXTURE_DIR / "empty_query_response.xml").read_bytes()

    async def fake_send_request(self, query_id):
        return "REF-1"

    async def fake_poll_statement(self, reference_code, max_wait_seconds=300):
        return xml_bytes

    monkeypatch.setattr(client_mod.FlexClient, "send_request", fake_send_request)
    monkeypatch.setattr(client_mod.FlexClient, "poll_statement", fake_poll_statement)

    SessionLocal = async_sessionmaker(db_engine, expire_on_commit=False)

    res_1 = await flex_job_mod.run(SessionLocal, organization_id=sample_org.id, trigger="cron")
    assert res_1[conn_id] is not None

    res_2 = await flex_job_mod.run(SessionLocal, organization_id=sample_org.id, trigger="cron")
    assert res_2[conn_id] is None

    async with SessionLocal() as s2:
        n_fi = await s2.scalar(
            select(func.count(FlexImport.id)).where(FlexImport.organization_id == sample_org.id)
        )
        assert n_fi == 1

        n_logs = await s2.scalar(
            select(func.count(IngestLog.id)).where(
                IngestLog.job_kind == "flex",
                IngestLog.organization_id == sample_org.id,
                IngestLog.status == "ok",
            )
        )
        assert n_logs == 2


# ---------------------------------------------------------------------------
# Per-connection fast-path logging (ok vs poison)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_logs_info_on_ok_hash_skip(
    monkeypatch, caplog, db_session, db_engine, sample_org
):
    """run() encuentra hash con status='ok' -> skip + info log."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from ibkr_control.db.models.flex_raw import FlexImport as FI
    from ibkr_control.ingest.flex import client as client_mod
    from ibkr_control.ingest.flex import job as flex_job_mod
    from ibkr_control.ingest.hash_dedup import xml_hash

    from tests.ingest.flex.conftest import _seed_connection

    caplog.set_level(logging.INFO, logger="ibkr_control.ingest.flex.job")
    _set_token_key(monkeypatch, seed=b"K")

    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    h = xml_hash(xml)

    conn_id = await _seed_connection(db_session, sample_org.id, query_id="123456")
    db_session.add(
        FI(
            organization_id=sample_org.id,
            connection_id=conn_id,
            xml_hash=h,
            xml_bytes=xml,
            xml_size_bytes=len(xml),
            anyo=2025,
            source="web_service",
            year_status="sealed",
            status="ok",
            period_covered_from=_date(2025, 1, 1),
            period_covered_to=_date(2025, 12, 31),
        )
    )
    await db_session.commit()

    monkeypatch.setattr(client_mod.FlexClient, "send_request", AsyncMock(return_value="ref-ok"))
    monkeypatch.setattr(client_mod.FlexClient, "poll_statement", AsyncMock(return_value=xml))

    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    result = await flex_job_mod.run(session_factory, organization_id=sample_org.id, trigger="cron")

    assert result[conn_id] is None
    assert "duplicate hash" in caplog.text and "skipped" in caplog.text


@pytest.mark.asyncio
async def test_run_logs_warning_on_poison_hash_skip(
    monkeypatch, caplog, db_session, db_engine, sample_org
):
    """run() encuentra hash con status='poison' -> skip + warning con recovery hint."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from ibkr_control.db.models.flex_raw import FlexImport as FI
    from ibkr_control.ingest.flex import client as client_mod
    from ibkr_control.ingest.flex import job as flex_job_mod
    from ibkr_control.ingest.hash_dedup import xml_hash

    from tests.ingest.flex.conftest import _seed_connection

    caplog.set_level(logging.WARNING, logger="ibkr_control.ingest.flex.job")
    _set_token_key(monkeypatch, seed=b"P")

    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    h = xml_hash(xml)

    conn_id = await _seed_connection(db_session, sample_org.id, query_id="123456")
    db_session.add(
        FI(
            organization_id=sample_org.id,
            connection_id=conn_id,
            xml_hash=h,
            xml_bytes=xml,
            xml_size_bytes=len(xml),
            anyo=2025,
            source="web_service",
            year_status="sealed",
            status="poison",
            poison_reason="forced parser crash",
            period_covered_from=_date(2025, 1, 1),
            period_covered_to=_date(2025, 12, 31),
        )
    )
    await db_session.commit()

    monkeypatch.setattr(client_mod.FlexClient, "send_request", AsyncMock(return_value="ref-poison"))
    monkeypatch.setattr(client_mod.FlexClient, "poll_statement", AsyncMock(return_value=xml))

    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    result = await flex_job_mod.run(session_factory, organization_id=sample_org.id, trigger="cron")

    assert result[conn_id] is None
    assert "previously poisoned" in caplog.text
    assert "poison_reset" in caplog.text
