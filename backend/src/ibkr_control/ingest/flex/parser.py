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

from datetime import date, datetime
from decimal import Decimal

from lxml import etree

from ibkr_control.ingest.flex._known_tags import KNOWN_TOP_LEVEL_TAGS, EXPLICITLY_IGNORED
from ibkr_control.ingest.flex._models import (
    ParsedAccount,
    ParsedTrade,
    ParsedClosedLot,
    ParsedOpenPositionLot,
    ParsedCashTransaction,
    ParsedTransfer,
    ParsedXML,
    ParsedDividendAccrual,
    ParsedOpenDividendAccrual,
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


def _parse_datetime(s: str | None) -> datetime | None:
    """Parse IBKR full datetime "YYYYMMDD;HHMMSS" or fallback to date-only.

    Returns None if input is empty/None. Date-only input gets time set to 00:00:00.
    """
    if s is None or s == "":
        return None
    s = s.strip()
    if ";" in s:
        date_part, time_part = s.split(";", 1)
        if (
            len(date_part) == 8
            and date_part.isdigit()
            and len(time_part) == 6
            and time_part.isdigit()
        ):
            return datetime(
                int(date_part[:4]),
                int(date_part[4:6]),
                int(date_part[6:8]),
                int(time_part[:2]),
                int(time_part[2:4]),
                int(time_part[4:6]),
            )
    if len(s) == 8 and s.isdigit():
        return datetime(int(s[:4]), int(s[4:6]), int(s[6:8]))
    # ISO format fallback
    return datetime.fromisoformat(s)


def _dec(s: str | None, default: str = "0") -> Decimal:
    if s is None or s == "":
        return Decimal(default)
    return Decimal(s)


def _attr(elem, name: str) -> str | None:
    """Get attribute or None."""
    val = elem.get(name)
    return val if val else None


def _is_summary_row(elem) -> bool:
    """Return True if this element is an IBKR SUMMARY-level rollup row.

    IBKR Activity XML emits some elements twice: a SUMMARY rollup (aggregated
    totals, often with accountId='-') and a DETAIL row with real per-account
    data. Skip SUMMARY rows to avoid duplicate counts and FK violations on
    the '-' placeholder account.
    """
    return elem.get("levelOfDetail") == "SUMMARY"


def parse(xml_bytes: bytes) -> ParsedXML:
    """Punto de entrada. Recibe bytes, devuelve ParsedXML."""
    root = etree.fromstring(xml_bytes)

    if root.tag != "FlexQueryResponse":
        raise ValueError(f"Expected root element <FlexQueryResponse>, got <{root.tag}>")

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
    change_in_dividend_accruals: list[ParsedDividendAccrual] = []
    open_dividend_accruals: list[ParsedOpenDividendAccrual] = []

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
                # Ignorado deliberadamente: <TransferLot> es sibling de <Transfer>
                # (no anidado) y carece de cost_basis/open_date -> impoblable desde
                # Activity Flex. Tabla transfer_lots eliminada (spec 2026-06-02).
                pass
            elif tag == "ChangeInDividendAccruals":
                _parse_change_in_dividend_accruals(child, change_in_dividend_accruals)
            elif tag == "OpenDividendAccruals":
                _parse_open_dividend_accruals(child, open_dividend_accruals)

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
        change_in_dividend_accruals=change_in_dividend_accruals,
        open_dividend_accruals=open_dividend_accruals,
    )


def _dedupe_accounts(accs: list[ParsedAccount]) -> list[ParsedAccount]:
    """Mismo account_id puede aparecer en varios FlexStatement."""
    seen: dict[str, ParsedAccount] = {}
    for a in accs:
        seen.setdefault(a.ibkr_account_id, a)
    return list(seen.values())


def _parse_account_information(elem, accounts: list[ParsedAccount]) -> None:
    accounts.append(
        ParsedAccount(
            ibkr_account_id=elem.get("accountId", ""),
            currency=elem.get("currency") or "USD",
            account_alias=elem.get("accountAlias") or None,
            account_type=elem.get("accountType") or None,
            name=elem.get("name") or None,
        )
    )


