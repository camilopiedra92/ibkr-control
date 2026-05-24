"""Tests del orchestrator flex_job (lock + log + parser + persister)."""
from pathlib import Path
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
    """Si persist() falla (dup transaction_id dentro del XML), el persister
    no deja flex_imports rows ni trades, pero el ingest_log 'failed' persiste.
    """
    from ibkr_control.ingest.flex._models import (
        ParsedAccount, ParsedTrade, ParsedXML,
    )
    from datetime import date
    from decimal import Decimal
    from unittest.mock import patch

    # Construir un ParsedXML que causara IntegrityError en el persister
    # (trade duplicado con mismo transaction_id)
    account_id = "U99999042"

    def _bad_parsed() -> "ParsedXML":
        trade = ParsedTrade(
            transaction_id="DUP-TXN-99",
            ibkr_account_id=account_id,
            symbol="AAPL",
            asset_class="STK",
            trade_date=date(2025, 3, 1),
            settle_date=date(2025, 3, 3),
            qty=Decimal("5"),
            price_usd=Decimal("100"),
            proceeds_usd=Decimal("-500"),
            commission_usd=Decimal("1"),
            open_close="O",
            buy_sell="BUY",
            raw_attrs={},
        )
        return ParsedXML(
            anyo=2025,
            period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31),
            accounts=[ParsedAccount(ibkr_account_id=account_id, currency="USD")],
            trades=[trade, trade],  # same object twice → duplicate transaction_id on flush
            closed_lots=[],
            open_position_lots=[],
            cash_transactions=[],
            transfers=[],
            change_in_dividend_accruals=[],
            open_dividend_accruals=[],
        )

    # Patch parse to return our bad ParsedXML
    with patch(
        "ibkr_control.ingest.flex.job.flex_parser_mod.parse",
        return_value=_bad_parsed(),
    ):
        from sqlalchemy.exc import IntegrityError
        with pytest.raises(IntegrityError):
            await flex_job.ingest_xml(
                db_session,
                user_id=sample_user.id,
                xml_bytes=b"<xml>bad</xml>",
                source="manual_upload",
                trigger="wizard",
            )

    # Verify via a fresh session (db_session may be in error state after exception)
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession as AS2
    maker2 = async_sessionmaker(db_engine, expire_on_commit=False, class_=AS2)
    async with maker2() as s2:
        # No flex_imports should exist for this bad xml_hash
        from ibkr_control.ingest.hash_dedup import xml_hash
        bad_hash = xml_hash(b"<xml>bad</xml>")
        fi = await s2.scalar(
            select(FlexImport).where(FlexImport.xml_hash == bad_hash)
        )
        assert fi is None, "Persister data should have been rolled back"

        # But the ingest_log 'failed' row should persist
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
