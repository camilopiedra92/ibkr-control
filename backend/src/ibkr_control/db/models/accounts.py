"""Cuenta IBKR (Uxxxxxxxx). Una sola fila por broker account."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        Index(None, "organization_id"),
        {
            "comment": (
                "Identidad COMPARTIDA. Una fila por cuenta IBKR; sin user_id a "
                "propósito — la propiedad se modela en participations (la conjunta "
                "es 50/50). ibkr_account_id UNIQUE global es correcto."
            )
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    ibkr_account_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    alias: Mapped[str | None] = mapped_column(String, nullable=True)
    currency: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'USD'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
