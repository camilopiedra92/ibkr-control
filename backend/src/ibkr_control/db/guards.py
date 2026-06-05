"""Fail-closed startup guard: the app must connect with a role that is SUBJECT
to RLS. A superuser or a role with rolbypassrls ignores every org_isolation
policy — booting under such a role silently disables tenant isolation. We
assert at startup and refuse to serve otherwise.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def assert_runtime_role_enforces_rls(engine: AsyncEngine) -> None:
    """Raise RuntimeError unless the connecting role is subject to RLS.

    Checks both vectors that bypass RLS in Postgres:
      * superuser  — bypasses RLS unconditionally.
      * rolbypassrls — the per-role BYPASSRLS attribute.
    """
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT current_user AS role, "
                    "current_setting('is_superuser')::bool AS is_su, "
                    "COALESCE((SELECT rolbypassrls FROM pg_roles "
                    "WHERE rolname = current_user), false) AS bypass"
                )
            )
        ).one()
    if row.is_su or row.bypass:
        raise RuntimeError(
            f"Refusing to start: DB role '{row.role}' can bypass RLS "
            f"(is_superuser={row.is_su}, rolbypassrls={row.bypass}). The app must "
            f"connect as the non-bypass app_rls role so tenant isolation is "
            f"enforced. Point DATABASE_URL at app_rls (see compose: the backend "
            f"service uses app_rls; migrations run in the separate migrate service)."
        )
