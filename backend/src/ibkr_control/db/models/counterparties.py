"""Counterparty externo (broker no-IBKR) referenciado en <Transfer> tags.

Ej. Shareworks/Solium/Morgan Stanley StockPlan (external_id 'CS-YYMMDD-NN').
Espeja a Account pero org-scoped: pertenece al org que registró la transferencia."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class Counterparty(Base):
    __tablename__ = "counterparties"
    __table_args__ = (
        # Per-org uniqueness, NOT global: two orgs can independently reference the
        # same external broker peer (e.g. Morgan Stanley StockPlan). A global
        # UNIQUE would let the first org's row block the second's (RLS hides it).
        UniqueConstraint("organization_id", "external_id", name="uq_counterparties_org_external"),
        Index(None, "organization_id"),
        {"comment": "Identidad externa org-scoped (espeja accounts). RLS."},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    source_label: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
