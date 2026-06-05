"""The runtime-role guard refuses any role that can bypass RLS (superuser or
rolbypassrls) and accepts the non-bypass app_rls role the app actually uses.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from ibkr_control.db.guards import assert_runtime_role_enforces_rls
from ibkr_control.db.rls import app_rls_password
from tests.conftest_ephemeral_db import swap_dsn_credentials


async def test_guard_raises_for_owner_superuser(_migrated_app_db):
    engine = create_async_engine(_migrated_app_db)  # owner == bootstrap superuser
    try:
        with pytest.raises(RuntimeError, match="bypass"):
            await assert_runtime_role_enforces_rls(engine)
    finally:
        await engine.dispose()


async def test_guard_passes_for_app_rls(_migrated_app_db):
    app_dsn = swap_dsn_credentials(_migrated_app_db, "app_rls", app_rls_password())
    engine = create_async_engine(app_dsn)
    try:
        await assert_runtime_role_enforces_rls(engine)  # no raise
    finally:
        await engine.dispose()


async def test_guard_raises_for_nonsuperuser_bypassrls(_migrated_app_db):
    """Lock the rolbypassrls arm specifically: a NON-superuser role that carries
    BYPASSRLS must be rejected. is_superuser is false here, so this can only pass
    via the rolbypassrls branch (the other two tests both go through the
    superuser path).
    """
    owner_engine = create_async_engine(_migrated_app_db)
    try:
        # CREATE/DROP ROLE cannot run inside a transaction block -> AUTOCOMMIT.
        async with owner_engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text("DROP ROLE IF EXISTS guard_bypass_test"))
            await conn.execute(
                text(
                    "CREATE ROLE guard_bypass_test LOGIN NOSUPERUSER BYPASSRLS "
                    "PASSWORD 'guard_bypass_pw'"
                )
            )

        bypass_dsn = swap_dsn_credentials(_migrated_app_db, "guard_bypass_test", "guard_bypass_pw")
        bypass_engine = create_async_engine(bypass_dsn)
        try:
            with pytest.raises(RuntimeError, match="bypass"):
                await assert_runtime_role_enforces_rls(bypass_engine)
        finally:
            await bypass_engine.dispose()
    finally:
        async with owner_engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text("DROP ROLE IF EXISTS guard_bypass_test"))
        await owner_engine.dispose()
