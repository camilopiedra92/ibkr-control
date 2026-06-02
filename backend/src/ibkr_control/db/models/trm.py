"""TRM Socrata DIAN — un row por día (con expansión de vigencia)."""

from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import BigInteger, Date, DateTime, Index, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class TrmDay(Base):
    __tablename__ = "trm_days"
    __table_args__ = (
        Index(None, "date"),
        {"comment": "1 row por día calendario, expandido desde vigencia_desde..vigencia_hasta"},
    )

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    value_cop: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    vigencia_desde: Mapped[date] = mapped_column(Date, nullable=False)
    vigencia_hasta: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'dian_socrata_ceyp_9c7c'")
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class TrmImport(Base):
    __tablename__ = "trm_imports"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    date_range_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_range_to: Mapped[date] = mapped_column(Date, nullable=False)
    n_rows_api: Mapped[int] = mapped_column(Integer, nullable=False)
    n_days_expanded: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
