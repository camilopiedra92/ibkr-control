"""Guard de paridad 2: el default app_rls es fail-closed (default-deny sin contexto).

Siembra una fila como owner (con contexto) y luego la consulta como app_rls SIN
setear app.current_org -> debe devolver 0 filas (NULLIF('','')::bigint = NULL ->
organization_id = NULL nunca es true). Prueba que olvidar el org_context FALLA
ruidoso, no filtra. Ver spec (Verificacion 4) + memoria rls-runtime-vs-test-parity.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ibkr_control.db.models.accounts import Account
from ibkr_control.db.models.organizations import Organization
from ibkr_control.db.rls import app_rls_password
from tests.conftest_ephemeral_db import swap_dsn_credentials


async def test_app_rls_without_context_sees_zero_rows(test_db):
    # Seed: una cuenta como OWNER (set_config defensivo para el WITH CHECK).
    owner_engine = create_async_engine(test_db)
    owner_sm = async_sessionmaker(owner_engine, expire_on_commit=False, class_=AsyncSession)
    async with owner_sm() as s:
        org = Organization(type="personal", name="FailClosed Org")
        s.add(org)
        await s.flush()
        await s.execute(
            text("SELECT set_config('app.current_org', :o, true)").bindparams(o=str(org.id))
        )
        s.add(Account(ibkr_account_id="U00000000", organization_id=org.id, currency="USD"))
        await s.commit()
    await owner_engine.dispose()

    # Query como app_rls SIN contexto -> default-deny -> 0 filas.
    app_dsn = swap_dsn_credentials(test_db, "app_rls", app_rls_password())
    app_engine = create_async_engine(app_dsn)
    app_sm = async_sessionmaker(app_engine, expire_on_commit=False, class_=AsyncSession)
    async with app_sm() as s:
        count = (await s.execute(text("SELECT count(*) FROM accounts"))).scalar_one()
    await app_engine.dispose()

    assert count == 0
