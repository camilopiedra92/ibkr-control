from datetime import datetime
from fastapi_users.db import SQLAlchemyBaseUserTable
from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column
from ibkr_control.db.base import Base


class User(SQLAlchemyBaseUserTable[int], Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_ingest_trigger_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
