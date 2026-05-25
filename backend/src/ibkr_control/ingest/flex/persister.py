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
- Transfers + TransferLot: usamos `_upsert_immutable_returning_inserted` para
  identificar qué Transfers fueron realmente insertados (vs NO-OP por
  duplicado) y solo entonces insertamos sus TransferLot children. Los
  Transfers ya existentes ya tienen sus lots en DB — no re-insertamos
  (TransferLot no tiene UNIQUE constraint, sería duplicación).
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
from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.db.models.accounts import Account
from ibkr_control.db.models.flex_raw import (
    CashTransaction,
    ChangeInDividendAccrual,
    ClosedLot,
    FlexImport,
    OpenDividendAccrual,
    OpenPositionLot,
    Trade,
    Transfer,
    TransferLot,
)
from ibkr_control.ingest.flex._models import ParsedXML
from ibkr_control.ingest.flex._upsert_helpers import (
    _upsert_immutable,
    _upsert_immutable_returning_inserted,
    _upsert_snapshot,
)
from ibkr_control.ingest.hash_dedup import xml_hash


def _is_shadow_account(ibkr_account_id: str) -> bool:
    """IB-UK Limited regulatory shadow account (NAV=0, no fiscal data).

    Per spec section 1 + renta sibling: F-accounts only carry fees/journals;
    never trades, open positions, or closed lots. Filtered at persist time
    to keep `accounts` table free of accounts that shouldn't have participations.
    """
    return ibkr_account_id.endswith("F")


