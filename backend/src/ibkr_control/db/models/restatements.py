"""Restatement log: mutación material de un hecho ya persistido (W3, T1-D10..D13).

Convierte el riesgo silencioso del DO UPDATE (snapshot tables) y de los sibling
rows de closed_lots en señal auditable org-scoped. Poblado SOLO por el persister
durante el ingest; detection-only (nunca bloquea ni revierte el upsert).
sealed_year = la fila afectada cae en un año con flex_imports.year_status='sealed'
("tu declaración pudo haber cambiado") — máxima severidad en UI.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class RestatementLog(Base):
    __tablename__ = "restatement_log"
    __table_args__ = (
        CheckConstraint("kind IN ('value_update', 'sibling_row')", name="kind"),
        Index(None, "organization_id", text("detected_at DESC")),
        Index(None, "flex_import_id"),
        # SP2-D9: filtro party-scoped del grantee (visible_account_ids) — el
        # access path es (org, account) bajo RLS.
        Index(None, "organization_id", "account_id"),
        {
            "comment": (
                "Org-scoped (RLS). Señal de restatement: IBKR cambió un valor "
                "material de un hecho ya persistido (value_update, snapshot "
                "tables) o emitió un sibling con distinto fifo_pnl (sibling_row, "
                "closed_lots). Detection-only; nunca borra hechos."
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
    table_name: Mapped[str] = mapped_column(String, nullable=False)
    natural_key: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    column_name: Mapped[str] = mapped_column(String, nullable=False)  # '*' para sibling_row
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    sealed_year: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
