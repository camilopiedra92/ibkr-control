"""W3 — restatement log: detección de mutación material (T1-D10..D13).

Cubre el test de oro (re-ingest idéntico byte-distinto => 0 restatements), la
detección de value_update en columnas materiales de snapshot tables, el churn de
mark_price como NO-restatement, los sibling rows de closed_lots (detección-only,
nunca borra), y el flag sealed_year.

Patrón: testcontainers (db_session/sample_org) + ParsedXML sintético + fixtures
reales para el golden test. xml_bytes byte-distinto para esquivar el hash dedup
fast-path A4 (la idempotencia del persister es a nivel fila, no de documento).
"""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.db.models.flex_raw import ClosedLot, FlexImport, OpenPositionLot
from ibkr_control.db.models.restatements import RestatementLog
from ibkr_control.ingest.flex._models import (
    ParsedAccount,
    ParsedClosedLot,
    ParsedOpenPositionLot,
    ParsedXML,
)
from ibkr_control.ingest.flex.parser import parse
from ibkr_control.ingest.flex.persister import persist

FIXTURE_DIR = Path(__file__).parent.parent.parent / "fixtures" / "xml"


def _open_lot(
    *,
    qty: Decimal = Decimal("100"),
    cost_basis: Decimal = Decimal("1000.0000"),
    mark_price: Decimal = Decimal("12.50"),
    snapshot_date: date = date(2025, 12, 31),
    asset_class: str = "STK",
) -> ParsedOpenPositionLot:
    return ParsedOpenPositionLot(
        ibkr_account_id="U99999001",
        symbol="ACME",
        asset_class=asset_class,
        conid="ACME-CONID",
        isin=None,
        description="Acme Corp",
        currency="USD",
        multiplier=None,
        open_date=date(2025, 6, 1),
        qty=qty,
        cost_basis_usd=cost_basis,
        mark_price_usd=mark_price,
        mark_value_usd=qty * mark_price,
        snapshot_date=snapshot_date,
        originating_transaction_id="TX-OPEN-1",
    )


def _xml_with_open_lots(
    lots: list[ParsedOpenPositionLot], *, anyo: int = 2025, period_to: date | None = None
) -> ParsedXML:
    return ParsedXML(
        anyo=anyo,
        period_from=date(anyo, 1, 1),
        period_to=period_to or date(anyo, 12, 31),
        accounts=[ParsedAccount(ibkr_account_id="U99999001", currency="USD")],
        trades=[],
        closed_lots=[],
        open_position_lots=lots,
        cash_transactions=[],
        transfers=[],
        change_in_dividend_accruals=[],
        open_dividend_accruals=[],
    )


def _closed_lot(*, fifo_pnl: Decimal, txn: str = "CL-TX-1") -> ParsedClosedLot:
    return ParsedClosedLot(
        ibkr_account_id="U99999001",
        symbol="IBIT",
        asset_class="STK",
        conid="IBIT-CONID",
        isin=None,
        description="iShares Bitcoin Trust",
        currency="USD",
        multiplier=None,
        open_date=date(2025, 1, 5),
        close_date=date(2025, 10, 2),
        close_datetime=datetime(2025, 10, 2, 15, 30, 0),
        qty=Decimal("50"),
        cost_basis_usd=Decimal("2000.0000"),
        proceeds_usd=Decimal("2100.0000"),
        fifo_pnl_usd=fifo_pnl,
        transaction_id=txn,
    )


def _xml_with_closed_lots(lots: list[ParsedClosedLot], *, anyo: int = 2025) -> ParsedXML:
    return ParsedXML(
        anyo=anyo,
        period_from=date(anyo, 1, 1),
        period_to=date(anyo, 12, 31),
        accounts=[ParsedAccount(ibkr_account_id="U99999001", currency="USD")],
        trades=[],
        closed_lots=lots,
        open_position_lots=[],
        cash_transactions=[],
        transfers=[],
        change_in_dividend_accruals=[],
        open_dividend_accruals=[],
    )


async def _count_restatements(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(RestatementLog))


