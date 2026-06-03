import os

# Defaults seteados ANTES de cualquier import de ibkr_control.*, porque pytest
# carga conftest.py antes de colectar test modules. Sin esto, test_health.py
# falla en collection time post-Task 5 cuando main.py importe auth -> db.session
# -> get_settings() -> ValidationError por DATABASE_URL/JWT_SECRET faltantes.
# Tests que necesitan DB real (test_db_connection, test_auth) sobreescriben
# via monkeypatch -- setdefault no piso valores ya seteados.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")

import asyncio

import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from ibkr_control.main import create_app
from ibkr_control.db.base import Base
from ibkr_control.db.session import get_async_session

from tests.conftest_ephemeral_db import (  # noqa: F401
    ephemeral_postgres,
    ephemeral_db_url,
    ephemeral_session_factory,
    rls_session_factory,
    build_alembic_config,
    swap_dsn_credentials,
)


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


# Fixtures compartidas por tests que necesitan DB real (test_auth, test_settings,
# y futuros). Viven en conftest.py — no en un test module — porque pytest
# desaconseja `pytest_plugins` en modules: se rompe cuando corren juntos en la
# misma session aunque funcione test-file por separado.
@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as pg:
        yield pg


@pytest.fixture
async def _migrated_app_db(monkeypatch):
    """A fresh, MIGRATED postgres for the endpoint app — own per-test container.

    This is the world-class RLS path: the schema is built via ``alembic upgrade
    head`` (NOT ``Base.metadata.create_all``), so the FORCE'd RLS policies + the
    non-superuser ``app_rls`` login role created by the baseline migration exist.
    The app (``app_with_db``) then connects as ``app_rls``, so every request is
    exercised under RLS instead of as the bypass owner.

    It is its OWN container, fully isolated from ``postgres_container`` (which
    ``db_session`` + the ``sample_*`` model fixtures keep on owner +
    ``create_all``). That isolation is what avoids the ``create_all`` /
    ``alembic`` collision on a shared session-scoped container.

    Yields the OWNER async DSN of the migrated DB. ``app_with_db`` derives the
    ``app_rls`` DSN from it; ``db_engine`` / seeding fixtures connect with it as
    OWNER (the container superuser bypasses RLS, and identity tables have no
    org-RLS anyway) to seed users/orgs/memberships/parties.
    """
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as pg:
        owner_url = pg.get_connection_url()
        monkeypatch.setenv("DATABASE_URL", owner_url)
        monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")
        monkeypatch.setenv("JWT_LIFETIME_SECONDS", "3600")
        monkeypatch.setenv("BACKEND_CORS_ORIGINS", "")

        from ibkr_control.config import get_settings

        get_settings.cache_clear()

        cfg = build_alembic_config()
        # alembic command.upgrade is sync — run in thread to not block event loop.
        await asyncio.to_thread(command.upgrade, cfg, "head")

        yield owner_url


@pytest.fixture
async def app_with_db(_migrated_app_db, monkeypatch):
    """The endpoint app, wired to a MIGRATED DB and connecting as ``app_rls``.

    The app's ``get_async_session`` is overridden to yield sessions on an engine
    that authenticates as the non-bypass ``app_rls`` role. Each request's
    ``org_context`` dependency ``SET LOCAL``s ``app.current_org`` / ``current_user``
    on that session, so RLS scopes every org-scoped query to the request's org.
    """
    owner_url = _migrated_app_db
    app_dsn = swap_dsn_credentials(owner_url, "app_rls", "app_rls_pw")

    engine = create_async_engine(app_dsn)
    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_session():
        async with session_maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_async_session] = override_get_session

    try:
        yield app
    finally:
        await engine.dispose()


