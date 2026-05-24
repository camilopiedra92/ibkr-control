import os

# Defaults seteados ANTES de cualquier import de ibkr_control.*, porque pytest
# carga conftest.py antes de colectar test modules. Sin esto, test_health.py
# falla en collection time post-Task 5 cuando main.py importe auth -> db.session
# -> get_settings() -> ValidationError por DATABASE_URL/JWT_SECRET faltantes.
# Tests que necesitan DB real (test_db_connection, test_auth) sobreescriben
# via monkeypatch -- setdefault no piso valores ya seteados.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")

import pytest
from httpx import ASGITransport, AsyncClient
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from ibkr_control.main import create_app
from ibkr_control.db.base import Base
from ibkr_control.db.session import get_async_session


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
async def app_with_db(postgres_container, monkeypatch):
    url = postgres_container.get_connection_url()
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")
    monkeypatch.setenv("JWT_LIFETIME_SECONDS", "3600")
    monkeypatch.setenv("BACKEND_CORS_ORIGINS", "")

    from ibkr_control.config import get_settings
    get_settings.cache_clear()

    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_session():
        async with session_maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_async_session] = override_get_session

    yield app

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
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
    open multiple concurrent sessions (e.g. advisory lock contention).

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
async def sample_user(db_session: AsyncSession):
    from ibkr_control.auth.models import User
    u = User(email='fixture@t.com', hashed_password='x', is_active=True, name='Fixture User')
    db_session.add(u)
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
async def sample_account(db_session: AsyncSession):
    from ibkr_control.db.models.accounts import Account
    a = Account(ibkr_account_id='U99999999', alias='fixture-acc', currency='USD')
    db_session.add(a)
    await db_session.commit()
    await db_session.refresh(a)
    return a


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
