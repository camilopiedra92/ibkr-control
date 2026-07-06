import pytest
from sqlalchemy import text

# HD-6: compare_metadata de Alembic NO diffea el ondelete de las FK, así que la
# semántica append-only-ledger (SET NULL en flex_import_id/user_id) vs org-wipe
# (CASCADE) vs facts->accounts (RESTRICT) queda desprotegida. Este mapa PINEA cada
# FK; un cambio de ondelete en un modelo que diverja de la DB rompe acá.
# confdeltype: a=NO ACTION, r=RESTRICT, c=CASCADE, n=SET NULL, d=SET DEFAULT.
EXPECTED: dict[str, str] = {
    "fk_access_grants_grantee_organization_id_organizations": "c",
    "fk_access_grants_grantee_user_id_users": "c",
    "fk_access_grants_grantor_party_id_parties": "c",
    "fk_access_grants_organization_id_organizations": "c",
    "fk_accounts_organization_id_organizations": "c",
    "fk_cash_transactions_account_id_accounts": "r",
    "fk_cash_transactions_flex_import_id_flex_imports": "n",
    "fk_cash_transactions_instrument_id_instruments": "r",
    "fk_cash_transactions_organization_id_organizations": "c",
    "fk_change_in_dividend_accruals_account_id_accounts": "r",
    "fk_change_in_dividend_accruals_flex_import_id_flex_imports": "n",
    "fk_change_in_dividend_accruals_instrument_id_instruments": "r",
    "fk_change_in_dividend_accruals_organization_id_organizations": "c",
    "fk_closed_lots_account_id_accounts": "r",
    "fk_closed_lots_flex_import_id_flex_imports": "n",
    "fk_closed_lots_instrument_id_instruments": "r",
    "fk_closed_lots_organization_id_organizations": "c",
    "fk_closed_lots_source_trade_id_trades": "a",
    "fk_connection_ibkr_flex_connection_id_connections": "c",
    "fk_connection_ibkr_flex_organization_id_organizations": "c",
    "fk_connections_institution_id_institutions": "r",
    "fk_connections_organization_id_organizations": "c",
    "fk_counterparties_organization_id_organizations": "c",
    "fk_flex_import_accounts_account_id_accounts": "c",
    "fk_flex_import_accounts_flex_import_id_flex_imports": "c",
    "fk_flex_import_accounts_organization_id_organizations": "c",
    "fk_flex_imports_connection_id_connections": "n",
    "fk_flex_imports_organization_id_organizations": "c",
    "fk_ingest_log_connection_id_connections": "n",
    "fk_ingest_log_organization_id_organizations": "c",
    "fk_instrument_identifiers_instrument_id_instruments": "c",
    "fk_memberships_organization_id_organizations": "c",
    "fk_memberships_user_id_users": "c",
    "fk_open_dividend_accruals_account_id_accounts": "r",
    "fk_open_dividend_accruals_flex_import_id_flex_imports": "n",
    "fk_open_dividend_accruals_instrument_id_instruments": "r",
    "fk_open_dividend_accruals_organization_id_organizations": "c",
    "fk_open_position_lots_account_id_accounts": "r",
    "fk_open_position_lots_flex_import_id_flex_imports": "n",
    "fk_open_position_lots_instrument_id_instruments": "r",
    "fk_open_position_lots_organization_id_organizations": "c",
    "fk_participations_account_id_accounts": "c",
    "fk_participations_organization_id_organizations": "c",
    "fk_participations_party_id_parties": "c",
    "fk_parties_organization_id_organizations": "c",
    "fk_parties_user_id_users": "n",
    "fk_restatement_log_account_id_accounts": "r",
    "fk_restatement_log_flex_import_id_flex_imports": "n",
    "fk_restatement_log_organization_id_organizations": "c",
    "fk_trades_account_id_accounts": "r",
    "fk_trades_flex_import_id_flex_imports": "n",
    "fk_trades_instrument_id_instruments": "r",
    "fk_trades_organization_id_organizations": "c",
    "fk_transfers_dst_account_id_accounts": "r",
    "fk_transfers_dst_counterparty_id_counterparties": "r",
    "fk_transfers_flex_import_id_flex_imports": "n",
    "fk_transfers_instrument_id_instruments": "r",
    "fk_transfers_organization_id_organizations": "c",
    "fk_transfers_src_account_id_accounts": "r",
    "fk_transfers_src_counterparty_id_counterparties": "r",
    "fk_user_settings_user_id_users": "c",
}


@pytest.mark.asyncio
async def test_fk_ondelete_policy_is_pinned(owner_engine):
    q = text(
        "SELECT c.conname, c.confdeltype::text AS confdeltype FROM pg_constraint c "
        "JOIN pg_class t ON t.oid = c.conrelid WHERE c.contype = 'f'"
    )
    async with owner_engine.connect() as conn:
        actual = {r.conname: r.confdeltype for r in (await conn.execute(q)).all()}

    # Ignorar FKs de tablas fuera de Base.metadata (apscheduler_jobs no tiene FKs;
    # defensivo por si el jobstore cambia).
    actual = {k: v for k, v in actual.items() if not k.startswith("apscheduler")}

    assert actual == EXPECTED, (
        "Deriva de política ondelete de FK (append-only-ledger / org-wipe / RESTRICT). "
        "Si el cambio es INTENCIONAL, actualizá EXPECTED; si no, es un bug de semántica "
        f"del ledger.\nFaltan/cambiaron: {set(EXPECTED.items()) ^ set(actual.items())}"
    )
