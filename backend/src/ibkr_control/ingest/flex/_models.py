"""Dataclasses que el parser produce a partir del XML (intermediarias, no DB)."""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass
class ParsedAccount:
    ibkr_account_id: str
    currency: str
    account_alias: str | None = None
    account_type: str | None = None
    name: str | None = None


@dataclass
class ParsedTrade:
    transaction_id: str
    ibkr_account_id: str
    symbol: str
    asset_class: str
    trade_date: date
    settle_date: date | None
    qty: Decimal
    price_usd: Decimal
    proceeds_usd: Decimal
    commission_usd: Decimal
    open_close: str | None  # 'O' | 'C' | None
    buy_sell: str           # 'BUY' | 'SELL'
    raw_attrs: dict


@dataclass
class ParsedClosedLot:
    ibkr_account_id: str
    symbol: str
    open_date: date
    close_date: date
    close_datetime: datetime  # A3 amendment #3: per-execution timestamp, discriminator for natural key
    qty: Decimal
    cost_basis_usd: Decimal
    proceeds_usd: Decimal
    fifo_pnl_usd: Decimal
    transaction_id: str | None  # Links to Trade via raw XML transactionID; None if not captured


@dataclass
class ParsedOpenPositionLot:
    ibkr_account_id: str
    symbol: str
    open_date: date
    qty: Decimal
    cost_basis_usd: Decimal
    mark_price_usd: Decimal | None
    mark_value_usd: Decimal | None
    snapshot_date: date
    originating_transaction_id: str
    # IBKR <OpenPosition originatingTransactionID="...">. Discriminator for the
    # natural key (spec A3 amendment 2026-05-25). Multi-fill orders produce
    # multiple LOT rows with the same (account, symbol, open_date, snapshot_date)
    # — only this txn id distinguishes them. LOT-level rows always have it
    # populated; SUMMARY rows (already filtered by the parser) do not.


@dataclass
class ParsedCashTransaction:
    transaction_id: str
    ibkr_account_id: str
    type: str
    currency: str
    amount_usd: Decimal
    description: str | None
    date: date
    symbol: str | None


@dataclass
class ParsedTransfer:
    transaction_id: str
    transfer_date: date
    direction: str
    src_ibkr_account_id: str | None
    dst_ibkr_account_id: str | None
    symbol: str
    qty: Decimal
    transfer_type: str


@dataclass
class ParsedDividendAccrual:
    ibkr_account_id: str
    symbol: str
    conid: str | None
    isin: str | None
    issuer_country: str | None
    currency: str
    ex_date: date | None
    pay_date: date | None
    report_date: date
    accrual_date: date | None
    quantity: Decimal
    gross_rate_per_share: Decimal | None
    gross_amount_usd: Decimal
    tax_usd: Decimal
    fee_usd: Decimal | None
    net_amount_usd: Decimal
    action_id: str | None
    asset_category: str | None
    sub_category: str | None
    level_of_detail: str | None
    code: str
    # IBKR <ChangeInDividendAccrual code="Po|Re|..."> — first-class column post
    # A3 amendment #2 (2026-05-25). Discriminator together with action_id +
    # report_date for accrual lifecycle events (Posted vs Reversal) over the same
    # accrual_date. Normalized to "" (never None) so it works as a UNIQUE key
    # column without needing NULLS NOT DISTINCT (which is PG15+ only).
    raw_attrs: dict


@dataclass
class ParsedOpenDividendAccrual:
    ibkr_account_id: str
    symbol: str
    conid: str | None
    isin: str | None
    issuer_country: str | None
    currency: str
    ex_date: date | None
    pay_date: date | None
    report_date: date
    quantity: Decimal
    gross_rate_per_share: Decimal | None
    gross_amount_usd: Decimal
    tax_usd: Decimal
    fee_usd: Decimal | None
    net_amount_usd: Decimal
    action_id: str | None
    asset_category: str | None
    sub_category: str | None
    code: str
    # Preemptive mirror of A3 amendment #2 (2026-05-25). The 2025 fixture only
    # exhibits the Po/Re collision pattern on change_in_dividend_accruals, but
    # the same IBKR semantics apply to open accruals — add the column +
    # discriminator now to avoid a future regression when an XML with multi-row
    # open accruals shows up.
    raw_attrs: dict


@dataclass
class ParsedXML:
    anyo: int
    period_from: date
    period_to: date
    accounts: list[ParsedAccount]
    trades: list[ParsedTrade]
    closed_lots: list[ParsedClosedLot]
    open_position_lots: list[ParsedOpenPositionLot]
    cash_transactions: list[ParsedCashTransaction]
    transfers: list[ParsedTransfer]
    change_in_dividend_accruals: list[ParsedDividendAccrual]
    open_dividend_accruals: list[ParsedOpenDividendAccrual]


class UnknownFlexTagError(RuntimeError):
    """Lanzada cuando el parser encuentra un tag TOP-level desconocido.

    No se silencia para forzar revisión humana cuando IBKR cambia el schema.
    """

    def __init__(self, tag: str):
        self.tag = tag
        super().__init__(
            f"Unknown TOP-level Flex tag: '{tag}'. "
            f"If this is a new IBKR tag, add it to _known_tags.KNOWN_TOP_LEVEL_TAGS "
            f"(and optionally to EXPLICITLY_IGNORED if it should be skipped)."
        )
