import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from ibkr_control.main import create_app
from ibkr_control.db.base import Base
from ibkr_control.db.session import get_async_session


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


async def test_register_creates_user(client):
    response = await client.post(
        "/api/auth/register",
        json={"email": "Test Owner@example.com", "password": "supersecret123", "name": "Test Owner"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "Test Owner@example.com"
    assert body["name"] == "Test Owner"


async def test_login_returns_jwt(client):
    await client.post(
        "/api/auth/register",
        json={"email": "c@x.com", "password": "supersecret123", "name": "C"},
    )
    response = await client.post(
        "/api/auth/jwt/login",
        data={"username": "c@x.com", "password": "supersecret123"},
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    assert token


async def test_me_requires_auth(client):
    await client.post(
        "/api/auth/register",
        json={"email": "c@x.com", "password": "supersecret123", "name": "C"},
    )
    login = await client.post(
        "/api/auth/jwt/login",
        data={"username": "c@x.com", "password": "supersecret123"},
    )
    token = login.json()["access_token"]

    me = await client.get("/api/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "c@x.com"


async def test_me_without_token_is_401(client):
    response = await client.get("/api/users/me")
    assert response.status_code == 401
