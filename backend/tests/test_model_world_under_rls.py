"""Guard: el model world (db_session/db_engine) corre como app_rls, no owner.

Lockea el flip de PR-C: si alguien revierte db_session/db_engine a owner, estos
tests se ponen rojos. Espeja el boot guard de runtime de PR #7 a nivel fixture.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ibkr_control.db.rls import app_rls_password
from tests.conftest_ephemeral_db import swap_dsn_credentials


async def test_db_session_connects_as_app_rls(db_session):
    role = (await db_session.execute(text("SELECT current_user"))).scalar_one()
    assert role == "app_rls"


async def test_owner_session_connects_as_owner(owner_session):
    role = (await owner_session.execute(text("SELECT current_user"))).scalar_one()
    assert role != "app_rls"  # el OWNER del contenedor (superuser), bypass RLS


async def test_app_rls_is_fail_closed_without_context(test_db):
    # Sin contexto, una query org-scoped como app_rls ve 0 filas (default-deny).
    app_dsn = swap_dsn_credentials(test_db, "app_rls", app_rls_password())
    engine = create_async_engine(app_dsn)
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with sm() as s:
        count = (await s.execute(text("SELECT count(*) FROM accounts"))).scalar_one()
    await engine.dispose()
    assert count == 0
