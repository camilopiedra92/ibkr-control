"""Cuenta IBKR (Uxxxxxxxx) — multi-home: una fila POR ORG que la conecta."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "ibkr_account_id", name="uq_accounts_org_ibkr_account_id"
        ),
        {
            "comment": (
                "Multi-home (patrón Plaid, spec 2026-06-10): la misma cuenta "
                "broker puede existir en N orgs, una fila por org — universos "
                "aislados, el SaaS no verifica exclusividad de propiedad. "
                "Dentro de un org sigue siendo identidad compartida: sin "
                "user_id, propiedad vía participations (la conjunta es 50/50)."
            )
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    ibkr_account_id: Mapped[str] = mapped_column(String, nullable=False)
    alias: Mapped[str | None] = mapped_column(String, nullable=True)
    currency: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'USD'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
