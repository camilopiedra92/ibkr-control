"""Tests del cliente Flex Web Service.

Usa VCR cassettes handwritten (minimas) para happy-path, y respx para
escenarios de error que no necesitan cassette.

Para regrabar con cassettes reales cuando IBKR cambie el API:
    RECORD_MODE=new_episodes uv run pytest tests/ingest/flex/test_client.py -v
    uv run python -m scripts.sanitize_cassette tests/fixtures/cassettes/flex/*.yaml
"""
import pytest
import respx
from httpx import Response

from ibkr_control.ingest.flex.client import (
    FlexAuthError,
    FlexClient,
    FlexPollTimeoutError,
    FlexStatementPendingError,
    POLL_STATEMENT_POLICY,
)


@pytest.mark.vcr(cassette_library_dir="tests/fixtures/cassettes/flex")
@pytest.mark.asyncio
async def test_send_request_returns_reference_code():
    """Happy path via cassette: SendRequest devuelve reference_code numerico."""
    client = FlexClient(token="fake-token", base_url="https://ndcdyn.interactivebrokers.com")
    ref = await client.send_request(query_id="1234567")
    assert ref
    assert ref.isdigit()


@pytest.mark.vcr(cassette_library_dir="tests/fixtures/cassettes/flex")
@pytest.mark.asyncio
async def test_poll_statement_returns_xml_bytes():
    """Cassette donde el reference ya esta READY — devuelve bytes XML."""
    client = FlexClient(token="fake-token", base_url="https://ndcdyn.interactivebrokers.com")
    xml = await client.poll_statement(reference_code="9999999")
    assert xml.startswith(b"<?xml") or xml.startswith(b"<FlexQueryResponse")


@pytest.mark.asyncio
async def test_send_request_with_invalid_token_raises_auth_error(respx_mock):
    """Token invalido (ErrorCode 1018) → FlexAuthError."""
    respx_mock.get(
        "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest"
    ).mock(
        return_value=Response(
            200,
            content=b"""<?xml version="1.0"?>
<FlexStatementResponse timestamp="2026-05-24 10:00:00.000">
  <Status>Fail</Status>
  <ErrorCode>1018</ErrorCode>
  <ErrorMessage>Invalid token</ErrorMessage>
</FlexStatementResponse>""",
        )
    )
    client = FlexClient(token="bad", base_url="https://ndcdyn.interactivebrokers.com")
    with pytest.raises(FlexAuthError):
        await client.send_request(query_id="1234567")


@pytest.mark.asyncio
async def test_poll_statement_times_out_after_5min():
    """Si IBKR sigue respondiendo 'pending' (ErrorCode 1019) se lanza FlexPollTimeoutError."""
    from unittest.mock import patch

    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    pending_body = (
        b"<?xml version='1.0'?>"
        b"<FlexStatementResponse>"
        b"<Status>Warn</Status>"
        b"<ErrorCode>1019</ErrorCode>"
        b"<ErrorMessage>Statement generation in progress</ErrorMessage>"
        b"</FlexStatementResponse>"
    )

    with patch("asyncio.sleep", side_effect=fake_sleep):
        with respx.mock(base_url="https://ndcdyn.interactivebrokers.com") as mock_router:
            mock_router.get("/AccountManagement/FlexWebService/GetStatement").mock(
                return_value=Response(200, content=pending_body)
            )
            client = FlexClient(token="x", base_url="https://ndcdyn.interactivebrokers.com")
            with pytest.raises(FlexPollTimeoutError):
                await client.poll_statement(reference_code="9999", max_wait_seconds=300)

    # Verificar que hubo multiples retries con backoff (1, 2, 4, 8, 16, ...)
    assert len(sleep_calls) >= 5


@pytest.mark.asyncio
async def test_poll_statement_succeeds_after_few_polls():
    """Primer poll: pending. Segundo poll: XML listo. Debe devolver los bytes."""
    from unittest.mock import patch

    pending_body = (
        b"<FlexStatementResponse>"
        b"<Status>Warn</Status>"
        b"<ErrorCode>1019</ErrorCode>"
        b"<ErrorMessage>pending</ErrorMessage>"
        b"</FlexStatementResponse>"
    )
    ready_body = (
        b"<?xml version='1.0'?>"
        b"<FlexQueryResponse>"
        b"<FlexStatements count='1'/>"
        b"</FlexQueryResponse>"
    )

    with patch("asyncio.sleep"):
        with respx.mock(base_url="https://ndcdyn.interactivebrokers.com") as mock_router:
            route = mock_router.get("/AccountManagement/FlexWebService/GetStatement").mock(
                side_effect=[
                    Response(200, content=pending_body),
                    Response(200, content=ready_body),
                ]
            )
            client = FlexClient(token="x", base_url="https://ndcdyn.interactivebrokers.com")
            xml = await client.poll_statement(reference_code="9999")
            assert b"FlexQueryResponse" in xml
            assert route.call_count == 2


@pytest.mark.asyncio
async def test_poll_statement_uses_retry_policy_on_pending(monkeypatch):
    """poll_statement debe reintentar via execute_with_retry mientras pending."""
    client = FlexClient(token="t")
    call_count = 0

    async def fake_poll_once(self, ref):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise FlexStatementPendingError(ref)
        return b'<FlexQueryResponse>ok</FlexQueryResponse>'

    monkeypatch.setattr(FlexClient, "_poll_once", fake_poll_once)

    from unittest.mock import AsyncMock, patch

    with patch("asyncio.sleep", new=AsyncMock()):
        result = await client.poll_statement("ref123")

    assert result == b'<FlexQueryResponse>ok</FlexQueryResponse>'
    assert call_count == 3


@pytest.mark.asyncio
async def test_poll_statement_raises_timeout_when_max_attempts_exceeded(monkeypatch):
    """Si todos los attempts dan FlexStatementPendingError, raise FlexPollTimeoutError."""
    from ibkr_control.ingest.flex import client as client_module
    from unittest.mock import patch

    client = FlexClient(token="t")

    async def always_pending(self, ref):
        raise FlexStatementPendingError(ref)

    monkeypatch.setattr(FlexClient, "_poll_once", always_pending)
    # Use a tight policy for the test so it doesn't loop 30x
    tight_policy = POLL_STATEMENT_POLICY.__class__(
        initial_delay_s=0.001, max_delay_s=0.001, multiplier=2,
        max_attempts=3,
        retryable_exceptions=(FlexStatementPendingError,),
    )
    monkeypatch.setattr(client_module, "POLL_STATEMENT_POLICY", tight_policy)

    with patch("asyncio.sleep"):
        with pytest.raises(FlexPollTimeoutError):
            await client.poll_statement("ref123")
