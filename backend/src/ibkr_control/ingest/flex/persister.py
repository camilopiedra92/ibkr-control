"""Persiste un ParsedXML en Postgres (dentro de una TX).

- Inserta flex_imports con xml_hash (dedup via UNIQUE constraint)
- Si hash ya existe → devuelve el flex_import_id existente, NO inserta children
- Si hash es nuevo → inserta children en bulk (trades, closed_lots, ...)
- Crea accounts on-the-fly si el XML referencia accounts no existentes
- Calcula year_status (sealed vs rolling) segun period_covered_to
- Vincula closed_lots.source_trade_id a trades.id via captured transaction_id
"""
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.db.models.accounts import Account
from ibkr_control.db.models.flex_raw import (
    CashTransaction,
    ClosedLot,
    FlexImport,
    OpenPositionLot,
    Trade,
    Transfer,
    TransferLot,
)
from ibkr_control.ingest.flex._models import ParsedXML
from ibkr_control.ingest.hash_dedup import xml_hash


async def persist(
    session: AsyncSession,
    *,
    parsed: ParsedXML,
    user_id: int,
    xml_bytes: bytes,
    source: str,  # 'web_service' | 'manual_upload'
) -> int:
    """Devuelve el flex_import_id (existente si hash duplicado, nuevo si no).

    Todo el trabajo ocurre dentro de la sesion dada — el caller es responsable
    del commit o del rollback. No se llama session.commit() aqui.
    """
    h = xml_hash(xml_bytes)

    # Dedup: si ya existe el hash, return early
    existing_id = await session.scalar(
        select(FlexImport.id).where(FlexImport.xml_hash == h)
    )
    if existing_id is not None:
        return existing_id

    # Recopilar todos los ibkr_account_ids referenciados en el XML
    all_account_ids: set[str] = set()
    for a in parsed.accounts:
        all_account_ids.add(a.ibkr_account_id)
    for t in parsed.trades:
        all_account_ids.add(t.ibkr_account_id)
    for cl in parsed.closed_lots:
        all_account_ids.add(cl.ibkr_account_id)
    for op in parsed.open_position_lots:
        all_account_ids.add(op.ibkr_account_id)
    for ct in parsed.cash_transactions:
        all_account_ids.add(ct.ibkr_account_id)
    # Transfers reference accounts via src/dst fields
    for tr in parsed.transfers:
        if tr.src_ibkr_account_id:
            all_account_ids.add(tr.src_ibkr_account_id)
        if tr.dst_ibkr_account_id:
            all_account_ids.add(tr.dst_ibkr_account_id)

    accounts_map = await _ensure_accounts(session, list(all_account_ids))

    year_status = "sealed" if parsed.period_to >= date(parsed.anyo, 12, 31) else "rolling"

    fi = FlexImport(
        user_id=user_id,
        anyo=parsed.anyo,
        xml_hash=h,
        xml_size_bytes=len(xml_bytes),
        source=source,
        period_covered_from=parsed.period_from,
        period_covered_to=parsed.period_to,
        year_status=year_status,
        n_trades=len(parsed.trades),
        n_lots_closed=len(parsed.closed_lots),
        n_open_lots=len(parsed.open_position_lots),
        n_cash_tx=len(parsed.cash_transactions),
        n_dividends=sum(1 for ct in parsed.cash_transactions if ct.type == "Dividends"),
        n_transfers=len(parsed.transfers),
        status="ok",
    )
    session.add(fi)
    await session.flush()  # para tener fi.id

    # Insertar trades y capturar transaction_id → db id para linked closed lots
    for t in parsed.trades:
        session.add(Trade(
            flex_import_id=fi.id,
            transaction_id=t.transaction_id,
            account_id=accounts_map[t.ibkr_account_id],
            symbol=t.symbol,
            asset_class=t.asset_class,
            trade_date=t.trade_date,
            settle_date=t.settle_date,
            qty=t.qty,
            price_usd=t.price_usd,
            proceeds_usd=t.proceeds_usd,
            commission_usd=t.commission_usd,
            open_close=t.open_close,
            buy_sell=t.buy_sell,
            raw_attrs=t.raw_attrs,
        ))

    # Flush trades antes de construir el mapa transaction_id → trade.id
    await session.flush()

    # Construir mapa transaction_id → trades.id para linkar closed_lots
    trade_id_map: dict[str, int] = {}
    if parsed.closed_lots:
        result = await session.execute(
            select(Trade.transaction_id, Trade.id).where(
                Trade.flex_import_id == fi.id
            )
        )
        trade_id_map = {tid: tid_db for tid, tid_db in result.all()}

    for cl in parsed.closed_lots:
        source_trade_id = (
            trade_id_map.get(cl.transaction_id)
            if cl.transaction_id
            else None
        )
        session.add(ClosedLot(
            flex_import_id=fi.id,
            account_id=accounts_map[cl.ibkr_account_id],
            symbol=cl.symbol,
            open_date=cl.open_date,
            close_date=cl.close_date,
            qty=cl.qty,
            cost_basis_usd=cl.cost_basis_usd,
            proceeds_usd=cl.proceeds_usd,
            fifo_pnl_usd=cl.fifo_pnl_usd,
            source_trade_id=source_trade_id,
        ))

    for op in parsed.open_position_lots:
        session.add(OpenPositionLot(
            flex_import_id=fi.id,
            account_id=accounts_map[op.ibkr_account_id],
            symbol=op.symbol,
            open_date=op.open_date,
            qty=op.qty,
            cost_basis_usd=op.cost_basis_usd,
            mark_price_usd=op.mark_price_usd,
            mark_value_usd=op.mark_value_usd,
            snapshot_date=op.snapshot_date,
        ))

    for ct in parsed.cash_transactions:
        session.add(CashTransaction(
            flex_import_id=fi.id,
            account_id=accounts_map[ct.ibkr_account_id],
            type=ct.type,
            currency=ct.currency,
            amount_usd=ct.amount_usd,
            description=ct.description,
            date=ct.date,
            symbol=ct.symbol,
        ))

    for tr in parsed.transfers:
        transfer = Transfer(
            flex_import_id=fi.id,
            transfer_date=tr.transfer_date,
            direction=tr.direction,
            src_account_id=(
                accounts_map.get(tr.src_ibkr_account_id)
                if tr.src_ibkr_account_id
                else None
            ),
            dst_account_id=(
                accounts_map.get(tr.dst_ibkr_account_id)
                if tr.dst_ibkr_account_id
                else None
            ),
            symbol=tr.symbol,
            qty=tr.qty,
            transfer_type=tr.transfer_type,
        )
        session.add(transfer)
        await session.flush()  # para tener transfer.id
        for lot in tr.lots:
            session.add(TransferLot(
                transfer_id=transfer.id,
                original_open_date=lot.original_open_date,
                qty=lot.qty,
                cost_basis_usd=lot.cost_basis_usd,
            ))

    await session.flush()
    return fi.id


async def _ensure_accounts(
    session: AsyncSession,
    ibkr_ids: list[str],
) -> dict[str, int]:
    """Garantiza que existan accounts para todos los ibkr_ids.

    Devuelve mapping ibkr_id → db id.
    Solo inserta accounts que no existan; los existentes se devuelven tal cual.
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
