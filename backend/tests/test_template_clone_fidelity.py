"""Guard de paridad 1: un clon fresco preserva el contexto de seguridad de prod.

Espeja el boot guard de PR #7: si el clonado dejara de copiar las policies RLS o
el rol app_rls perdiera su no-bypass, estos tests se ponen rojos — no un leak en
prod. Ver docs/specs/2026-06-11-test-infra-worldclass-design.md (Verificacion 3).
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from ibkr_control.db.rls import ORG_SCOPED_TABLES, app_rls_password
from tests.conftest_ephemeral_db import swap_dsn_credentials


async def test_clone_has_force_rls_on_all_org_scoped_tables(test_db):
    """El clon tiene FORCE ROW LEVEL SECURITY en las 17 tablas org-scoped."""
    engine = create_async_engine(test_db)  # test_db = owner DSN del clon
    try:
        async with engine.connect() as conn:
            forced = (
                (
                    await conn.execute(
                        text(
                            "SELECT relname FROM pg_class "
                            "WHERE relname = ANY(:names) AND relforcerowsecurity"
                        ).bindparams(names=list(ORG_SCOPED_TABLES))
                    )
                )
                .scalars()
                .all()
            )
        assert set(forced) == set(ORG_SCOPED_TABLES)
    finally:
        await engine.dispose()


async def test_clone_app_rls_role_is_non_bypass(test_db):
    """app_rls en el clon es login no-superuser y no-BYPASSRLS."""
    app_dsn = swap_dsn_credentials(test_db, "app_rls", app_rls_password())
    engine = create_async_engine(app_dsn)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'app_rls'")
                )
            ).one()
        assert row.rolsuper is False
        assert row.rolbypassrls is False
    finally:
        await engine.dispose()
