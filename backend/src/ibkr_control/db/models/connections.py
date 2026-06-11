"""Connection = vínculo org↔institución con credenciales y estado de sync (W1+W4).

Patrón Plaid Item: `connections` es org-scoped (RLS) y genérica; el detalle
provider-specific vive en una tabla 1:1 tipada por provider (T1-D1) con
integridad de subtipo enforced en SQL (T1-D2): UNIQUE(id, provider_type) en el
padre + FK compuesto (connection_id, provider_type) + CHECK del literal en la
detail — imposible colgar una detail ibkr_flex de una connection de otro
provider. Las transiciones de `status` pasan SOLO por
ibkr_control.ingest.connection_state (T1-D5) — nunca UPDATE directo.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base

PROVIDER_IBKR_FLEX = "ibkr_flex"
# DB-side source of truth (CHECK abajo). El Literal API-side
# (api/_schemas.py::ConnectionStatus) debe mantenerse en lockstep.
CONNECTION_STATUSES = ("active", "degraded", "reauth_required", "disabled")


class Connection(Base):
    __tablename__ = "connections"
    __table_args__ = (
        CheckConstraint("provider_type IN ('ibkr_flex')", name="provider_type"),
        CheckConstraint(
            "status IN ('active', 'degraded', 'reauth_required', 'disabled')",
            name="status",
        ),
        CheckConstraint(
            "last_sync_status IS NULL OR last_sync_status IN ('ok', 'failed')",
            name="last_sync_status",
        ),
        # Ancla del FK compuesto de subtipo (T1-D2).
        UniqueConstraint("id", "provider_type", name="uq_connections_id_provider_type"),
        Index(None, "organization_id"),
        # FK RESTRICT joineado en cada lectura de connections (Task 5 serializa
        # Institution.code) — regla sp1-db-hardening: todo FK usado en
        # joins/deletes lleva índice.
        Index(None, "institution_id"),
        {
            "comment": (
                "Org-scoped (RLS). Vínculo org<->institución (patrón Plaid Item). "
                "Config provider-specific en la detail 1:1 (connection_ibkr_flex). "
                "status SOLO vía ingest/connection_state.py (W4)."
            )
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    institution_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("institutions.id", ondelete="RESTRICT"), nullable=False
    )
    provider_type: Mapped[str] = mapped_column(String, nullable=False, default=PROVIDER_IBKR_FLEX)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    # default Python + server_default juntos: con expire_on_commit=False, un
    # server_default solo deja el atributo None en memoria post-commit
    # (lección Phase 2.8, DataAccessGrant.role).
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="active", server_default=text("'active'")
    )
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_status: Mapped[str | None] = mapped_column(String, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class ConnectionIbkrFlex(Base):
    __tablename__ = "connection_ibkr_flex"
    __table_args__ = (
        CheckConstraint("provider_type = 'ibkr_flex'", name="provider_type"),
        ForeignKeyConstraint(
            ["connection_id", "provider_type"],
            ["connections.id", "connections.provider_type"],
            ondelete="CASCADE",
        ),
        Index(None, "organization_id"),
        {
            "comment": (
                "Detail 1:1 tipada del provider ibkr_flex (T1-D1, cero JSONB). "
                "Org-scoped (RLS). Subtipo enforced por FK compuesto + CHECK (T1-D2)."
            )
        },
    )

    connection_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    provider_type: Mapped[str] = mapped_column(
        String, nullable=False, default=PROVIDER_IBKR_FLEX, server_default=text("'ibkr_flex'")
    )
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    token_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    query_id: Mapped[str] = mapped_column(String, nullable=False)
    last_rotated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