# Schema fijo de Trade (atributos que mapean a columnas tipadas).
# Cualquier otro atributo va a raw_attrs.
_TRADE_TYPED_ATTRS: frozenset[str] = frozenset(
    {
        "transactionID",
        "accountId",
        "symbol",
        "assetCategory",
        # W2: instrument identity/attributes promoted to typed fields (not raw_attrs).
        "conid",
        "isin",
        "description",
        "currency",
        "multiplier",
        "tradeDate",
        "settleDateTarget",
        "quantity",
        "tradePrice",
        "proceeds",
        "ibCommission",
        "openCloseIndicator",
        "buySell",
    }
)


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

    trades.append(
        ParsedTrade(
            transaction_id=elem.get("transactionID") or "",
            ibkr_account_id=elem.get("accountId") or "",
            symbol=elem.get("symbol") or "",
            asset_class=_require_asset_class(elem, "<Trade>"),
            conid=_require_conid(elem, "<Trade>"),
            isin=_instrument_isin(elem),
            description=_instrument_description(elem),
            currency=_instrument_currency(elem),
            multiplier=_instrument_multiplier(elem),
            trade_date=trade_date,
            settle_date=settle_date,
            qty=_dec(elem.get("quantity")),
            price_usd=_dec(elem.get("tradePrice")),
            proceeds_usd=_dec(elem.get("proceeds")),
            commission_usd=_dec(elem.get("ibCommission")),
            open_close=elem.get("openCloseIndicator") or None,
            buy_sell=elem.get("buySell") or "BUY",
            raw_attrs=raw_attrs,
            issuer_country=_instrument_issuer_country(elem),
        )
    )


def _require_asset_class(elem, context: str) -> str:
    """Extract assetCategory, failing loud if absent.

    asset_class is the fiscal-regime discriminator (STK -> Art.288 + 730d;
    FUT/OPT -> Decreto 1797). It must never be silently empty. Verified 100%
    present on real <Lot CLOSED_LOT>/<OpenPosition> rows; this guard catches
    future drift (consistent with the _known_tags fail-loud philosophy).
    """
    asset_class = elem.get("assetCategory")
    if not asset_class:
        raise ValueError(f"{context} missing required assetCategory attribute")
    return asset_class


def _require_conid(elem, context: str) -> str:
    """Extract conid, failing loud if absent OR empty (W2, T1-D8).

    conid is the securities-master identity for creator tags (trades, closed
    lots, open positions, accruals, and — per TL-D1 — security transfers with
    assetCategory != 'CASH'). CR-1 + the TL-D1 census verified 100% presence on
    these tags against the 3 real fixtures, so a missing/empty conid is drift,
    not a valid case — fail loud (mirror of _require_asset_class). Resolver tags
    (cash transactions, CASH transfers) use ``elem.get("conid") or None``
    instead and never reach here.
    """
    conid = elem.get("conid")
    if not conid:
        raise ValueError(f"{context} missing required conid attribute")
    return conid


def _instrument_isin(elem) -> str | None:
    return _attr(elem, "isin")


def _instrument_description(elem) -> str | None:
    return _attr(elem, "description")


def _instrument_currency(elem) -> str | None:
    return _attr(elem, "currency")


def _instrument_multiplier(elem) -> Decimal | None:
    """Multiplier (futuros). XML always carries it as a string; '' -> None."""
    raw = elem.get("multiplier")
    return _dec(raw) if raw else None


def _instrument_issuer_country(elem) -> str | None:
    """IC-2: país emisor canónico (issuerCountryCode). Idem isin/currency —
    atributo de instrumento que los creators aportan al spec en el persister."""
    return _attr(elem, "issuerCountryCode")


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
    close_dt_raw = elem.get("dateTime") or elem.get("tradeDate")
    close_date = _parse_date(close_dt_raw)
    if close_date is None:
        return None
    # A3 amendment #3: per-execution timestamp discriminator. Falls back to
    # midnight if IBKR emits date-only (rare, _parse_datetime handles both).
    close_datetime = _parse_datetime(close_dt_raw)
    if close_datetime is None:
        close_datetime = datetime.combine(close_date, datetime.min.time())

    cost_basis = _dec(elem.get("cost"))
    fifo_pnl = _dec(elem.get("fifoPnlRealized"))
    # proceeds is empty in Activity XML Lot rows; reconstruct from cost + pnl
    proceeds = cost_basis + fifo_pnl

    return ParsedClosedLot(
        ibkr_account_id=elem.get("accountId") or "",
        symbol=elem.get("symbol") or "",
        asset_class=_require_asset_class(elem, "<Lot CLOSED_LOT>"),
        conid=_require_conid(elem, "<Lot CLOSED_LOT>"),
        isin=_instrument_isin(elem),
        description=_instrument_description(elem),
        currency=_instrument_currency(elem),
        multiplier=_instrument_multiplier(elem),
        open_date=open_date,
        close_date=close_date,
        close_datetime=close_datetime,
        qty=_dec(elem.get("quantity")),
        cost_basis_usd=cost_basis,
        proceeds_usd=proceeds,
        fifo_pnl_usd=fifo_pnl,
        transaction_id=elem.get("transactionID") or None,
        issuer_country=_instrument_issuer_country(elem),
    )


