from datetime import datetime
from decimal import Decimal
from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column
from ibkr_control.db.base import Base


class UserSettings(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    marginal_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, server_default="0.3900"
    )
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="America/Bogota"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
