"""phase25 closed_lots promote close_datetime not null + unique

Revision ID: a5199ec783c6
Revises: cae8640be393
Create Date: 2026-05-25 17:26:00.575531

A3 amendment #3 part 2: post-backfill (via scripts/backfill_closed_lots.py)
all rows now have close_datetime populated. Promote NOT NULL + add the new
composite UNIQUE (transaction_id, close_datetime, qty).
"""

from typing import Sequence, Union

from alembic import op


revision: str = "a5199ec783c6"
down_revision: Union[str, Sequence[str], None] = "cae8640be393"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("closed_lots", "close_datetime", nullable=False)
    op.create_unique_constraint(
        "closed_lots_natural_key",
        "closed_lots",
        ["transaction_id", "close_datetime", "qty", "fifo_pnl_usd"],
    )


def downgrade() -> None:
    op.drop_constraint("closed_lots_natural_key", "closed_lots", type_="unique")
    op.alter_column("closed_lots", "close_datetime", nullable=True)
