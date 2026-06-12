"""Cross-org delegated read: a Party shares its fiscal data with a firm/user.

Party-scoped (grantor_party_id). Grantee is exactly one of org|user (exclusive
arc). organization_id = the grantor party's org (the household that granted).
RLS on this table is SPECIAL (grantor-org OR grantee can see it) — added later.
Enforcement of the grant (who may switch into whose org) is SP2.

Vigencia half-open [valid_from, valid_to): valid_to == hoy => inactivo ya;
intervalo vacío legal (revoke same-day, SP2-D8).
"""

from datetime import date, datetime

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class AccessGrant(Base):
    __tablename__ = "access_grants"
    __table_args__ = (
        CheckConstraint(
            "(grantee_organization_id IS NOT NULL) <> (grantee_user_id IS NOT NULL)",
            name="grantee_arc",
        ),
        CheckConstraint("role IN ('read_only')", name="role"),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="valid_range"),
        # D1 sp1-db-hardening: la policy RLS grant_visibility evalúa
        # grantor/grantee en CADA query a esta tabla, y SP2 la pone en el hot
        # path de autorización.
        Index(None, "grantor_party_id"),
        Index(None, "grantee_organization_id"),
        Index(None, "grantee_user_id"),
        Index(None, "organization_id"),
        {"comment": "Grant cross-org party-scoped. RLS especial (grantor-org OR grantee)."},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    grantor_party_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("parties.id", ondelete="CASCADE"), nullable=False
    )
    grantee_organization_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    grantee_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(
        String, nullable=False, default="read_only", server_default=text("'read_only'")
    )
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
