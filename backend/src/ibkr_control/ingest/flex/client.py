"""Cliente HTTP del IBKR Flex Web Service.

Flujo:
  1. send_request(token, query_id) → reference_code
  2. poll_statement(token, reference_code) con backoff exponencial hasta que
     IBKR responda con el XML completo, o lanza FlexPollTimeoutError a los
     max_wait_seconds (default 300 s = 5 min).

URLs production (Flex Web Service V3 — host ndcdyn + AccountManagement path):
  https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest
  https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement
All requests carry a User-Agent header (V3 spec requires it; absent UA can
trigger throttling).

Notas sobre _is_pending:
  - Si el body NO empieza con <?xml, se asume que es directamente el XML del
    FlexQueryResponse (IBKR a veces lo devuelve sin prolog). → ready.
  - Si la raiz es <FlexQueryResponse> → ready.
  - Si la raiz es <FlexStatementResponse> con ErrorCode 1019 → pending.
  - Si <FlexStatementResponse> con otro ErrorCode (ej. Fail/1018 durante poll,
    raro pero posible), _is_pending devuelve False y el body se retorna al
    llamador como si fuera el XML final; el persister lo rechazara en parse.
  - Cualquier otra estructura → ready (conservador: deja que el parser falle
    con un mensaje informativo en vez de ciclar para siempre).
"""
import asyncio
from typing import Final

import httpx
from lxml import etree


SEND_REQUEST_PATH: Final = "/AccountManagement/FlexWebService/SendRequest"
GET_STATEMENT_PATH: Final = "/AccountManagement/FlexWebService/GetStatement"
DEFAULT_BASE_URL: Final = "https://ndcdyn.interactivebrokers.com"

# V3 spec mandates a User-Agent header on every request. httpx's default
# (`python-httpx/x.y.z`) is treated as a bot by IBKR and rate-limited.
_USER_AGENT: Final = "ibkr-control/0.2 (+https://github.com/owner)"

# Codigos de error documentados por IBKR Flex WS
ERR_INVALID_TOKEN: Final = "1018"
ERR_STATEMENT_PENDING: Final = "1019"
# IBKR Flex Web Service error codes seen in production:
#   1001 → "Statement could not be generated at this time" (transient throttling)
#   1003, 1004 → bad token / token expired (auth-class)
#   1005 → "Invalid request or required parameter is missing" (typically wrong query_id)
ERR_BUSY: Final = "1001"
ERR_AUTH_CODES: Final = {"1003", "1004", ERR_INVALID_TOKEN}
ERR_QUERY_NOT_FOUND: Final = "1005"

# Backoff exponencial: 1 → 2 → 4 → 8 → 16 (capped)
_BACKOFF_INITIAL: Final = 1
_BACKOFF_MAX: Final = 16


class FlexAuthError(RuntimeError):
    """Token invalido o sin permisos para el query_id (ErrorCode 1018)."""

    def __init__(self, error_code: str, error_message: str) -> None:
        self.error_code = error_code
        self.error_message = error_message
        super().__init__(f"Flex auth error {error_code}: {error_message}")


class FlexPollTimeoutError(RuntimeError):
    """IBKR no entrego el XML dentro del timeout configurado."""

    def __init__(self, reference_code: str, waited: int) -> None:
        self.reference_code = reference_code
        self.waited = waited
        super().__init__(
            f"Flex poll timeout: reference={reference_code} after {waited}s"
        )


class FlexClientError(RuntimeError):
    """Error generico del API Flex (no auth, no timeout).

    Tiene .code para que el caller pueda inspeccionar el ErrorCode IBKR
    sin parsear el mensaje.
    """

    def __init__(self, message: str, code: str | None = None) -> None:
        self.code = code
        super().__init__(message)


# Alias para que el caller pueda hacer `except flex_client_mod.FlexError`
# matcheando cualquier error tipado del modulo (Auth, Busy, QueryNotFound,
# generico). FlexError es la base abstracta logica.
FlexError = FlexClientError


class FlexBusyError(FlexClientError):
    """IBKR ErrorCode 1001: Statement could not be generated at this time.

    Throttling transient — el caller deberia hacer retry con backoff.
    """

    def __init__(self, error_message: str = "Statement could not be generated at this time") -> None:
        super().__init__(f"Flex busy (1001): {error_message}", code=ERR_BUSY)
        self.error_message = error_message


class FlexQueryNotFoundError(FlexClientError):
    """IBKR ErrorCode 1005: Invalid request — query_id desconocido o sin permisos."""

    def __init__(self, error_message: str = "Query not found") -> None:
        super().__init__(f"Flex query not found (1005): {error_message}", code=ERR_QUERY_NOT_FOUND)
        self.error_message = error_message


