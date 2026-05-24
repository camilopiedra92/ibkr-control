"""Dataclasses que el parser produce a partir del XML (intermediarias, no DB)."""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass
class ParsedAccount:
    ibkr_account_id: str
    currency: str


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
    qty: Decimal
    cost_basis_usd: Decimal
    proceeds_usd: Decimal
    fifo_pnl_usd: Decimal


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


@dataclass
class ParsedCashTransaction:
    ibkr_account_id: str
    type: str
    currency: str
    amount_usd: Decimal
    description: str | None
    date: date
    symbol: str | None


@dataclass
class ParsedTransfer:
    transfer_date: date
    direction: str
    src_ibkr_account_id: str | None
    dst_ibkr_account_id: str | None
    symbol: str
    qty: Decimal
    transfer_type: str
    lots: list["ParsedTransferLot"] = field(default_factory=list)


@dataclass
class ParsedTransferLot:
    original_open_date: date
    qty: Decimal
    cost_basis_usd: Decimal


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