@pytest.mark.asyncio
async def test_golden_identical_reingest_zero_restatements(db_session: AsyncSession, sample_org):
    """EL TEST DE ORO: persist fixture 2025 -> re-persist el MISMO contenido con
    bytes distintos (comment XML para esquivar el hash dedup) -> 0 restatements.
    Cero falsos positivos por churn diario."""
    raw = (FIXTURE_DIR / "ACTIVITY_2025_sanitized.xml").read_bytes()
    parsed = parse(raw)
    await persist(
        db_session,
        parsed=parsed,
        organization_id=sample_org.id,
        xml_bytes=raw,
        source="manual_upload",
    )
    assert await _count_restatements(db_session) == 0

    # Byte-distinto (comment appended) pero semánticamente idéntico: el persister
    # re-parsea el mismo contenido y upsertea las mismas filas -> 0 restatements.
    reingest = raw + b"\n<!-- reingest -->"
    parsed2 = parse(reingest)
    await persist(
        db_session,
        parsed=parsed2,
        organization_id=sample_org.id,
        xml_bytes=reingest,
        source="manual_upload",
    )
    assert await _count_restatements(db_session) == 0


@pytest.mark.asyncio
async def test_value_update_detected_on_material_change(db_session: AsyncSession, sample_org):
    """Re-persist con cost_basis_usd de un open_position_lot cambiado -> 1 row
    value_update con table_name/natural_key/column_name/old/new correctos."""
    await persist(
        db_session,
        parsed=_xml_with_open_lots([_open_lot(cost_basis=Decimal("1000.0000"))]),
        organization_id=sample_org.id,
        xml_bytes=b"<v1/>",
        source="manual_upload",
    )
    assert await _count_restatements(db_session) == 0

    await persist(
        db_session,
        parsed=_xml_with_open_lots([_open_lot(cost_basis=Decimal("1234.5600"))]),
        organization_id=sample_org.id,
        xml_bytes=b"<v2/>",
        source="manual_upload",
    )

    rows = (await db_session.scalars(select(RestatementLog))).all()
    assert len(rows) == 1
    r = rows[0]
    assert r.kind == "value_update"
    assert r.table_name == "open_position_lots"
    assert r.column_name == "cost_basis_usd"
    assert r.old_value == "1000.0000"
    assert r.new_value == "1234.5600"
    assert r.natural_key["symbol"] == "ACME"
    assert r.natural_key["snapshot_date"] == "2025-12-31"
    assert r.organization_id == sample_org.id


@pytest.mark.asyncio
async def test_mark_price_churn_not_a_restatement(db_session: AsyncSession, sample_org):
    """Re-persist con mark_price (+ mark_value) cambiado -> 0 restatements
    (columna no-material: churn diario esperado)."""
    await persist(
        db_session,
        parsed=_xml_with_open_lots([_open_lot(mark_price=Decimal("12.50"))]),
        organization_id=sample_org.id,
        xml_bytes=b"<v1/>",
        source="manual_upload",
    )
    await persist(
        db_session,
        parsed=_xml_with_open_lots([_open_lot(mark_price=Decimal("99.99"))]),
        organization_id=sample_org.id,
        xml_bytes=b"<v2/>",
        source="manual_upload",
    )
    assert await _count_restatements(db_session) == 0


@pytest.mark.asyncio
async def test_full_precision_value_roundtrips_and_is_not_a_restatement(
    db_session: AsyncSession, sample_org
):
    """PD-1/PD-3 (spec 2026-06-12): storage exacto => re-ingest idéntico da cero
    restatements POR CONSTRUCCIÓN, no por paridad de rounding-mode.

    Históricamente este test fijaba ROUND_HALF_UP vs HALF_EVEN contra la columna
    Numeric(20,4) (el boundary 255.86945 divergía entre PG y el default de
    Decimal.quantize). Con NUMERIC unconstrained la columna no redondea:
    _quantize_to_scale es pass-through (scale=None) y la comparación de
    _values_differ es exacta. Se preserva el valor boundary como input y se
    agrega el assert de roundtrip exacto."""
    value = Decimal("255.86945")
    await persist(
        db_session,
        parsed=_xml_with_open_lots([_open_lot(cost_basis=value)]),
        organization_id=sample_org.id,
        xml_bytes=b"<v1/>",
        source="manual_upload",
    )
    await persist(
        db_session,
        parsed=_xml_with_open_lots([_open_lot(cost_basis=value)]),
        organization_id=sample_org.id,
        xml_bytes=b"<v2/>",
        source="manual_upload",
    )
    stored = await db_session.scalar(select(OpenPositionLot.cost_basis_usd))
    assert stored == value, f"roundtrip no exacto: {stored} != {value}"
    assert await _count_restatements(db_session) == 0


