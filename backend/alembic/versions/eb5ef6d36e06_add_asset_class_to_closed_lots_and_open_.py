"""add asset_class to closed_lots and open_position_lots

Revision ID: eb5ef6d36e06
Revises: cbeaac94933d
Create Date: 2026-06-03 02:58:20.885931

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "eb5ef6d36e06"
down_revision: Union[str, Sequence[str], None] = "cbeaac94933d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # apscheduler_jobs is created at runtime by APScheduler's SQLAlchemyJobStore,
    # not in Base.metadata, so autogenerate emits a spurious drop — removed here.
    op.add_column("closed_lots", sa.Column("asset_class", sa.String(), nullable=False))
    op.add_column("open_position_lots", sa.Column("asset_class", sa.String(), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("open_position_lots", "asset_class")
    op.drop_column("closed_lots", "asset_class")
