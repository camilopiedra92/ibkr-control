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


def test_counterparties_comment_org_scoped():
    c = _comment("counterparties")
    assert c is not None
    assert len(c) > 0


def test_organizations_comment_tenant_boundary():
    c = _comment("organizations")
    assert c is not None
    assert "tenant" in c.lower() or "boundary" in c.lower() or "personal" in c.lower()


def test_memberships_comment_exists():
    c = _comment("memberships")
    assert c is not None
    assert len(c) > 0


def test_parties_comment_exists():
    c = _comment("parties")
    assert c is not None
    assert "fiscal" in c.lower() or "contribuyente" in c.lower()


def test_access_grants_comment_exists():
    c = _comment("access_grants")
    assert c is not None
    assert len(c) > 0


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
