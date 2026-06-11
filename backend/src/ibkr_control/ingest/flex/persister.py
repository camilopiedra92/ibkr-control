"""Persiste un ParsedXML en Postgres idempotentemente (spec phase 2.5).

Estrategia (spec A4 + A8):
- Hash dedup fast-path: si xml_hash ya existe en flex_imports → return early
  con `(existing_id, {"hash_dedup": True})`
- Sino: crear FlexImport row (con xml_bytes A0) + UPSERT children por entity
- Immutable entities (Trade, ClosedLot, CashTx, Transfer):
  ON CONFLICT (transaction_id) DO NOTHING → preserva first-seen semantics
  (spec A1, `flex_import_id` queda apuntando a la primera importación)
- Snapshot entities (OpenPositionLot, ChangeInDividendAccrual,
  OpenDividendAccrual): ON CONFLICT (natural_key) DO UPDATE → la fila refleja
  el último XML que la observó (spec A1-bis, `flex_import_id` "last updated by")
- Counters n_observed_* (rows que llegaron en el XML) + n_new_* (rows que
  efectivamente se insertaron o actualizaron) persistidos en flex_imports
  (spec A5) y devueltos al caller en el dict de retorno.

ClosedLot.transaction_id placeholder
------------------------------------
El parser captura `transaction_id` desde el atributo XML transactionID, que
IBKR emite virtually always para `<ClosedLot>` en Activity statements. Si
una fila llega sin transactionID (rare), sintetizamos un placeholder
determinístico `NO-TX-{symbol}-{close_date}-{i}` (i = enumerate index).
Esto preserva idempotencia: mismo XML → mismos placeholders → ON CONFLICT
DO NOTHING absorbe colisiones cross-XML sin error.
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.db.models.accounts import Account
from ibkr_control.db.models.counterparties import Counterparty
from ibkr_control.db.models.instruments import Instrument, InstrumentIdentifier
from ibkr_control.db.models.flex_raw import (
    CashTransaction,
    ChangeInDividendAccrual,
    ClosedLot,
    FlexImport,
    FlexImportAccount,
    OpenDividendAccrual,
    OpenPositionLot,
    Trade,
    Transfer,
)
from ibkr_control.db.models.restatements import RestatementLog
from ibkr_control.ingest.flex._models import ParsedXML
from ibkr_control.ingest.flex._upsert_helpers import (
    _MAX_BIND_PARAMS,
    _upsert_immutable,
    _upsert_immutable_returning_inserted,
    _upsert_snapshot_with_audit,
)
from ibkr_control.ingest.hash_dedup import xml_hash

logger = logging.getLogger(__name__)

# Chunk size for the conid/instrument_id SELECT ... IN (...) lookups, mirroring
# _upsert_helpers._BATCH_SIZE: keeps the bind-param count well under asyncpg's
# 32767 ceiling (a single-column IN is one param per value).
_INSTRUMENT_BATCH = 5000


def _chunked(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _is_shadow_account(ibkr_account_id: str) -> bool:
    """IB-UK Limited regulatory shadow account (NAV=0, no fiscal data).

    Per spec section 1 + renta sibling: F-accounts only carry fees/journals;
    never trades, open positions, or closed lots. Filtered at persist time
    to keep `accounts` table free of accounts that shouldn't have participations.
    """
    return ibkr_account_id.endswith("F")


# Map de tabla afectada -> columna del key cuya fecha define el año fiscal de la
# fila (sealed_year, T1-D13): snapshot_date para lotes abiertos, report_date para
# accruals, close_date para closed_lots siblings. El año se deriva de ese valor.
_YEAR_KEY_COL = {
    "open_position_lots": "snapshot_date",
    "change_in_dividend_accruals": "report_date",
    "open_dividend_accruals": "report_date",
    "closed_lots": "close_date",
}


def _json_safe(value: Any) -> Any:
    """Serializa un valor de natural-key/columna a algo JSONB/Text-friendly.

    date/datetime -> ISO string; Decimal -> str (preserva escala exacta, sin
    float lossy); None pasa; el resto str(). Usado tanto para natural_key (JSONB)
    como para old_value/new_value (Text)."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date,)):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return str(value)


class _RestatementCollector:
    """Acumula restatements detectados durante el persist (un solo import).

    audit_sink (snapshot tables) y add_sibling (closed_lots) empujan acá; al final
    persist() computa sealed_year por año afectado y hace un INSERT batched.
    """

    def __init__(self) -> None:
        # (table_name, natural_key_dict_json, column_name, old_json, new_json, kind)
        self.rows: list[dict[str, Any]] = []

    def audit_sink(
        self, table_name: str, natural_key: dict[str, Any], column_name: str, old: Any, new: Any
    ) -> None:
        self.rows.append(
            {
                "table_name": table_name,
                "natural_key": {k: _json_safe(v) for k, v in natural_key.items()},
                "_natural_key_raw": natural_key,
                "column_name": column_name,
                "old_value": _json_safe(old),
                "new_value": _json_safe(new),
                "kind": "value_update",
            }
        )

    def add_sibling(self, table_name: str, natural_key: dict[str, Any], old: Any, new: Any) -> None:
        self.rows.append(
            {
                "table_name": table_name,
                "natural_key": {k: _json_safe(v) for k, v in natural_key.items()},
                "_natural_key_raw": natural_key,
                "column_name": "*",
                "old_value": _json_safe(old),
                "new_value": _json_safe(new),
                "kind": "sibling_row",
            }
        )