@pytest.fixture
async def client(app_with_db):
    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def db_session(postgres_container, monkeypatch):
    """Sesion de DB directa para tests de schema/modelos (sin HTTP layer).

    Crea las tablas via Base.metadata.create_all (misma ruta que app_with_db),
    pero expone la sesion directamente para hacer DML/DDL checks.
    Cada test obtiene una sesion limpia; las tablas se recrean por test.
    """
    url = postgres_container.get_connection_url()
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")

    from ibkr_control.config import get_settings

    get_settings.cache_clear()

    import ibkr_control.db  # noqa: F401 — registra todos los modelos en Base.metadata

    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_maker() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_engine(postgres_container):
    """Engine sharing the testcontainer with db_session; used by tests that need to
    open multiple concurrent sessions (e.g. advisory lock contention) on the
    owner + create_all world.

    Function-scoped (not session-scoped) so it does not outlive per-test DB state.
    Uses the same postgres_container URL as db_session (asyncpg driver included).
    """
    url = postgres_container.get_connection_url()
    engine = create_async_engine(url, echo=False)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def app_owner_engine(_migrated_app_db):
    """OWNER engine on the SAME migrated DB the endpoint app (``app_with_db``) uses.

    Used by the endpoint seeding fixtures (``auth_headers_with_org`` &c.) to
    insert users/orgs/memberships/parties against the very DB ``app_with_db``
    serves requests from. Connects as the container OWNER (superuser → bypasses
    RLS; identity tables have no org-RLS anyway), so the seed is unconstrained.

    Distinct from ``db_engine`` (which stays on ``postgres_container`` with the
    owner/create_all ``db_session`` world). Endpoint seeding targets the migrated
    DB; model-level tests stay on the create_all DB.
    """
    engine = create_async_engine(_migrated_app_db, echo=False)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def app_rls_db_session(_migrated_app_db):
    """A direct session on the migrated app DB, connecting as ``app_rls``.

    For assertions about the role the endpoint app runs under (e.g. it is NOT
    the bypass owner). Same non-superuser role + DB as ``app_with_db``.
    """
    app_dsn = swap_dsn_credentials(_migrated_app_db, "app_rls", "app_rls_pw")
    engine = create_async_engine(app_dsn, echo=False)
    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with session_maker() as session:
            yield session
    finally:
        await engine.dispose()


@pytest.fixture
async def sample_org(db_session: AsyncSession):
    """Organización (tenant) base para tests de modelos/persister.

    Es el ancla de identidad: sample_user, sample_party, sample_account y
    sample_flex_import cuelgan de este mismo org para que el contexto sea
    coherente (un solo tenant). Tests que necesitan el organization_id lo
    obtienen de aquí.
    """
    from ibkr_control.db.models.organizations import Organization

    org = Organization(type="personal", name="Fixture Org")
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)
    return org


@pytest.fixture
async def sample_user(db_session: AsyncSession, sample_org):
    """Usuario fundador de sample_org: crea User + UserSettings + Membership(owner)
    + Party.

    Espeja provision_org() (y on_after_register) pero sobre el sample_org ya
    existente (no crea un org nuevo). Incluye la fila UserSettings que en prod
    SIEMPRE existe (on_after_register la crea), para que tests que lleguen a
    settings vía sample_user no diverjan de prod. Devuelve el User; el org es
    sample_org y el party fundador queda asociado (party.user_id == user.id).
    """
    from ibkr_control.auth.models import User
    from ibkr_control.db.models.memberships import Membership
    from ibkr_control.db.models.parties import Party
    from ibkr_control.settings.models import UserSettings

    u = User(email="fixture@t.com", hashed_password="x", is_active=True, name="Fixture User")
    db_session.add(u)
    await db_session.flush()
    db_session.add(UserSettings(user_id=u.id))
    db_session.add(Membership(user_id=u.id, organization_id=sample_org.id, role="owner"))
    db_session.add(Party(organization_id=sample_org.id, display_name="Fixture User", user_id=u.id))
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def sample_party(db_session: AsyncSession, sample_org):
    """Party (persona fiscal) sin login en sample_org.

    Para tests que necesitan un party explícito (p.ej. participations
    party-anchored) distinto del party fundador de sample_user.
    """
    from ibkr_control.db.models.parties import Party

    p = Party(organization_id=sample_org.id, display_name="Fixture Party", user_id=None)
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p


