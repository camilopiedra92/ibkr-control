"""Token Flex encriptado + query_id por organization (no por user)."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, LargeBinary, String, text
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class FlexCredentials(Base):
    __tablename__ = "flex_credentials"
    __table_args__ = (
        Index(None, "organization_id"),
        {
            "comment": (
                "Flex token del org (no del user). Org-scoped, RLS. "
                ">1 login IBKR por org permitido."
            )
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    token_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    ytd_query_id: Mapped[str] = mapped_column(String, nullable=False)
    last_rotated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
