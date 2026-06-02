"""Participacion de un user en un account (M:N temporal, con valid_from/valid_to)."""

from datetime import date
from decimal import Decimal
from sqlalchemy import BigInteger, Numeric, Date, ForeignKey, CheckConstraint, PrimaryKeyConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class Participation(Base):
    __tablename__ = "participations"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "account_id", "valid_from"),
        CheckConstraint("pct >= 0 AND pct <= 1", name="ck_participations_pct_range"),
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from", name="ck_participations_valid_range"
        ),
    )

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id", ondelete="CASCADE")
    )
    pct: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
