"""Participacion de un Party en un Account (M:N temporal SCD-2)."""

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    PrimaryKeyConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class Participation(Base):
    __tablename__ = "participations"
    __table_args__ = (
        PrimaryKeyConstraint("party_id", "account_id", "valid_from"),
        CheckConstraint("pct >= 0 AND pct <= 1", name="pct_range"),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="valid_range"),
        Index(None, "organization_id"),
        {"comment": "Propiedad fiscal: Party posee Account con pct (SCD-2). Org-scoped."},
    )

    party_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("parties.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    pct: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
