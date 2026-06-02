"""Counterparty externo (broker no-IBKR) referenciado en <Transfer> tags.

Ej. Shareworks/Solium/Morgan Stanley StockPlan (external_id 'CS-YYMMDD-NN').
Espeja a Account: global single-user, sin user_id. El particionado per-user
multi-user aplica a accounts + counterparties juntas (item futuro)."""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, text
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class Counterparty(Base):
    __tablename__ = "counterparties"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    source_label: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
