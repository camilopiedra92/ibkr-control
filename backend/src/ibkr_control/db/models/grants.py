"""Delegación de lectura: grantor permite a grantee leer SUS datos (vía las
participations del grantor). Read-only. Separada de participations: poder-leer
≠ poseer (mezclarlas forzaría un pct nullable sin sentido). El contador es el
caso de uso: lee la declaración del owner sin tener participación."""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    PrimaryKeyConstraint,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class DataAccessGrant(Base):
    __tablename__ = "data_access_grants"
    __table_args__ = (
        PrimaryKeyConstraint("grantor_user_id", "grantee_user_id", "valid_from"),
        CheckConstraint("role IN ('read_only')", name="role"),
        CheckConstraint("grantor_user_id <> grantee_user_id", name="no_self"),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="valid_range"),
        {
            "comment": (
                "Delegación de lectura read-only: grantor habilita a grantee a "
                "leer sus datos vía las participations del grantor. Separada de "
                "participations: poder-leer no es poseer."
            )
        },
    )

    grantor_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    grantee_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(
        String, nullable=False, default="read_only", server_default=text("'read_only'")
    )
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
