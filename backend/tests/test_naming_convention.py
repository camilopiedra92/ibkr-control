"""H1: la naming_convention produce nombres finales determinísticos, sin
doble-prefijo y sin truncado a 63 chars en natural keys compuestas."""

import ibkr_control.db  # noqa: F401 — carga modelos en Base.metadata
from ibkr_control.db.base import Base


def _names(table_name):
    t = Base.metadata.tables[table_name]
    out = set()
    for c in t.constraints:
        if c.name:
            out.add(c.name)
    for ix in t.indexes:
        out.add(ix.name)
    return out


def test_convention_is_registered():
    assert Base.metadata.naming_convention["ck"] == "ck_%(table_name)s_%(constraint_name)s"
    assert Base.metadata.naming_convention["fk"].startswith("fk_")


def test_check_names_no_double_prefix():
    names = _names("transfers")
    assert "ck_transfers_src_arc" in names
    assert "ck_transfers_dst_arc" in names
    assert "ck_transfers_direction" in names
    assert "ck_transfers_ck_transfers_src_arc" not in names


def test_natural_keys_explicit_uq_prefix_no_truncation():
    assert "uq_closed_lots_natural_key" in _names("closed_lots")
    assert "uq_open_position_lots_natural_key" in _names("open_position_lots")
    long_name = "uq_change_in_dividend_accruals_natural_key"
    assert long_name in _names("change_in_dividend_accruals")
    assert len(long_name) <= 63
    assert "uq_open_dividend_accruals_natural_key" in _names("open_dividend_accruals")


def test_fk_follows_convention():
    names = _names("trades")
    assert "fk_trades_account_id_accounts" in names


def test_single_col_unique_follows_convention():
    assert "uq_accounts_ibkr_account_id" in _names("accounts")


def test_index_names_follow_convention():
    assert "ix_trades_account_id_symbol" in _names("trades")
    assert "ix_trades_trade_date" in _names("trades")
    assert "ix_closed_lots_account_id_symbol" in _names("closed_lots")
    assert "ix_trm_days_date" in _names("trm_days")
