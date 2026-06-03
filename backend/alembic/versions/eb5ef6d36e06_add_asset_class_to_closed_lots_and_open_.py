"""add asset_class to closed_lots and open_position_lots

Revision ID: eb5ef6d36e06
Revises: cbeaac94933d
Create Date: 2026-06-03 02:58:20.885931

PRECONDITION: adds a NOT NULL column without a server_default, so closed_lots
and open_position_lots MUST be empty when this runs (no backfill — asset_class
comes from re-parsing the XML). Rollout is wipe + upgrade + re-import via the
wizard, in that order (see docs/plans/2026-06-02-lot-asset-class.md Task 4).
Running this against populated lot tables will fail loudly — by design.

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
