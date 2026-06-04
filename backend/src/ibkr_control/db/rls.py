"""Single source of truth for RLS: which tables are org-scoped.

Reused by the baseline migration (to create policies) and by tests (to assert
coverage). Provides the org-scoped table list plus the policy/role SQL builders,
and ``apply_org_context`` — the GUC WRITER that pairs with the policy READERS
below (``_CURRENT_ORG`` / ``_CURRENT_USER``). Co-located so the SET LOCAL writer
and its NULLIF(...,'')::bigint reader convention live in one cohesive module.

This module imports nothing from the project (only sqlalchemy), so it sits at
the bottom of the dependency graph — the web layer (``api/_context``) and the
ingest layer (``ingest/flex/job``) both import ``apply_org_context`` from here
without inverting the inner→outer direction.
"""

import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def apply_org_context(
    session: AsyncSession, *, org_id: int, user_id: int | None = None
) -> None:
    """SET LOCAL the RLS GUCs for this transaction.

    ``user_id`` is optional: a system/cron job (e.g. flex_job.run) has NO current
    user. We map ``None -> ''`` (empty string), NOT ``'None'``: the RLS policies
    read the user GUC as ``NULLIF(current_setting('app.current_user', true),
    '')::bigint`` (``_CURRENT_USER`` below) — an empty string yields NULL (clean
    default-deny), whereas the literal string ``'None'`` would raise 22P02
    (invalid bigint) on any access_grants query. So a no-user context must set ''
    here.
    """
    # set_config(key, value, is_local=true) == SET LOCAL; parameterized (no injection).
    await session.execute(
        text(
            "SELECT set_config('app.current_org', :o, true), "
            "set_config('app.current_user', :u, true)"
        ).bindparams(o=str(org_id), u="" if user_id is None else str(user_id))
    )


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

# Read a session GUC as bigint. NULLIF(..., '') is load-bearing: current_setting
# with missing_ok=true returns '' (empty string) when the GUC is unset, and
# ''::bigint RAISES (22P02) rather than yielding NULL. Without NULLIF an unset
# context would 500 instead of cleanly matching nothing. With it, an unset
# context yields NULL → `organization_id = NULL` is never true → default-deny,
# no error. Fail-closed AND clean.
_CURRENT_ORG = "NULLIF(current_setting('app.current_org', true), '')::bigint"
_CURRENT_USER = "NULLIF(current_setting('app.current_user', true), '')::bigint"


def standard_policy_sql(table: str) -> list[str]:
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"""CREATE POLICY org_isolation ON {table}
            USING (organization_id = {_CURRENT_ORG})
            WITH CHECK (organization_id = {_CURRENT_ORG})""",
    ]


def access_grants_policy_sql() -> list[str]:
    return [
        "ALTER TABLE access_grants ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE access_grants FORCE ROW LEVEL SECURITY",
        f"""CREATE POLICY grant_visibility ON access_grants
            USING (
              organization_id = {_CURRENT_ORG}
              OR grantee_organization_id = {_CURRENT_ORG}
              OR grantee_user_id = {_CURRENT_USER}
            )
            WITH CHECK (organization_id = {_CURRENT_ORG})""",
    ]


def system_enum_function_sql() -> list[str]:
    """CONTROL-PLANE capability: cross-tenant enumeration of credentialed orgs.

    The Flex cron must answer "which organizations have flex_credentials?" — an
    inherently CROSS-TENANT, system (control-plane) question. As the non-bypass
    ``app_rls`` role under FORCE ROW LEVEL SECURITY with no ``app.current_org``
    set, a plain ``SELECT DISTINCT organization_id FROM flex_credentials``
    default-denies to ZERO rows → the cron would run and fetch nothing, silently
    (the bug this closes).

    Rather than hand ``app_rls`` a broad ``BYPASSRLS`` role/connection (which
    would also leak cross-tenant read/write of EVERYTHING and pull SP4's secrets/
    roles layer forward), we expose exactly ONE narrow, audited cross-tenant
    capability: a ``SECURITY DEFINER`` function.

    Security envelope (all three are load-bearing — do not weaken):
      * ``SECURITY DEFINER`` runs the body as the function OWNER. The owner is the
        migration role (a superuser in our setup), which is exempt from RLS even
        under FORCE — so the function sees all orgs. This is the same elevated-
        privilege assumption the baseline already makes (it creates roles + FORCE
        RLS as that owner).
      * ``SET search_path = pg_catalog, public`` is MANDATORY on SECURITY DEFINER
        functions: it pins name resolution so a caller cannot hijack the search
        path to shadow ``flex_credentials`` (or any builtin) with a malicious
        object and escalate privilege. Never omit it.
      * ``REVOKE EXECUTE ... FROM PUBLIC`` + ``GRANT EXECUTE ... TO app_rls``:
        least privilege — only the app role may call it, nothing more.

    Per-TENANT work stays fully RLS-enforced: the scheduler calls this function
    only to enumerate, then runs ``flex_job.run(organization_id=...)`` as
    ``app_rls`` with ``SET LOCAL app.current_org`` (defense-in-depth intact). When
    SP5/SP7 bring real background workers + a dedicated system role, that role is
    THEIR foundation; this single-purpose function coexists.
    """
    return [
        "CREATE OR REPLACE FUNCTION system_credentialed_org_ids() "
        "RETURNS SETOF bigint LANGUAGE sql STABLE SECURITY DEFINER "
        "SET search_path = pg_catalog, public AS $$ "
        "SELECT DISTINCT organization_id FROM flex_credentials $$",
        "REVOKE EXECUTE ON FUNCTION system_credentialed_org_ids() FROM PUBLIC",
        f"GRANT EXECUTE ON FUNCTION system_credentialed_org_ids() TO {APP_ROLE}",
    ]


def app_rls_password() -> str:
    """The password for the ``app_rls`` login role — single source of truth.

    Read from ``APP_RLS_PASSWORD`` (env), defaulting to the clearly-labeled
    DEV/TEST literal ``"app_rls_pw"``. Prod sets ``APP_RLS_PASSWORD`` to a real
    secret (SP4/deploy). Both the role-creation DDL (``app_role_grants_sql``) and
    the test fixtures that build the connecting ``app_rls`` DSN call this, so the
    role's password and the DSN that connects with it always agree.

    The env is read INSIDE the function (never at module level) so tests that
    ``monkeypatch.setenv`` see the change without a module reload.

    SQL-literal safety: the returned value is interpolated into a single-quoted
    SQL string literal in ``CREATE ROLE ... LOGIN PASSWORD '<value>'``. We
    fail-loud REJECT any value containing a single quote (the only char that
    could break out of the literal), rather than silently escaping — an operator
    setting a password with a ``'`` is almost certainly a mistake, and rejecting
    keeps the DDL provably injection-free. Any other characters are safe inside
    the literal.
    """
    pw = os.environ.get("APP_RLS_PASSWORD", "app_rls_pw")
    if "'" in pw:
        raise ValueError(
            "APP_RLS_PASSWORD must not contain a single quote "
            "(it is embedded in a SQL string literal in the CREATE ROLE DDL)"
        )
    return pw


def app_role_grants_sql() -> list[str]:
    # app_rls: non-superuser, non-owner login role. Migrations run as the owner;
    # the app connects as this so RLS (with FORCE) actually applies. The password
    # comes from app_rls_password() (env APP_RLS_PASSWORD, dev/test default) — no
    # hardcoded credential in source; prod injects a real secret (SP4/deploy).
    return [
        f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{APP_ROLE}') "
        f"THEN CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{app_rls_password()}'; END IF; END $$",
        f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}",
    ]