async def persist(
    session: AsyncSession,
    *,
    parsed: ParsedXML,
    organization_id: int,
    xml_bytes: bytes,
    source: str,  # 'web_service' | 'manual_upload'
    connection_id: int | None = None,
) -> tuple[int, dict]:
    """Idempotent persist. Devuelve `(flex_import_id, counters_dict)`.

    counters_dict shape:
      - `hash_dedup: bool` — True si el fast-path A4 disparó (xml_hash ya
        conocido), en cuyo caso ninguna otra key está presente
      - `n_observed_{trades,lots_closed,open_lots,cash_tx,dividends,transfers}`:
        rows que llegaron en el XML por entity type (post F-filter)
      - `n_new_{trades,lots_closed,open_lots,cash_tx,dividends,transfers}`:
        rows que efectivamente se insertaron o actualizaron en DB

    Todo el trabajo ocurre dentro de la sesión dada — el caller es
    responsable del commit o del rollback. No se llama session.commit() aquí.
    """
    h = xml_hash(xml_bytes)

    # Fast-path A4: hash dedup — scoped to (organization_id, xml_hash).
    # Scoping by org prevents matching rows from other orgs (RLS not active
    # on owner connections used in tests; explicit WHERE is correct in both
    # prod and test contexts).
    row = await session.execute(
        select(FlexImport.id, FlexImport.status).where(
            FlexImport.organization_id == organization_id,
            FlexImport.xml_hash == h,
        )
    )
    existing = row.first()
    if existing is not None:
        existing_id, existing_status = existing
        return existing_id, {"hash_dedup": True, "hash_status": existing_status}

    # Recopilar todos los ibkr_account_ids referenciados en el XML.
    # Skip F-suffix shadow accounts (IB-UK Limited, NAV=0) at every collection
    # point so they never reach the `accounts` table — see _is_shadow_account.
    all_account_ids: set[str] = set()
    for a in parsed.accounts:
        if not _is_shadow_account(a.ibkr_account_id):
            all_account_ids.add(a.ibkr_account_id)
    for t in parsed.trades:
        if not _is_shadow_account(t.ibkr_account_id):
            all_account_ids.add(t.ibkr_account_id)
    for cl in parsed.closed_lots:
        if not _is_shadow_account(cl.ibkr_account_id):
            all_account_ids.add(cl.ibkr_account_id)
    for op_lot in parsed.open_position_lots:
        if not _is_shadow_account(op_lot.ibkr_account_id):
            all_account_ids.add(op_lot.ibkr_account_id)
    for ct in parsed.cash_transactions:
        if not _is_shadow_account(ct.ibkr_account_id):
            all_account_ids.add(ct.ibkr_account_id)
    # NOTE: transfer src/dst peers are NOT collected here — external peers go to
    # counterparties (spec #6); own peers resolve via accounts_map below.
    for da in parsed.change_in_dividend_accruals:
        if not _is_shadow_account(da.ibkr_account_id):
            all_account_ids.add(da.ibkr_account_id)
    for oda in parsed.open_dividend_accruals:
        if not _is_shadow_account(oda.ibkr_account_id):
            all_account_ids.add(oda.ibkr_account_id)

    accounts_map = await _ensure_accounts(
        session, list(all_account_ids), organization_id=organization_id
    )

    # W2: securities master. Creators (trades/lots/accruals) crean el instrument;
    # cash/transfers son resolver-only (lookup por conid, nunca crean). Devuelve
    # conid -> instrument_id para threadear en los row builders. Control plane
    # (sin org scoping): AAPL es AAPL para todos los tenants (T1-D7).
    instruments_map = await _ensure_instruments(session, parsed)

    year_status = "sealed" if parsed.period_to >= date(parsed.anyo, 12, 31) else "rolling"

    # Counters: n_observed_* del XML (sin filtrar por shadow account porque el
    # filtro es ortogonal y los conteos son del XML "como llegó")
    n_observed = {
        "trades": len(parsed.trades),
        "lots_closed": len(parsed.closed_lots),
        "open_lots": len(parsed.open_position_lots),
        "cash_tx": len(parsed.cash_transactions),
        "dividends": sum(1 for ct in parsed.cash_transactions if ct.type == "Dividends"),
        "transfers": len(parsed.transfers),
    }

    fi = FlexImport(
        organization_id=organization_id,
        connection_id=connection_id,  # W1: linaje import -> connection (None=manual_upload)
        anyo=parsed.anyo,
        xml_hash=h,
        xml_size_bytes=len(xml_bytes),
        xml_bytes=xml_bytes,  # spec A0
        source=source,
        period_covered_from=parsed.period_from,
        period_covered_to=parsed.period_to,
        year_status=year_status,
        n_observed_trades=n_observed["trades"],
        n_observed_lots_closed=n_observed["lots_closed"],
        n_observed_open_lots=n_observed["open_lots"],
        n_observed_cash_tx=n_observed["cash_tx"],
        n_observed_dividends=n_observed["dividends"],
        n_observed_transfers=n_observed["transfers"],
        status="ok",
    )
    session.add(fi)
    await session.flush()  # para tener fi.id

    # Procedencia cuenta<->import (hardening H1): registra TODAS las cuentas
    # no-shadow observadas en el XML (accounts_map ya excluye F-shadow e incluye
    # las AccountInformation-only sin hechos). Es el link durable que el wizard
    # usa para scopear la validacion anti-IDOR de step2/save. ON CONFLICT DO
    # NOTHING lo hace idempotente; el fast-path A4 ya cubre el re-ingest del
    # mismo XML, esto cubre el caso degenerado de un retry intra-transaccion.
    if accounts_map:
        await session.execute(
            pg_insert(FlexImportAccount.__table__)
            .values(
                [
                    {
                        "flex_import_id": fi.id,
                        "account_id": aid,
                        "organization_id": organization_id,
                    }
                    for aid in accounts_map.values()
                ]
            )
            .on_conflict_do_nothing(index_elements=["flex_import_id", "account_id"])
        )

    # W3: acumulador de restatements (snapshot value_update + closed_lots sibling).
    restatements = _RestatementCollector()

    # UPSERTs en orden de dependencia FK
    n_new = await _upsert_all_children(
        session, fi, parsed, accounts_map, instruments_map, organization_id, restatements
    )

    # Update n_new_* en flex_imports
    fi.n_new_trades = n_new["trades"]
    fi.n_new_lots_closed = n_new["lots_closed"]
    fi.n_new_open_lots = n_new["open_lots"]
    fi.n_new_cash_tx = n_new["cash_tx"]
    fi.n_new_dividends = n_new["dividends"]
    fi.n_new_transfers = n_new["transfers"]
    await session.flush()

    # W3: persistir restatement_log (con sealed_year computado por año afectado).
    n_restatements = await _persist_restatements(
        session, restatements, organization_id=organization_id, flex_import_id=fi.id
    )

    # R1 latest-1 cleanup. For year_status='rolling' rows of the same
    # (organization_id, anyo, source), retain only the row just persisted (fi.id).
    # Sealed rows are pinned. Poison rows are forensic evidence — preserved.
    #
    # H1 provenance interaction: deleting an evicted rolling import CASCADE-deletes
    # its flex_import_accounts rows. This is intentional — provenance follows the
    # import. The current import's provenance was just written above (same
    # transaction, before this DELETE, against a different fi.id), so it survives;
    # any account still present in the new XML is re-covered. Consequence: if a
    # later rolling import for the same (org, anyo, source) drops an account
    # (not in the new XML), that account's provenance is gone — step2/save would
    # then return ACCOUNT_NOT_DETECTED for it. That is the correct outcome: the
    # most recent statement is authoritative about which accounts exist.
    await session.execute(
        text("""
            DELETE FROM flex_imports
            WHERE organization_id = :organization_id
              AND anyo = :anyo
              AND source = :source
              AND year_status = 'rolling'
              AND status = 'ok'
              AND id != :current_id
        """),
        {
            "organization_id": organization_id,
            "anyo": parsed.anyo,
            "source": source,
            "current_id": fi.id,
        },
    )

    return fi.id, {
        "hash_dedup": False,
        **{f"n_observed_{k}": v for k, v in n_observed.items()},
        **{f"n_new_{k}": v for k, v in n_new.items()},
        "n_restatements": n_restatements,
    }


