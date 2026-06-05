"""apscheduler_jobs table (owner-created; app_rls is DDL-free)

APScheduler's SQLAlchemyJobStore lazily create_all()s this table on
scheduler.start(). The app connects as the non-bypass app_rls role, which has
USAGE but not CREATE on schema public — so it cannot create the table. We
pre-create it here (run as the owner) so APScheduler's checkfirst sees it and
skips DDL. Schema matches APScheduler 3.x SQLAlchemyJobStore exactly:
  id VARCHAR(191) PRIMARY KEY, next_run_time DOUBLE PRECISION (indexed),
  job_state BYTEA NOT NULL.

IDEMPOTENT on purpose (raw IF NOT EXISTS DDL, not op.create_table which has no
checkfirst): APScheduler's SQLAlchemyJobStore lazily create_all()s this exact
table at runtime, so any volume that ran the PRE-SPLIT app (which chained
alembic INTO the uvicorn CMD and started the scheduler from the same process)
already has the table. An unconditional op.create_table raises
DuplicateTableError on such volumes, taking the migrate step — and therefore the
backend (depends_on: service_completed_successfully) — down. The schema is
APScheduler-owned and frozen by the APScheduler==3.11.* pin, so IF NOT EXISTS
cannot mask meaningful drift (there is no third writer with a different shape).

Not part of Base.metadata (runtime table) — the drift test already ignores it
(tests/test_migrations.py: "apscheduler_jobs" in text). Explicit grant to
app_rls so the running app can read/write job rows.

Revision ID: 7fdaf6528762
Revises: a1f2c3d4e5b6
Create Date: 2026-06-05
"""

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "7fdaf6528762"
down_revision: Union[str, Sequence[str], None] = "a1f2c3d4e5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent on purpose: APScheduler's SQLAlchemyJobStore lazily create_all()s
    # this exact table at runtime, so any volume that ran the pre-split app already
    # has it. The schema is APScheduler-owned and frozen by the APScheduler==3.11.*
    # pin, so IF NOT EXISTS cannot mask meaningful drift (no third writer). This lets
    # the migrate step converge instead of failing DuplicateTableError on such volumes.
    op.execute(
        "CREATE TABLE IF NOT EXISTS apscheduler_jobs ("
        "id VARCHAR(191) NOT NULL PRIMARY KEY, "
        "next_run_time DOUBLE PRECISION, "
        "job_state BYTEA NOT NULL)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_apscheduler_jobs_next_run_time "
        "ON apscheduler_jobs (next_run_time)"
    )
    # Defensive + explicit (idempotent): see baseline ALTER DEFAULT PRIVILEGES; this
    # GRANT is what survives once the migrate-owner is split from that role.
    from ibkr_control.db.rls import APP_ROLE

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON apscheduler_jobs TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_apscheduler_jobs_next_run_time")
    op.execute("DROP TABLE IF EXISTS apscheduler_jobs")
