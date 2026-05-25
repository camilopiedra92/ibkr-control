"""phase25 accruals code amendment

Revision ID: b47ebffe065b
Revises: a5d5e36fb471
Create Date: 2026-05-25 15:04:55.966416

Extend natural keys on both change_in_dividend_accruals and
open_dividend_accruals (A3 amendment #2 — see
docs/specs/2026-05-25-flex-persister-idempotent-design.md).

Real IBKR data emits multiple accrual lifecycle events (Posted and Reversal,
indicated by the `code` attribute: "Po" / "Re") for the same dividend payment.
They share (account, conid, ex_date, pay_date, accrual_date) and differ only
by (report_date, action_id, code). Without these in the natural key,
ON CONFLICT DO UPDATE raises asyncpg.exceptions.CardinalityViolationError
("command cannot affect row a second time") on the second-and-onwards rows.

Concrete example from ACTIVITY_2025_sanitized.xml fixture: ASML ex_date=
2025-02-11 + pay_date=2025-02-19 + accrual_date=2025-02-10 has 3 rows: a Po
on report_date=2025-02-11, a Po on report_date=2025-02-13, and a Re on
report_date=2025-02-13 (the reversal of the first Po).

Open accruals get the same column + extended key preemptively. The 2025
fixture only has 1 open accrual row so no collision is observed there, but
the same IBKR lifecycle semantics apply — fix the class to avoid future
regression once a multi-row case appears.

`code` is promoted from raw_attrs to a first-class column on both models.
NOT NULL with server_default='' so empty tables (post Rev2 wipe + Rev3 add)
backfill safely. At persister write time, the parser normalizes any missing
attribute to '' via `code=row.get("code") or ""`, so we never insert NULL.
Avoids PG15-only NULLS NOT DISTINCT syntax — keeps the schema PG14/15/16
compatible.

Pre-conditions: applies AFTER Rev3 (a5d5e36fb471). At canonical apply time,
both accrual tables are still empty post-Task5 wipe, so the NOT NULL column
add is trivial. server_default handles defensive backfill if applied on a
dev DB that has been re-populated.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b47ebffe065b'
down_revision: Union[str, Sequence[str], None] = 'a5d5e36fb471'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add `code` column to both accrual tables. server_default='' is the
    #    same sentinel the parser produces from missing XML attrs
    #    (`row.get("code") or ""`) so existing-row backfill matches new-row
    #    behavior.
    op.add_column(
        'change_in_dividend_accruals',
        sa.Column('code', sa.String(), nullable=False, server_default=sa.text("''")),
    )
    op.add_column(
        'open_dividend_accruals',
        sa.Column('code', sa.String(), nullable=False, server_default=sa.text("''")),
    )

    # 2. Drop old UNIQUE constraints (created by Rev2).
    op.drop_constraint(
        'change_in_dividend_accruals_natural_key',
        'change_in_dividend_accruals',
        type_='unique',
    )
    op.drop_constraint(
        'open_dividend_accruals_natural_key',
        'open_dividend_accruals',
        type_='unique',
    )

    # 3. Recreate with extended natural keys.
    op.create_unique_constraint(
        'change_in_dividend_accruals_natural_key',
        'change_in_dividend_accruals',
        ['account_id', 'conid', 'ex_date', 'pay_date', 'accrual_date',
         'report_date', 'action_id', 'code'],
    )
    op.create_unique_constraint(
        'open_dividend_accruals_natural_key',
        'open_dividend_accruals',
        ['account_id', 'conid', 'ex_date', 'pay_date', 'report_date',
         'action_id', 'code'],
    )


def downgrade() -> None:
    op.drop_constraint(
        'open_dividend_accruals_natural_key',
        'open_dividend_accruals',
        type_='unique',
    )
    op.drop_constraint(
        'change_in_dividend_accruals_natural_key',
        'change_in_dividend_accruals',
        type_='unique',
    )
    op.create_unique_constraint(
        'open_dividend_accruals_natural_key',
        'open_dividend_accruals',
        ['account_id', 'conid', 'ex_date', 'pay_date', 'report_date'],
    )
    op.create_unique_constraint(
        'change_in_dividend_accruals_natural_key',
        'change_in_dividend_accruals',
        ['account_id', 'conid', 'ex_date', 'pay_date', 'accrual_date'],
    )
    op.drop_column('open_dividend_accruals', 'code')
    op.drop_column('change_in_dividend_accruals', 'code')
