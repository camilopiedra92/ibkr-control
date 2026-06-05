"""apscheduler_jobs table (owner-created; app_rls is DDL-free)

APScheduler's SQLAlchemyJobStore lazily create_all()s this table on
scheduler.start(). The app connects as the non-bypass app_rls role, which has
USAGE but not CREATE on schema public — so it cannot create the table. We
pre-create it here (run as the owner) so APScheduler's checkfirst sees it and
skips DDL. Schema matches APScheduler 3.x SQLAlchemyJobStore exactly:
  id VARCHAR(191) PRIMARY KEY, next_run_time DOUBLE PRECISION (indexed),
  job_state BYTEA NOT NULL.
Not part of Base.metadata (runtime table) — the drift test already ignores it
(tests/test_migrations.py: "apscheduler_jobs" in text). Explicit grant to
app_rls so the running app can read/write job rows.

Revision ID: 7fdaf6528762
Revises: a1f2c3d4e5b6
Create Date: 2026-06-05
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "7fdaf6528762"
down_revision: Union[str, Sequence[str], None] = "a1f2c3d4e5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "apscheduler_jobs",
        sa.Column("id", sa.Unicode(191), primary_key=True, nullable=False),
        sa.Column("next_run_time", sa.Float(25), nullable=True),
        sa.Column("job_state", sa.LargeBinary(), nullable=False),
    )
    op.create_index("ix_apscheduler_jobs_next_run_time", "apscheduler_jobs", ["next_run_time"])
    from ibkr_control.db.rls import APP_ROLE

    # Defensive + explicit: the baseline's ALTER DEFAULT PRIVILEGES already grants
    # app_rls DML on tables created by the migration owner, but this explicit GRANT
    # is what survives once Task 4 splits the migrate-owner from that role.
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON apscheduler_jobs TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_index("ix_apscheduler_jobs_next_run_time", table_name="apscheduler_jobs")
    op.drop_table("apscheduler_jobs")
