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
)


@pytest.mark.vcr(cassette_library_dir="tests/fixtures/cassettes/flex")
@pytest.mark.asyncio
async def test_send_request_returns_reference_code():
    """Happy path via cassette: SendRequest devuelve reference_code numerico."""
    client = FlexClient(token="fake-token", base_url="https://gdcdyn.interactivebrokers.com")
    ref = await client.send_request(query_id="1234567")
    assert ref
    assert ref.isdigit()


@pytest.mark.vcr(cassette_library_dir="tests/fixtures/cassettes/flex")
@pytest.mark.asyncio
async def test_poll_statement_returns_xml_bytes():
    """Cassette donde el reference ya esta READY — devuelve bytes XML."""
    client = FlexClient(token="fake-token", base_url="https://gdcdyn.interactivebrokers.com")
    xml = await client.poll_statement(reference_code="9999999")
    assert xml.startswith(b"<?xml") or xml.startswith(b"<FlexQueryResponse")


@pytest.mark.asyncio
async def test_send_request_with_invalid_token_raises_auth_error(respx_mock):
    """Token invalido (ErrorCode 1018) → FlexAuthError."""
    respx_mock.get(
        "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.SendRequest"
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
    client = FlexClient(token="bad", base_url="https://gdcdyn.interactivebrokers.com")
    with pytest.raises(FlexAuthError):
        await client.send_request(query_id="1234567")


@pytest.mark.asyncio
async def test_poll_statement_times_out_after_5min(monkeypatch):
    """Si IBKR sigue respondiendo 'pending' (ErrorCode 1019) se lanza FlexPollTimeoutError."""
    from ibkr_control.ingest.flex import client as client_module

    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(client_module.asyncio, "sleep", fake_sleep)

    pending_body = (
        b"<?xml version='1.0'?>"
        b"<FlexStatementResponse>"
        b"<Status>Warn</Status>"
        b"<ErrorCode>1019</ErrorCode>"
        b"<ErrorMessage>Statement generation in progress</ErrorMessage>"
        b"</FlexStatementResponse>"
    )

    with respx.mock(base_url="https://gdcdyn.interactivebrokers.com") as mock_router:
        mock_router.get("/Universal/servlet/FlexStatementService.GetStatement").mock(
            return_value=Response(200, content=pending_body)
        )
        client = FlexClient(token="x", base_url="https://gdcdyn.interactivebrokers.com")
        with pytest.raises(FlexPollTimeoutError):
            await client.poll_statement(reference_code="9999", max_wait_seconds=300)

    # Verificar que hubo multiples retries con backoff (1, 2, 4, 8, 16, ...)
    assert len(sleep_calls) >= 5


@pytest.mark.asyncio
async def test_poll_statement_succeeds_after_few_polls(monkeypatch):
    """Primer poll: pending. Segundo poll: XML listo. Debe devolver los bytes."""
    from ibkr_control.ingest.flex import client as client_module

    async def fake_sleep(seconds: float) -> None:
        pass

    monkeypatch.setattr(client_module.asyncio, "sleep", fake_sleep)

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

    with respx.mock(base_url="https://gdcdyn.interactivebrokers.com") as mock_router:
        route = mock_router.get("/Universal/servlet/FlexStatementService.GetStatement").mock(
            side_effect=[
                Response(200, content=pending_body),
                Response(200, content=ready_body),
            ]
        )
        client = FlexClient(token="x", base_url="https://gdcdyn.interactivebrokers.com")
        xml = await client.poll_statement(reference_code="9999")
        assert b"FlexQueryResponse" in xml
        assert route.call_count == 2
