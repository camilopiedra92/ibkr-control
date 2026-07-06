"""Membership = user belongs to an organization with a role."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    PrimaryKeyConstraint,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "organization_id"),
        CheckConstraint("role IN ('owner', 'admin', 'member')", name="role"),
        {
            "comment": "User<->org con rol. Identidad; sin org-RLS (se lee para resolver contexto).",
            "info": {
                "rls_exempt": (
                    "load-bearing: memberships se lee SIN contexto org para resolver "
                    "authz (list_grants grants.py:55-57, authz_grant_party_ids rls.py:249). "
                    "RLS acá haría default-deny del propio resolver. Régimen revisado en SP4."
                )
            },
        },
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
