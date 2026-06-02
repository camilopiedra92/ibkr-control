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
async def test_ingest_xml_happy_path(db_session: AsyncSession, sample_user):
    """ingest_xml recibe bytes, parsea, persiste, loggea."""
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    flex_import_id = await flex_job.ingest_xml(
        db_session,
        user_id=sample_user.id,
        xml_bytes=xml,
        source="manual_upload",
        trigger="wizard",
    )
    assert flex_import_id is not None

    fi = await db_session.scalar(select(FlexImport).where(FlexImport.id == flex_import_id))
    assert fi.status == "ok"

    # ingest_log debe tener 1 row ok del kind manual_upload
    n_logs = await db_session.scalar(
        select(func.count(IngestLog.id)).where(
            IngestLog.job_kind == "manual_upload",
            IngestLog.user_id == sample_user.id,
            IngestLog.status == "ok",
        )
    )
    assert n_logs >= 1


@pytest.mark.asyncio
async def test_ingest_xml_duplicate_returns_existing(db_session: AsyncSession, sample_user):
    """Re-upload del mismo XML devuelve el flex_import_id existente."""
    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    id_1 = await flex_job.ingest_xml(
        db_session,
        user_id=sample_user.id,
        xml_bytes=xml,
        source="manual_upload",
        trigger="wizard",
    )
    id_2 = await flex_job.ingest_xml(
        db_session,
        user_id=sample_user.id,
        xml_bytes=xml,
        source="manual_upload",
        trigger="wizard",
    )
    assert id_1 == id_2


@pytest.mark.asyncio
async def test_ingest_xml_logs_failure_on_parse_error(db_session: AsyncSession, db_engine, sample_user):
    """Si el parser lanza XMLSyntaxError, ingest_log queda en 'failed' con error_message."""
    bad_xml = (FIXTURE_DIR / "malformed_xml.xml").read_bytes()
    with pytest.raises(XMLSyntaxError):
        await flex_job.ingest_xml(
            db_session,
            user_id=sample_user.id,
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
                IngestLog.user_id == sample_user.id,
                IngestLog.status == "failed",
            )
            .order_by(IngestLog.id.desc())
            .limit(1)
        )
        assert row is not None, "Expected a failed ingest_log row"
        assert row.status == "failed"
        assert row.error_message is not None


