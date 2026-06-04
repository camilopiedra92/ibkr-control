"""system_credentialed_org_ids SECURITY DEFINER enum function (H1)

Closes the cron silent-failure hole: ``_run_flex_for_all_orgs`` must enumerate
"which orgs have flex_credentials" — a cross-tenant control-plane read that
default-denies to 0 rows under the non-bypass ``app_rls`` role + FORCE RLS.
Adds one narrow, audited SECURITY DEFINER function (owner = migration superuser,
which bypasses RLS), EXECUTE granted only to ``app_rls``. SQL lives in
``db/rls.py::system_enum_function_sql`` (the RLS SSOT).

Functions are not part of ``Base.metadata`` so this won't appear in autogenerate
/ ``compare_metadata`` drift checks — the function is created/dropped here only.

Revision ID: a1f2c3d4e5b6
Revises: 05943d9efcdb
Create Date: 2026-06-03

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1f2c3d4e5b6"
down_revision: Union[str, Sequence[str], None] = "05943d9efcdb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from ibkr_control.db.rls import system_enum_function_sql

    for stmt in system_enum_function_sql():
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS system_credentialed_org_ids()")
