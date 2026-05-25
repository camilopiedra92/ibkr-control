"""F-account shadow filter at persister boundary.

Per spec section 1: accounts ending in 'F' are IB-UK Limited regulatory
shadow accounts (NAV=0, no fiscal data). The persister must filter them
before any INSERT into accounts/trades/cash_transactions/etc.

NOTE: this test was adapted from the Task 2 plan text to match the actual
shape of the persister API and parser dataclasses in this codebase:
- The persister function is `persist(session, *, parsed, user_id, xml_bytes, source)`
  and it constructs the `FlexImport` row internally — callers do not pass one in.
- The parser dataclasses are `ParsedAccount`, `ParsedTrade`, `ParsedCashTransaction`,
  living in `ibkr_control.ingest.flex._models`.
- `Trade` and `CashTransaction` ORM models live in `ibkr_control.db.models.flex_raw`.
"""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.db.models.accounts import Account
from ibkr_control.db.models.flex_raw import CashTransaction, Trade
from ibkr_control.ingest.flex._models import (
    ParsedAccount,
    ParsedCashTransaction,
    ParsedTrade,
    ParsedXML,
)
from ibkr_control.ingest.flex.persister import persist


def _make_trade(account_id: str, txn_suffix: str) -> ParsedTrade:
    return ParsedTrade(
        transaction_id=f"TXN-{account_id}-{txn_suffix}",
        ibkr_account_id=account_id,
        symbol="AAPL",
        asset_class="STK",
        trade_date=date(2026, 1, 15),
        settle_date=date(2026, 1, 17),
        qty=Decimal("10"),
        price_usd=Decimal("150.00"),
        proceeds_usd=Decimal("-1500.00"),
        commission_usd=Decimal("-1.00"),
        open_close="O",
        buy_sell="BUY",
        raw_attrs={},
    )


def _make_cash_tx(account_id: str, amount: str, description: str) -> ParsedCashTransaction:
    return ParsedCashTransaction(
        transaction_id="TXN-CASH-test-001",
        ibkr_account_id=account_id,
        type="Commissions",
        currency="USD",
        amount_usd=Decimal(amount),
        description=description,
        date=date(2026, 1, 15),
        symbol=None,
    )


@pytest.mark.asyncio
async def test_persister_filters_f_accounts_from_all_tables(
    db_session: AsyncSession, sample_user
):
    """Single ParsedXML with both U99999999 and U99999999F across all entities.

    Only the non-shadow account row + its trade + its cash_tx must persist.
    The F-suffix shadow account must not appear in accounts; its child rows
    must not appear in trades/cash_transactions.
    """
    parsed = ParsedXML(
        anyo=2026,
        period_from=date(2026, 1, 1),
        period_to=date(2026, 12, 31),
        accounts=[
            ParsedAccount(ibkr_account_id="U99999999", currency="USD"),
            ParsedAccount(ibkr_account_id="U99999999F", currency="USD"),
        ],
        trades=[
            _make_trade("U99999999", "main"),
            _make_trade("U99999999F", "shadow"),
        ],
        closed_lots=[],
        open_position_lots=[],
        cash_transactions=[
            _make_cash_tx("U99999999", "-1.00", "Trade fee"),
            _make_cash_tx("U99999999F", "-0.50", "Shadow fee"),
        ],
        transfers=[],
        change_in_dividend_accruals=[],
        open_dividend_accruals=[],
    )

    await persist(
        db_session,
        parsed=parsed,
        user_id=sample_user.id,
        xml_bytes=b"<xml>f-filter-001</xml>",
        source="manual_upload",
    )
    await db_session.commit()

    accts = (await db_session.scalars(select(Account))).all()
    assert {a.ibkr_account_id for a in accts} == {"U99999999"}, (
        f"Only U99999999 must exist; got {[a.ibkr_account_id for a in accts]}"
    )

    trades = (await db_session.scalars(select(Trade))).all()
    assert len(trades) == 1
    assert trades[0].account_id == accts[0].id

    cash = (await db_session.scalars(select(CashTransaction))).all()
    assert len(cash) == 1
    assert cash[0].account_id == accts[0].id


@pytest.mark.asyncio
async def test_persister_handles_xml_with_only_f_accounts(
    db_session: AsyncSession, sample_user
):
    """Degenerate case: XML where every accountId ends in F. Insert nothing, no error.

    The persister must not crash on a KeyError trying to look up an F-account in
    `accounts_map`, and no Account/CashTransaction rows must be created.
    """
    parsed = ParsedXML(
        anyo=2026,
        period_from=date(2026, 1, 1),
        period_to=date(2026, 12, 31),
        accounts=[ParsedAccount(ibkr_account_id="U99999999F", currency="USD")],
        trades=[],
        closed_lots=[],
        open_position_lots=[],
        cash_transactions=[_make_cash_tx("U99999999F", "-0.50", "Shadow fee only")],
        transfers=[],
        change_in_dividend_accruals=[],
        open_dividend_accruals=[],
    )

    await persist(
        db_session,
        parsed=parsed,
        user_id=sample_user.id,
        xml_bytes=b"<xml>f-filter-only-002</xml>",
        source="manual_upload",
    )
    await db_session.commit()

    assert (await db_session.scalar(select(func.count(Account.id)))) == 0
    assert (await db_session.scalar(select(func.count(CashTransaction.id)))) == 0
