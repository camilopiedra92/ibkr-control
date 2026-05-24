"""Parser de XML Flex → ParsedXML.

Recibe bytes del XML, devuelve dataclasses tipadas. NO toca DB.
Hace audit de tags TOP-level y aborta si encuentra alguno desconocido
(patrón replicado de renta/documentos/ibkr_flex/_audit.py).

Notas sobre el formato Activity XML de IBKR:
- <Lot levelOfDetail="CLOSED_LOT"> aparece dentro de <Trades>, NO en un
  <ClosedLots> wrapper separado. La Activity query consolida trades y lots.
- <OpenPosition> tiene levelOfDetail="SUMMARY" (por symbol) y "LOT" (por lote
  individual). Solo los LOT-level tienen openDateTime.
- Fechas pueden venir como "YYYY-MM-DD" o "YYYYMMDD" o "YYYYMMDD;HHMMSS"
  (timestamp con separador punto y coma).
- Transfers usan "account" (no "transferAccount") como peer account ID.
"""
from datetime import date
from decimal import Decimal

from lxml import etree

from ibkr_control.ingest.flex._known_tags import KNOWN_TOP_LEVEL_TAGS, EXPLICITLY_IGNORED
from ibkr_control.ingest.flex._models import (
    ParsedAccount, ParsedTrade, ParsedClosedLot, ParsedOpenPositionLot,
    ParsedCashTransaction, ParsedTransfer, ParsedTransferLot, ParsedXML,
    UnknownFlexTagError,
)


def _parse_date(s: str | None) -> date | None:
    """IBKR usa YYYY-MM-DD o YYYYMMDD según el campo.

    Algunos campos incluyen timestamp: "YYYYMMDD;HHMMSS" — solo tomamos la
    parte de fecha.
    """
    if s is None or s == "":
        return None
    s = s.strip()
    # Strip timestamp suffix (e.g. "20241204;094637" → "20241204")
    if ";" in s:
        s = s.split(";")[0]
    if len(s) == 8 and s.isdigit():
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    return date.fromisoformat(s)


def _parse_date_required(s: str | None, field_name: str = "date") -> date:
    """Like _parse_date but raises ValueError if the result would be None."""
    result = _parse_date(s)
    if result is None:
        raise ValueError(f"Required date field '{field_name}' is missing or empty")
    return result


def _dec(s: str | None, default: str = "0") -> Decimal:
    if s is None or s == "":
        return Decimal(default)
    return Decimal(s)


def _attr(elem, name: str) -> str | None:
    """Get attribute or None."""
    val = elem.get(name)
    return val if val else None


def parse(xml_bytes: bytes) -> ParsedXML:
    """Punto de entrada. Recibe bytes, devuelve ParsedXML."""
    root = etree.fromstring(xml_bytes)

    if root.tag != "FlexQueryResponse":
        raise ValueError(
            f"Expected root element <FlexQueryResponse>, got <{root.tag}>"
        )

    statements = root.findall(".//FlexStatement")
    if not statements:
        raise ValueError("No <FlexStatement> found in <FlexQueryResponse>")

    # Multi-account: cada FlexStatement puede ser de un account distinto.
    # Agregamos todo en un solo ParsedXML.
    accounts: list[ParsedAccount] = []
    trades: list[ParsedTrade] = []
    closed_lots: list[ParsedClosedLot] = []
    open_position_lots: list[ParsedOpenPositionLot] = []
    cash_transactions: list[ParsedCashTransaction] = []
    transfers: list[ParsedTransfer] = []

    period_from_list: list[date] = []
    period_to_list: list[date] = []

    for stmt in statements:
        from_date = _parse_date_required(stmt.get("fromDate"), "fromDate")
        to_date = _parse_date_required(stmt.get("toDate"), "toDate")
        period_from_list.append(from_date)
        period_to_list.append(to_date)

        # Audit de tags TOP-level: abortar si encontramos uno desconocido
        for child in stmt:
            tag = child.tag
            if tag not in KNOWN_TOP_LEVEL_TAGS:
                raise UnknownFlexTagError(tag)
            if tag in EXPLICITLY_IGNORED:
                continue

            if tag == "AccountInformation":
                _parse_account_information(child, accounts)
            elif tag == "Trades":
                _parse_trades_wrapper(child, trades, closed_lots)
            elif tag == "ClosedLots":
                # Standalone ClosedLots wrapper (separate Flex query, not Activity)
                closed_lots.extend(_parse_closed_lots_wrapper(child))
            elif tag == "OpenPositions":
                open_position_lots.extend(_parse_open_positions(child))
            elif tag == "CashTransactions":
                cash_transactions.extend(_parse_cash_transactions(child))
            elif tag == "Transfers":
                transfers.extend(_parse_transfers(child))
            elif tag == "TransferLots":
                # TransferLots can appear as a standalone top-level section;
                # when nested inside a Transfer they are handled in _parse_transfers.
                pass

    period_from = min(period_from_list)
    period_to = max(period_to_list)
    anyo = period_to.year

    return ParsedXML(
        anyo=anyo,
        period_from=period_from,
        period_to=period_to,
        accounts=_dedupe_accounts(accounts),
        trades=trades,
        closed_lots=closed_lots,
        open_position_lots=open_position_lots,
        cash_transactions=cash_transactions,
        transfers=transfers,
    )


