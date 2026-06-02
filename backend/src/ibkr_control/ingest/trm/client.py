"""Cliente HTTP del Socrata DIAN dataset ceyp-9c7c (TRM).

Endpoint: https://www.datos.gov.co/resource/ceyp-9c7c.json
Filtro: ?$where=vigenciadesde > 'YYYY-MM-DDT00:00:00.000'
        &$order=vigenciadesde ASC
        &$limit=50000

Si since=None → backfill total (sin filtro $where, devuelve todo desde 1991).
"""

from datetime import date
from typing import Final

import httpx


SOCRATA_URL: Final = "https://www.datos.gov.co/resource/ceyp-9c7c.json"
PAGE_LIMIT: Final = 50000


class TrmClient:
    """Cliente async para el dataset TRM de Socrata DIAN.

    Args:
        base_url: URL base del endpoint Socrata. Default: produccion DIAN.
        timeout: Timeout por request HTTP en segundos.
    """

    def __init__(
        self,
        base_url: str = SOCRATA_URL,
        timeout: float = 60.0,
    ) -> None:
        self._url = base_url
        self._timeout = timeout

    async def fetch(self, *, since: date | None) -> list[dict]:
        """Devuelve rows desde Socrata con vigenciadesde > since.

        Args:
            since: Fecha desde la cual filtrar (exclusive). Si None, descarga
                   el historico completo (backfill desde 1991).

        Returns:
            Lista de dicts con las columnas del dataset Socrata.

        Raises:
            httpx.HTTPStatusError: Error HTTP no-2xx.
        """
        params: dict[str, str] = {
            "$order": "vigenciadesde ASC",
            "$limit": str(PAGE_LIMIT),
        }
        if since is not None:
            params["$where"] = f"vigenciadesde > '{since.isoformat()}T00:00:00.000'"

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.get(self._url, params=params)
            resp.raise_for_status()
            return resp.json()
