"""add asset_class to closed_lots and open_position_lots

Revision ID: 969491a8250c
Revises: cbeaac94933d
Create Date: 2026-06-02 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "969491a8250c"
down_revision: Union[str, Sequence[str], None] = "cbeaac94933d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("closed_lots", sa.Column("asset_class", sa.String(), nullable=False))
    op.add_column("open_position_lots", sa.Column("asset_class", sa.String(), nullable=False))


def downgrade() -> None:
    op.drop_column("open_position_lots", "asset_class")
    op.drop_column("closed_lots", "asset_class")
