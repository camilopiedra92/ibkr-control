"""Observability log — tracks every cron/manual/wizard ingest run."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base

_JOB_KIND_VALUES = ("flex", "trm", "manual_refresh", "manual_upload", "setup_initial")
_STATUS_VALUES = ("running", "ok", "failed")
_TRIGGER_VALUES = ("cron", "manual", "wizard")


class IngestLog(Base):
    __tablename__ = "ingest_log"
    __table_args__ = (
        CheckConstraint(
            f"job_kind IN {_JOB_KIND_VALUES}",
            name="job_kind",
        ),
        CheckConstraint(
            f"status IN {_STATUS_VALUES}",
            name="status",
        ),
        CheckConstraint(
            f"trigger IN {_TRIGGER_VALUES}",
            name="trigger",
        ),
        Index("ix_ingest_log_user_id_started_at", "user_id", text("started_at DESC")),
        Index(None, "organization_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    job_kind: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    items_processed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