class FlexClient:
    """Cliente async para el IBKR Flex Web Service.

    Args:
        token: Token de acceso Flex (en texto claro; el llamador es responsable
               de desencriptarlo antes de construir el cliente).
        base_url: Base URL del servicio. Default: IBKR production.
        timeout: Timeout por request HTTP en segundos.
    """

    def __init__(
        self,
        token: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def send_request(self, query_id: str) -> str:
        """Inicia la generacion de un statement en IBKR.

        Args:
            query_id: ID del Flex Query configurado en Account Management.

        Returns:
            reference_code: string numerico para usar en poll_statement.

        Raises:
            FlexAuthError: Token invalido (ErrorCode 1018).
            FlexClientError: Otro error del API.
            httpx.HTTPStatusError: Error HTTP no-2xx.
        """
        url = f"{self._base_url}{SEND_REQUEST_PATH}"
        params = {"v": "3", "t": self._token, "q": query_id}

        async with httpx.AsyncClient(
            timeout=self._timeout, headers={"User-Agent": _USER_AGENT}
        ) as http:
            resp = await http.get(url, params=params)
            resp.raise_for_status()

        return self._parse_send_response(resp.content)

    async def poll_statement(
        self,
        reference_code: str,
        max_wait_seconds: int = 300,
    ) -> bytes:
        """Consulta el statement con backoff exponencial hasta que este listo.

        Args:
            reference_code: Codigo devuelto por send_request.
            max_wait_seconds: Tiempo maximo de espera acumulado en segundos.

        Returns:
            Bytes del XML del FlexQueryResponse.

        Raises:
            FlexPollTimeoutError: Timeout superado antes de recibir el XML.
            httpx.HTTPStatusError: Error HTTP no-2xx.
        """
        url = f"{self._base_url}{GET_STATEMENT_PATH}"
        params = {"v": "3", "t": self._token, "q": reference_code}

        backoff = _BACKOFF_INITIAL
        elapsed = 0

        async with httpx.AsyncClient(
            timeout=self._timeout, headers={"User-Agent": _USER_AGENT}
        ) as http:
            while elapsed < max_wait_seconds:
                resp = await http.get(url, params=params)
                resp.raise_for_status()
                content = resp.content

                if self._is_pending(content):
                    await asyncio.sleep(backoff)
                    elapsed += backoff
                    backoff = min(backoff * 2, _BACKOFF_MAX)
                    continue

                return content

        raise FlexPollTimeoutError(reference_code, elapsed)

    async def get_statement(
        self,
        reference_code: str,
        max_wait_seconds: int = 300,
    ) -> bytes:
        """Alias semantico de poll_statement.

        IBKR documenta el endpoint como GetStatement; el cliente hace polling
        internamente cuando el statement no esta listo (ErrorCode 1019).
        """
        return await self.poll_statement(reference_code, max_wait_seconds=max_wait_seconds)

    @staticmethod
    def _parse_send_response(body: bytes) -> str:
        """Extrae ReferenceCode del XML de respuesta de SendRequest.

        Raises:
            FlexAuthError: ErrorCode 1018.
            FlexClientError: Cualquier otro Fail o ReferenceCode ausente.
        """
        try:
            tree = etree.fromstring(body)
        except etree.XMLSyntaxError as exc:
            raise FlexClientError(f"SendRequest: respuesta no es XML valido: {exc}") from exc

        status = tree.findtext("Status")
        if status == "Success":
            ref = tree.findtext("ReferenceCode")
            if not ref:
                raise FlexClientError("SendRequest Success pero sin ReferenceCode en la respuesta")
            return ref

        error_code = tree.findtext("ErrorCode") or ""
        error_message = tree.findtext("ErrorMessage") or "unknown"
        if error_code in ERR_AUTH_CODES:
            raise FlexAuthError(error_code, error_message)
        if error_code == ERR_BUSY:
            raise FlexBusyError(error_message)
        if error_code == ERR_QUERY_NOT_FOUND:
            raise FlexQueryNotFoundError(error_message)
        raise FlexClientError(
            f"SendRequest fallo {error_code}: {error_message}", code=error_code or None
        )

    @staticmethod
    def _is_pending(body: bytes) -> bool:
        """Determina si la respuesta es 'statement en generacion' (ErrorCode 1019).

        Returns True solo si IBKR explicitamente indica que el statement no
        esta listo aun (ErrorCode 1019). En cualquier otro caso devuelve False
        (ready) para no ciclar indefinidamente.

        IBKR puede omitir el prolog <?xml ... ?> en la respuesta de poll, por lo
        que no se usa el prolog como señal de pendiente — siempre se intenta
        parsear el XML.
        """
        try:
            tree = etree.fromstring(body)
        except etree.XMLSyntaxError:
            # XML malformado → dejar que el persister/parser lo rechace
            return False

        if tree.tag == "FlexQueryResponse":
            return False

        if tree.tag == "FlexStatementResponse":
            error_code = tree.findtext("ErrorCode") or ""
            return error_code == ERR_STATEMENT_PENDING

        # Cualquier otra estructura → tratar como ready
        return False