def _restatement_year(row: dict[str, Any]) -> int | None:
    """Año fiscal de la fila afectada (T1-D13): derivado del valor-fecha del key
    que define el año por tabla (_YEAR_KEY_COL). Devuelve None si no se puede
    derivar (tabla sin mapeo o key sin la columna) -> sealed_year queda False."""
    col = _YEAR_KEY_COL.get(row["table_name"])
    if col is None:
        return None
    raw = row["_natural_key_raw"].get(col)
    if isinstance(raw, date):
        return raw.year
    if isinstance(raw, str) and len(raw) >= 4 and raw[:4].isdigit():
        return int(raw[:4])
    return None


async def _persist_restatements(
    session: AsyncSession,
    collector: "_RestatementCollector",
    *,
    organization_id: int,
    flex_import_id: int,
) -> int:
    """INSERT batched de restatement_log con sealed_year computado.

    sealed_year (T1-D13): el año fiscal de la fila afectada tiene un flex_imports
    row del org con year_status='sealed'. Se consulta una sola vez por el set de
    años distintos del batch. Devuelve el count de restatements persistidos.
    """
    if not collector.rows:
        return 0

    years = {y for row in collector.rows if (y := _restatement_year(row)) is not None}
    sealed_years: set[int] = set()
    if years:
        result = await session.execute(
            select(FlexImport.anyo)
            .where(
                FlexImport.organization_id == organization_id,
                FlexImport.anyo.in_(years),
                FlexImport.year_status == "sealed",
            )
            .distinct()
        )
        sealed_years = {y for (y,) in result.all()}

    insert_rows = [
        {
            "organization_id": organization_id,
            "flex_import_id": flex_import_id,
            "table_name": row["table_name"],
            "natural_key": row["natural_key"],
            "column_name": row["column_name"],
            "old_value": row["old_value"],
            "new_value": row["new_value"],
            "kind": row["kind"],
            "sealed_year": _restatement_year(row) in sealed_years,
        }
        for row in collector.rows
    ]
    # Chunk key-count-aware (mismo criterio que _upsert_helpers): cada row expande
    # len(cols) bind params; dividir el techo por el ancho de la fila.
    chunk_size = max(1, _MAX_BIND_PARAMS // len(insert_rows[0]))
    for batch in _chunked(insert_rows, chunk_size):
        await session.execute(pg_insert(RestatementLog.__table__).values(batch))
    return len(insert_rows)


async def _detect_closed_lot_siblings(
    session: AsyncSession,
    inserted: list[Row],
    *,
    organization_id: int,
    collector: "_RestatementCollector",
) -> None:
    """Por cada closed_lot recién insertado, busca filas existentes con el mismo
    (organization_id, transaction_id, close_datetime, qty) y distinto fifo_pnl_usd.

    Cada pareja (existente con distinto pnl ↔ nueva) es un sibling row: IBKR emitió
    un segundo cierre con el mismo timestamp/qty pero PnL realizado distinto
    (wash-sale o ajuste contable). Nunca borra; registra kind='sibling_row' una vez
    por sibling nuevo, con old=pnl preexistente, new=pnl de la fila nueva.

    Dedupe same-batch: si AMBAS filas de la pareja se insertaron en este mismo
    batch (caso real IBIT 2024, transactionID=29018827751: dos <Lot> con mismo
    timestamp/qty y pnls distintos en UN solo XML), cada una "ve" a la otra en su
    query y la pareja se loguearía DOS veces (espejo old<->new). Trackeamos la
    pareja NO-ordenada (key + frozenset de ambos pnls) y emitimos una sola vez.
    """
    if not inserted:
        return
    seen_pairs: set[tuple] = set()
    for row in inserted:
        result = await session.execute(
            select(ClosedLot.fifo_pnl_usd, ClosedLot.close_date).where(
                ClosedLot.organization_id == organization_id,
                ClosedLot.transaction_id == row.transaction_id,
                ClosedLot.close_datetime == row.close_datetime,
                ClosedLot.qty == row.qty,
                ClosedLot.fifo_pnl_usd != row.fifo_pnl_usd,
            )
        )
        for prior_pnl, prior_close_date in result.all():
            pair = (
                row.transaction_id,
                row.close_datetime,
                row.qty,
                frozenset({prior_pnl, row.fifo_pnl_usd}),
            )
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            collector.add_sibling(
                "closed_lots",
                {
                    "transaction_id": row.transaction_id,
                    "close_datetime": row.close_datetime,
                    "qty": row.qty,
                    "close_date": prior_close_date,
                },
                prior_pnl,
                row.fifo_pnl_usd,
            )


async def _upsert_all_children(
    session: AsyncSession,
    fi: FlexImport,
    parsed: ParsedXML,
    accounts_map: dict[str, int],
    instruments_map: dict[str, int],
    organization_id: int,
    restatements: "_RestatementCollector",
) -> dict[str, int]:
    """Hace UPSERT de todos los children. Devuelve n_new por entity type."""
    # Resolver-only lookup para cash/transfers (W2): conid presente pero no en el
    # map => NULL + un warning por conid (NUNCA crea instrument desde un resolver).
    _warned_missing_conids: set[str] = set()

    def _resolve_instrument(conid: str | None) -> int | None:
        if not conid:
            return None
        iid = instruments_map.get(conid)
        if iid is None and conid not in _warned_missing_conids:
            _warned_missing_conids.add(conid)
            logger.warning(
                "resolver conid %s has no instrument in this batch; "
                "instrument_id left NULL (resolver never creates instruments)",
                conid,
            )
        return iid

    # === Trades (immutable) ===
    trade_rows = [
        {
            "flex_import_id": fi.id,
            "organization_id": organization_id,
            "transaction_id": t.transaction_id,
            "account_id": accounts_map[t.ibkr_account_id],
            "instrument_id": instruments_map[t.conid],
            "symbol": t.symbol,
            "asset_class": t.asset_class,
            "trade_date": t.trade_date,
            "settle_date": t.settle_date,
            "qty": t.qty,
            "price_usd": t.price_usd,
            "proceeds_usd": t.proceeds_usd,
            "commission_usd": t.commission_usd,
            "open_close": t.open_close,
            "buy_sell": t.buy_sell,
            "raw_attrs": t.raw_attrs,
        }
        for t in parsed.trades
        if not _is_shadow_account(t.ibkr_account_id)
    ]
    n_new_trades = await _upsert_immutable(
        session, Trade.__table__, trade_rows, ["organization_id", "transaction_id"]
    )

    # === ClosedLots (immutable) ===
    # Necesitamos un mapa transaction_id → trades.id para linkar source_trade_id.
    # Funciona tanto si el trade fue recién insertado como si ya existía:
    # SELECT por transaction_id sobre toda la tabla.
    trade_id_map: dict[str, int] = {}
    if parsed.closed_lots:
        tx_ids_needed = [cl.transaction_id for cl in parsed.closed_lots if cl.transaction_id]
        if tx_ids_needed:
            result = await session.execute(
                select(Trade.transaction_id, Trade.id).where(
                    Trade.transaction_id.in_(tx_ids_needed)
                )
            )
            trade_id_map = {tid: tid_db for tid, tid_db in result.all()}

    closed_rows = [
        {
            "flex_import_id": fi.id,
            "organization_id": organization_id,
            "transaction_id": (cl.transaction_id or f"NO-TX-{cl.symbol}-{cl.close_date}-{i}"),
            "account_id": accounts_map[cl.ibkr_account_id],
            "instrument_id": instruments_map[cl.conid],
            "symbol": cl.symbol,
            "asset_class": cl.asset_class,
            "open_date": cl.open_date,
            "close_date": cl.close_date,
            "close_datetime": cl.close_datetime,
            "qty": cl.qty,
            "cost_basis_usd": cl.cost_basis_usd,
            "proceeds_usd": cl.proceeds_usd,
            "fifo_pnl_usd": cl.fifo_pnl_usd,
            "source_trade_id": (trade_id_map.get(cl.transaction_id) if cl.transaction_id else None),
        }
        for i, cl in enumerate(parsed.closed_lots)
        if not _is_shadow_account(cl.ibkr_account_id)
    ]
    # A3 amendment #3: natural key includes close_datetime + qty + fifo_pnl_usd
    # to preserve IBKR's multiple <Lot> rows sharing the same transactionID:
    # - Multi-execution closes against the same open_lot at different times
    # - Same close timestamp + qty but different realized PnL (wash-sale or
    #   accounting adjustments — seen on IBIT 2024-10-02 in fixture 2024).
    inserted_closed = await _upsert_immutable_returning_inserted(
        session,
        ClosedLot.__table__,
        closed_rows,
        ["organization_id", "transaction_id", "close_datetime", "qty", "fifo_pnl_usd"],
        ["transaction_id", "close_datetime", "qty", "fifo_pnl_usd", "close_date"],
    )
    n_new_closed = len(inserted_closed)

    # W3 sibling detection (T1-D12): una fila recién insertada cuyo
    # (organization_id, transaction_id, close_datetime, qty) coincide con una
    # existente pero con distinto fifo_pnl_usd es un sibling row (caso IBIT
    # wash-sale). Detección-only: NUNCA borra — ambas filas son hechos first-seen;
    # solo registramos el restatement una vez por sibling nuevo.
    await _detect_closed_lot_siblings(
        session, inserted_closed, organization_id=organization_id, collector=restatements
    )

    # === CashTransactions (immutable) ===
    # Usamos el returning helper para contar dividends en el mismo round-trip
    # (sin un SELECT extra por type).
    cash_rows = [
        {
            "flex_import_id": fi.id,
            "organization_id": organization_id,
            "transaction_id": ct.transaction_id,
            "account_id": accounts_map[ct.ibkr_account_id],
            "instrument_id": _resolve_instrument(ct.conid),
            "type": ct.type,
            "currency": ct.currency,
            "amount_usd": ct.amount_usd,
            "description": ct.description,
            "date": ct.date,
            "symbol": ct.symbol,
        }
        for ct in parsed.cash_transactions
        if not _is_shadow_account(ct.ibkr_account_id)
    ]
    inserted_cash = await _upsert_immutable_returning_inserted(
        session,
        CashTransaction.__table__,
        cash_rows,
        ["organization_id", "transaction_id"],
        ["transaction_id", "type"],
    )
    n_new_cash = len(inserted_cash)
    n_new_dividends = sum(1 for row in inserted_cash if row.type == "Dividends")

    # === Transfers (immutable por transaction_id) ===
    # Resolucion de peer por lado: si el ibkr_account_id es una cuenta propia
    # (en accounts_map) -> FK account; si no -> counterparty externo (spec #6).
    external_ids: set[str] = set()
    for tr in parsed.transfers:
        for peer in (tr.src_ibkr_account_id, tr.dst_ibkr_account_id):
            if peer and not _is_shadow_account(peer) and peer not in accounts_map:
                external_ids.add(peer)
    counterparties_map = await _ensure_counterparties(
        session, list(external_ids), organization_id=organization_id
    )

    def _resolve_side(peer: str | None) -> tuple[int | None, int | None]:
        """Returns (account_id, counterparty_id) — exactamente uno non-None,
        o (None, None) si no hay peer (rebota contra el exclusive arc CHECK)."""
        if not peer:
            return (None, None)
        if peer in accounts_map:
            return (accounts_map[peer], None)
        # peer not in accounts_map: must be external — guaranteed by external_ids pre-scan above
        return (None, counterparties_map[peer])

    transfer_rows: list[dict] = []
    for tr in parsed.transfers:
        src_shadow = tr.src_ibkr_account_id and _is_shadow_account(tr.src_ibkr_account_id)
        dst_shadow = tr.dst_ibkr_account_id and _is_shadow_account(tr.dst_ibkr_account_id)
        if src_shadow or dst_shadow:
            continue
        src_acct, src_cp = _resolve_side(tr.src_ibkr_account_id)
        dst_acct, dst_cp = _resolve_side(tr.dst_ibkr_account_id)
        transfer_rows.append(
            {
                "flex_import_id": fi.id,
                "organization_id": organization_id,
                "transaction_id": tr.transaction_id,
                "instrument_id": _resolve_instrument(tr.conid),
                "transfer_date": tr.transfer_date,
                "direction": tr.direction,
                "src_account_id": src_acct,
                "src_counterparty_id": src_cp,
                "dst_account_id": dst_acct,
                "dst_counterparty_id": dst_cp,
                "symbol": tr.symbol,
                "qty": tr.qty,
                "transfer_type": tr.transfer_type,
            }
        )

    n_new_transfers = await _upsert_immutable(
        session, Transfer.__table__, transfer_rows, ["organization_id", "transaction_id"]
    )

    # === OpenPositionLots (snapshot) ===
    # Natural key incluye originating_transaction_id (A3 amendment 2026-05-25):
    # IBKR multi-fill orders generan múltiples LOT rows con la misma
    # (account, symbol, open_date, snapshot_date) tuple, distinguidos solo por
    # el originatingTransactionID per-lot. Sin él en el key, ON CONFLICT DO
    # UPDATE rechaza el batch con CardinalityViolationError.
    open_rows = [
        {
            "flex_import_id": fi.id,
            "organization_id": organization_id,
            "account_id": accounts_map[op_lot.ibkr_account_id],
            "instrument_id": instruments_map[op_lot.conid],
            "symbol": op_lot.symbol,
            "asset_class": op_lot.asset_class,
            "open_date": op_lot.open_date,
            "qty": op_lot.qty,
            "cost_basis_usd": op_lot.cost_basis_usd,
            "mark_price_usd": op_lot.mark_price_usd,
            "mark_value_usd": op_lot.mark_value_usd,
            "snapshot_date": op_lot.snapshot_date,
            "originating_transaction_id": op_lot.originating_transaction_id,
        }
        for op_lot in parsed.open_position_lots
        if not _is_shadow_account(op_lot.ibkr_account_id)
    ]
    open_conflict_cols = [
        "account_id",
        "symbol",
        "open_date",
        "snapshot_date",
        "originating_transaction_id",
    ]
    n_new_open = await _upsert_snapshot_with_audit(
        session,
        OpenPositionLot.__table__,
        open_rows,
        open_conflict_cols,
        [
            "flex_import_id",
            "asset_class",
            "qty",
            "cost_basis_usd",
            "mark_price_usd",
            "mark_value_usd",
        ],
        # CR-2 (W3): columnas materiales = las que un restatement cambiaría.
        # mark_price/mark_value/flex_import_id son churn diario esperado (excluidas).
        material_cols=["qty", "cost_basis_usd", "asset_class"],
        natural_key_cols=open_conflict_cols,
        audit_sink=restatements.audit_sink,
    )

    # === ChangeInDividendAccruals (snapshot) ===
    # Natural key extendido con (report_date, action_id, code) per A3
    # amendment #2 (2026-05-25). IBKR emite múltiples eventos de lifecycle
    # (Posted/Reversal vía `code`) para el mismo dividendo; sin el
    # discriminador ON CONFLICT DO UPDATE rechaza con CardinalityViolationError.
    # report_date, action_id y code dejan de estar en update_cols (son parte
    # del key — actualizarlos cambiaría la identidad de la fila).
    change_div_rows = [
        {
            "flex_import_id": fi.id,
            "organization_id": organization_id,
            "account_id": accounts_map[da.ibkr_account_id],
            "instrument_id": instruments_map[da.conid],
            "symbol": da.symbol,
            "conid": da.conid,
            "isin": da.isin,
            "issuer_country": da.issuer_country,
            "currency": da.currency,
            "ex_date": da.ex_date,
            "pay_date": da.pay_date,
            "report_date": da.report_date,
            "accrual_date": da.accrual_date,
            "quantity": da.quantity,
            "gross_rate_per_share": da.gross_rate_per_share,
            "gross_amount_usd": da.gross_amount_usd,
            "tax_usd": da.tax_usd,
            "fee_usd": da.fee_usd,
            "net_amount_usd": da.net_amount_usd,
            "action_id": da.action_id,
            "asset_category": da.asset_category,
            "sub_category": da.sub_category,
            "level_of_detail": da.level_of_detail,
            "code": da.code,
            "raw_attrs": da.raw_attrs,
        }
        for da in parsed.change_in_dividend_accruals
        if not _is_shadow_account(da.ibkr_account_id)
    ]
    change_div_conflict_cols = [
        "account_id",
        "conid",
        "ex_date",
        "pay_date",
        "accrual_date",
        "report_date",
        "action_id",
        "code",
    ]
    await _upsert_snapshot_with_audit(
        session,
        ChangeInDividendAccrual.__table__,
        change_div_rows,
        change_div_conflict_cols,
        [
            "flex_import_id",
            "symbol",
            "isin",
            "issuer_country",
            "currency",
            "quantity",
            "gross_rate_per_share",
            "gross_amount_usd",
            "tax_usd",
            "fee_usd",
            "net_amount_usd",
            "asset_category",
            "sub_category",
            "level_of_detail",
            "raw_attrs",
        ],
        # CR-2 (W3): montos materiales. symbol/isin/currency/metadata + flex_import_id
        # excluidos (no son restatement fiscal).
        material_cols=[
            "quantity",
            "gross_rate_per_share",
            "gross_amount_usd",
            "tax_usd",
            "fee_usd",
            "net_amount_usd",
        ],
        natural_key_cols=change_div_conflict_cols,
        audit_sink=restatements.audit_sink,
    )

    # === OpenDividendAccruals (snapshot) ===
    # Preemptive mirror of A3 amendment #2: extended natural key with
    # (action_id, code). Misma rationale que change_in_dividend_accruals — la
    # fixture 2025 no tiene multi-row pero la misma semántica IBKR aplica.
    open_div_rows = [
        {
            "flex_import_id": fi.id,
            "organization_id": organization_id,
            "account_id": accounts_map[oda.ibkr_account_id],
            "instrument_id": instruments_map[oda.conid],
            "symbol": oda.symbol,
            "conid": oda.conid,
            "isin": oda.isin,
            "issuer_country": oda.issuer_country,
            "currency": oda.currency,
            "ex_date": oda.ex_date,
            "pay_date": oda.pay_date,
            "report_date": oda.report_date,
            "quantity": oda.quantity,
            "gross_rate_per_share": oda.gross_rate_per_share,
            "gross_amount_usd": oda.gross_amount_usd,
            "tax_usd": oda.tax_usd,
            "fee_usd": oda.fee_usd,
            "net_amount_usd": oda.net_amount_usd,
            "action_id": oda.action_id,
            "asset_category": oda.asset_category,
            "sub_category": oda.sub_category,
            "code": oda.code,
            "raw_attrs": oda.raw_attrs,
        }
        for oda in parsed.open_dividend_accruals
        if not _is_shadow_account(oda.ibkr_account_id)
    ]
    open_div_conflict_cols = [
        "account_id",
        "conid",
        "ex_date",
        "pay_date",
        "report_date",
        "action_id",
        "code",
    ]
    await _upsert_snapshot_with_audit(
        session,
        OpenDividendAccrual.__table__,
        open_div_rows,
        open_div_conflict_cols,
        [
            "flex_import_id",
            "symbol",
            "isin",
            "issuer_country",
            "currency",
            "quantity",
            "gross_rate_per_share",
            "gross_amount_usd",
            "tax_usd",
            "fee_usd",
            "net_amount_usd",
            "asset_category",
            "sub_category",
            "raw_attrs",
        ],
        # CR-2 (W3): mismo set material que change_in_dividend_accruals.
        material_cols=[
            "quantity",
            "gross_rate_per_share",
            "gross_amount_usd",
            "tax_usd",
            "fee_usd",
            "net_amount_usd",
        ],
        natural_key_cols=open_div_conflict_cols,
        audit_sink=restatements.audit_sink,
    )

    return {
        "trades": n_new_trades,
        "lots_closed": n_new_closed,
        "open_lots": n_new_open,
        "cash_tx": n_new_cash,
        "dividends": n_new_dividends,
        "transfers": n_new_transfers,
    }


async def _ensure_counterparties(
    session: AsyncSession,
    external_ids: list[str],
    *,
    organization_id: int,
) -> dict[str, int]:
    """Ensure counterparties rows exist for external (non-own) transfer peers.

    Returns external_id -> db id map. Scoped by organization_id because
    counterparties now have UNIQUE(organization_id, external_id) — two orgs
    can independently reference the same external broker peer.
    """
    if not external_ids:
        return {}

    result = await session.scalars(
        select(Counterparty).where(
            Counterparty.organization_id == organization_id,
            Counterparty.external_id.in_(external_ids),
        )
    )
    existing: dict[str, int] = {c.external_id: c.id for c in result.all()}

    missing = set(external_ids) - set(existing.keys())
    for ext_id in missing:
        session.add(Counterparty(organization_id=organization_id, external_id=ext_id))

    if missing:
        await session.flush()
        result2 = await session.scalars(
            select(Counterparty).where(
                Counterparty.organization_id == organization_id,
                Counterparty.external_id.in_(missing),
            )
        )
        for c in result2.all():
            existing[c.external_id] = c.id

    return existing


def _collect_instrument_specs(parsed: ParsedXML) -> dict[str, dict]:
    """Recolecta specs de instrumento de los CREATORS (W2, T1-D7).

    Creators = trades, closed_lots, open_position_lots, accruals x2 (los 5 tags
    que CR-1 confirmó con conid 100% presente). cash/transfers son resolvers — no
    aportan specs. Last-seen gana dentro del batch (un trade tardío con el ticker
    renombrado pisa al temprano). Devuelve conid -> {symbol, asset_class, name,
    currency, multiplier}.

    Los accruals no traen description/currency/multiplier; usan asset_category
    (nullable en DB por fidelidad de fuente, aunque CR-1 lo verificó 100% presente
    en los accruals reales). Aportan symbol + isin; el resto queda None y NO pisa
    lo que un trade/lot ya escribió (merge no destructivo abajo). Si un accrual es
    el ÚNICO creator de un conid y carece de assetCategory, _ensure_instruments
    falla loud (asset_class es NOT NULL en instruments) en vez de dejar que el
    INSERT reviente con un IntegrityError opaco.
    """
    specs: dict[str, dict] = {}

    def _merge(
        conid: str,
        *,
        symbol: str,
        asset_class: str | None,
        name: str | None = None,
        currency: str | None = None,
        multiplier=None,
    ) -> None:
        prev = specs.get(conid, {})
        specs[conid] = {
            "symbol": symbol or prev.get("symbol"),
            # asset_class del instrument: el primer creator con un valor no-vacío.
            # NOT NULL en el modelo -> garantizado por trades/lots (siempre lo traen).
            "asset_class": asset_class or prev.get("asset_class"),
            "name": name if name is not None else prev.get("name"),
            "currency": currency if currency is not None else prev.get("currency"),
            "multiplier": multiplier if multiplier is not None else prev.get("multiplier"),
            "isin": prev.get("isin"),
        }

    def _set_isin(conid: str, isin: str | None) -> None:
        if isin and specs.get(conid, {}).get("isin") is None:
            specs.setdefault(conid, {})["isin"] = isin

    for t in parsed.trades:
        _merge(
            t.conid,
            symbol=t.symbol,
            asset_class=t.asset_class,
            name=t.description,
            currency=t.currency,
            multiplier=t.multiplier,
        )
        _set_isin(t.conid, t.isin)
    for cl in parsed.closed_lots:
        _merge(
            cl.conid,
            symbol=cl.symbol,
            asset_class=cl.asset_class,
            name=cl.description,
            currency=cl.currency,
            multiplier=cl.multiplier,
        )
        _set_isin(cl.conid, cl.isin)
    for op_lot in parsed.open_position_lots:
        _merge(
            op_lot.conid,
            symbol=op_lot.symbol,
            asset_class=op_lot.asset_class,
            name=op_lot.description,
            currency=op_lot.currency,
            multiplier=op_lot.multiplier,
        )
        _set_isin(op_lot.conid, op_lot.isin)
    # conid es REQUIRED en los accruals (parser _require_conid, spec review W2):
    # sin guard de None — un accrual sin conid ya falló loud en parse-time.
    for da in parsed.change_in_dividend_accruals:
        _merge(da.conid, symbol=da.symbol, asset_class=da.asset_category, currency=da.currency)
        _set_isin(da.conid, da.isin)
    for oda in parsed.open_dividend_accruals:
        _merge(oda.conid, symbol=oda.symbol, asset_class=oda.asset_category, currency=oda.currency)
        _set_isin(oda.conid, oda.isin)

    return specs


async def _ensure_instruments(
    session: AsyncSession,
    parsed: ParsedXML,
) -> dict[str, int]:
    """Securities master: garantiza instruments + identifiers para cada conid de
    los creators. Devuelve conid -> instrument_id (W2, T1-D7/D8/D9).

    Control plane (sin RLS, sin org scoping): AAPL es AAPL para todos los tenants.
    - Resuelve por identifier ('conid', value).
    - Para conids faltantes: INSERT instrument + identifier rows (conid siempre;
      isin si está). ON CONFLICT (id_type, id_value) DO NOTHING + re-SELECT
      resuelve la carrera cross-org (mismo patrón que _ensure_accounts
      post-multihome): otro org pudo insertar el mismo conid concurrentemente.
    - Para existentes: DO UPDATE last-seen de symbol/name/currency/multiplier +
      updated_at SOLO si algo material cambió (comparación en Python) — evita
      churn de updated_at en cada ingest y deja el hook limpio para W3.
    """
    specs = _collect_instrument_specs(parsed)
    if not specs:
        return {}

    conids = list(specs.keys())

    # Resolver conids -> instrument_id por identifiers existentes (chunked).
    conid_to_iid: dict[str, int] = {}
    for batch in _chunked(conids, _INSTRUMENT_BATCH):
        rows = await session.execute(
            select(InstrumentIdentifier.id_value, InstrumentIdentifier.instrument_id).where(
                InstrumentIdentifier.id_type == "conid",
                InstrumentIdentifier.id_value.in_(batch),
            )
        )
        for id_value, iid in rows.all():
            conid_to_iid[id_value] = iid

    missing = [c for c in conids if c not in conid_to_iid]
    for conid in missing:
        spec = specs[conid]
        # Fail-loud (spec review W2): si el único creator de este conid fue un
        # accrual sin assetCategory, asset_class queda None y el INSERT rebotaría
        # con un IntegrityError opaco (NOT NULL). CR-1 verificó assetCategory 100%
        # presente en los accruals reales — este guard atrapa drift futuro con un
        # error accionable, igual que _require_asset_class/_require_conid.
        if not spec["asset_class"]:
            raise ValueError(
                f"instrument spec for conid {conid} (symbol {spec['symbol']!r}) is "
                "first created by an accrual that lacks assetCategory; cannot create "
                "instrument (asset_class is NOT NULL). Check the source XML."
            )
        instrument = Instrument(
            symbol=spec["symbol"],
            name=spec.get("name"),
            asset_class=spec["asset_class"],
            currency=spec.get("currency"),
            multiplier=spec.get("multiplier"),
        )
        session.add(instrument)
        await session.flush()  # para tener instrument.id

        # conid identifier siempre; isin si el XML lo trae. ON CONFLICT DO NOTHING
        # absorbe la carrera cross-org (otro org ya creó el mismo conid/isin).
        identifier_rows = [{"instrument_id": instrument.id, "id_type": "conid", "id_value": conid}]
        if spec.get("isin"):
            identifier_rows.append(
                {"instrument_id": instrument.id, "id_type": "isin", "id_value": spec["isin"]}
            )
        await session.execute(
            pg_insert(InstrumentIdentifier.__table__)
            .values(identifier_rows)
            .on_conflict_do_nothing(index_elements=["id_type", "id_value"])
        )

    # Re-SELECT para resolver TODOS los conids (incluidos los que perdieron la
    # carrera cross-org: su Instrument quedó huérfano sin identifier, pero el
    # conid resuelve al instrument ganador). Idempotente y correcto bajo cualquier rol.
    if missing:
        conid_to_iid = {}
        for batch in _chunked(conids, _INSTRUMENT_BATCH):
            rows = await session.execute(
                select(InstrumentIdentifier.id_value, InstrumentIdentifier.instrument_id).where(
                    InstrumentIdentifier.id_type == "conid",
                    InstrumentIdentifier.id_value.in_(batch),
                )
            )
            for id_value, iid in rows.all():
                conid_to_iid[id_value] = iid

    # DO UPDATE last-seen de atributos para los instruments existentes, SOLO si
    # algo material cambió (anti-churn de updated_at; hook limpio para W3).
    iids = list(conid_to_iid.values())
    existing_instruments: dict[int, Instrument] = {}
    for batch in _chunked(iids, _INSTRUMENT_BATCH):
        result = await session.scalars(select(Instrument).where(Instrument.id.in_(batch)))
        for inst in result.all():
            existing_instruments[inst.id] = inst

    for conid, iid in conid_to_iid.items():
        inst = existing_instruments.get(iid)
        if inst is None:
            continue
        spec = specs[conid]
        new_symbol = spec["symbol"]
        new_name = spec.get("name")
        new_currency = spec.get("currency")
        new_multiplier = spec.get("multiplier")
        changed = (
            (new_symbol and new_symbol != inst.symbol)
            or (new_name is not None and new_name != inst.name)
            or (new_currency is not None and new_currency != inst.currency)
            or (new_multiplier is not None and new_multiplier != inst.multiplier)
        )
        if changed:
            if new_symbol:
                inst.symbol = new_symbol
            if new_name is not None:
                inst.name = new_name
            if new_currency is not None:
                inst.currency = new_currency
            if new_multiplier is not None:
                inst.multiplier = new_multiplier
            inst.updated_at = func.now()

    return conid_to_iid


async def _ensure_accounts(
    session: AsyncSession,
    ibkr_ids: list[str],
    *,
    organization_id: int,
) -> dict[str, int]:
    """Ensure per-org rows exist for the given IBKR account IDs. Returns ibkr_id -> db id.

    Multi-home (spec 2026-06-10): accounts son únicos POR ORG
    (uq_accounts_org_ibkr_account_id) — la misma cuenta broker puede existir en
    N orgs. El SELECT scopea por organization_id EXPLÍCITAMENTE (no confía en
    el RLS-blinding: correcto bajo cualquier rol, tests con owner incluidos).

    Concurrencia same-org (ex-H2, ahora trivial): INSERT ... ON CONFLICT
    (organization_id, ibkr_account_id) DO NOTHING + re-select. El conflicto
    cross-org dejó de existir por diseño; el same-org (dos uploads paralelos,
    spec D9 sin advisory lock) lo absorbe el ON CONFLICT y el re-select SÍ ve
    la fila (mismo org) — el razonamiento de H2 sobre por qué esto no
    alcanzaba aplicaba SOLO a la unicidad global (SUPERSEDED).
    """
    if not ibkr_ids:
        return {}

    scoped = select(Account).where(
        Account.organization_id == organization_id,
        Account.ibkr_account_id.in_(ibkr_ids),
    )
    existing: dict[str, int] = {
        a.ibkr_account_id: a.id for a in (await session.scalars(scoped)).all()
    }

    missing = sorted(set(ibkr_ids) - set(existing))
    if missing:
        stmt = (
            pg_insert(Account.__table__)
            .values(
                [
                    {
                        "organization_id": organization_id,
                        "ibkr_account_id": ibkr_id,
                        "alias": None,
                        "currency": "USD",
                    }
                    for ibkr_id in missing
                ]
            )
            .on_conflict_do_nothing(index_elements=["organization_id", "ibkr_account_id"])
        )
        await session.execute(stmt)
        existing = {a.ibkr_account_id: a.id for a in (await session.scalars(scoped)).all()}

    return existing
