"""phase25 closed_lots close_datetime amendment a3 #3 (schema prep)

Revision ID: cae8640be393
Revises: b47ebffe065b
Create Date: 2026-05-25 17:24:34.521969

A3 amendment #3 (2026-05-25): real IBKR data emits multiple <Lot> rows
sharing the same transactionID when a close trade closes fractions of one
open_lot across multiple execution events. Without close_datetime in the
natural key, ON CONFLICT DO NOTHING collapses these distinct events
(verified in fixture 2025 ICSH transactionID=30666937825: 7 close events
collapsed to 1, losing 6 fifoPnlRealized entries).

This revision is schema-prep only:
- Drops old single-column UNIQUE on transaction_id
- Adds close_datetime TIMESTAMP NULLABLE

Backfill of existing closed_lots happens via
backend/scripts/backfill_closed_lots.py (replays xml_bytes from
flex_imports — no user re-upload required thanks to A0).

Revision 7 then promotes close_datetime NOT NULL + adds the new composite
UNIQUE (transaction_id, close_datetime, qty).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'cae8640be393'
down_revision: Union[str, Sequence[str], None] = 'b47ebffe065b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        'closed_lots_transaction_id_key', 'closed_lots', type_='unique'
    )
    op.add_column(
        'closed_lots',
        sa.Column('close_datetime', sa.DateTime(timezone=False), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('closed_lots', 'close_datetime')
    op.create_unique_constraint(
        'closed_lots_transaction_id_key', 'closed_lots', ['transaction_id']
    )
