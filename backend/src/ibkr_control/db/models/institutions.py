"""Institutions = catálogo global de brokers/proveedores (control plane, sin RLS).

Mismo plano que trm_days (dato de sistema, una sola verdad): el catálogo de
instituciones no es de ningún tenant. Seeded por migración — no es input de
usuario. W1, spec 2026-06-10 T1-D3.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, text
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class Institution(Base):
    __tablename__ = "institutions"
    __table_args__ = (
        {
            "comment": (
                "Control plane (global, sin RLS, como trm_days): catálogo de "
                "instituciones. Seeded por migración, no input de usuario."
            )
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
