"""phase25 idempotent constraints

Revision ID: 256faf0dfa89
Revises: 728ea27254b5
Create Date: 2026-05-25 14:27:52.133277

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "256faf0dfa89"
down_revision: Union[str, Sequence[str], None] = "728ea27254b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Requires DB wipe via backend/scripts/wipe_flex_data.py before running.

    Promotes transaction_id to NOT NULL + UNIQUE on closed_lots/cash_transactions/
    transfers, adds composite UNIQUE on snapshot tables, and promotes xml_bytes
    to NOT NULL. All operations only valid because tables were truncated.
    """
    # 1. Promote transaction_id NOT NULL + UNIQUE
    op.alter_column("closed_lots", "transaction_id", nullable=False)
    op.create_unique_constraint("closed_lots_transaction_id_key", "closed_lots", ["transaction_id"])
    op.alter_column("cash_transactions", "transaction_id", nullable=False)
    op.create_unique_constraint(
        "cash_transactions_transaction_id_key", "cash_transactions", ["transaction_id"]
    )
    op.alter_column("transfers", "transaction_id", nullable=False)
    op.create_unique_constraint("transfers_transaction_id_key", "transfers", ["transaction_id"])

    # 2. UNIQUE constraints on snapshot tables (natural keys per spec A3)
    op.create_unique_constraint(
        "open_position_lots_natural_key",
        "open_position_lots",
        ["account_id", "symbol", "open_date", "snapshot_date"],
    )
    op.create_unique_constraint(
        "change_in_dividend_accruals_natural_key",
        "change_in_dividend_accruals",
        ["account_id", "conid", "ex_date", "pay_date", "accrual_date"],
    )
    op.create_unique_constraint(
        "open_dividend_accruals_natural_key",
        "open_dividend_accruals",
        ["account_id", "conid", "ex_date", "pay_date", "report_date"],
    )

    # 3. Promote xml_bytes NOT NULL
    op.alter_column("flex_imports", "xml_bytes", nullable=False)


def downgrade() -> None:
    op.alter_column("flex_imports", "xml_bytes", nullable=True)
    op.drop_constraint(
        "open_dividend_accruals_natural_key", "open_dividend_accruals", type_="unique"
    )
    op.drop_constraint(
        "change_in_dividend_accruals_natural_key", "change_in_dividend_accruals", type_="unique"
    )
    op.drop_constraint("open_position_lots_natural_key", "open_position_lots", type_="unique")
    op.drop_constraint("transfers_transaction_id_key", "transfers", type_="unique")
    op.alter_column("transfers", "transaction_id", nullable=True)
    op.drop_constraint("cash_transactions_transaction_id_key", "cash_transactions", type_="unique")
    op.alter_column("cash_transactions", "transaction_id", nullable=True)
    op.drop_constraint("closed_lots_transaction_id_key", "closed_lots", type_="unique")
    op.alter_column("closed_lots", "transaction_id", nullable=True)