@pytest.mark.asyncio
async def test_ingest_xml_rolls_back_persister_on_failure(db_session: AsyncSession, db_engine, sample_user):
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
        ParsedAccount, ParsedCashTransaction, ParsedXML,
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

    async def fake_ensure_accounts(session, ibkr_ids):
        # Devuelve un mapping con un account_id inválido (FK violation al INSERT cash_tx)
        return {account_id: 999_999_999}

    with patch(
        "ibkr_control.ingest.flex.job.flex_parser_mod.parse",
        return_value=_parsed_with_bad_account(),
    ), patch(
        "ibkr_control.ingest.flex.persister._ensure_accounts",
        side_effect=fake_ensure_accounts,
    ):
        from sqlalchemy.exc import IntegrityError
        with pytest.raises(IntegrityError):
            await flex_job.ingest_xml(
                db_session,
                user_id=sample_user.id,
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
        fi = await s2.scalar(
            select(FlexImport).where(FlexImport.xml_hash == bad_hash)
        )
        assert fi is not None, "Poison FlexImport row must exist after persist failure"
        assert fi.status == "poison", f"FlexImport should be status='poison', got {fi.status!r}"

        # SAVEPOINT rollback was effective: no partial cash_transactions from the bad insert
        n_cash = await s2.scalar(
            select(func.count(CashTransaction.id))
        )
        assert n_cash == 0, "CashTransaction writes should have been rolled back by SAVEPOINT"

        # The ingest_log 'failed' row must persist
        log_row = await s2.scalar(
            select(IngestLog)
            .where(
                IngestLog.user_id == sample_user.id,
                IngestLog.status == "failed",
            )
            .order_by(IngestLog.id.desc())
            .limit(1)
        )
        assert log_row is not None, "Expected a failed ingest_log row to persist"


# ---------------------------------------------------------------------------
# Tests for run() — the cron entry point
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_happy_path_with_mocked_flex_client(
    monkeypatch, db_session: AsyncSession, db_engine, sample_user
):
    """run() fetches from Flex WS (mocked), persists XML, marks log ok."""
    import base64
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from ibkr_control.db.models.flex_credentials import FlexCredentials
    from ibkr_control.db.models.flex_raw import FlexImport
    from ibkr_control.db.models.ingest_log import IngestLog
    from ibkr_control.ingest.flex import client as client_mod
    from ibkr_control.ingest.flex import crypto as crypto_mod

    # Set TOKEN_ENCRYPTION_KEY before calling encrypt_token
    test_key = base64.b64encode(b"Y" * 32).decode("ascii")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", test_key)

    # Write FlexCredentials for sample_user using the existing db_session
    encrypted = crypto_mod.encrypt_token("test-token-real")
    creds = FlexCredentials(
        user_id=sample_user.id,
        token_encrypted=encrypted,
        ytd_query_id="QUERY-123",
    )
    db_session.add(creds)
    await db_session.commit()

    # Prepare canned XML from fixture
    xml_bytes = (FIXTURE_DIR / "empty_query_response.xml").read_bytes()

    async def fake_send_request(self, query_id):
        assert query_id == "QUERY-123"
        return "REF-999"

    async def fake_poll_statement(self, reference_code, max_wait_seconds=300):
        assert reference_code == "REF-999"
        return xml_bytes

    monkeypatch.setattr(client_mod.FlexClient, "send_request", fake_send_request)
    monkeypatch.setattr(client_mod.FlexClient, "poll_statement", fake_poll_statement)

    # session_factory backed by same test DB (schema already up via db_session fixture)
    SessionLocal = async_sessionmaker(db_engine, expire_on_commit=False)

    from ibkr_control.ingest.flex import job as flex_job_mod
    flex_import_id = await flex_job_mod.run(
        SessionLocal, user_id=sample_user.id, trigger="cron"
    )
    assert flex_import_id is not None

    # Verify FlexImport row was created correctly
    async with SessionLocal() as s2:
        fi = await s2.get(FlexImport, flex_import_id)
        assert fi is not None
        assert fi.user_id == sample_user.id
        assert fi.source == "web_service"
        assert fi.status == "ok"

        # Verify ingest_log row was created with correct metadata
        n_logs = await s2.scalar(
            select(func.count(IngestLog.id)).where(
                IngestLog.job_kind == "flex",
                IngestLog.user_id == sample_user.id,
                IngestLog.status == "ok",
                IngestLog.trigger == "cron",
            )
        )
        assert n_logs >= 1


@pytest.mark.asyncio
async def test_run_idempotent_across_different_xmls_with_overlapping_trades(
    monkeypatch, db_session: AsyncSession, db_engine, sample_user
):
    """Regression test para D13 [BUG-FIXED] (2026-05-25).

    Pre Phase 2.5: el cron Flex fallaba al segundo run con
    UniqueViolationError porque el persister dedupea solo a nivel xml_hash y
    los XMLs YTD cambian byte-a-byte cada dia. Cada hash nuevo intentaba
    re-INSERT de todos los trades del año -> choque con UNIQUE(transaction_id).

    Post Phase 2.5: el persister es idempotent fila por fila via UPSERT por
    natural key. Dos runs con XMLs distintos (hashes distintos) pero trades
    overlapping deben ambos terminar OK, sin duplicados en DB.
    """
    import base64
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from ibkr_control.db.models.flex_credentials import FlexCredentials
    from ibkr_control.db.models.flex_raw import FlexImport, Trade
    from ibkr_control.db.models.ingest_log import IngestLog
    from ibkr_control.ingest.flex import client as client_mod
    from ibkr_control.ingest.flex import crypto as crypto_mod
    from ibkr_control.ingest.flex import job as flex_job_mod

    test_key = base64.b64encode(b"D" * 32).decode("ascii")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", test_key)

    encrypted = crypto_mod.encrypt_token("test-token-d13")
    db_session.add(FlexCredentials(
        user_id=sample_user.id,
        token_encrypted=encrypted,
        ytd_query_id="QUERY-D13",
    ))
    await db_session.commit()

    # Use the real 2025 sanitized fixture (has trades + accruals + transfers
    # — exercises the full persister surface that originally crashed).
    xml_day1 = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    # Day 2: simulate IBKR re-emitting the same year with timestamp drift.
    # Trailing whitespace changes the bytes (different hash) without
    # affecting parsed content — exactly what happens day-over-day in YTD.
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

    # First run: fresh insert, must succeed
    fi_id_1 = await flex_job_mod.run(SessionLocal, user_id=sample_user.id, trigger="cron")
    assert fi_id_1 is not None

    # Second run with a DIFFERENT XML (different hash) but overlapping trades.
    # Pre-D13-fix this would crash with UniqueViolationError on trades_transaction_id_key.
    # Post-fix it must succeed and return a new flex_import_id (new XML = new row),
    # but children get DO NOTHING / DO UPDATE per entity type.
    fi_id_2 = await flex_job_mod.run(SessionLocal, user_id=sample_user.id, trigger="cron")
    assert fi_id_2 is not None
    assert fi_id_2 != fi_id_1, "Different XML bytes should create a new FlexImport"

    async with SessionLocal() as s2:
        # Both runs logged as ok in ingest_log
        n_ok = await s2.scalar(
            select(func.count(IngestLog.id)).where(
                IngestLog.job_kind == "flex",
                IngestLog.user_id == sample_user.id,
                IngestLog.status == "ok",
                IngestLog.trigger == "cron",
            )
        )
        assert n_ok == 2, "Both cron runs should be marked ok in ingest_log"

        # ZERO failed logs (the original bug surfaced as failed rows)
        n_failed = await s2.scalar(
            select(func.count(IngestLog.id)).where(
                IngestLog.job_kind == "flex",
                IngestLog.user_id == sample_user.id,
                IngestLog.status == "failed",
            )
        )
        assert n_failed == 0, "No failed runs expected post-D13-fix"

        # 2 flex_imports rows (one per distinct hash)
        n_fi = await s2.scalar(
            select(func.count(FlexImport.id)).where(
                FlexImport.user_id == sample_user.id
            )
        )
        assert n_fi == 2

        # Trades: ALL trades from the fixture, NOT duplicated across the 2 runs.
        # Count by distinct transaction_id should equal total count.
        n_trades = await s2.scalar(select(func.count(Trade.id)))
        n_distinct_tx = await s2.scalar(
            select(func.count(func.distinct(Trade.transaction_id)))
        )
        assert n_trades == n_distinct_tx, (
            f"trades duplicated across runs: {n_trades} rows but {n_distinct_tx} "
            f"distinct transaction_ids — exactly the D13 bug if these differ"
        )

        # The second flex_import should report n_new_trades == 0 (all trades
        # were already in DB from the first run).
        fi_2 = await s2.get(FlexImport, fi_id_2)
        assert fi_2.n_new_trades == 0, (
            f"Second run should have inserted 0 new trades; got {fi_2.n_new_trades}"
        )


@pytest.mark.asyncio
async def test_run_returns_none_if_hash_already_known(
    monkeypatch, db_session: AsyncSession, db_engine, sample_user
):
    """If is_known_hash(fetched_xml) == True, run() returns None and items_processed=0."""
    import base64
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from ibkr_control.db.models.flex_credentials import FlexCredentials
    from ibkr_control.db.models.flex_raw import FlexImport
    from ibkr_control.db.models.ingest_log import IngestLog
    from ibkr_control.ingest.flex import client as client_mod
    from ibkr_control.ingest.flex import crypto as crypto_mod
    from ibkr_control.ingest.flex import job as flex_job_mod

    test_key = base64.b64encode(b"Z" * 32).decode("ascii")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", test_key)

    encrypted = crypto_mod.encrypt_token("test-token-2")
    db_session.add(FlexCredentials(
        user_id=sample_user.id,
        token_encrypted=encrypted,
        ytd_query_id="QUERY-456",
    ))
    await db_session.commit()

    xml_bytes = (FIXTURE_DIR / "empty_query_response.xml").read_bytes()

    async def fake_send_request(self, query_id):
        return "REF-1"

    async def fake_poll_statement(self, reference_code, max_wait_seconds=300):
        return xml_bytes

    monkeypatch.setattr(client_mod.FlexClient, "send_request", fake_send_request)
    monkeypatch.setattr(client_mod.FlexClient, "poll_statement", fake_poll_statement)

    SessionLocal = async_sessionmaker(db_engine, expire_on_commit=False)

    # First run: persists XML, returns a valid ID
    fi_id_1 = await flex_job_mod.run(SessionLocal, user_id=sample_user.id, trigger="cron")
    assert fi_id_1 is not None

    # Second run: same XML hash → early exit, returns None
    fi_id_2 = await flex_job_mod.run(SessionLocal, user_id=sample_user.id, trigger="cron")
    assert fi_id_2 is None

    async with SessionLocal() as s2:
        # Only 1 FlexImport row should exist (the first one, not duplicated)
        n_fi = await s2.scalar(
            select(func.count(FlexImport.id)).where(
                FlexImport.user_id == sample_user.id
            )
        )
        assert n_fi == 1

        # Two ingest_log entries (one per run, both ok)
        n_logs = await s2.scalar(
            select(func.count(IngestLog.id)).where(
                IngestLog.job_kind == "flex",
                IngestLog.user_id == sample_user.id,
                IngestLog.status == "ok",
            )
        )
        assert n_logs == 2


# ---------------------------------------------------------------------------
# Task 6: per-user fast-path with distinct logging (ok vs poison)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_logs_info_on_ok_hash_skip(
    monkeypatch, caplog, db_session, db_engine, sample_user,
):
    """run() encuentra hash con status='ok' -> skip + info log."""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from ibkr_control.db.models.flex_credentials import FlexCredentials
    from ibkr_control.db.models.flex_raw import FlexImport as FI
    from ibkr_control.ingest.flex import crypto as flex_crypto_mod
    from ibkr_control.ingest.hash_dedup import xml_hash

    caplog.set_level(logging.INFO, logger="ibkr_control.ingest.flex.job")

    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    h = xml_hash(xml)

    # Seed credentials + existing flex_imports row with status='ok'
    import base64
    test_key = base64.b64encode(b"K" * 32).decode("ascii")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", test_key)

    db_session.add(FlexCredentials(
        user_id=sample_user.id,
        token_encrypted=flex_crypto_mod.encrypt_token("dummy-token"),
        ytd_query_id="123456",
    ))
    db_session.add(FI(
        user_id=sample_user.id, xml_hash=h, xml_bytes=xml,
        xml_size_bytes=len(xml), anyo=2025, source="web_service",
        year_status="sealed", status="ok",
        period_covered_from=_date(2025, 1, 1),
        period_covered_to=_date(2025, 12, 31),
    ))
    await db_session.commit()

    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)

    from ibkr_control.ingest.flex import client as client_mod
    monkeypatch.setattr(
        client_mod.FlexClient, "send_request", AsyncMock(return_value="ref-ok")
    )
    monkeypatch.setattr(
        client_mod.FlexClient, "poll_statement", AsyncMock(return_value=xml)
    )

    from ibkr_control.ingest.flex import job as flex_job_mod
    result = await flex_job_mod.run(session_factory, user_id=sample_user.id, trigger="cron")

    assert result is None
    assert "duplicate hash" in caplog.text and "skipped" in caplog.text


