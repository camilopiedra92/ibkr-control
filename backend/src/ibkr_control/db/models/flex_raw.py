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
        # Dedup is per-ORG: the persister scopes by organization_id. The org is
        # the unit of tenancy and operation — there is no user_id on operational
        # tables (D-CONV-3).
        UniqueConstraint("organization_id", "xml_hash", name="uq_flex_imports_org_xml_hash"),
        Index(None, "organization_id"),
        Index(None, "connection_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    # W1: linaje import/run -> connection. NULL para manual_upload (no hay
    # conexión) y para rows que sobreviven al borrado de su conexión (SET NULL —
    # append-only ledger: el hecho no muere con la credencial).
    connection_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("connections.id", ondelete="SET NULL"), nullable=True
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


class FlexImportAccount(Base):
    __tablename__ = "flex_import_accounts"
    __table_args__ = (
        Index(None, "organization_id"),
        {
            "comment": (
                "Procedencia cuenta<->import: cada cuenta observada en un FlexImport "
                "(incluidas las AccountInformation-only sin hechos). Org-scoped (RLS). "
                "Hecho de primera clase: que cuentas trajo el import de un org. El "
                "aislamiento cross-tenant lo da RLS por organization_id; esta tabla "
                "sirve al wizard para scopear que cuentas reclama un org via sus imports."
            )
        },
    )

    flex_import_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("flex_imports.id", ondelete="CASCADE"),
        primary_key=True,
    )
    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        CheckConstraint("open_close IS NULL OR open_close IN ('O', 'C')", name="open_close"),
        CheckConstraint("buy_sell IN ('BUY', 'SELL')", name="buy_sell"),
        UniqueConstraint("organization_id", "transaction_id", name="uq_trades_org_transaction_id"),
        Index(None, "account_id", "symbol"),
        Index(None, "account_id", "instrument_id"),
        Index(None, "trade_date"),
        Index(None, "flex_import_id"),
        {
            "comment": (
                "Account-scoped. Visibilidad vía participations; sin user_id. "
                "transaction_id único POR TENANT (multi-home, spec 2026-06-10): "
                "la misma cuenta broker puede existir en N orgs, cada org tiene "
                "su copia de los hechos."
            )
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    flex_import_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("flex_imports.id", ondelete="SET NULL"), nullable=True
    )
    transaction_id: Mapped[str] = mapped_column(String, nullable=False)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    # W2 (T1-D8): FK al securities master. NOT NULL fail-loud - CR-1 verificó
    # conid 100% presente en este tag contra los 3 fixtures reales. symbol y
    # asset_class se conservan como fidelidad de fuente; el agrupado canónico
    # (FIFO Phase 3) es por (account_id, instrument_id) - inmune a ticker changes.
    instrument_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("instruments.id", ondelete="RESTRICT"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    asset_class: Mapped[str] = mapped_column(String, nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    settle_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    price_usd: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    proceeds_usd: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    commission_usd: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    open_close: Mapped[str | None] = mapped_column(String, nullable=True)
    buy_sell: Mapped[str] = mapped_column(String, nullable=False)
    raw_attrs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class ClosedLot(Base):
    __tablename__ = "closed_lots"
    __table_args__ = (
        Index(None, "account_id", "symbol"),
        Index(None, "account_id", "instrument_id"),
        Index(None, "flex_import_id"),
        Index(None, "source_trade_id"),
        UniqueConstraint(
            "organization_id",
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
                "transaction_id NO es único ni global ni per-org — múltiples "
                "ejecuciones de cierre lo comparten (amendment A3); la key es "
                "per-tenant (multi-home, spec 2026-06-10)."
            )
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    flex_import_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("flex_imports.id", ondelete="SET NULL"), nullable=True
    )
    transaction_id: Mapped[str] = mapped_column(String, nullable=False)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    # W2 (T1-D8): FK al securities master. NOT NULL fail-loud - CR-1 verificó
    # conid 100% presente en este tag contra los 3 fixtures reales.
    instrument_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("instruments.id", ondelete="RESTRICT"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    asset_class: Mapped[str] = mapped_column(String, nullable=False)
    open_date: Mapped[date] = mapped_column(Date, nullable=False)
    close_date: Mapped[date] = mapped_column(Date, nullable=False)
    # A3 amendment #3: per-execution timestamp discriminator (multiple <Lot>
    # rows can share the same transaction_id when a close trade closes
    # fractions of one open_lot across separate execution events).
    close_datetime: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        comment=(
            "Naive POR DISEÑO (D3 sp1-db-hardening): IBKR emite "
            "'YYYYMMDD;HHMMSS' sin timezone (exchange-local); timestamptz "
            "inventaría una zona. La regla 730d (Art. 300 ET) opera a "
            "granularidad de día sobre close_date."
        ),
    )
    qty: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    cost_basis_usd: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    proceeds_usd: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    fifo_pnl_usd: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    source_trade_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("trades.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class OpenPositionLot(Base):
    __tablename__ = "open_position_lots"
    __table_args__ = (
        Index(None, "account_id", "symbol"),
        Index(None, "account_id", "instrument_id"),
        Index(None, "organization_id"),
        Index(None, "flex_import_id"),
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
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    flex_import_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("flex_imports.id", ondelete="SET NULL"), nullable=True
    )
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    # W2 (T1-D8): FK al securities master. NOT NULL fail-loud - CR-1 verificó
    # conid 100% presente en este tag contra los 3 fixtures reales.
    instrument_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("instruments.id", ondelete="RESTRICT"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    asset_class: Mapped[str] = mapped_column(String, nullable=False)
    open_date: Mapped[date] = mapped_column(Date, nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    cost_basis_usd: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    mark_price_usd: Mapped[Decimal | None] = mapped_column(Numeric(), nullable=True)
    mark_value_usd: Mapped[Decimal | None] = mapped_column(Numeric(), nullable=True)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    originating_transaction_id: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


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
        CheckConstraint(
            "(asset_class = 'CASH') = (instrument_id IS NULL)",
            name="transfer_cash_iff_no_instrument",
        ),
        UniqueConstraint(
            "organization_id", "transaction_id", name="uq_transfers_org_transaction_id"
        ),
        Index(None, "flex_import_id"),
        Index(None, "instrument_id"),
        Index(None, "src_account_id"),
        Index(None, "dst_account_id"),
        Index(None, "src_counterparty_id"),
        Index(None, "dst_counterparty_id"),
        {
            "comment": (
                "Account-scoped. Visibilidad vía participations; sin user_id. "
                "transaction_id único POR TENANT (multi-home, spec 2026-06-10): "
                "la misma cuenta broker puede existir en N orgs, cada org tiene "
                "su copia de los hechos."
            )
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    flex_import_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("flex_imports.id", ondelete="SET NULL"), nullable=True
    )
    transaction_id: Mapped[str] = mapped_column(String, nullable=False)
    transfer_date: Mapped[date] = mapped_column(Date, nullable=False)
    direction: Mapped[str] = mapped_column(String, nullable=False)
    src_account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=True
    )
    src_counterparty_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("counterparties.id", ondelete="RESTRICT"), nullable=True
    )
    dst_account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=True
    )
    dst_counterparty_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("counterparties.id", ondelete="RESTRICT"), nullable=True
    )
    # TL-D1 (spec 2026-06-11, supersede T1-D8/CR-1): los transfers de securities
    # (asset_class != 'CASH') son CREATORS del securities master — el tag trae
    # conid+isin+description 100% en data real (el comentario anterior "los FOP
    # no traen conid" era un error del grep inicial). instrument_id es NULL SOLO
    # para los CASH internos (symbol="--", sin conid): plata, no instrumento.
    # Invariante lockeado por el CHECK bicondicional (TL-D5).
    instrument_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("instruments.id", ondelete="RESTRICT"), nullable=True
    )
    # TL-D4: fidelidad de fuente (simetría con accruals — conid crudo conservado).
    asset_class: Mapped[str] = mapped_column(String, nullable=False)
    conid: Mapped[str | None] = mapped_column(String, nullable=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    transfer_type: Mapped[str] = mapped_column(String, nullable=False)


class CashTransaction(Base):
    __tablename__ = "cash_transactions"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "transaction_id", name="uq_cash_transactions_org_transaction_id"
        ),
        Index(None, "date"),
        Index(None, "instrument_id"),
        Index(None, "flex_import_id"),
        {
            "comment": (
                "Account-scoped. Visibilidad vía participations; sin user_id. "
                "transaction_id único POR TENANT (multi-home, spec 2026-06-10): "
                "la misma cuenta broker puede existir en N orgs, cada org tiene "
                "su copia de los hechos."
            )
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    flex_import_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("flex_imports.id", ondelete="SET NULL"), nullable=True
    )
    transaction_id: Mapped[str] = mapped_column(String, nullable=False)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    # W2 (T1-D8): FK al securities master, NULLABLE por CR-1. La Flex Query 2024
    # ni trae la columna conid en cash; fees/intereses no tienen instrumento;
    # resolver-only (lookup por conid si está, NUNCA crea).
    instrument_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("instruments.id", ondelete="RESTRICT"), nullable=True
    )
    # TL-D4: conid crudo, fidelidad de fuente — un instrument_id NULL es
    # auditable sin re-parsear xml_bytes. Nullable real: la Flex Query 2024 ni
    # trae la columna; fees/intereses no tienen instrumento.
    conid: Mapped[str | None] = mapped_column(String, nullable=True)
    type: Mapped[str] = mapped_column(String, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'USD'"))
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    symbol: Mapped[str | None] = mapped_column(String, nullable=True)
    # IC-1: source data fiscal que el parser ya ve (poblado por Task 2). action_id
    # linkea un dividendo con su Withholding Tax (descuento Art. 254 ET); las
    # fechas de settle/report/ex y issuer_country sostienen tratado + Form 160.
    # raw_attrs preserva el resto de atributos del <CashTransaction> sin perder
    # fidelidad de fuente (idem accruals).
    settle_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    report_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    ex_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    issuer_country: Mapped[str | None] = mapped_column(String, nullable=True)
    action_id: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_attrs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class ChangeInDividendAccrual(Base):
    __tablename__ = "change_in_dividend_accruals"
    __table_args__ = (
        Index(None, "report_date"),
        Index(None, "account_id", "symbol"),
        Index(None, "account_id", "instrument_id"),
        Index(None, "organization_id"),
        Index(None, "flex_import_id"),
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
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    flex_import_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("flex_imports.id", ondelete="SET NULL"), nullable=True
    )
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    # W2 (T1-D8): FK al securities master. NOT NULL fail-loud - CR-1 verificó
    # conid 100% presente en este tag contra los fixtures reales. La columna
    # conid raw se conserva como fidelidad de fuente.
    instrument_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("instruments.id", ondelete="RESTRICT"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    conid: Mapped[str | None] = mapped_column(String, nullable=True)
    isin: Mapped[str | None] = mapped_column(String, nullable=True)
    issuer_country: Mapped[str | None] = mapped_column(String, nullable=True)
    currency: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'USD'"))
    ex_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    pay_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    accrual_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    gross_rate_per_share: Mapped[Decimal | None] = mapped_column(Numeric(), nullable=True)
    gross_amount_usd: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    tax_usd: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    fee_usd: Mapped[Decimal | None] = mapped_column(Numeric(), nullable=True)
    net_amount_usd: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    action_id: Mapped[str | None] = mapped_column(String, nullable=True)
    asset_category: Mapped[str | None] = mapped_column(String, nullable=True)
    sub_category: Mapped[str | None] = mapped_column(String, nullable=True)
    level_of_detail: Mapped[str | None] = mapped_column(String, nullable=True)
    code: Mapped[str] = mapped_column(String, nullable=False, server_default=text("''"))
    raw_attrs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class OpenDividendAccrual(Base):
    __tablename__ = "open_dividend_accruals"
    __table_args__ = (
        Index(None, "report_date"),
        Index(None, "account_id", "symbol"),
        Index(None, "account_id", "instrument_id"),
        Index(None, "organization_id"),
        Index(None, "flex_import_id"),
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
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    flex_import_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("flex_imports.id", ondelete="SET NULL"), nullable=True
    )
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    # W2 (T1-D8): FK al securities master. NOT NULL fail-loud - CR-1 verificó
    # conid 100% presente en este tag contra los fixtures reales. La columna
    # conid raw se conserva como fidelidad de fuente.
    instrument_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("instruments.id", ondelete="RESTRICT"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    conid: Mapped[str | None] = mapped_column(String, nullable=True)
    isin: Mapped[str | None] = mapped_column(String, nullable=True)
    issuer_country: Mapped[str | None] = mapped_column(String, nullable=True)
    currency: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'USD'"))
    ex_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    pay_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    gross_rate_per_share: Mapped[Decimal | None] = mapped_column(Numeric(), nullable=True)
    gross_amount_usd: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    tax_usd: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    fee_usd: Mapped[Decimal | None] = mapped_column(Numeric(), nullable=True)
    net_amount_usd: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    action_id: Mapped[str | None] = mapped_column(String, nullable=True)
    asset_category: Mapped[str | None] = mapped_column(String, nullable=True)
    sub_category: Mapped[str | None] = mapped_column(String, nullable=True)
    code: Mapped[str] = mapped_column(String, nullable=False, server_default=text("''"))
    raw_attrs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
