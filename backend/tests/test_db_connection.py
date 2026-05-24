import pytest
from sqlalchemy import text
from testcontainers.postgres import PostgresContainer
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as pg:
        yield pg


async def test_db_connection_returns_one(postgres_container):
    url = postgres_container.get_connection_url()
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        assert result.scalar() == 1
    await engine.dispose()
