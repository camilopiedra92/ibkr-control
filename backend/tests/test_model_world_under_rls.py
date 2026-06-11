"""Guard: el model world (db_session/db_engine) corre como app_rls, no owner.

Lockea el flip de PR-C: si alguien revierte db_session/db_engine a owner, estos
tests se ponen rojos. Espeja el boot guard de runtime de PR #7 a nivel fixture.
"""

from sqlalchemy import text


async def test_db_session_connects_as_app_rls(db_session):
    role = (await db_session.execute(text("SELECT current_user"))).scalar_one()
    assert role == "app_rls"


async def test_owner_session_connects_as_owner(owner_session):
    role = (await owner_session.execute(text("SELECT current_user"))).scalar_one()
    assert role != "app_rls"  # el OWNER del contenedor (superuser), bypass RLS
