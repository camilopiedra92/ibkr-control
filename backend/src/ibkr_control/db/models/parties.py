"""Party = fiscal/legal person (taxpayer). Owns accounts. Separate from User (login)."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class Party(Base):
    __tablename__ = "parties"
    __table_args__ = (
        Index(None, "organization_id"),
        {
            "comment": (
                "Persona fiscal (contribuyente). Duena de cuentas via participations. "
                "Separada de User: puede no tener login (conyuge, cliente del estudio). "
                "Org-scoped (organization_id, RLS)."
            )
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    tax_id: Mapped[str | None] = mapped_column(String, nullable=True)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
