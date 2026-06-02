"""counterparties + exclusive arc on transfers; drop transfer_lots

Revision ID: 1702589703e1
Revises: 2b0b2863c6e9
Create Date: 2026-06-02 17:18:10.089889

Spec docs/specs/2026-06-02-persister-counterparties-cleanup-design.md
- #6: counterparties table + src/dst_counterparty_id FK + exclusive-arc CHECK.
  Reconcilia accounts huerfanos (counterparties externos mal creados como
  Account) moviendolos a counterparties.
- #7: drop transfer_lots (impoblable desde Activity Flex).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1702589703e1'
down_revision: Union[str, Sequence[str], None] = '2b0b2863c6e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. counterparties
    op.create_table(
        "counterparties",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("source_label", sa.String(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("external_id", name="uq_counterparties_external_id"),
    )

    # 2. transfers: columnas counterparty (FK nullable)
    op.add_column("transfers", sa.Column("src_counterparty_id", sa.BigInteger(), nullable=True))
    op.add_column("transfers", sa.Column("dst_counterparty_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_transfers_src_counterparty", "transfers", "counterparties",
        ["src_counterparty_id"], ["id"],
    )
    op.create_foreign_key(
        "fk_transfers_dst_counterparty", "transfers", "counterparties",
        ["dst_counterparty_id"], ["id"],
    )

    # 3. data-migration: reconciliar accounts huerfanos (counterparties externos).
    #    Huerfano = account referenciado por transfers, sin participation y sin
    #    aparecer en ninguna tabla de hechos (trades/lots/cash/accruals).
    conn = op.get_bind()
    orphans = conn.execute(sa.text("""
        SELECT a.id, a.ibkr_account_id FROM accounts a
        WHERE a.id IN (
            SELECT src_account_id FROM transfers WHERE src_account_id IS NOT NULL
            UNION SELECT dst_account_id FROM transfers WHERE dst_account_id IS NOT NULL
        )
        AND a.id NOT IN (SELECT account_id FROM participations)
        AND a.id NOT IN (SELECT account_id FROM trades)
        AND a.id NOT IN (SELECT account_id FROM closed_lots)
        AND a.id NOT IN (SELECT account_id FROM open_position_lots)
        AND a.id NOT IN (SELECT account_id FROM cash_transactions)
        AND a.id NOT IN (SELECT account_id FROM change_in_dividend_accruals)
        AND a.id NOT IN (SELECT account_id FROM open_dividend_accruals)
    """)).fetchall()

    for acct_id, ext_id in orphans:
        cp_id = conn.execute(sa.text("""
            INSERT INTO counterparties (external_id) VALUES (:ext)
            ON CONFLICT (external_id) DO UPDATE SET external_id = EXCLUDED.external_id
            RETURNING id
        """), {"ext": ext_id}).scalar()
        conn.execute(sa.text(
            "UPDATE transfers SET src_counterparty_id=:cp, src_account_id=NULL "
            "WHERE src_account_id=:a"
        ), {"cp": cp_id, "a": acct_id})
        conn.execute(sa.text(
            "UPDATE transfers SET dst_counterparty_id=:cp, dst_account_id=NULL "
            "WHERE dst_account_id=:a"
        ), {"cp": cp_id, "a": acct_id})
        conn.execute(sa.text("DELETE FROM accounts WHERE id=:a"), {"a": acct_id})

    # 4. assert precondicion del exclusive arc (fail-loud antes de crear el CHECK)
    bad = conn.execute(sa.text("""
        SELECT count(*) FROM transfers
        WHERE ((src_account_id IS NOT NULL)::int + (src_counterparty_id IS NOT NULL)::int) <> 1
           OR ((dst_account_id IS NOT NULL)::int + (dst_counterparty_id IS NOT NULL)::int) <> 1
    """)).scalar()
    if bad:
        raise RuntimeError(
            f"{bad} transfers violate exclusive-arc precondition (a side with "
            f"zero or two endpoints); cannot add CHECK. Investigate before migrating."
        )

    # 5. CHECK constraints (exclusive arc, exactly-one por lado)
    op.create_check_constraint(
        "ck_transfers_src_arc", "transfers",
        "(src_account_id IS NOT NULL) <> (src_counterparty_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_transfers_dst_arc", "transfers",
        "(dst_account_id IS NOT NULL) <> (dst_counterparty_id IS NOT NULL)",
    )

    # 6. drop transfer_lots (impoblable, #7)
    op.drop_table("transfer_lots")


def downgrade() -> None:
    # 1. recrear transfer_lots
    op.create_table(
        "transfer_lots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("transfer_id", sa.BigInteger(),
                  sa.ForeignKey("transfers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("original_open_date", sa.Date(), nullable=False),
        sa.Column("qty", sa.Numeric(20, 8), nullable=False),
        sa.Column("cost_basis_usd", sa.Numeric(20, 4), nullable=False),
    )

    # 2. drop CHECK
    op.drop_constraint("ck_transfers_src_arc", "transfers", type_="check")
    op.drop_constraint("ck_transfers_dst_arc", "transfers", type_="check")

    # 3. reverse data-migration: re-crear Account desde counterparties + re-apuntar.
    #    Restaura el estado buggy a proposito (reversibilidad).
    conn = op.get_bind()
    cps = conn.execute(sa.text("SELECT id, external_id FROM counterparties")).fetchall()
    for cp_id, ext_id in cps:
        acct_id = conn.execute(sa.text("""
            INSERT INTO accounts (ibkr_account_id, currency) VALUES (:ext, 'USD')
            ON CONFLICT (ibkr_account_id) DO UPDATE SET ibkr_account_id = EXCLUDED.ibkr_account_id
            RETURNING id
        """), {"ext": ext_id}).scalar()
        conn.execute(sa.text(
            "UPDATE transfers SET src_account_id=:a, src_counterparty_id=NULL "
            "WHERE src_counterparty_id=:cp"
        ), {"a": acct_id, "cp": cp_id})
        conn.execute(sa.text(
            "UPDATE transfers SET dst_account_id=:a, dst_counterparty_id=NULL "
            "WHERE dst_counterparty_id=:cp"
        ), {"a": acct_id, "cp": cp_id})

    # 4. drop columnas counterparty
    op.drop_constraint("fk_transfers_src_counterparty", "transfers", type_="foreignkey")
    op.drop_constraint("fk_transfers_dst_counterparty", "transfers", type_="foreignkey")
    op.drop_column("transfers", "src_counterparty_id")
    op.drop_column("transfers", "dst_counterparty_id")

    # 5. drop counterparties
    op.drop_table("counterparties")
