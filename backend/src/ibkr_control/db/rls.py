"""Single source of truth for RLS: which tables are org-scoped.

Reused by the baseline migration (to create policies) and by tests (to assert
coverage). Provides the org-scoped table list plus the policy/role SQL builders.
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


def standard_policy_sql(table: str) -> list[str]:
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"""CREATE POLICY org_isolation ON {table}
            USING (organization_id = current_setting('app.current_org', true)::bigint)
            WITH CHECK (organization_id = current_setting('app.current_org', true)::bigint)""",
    ]


def access_grants_policy_sql() -> list[str]:
    return [
        "ALTER TABLE access_grants ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE access_grants FORCE ROW LEVEL SECURITY",
        """CREATE POLICY grant_visibility ON access_grants
            USING (
              organization_id = current_setting('app.current_org', true)::bigint
              OR grantee_organization_id = current_setting('app.current_org', true)::bigint
              OR grantee_user_id = current_setting('app.current_user', true)::bigint
            )""",
    ]


def app_role_grants_sql() -> list[str]:
    # app_rls: non-superuser, non-owner login role. Migrations run as the owner;
    # the app connects as this so RLS (with FORCE) actually applies. The password
    # is dev/test only — prod injects a real secret (SP4/deploy).
    return [
        f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{APP_ROLE}') "
        f"THEN CREATE ROLE {APP_ROLE} LOGIN PASSWORD 'app_rls_pw'; END IF; END $$",
        f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}",
    ]
