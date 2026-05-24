"""Tests de la expansión vigencia_desde..vigencia_hasta."""
from datetime import date
from decimal import Decimal

from ibkr_control.ingest.trm.parser import expand_vigencias


def test_expand_single_day():
    rows = [{
        "vigenciadesde": "2026-01-15T00:00:00.000",
        "vigenciahasta": "2026-01-15T00:00:00.000",
        "valor": "4123.4567",
    }]
    out = list(expand_vigencias(rows))
    assert len(out) == 1
    d = out[0]
    assert d["date"] == date(2026, 1, 15)
    assert d["value_cop"] == Decimal("4123.4567")
    assert d["vigencia_desde"] == date(2026, 1, 15)
    assert d["vigencia_hasta"] == date(2026, 1, 15)


def test_expand_weekend_friday_to_monday():
    """Viernes 2026-01-16 vigente hasta domingo 2026-01-18 → 3 días."""
    rows = [{
        "vigenciadesde": "2026-01-16T00:00:00.000",
        "vigenciahasta": "2026-01-18T00:00:00.000",
        "valor": "4150.00",
    }]
    out = list(expand_vigencias(rows))
    assert len(out) == 3
    dates = [d["date"] for d in out]
    assert dates == [date(2026, 1, 16), date(2026, 1, 17), date(2026, 1, 18)]
    assert all(d["value_cop"] == Decimal("4150.00") for d in out)


def test_expand_multiple_rows_no_overlap():
    rows = [
        {"vigenciadesde": "2026-01-01T00:00:00.000", "vigenciahasta": "2026-01-02T00:00:00.000", "valor": "4100"},
        {"vigenciadesde": "2026-01-03T00:00:00.000", "vigenciahasta": "2026-01-05T00:00:00.000", "valor": "4120"},
    ]
    out = list(expand_vigencias(rows))
    assert len(out) == 5
    assert out[0]["date"] == date(2026, 1, 1)
    assert out[4]["date"] == date(2026, 1, 5)


def test_expand_ignores_invalid_row():
    """Si una row no tiene los 3 campos requeridos, se saltea (con warn)."""
    rows = [
        {"vigenciadesde": "2026-01-01T00:00:00.000", "vigenciahasta": "2026-01-01T00:00:00.000", "valor": "4100"},
        {"vigenciadesde": "2026-01-02T00:00:00.000"},  # incompleta
        {"vigenciadesde": "2026-01-03T00:00:00.000", "vigenciahasta": "2026-01-03T00:00:00.000", "valor": "4110"},
    ]
    out = list(expand_vigencias(rows))
    assert len(out) == 2


def test_expand_handles_date_only_format():
    """Algunos endpoints de Socrata devuelven '2026-01-15' sin time."""
    rows = [{
        "vigenciadesde": "2026-01-15",
        "vigenciahasta": "2026-01-15",
        "valor": "4100",
    }]
    out = list(expand_vigencias(rows))
    assert out[0]["date"] == date(2026, 1, 15)
