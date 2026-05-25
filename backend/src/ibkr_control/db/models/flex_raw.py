"""Tablas crudas del Flex XML: flex_imports + trades + lots + transfers + cash_transactions
+ dividend accruals.

Estas tablas se pueblan tal-cual del XML, sin transformaciones fiscales.
La capa de classification (lot_classifications) vive en Phase 3.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Index,
    Integer, LargeBinary, Numeric, String, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class FlexImport(Base):
    __tablename__ = "flex_imports"
    __table_args__ = (
        CheckConstraint("source IN ('web_service', 'manual_upload')", name="ck_flex_imports_source"),
        CheckConstraint("year_status IN ('rolling', 'sealed')", name="ck_flex_imports_year_status"),
        CheckConstraint("status IN ('ok', 'failed')", name="ck_flex_imports_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    anyo: Mapped[int] = mapped_column(Integer, nullable=False)
    xml_hash: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    xml_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    xml_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    period_covered_from: Mapped[date] = mapped_column(Date, nullable=False)
    period_covered_to: Mapped[date] = mapped_column(Date, nullable=False)
    year_status: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'rolling'")
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    n_observed_trades: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_observed_lots_closed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_observed_open_lots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_observed_cash_tx: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_observed_dividends: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_observed_transfers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_new_trades: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_new_lots_closed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_new_open_lots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_new_cash_tx: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_new_dividends: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_new_transfers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        CheckConstraint(
            "open_close IS NULL OR open_close IN ('O', 'C')", name="ck_trades_open_close"
        ),
        CheckConstraint("buy_sell IN ('BUY', 'SELL')", name="ck_trades_buy_sell"),
        Index("trades_account_symbol_idx", "account_id", "symbol"),
        Index("trades_trade_date_idx", "trade_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    flex_import_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("flex_imports.id", ondelete="SET NULL"), nullable=True
    )
    transaction_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    asset_class: Mapped[str] = mapped_column(String, nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    settle_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    price_usd: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    proceeds_usd: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    commission_usd: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    open_close: Mapped[str | None] = mapped_column(String, nullable=True)
    buy_sell: Mapped[str] = mapped_column(String, nullable=False)
    raw_attrs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class ClosedLot(Base):
    __tablename__ = "closed_lots"
    __table_args__ = (
        Index("closed_lots_account_symbol_idx", "account_id", "symbol"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    flex_import_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("flex_imports.id", ondelete="SET NULL"), nullable=True
    )
    transaction_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    open_date: Mapped[date] = mapped_column(Date, nullable=False)
    close_date: Mapped[date] = mapped_column(Date, nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    cost_basis_usd: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    proceeds_usd: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    fifo_pnl_usd: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    source_trade_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("trades.id"), nullable=True
    )


class OpenPositionLot(Base):
    __tablename__ = "open_position_lots"
    __table_args__ = (
        Index("open_position_lots_account_symbol_idx", "account_id", "symbol"),
        # A3 amendment 2026-05-25: extended natural key with
        # originating_transaction_id (IBKR's per-lot id) because multi-fill
        # orders produce multiple distinct LOT rows for the same
        # (account, symbol, open_date, snapshot_date) tuple. See Alembic
        # Revision 3 (phase25_op_lots_otid_amendment).
        UniqueConstraint(
            "account_id", "symbol", "open_date", "snapshot_date",
            "originating_transaction_id",
            name="open_position_lots_natural_key",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    flex_import_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("flex_imports.id", ondelete="SET NULL"), nullable=True
    )
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    open_date: Mapped[date] = mapped_column(Date, nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    cost_basis_usd: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    mark_price_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    mark_value_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    originating_transaction_id: Mapped[str] = mapped_column(String, nullable=False)


class Transfer(Base):
    __tablename__ = "transfers"
    __table_args__ = (
        CheckConstraint("direction IN ('IN', 'OUT')", name="ck_transfers_direction"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    flex_import_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("flex_imports.id", ondelete="SET NULL"), nullable=True
    )
    transaction_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    transfer_date: Mapped[date] = mapped_column(Date, nullable=False)
    direction: Mapped[str] = mapped_column(String, nullable=False)
    src_account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("accounts.id"), nullable=True
    )
    dst_account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("accounts.id"), nullable=True
    )
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    transfer_type: Mapped[str] = mapped_column(String, nullable=False)


class TransferLot(Base):
    __tablename__ = "transfer_lots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    transfer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("transfers.id", ondelete="CASCADE"), nullable=False
    )
    original_open_date: Mapped[date] = mapped_column(Date, nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    cost_basis_usd: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)


class CashTransaction(Base):
    __tablename__ = "cash_transactions"
    __table_args__ = (
        Index("cash_transactions_date_idx", "date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    flex_import_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("flex_imports.id", ondelete="SET NULL"), nullable=True
    )
    transaction_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id"), nullable=False
    )
    type: Mapped[str] = mapped_column(String, nullable=False)
    currency: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'USD'")
    )
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    symbol: Mapped[str | None] = mapped_column(String, nullable=True)


class ChangeInDividendAccrual(Base):
    __tablename__ = "change_in_dividend_accruals"
    __table_args__ = (
        Index("change_in_dividend_accruals_report_date_idx", "report_date"),
        Index("change_in_dividend_accruals_account_symbol_idx", "account_id", "symbol"),
        # A3 amendment #2 (2026-05-25): extended natural key with
        # (report_date, action_id, code). Real IBKR data emits multiple accrual
        # lifecycle events (Posted/Reversal) for the same dividend payment that
        # share (account, conid, ex_date, pay_date, accrual_date) and only
        # differ by report_date + action_id + code. See Alembic Rev 4
        # (phase25_accruals_code_amendment).
        UniqueConstraint(
            "account_id", "conid", "ex_date", "pay_date", "accrual_date",
            "report_date", "action_id", "code",
            name="change_in_dividend_accruals_natural_key",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    flex_import_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("flex_imports.id", ondelete="SET NULL"), nullable=True
    )
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    conid: Mapped[str | None] = mapped_column(String, nullable=True)
    isin: Mapped[str | None] = mapped_column(String, nullable=True)
    issuer_country: Mapped[str | None] = mapped_column(String, nullable=True)
    currency: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'USD'")
    )
    ex_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    pay_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    accrual_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    gross_rate_per_share: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    gross_amount_usd: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    tax_usd: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    fee_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    net_amount_usd: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    action_id: Mapped[str | None] = mapped_column(String, nullable=True)
    asset_category: Mapped[str | None] = mapped_column(String, nullable=True)
    sub_category: Mapped[str | None] = mapped_column(String, nullable=True)
    level_of_detail: Mapped[str | None] = mapped_column(String, nullable=True)
    code: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("''")
    )
    raw_attrs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class OpenDividendAccrual(Base):
    __tablename__ = "open_dividend_accruals"
    __table_args__ = (
        Index("open_dividend_accruals_report_date_idx", "report_date"),
        Index("open_dividend_accruals_account_symbol_idx", "account_id", "symbol"),
        # A3 amendment #2 preemptive mirror (2026-05-25): extended natural key
        # with (action_id, code). The 2025 fixture has only 1 open accrual row
        # so no collision is observed, but the same IBKR Po/Re lifecycle
        # semantics apply — fix the class to avoid future regression.
        UniqueConstraint(
            "account_id", "conid", "ex_date", "pay_date", "report_date",
            "action_id", "code",
            name="open_dividend_accruals_natural_key",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    flex_import_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("flex_imports.id", ondelete="SET NULL"), nullable=True
    )
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    conid: Mapped[str | None] = mapped_column(String, nullable=True)
    isin: Mapped[str | None] = mapped_column(String, nullable=True)
    issuer_country: Mapped[str | None] = mapped_column(String, nullable=True)
    currency: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'USD'")
    )
    ex_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    pay_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    gross_rate_per_share: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    gross_amount_usd: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    tax_usd: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    fee_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    net_amount_usd: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    action_id: Mapped[str | None] = mapped_column(String, nullable=True)
    asset_category: Mapped[str | None] = mapped_column(String, nullable=True)
    sub_category: Mapped[str | None] = mapped_column(String, nullable=True)
    code: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("''")
    )
    raw_attrs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
