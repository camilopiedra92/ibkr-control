"""Token Flex encriptado + query_id por user."""
from datetime import datetime
from sqlalchemy import BigInteger, LargeBinary, String, DateTime, ForeignKey, text
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class FlexCredentials(Base):
    __tablename__ = "flex_credentials"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    token_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    ytd_query_id: Mapped[str] = mapped_column(String, nullable=False)
    last_rotated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
