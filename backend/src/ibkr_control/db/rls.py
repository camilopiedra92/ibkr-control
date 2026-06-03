"""Single source of truth for RLS: which tables are org-scoped.

Reused by the baseline migration (to create policies) and by tests (to assert
coverage). Policy SQL builders are added in a later task; this is the table list.
"""

# Tenant tables: organization_id NOT NULL + (later) standard single-org RLS policy.
ORG_SCOPED_TABLES = [
    "accounts",
    "parties",
    "participations",
    "flex_credentials",
    "counterparties",
    "flex_imports",
    "flex_import_accounts",
    "trades",
    "closed_lots",
    "open_position_lots",
    "transfers",
    "cash_transactions",
    "change_in_dividend_accruals",
    "open_dividend_accruals",
    "ingest_log",
]

APP_ROLE = "app_rls"