def _parse_closed_lots_wrapper(elem) -> list[ParsedClosedLot]:
    """Parse standalone <ClosedLots> wrapper (from a separate Flex query).

    This is different from the Activity XML Lot elements. In this query type,
    each <ClosedLot> element has explicit costBasis and proceeds fields.
    """
    out: list[ParsedClosedLot] = []
    for lot in elem.iterchildren("ClosedLot"):
        open_date = _parse_date(lot.get("openDateTime") or lot.get("openDate"))
        close_dt_raw = lot.get("dateTime") or lot.get("closeDate") or lot.get("tradeDate")
        close_date = _parse_date(close_dt_raw)
        if open_date is None or close_date is None:
            continue
        close_datetime = _parse_datetime(close_dt_raw) or datetime.combine(
            close_date, datetime.min.time()
        )
        cost_basis = _dec(lot.get("costBasis"))
        fifo_pnl = _dec(lot.get("fifoPnlRealized"))
        proceeds_raw = lot.get("proceeds")
        proceeds = _dec(proceeds_raw) if proceeds_raw else cost_basis + fifo_pnl

        out.append(
            ParsedClosedLot(
                ibkr_account_id=lot.get("accountId") or "",
                symbol=lot.get("symbol") or "",
                asset_class=_require_asset_class(lot, "<ClosedLot>"),
                conid=_require_conid(lot, "<ClosedLot>"),
                isin=_instrument_isin(lot),
                description=_instrument_description(lot),
                currency=_instrument_currency(lot),
                multiplier=_instrument_multiplier(lot),
                open_date=open_date,
                close_date=close_date,
                close_datetime=close_datetime,
                qty=_dec(lot.get("quantity")),
                cost_basis_usd=cost_basis,
                proceeds_usd=proceeds,
                fifo_pnl_usd=fifo_pnl,
                transaction_id=lot.get("transactionID") or None,
                issuer_country=_instrument_issuer_country(lot),
            )
        )
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
        open_date = _parse_date(pos.get("openDateTime") or pos.get("holdingPeriodDateTime"))
        if open_date is None:
            continue  # Can't store a lot without its open date
        snapshot_date = _parse_date_required(pos.get("reportDate"), "reportDate")
        out.append(
            ParsedOpenPositionLot(
                ibkr_account_id=pos.get("accountId") or "",
                symbol=pos.get("symbol") or "",
                asset_class=_require_asset_class(pos, "<OpenPosition>"),
                conid=_require_conid(pos, "<OpenPosition>"),
                isin=_instrument_isin(pos),
                description=_instrument_description(pos),
                currency=_instrument_currency(pos),
                multiplier=_instrument_multiplier(pos),
                open_date=open_date,
                qty=_dec(pos.get("position")),
                cost_basis_usd=_dec(pos.get("costBasisMoney") or pos.get("costBasisPrice")),
                mark_price_usd=_dec(pos.get("markPrice")) if pos.get("markPrice") else None,
                mark_value_usd=_dec(pos.get("positionValue")) if pos.get("positionValue") else None,
                snapshot_date=snapshot_date,
                originating_transaction_id=pos.get("originatingTransactionID") or "",
                issuer_country=_instrument_issuer_country(pos),
            )
        )
    return out