async def persist(
    session: AsyncSession,
    *,
    parsed: ParsedXML,
    user_id: int,
    xml_bytes: bytes,
    source: str,  # 'web_service' | 'manual_upload'
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

    # Fast-path A4: hash dedup
    existing_id = await session.scalar(
        select(FlexImport.id).where(FlexImport.xml_hash == h)
    )
    if existing_id is not None:
        return existing_id, {"hash_dedup": True}

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
    # Transfers reference accounts via src/dst fields
    for tr in parsed.transfers:
        if tr.src_ibkr_account_id and not _is_shadow_account(tr.src_ibkr_account_id):
            all_account_ids.add(tr.src_ibkr_account_id)
        if tr.dst_ibkr_account_id and not _is_shadow_account(tr.dst_ibkr_account_id):
            all_account_ids.add(tr.dst_ibkr_account_id)
    for da in parsed.change_in_dividend_accruals:
        if not _is_shadow_account(da.ibkr_account_id):
            all_account_ids.add(da.ibkr_account_id)
    for oda in parsed.open_dividend_accruals:
        if not _is_shadow_account(oda.ibkr_account_id):
            all_account_ids.add(oda.ibkr_account_id)

    accounts_map = await _ensure_accounts(session, list(all_account_ids))

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
        user_id=user_id,
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

    # UPSERTs en orden de dependencia FK
    n_new = await _upsert_all_children(session, fi, parsed, accounts_map)

    # Update n_new_* en flex_imports
    fi.n_new_trades = n_new["trades"]
    fi.n_new_lots_closed = n_new["lots_closed"]
    fi.n_new_open_lots = n_new["open_lots"]
    fi.n_new_cash_tx = n_new["cash_tx"]
    fi.n_new_dividends = n_new["dividends"]
    fi.n_new_transfers = n_new["transfers"]
    await session.flush()

    return fi.id, {
        "hash_dedup": False,
        **{f"n_observed_{k}": v for k, v in n_observed.items()},
        **{f"n_new_{k}": v for k, v in n_new.items()},
    }


async def _upsert_all_children(
    session: AsyncSession,
    fi: FlexImport,
    parsed: ParsedXML,
    accounts_map: dict[str, int],
) -> dict[str, int]:
    """Hace UPSERT de todos los children. Devuelve n_new por entity type."""
    # === Trades (immutable) ===
    trade_rows = [
        {
            "flex_import_id": fi.id,
            "transaction_id": t.transaction_id,
            "account_id": accounts_map[t.ibkr_account_id],
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
        session, Trade.__table__, trade_rows, ["transaction_id"]
    )

    # === ClosedLots (immutable) ===
    # Necesitamos un mapa transaction_id → trades.id para linkar source_trade_id.
    # Funciona tanto si el trade fue recién insertado como si ya existía:
    # SELECT por transaction_id sobre toda la tabla.
    trade_id_map: dict[str, int] = {}
    if parsed.closed_lots:
        tx_ids_needed = [
            cl.transaction_id for cl in parsed.closed_lots if cl.transaction_id
        ]
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
            "transaction_id": (
                cl.transaction_id
                or f"NO-TX-{cl.symbol}-{cl.close_date}-{i}"
            ),
            "account_id": accounts_map[cl.ibkr_account_id],
            "symbol": cl.symbol,
            "open_date": cl.open_date,
            "close_date": cl.close_date,
            "qty": cl.qty,
            "cost_basis_usd": cl.cost_basis_usd,
            "proceeds_usd": cl.proceeds_usd,
            "fifo_pnl_usd": cl.fifo_pnl_usd,
            "source_trade_id": (
                trade_id_map.get(cl.transaction_id) if cl.transaction_id else None
            ),
        }
        for i, cl in enumerate(parsed.closed_lots)
        if not _is_shadow_account(cl.ibkr_account_id)
    ]
    n_new_closed = await _upsert_immutable(
        session, ClosedLot.__table__, closed_rows, ["transaction_id"]
    )

    # === CashTransactions (immutable) ===
    # Usamos el returning helper para contar dividends en el mismo round-trip
    # (sin un SELECT extra por type).
    cash_rows = [
        {
            "flex_import_id": fi.id,
            "transaction_id": ct.transaction_id,
            "account_id": accounts_map[ct.ibkr_account_id],
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
        ["transaction_id"],
        ["transaction_id", "type"],
    )
    n_new_cash = len(inserted_cash)
    n_new_dividends = sum(1 for row in inserted_cash if row.type == "Dividends")

    # === Transfers + TransferLots (spec § "Transfers con children") ===
    transfer_rows: list[dict] = []
    for tr in parsed.transfers:
        src_shadow = tr.src_ibkr_account_id and _is_shadow_account(
            tr.src_ibkr_account_id
        )
        dst_shadow = tr.dst_ibkr_account_id and _is_shadow_account(
            tr.dst_ibkr_account_id
        )
        if src_shadow or dst_shadow:
            continue
        transfer_rows.append(
            {
                "flex_import_id": fi.id,
                "transaction_id": tr.transaction_id,
                "transfer_date": tr.transfer_date,
                "direction": tr.direction,
                "src_account_id": (
                    accounts_map.get(tr.src_ibkr_account_id)
                    if tr.src_ibkr_account_id
                    else None
                ),
                "dst_account_id": (
                    accounts_map.get(tr.dst_ibkr_account_id)
                    if tr.dst_ibkr_account_id
                    else None
                ),
                "symbol": tr.symbol,
                "qty": tr.qty,
                "transfer_type": tr.transfer_type,
            }
        )

    inserted_transfers = await _upsert_immutable_returning_inserted(
        session,
        Transfer.__table__,
        transfer_rows,
        ["transaction_id"],
        ["id", "transaction_id"],
    )
    inserted_tx_ids = {r.transaction_id for r in inserted_transfers}
    transfer_id_map = {r.transaction_id: r.id for r in inserted_transfers}
    n_new_transfers = len(inserted_transfers)

    # Insert TransferLots solo para Transfers nuevos. Los Transfers existentes
    # ya tienen sus children en DB; re-insertar duplicaría (TransferLot no
    # tiene UNIQUE, no podríamos hacer ON CONFLICT).
    lot_rows: list[dict] = []
    for tr in parsed.transfers:
        if tr.transaction_id not in inserted_tx_ids:
            continue
        transfer_db_id = transfer_id_map[tr.transaction_id]
        for lot in tr.lots:
            lot_rows.append(
                {
                    "transfer_id": transfer_db_id,
                    "original_open_date": lot.original_open_date,
                    "qty": lot.qty,
                    "cost_basis_usd": lot.cost_basis_usd,
                }
            )
    if lot_rows:
        stmt = pg_insert(TransferLot.__table__).values(lot_rows)
        await session.execute(stmt)

    # === OpenPositionLots (snapshot) ===
    # Natural key incluye originating_transaction_id (A3 amendment 2026-05-25):
    # IBKR multi-fill orders generan múltiples LOT rows con la misma
    # (account, symbol, open_date, snapshot_date) tuple, distinguidos solo por
    # el originatingTransactionID per-lot. Sin él en el key, ON CONFLICT DO
    # UPDATE rechaza el batch con CardinalityViolationError.
    open_rows = [
        {
            "flex_import_id": fi.id,
            "account_id": accounts_map[op_lot.ibkr_account_id],
            "symbol": op_lot.symbol,
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
    n_new_open = await _upsert_snapshot(
        session,
        OpenPositionLot.__table__,
        open_rows,
        ["account_id", "symbol", "open_date", "snapshot_date", "originating_transaction_id"],
        [
            "flex_import_id",
            "qty",
            "cost_basis_usd",
            "mark_price_usd",
            "mark_value_usd",
        ],
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
            "account_id": accounts_map[da.ibkr_account_id],
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
    await _upsert_snapshot(
        session,
        ChangeInDividendAccrual.__table__,
        change_div_rows,
        [
            "account_id", "conid", "ex_date", "pay_date", "accrual_date",
            "report_date", "action_id", "code",
        ],
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
    )

    # === OpenDividendAccruals (snapshot) ===
    # Preemptive mirror of A3 amendment #2: extended natural key with
    # (action_id, code). Misma rationale que change_in_dividend_accruals — la
    # fixture 2025 no tiene multi-row pero la misma semántica IBKR aplica.
    open_div_rows = [
        {
            "flex_import_id": fi.id,
            "account_id": accounts_map[oda.ibkr_account_id],
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
    await _upsert_snapshot(
        session,
        OpenDividendAccrual.__table__,
        open_div_rows,
        [
            "account_id", "conid", "ex_date", "pay_date", "report_date",
            "action_id", "code",
        ],
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
    )

    return {
        "trades": n_new_trades,
        "lots_closed": n_new_closed,
        "open_lots": n_new_open,
        "cash_tx": n_new_cash,
        "dividends": n_new_dividends,
        "transfers": n_new_transfers,
    }


async def _ensure_accounts(
    session: AsyncSession,
    ibkr_ids: list[str],
) -> dict[str, int]:
    """Ensure DB rows exist for all given IBKR account IDs. Returns ibkr_id -> db id map.

    Race condition note: this function uses SELECT-then-INSERT, not ON CONFLICT.
    For the cron Flex flow, this is safe because flex_job.run() holds an
    advisory_lock(source='flex', user_id=X) preventing concurrent ingests for
    the same user. For manual uploads via /api/imports/upload, the spec (D9)
    explicitly forgoes the advisory lock (different XML hashes = independent
    work). In that case, two parallel uploads referencing the same NEW account
    could race here — the second would either get the existing row (race lost
    safely) or hit the UNIQUE(ibkr_account_id) constraint and fail. Acceptable
    V1 trade-off; if it becomes a problem, wrap with ON CONFLICT DO NOTHING.
    """
    if not ibkr_ids:
        return {}

    result = await session.scalars(
        select(Account).where(Account.ibkr_account_id.in_(ibkr_ids))
    )
    existing: dict[str, int] = {a.ibkr_account_id: a.id for a in result.all()}

    missing = set(ibkr_ids) - set(existing.keys())
    for ibkr_id in missing:
        a = Account(ibkr_account_id=ibkr_id, alias=None, currency="USD")
        session.add(a)

    if missing:
        await session.flush()
        result2 = await session.scalars(
            select(Account).where(Account.ibkr_account_id.in_(missing))
        )
        for a in result2.all():
            existing[a.ibkr_account_id] = a.id

    return existing
