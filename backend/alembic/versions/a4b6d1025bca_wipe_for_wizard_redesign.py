"""wipe for wizard redesign

Revision ID: a4b6d1025bca
Revises: e39428dc5cd5
Create Date: 2026-05-25 01:09:01.177939

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a4b6d1025bca"
down_revision: Union[str, Sequence[str], None] = "e39428dc5cd5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Destructive wipe for wizard redesign — re-ingest from scratch per spec D1.
    # Order matters: dependents first to satisfy FK constraints.
    op.execute("DELETE FROM open_dividend_accruals")
    op.execute("DELETE FROM change_in_dividend_accruals")
    op.execute("DELETE FROM transfer_lots")
    op.execute("DELETE FROM transfers")
    op.execute("DELETE FROM cash_transactions")
    op.execute("DELETE FROM open_position_lots")
    op.execute("DELETE FROM closed_lots")
    op.execute("DELETE FROM trades")
    op.execute("DELETE FROM flex_imports")
    op.execute("DELETE FROM participations")
    op.execute("DELETE FROM accounts")
    op.execute("UPDATE users SET setup_progress = '{}'::jsonb, setup_completed_at = NULL")
    # Preserve: flex_credentials (token + query_id) — user no re-pega Flex Token.
    # Preserve: apscheduler_jobs — scheduler crons stay armed.


def downgrade() -> None:
    # Destructive migration — downgrade is no-op. To recover, re-run wizard.
    pass