def _parse_cash_transactions(elem) -> list[ParsedCashTransaction]:
    out: list[ParsedCashTransaction] = []
    for tx in elem.iterchildren("CashTransaction"):
        # IBKR Activity XML emits each CashTransaction twice:
        # - levelOfDetail="SUMMARY" with accountId="-" (rollup)
        # - levelOfDetail="DETAIL"  with the real accountId
        # We only want DETAIL rows. Skipping SUMMARY also avoids FK violations
        # on persistence (the "-" placeholder doesn't map to any accounts row).
        if _is_summary_row(tx):
            continue
        tx_date = _parse_date(tx.get("dateTime") or tx.get("settleDate"))
        if tx_date is None:
            continue
        raw_attrs = {k: v for k, v in tx.attrib.items() if k not in _CASH_TYPED_ATTRS}
        out.append(
            ParsedCashTransaction(
                transaction_id=tx.get("transactionID") or "",
                ibkr_account_id=tx.get("accountId") or "",
                type=tx.get("type") or "",
                currency=tx.get("currency") or "USD",
                amount_usd=_dec(tx.get("amount")),
                description=tx.get("description") or None,
                date=tx_date,
                symbol=tx.get("symbol") or None,
                conid=_attr(tx, "conid"),  # resolver-only: lookup if present, never create
                settle_date=_parse_date(tx.get("settleDate")),
                report_date=_parse_date(tx.get("reportDate")),
                ex_date=_parse_date(tx.get("exDate")),
                issuer_country=_attr(tx, "issuerCountryCode"),
                action_id=_attr(tx, "actionID"),
                raw_attrs=raw_attrs,
            )
        )
    return out


# Schema fijo de CashTransaction (atributos que mapean a columnas tipadas).
# Cualquier otro atributo va a raw_attrs (IC-1, idem accruals).
_CASH_TYPED_ATTRS: frozenset[str] = frozenset(
    {
        "transactionID",
        "accountId",
        "type",
        "currency",
        "amount",
        "description",
        "dateTime",
        "settleDate",
        "reportDate",
        "exDate",
        "issuerCountryCode",
        "actionID",
        "symbol",
        "conid",
        "levelOfDetail",
    }
)


# Schema fijo de ChangeInDividendAccrual (atributos que mapean a columnas tipadas).
# Cualquier otro atributo va a raw_attrs.
_DIV_ACCRUAL_TYPED_ATTRS: frozenset[str] = frozenset(
    {
        "accountId",
        "symbol",
        "conid",
        "isin",
        "issuerCountryCode",
        "currency",
        "exDate",
        "payDate",
        "reportDate",
        "date",
        "quantity",
        "grossRate",
        "grossAmount",
        "tax",
        "fee",
        "netAmount",
        "actionID",
        "assetCategory",
        "subCategory",
        "levelOfDetail",
        # `code` (Po/Re) promoted to first-class column post A3 amendment #2
        # (2026-05-25). Excluded from raw_attrs so it's not stored twice.
        "code",
    }
)

# Schema fijo de OpenDividendAccrual.
_OPEN_DIV_ACCRUAL_TYPED_ATTRS: frozenset[str] = frozenset(
    {
        "accountId",
        "symbol",
        "conid",
        "isin",
        "issuerCountryCode",
        "currency",
        "exDate",
        "payDate",
        "reportDate",
        "quantity",
        "grossRate",
        "grossAmount",
        "tax",
        "fee",
        "netAmount",
        "actionID",
        "assetCategory",
        "subCategory",
        "code",  # promoted post A3 amendment #2 (preemptive mirror)
    }
)


