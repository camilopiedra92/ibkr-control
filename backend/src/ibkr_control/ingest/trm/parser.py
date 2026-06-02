"""Expansion de rows Socrata (vigencia_desde..vigencia_hasta) a 1 row por dia."""

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Iterable, Iterator

logger = logging.getLogger(__name__)


def _parse_socrata_date(s: str) -> date:
    """Socrata devuelve '2026-01-15T00:00:00.000' o '2026-01-15'."""
    if "T" in s:
        return datetime.fromisoformat(s.split("T")[0]).date()
    return date.fromisoformat(s)


def expand_vigencias(rows: Iterable[dict]) -> Iterator[dict]:
    """Recibe rows del API Socrata, yield 1 dict por dia calendario.

    Cada dict contiene: date, value_cop, vigencia_desde, vigencia_hasta.

    El TRM del viernes es vigente durante sabado y domingo — la expansion
    cubre todos los dias del rango vigenciadesde..vigenciahasta inclusive.
    """
    for row in rows:
        try:
            vd_raw = row["vigenciadesde"]
            vh_raw = row["vigenciahasta"]
            valor_raw = row["valor"]
        except KeyError as e:
            logger.warning("TRM row missing field, skipping: %s", e)
            continue

        try:
            vd = _parse_socrata_date(vd_raw)
            vh = _parse_socrata_date(vh_raw)
            value = Decimal(str(valor_raw))
        except (ValueError, ArithmeticError) as e:
            logger.warning("TRM row malformed, skipping: %s", e)
            continue

        d = vd
        while d <= vh:
            yield {
                "date": d,
                "value_cop": value,
                "vigencia_desde": vd,
                "vigencia_hasta": vh,
            }
            d += timedelta(days=1)
