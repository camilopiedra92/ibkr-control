"""phase26 flex hardening: poison-pill + per-user xml_hash unique

Revision ID: 2b0b2863c6e9
Revises: a5199ec783c6
Create Date: 2026-05-25 19:20:53.115443

Phase 2.6 R2 + R6 schema changes:
- Drop existing CHECK status IN ('ok', 'failed'); create new CHECK
  status IN ('ok', 'poison'). 'failed' was never written (verified) so
  no data migration needed.
- Add server_default 'ok' to status (so poison capture INSERT-with-defaults
  doesn't fail on missing value).
- Narrow status to VARCHAR(20) to match the closed enum.
- Add poison_reason column (nullable string) capturing str(exc).
- Drop UNIQUE(xml_hash) global; create UNIQUE(user_id, xml_hash) — fixes
  latent multi-user collision bug.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2b0b2863c6e9'
down_revision: Union[str, Sequence[str], None] = 'a5199ec783c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. status column: drop old CHECK, narrow type, add server_default
    op.drop_constraint("ck_flex_imports_status", "flex_imports", type_="check")
    op.alter_column(
        "flex_imports",
        "status",
        existing_type=sa.String(),
        type_=sa.String(20),
        nullable=False,
        server_default=sa.text("'ok'"),
    )
    op.create_check_constraint(
        "ck_flex_imports_status",
        "flex_imports",
        "status IN ('ok', 'poison')",
    )

    # 2. poison_reason column
    op.add_column(
        "flex_imports",
        sa.Column("poison_reason", sa.String(), nullable=True),
    )

    # 3. UNIQUE(xml_hash) → UNIQUE(user_id, xml_hash)
    op.drop_constraint("flex_imports_xml_hash_key", "flex_imports", type_="unique")
    op.create_unique_constraint(
        "flex_imports_user_xml_hash_key",
        "flex_imports",
        ["user_id", "xml_hash"],
    )


def downgrade() -> None:
    # Reverse order of upgrade
    op.drop_constraint(
        "flex_imports_user_xml_hash_key", "flex_imports", type_="unique"
    )
    op.create_unique_constraint(
        "flex_imports_xml_hash_key", "flex_imports", ["xml_hash"]
    )

    op.drop_column("flex_imports", "poison_reason")

    op.drop_constraint("ck_flex_imports_status", "flex_imports", type_="check")
    op.alter_column(
        "flex_imports",
        "status",
        existing_type=sa.String(20),
        type_=sa.String(),
        nullable=False,
        server_default=None,
    )
    op.create_check_constraint(
        "ck_flex_imports_status",
        "flex_imports",
        "status IN ('ok', 'failed')",
    )
