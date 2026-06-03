"""Tablas crudas del Flex XML: flex_imports + trades + lots + transfers + cash_transactions
+ dividend accruals.

Estas tablas se pueblan tal-cual del XML, sin transformaciones fiscales.
La capa de classification (lot_classifications) vive en Phase 3.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class FlexImport(Base):
    __tablename__ = "flex_imports"
    __table_args__ = (
        CheckConstraint("source IN ('web_service', 'manual_upload')", name="source"),
        CheckConstraint("year_status IN ('rolling', 'sealed')", name="year_status"),
        CheckConstraint("status IN ('ok', 'poison')", name="status"),
        UniqueConstraint("user_id", "xml_hash"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    anyo: Mapped[int] = mapped_column(Integer, nullable=False)
    xml_hash: Mapped[str] = mapped_column(String, nullable=False)
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
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'ok'"))
    poison_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        CheckConstraint("open_close IS NULL OR open_close IN ('O', 'C')", name="open_close"),
        CheckConstraint("buy_sell IN ('BUY', 'SELL')", name="buy_sell"),
        Index(None, "account_id", "symbol"),
        Index(None, "trade_date"),
        {
            "comment": (
                "Account-scoped. Visibilidad vía participations; sin user_id. "
                "transaction_id UNIQUE global correcto — un hecho pertenece a la "
                "cuenta, no al usuario."
            )
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    flex_import_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("flex_imports.id", ondelete="SET NULL"), nullable=True
    )
    transaction_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), nullable=False)
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
        Index(None, "account_id", "symbol"),
        UniqueConstraint(
            "transaction_id",
            "close_datetime",
            "qty",
            "fifo_pnl_usd",
            name="uq_closed_lots_natural_key",
        ),
        {
            "comment": (
                "Account-scoped. Visibilidad vía participations; sin user_id. "
                "Identidad por natural key compuesto (uq_closed_lots_natural_key); "
                "transaction_id NO es único global — múltiples ejecuciones de "
                "cierre lo comparten (amendment A3)."
            )
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    flex_import_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("flex_imports.id", ondelete="SET NULL"), nullable=True
    )
    transaction_id: Mapped[str] = mapped_column(String, nullable=False)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    asset_class: Mapped[str] = mapped_column(String, nullable=False)
    open_date: Mapped[date] = mapped_column(Date, nullable=False)
    close_date: Mapped[date] = mapped_column(Date, nullable=False)
    # A3 amendment #3: per-execution timestamp discriminator (multiple <Lot>
    # rows can share the same transaction_id when a close trade closes
    # fractions of one open_lot across separate execution events).
    close_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
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
        Index(None, "account_id", "symbol"),
        # A3 amendment 2026-05-25: extended natural key with
        # originating_transaction_id (IBKR's per-lot id) because multi-fill
        # orders produce multiple distinct LOT rows for the same
        # (account, symbol, open_date, snapshot_date) tuple. See Alembic
        # Revision 3 (phase25_op_lots_otid_amendment).
        UniqueConstraint(
            "account_id",
            "symbol",
            "open_date",
            "snapshot_date",
            "originating_transaction_id",
            name="uq_open_position_lots_natural_key",
        ),
        {
            "comment": (
                "Account-scoped. Visibilidad vía participations; sin user_id. "
                "Identidad por natural key compuesto "
                "(uq_open_position_lots_natural_key); no hay transaction_id "
                "global, sí originating_transaction_id como discriminador."
            )
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    flex_import_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("flex_imports.id", ondelete="SET NULL"), nullable=True
    )
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    asset_class: Mapped[str] = mapped_column(String, nullable=False)
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
        CheckConstraint("direction IN ('IN', 'OUT')", name="direction"),
        CheckConstraint(
            "(src_account_id IS NOT NULL) <> (src_counterparty_id IS NOT NULL)",
            name="src_arc",
        ),
        CheckConstraint(
            "(dst_account_id IS NOT NULL) <> (dst_counterparty_id IS NOT NULL)",
            name="dst_arc",
        ),
        {
            "comment": (
                "Account-scoped. Visibilidad vía participations; sin user_id. "
                "transaction_id UNIQUE global correcto — un hecho pertenece a la "
                "cuenta, no al usuario."
            )
        },
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
    src_counterparty_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("counterparties.id"), nullable=True
    )
    dst_account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("accounts.id"), nullable=True
    )
    dst_counterparty_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("counterparties.id"), nullable=True
    )
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    transfer_type: Mapped[str] = mapped_column(String, nullable=False)


class CashTransaction(Base):
    __tablename__ = "cash_transactions"
    __table_args__ = (
        Index(None, "date"),
        {
            "comment": (
                "Account-scoped. Visibilidad vía participations; sin user_id. "
                "transaction_id UNIQUE global correcto — un hecho pertenece a la "
                "cuenta, no al usuario."
            )
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    flex_import_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("flex_imports.id", ondelete="SET NULL"), nullable=True
    )
    transaction_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'USD'"))
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    symbol: Mapped[str | None] = mapped_column(String, nullable=True)


class ChangeInDividendAccrual(Base):
    __tablename__ = "change_in_dividend_accruals"
    __table_args__ = (
        Index(None, "report_date"),
        Index(None, "account_id", "symbol"),
        # A3 amendment #2 (2026-05-25): extended natural key with
        # (report_date, action_id, code). Real IBKR data emits multiple accrual
        # lifecycle events (Posted/Reversal) for the same dividend payment that
        # share (account, conid, ex_date, pay_date, accrual_date) and only
        # differ by report_date + action_id + code. See Alembic Rev 4
        # (phase25_accruals_code_amendment).
        UniqueConstraint(
            "account_id",
            "conid",
            "ex_date",
            "pay_date",
            "accrual_date",
            "report_date",
            "action_id",
            "code",
            name="uq_change_in_dividend_accruals_natural_key",
        ),
        {
            "comment": (
                "Account-scoped. Visibilidad vía participations; sin user_id. "
                "Identidad por natural key compuesto "
                "(uq_change_in_dividend_accruals_natural_key); sin transaction_id."
            )
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    flex_import_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("flex_imports.id", ondelete="SET NULL"), nullable=True
    )
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    conid: Mapped[str | None] = mapped_column(String, nullable=True)
    isin: Mapped[str | None] = mapped_column(String, nullable=True)
    issuer_country: Mapped[str | None] = mapped_column(String, nullable=True)
    currency: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'USD'"))
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
    code: Mapped[str] = mapped_column(String, nullable=False, server_default=text("''"))
    raw_attrs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class OpenDividendAccrual(Base):
    __tablename__ = "open_dividend_accruals"
    __table_args__ = (
        Index(None, "report_date"),
        Index(None, "account_id", "symbol"),
        # A3 amendment #2 preemptive mirror (2026-05-25): extended natural key
        # with (action_id, code). The 2025 fixture has only 1 open accrual row
        # so no collision is observed, but the same IBKR Po/Re lifecycle
        # semantics apply — fix the class to avoid future regression.
        UniqueConstraint(
            "account_id",
            "conid",
            "ex_date",
            "pay_date",
            "report_date",
            "action_id",
            "code",
            name="uq_open_dividend_accruals_natural_key",
        ),
        {
            "comment": (
                "Account-scoped. Visibilidad vía participations; sin user_id. "
                "Identidad por natural key compuesto "
                "(uq_open_dividend_accruals_natural_key); sin transaction_id."
            )
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    flex_import_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("flex_imports.id", ondelete="SET NULL"), nullable=True
    )
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    conid: Mapped[str | None] = mapped_column(String, nullable=True)
    isin: Mapped[str | None] = mapped_column(String, nullable=True)
    issuer_country: Mapped[str | None] = mapped_column(String, nullable=True)
    currency: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'USD'"))
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
    code: Mapped[str] = mapped_column(String, nullable=False, server_default=text("''"))
    raw_attrs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
