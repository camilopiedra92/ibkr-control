import os

# Defaults seteados ANTES de cualquier import de ibkr_control.*, porque pytest
# carga conftest.py antes de colectar test modules. Sin esto, test_health.py
# falla en collection time post-Task 5 cuando main.py importe auth → db.session
# → get_settings() → ValidationError por DATABASE_URL/JWT_SECRET faltantes.
# Tests que necesitan DB real (test_db_connection, test_auth) sobreescriben
# vía monkeypatch — setdefault no piso valores ya seteados.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")

import pytest
from httpx import ASGITransport, AsyncClient
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
