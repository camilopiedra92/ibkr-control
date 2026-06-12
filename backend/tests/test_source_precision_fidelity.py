"""Fidelidad de fuente (PD-5, spec 2026-06-12): roundtrip XML -> DB exacto.

Para cada columna NUMERIC proveniente del XML Flex (PD-2), el valor almacenado
en Postgres debe ser EXACTAMENTE el Decimal que emitió la fuente — sin redondeo
en el write path (PD-1: NUMERIC unconstrained). Este es el test que habría
atrapado Migration F ("match precisión del XML" con scale 4 vs fuente de 9
decimales en ibCommission) y los 114 falsos positivos del golden run W3.

Diseño:
- Subset check ``db_values <= source_values``: cada valor almacenado existe
  textualmente en el output del parser (que es ``Decimal(attr)`` crudo).
  Cualquier redondeo produce un valor que NO está en la fuente -> falla.
  Subset (no igualdad) porque el persister colapsa duplicados por natural key
  (e.g. transfers IBKR-mirror: n_observed != n_new documentado).
- Non-vacuity (test aparte, source-side): las columnas con pérdida probada en
  el censo 2026-06-12 deben exhibir el max de decimales censado — protege los
  dientes del test contra swaps/truncados de fixtures.
"""

from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.db.models.flex_raw import (
    CashTransaction,
    ChangeInDividendAccrual,
    ClosedLot,
    OpenDividendAccrual,
    OpenPositionLot,
    Trade,
    Transfer,
)
from ibkr_control.db.models.instruments import Instrument
from ibkr_control.ingest.flex.parser import parse
from ibkr_control.ingest.flex.persister import persist

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "xml"
FIXTURES = [
    "ACTIVITY_2024_sanitized.xml",
    "ACTIVITY_2025_sanitized.xml",
    "ACTIVITY_2026_FOP_sanitized.xml",
]

_ACCRUAL_COLS = [
    "quantity",
    "gross_rate_per_share",
    "gross_amount_usd",
    "tax_usd",
    "fee_usd",
    "net_amount_usd",
]

# (atributo de ParsedXML, modelo, columnas PD-2). Los nombres de campo de los
# dataclasses Parsed* espejan las columnas 1:1 (verificado contra _models.py).
ENTITIES = [
    ("trades", Trade, ["qty", "price_usd", "proceeds_usd", "commission_usd"]),
    ("closed_lots", ClosedLot, ["qty", "cost_basis_usd", "proceeds_usd", "fifo_pnl_usd"]),
    (
        "open_position_lots",
        OpenPositionLot,
        ["qty", "cost_basis_usd", "mark_price_usd", "mark_value_usd"],
    ),
    ("transfers", Transfer, ["qty"]),
    ("cash_transactions", CashTransaction, ["amount_usd"]),
    ("change_in_dividend_accruals", ChangeInDividendAccrual, _ACCRUAL_COLS),
    ("open_dividend_accruals", OpenDividendAccrual, _ACCRUAL_COLS),
]

# Censo 2026-06-12 contra los 3 XMLs reales: (entity, col) -> max decimales
# observados en la FUENTE. Si un fixture futuro baja de esto, el test de
# fidelidad se queda sin dientes -> este guard lo detecta.
_PROVEN_PRECISION = {
    ("trades", "commission_usd"): 9,
    ("trades", "proceeds_usd"): 8,
    ("open_position_lots", "mark_value_usd"): 9,
    ("open_position_lots", "cost_basis_usd"): 6,
    ("closed_lots", "cost_basis_usd"): 6,
    ("closed_lots", "fifo_pnl_usd"): 6,
    ("closed_lots", "proceeds_usd"): 6,
}


def _decimals(v: Decimal) -> int:
    exp = v.as_tuple().exponent
    return -exp if isinstance(exp, int) and exp < 0 else 0


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture_name", FIXTURES)
async def test_db_values_are_exact_source_values(
    db_session: AsyncSession, sample_org, fixture_name
):
    xml = (FIXTURES_DIR / fixture_name).read_bytes()
    parsed = parse(xml)
    await persist(
        db_session,
        parsed=parsed,
        organization_id=sample_org.id,
        xml_bytes=xml,
        source="manual_upload",
    )

    for attr, model, cols in ENTITIES:
        entities = getattr(parsed, attr)
        if not entities:
            continue  # e.g. 2024 no trae accruals
        for col in cols:
            source_values = {getattr(e, col) for e in entities if getattr(e, col) is not None}
            result = await db_session.execute(select(getattr(model, col)))
            db_values = {v for (v,) in result.all() if v is not None}
            rounded = db_values - source_values
            assert not rounded, (
                f"{fixture_name} {model.__tablename__}.{col}: {len(rounded)} valores "
                f"en DB que NO existen en la fuente (redondeo en el write path): "
                f"{sorted(rounded)[:5]}"
            )

    # instruments.multiplier (PD-2): el persister lo toma de los specs de los
    # creators — el valor almacenado debe existir en ALGÚN entity parseado.
    source_multipliers = set()
    for attr, _, _ in ENTITIES:
        for e in getattr(parsed, attr):
            m = getattr(e, "multiplier", None)
            if m is not None:
                source_multipliers.add(m)
    result = await db_session.execute(select(Instrument.multiplier))
    db_multipliers = {v for (v,) in result.all() if v is not None}
    rounded = db_multipliers - source_multipliers
    assert not rounded, f"instruments.multiplier redondeado: {sorted(rounded)[:5]}"


def test_proven_precision_columns_keep_teeth():
    """Non-vacuity source-side (no toca DB): el censo de decimales sigue vigente."""
    observed: dict[tuple[str, str], int] = {}
    for fixture_name in FIXTURES:
        parsed = parse((FIXTURES_DIR / fixture_name).read_bytes())
        for attr, col in _PROVEN_PRECISION:
            for e in getattr(parsed, attr):
                v = getattr(e, col)
                if v is not None:
                    key = (attr, col)
                    observed[key] = max(observed.get(key, 0), _decimals(v))
    for key, required in _PROVEN_PRECISION.items():
        assert observed.get(key, 0) >= required, (
            f"{key}: max decimales en fixtures = {observed.get(key, 0)} < {required} "
            f"censado — el test de fidelidad perdió los dientes (¿fixture truncado?)"
        )
