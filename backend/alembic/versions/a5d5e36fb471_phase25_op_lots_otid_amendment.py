"""phase25 op lots otid amendment

Revision ID: a5d5e36fb471
Revises: 256faf0dfa89
Create Date: 2026-05-25 14:57:31.701438

Extend open_position_lots natural key to include originating_transaction_id
(A3 amendment 2026-05-25 — see docs/specs/2026-05-25-flex-persister-idempotent-design.md).

Real IBKR data has multiple distinct LOT rows for the same
(account, symbol, open_date, snapshot_date) tuple coming from multi-fill orders,
distinguished only by the per-lot originatingTransactionID XML attribute. Without
this column in the natural key, ON CONFLICT DO UPDATE raises
asyncpg.exceptions.CardinalityViolationError ("command cannot affect row a second
time") because the proposed insert batch targets the same conflict-row twice.

Concrete example from ACTIVITY_2025_sanitized.xml fixture: AMD open_date=2025-02-05
has 2 LOT rows — qty=0.8638 from txn 31233843972 and qty=1 from txn 31233844034 —
both with the same snapshot_date=2025-12-31. They are NOT duplicates; they are
distinct partial fills of the same order. 22 out of 115 LOT rows in that fixture
have such collisions.

Pre-conditions: this revision runs AFTER Revision 2 (256faf0dfa89), which already
ran the wipe via backend/scripts/wipe_flex_data.py. open_position_lots is empty
at apply time, so the NOT NULL add is safe. If a future application of this rev
ever encounters existing data with NULL otid, we backfill with '' first (defensive).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a5d5e36fb471"
down_revision: Union[str, Sequence[str], None] = "256faf0dfa89"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add column NULLABLE first so the migration is safe even if the table is
    # somehow not empty at apply time (e.g. running it on a dev DB that has
    # been re-populated after Rev2 wipe).
    op.add_column(
        "open_position_lots",
        sa.Column("originating_transaction_id", sa.String(), nullable=True),
    )

    # Defensive backfill: empty string for any existing rows that lack the
    # attribute. In the canonical flow this is a no-op (Rev2 wipe leaves the
    # table empty), but it makes the migration robust if applied out of order.
    op.execute(
        "UPDATE open_position_lots "
        "SET originating_transaction_id = '' "
        "WHERE originating_transaction_id IS NULL"
    )

    # Promote NOT NULL.
    op.alter_column("open_position_lots", "originating_transaction_id", nullable=False)

    # Swap UNIQUE constraint: drop the old 4-column key from Rev2 and recreate
    # with the same name + the additional discriminator column.
    op.drop_constraint(
        "open_position_lots_natural_key",
        "open_position_lots",
        type_="unique",
    )
    op.create_unique_constraint(
        "open_position_lots_natural_key",
        "open_position_lots",
        ["account_id", "symbol", "open_date", "snapshot_date", "originating_transaction_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "open_position_lots_natural_key",
        "open_position_lots",
        type_="unique",
    )
    op.create_unique_constraint(
        "open_position_lots_natural_key",
        "open_position_lots",
        ["account_id", "symbol", "open_date", "snapshot_date"],
    )
    op.drop_column("open_position_lots", "originating_transaction_id")
