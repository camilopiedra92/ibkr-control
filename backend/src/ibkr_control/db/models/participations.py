"""Participacion de un Party en un Account (M:N temporal SCD-2)."""

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    Date,
    ForeignKey,
    Index,
    Numeric,
    PrimaryKeyConstraint,
)
from sqlalchemy.dialects.postgresql import DATERANGE, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class Participation(Base):
    __tablename__ = "participations"
    __table_args__ = (
        PrimaryKeyConstraint("party_id", "account_id", "valid_from"),
        CheckConstraint("pct >= 0 AND pct <= 1", name="pct_range"),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="valid_range"),
        # IC-3: vigencias no-solapadas por (org, party, account). El rango se
        # tipa como columna generada `validity` (single source of truth desde
        # valid_from/valid_to) — NO se computa inline en el constraint, que
        # Postgres normalizaría y volvería frágil el drift test.
        ExcludeConstraint(
            ("organization_id", "="),
            ("party_id", "="),
            ("account_id", "="),
            ("validity", "&&"),
            using="gist",
            name="participations_no_overlap",
        ),
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
    # Columna generada: Postgres la mantiene desde valid_from/valid_to. Base del
    # EXCLUDE gist (IC-3); consultable para apply_pct en Phase 3.
    validity: Mapped[object] = mapped_column(
        DATERANGE,
        Computed("daterange(valid_from, valid_to, '[)')", persisted=True),
        nullable=False,
    )