def _dedupe_accounts(accs: list[ParsedAccount]) -> list[ParsedAccount]:
    """Mismo account_id puede aparecer en varios FlexStatement."""
    seen: dict[str, ParsedAccount] = {}
    for a in accs:
        seen.setdefault(a.ibkr_account_id, a)
    return list(seen.values())


def _parse_account_information(elem, accounts: list[ParsedAccount]) -> None:
    accounts.append(ParsedAccount(
        ibkr_account_id=elem.get("accountId", ""),
        currency=elem.get("currency") or "USD",
    ))


# Schema fijo de Trade (atributos que mapean a columnas tipadas).
# Cualquier otro atributo va a raw_attrs.
_TRADE_TYPED_ATTRS: frozenset[str] = frozenset({
    "transactionID", "accountId", "symbol", "assetCategory",
    "tradeDate", "settleDateTarget", "quantity", "tradePrice",
    "proceeds", "ibCommission", "openCloseIndicator", "buySell",
})


def _parse_trades_wrapper(
    elem,
    trades: list[ParsedTrade],
    closed_lots: list[ParsedClosedLot],
) -> None:
    """Parse <Trades> wrapper.

    Contiene Trade (executions), Lot (levelOfDetail=CLOSED_LOT), Order, SymbolSummary,
    AssetSummary. Solo procesamos Trade y Lot.
    """
    for child in elem:
        tag = child.tag
        if tag == "Trade":
            _parse_trade(child, trades)
        elif tag == "Lot":
            if child.get("levelOfDetail") == "CLOSED_LOT":
                lot = _parse_lot_as_closed_lot(child)
                if lot is not None:
                    closed_lots.append(lot)
        # Order, SymbolSummary, AssetSummary — ignored in Phase 2


def _parse_trade(elem, trades: list[ParsedTrade]) -> None:
    raw_attrs = {k: v for k, v in elem.attrib.items() if k not in _TRADE_TYPED_ATTRS}
    trade_date = _parse_date(elem.get("tradeDate"))
    if trade_date is None:
        # Skip trades without a trade date (malformed)
        return
    settle_raw = elem.get("settleDateTarget")
    settle_date = _parse_date(settle_raw) if settle_raw else None

    trades.append(ParsedTrade(
        transaction_id=elem.get("transactionID") or "",
        ibkr_account_id=elem.get("accountId") or "",
        symbol=elem.get("symbol") or "",
        asset_class=elem.get("assetCategory") or "",
        trade_date=trade_date,
        settle_date=settle_date,
        qty=_dec(elem.get("quantity")),
        price_usd=_dec(elem.get("tradePrice")),
        proceeds_usd=_dec(elem.get("proceeds")),
        commission_usd=_dec(elem.get("ibCommission")),
        open_close=elem.get("openCloseIndicator") or None,
        buy_sell=elem.get("buySell") or "BUY",
        raw_attrs=raw_attrs,
    ))


def _parse_lot_as_closed_lot(elem) -> ParsedClosedLot | None:
    """Convert a <Lot levelOfDetail="CLOSED_LOT"> into a ParsedClosedLot.

    In Activity XML, the Lot has:
    - openDateTime / holdingPeriodDateTime = open date+time
    - dateTime = close date+time
    - tradeDate = close date (fallback)
    - cost = cost_basis_usd (purchase cost)
    - tradePrice = sell price (per unit)
    - quantity = lot qty
    - fifoPnlRealized = realized PnL
    - proceeds is empty; compute as cost + fifoPnlRealized
    """
    open_date = _parse_date(elem.get("openDateTime") or elem.get("holdingPeriodDateTime"))
    if open_date is None:
        return None
    close_date = _parse_date(
        elem.get("dateTime") or elem.get("tradeDate")
    )
    if close_date is None:
        return None

    cost_basis = _dec(elem.get("cost"))
    fifo_pnl = _dec(elem.get("fifoPnlRealized"))
    # proceeds is empty in Activity XML Lot rows; reconstruct from cost + pnl
    proceeds = cost_basis + fifo_pnl

    return ParsedClosedLot(
        ibkr_account_id=elem.get("accountId") or "",
        symbol=elem.get("symbol") or "",
        open_date=open_date,
        close_date=close_date,
        qty=_dec(elem.get("quantity")),
        cost_basis_usd=cost_basis,
        proceeds_usd=proceeds,
        fifo_pnl_usd=fifo_pnl,
        transaction_id=elem.get("transactionID") or None,
    )