@pytest.fixture
async def second_sample_user(db_session: AsyncSession):
    """A second User for multi-user isolation tests.

    Crea su PROPIO org + UserSettings + membership + party (tenant separado de
    sample_org) para escenarios de aislamiento cross-tenant. Incluye UserSettings
    (que en prod siempre existe vía on_after_register) para ser una identidad fiel.
    """
    from ibkr_control.auth.models import User
    from ibkr_control.db.models.memberships import Membership
    from ibkr_control.db.models.organizations import Organization
    from ibkr_control.db.models.parties import Party
    from ibkr_control.settings.models import UserSettings

    org = Organization(type="personal", name="Second Fixture Org")
    db_session.add(org)
    await db_session.flush()
    u = User(
        email="second_fixture@t.com",
        hashed_password="x",
        is_active=True,
        name="Second Fixture User",
    )
    db_session.add(u)
    await db_session.flush()
    db_session.add(UserSettings(user_id=u.id))
    db_session.add(Membership(user_id=u.id, organization_id=org.id, role="owner"))
    db_session.add(Party(organization_id=org.id, display_name="Second Fixture User", user_id=u.id))
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def auth_headers(client: AsyncClient) -> dict:
    """Registra un usuario de test y devuelve headers de autorizacion JWT."""
    await client.post(
        "/api/auth/register",
        json={"email": "api_test@test.com", "password": "supersecret123", "name": "API Test User"},
    )
    login = await client.post(
        "/api/auth/jwt/login",
        data={"username": "api_test@test.com", "password": "supersecret123"},
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def second_auth_headers(client: AsyncClient) -> dict:
    """Registra un segundo usuario para tests de isolation R6."""
    await client.post(
        "/api/auth/register",
        json={
            "email": "api_test_2@test.com",
            "password": "supersecret123",
            "name": "API Test User 2",
        },
    )
    login = await client.post(
        "/api/auth/jwt/login",
        data={"username": "api_test_2@test.com", "password": "supersecret123"},
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def auth_headers_with_org(client: AsyncClient, app_owner_engine) -> dict:
    """Registra un usuario via API, le provisiona un org + membership(owner) +
    party, y devuelve headers JWT.

    A diferencia de auth_headers (que solo registra), este deja al usuario con
    una membership única para que org_context resuelva su contexto. NO usa
    provision_org() (eso crearía un segundo usuario nuevo): registra primero
    via la API (dispara on_after_register → UserSettings) y luego inserta el
    org/membership/party PARA ese usuario ya registrado.

    Comparte la DB migrada del app vía app_owner_engine (mismo `_migrated_app_db` que
    app_with_db). Seedea como OWNER (superuser → bypassa RLS); `parties` está bajo
    FORCE RLS, así que setea `app.current_org` antes de insertar el Party para que
    pase el WITH CHECK (mismo patrón que rls_session_factory.seed).
    """
    from sqlalchemy import select, text
    from ibkr_control.auth.models import User
    from ibkr_control.db.models.memberships import Membership
    from ibkr_control.db.models.organizations import Organization
    from ibkr_control.db.models.parties import Party

    email = "org_owner@test.com"
    await client.post(
        "/api/auth/register",
        json={"email": email, "password": "supersecret123", "name": "Org Owner"},
    )

    session_maker = async_sessionmaker(
        app_owner_engine, expire_on_commit=False, class_=AsyncSession
    )
    async with session_maker() as session:
        user = await session.scalar(select(User).where(User.email == email))
        org = Organization(type="personal", name="Org Owner Household")
        session.add(org)
        await session.flush()
        session.add(Membership(user_id=user.id, organization_id=org.id, role="owner"))
        # parties is org-scoped under FORCE RLS → set context so its WITH CHECK passes.
        await session.execute(
            text("SELECT set_config('app.current_org', :o, true)").bindparams(o=str(org.id))
        )
        session.add(Party(organization_id=org.id, display_name="Org Owner", user_id=user.id))
        await session.commit()

    login = await client.post(
        "/api/auth/jwt/login",
        data={"username": email, "password": "supersecret123"},
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def second_auth_headers_with_org(client: AsyncClient, app_owner_engine) -> dict:
    """A second org-having user for cross-tenant isolation tests.

    Mirrors auth_headers_with_org but with a distinct email/org so the two
    can detect disjoint accounts and assert that one org cannot claim the
    other's. Shares the migrated app DB via app_owner_engine (same lifecycle as
    auth_headers_with_org)."""
    from sqlalchemy import select, text
    from ibkr_control.auth.models import User
    from ibkr_control.db.models.memberships import Membership
    from ibkr_control.db.models.organizations import Organization
    from ibkr_control.db.models.parties import Party

    email = "org_owner_2@test.com"
    await client.post(
        "/api/auth/register",
        json={"email": email, "password": "supersecret123", "name": "Org Owner 2"},
    )

    session_maker = async_sessionmaker(
        app_owner_engine, expire_on_commit=False, class_=AsyncSession
    )
    async with session_maker() as session:
        user = await session.scalar(select(User).where(User.email == email))
        org = Organization(type="personal", name="Org Owner 2 Household")
        session.add(org)
        await session.flush()
        session.add(Membership(user_id=user.id, organization_id=org.id, role="owner"))
        # parties is org-scoped under FORCE RLS → set context so its WITH CHECK passes.
        await session.execute(
            text("SELECT set_config('app.current_org', :o, true)").bindparams(o=str(org.id))
        )
        session.add(Party(organization_id=org.id, display_name="Org Owner 2", user_id=user.id))
        await session.commit()

    login = await client.post(
        "/api/auth/jwt/login",
        data={"username": email, "password": "supersecret123"},
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def sample_account(db_session: AsyncSession, sample_org):
    from ibkr_control.db.models.accounts import Account

    a = Account(
        ibkr_account_id="U99999999",
        organization_id=sample_org.id,
        alias="fixture-acc",
        currency="USD",
    )
    db_session.add(a)
    await db_session.commit()
    await db_session.refresh(a)
    return a


@pytest.fixture
async def sample_flex_import(db_session: AsyncSession, sample_org):
    from datetime import date
    from ibkr_control.db.models.flex_raw import FlexImport

    fi = FlexImport(
        organization_id=sample_org.id,
        anyo=2025,
        xml_hash="helper-test-hash",
        xml_size_bytes=100,
        xml_bytes=b"<test/>",
        source="manual_upload",
        period_covered_from=date(2025, 1, 1),
        period_covered_to=date(2025, 12, 31),
        year_status="sealed",
        status="ok",
    )
    db_session.add(fi)
    await db_session.flush()
    return fi


@pytest.fixture(scope="session")
def cassette_dir() -> Path:
    return Path(__file__).parent / "fixtures" / "cassettes"


@pytest.fixture(scope="module")
def vcr_config():
    """Config base para VCR. Filtra Authorization header y query params sensibles.

    scope="module" es requerido por pytest-vcr: el fixture vcr interno que
    consume vcr_config usa scope module, y pytest no permite que un fixture de
    scope inferior (function) sea consumido por uno de scope superior.

    match_on excluye query params para que las cassettes handwritten (con tokens
    de prueba) coincidan con cualquier request al mismo path, sin importar el
    valor del token/query_id en los params. Es seguro porque los cassettes son
    por-path y los tokens se filtran del header a nivel de record.
    """
    return {
        "filter_headers": ["Authorization", "X-Api-Key"],
        "filter_query_parameters": ["t", "token", "$$app_token"],
        "decode_compressed_response": True,
        "record_mode": "none",  # CI fails if cassette missing
        "match_on": ["method", "scheme", "host", "path"],
    }
