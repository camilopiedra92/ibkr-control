"""phase25 idempotent schema

Revision ID: 728ea27254b5
Revises: a4b6d1025bca
Create Date: 2026-05-25 14:17:53.167562

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '728ea27254b5'
down_revision: Union[str, Sequence[str], None] = 'a4b6d1025bca'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CHILD_TABLES = [
    'trades', 'closed_lots', 'cash_transactions', 'transfers',
    'open_position_lots', 'change_in_dividend_accruals', 'open_dividend_accruals',
]


def upgrade() -> None:
    # 1. Drop CASCADE FK + make flex_import_id nullable + recreate as SET NULL
    for table in _CHILD_TABLES:
        op.drop_constraint(f'{table}_flex_import_id_fkey', table, type_='foreignkey')
        op.alter_column(table, 'flex_import_id', nullable=True)
        op.create_foreign_key(
            f'{table}_flex_import_id_fkey', table, 'flex_imports',
            ['flex_import_id'], ['id'], ondelete='SET NULL',
        )

    # 2. Add transaction_id NULLABLE to closed_lots, cash_transactions, transfers
    # (rows existentes quedan con NULL — sin UNIQUE constraint todavia)
    op.add_column('closed_lots', sa.Column('transaction_id', sa.String(), nullable=True))
    op.add_column('cash_transactions', sa.Column('transaction_id', sa.String(), nullable=True))
    op.add_column('transfers', sa.Column('transaction_id', sa.String(), nullable=True))

    # 3. Add xml_bytes NULLABLE (NOT NULL post-wipe in Revision 2)
    op.add_column('flex_imports', sa.Column('xml_bytes', sa.LargeBinary(), nullable=True))

    # 4. Add n_new_* counters NULLABLE
    for col in ['n_new_trades', 'n_new_lots_closed', 'n_new_open_lots',
                'n_new_cash_tx', 'n_new_dividends', 'n_new_transfers']:
        op.add_column('flex_imports', sa.Column(col, sa.Integer(), nullable=True))

    # 5. Rename existing n_* -> n_observed_*
    op.alter_column('flex_imports', 'n_trades', new_column_name='n_observed_trades')
    op.alter_column('flex_imports', 'n_lots_closed', new_column_name='n_observed_lots_closed')
    op.alter_column('flex_imports', 'n_open_lots', new_column_name='n_observed_open_lots')
    op.alter_column('flex_imports', 'n_cash_tx', new_column_name='n_observed_cash_tx')
    op.alter_column('flex_imports', 'n_dividends', new_column_name='n_observed_dividends')
    op.alter_column('flex_imports', 'n_transfers', new_column_name='n_observed_transfers')


def downgrade() -> None:
    # Reverse order
    op.alter_column('flex_imports', 'n_observed_transfers', new_column_name='n_transfers')
    op.alter_column('flex_imports', 'n_observed_dividends', new_column_name='n_dividends')
    op.alter_column('flex_imports', 'n_observed_cash_tx', new_column_name='n_cash_tx')
    op.alter_column('flex_imports', 'n_observed_open_lots', new_column_name='n_open_lots')
    op.alter_column('flex_imports', 'n_observed_lots_closed', new_column_name='n_lots_closed')
    op.alter_column('flex_imports', 'n_observed_trades', new_column_name='n_trades')

    for col in ['n_new_trades', 'n_new_lots_closed', 'n_new_open_lots',
                'n_new_cash_tx', 'n_new_dividends', 'n_new_transfers']:
        op.drop_column('flex_imports', col)

    op.drop_column('flex_imports', 'xml_bytes')

    op.drop_column('closed_lots', 'transaction_id')
    op.drop_column('cash_transactions', 'transaction_id')
    op.drop_column('transfers', 'transaction_id')

    for table in _CHILD_TABLES:
        op.drop_constraint(f'{table}_flex_import_id_fkey', table, type_='foreignkey')
        op.alter_column(table, 'flex_import_id', nullable=False)
        op.create_foreign_key(
            f'{table}_flex_import_id_fkey', table, 'flex_imports',
            ['flex_import_id'], ['id'], ondelete='CASCADE',
        )
