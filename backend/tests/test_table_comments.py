"""H4: la invariante de identidad compartida queda explícita en los comments
del catálogo (visible con \\d+ y en Base.metadata)."""

import ibkr_control.db  # noqa: F401
from ibkr_control.db.base import Base


def _comment(table_name):
    return Base.metadata.tables[table_name].comment


def test_accounts_comment_marks_shared_identity():
    c = _comment("accounts")
    assert c is not None
    assert "COMPARTIDA" in c
    assert "participations" in c


def test_counterparties_comment_marks_shared_identity():
    assert "compartida" in (_comment("counterparties") or "").lower()


def test_fact_tables_comment_account_scoped():
    for t in (
        "trades",
        "closed_lots",
        "open_position_lots",
        "transfers",
        "cash_transactions",
        "change_in_dividend_accruals",
        "open_dividend_accruals",
    ):
        c = _comment(t)
        assert c is not None and "account-scoped" in c.lower()
        assert "sin user_id" in c