@pytest.mark.asyncio
async def test_run_logs_warning_on_poison_hash_skip(
    monkeypatch, caplog, db_session, db_engine, sample_user,
):
    """run() encuentra hash con status='poison' -> skip + warning log con recovery hint."""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from ibkr_control.db.models.flex_credentials import FlexCredentials
    from ibkr_control.db.models.flex_raw import FlexImport as FI
    from ibkr_control.ingest.flex import client as client_mod
    from ibkr_control.ingest.flex import crypto as flex_crypto_mod
    from ibkr_control.ingest.hash_dedup import xml_hash

    caplog.set_level(logging.WARNING, logger="ibkr_control.ingest.flex.job")

    xml = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    h = xml_hash(xml)

    import base64
    test_key = base64.b64encode(b"P" * 32).decode("ascii")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", test_key)

    db_session.add(FlexCredentials(
        user_id=sample_user.id,
        token_encrypted=flex_crypto_mod.encrypt_token("dummy-token"),
        ytd_query_id="123456",
    ))
    db_session.add(FI(
        user_id=sample_user.id, xml_hash=h, xml_bytes=xml,
        xml_size_bytes=len(xml), anyo=2025, source="web_service",
        year_status="sealed", status="poison",
        poison_reason="forced parser crash",
        period_covered_from=_date(2025, 1, 1),
        period_covered_to=_date(2025, 12, 31),
    ))
    await db_session.commit()

    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)

    monkeypatch.setattr(
        client_mod.FlexClient, "send_request", AsyncMock(return_value="ref-poison")
    )
    monkeypatch.setattr(
        client_mod.FlexClient, "poll_statement", AsyncMock(return_value=xml)
    )

    from ibkr_control.ingest.flex import job as flex_job_mod
    result = await flex_job_mod.run(session_factory, user_id=sample_user.id, trigger="cron")

    assert result is None
    assert "previously poisoned" in caplog.text
    assert "poison_reset" in caplog.text
