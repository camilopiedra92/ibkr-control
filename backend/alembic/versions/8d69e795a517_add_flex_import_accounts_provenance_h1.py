"""add flex_import_accounts provenance (H1)

Revision ID: 8d69e795a517
Revises: eb5ef6d36e06
Create Date: 2026-06-03 13:04:03.588919

Provenance table linking each FlexImport to every account it referenced
(including AccountInformation-only accounts with no facts). Backs the
anti-IDOR scoping of the setup wizard: step2/save validates incoming
account_ids against the accounts that appear in the *current user's* imports
(join via flex_imports.user_id), not the whole shared `accounts` table.

The `apscheduler_jobs` table that `alembic revision --autogenerate` wants to
drop is a runtime table created by APScheduler's SQLAlchemyJobStore at boot;
it is intentionally NOT part of Base.metadata, so this migration leaves it
untouched (same handling as revision eb5ef6d36e06).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "8d69e795a517"
down_revision: Union[str, Sequence[str], None] = "eb5ef6d36e06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "flex_import_accounts",
        sa.Column("flex_import_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_flex_import_accounts_account_id_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["flex_import_id"],
            ["flex_imports.id"],
            name=op.f("fk_flex_import_accounts_flex_import_id_flex_imports"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "flex_import_id", "account_id", name=op.f("pk_flex_import_accounts")
        ),
        comment=(
            "Procedencia cuenta<->import: cada cuenta observada en un FlexImport "
            "(incluidas las AccountInformation-only sin hechos). Hecho de primera "
            "clase, no inferido. El wizard lo usa para scopear la validacion "
            "anti-IDOR de step2/save: un usuario solo reclama participacion en "
            "cuentas que aparecen en SUS imports (join via flex_imports.user_id), "
            "no en toda la tabla compartida accounts. Ver hardening H1."
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("flex_import_accounts")