@pytest.mark.asyncio
async def test_sibling_row_detected_closed_lots(db_session: AsyncSession, sample_org):
    """persist closed lot -> re-persist variante con MISMO (txn, close_datetime,
    qty) y distinto fifo_pnl (caso IBIT wash-sale): ambas filas EXISTEN (nunca
    borra) + 1 row sibling_row (column_name='*', old=pnl viejo, new=pnl nuevo)."""
    await persist(
        db_session,
        parsed=_xml_with_closed_lots([_closed_lot(fifo_pnl=Decimal("100.0000"))]),
        organization_id=sample_org.id,
        xml_bytes=b"<v1/>",
        source="manual_upload",
    )
    await persist(
        db_session,
        parsed=_xml_with_closed_lots([_closed_lot(fifo_pnl=Decimal("85.0000"))]),
        organization_id=sample_org.id,
        xml_bytes=b"<v2/>",
        source="manual_upload",
    )

    # Ambas closed_lots existen (nunca borra).
    n_lots = await db_session.scalar(
        select(func.count()).select_from(ClosedLot).where(ClosedLot.symbol == "IBIT")
    )
    assert n_lots == 2

    rows = (await db_session.scalars(select(RestatementLog))).all()
    assert len(rows) == 1
    r = rows[0]
    assert r.kind == "sibling_row"
    assert r.table_name == "closed_lots"
    assert r.column_name == "*"
    assert {r.old_value, r.new_value} == {"100.0000", "85.0000"}
    assert r.natural_key["transaction_id"] == "CL-TX-1"


@pytest.mark.asyncio
async def test_sibling_pair_in_single_batch_logged_once(db_session: AsyncSession, sample_org):
    """Regression (quality review Task 1): pareja de siblings en UN MISMO XML.

    Caso real ACTIVITY_2024 (transactionID=29018827751, dateTime=20241002;101005,
    qty=17, pnls 51.61965 vs 51.969907): ambas filas se insertan en el mismo batch
    y cada una "ve" a la otra en su query -> sin el dedupe de pareja no-ordenada
    se loguearían 2 restatements espejo (old<->new invertidos) para 1 pareja
    lógica. Deben quedar las 2 filas en closed_lots (nunca borra) y EXACTAMENTE
    1 sibling_row."""
    lots = [
        _closed_lot(fifo_pnl=Decimal("51.61965"), txn="29018827751"),
        _closed_lot(fifo_pnl=Decimal("51.969907"), txn="29018827751"),
    ]
    await persist(
        db_session,
        parsed=_xml_with_closed_lots(lots),
        organization_id=sample_org.id,
        xml_bytes=b"<same-batch-siblings/>",
        source="manual_upload",
    )

    n_lots = await db_session.scalar(
        select(func.count()).select_from(ClosedLot).where(ClosedLot.transaction_id == "29018827751")
    )
    assert n_lots == 2

    rows = (await db_session.scalars(select(RestatementLog))).all()
    assert len(rows) == 1
    assert rows[0].kind == "sibling_row"


@pytest.mark.asyncio
async def test_sealed_year_flag(db_session: AsyncSession, sample_org):
    """Con el flex_imports del año marcado year_status='sealed', el restatement
    cae con sealed_year=True; sin sealed, False."""
    # Caso sealed: el primer import (period_to == 31-dic) ya queda 'sealed'.
    await persist(
        db_session,
        parsed=_xml_with_open_lots([_open_lot(cost_basis=Decimal("1000.0000"))]),
        organization_id=sample_org.id,
        xml_bytes=b"<v1/>",
        source="manual_upload",
    )
    # Forzar sealed por si la heurística period_to no aplicara.
    await db_session.execute(
        update(FlexImport)
        .where(FlexImport.organization_id == sample_org.id)
        .values(year_status="sealed")
    )
    await db_session.flush()

    await persist(
        db_session,
        parsed=_xml_with_open_lots([_open_lot(cost_basis=Decimal("2000.0000"))]),
        organization_id=sample_org.id,
        xml_bytes=b"<v2/>",
        source="manual_upload",
    )
    r = await db_session.scalar(select(RestatementLog))
    assert r.sealed_year is True