def _parse_closed_lots_wrapper(elem) -> list[ParsedClosedLot]:
    """Parse standalone <ClosedLots> wrapper (from a separate Flex query).

    This is different from the Activity XML Lot elements. In this query type,
    each <ClosedLot> element has explicit costBasis and proceeds fields.
    """
    out: list[ParsedClosedLot] = []
    for lot in elem.iterchildren("ClosedLot"):
        open_date = _parse_date(
            lot.get("openDateTime") or lot.get("openDate")
        )
        close_date = _parse_date(
            lot.get("dateTime") or lot.get("closeDate") or lot.get("tradeDate")
        )
        if open_date is None or close_date is None:
            continue
        cost_basis = _dec(lot.get("costBasis"))
        fifo_pnl = _dec(lot.get("fifoPnlRealized"))
        proceeds_raw = lot.get("proceeds")
        proceeds = _dec(proceeds_raw) if proceeds_raw else cost_basis + fifo_pnl

        out.append(ParsedClosedLot(
            ibkr_account_id=lot.get("accountId") or "",
            symbol=lot.get("symbol") or "",
            open_date=open_date,
            close_date=close_date,
            qty=_dec(lot.get("quantity")),
            cost_basis_usd=cost_basis,
            proceeds_usd=proceeds,
            fifo_pnl_usd=fifo_pnl,
            transaction_id=lot.get("transactionID") or None,
        ))
    return out


def _parse_open_positions(elem) -> list[ParsedOpenPositionLot]:
    """Parse <OpenPositions> wrapper.

    Only processes LOT-level entries (levelOfDetail="LOT"); SUMMARY-level
    entries are aggregated rollups without per-lot open dates.
    """
    out: list[ParsedOpenPositionLot] = []
    for pos in elem.iterchildren("OpenPosition"):
        if pos.get("levelOfDetail") != "LOT":
            continue  # Skip SUMMARY-level; no individual lot open date
        open_date = _parse_date(
            pos.get("openDateTime") or pos.get("holdingPeriodDateTime")
        )
        if open_date is None:
            continue  # Can't store a lot without its open date
        snapshot_date = _parse_date_required(
            pos.get("reportDate"), "reportDate"
        )
        out.append(ParsedOpenPositionLot(
            ibkr_account_id=pos.get("accountId") or "",
            symbol=pos.get("symbol") or "",
            open_date=open_date,
            qty=_dec(pos.get("position")),
            cost_basis_usd=_dec(pos.get("costBasisMoney") or pos.get("costBasisPrice")),
            mark_price_usd=_dec(pos.get("markPrice")) if pos.get("markPrice") else None,
            mark_value_usd=_dec(pos.get("positionValue")) if pos.get("positionValue") else None,
            snapshot_date=snapshot_date,
        ))
    return out


def _parse_cash_transactions(elem) -> list[ParsedCashTransaction]:
    out: list[ParsedCashTransaction] = []
    for tx in elem.iterchildren("CashTransaction"):
        # IBKR Activity XML emits each CashTransaction twice:
        # - levelOfDetail="SUMMARY" with accountId="-" (rollup)
        # - levelOfDetail="DETAIL"  with the real accountId
        # We only want DETAIL rows. Skipping SUMMARY also avoids FK violations
        # on persistence (the "-" placeholder doesn't map to any accounts row).
        if tx.get("levelOfDetail") == "SUMMARY":
            continue
        tx_date = _parse_date(tx.get("dateTime") or tx.get("settleDate"))
        if tx_date is None:
            continue
        out.append(ParsedCashTransaction(
            ibkr_account_id=tx.get("accountId") or "",
            type=tx.get("type") or "",
            currency=tx.get("currency") or "USD",
            amount_usd=_dec(tx.get("amount")),
            description=tx.get("description") or None,
            date=tx_date,
            symbol=tx.get("symbol") or None,
        ))
    return out


def _parse_transfers(elem) -> list[ParsedTransfer]:
    """Parse <Transfers> wrapper.

    In Activity XML, Transfer uses "account" (not "transferAccount") for the
    peer account. Direction="IN" means the asset arrived at accountId from account.
    Direction="OUT" means the asset left accountId to account.
    """
    out: list[ParsedTransfer] = []
    for tr in elem.iterchildren("Transfer"):
        transfer_date = _parse_date(tr.get("dateTime") or tr.get("date"))
        if transfer_date is None:
            continue

        account_id = tr.get("accountId") or ""
        peer_account = tr.get("account") or tr.get("transferAccount") or None
        direction = tr.get("direction") or ""

        if direction == "IN":
            src = peer_account
            dst = account_id
        else:
            src = account_id
            dst = peer_account

        transfer = ParsedTransfer(
            transfer_date=transfer_date,
            direction=direction,
            src_ibkr_account_id=src,
            dst_ibkr_account_id=dst,
            symbol=tr.get("symbol") or "",
            qty=_dec(tr.get("quantity")),
            transfer_type=tr.get("type") or tr.get("transferType") or "unknown",
        )
        # Nested TransferLot rows (present in some query types)
        for lot in tr.iterchildren("TransferLot"):
            open_date = _parse_date(
                lot.get("openDateTime") or lot.get("originalOpenDate")
            )
            if open_date is None:
                continue
            transfer.lots.append(ParsedTransferLot(
                original_open_date=open_date,
                qty=_dec(lot.get("quantity")),
                cost_basis_usd=_dec(lot.get("costBasis")),
            ))
        out.append(transfer)
    return out