def _parse_change_in_dividend_accruals(
    elem,
    out: list[ParsedDividendAccrual],
) -> None:
    """Parse <ChangeInDividendAccruals> wrapper.

    Skips rows where accountId is None or '-' (SUMMARY-level rollups).
    In the V1 2025 fixture ALL 97 rows are SUMMARY with accountId='-',
    so the result will be 0 rows. Future Flex queries with DETAIL-level
    accruals will populate this list.
    """
    for row in elem.iterchildren("ChangeInDividendAccrual"):
        acct = row.get("accountId") or ""
        if not acct or acct == "-":
            continue
        rd = _parse_date(row.get("reportDate"))
        if rd is None:
            continue
        raw_attrs = {k: v for k, v in row.attrib.items() if k not in _DIV_ACCRUAL_TYPED_ATTRS}
        fee_raw = row.get("fee")
        gross_rate_raw = row.get("grossRate")
        out.append(
            ParsedDividendAccrual(
                ibkr_account_id=acct,
                symbol=row.get("symbol") or "",
                conid=_require_conid(row, "<ChangeInDividendAccrual>"),
                isin=_attr(row, "isin"),
                issuer_country=_attr(row, "issuerCountryCode"),
                currency=row.get("currency") or "USD",
                ex_date=_parse_date(row.get("exDate")),
                pay_date=_parse_date(row.get("payDate")),
                report_date=rd,
                accrual_date=_parse_date(row.get("date")),
                quantity=_dec(row.get("quantity")),
                gross_rate_per_share=_dec(gross_rate_raw) if gross_rate_raw else None,
                gross_amount_usd=_dec(row.get("grossAmount")),
                tax_usd=_dec(row.get("tax")),
                fee_usd=_dec(fee_raw) if fee_raw else None,
                net_amount_usd=_dec(row.get("netAmount")),
                action_id=_attr(row, "actionID"),
                asset_category=_attr(row, "assetCategory"),
                sub_category=_attr(row, "subCategory"),
                level_of_detail=_attr(row, "levelOfDetail"),
                code=row.get("code") or "",
                raw_attrs=raw_attrs,
            )
        )


def _parse_open_dividend_accruals(
    elem,
    out: list[ParsedOpenDividendAccrual],
) -> None:
    """Parse <OpenDividendAccruals> wrapper.

    Skips rows where accountId is None or '-' (SUMMARY-level rollups).
    """
    for row in elem.iterchildren("OpenDividendAccrual"):
        acct = row.get("accountId") or ""
        if not acct or acct == "-":
            continue
        rd = _parse_date(row.get("reportDate"))
        if rd is None:
            continue
        raw_attrs = {k: v for k, v in row.attrib.items() if k not in _OPEN_DIV_ACCRUAL_TYPED_ATTRS}
        fee_raw = row.get("fee")
        gross_rate_raw = row.get("grossRate")
        out.append(
            ParsedOpenDividendAccrual(
                ibkr_account_id=acct,
                symbol=row.get("symbol") or "",
                conid=_require_conid(row, "<OpenDividendAccrual>"),
                isin=_attr(row, "isin"),
                issuer_country=_attr(row, "issuerCountryCode"),
                currency=row.get("currency") or "USD",
                ex_date=_parse_date(row.get("exDate")),
                pay_date=_parse_date(row.get("payDate")),
                report_date=rd,
                quantity=_dec(row.get("quantity")),
                gross_rate_per_share=_dec(gross_rate_raw) if gross_rate_raw else None,
                gross_amount_usd=_dec(row.get("grossAmount")),
                tax_usd=_dec(row.get("tax")),
                fee_usd=_dec(fee_raw) if fee_raw else None,
                net_amount_usd=_dec(row.get("netAmount")),
                action_id=_attr(row, "actionID"),
                asset_category=_attr(row, "assetCategory"),
                sub_category=_attr(row, "subCategory"),
                code=row.get("code") or "",
                raw_attrs=raw_attrs,
            )
        )


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

        asset_class = _require_asset_class(tr, "<Transfer>")
        if asset_class == "CASH":
            # Movimiento interno de plata: sin instrumento por diseño (TL-D1).
            conid = _attr(tr, "conid")  # data real: siempre None (censo 100%)
        else:
            # Security transfer: spec completo de instrumento en el tag -> creator.
            conid = _require_conid(tr, "<Transfer>")

        transfer = ParsedTransfer(
            transaction_id=tr.get("transactionID") or "",
            transfer_date=transfer_date,
            direction=direction,
            src_ibkr_account_id=src,
            dst_ibkr_account_id=dst,
            symbol=tr.get("symbol") or "",
            qty=_dec(tr.get("quantity")),
            transfer_type=tr.get("type") or tr.get("transferType") or "unknown",
            asset_class=asset_class,
            conid=conid,
            isin=_instrument_isin(tr),
            description=_instrument_description(tr),
            issuer_country=_instrument_issuer_country(tr),
        )
        out.append(transfer)
    return out
