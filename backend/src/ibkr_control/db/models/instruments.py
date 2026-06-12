"""Securities master: instrumento como entidad de primera clase (W2, T1-D7).

Control plane (global, SIN RLS - como trm_days/institutions): AAPL es AAPL
para todos los tenants. Identidad externa por FILAS en instrument_identifiers
(el conid de IBKR es una fila, no la PK) - un provider futuro agrega filas,
no migra el master. Los hechos referencian el surrogate instruments.id, por lo
que el esquema de identidad puede evolucionar sin tocar hechos (T1-D7).

Escritura: solo el persister (_ensure_instruments), con valores parseados del
XML de IBKR - no input directo de usuario (T1-D9). symbol es last-seen: un
ticker change (FB->META, mismo conid) actualiza symbol y updated_at.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base

IDENTIFIER_TYPES = ("conid", "isin", "cusip", "figi")


class Instrument(Base):
    __tablename__ = "instruments"
    __table_args__ = (
        {
            "comment": (
                "Control plane (global, sin RLS): securities master. Identidad "
                "externa en instrument_identifiers; symbol/atributos last-seen "
                "del XML IBKR. Escrito solo por el persister (T1-D9)."
            )
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    asset_class: Mapped[str] = mapped_column(String, nullable=False)
    currency: Mapped[str | None] = mapped_column(String, nullable=True)
    multiplier: Mapped[Decimal | None] = mapped_column(Numeric(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class InstrumentIdentifier(Base):
    __tablename__ = "instrument_identifiers"
    __table_args__ = (
        CheckConstraint("id_type IN ('conid', 'isin', 'cusip', 'figi')", name="id_type"),
        UniqueConstraint("id_type", "id_value", name="uq_instrument_identifiers_id_type_id_value"),
        Index(None, "instrument_id"),
        {
            "comment": (
                "Identidad externa del instrumento, una fila por (tipo, valor). "
                "Multi-provider day-1 (T1-D7): conid IBKR hoy; isin cuando el "
                "XML lo trae; cusip/figi reservados."
            )
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False
    )
    id_type: Mapped[str] = mapped_column(String, nullable=False)
    id_value: Mapped[str] = mapped_column(String, nullable=False)