@pytest.mark.asyncio
async def test_unsealed_year_flag_false(db_session: AsyncSession, sample_org):
    """Sin ningún flex_imports 'sealed' para el año, sealed_year=False. period_to
    mid-año => persist marca los imports 'rolling' (YTD intra-año)."""
    mid = date(2025, 6, 30)
    await persist(
        db_session,
        parsed=_xml_with_open_lots(
            [_open_lot(cost_basis=Decimal("1000.0000"), snapshot_date=mid)], period_to=mid
        ),
        organization_id=sample_org.id,
        xml_bytes=b"<v1/>",
        source="manual_upload",
    )
    await persist(
        db_session,
        parsed=_xml_with_open_lots(
            [_open_lot(cost_basis=Decimal("2000.0000"), snapshot_date=mid)], period_to=mid
        ),
        organization_id=sample_org.id,
        xml_bytes=b"<v2/>",
        source="manual_upload",
    )
    # Ningún flex_imports quedó 'sealed' (todos 'rolling' por period_to mid-año).
    n_sealed = await db_session.scalar(
        select(func.count()).select_from(FlexImport).where(FlexImport.year_status == "sealed")
    )
    assert n_sealed == 0
    r = await db_session.scalar(select(RestatementLog))
    assert r.sealed_year is False


@pytest.mark.asyncio
async def test_n_restatements_counter_in_persist_result(db_session: AsyncSession, sample_org):
    """persist() devuelve n_restatements en el dict de counters."""
    await persist(
        db_session,
        parsed=_xml_with_open_lots([_open_lot(cost_basis=Decimal("1000.0000"))]),
        organization_id=sample_org.id,
        xml_bytes=b"<v1/>",
        source="manual_upload",
    )
    _, counters = await persist(
        db_session,
        parsed=_xml_with_open_lots([_open_lot(cost_basis=Decimal("5000.0000"))]),
        organization_id=sample_org.id,
        xml_bytes=b"<v2/>",
        source="manual_upload",
    )
    assert counters["n_restatements"] == 1


@pytest.mark.asyncio
async def test_restatement_rows_carry_account_id(db_session: AsyncSession, sample_org):
    """SP2-D9: cada fila de restatement_log lleva el account_id del hecho afectado
    — en AMBOS kinds (value_update vía audit_sink + sibling_row vía add_sibling)."""
    from ibkr_control.db.models.accounts import Account

    # value_update: mismo natural key, qty material cambia entre ingests.
    await persist(
        db_session,
        parsed=_xml_with_open_lots([_open_lot(cost_basis=Decimal("1000.0000"))]),
        organization_id=sample_org.id,
        xml_bytes=b"<acct-a/>",
        source="manual_upload",
    )
    await persist(
        db_session,
        parsed=_xml_with_open_lots([_open_lot(cost_basis=Decimal("1234.5600"))]),
        organization_id=sample_org.id,
        xml_bytes=b"<acct-b/>",
        source="manual_upload",
    )
    # sibling_row: mismo (txn, close_datetime, qty), distinto fifo_pnl.
    await persist(
        db_session,
        parsed=_xml_with_closed_lots([_closed_lot(fifo_pnl=Decimal("10.00"))]),
        organization_id=sample_org.id,
        xml_bytes=b"<acct-c/>",
        source="manual_upload",
    )
    await persist(
        db_session,
        parsed=_xml_with_closed_lots([_closed_lot(fifo_pnl=Decimal("-2.50"))]),
        organization_id=sample_org.id,
        xml_bytes=b"<acct-d/>",
        source="manual_upload",
    )

    acct = await db_session.scalar(select(Account).where(Account.ibkr_account_id == "U99999001"))
    rows = (await db_session.scalars(select(RestatementLog))).all()
    kinds = {r.kind for r in rows}
    assert kinds == {"value_update", "sibling_row"}
    assert all(r.account_id == acct.id for r in rows)


@pytest.mark.asyncio
async def test_restatement_log_has_rls(db_session: AsyncSession):
    """restatement_log es org-scoped: aparece con RLS habilitado en pg_class bajo
    el schema migrado (cubierto además por test_rls.py vía ORG_SCOPED_TABLES). Acá
    verificamos la columna organization_id + el FK al import existe."""
    cols = (
        (
            await db_session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'restatement_log' ORDER BY column_name"
                )
            )
        )
        .scalars()
        .all()
    )
    assert "organization_id" in cols
    assert "flex_import_id" in cols
    assert "sealed_year" in cols
