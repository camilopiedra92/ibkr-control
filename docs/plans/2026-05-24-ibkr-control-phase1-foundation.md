# IBKR Control Center — Phase 1: Foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Crear el nuevo repo `ibkr-control/` con backend FastAPI + frontend Next.js + Postgres en docker-compose, deployable a Coolify, con auth JWT funcionando (register + login + protected route + settings) y schema base (users + user_settings).

**Architecture:** Monolito modular (Opción A del spec §3). Backend FastAPI con `fastapi-users` para auth. Frontend Next.js 14 App Router con orval para cliente TypeScript autogenerado del OpenAPI. Postgres 16 en docker-compose. Coolify lee `docker-compose.yml` directamente.

**Tech Stack:**
- Backend: Python 3.12 + FastAPI + SQLAlchemy 2.x (async) + asyncpg + Alembic + fastapi-users + uvicorn + pytest + testcontainers
- Frontend: Next.js 14 (App Router) + TypeScript + Tailwind + shadcn/ui + TanStack Query + orval + Playwright
- DB: Postgres 16
- Tooling: uv (Python), pnpm (Node), Docker, Coolify

**Spec referencia:** `docs/specs/2026-05-24-ibkr-control-center-design.md` (en ESTE repo, no en `renta`)

**Cobertura Phase 1:** §3 topología, §5.1 modelo de identidad (users + user_settings), §9 estructura repo (parcial), §10 riesgos relevantes a auth/deploy.

---

### Task 1: Verificar bootstrap (ya completado manualmente)

El repo `/Users/owner/Development/ibkr-control/` se creó durante
la sesión de planning con la estructura base (`.gitignore`, `README.md`,
`CLAUDE.md`, `docs/specs/`, `docs/plans/`). Esta task solo verifica que
todo esté en su lugar antes de arrancar con el backend.

**Files (ya existen):**
- `/Users/owner/Development/ibkr-control/.gitignore`
- `/Users/owner/Development/ibkr-control/README.md`
- `/Users/owner/Development/ibkr-control/CLAUDE.md`
- `/Users/owner/Development/ibkr-control/docs/specs/2026-05-24-ibkr-control-center-design.md`
- `/Users/owner/Development/ibkr-control/docs/plans/2026-05-24-ibkr-control-phase1-foundation.md` (este archivo)

- [x] **Step 1: Verificar estructura**

```bash
ls /Users/owner/Development/ibkr-control/
ls /Users/owner/Development/ibkr-control/docs/specs/
ls /Users/owner/Development/ibkr-control/docs/plans/
```

Expected: ver `.gitignore`, `README.md`, `CLAUDE.md`, `docs/`. En
`docs/specs/` el spec; en `docs/plans/` este plan.

- [x] **Step 2: Inicializar git si no está**

```bash
cd /Users/owner/Development/ibkr-control
if [ ! -d .git ]; then
  git init -b main
fi
git status
```

Expected: branch `main`, archivos untracked
(`.gitignore`, `README.md`, `CLAUDE.md`, `docs/`).

- [x] **Step 3: Commit inicial**

```bash
cd /Users/owner/Development/ibkr-control
git add .gitignore README.md CLAUDE.md docs/
git commit -m "chore: bootstrap repo con spec, plan Phase 1 y CLAUDE.md"
```

Expected: 1 commit con ≥5 archivos.

---

### Task 2: Estructura del backend Python con uv

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/src/ibkr_control/__init__.py`
- Create: `backend/src/ibkr_control/main.py`
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_health.py`
- Create: `backend/uv.lock` (generado por `uv lock`)

- [x] **Step 1: Verificar que uv está instalado, si no instalar**

Run:

```bash
uv --version
```

Si falla:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

- [x] **Step 2: Crear backend/pyproject.toml**

Contenido completo:

```toml
[project]
name = "ibkr-control-backend"
version = "0.1.0"
description = "IBKR Control Center backend"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "sqlalchemy[asyncio]>=2.0.36",
    "asyncpg>=0.30.0",
    "alembic>=1.14.0",
    "fastapi-users[sqlalchemy]>=14.0.0",
    "pydantic-settings>=2.6.0",
    "python-multipart>=0.0.12",
]

[dependency-groups]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.24.0",
    "httpx>=0.28.0",
    "testcontainers[postgres]>=4.8.0",
    "ruff>=0.8.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/ibkr_control"]
```

- [x] **Step 3: Crear estructura de directorios y __init__.py vacíos**

```bash
cd /Users/owner/Development/ibkr-control
mkdir -p backend/src/ibkr_control backend/tests
touch backend/src/ibkr_control/__init__.py
touch backend/tests/__init__.py
```

- [x] **Step 4: Escribir test del health endpoint (TDD failing test)**

`backend/tests/test_health.py`:

```python
from httpx import ASGITransport, AsyncClient
from ibkr_control.main import app


async def test_health_returns_ok():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [x] **Step 5: Crear conftest.py mínimo**

`backend/tests/conftest.py`:

```python
import pytest


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"
```

- [x] **Step 6: Instalar deps + correr test (debe fallar — ImportError)**

```bash
cd /Users/owner/Development/ibkr-control/backend
uv sync
uv run pytest tests/test_health.py -v
```

Expected: ERROR — `ModuleNotFoundError: No module named 'ibkr_control.main'` (o el FastAPI app no existe).

- [x] **Step 7: Implementar main.py mínimo para pasar el test**

`backend/src/ibkr_control/main.py`:

```python
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="IBKR Control Center", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
```

- [x] **Step 8: Correr test, verificar que pasa**

```bash
uv run pytest tests/test_health.py -v
```

Expected: `1 passed`.

- [x] **Step 9: Verificar que el server arranca local**

```bash
uv run uvicorn ibkr_control.main:app --reload --port 8000 &
sleep 2
curl -s http://localhost:8000/health
kill %1
```

Expected: `{"status":"ok"}`.

- [x] **Step 10: Commit**

```bash
cd /Users/owner/Development/ibkr-control
git add backend/
git commit -m "feat(backend): FastAPI skeleton with health endpoint"
```

---

### Task 3: docker-compose con Postgres + backend wired

**Files:**
- Create: `docker-compose.yml`
- Create: `backend/Dockerfile`
- Create: `backend/.dockerignore`
- Create: `.env.example`

- [x] **Step 1: Crear backend/Dockerfile (multi-stage para dev)**

`backend/Dockerfile`:

```dockerfile
# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5.4 /uv /uvx /usr/local/bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY src/ ./src/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "ibkr_control.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [x] **Step 2: Crear backend/.dockerignore**

`backend/.dockerignore`:

```
__pycache__
*.pyc
.pytest_cache
.coverage
.venv
tests/
.env
.env.*
```

- [x] **Step 3: Crear .env.example en la raíz**

`/Users/owner/Development/ibkr-control/.env.example`:

```bash
# Postgres
POSTGRES_USER=ibkr
POSTGRES_PASSWORD=changeme
POSTGRES_DB=ibkr_control
POSTGRES_PORT=5432

# Backend
DATABASE_URL=postgresql+asyncpg://ibkr:changeme@postgres:5432/ibkr_control
JWT_SECRET=changeme-min-32-chars-please-rotate
JWT_LIFETIME_SECONDS=3600
BACKEND_CORS_ORIGINS=http://localhost:3000
```

- [x] **Step 4: Copiar .env.example a .env y editar JWT_SECRET**

```bash
cd /Users/owner/Development/ibkr-control
cp .env.example .env
# Generar un JWT_SECRET fuerte
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
# Reemplazar JWT_SECRET en .env con el valor generado
```

- [x] **Step 5: Crear docker-compose.yml**

`/Users/owner/Development/ibkr-control/docker-compose.yml`:

```yaml
services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    ports:
      - "${POSTGRES_PORT:-5432}:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 5s
      timeout: 5s
      retries: 10

  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    restart: unless-stopped
    environment:
      DATABASE_URL: ${DATABASE_URL}
      JWT_SECRET: ${JWT_SECRET}
      JWT_LIFETIME_SECONDS: ${JWT_LIFETIME_SECONDS}
      BACKEND_CORS_ORIGINS: ${BACKEND_CORS_ORIGINS}
    ports:
      - "8000:8000"
    depends_on:
      postgres:
        condition: service_healthy

volumes:
  postgres_data:
```

(El servicio `frontend` se agrega en Task 7.)

- [x] **Step 6: Arrancar el stack y verificar**

```bash
cd /Users/owner/Development/ibkr-control
docker compose up -d --build
sleep 10
curl -s http://localhost:8000/health
```

Expected: `{"status":"ok"}`.

- [x] **Step 7: Verificar Postgres está vivo**

```bash
docker compose exec postgres pg_isready -U ibkr
```

Expected: `/var/run/postgresql:5432 - accepting connections`.

- [x] **Step 8: Commit**

```bash
docker compose down
git add backend/Dockerfile backend/.dockerignore docker-compose.yml .env.example
git commit -m "feat: docker-compose con postgres + backend"
```

---

### Task 4: Alembic + Settings + DB session

**Files:**
- Create: `backend/src/ibkr_control/config.py`
- Create: `backend/src/ibkr_control/db/__init__.py`
- Create: `backend/src/ibkr_control/db/base.py`
- Create: `backend/src/ibkr_control/db/session.py`
- Create: `backend/alembic.ini`
- Create: `backend/alembic/env.py`
- Create: `backend/alembic/script.py.mako`
- Create: `backend/alembic/versions/.gitkeep`
- Create: `backend/tests/test_db_connection.py`

- [x] **Step 1: Crear config.py con pydantic-settings**

`backend/src/ibkr_control/config.py`:

```python
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str
    jwt_secret: str
    jwt_lifetime_seconds: int = 3600
    backend_cors_origins: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.backend_cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
```

- [x] **Step 2: Crear db/base.py con DeclarativeBase**

`backend/src/ibkr_control/db/__init__.py`: vacío.

`backend/src/ibkr_control/db/base.py`:

```python
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

- [x] **Step 3: Crear db/session.py con AsyncSession**

`backend/src/ibkr_control/db/session.py`:

```python
from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from ibkr_control.config import get_settings

settings = get_settings()

engine = create_async_engine(settings.database_url, echo=False, future=True)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session
```

- [x] **Step 4: Inicializar Alembic**

```bash
cd /Users/owner/Development/ibkr-control/backend
uv run alembic init -t async alembic
```

Expected: Creates `alembic.ini` + `alembic/env.py` + `alembic/versions/`.

- [x] **Step 5: Editar alembic.ini (sacar URL hardcoded, leer de env)**

En `backend/alembic.ini`, comentar la línea `sqlalchemy.url = ...`:

```ini
# sqlalchemy.url = driver://user:pass@localhost/dbname
```

- [x] **Step 6: Reescribir alembic/env.py para usar settings y modelos async**

`backend/alembic/env.py` (reemplazar todo):

```python
import asyncio
from logging.config import fileConfig
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context

from ibkr_control.config import get_settings
from ibkr_control.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [x] **Step 7: Escribir test de conexión a DB (failing, no migrations todavía)**

`backend/tests/test_db_connection.py`:

```python
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
```

- [x] **Step 8: Correr test**

```bash
cd /Users/owner/Development/ibkr-control/backend
uv run pytest tests/test_db_connection.py -v
```

Expected: `1 passed` (testcontainers levanta Postgres, conexión funciona).

- [x] **Step 9: Verificar que Alembic puede correr (sin migraciones aún)**

Asegurar que el stack está corriendo:

```bash
cd /Users/owner/Development/ibkr-control
docker compose up -d postgres
cd backend
uv run alembic current
```

Expected: `(empty)` o `Current revision(s) for ...: <empty>`. Sin errores.

- [x] **Step 10: Commit**

```bash
cd /Users/owner/Development/ibkr-control
git add backend/src/ibkr_control/config.py backend/src/ibkr_control/db/ \
        backend/alembic.ini backend/alembic/ backend/tests/test_db_connection.py
git commit -m "feat(backend): alembic + async DB session + settings"
```

---

### Task 5: User model + fastapi-users + primera migración

**Files:**
- Create: `backend/src/ibkr_control/auth/__init__.py`
- Create: `backend/src/ibkr_control/auth/models.py`
- Create: `backend/src/ibkr_control/auth/schemas.py`
- Create: `backend/src/ibkr_control/auth/manager.py`
- Create: `backend/src/ibkr_control/auth/backend.py`
- Create: `backend/src/ibkr_control/auth/router.py`
- Create: `backend/alembic/versions/001_initial_users.py` (generado vía autogenerate)
- Modify: `backend/src/ibkr_control/main.py`
- Create: `backend/tests/test_auth.py`

- [x] **Step 1: Crear User model usando fastapi-users**

`backend/src/ibkr_control/auth/__init__.py`: vacío.

`backend/src/ibkr_control/auth/models.py`:

```python
from datetime import datetime
from fastapi_users.db import SQLAlchemyBaseUserTable
from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column
from ibkr_control.db.base import Base


class User(SQLAlchemyBaseUserTable[int], Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [x] **Step 2: Crear schemas Pydantic para User**

`backend/src/ibkr_control/auth/schemas.py`:

```python
from fastapi_users import schemas


class UserRead(schemas.BaseUser[int]):
    name: str


class UserCreate(schemas.BaseUserCreate):
    name: str


class UserUpdate(schemas.BaseUserUpdate):
    name: str | None = None
```

- [x] **Step 3: Crear UserManager**

`backend/src/ibkr_control/auth/manager.py`:

```python
from collections.abc import AsyncGenerator
from fastapi import Depends
from fastapi_users import BaseUserManager, IntegerIDMixin
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.config import get_settings
from ibkr_control.db.session import get_async_session
from ibkr_control.auth.models import User

settings = get_settings()


async def get_user_db(
    session: AsyncSession = Depends(get_async_session),
) -> AsyncGenerator[SQLAlchemyUserDatabase, None]:
    yield SQLAlchemyUserDatabase(session, User)


class UserManager(IntegerIDMixin, BaseUserManager[User, int]):
    reset_password_token_secret = settings.jwt_secret
    verification_token_secret = settings.jwt_secret


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
) -> AsyncGenerator[UserManager, None]:
    yield UserManager(user_db)
```

- [x] **Step 4: Crear backend de auth con JWT**

`backend/src/ibkr_control/auth/backend.py`:

```python
from fastapi_users.authentication import AuthenticationBackend, BearerTransport, JWTStrategy
from fastapi_users import FastAPIUsers
from fastapi import Depends

from ibkr_control.config import get_settings
from ibkr_control.auth.manager import get_user_manager
from ibkr_control.auth.models import User

settings = get_settings()

bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(secret=settings.jwt_secret, lifetime_seconds=settings.jwt_lifetime_seconds)


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, int](get_user_manager, [auth_backend])

current_active_user = fastapi_users.current_user(active=True)
```

- [x] **Step 5: Crear router con endpoints auth**

`backend/src/ibkr_control/auth/router.py`:

```python
from fastapi import APIRouter
from ibkr_control.auth.backend import auth_backend, fastapi_users
from ibkr_control.auth.schemas import UserCreate, UserRead, UserUpdate

router = APIRouter()

router.include_router(
    fastapi_users.get_auth_router(auth_backend),
    prefix="/auth/jwt",
    tags=["auth"],
)
router.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/auth",
    tags=["auth"],
)
router.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
    prefix="/users",
    tags=["users"],
)
```

- [x] **Step 6: Wire router en main.py**

`backend/src/ibkr_control/main.py` (reemplazar):

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ibkr_control.config import get_settings
from ibkr_control.auth.router import router as auth_router


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="IBKR Control Center", version="0.1.0")

    if settings.cors_origins_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins_list,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth_router, prefix="/api")
    return app


app = create_app()
```

- [x] **Step 7: Asegurar que Base.metadata vea User (importarlo en algún módulo cargado por Alembic env)**

Editar `backend/src/ibkr_control/db/__init__.py`:

```python
# Re-export models para que Alembic detecte todas las tablas via Base.metadata
from ibkr_control.auth.models import User  # noqa: F401
from ibkr_control.db.base import Base  # noqa: F401
```

Y en `backend/alembic/env.py`, antes de `target_metadata = Base.metadata`, agregar:

```python
import ibkr_control.db  # noqa: F401  (carga modelos)
```

- [x] **Step 8: Generar migración initial users**

Con Postgres corriendo:

```bash
cd /Users/owner/Development/ibkr-control
docker compose up -d postgres
cd backend
uv run alembic revision --autogenerate -m "001 initial users"
```

Expected: Archivo creado en `backend/alembic/versions/<hash>_001_initial_users.py`. Renombrar a `001_initial_users.py` para que tenga prefix estable:

```bash
mv backend/alembic/versions/*001_initial_users.py backend/alembic/versions/001_initial_users.py
```

Revisar que el archivo contenga `op.create_table('users', ...)`.

- [x] **Step 9: Aplicar migración**

```bash
uv run alembic upgrade head
```

Expected: `INFO  [alembic.runtime.migration] Running upgrade  -> 001, 001 initial users`.

Verificar tabla creada:

```bash
docker compose exec postgres psql -U ibkr -d ibkr_control -c "\d users"
```

Expected: Salida con columnas `id`, `email`, `hashed_password`, `is_active`, `is_superuser`, `is_verified`, `name`, `created_at`.

- [x] **Step 10: Escribir tests de auth (register + login + protected)**

`backend/tests/test_auth.py`:

```python
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
```

- [x] **Step 11: Correr tests**

```bash
cd /Users/owner/Development/ibkr-control/backend
uv run pytest tests/test_auth.py -v
```

Expected: `4 passed`.

- [x] **Step 12: Commit**

```bash
cd /Users/owner/Development/ibkr-control
git add backend/src/ibkr_control/auth/ backend/src/ibkr_control/main.py \
        backend/src/ibkr_control/db/__init__.py backend/alembic/ \
        backend/tests/test_auth.py
git commit -m "feat(auth): fastapi-users JWT + users table + register/login endpoints"
```

---

### Task 6: UserSettings model + auto-create on register

**Files:**
- Create: `backend/src/ibkr_control/settings/__init__.py`
- Create: `backend/src/ibkr_control/settings/models.py`
- Create: `backend/src/ibkr_control/settings/schemas.py`
- Create: `backend/src/ibkr_control/settings/router.py`
- Modify: `backend/src/ibkr_control/auth/manager.py`
- Modify: `backend/src/ibkr_control/db/__init__.py`
- Modify: `backend/src/ibkr_control/main.py`
- Create: `backend/alembic/versions/002_user_settings.py`
- Create: `backend/tests/test_settings.py`

- [x] **Step 1: Crear UserSettings model**

`backend/src/ibkr_control/settings/__init__.py`: vacío.

`backend/src/ibkr_control/settings/models.py`:

```python
from datetime import datetime
from decimal import Decimal
from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column
from ibkr_control.db.base import Base


class UserSettings(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    marginal_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, server_default="0.3900"
    )
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="America/Bogota"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
```

- [x] **Step 2: Crear schemas para settings**

`backend/src/ibkr_control/settings/schemas.py`:

```python
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field


class UserSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    marginal_rate: Decimal
    timezone: str


class UserSettingsUpdate(BaseModel):
    marginal_rate: Decimal | None = Field(default=None, gt=Decimal("0"), lt=Decimal("1"))
    timezone: str | None = None
```

- [x] **Step 3: Crear router de settings**

`backend/src/ibkr_control/settings/router.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.auth.backend import current_active_user
from ibkr_control.auth.models import User
from ibkr_control.db.session import get_async_session
from ibkr_control.settings.models import UserSettings
from ibkr_control.settings.schemas import UserSettingsRead, UserSettingsUpdate

router = APIRouter(prefix="/settings", tags=["settings"])


async def _load(user_id: int, session: AsyncSession) -> UserSettings:
    result = await session.execute(select(UserSettings).where(UserSettings.user_id == user_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="settings not found")
    return row


@router.get("", response_model=UserSettingsRead)
async def get_settings(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> UserSettings:
    return await _load(user.id, session)


@router.patch("", response_model=UserSettingsRead)
async def update_settings(
    payload: UserSettingsUpdate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> UserSettings:
    row = await _load(user.id, session)
    if payload.marginal_rate is not None:
        row.marginal_rate = payload.marginal_rate
    if payload.timezone is not None:
        row.timezone = payload.timezone
    await session.commit()
    await session.refresh(row)
    return row
```

- [x] **Step 4: Auto-crear UserSettings al registrar usuario**

Modificar `backend/src/ibkr_control/auth/manager.py` para sobreescribir `on_after_register`:

```python
from collections.abc import AsyncGenerator
from fastapi import Depends, Request
from fastapi_users import BaseUserManager, IntegerIDMixin
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.config import get_settings
from ibkr_control.db.session import get_async_session
from ibkr_control.auth.models import User
from ibkr_control.settings.models import UserSettings

settings = get_settings()


async def get_user_db(
    session: AsyncSession = Depends(get_async_session),
) -> AsyncGenerator[SQLAlchemyUserDatabase, None]:
    yield SQLAlchemyUserDatabase(session, User)


class UserManager(IntegerIDMixin, BaseUserManager[User, int]):
    reset_password_token_secret = settings.jwt_secret
    verification_token_secret = settings.jwt_secret

    async def on_after_register(self, user: User, request: Request | None = None) -> None:
        session: AsyncSession = self.user_db.session  # type: ignore[attr-defined]
        session.add(UserSettings(user_id=user.id))
        await session.commit()


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
) -> AsyncGenerator[UserManager, None]:
    yield UserManager(user_db)
```

- [x] **Step 5: Registrar UserSettings en `db/__init__.py`**

Editar `backend/src/ibkr_control/db/__init__.py`:

```python
from ibkr_control.auth.models import User  # noqa: F401
from ibkr_control.settings.models import UserSettings  # noqa: F401
from ibkr_control.db.base import Base  # noqa: F401
```

- [x] **Step 6: Incluir router en main.py**

Editar `backend/src/ibkr_control/main.py` para agregar:

```python
from ibkr_control.settings.router import router as settings_router
# ...
app.include_router(settings_router, prefix="/api")
```

(Justo después del `app.include_router(auth_router, ...)`.)

- [x] **Step 7: Generar migración**

```bash
cd /Users/owner/Development/ibkr-control/backend
uv run alembic revision --autogenerate -m "002 user settings"
mv backend/alembic/versions/*002_user_settings.py backend/alembic/versions/002_user_settings.py
uv run alembic upgrade head
```

Verificar tabla `user_settings` creada.

- [x] **Step 8: Escribir tests de settings**

`backend/tests/test_settings.py`:

```python
from httpx import ASGITransport, AsyncClient
import pytest

# Reusa fixtures de test_auth (client, app_with_db, postgres_container)
pytest_plugins = ["tests.test_auth"]


async def _register_and_login(client: AsyncClient) -> str:
    await client.post(
        "/api/auth/register",
        json={"email": "c@x.com", "password": "supersecret123", "name": "C"},
    )
    login = await client.post(
        "/api/auth/jwt/login",
        data={"username": "c@x.com", "password": "supersecret123"},
    )
    return login.json()["access_token"]


async def test_settings_auto_created_on_register(client):
    token = await _register_and_login(client)
    response = await client.get("/api/settings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["marginal_rate"] == "0.3900"
    assert body["timezone"] == "America/Bogota"


async def test_settings_patch_marginal_rate(client):
    token = await _register_and_login(client)
    response = await client.patch(
        "/api/settings",
        json={"marginal_rate": "0.3300"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["marginal_rate"] == "0.3300"


async def test_settings_patch_rejects_out_of_range(client):
    token = await _register_and_login(client)
    response = await client.patch(
        "/api/settings",
        json={"marginal_rate": "1.5000"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


async def test_settings_requires_auth(client):
    response = await client.get("/api/settings")
    assert response.status_code == 401
```

- [x] **Step 9: Correr tests**

```bash
cd /Users/owner/Development/ibkr-control/backend
uv run pytest tests/test_settings.py -v
```

Expected: `4 passed`.

- [x] **Step 10: Commit**

```bash
cd /Users/owner/Development/ibkr-control
git add backend/src/ibkr_control/settings/ backend/src/ibkr_control/auth/manager.py \
        backend/src/ibkr_control/db/__init__.py backend/src/ibkr_control/main.py \
        backend/alembic/versions/002_user_settings.py backend/tests/test_settings.py
git commit -m "feat(settings): user_settings table with marginal_rate + timezone + auto-create"
```

---

### Task 7: Bootstrap del frontend Next.js + Tailwind + shadcn

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/pnpm-lock.yaml` (generado)
- Create: `frontend/next.config.mjs`
- Create: `frontend/tsconfig.json`
- Create: `frontend/tailwind.config.ts`
- Create: `frontend/postcss.config.mjs`
- Create: `frontend/components.json` (shadcn)
- Create: `frontend/src/app/layout.tsx`
- Create: `frontend/src/app/page.tsx`
- Create: `frontend/src/app/globals.css`
- Create: `frontend/Dockerfile`
- Create: `frontend/.dockerignore`
- Modify: `docker-compose.yml`

- [x] **Step 1: Verificar pnpm instalado**

```bash
pnpm --version
```

Si falla:

```bash
npm install -g pnpm@latest
```

- [x] **Step 2: Bootstrap Next.js con TypeScript + Tailwind**

```bash
cd /Users/owner/Development/ibkr-control
pnpm create next-app@latest frontend \
  --typescript --tailwind --app --src-dir --import-alias "@/*" --no-eslint --use-pnpm
```

Acepta defaults. Verificar que crea `frontend/` con la estructura esperada.

- [x] **Step 3: Agregar dependencias adicionales**

```bash
cd /Users/owner/Development/ibkr-control/frontend
pnpm add @tanstack/react-query axios zod react-hook-form @hookform/resolvers
pnpm add -D @types/node @playwright/test orval
```

- [x] **Step 4: Inicializar shadcn/ui**

```bash
pnpm dlx shadcn@latest init -d
```

Acepta defaults (Slate, CSS variables). Esto crea `components.json` + `src/lib/utils.ts` + actualiza `globals.css`.

Agregar componentes mínimos:

```bash
pnpm dlx shadcn@latest add button input label form card
```

- [x] **Step 5: Crear home page mínimo (placeholder)**

`frontend/src/app/page.tsx` (reemplazar lo que generó Next):

```tsx
export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-24">
      <h1 className="text-4xl font-bold">IBKR Control Center</h1>
      <p className="mt-4 text-sm text-muted-foreground">Foundation OK · Phase 1</p>
    </main>
  );
}
```

- [x] **Step 6: Test de smoke local**

```bash
cd /Users/owner/Development/ibkr-control/frontend
pnpm dev &
sleep 5
curl -s http://localhost:3000 | grep "IBKR Control Center"
kill %1
```

Expected: La línea con `IBKR Control Center` aparece en la salida.

- [x] **Step 7: Crear frontend/Dockerfile (multi-stage prod build)**

`frontend/Dockerfile`:

```dockerfile
# syntax=docker/dockerfile:1.7
FROM node:20-alpine AS deps
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@latest --activate
COPY package.json pnpm-lock.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile

FROM node:20-alpine AS builder
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@latest --activate
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
RUN addgroup -S app && adduser -S app -G app
COPY --from=builder --chown=app:app /app/.next/standalone ./
COPY --from=builder --chown=app:app /app/.next/static ./.next/static
COPY --from=builder --chown=app:app /app/public ./public
USER app
EXPOSE 3000
CMD ["node", "server.js"]
```

- [x] **Step 8: Habilitar standalone output en Next.js**

`frontend/next.config.mjs`:

```js
/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
};
export default nextConfig;
```

- [x] **Step 9: Crear frontend/.dockerignore**

`frontend/.dockerignore`:

```
node_modules
.next
.git
.env
.env.*
.pnpm-store
test-results
playwright-report
```

- [x] **Step 10: Agregar servicio frontend a docker-compose.yml**

Editar `docker-compose.yml` para agregar el servicio frontend, justo antes del bloque `volumes`:

```yaml
  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    restart: unless-stopped
    environment:
      NEXT_PUBLIC_API_URL: http://localhost:8000/api
    ports:
      - "3000:3000"
    depends_on:
      - backend
```

- [x] **Step 11: Build + start full stack**

```bash
cd /Users/owner/Development/ibkr-control
docker compose up -d --build
sleep 15
curl -s http://localhost:3000 | grep "IBKR Control Center"
curl -s http://localhost:8000/health
```

Expected: La línea aparece + `{"status":"ok"}`.

- [x] **Step 12: Down + commit**

```bash
docker compose down
git add frontend/ docker-compose.yml
git commit -m "feat(frontend): Next.js + Tailwind + shadcn skeleton + Dockerfile prod"
```

---

### Task 8: OpenAPI client autogenerado con orval

**Files:**
- Create: `frontend/orval.config.ts`
- Create: `frontend/src/lib/api/index.ts` (re-export)
- Create: `frontend/src/lib/api/queryClient.ts`
- Create: `frontend/src/app/providers.tsx`
- Modify: `frontend/src/app/layout.tsx`
- Create: `frontend/scripts/fetch-openapi.sh`
- Create: `frontend/openapi.json` (generado, no commitear si querés, pero más simple commitearlo)

- [x] **Step 1: Levantar backend para bajar el schema**

```bash
cd /Users/owner/Development/ibkr-control
docker compose up -d backend postgres
sleep 5
```

- [x] **Step 2: Crear script para fetch del OpenAPI**

`frontend/scripts/fetch-openapi.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
OUT="$SCRIPT_DIR/../openapi.json"
URL="${OPENAPI_URL:-http://localhost:8000/openapi.json}"
curl -fsSL "$URL" -o "$OUT"
echo "Wrote $OUT"
```

```bash
chmod +x frontend/scripts/fetch-openapi.sh
./frontend/scripts/fetch-openapi.sh
```

Expected: `Wrote .../frontend/openapi.json`.

- [x] **Step 3: Crear orval.config.ts**

`frontend/orval.config.ts`:

```ts
import { defineConfig } from "orval";

export default defineConfig({
  api: {
    input: "./openapi.json",
    output: {
      target: "./src/lib/api/generated.ts",
      client: "react-query",
      httpClient: "axios",
      mode: "single",
      override: {
        mutator: {
          path: "./src/lib/api/mutator.ts",
          name: "axiosMutator",
        },
        query: {
          useQuery: true,
          useMutation: true,
        },
      },
    },
  },
});
```

- [x] **Step 4: Crear axios mutator (lee JWT del localStorage)**

`frontend/src/lib/api/mutator.ts`:

```ts
import Axios, { AxiosRequestConfig } from "axios";

const baseURL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";

export const axiosInstance = Axios.create({ baseURL });

axiosInstance.interceptors.request.use((cfg) => {
  if (typeof window !== "undefined") {
    const token = localStorage.getItem("auth_token");
    if (token) {
      cfg.headers = cfg.headers ?? {};
      cfg.headers["Authorization"] = `Bearer ${token}`;
    }
  }
  return cfg;
});

export const axiosMutator = <T>(config: AxiosRequestConfig): Promise<T> => {
  return axiosInstance.request<T, T>(config);
};

export default axiosMutator;
```

- [x] **Step 5: Correr orval**

```bash
cd /Users/owner/Development/ibkr-control/frontend
pnpm orval
```

Expected: `frontend/src/lib/api/generated.ts` creado, con hooks `useUsersUsersCurrentUser`, `useAuthJwtLoginAuthJwtLoginPost`, etc.

- [x] **Step 6: Crear queryClient + providers**

`frontend/src/lib/api/queryClient.ts`:

```ts
import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 60_000, retry: 1 },
  },
});
```

`frontend/src/app/providers.tsx`:

```tsx
"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { ReactNode } from "react";
import { queryClient } from "@/lib/api/queryClient";

export function Providers({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
```

- [x] **Step 7: Re-export del API en index.ts**

`frontend/src/lib/api/index.ts`:

```ts
export * from "./generated";
export { axiosInstance, axiosMutator } from "./mutator";
export { queryClient } from "./queryClient";
```

- [x] **Step 8: Wire Providers en layout**

`frontend/src/app/layout.tsx`:

```tsx
import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "IBKR Control Center",
  description: "Control center for IBKR investments and taxes",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
```

- [x] **Step 9: Agregar npm scripts**

Editar `frontend/package.json`, sección `scripts`:

```json
"scripts": {
  "dev": "next dev",
  "build": "next build",
  "start": "next start",
  "lint": "next lint",
  "openapi:fetch": "./scripts/fetch-openapi.sh",
  "openapi:gen": "pnpm openapi:fetch && orval"
}
```

- [x] **Step 10: Verificar build sin errores TS**

```bash
cd /Users/owner/Development/ibkr-control/frontend
pnpm build
```

Expected: Build OK, sin errores.

- [x] **Step 11: Commit**

```bash
cd /Users/owner/Development/ibkr-control
git add frontend/orval.config.ts frontend/scripts/ frontend/openapi.json \
        frontend/src/lib/ frontend/src/app/providers.tsx frontend/src/app/layout.tsx \
        frontend/package.json frontend/pnpm-lock.yaml
git commit -m "feat(frontend): orval-generated API client + TanStack Query provider"
```

---

### Task 9: Login + Register pages

**Files:**
- Create: `frontend/src/app/(auth)/layout.tsx`
- Create: `frontend/src/app/(auth)/login/page.tsx`
- Create: `frontend/src/app/(auth)/register/page.tsx`
- Create: `frontend/src/lib/auth/storeToken.ts`

- [ ] **Step 1: Crear helper para guardar token**

`frontend/src/lib/auth/storeToken.ts`:

```ts
const KEY = "auth_token";

export function storeToken(token: string) {
  if (typeof window === "undefined") return;
  localStorage.setItem(KEY, token);
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(KEY);
}

export function clearToken() {
  if (typeof window === "undefined") return;
  localStorage.removeItem(KEY);
}
```

- [ ] **Step 2: Crear layout del grupo (auth)**

`frontend/src/app/(auth)/layout.tsx`:

```tsx
import { ReactNode } from "react";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 p-4">
      <div className="w-full max-w-sm rounded-xl border bg-white p-6 shadow-sm">
        {children}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Crear página de login**

`frontend/src/app/(auth)/login/page.tsx`:

```tsx
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { axiosInstance } from "@/lib/api";
import { storeToken } from "@/lib/auth/storeToken";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const body = new URLSearchParams({ username: email, password });
      const response = await axiosInstance.post<{ access_token: string }>(
        "/auth/jwt/login",
        body,
        { headers: { "Content-Type": "application/x-www-form-urlencoded" } }
      );
      storeToken(response.data.access_token);
      router.push("/dashboard");
    } catch {
      setError("Credenciales inválidas");
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <h1 className="text-xl font-semibold">Iniciar sesión</h1>
      <div className="space-y-2">
        <Label htmlFor="email">Email</Label>
        <Input
          id="email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="password">Contraseña</Label>
        <Input
          id="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <Button type="submit" disabled={loading} className="w-full">
        {loading ? "Entrando…" : "Entrar"}
      </Button>
      <p className="text-center text-sm text-muted-foreground">
        ¿Sin cuenta? <a href="/register" className="underline">Registrate</a>
      </p>
    </form>
  );
}
```

- [ ] **Step 4: Crear página de register**

`frontend/src/app/(auth)/register/page.tsx`:

```tsx
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { axiosInstance } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export default function RegisterPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await axiosInstance.post("/auth/register", { email, password, name });
      router.push("/login");
    } catch {
      setError("No se pudo registrar (¿email ya existe?)");
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <h1 className="text-xl font-semibold">Crear cuenta</h1>
      <div className="space-y-2">
        <Label htmlFor="name">Nombre</Label>
        <Input id="name" value={name} onChange={(e) => setName(e.target.value)} required />
      </div>
      <div className="space-y-2">
        <Label htmlFor="email">Email</Label>
        <Input
          id="email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="password">Contraseña (min 8)</Label>
        <Input
          id="password"
          type="password"
          minLength={8}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <Button type="submit" disabled={loading} className="w-full">
        {loading ? "Creando…" : "Crear cuenta"}
      </Button>
      <p className="text-center text-sm text-muted-foreground">
        ¿Ya tenés? <a href="/login" className="underline">Entrar</a>
      </p>
    </form>
  );
}
```

- [ ] **Step 5: Smoke test manual**

```bash
cd /Users/owner/Development/ibkr-control
docker compose up -d --build
sleep 10
open http://localhost:3000/register
# Crear una cuenta de prueba, luego ir a /login y entrar
```

Expected: Registro funciona, login devuelve token, queda guardado en localStorage (verificable en DevTools).

- [ ] **Step 6: Commit**

```bash
docker compose down
git add frontend/src/app/\(auth\)/ frontend/src/lib/auth/
git commit -m "feat(frontend): login + register pages con JWT en localStorage"
```

---

### Task 10: Protected layout con sidebar (10 pantallas placeholder)

**Files:**
- Create: `frontend/src/app/(app)/layout.tsx`
- Create: `frontend/src/app/(app)/dashboard/page.tsx`
- Create: `frontend/src/app/(app)/lots/page.tsx`
- Create: `frontend/src/app/(app)/closed/page.tsx`
- Create: `frontend/src/app/(app)/alerts/page.tsx`
- Create: `frontend/src/app/(app)/simulator/page.tsx`
- Create: `frontend/src/app/(app)/dividends/page.tsx`
- Create: `frontend/src/app/(app)/patrimonio/page.tsx`
- Create: `frontend/src/app/(app)/form160/page.tsx`
- Create: `frontend/src/app/(app)/report/page.tsx`
- Create: `frontend/src/app/(app)/settings/page.tsx`
- Create: `frontend/src/components/sidebar.tsx`
- Create: `frontend/src/components/auth-gate.tsx`

- [ ] **Step 1: Crear AuthGate (client guard que redirige a /login si no hay token)**

`frontend/src/components/auth-gate.tsx`:

```tsx
"use client";

import { useEffect, useState, ReactNode } from "react";
import { useRouter } from "next/navigation";
import { getToken } from "@/lib/auth/storeToken";

export function AuthGate({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
    } else {
      setChecked(true);
    }
  }, [router]);

  if (!checked) return null;
  return <>{children}</>;
}
```

- [ ] **Step 2: Crear sidebar component**

`frontend/src/components/sidebar.tsx`:

```tsx
"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { clearToken } from "@/lib/auth/storeToken";

const items = [
  { href: "/dashboard", label: "Dashboard", icon: "📊" },
  { href: "/lots", label: "Lotes abiertos", icon: "📦" },
  { href: "/closed", label: "Cerrados", icon: "✓" },
  { href: "/alerts", label: "Alertas 730d", icon: "⚠" },
  { href: "/simulator", label: "Simulador", icon: "🧮" },
  { href: "/dividends", label: "Dividendos", icon: "💰" },
  { href: "/patrimonio", label: "Patrimonio", icon: "🏦" },
  { href: "/form160", label: "Form 160", icon: "🌍" },
  { href: "/report", label: "Reporte Form 210", icon: "📋" },
  { href: "/settings", label: "Settings", icon: "⚙" },
];

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();

  function logout() {
    clearToken();
    router.replace("/login");
  }

  return (
    <aside className="flex h-screen w-56 flex-col border-r bg-slate-50 p-3">
      <div className="px-2 pb-4 text-sm font-semibold">IBKR Control</div>
      <nav className="flex-1 space-y-1">
        {items.map((it) => {
          const active = pathname?.startsWith(it.href);
          return (
            <Link
              key={it.href}
              href={it.href}
              className={`flex items-center gap-2 rounded px-3 py-2 text-sm ${
                active ? "bg-blue-500 text-white" : "text-slate-700 hover:bg-slate-200"
              }`}
            >
              <span className="w-5 text-center">{it.icon}</span>
              <span>{it.label}</span>
            </Link>
          );
        })}
      </nav>
      <button
        onClick={logout}
        className="mt-2 rounded px-3 py-2 text-left text-sm text-slate-600 hover:bg-slate-200"
      >
        Salir
      </button>
    </aside>
  );
}
```

- [ ] **Step 3: Crear layout protegido**

`frontend/src/app/(app)/layout.tsx`:

```tsx
import { ReactNode } from "react";
import { Sidebar } from "@/components/sidebar";
import { AuthGate } from "@/components/auth-gate";

export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <AuthGate>
      <div className="flex min-h-screen">
        <Sidebar />
        <main className="flex-1 bg-white p-6">{children}</main>
      </div>
    </AuthGate>
  );
}
```

- [ ] **Step 4: Crear 10 páginas placeholder (mismo patrón)**

Para cada una de las 10 páginas (dashboard, lots, closed, alerts, simulator, dividends, patrimonio, form160, report, settings), crear el archivo `frontend/src/app/(app)/<slug>/page.tsx`:

```tsx
export default function Page() {
  return (
    <div>
      <h1 className="text-2xl font-semibold">{/* nombre humano */}</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        Pantalla pendiente de implementar en fase siguiente.
      </p>
    </div>
  );
}
```

Reemplazar `{/* nombre humano */}` con: `Dashboard`, `Lotes abiertos`, `Cerrados`, `Alertas 730d`, `Simulador`, `Dividendos`, `Patrimonio`, `Form 160`, `Reporte Form 210`, `Settings`.

(Settings se sobreescribe en Task 11 con contenido real.)

- [ ] **Step 5: Smoke test full flow**

```bash
cd /Users/owner/Development/ibkr-control
docker compose up -d --build
sleep 10
open http://localhost:3000/register
# 1. Registrar
# 2. Login
# 3. Verificar redirect a /dashboard
# 4. Click cada item de la sidebar, verificar que carga
# 5. Click "Salir" → vuelve a /login
```

- [ ] **Step 6: Commit**

```bash
docker compose down
git add frontend/src/app/\(app\)/ frontend/src/components/
git commit -m "feat(frontend): protected layout + sidebar + 10 placeholder pages"
```

---

### Task 11: Pantalla Settings funcional (marginal_rate editable)

**Files:**
- Modify: `frontend/src/app/(app)/settings/page.tsx`

- [ ] **Step 1: Reescribir settings/page.tsx con datos reales**

`frontend/src/app/(app)/settings/page.tsx` (reemplazar):

```tsx
"use client";

import { useEffect, useState } from "react";
import { axiosInstance } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface Settings {
  marginal_rate: string;
  timezone: string;
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [rate, setRate] = useState("");
  const [tz, setTz] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    axiosInstance.get<Settings>("/settings").then((r) => {
      setSettings(r.data);
      setRate(r.data.marginal_rate);
      setTz(r.data.timezone);
    });
  }, []);

  async function save() {
    setSaving(true);
    setMessage(null);
    try {
      const r = await axiosInstance.patch<Settings>("/settings", {
        marginal_rate: rate,
        timezone: tz,
      });
      setSettings(r.data);
      setMessage("Guardado");
    } catch {
      setMessage("Error al guardar");
    } finally {
      setSaving(false);
    }
  }

  if (!settings) return <p className="text-sm text-muted-foreground">Cargando…</p>;

  return (
    <div className="max-w-md space-y-6">
      <h1 className="text-2xl font-semibold">Settings</h1>
      <div className="space-y-2">
        <Label htmlFor="rate">
          Tarifa marginal RO (0–1, ej. 0.39 para 39%)
        </Label>
        <Input
          id="rate"
          value={rate}
          onChange={(e) => setRate(e.target.value)}
          placeholder="0.39"
        />
        <p className="text-xs text-muted-foreground">
          Usada por el simulador para estimar impuesto cuando un cierre es Renta Ordinaria.
        </p>
      </div>
      <div className="space-y-2">
        <Label htmlFor="tz">Timezone</Label>
        <Input id="tz" value={tz} onChange={(e) => setTz(e.target.value)} />
      </div>
      {message && <p className="text-sm">{message}</p>}
      <Button onClick={save} disabled={saving}>
        {saving ? "Guardando…" : "Guardar"}
      </Button>
    </div>
  );
}
```

- [ ] **Step 2: Smoke test**

```bash
cd /Users/owner/Development/ibkr-control
docker compose up -d --build
sleep 10
# En el browser: login → /settings → cambiar marginal_rate a 0.33 → Guardar
# → refrescar la página → debe seguir mostrando 0.3300
```

- [ ] **Step 3: Commit**

```bash
docker compose down
git add frontend/src/app/\(app\)/settings/page.tsx
git commit -m "feat(frontend): pantalla Settings funcional con marginal_rate editable"
```

---

### Task 12: E2E test con Playwright

**Files:**
- Create: `frontend/playwright.config.ts`
- Create: `frontend/e2e/auth-flow.spec.ts`

- [ ] **Step 1: Instalar browsers Playwright**

```bash
cd /Users/owner/Development/ibkr-control/frontend
pnpm exec playwright install chromium
```

- [ ] **Step 2: Crear playwright.config.ts**

`frontend/playwright.config.ts`:

```ts
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: "http://localhost:3000",
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
```

- [ ] **Step 3: Escribir test E2E del flow completo**

`frontend/e2e/auth-flow.spec.ts`:

```ts
import { test, expect } from "@playwright/test";

test("register → login → settings update → logout flow", async ({ page }) => {
  const ts = Date.now();
  const email = `user${ts}@example.com`;
  const password = "supersecret123";

  // Register
  await page.goto("/register");
  await page.getByLabel("Nombre").fill("Test Owner Test");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel(/Contraseña/).fill(password);
  await page.getByRole("button", { name: /Crear cuenta/i }).click();

  // Tras registro va a /login
  await expect(page).toHaveURL(/\/login/);

  // Login
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Contraseña").fill(password);
  await page.getByRole("button", { name: /Entrar/i }).click();
  await expect(page).toHaveURL(/\/dashboard/);

  // Sidebar visible
  await expect(page.getByText("IBKR Control")).toBeVisible();

  // Ir a Settings, cambiar marginal_rate
  await page.getByRole("link", { name: /Settings/i }).click();
  await expect(page).toHaveURL(/\/settings/);
  const rateInput = page.getByLabel(/Tarifa marginal/i);
  await rateInput.fill("0.33");
  await page.getByRole("button", { name: /Guardar/i }).click();
  await expect(page.getByText("Guardado")).toBeVisible();

  // Refrescar y verificar persistencia
  await page.reload();
  await expect(rateInput).toHaveValue("0.3300");

  // Logout
  await page.getByRole("button", { name: /Salir/i }).click();
  await expect(page).toHaveURL(/\/login/);
});
```

- [ ] **Step 4: Agregar npm script y correr**

Editar `frontend/package.json` scripts:

```json
"e2e": "playwright test"
```

Arrancar full stack y correr E2E:

```bash
cd /Users/owner/Development/ibkr-control
docker compose up -d --build
sleep 15
cd frontend
pnpm e2e
```

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/owner/Development/ibkr-control
docker compose down
git add frontend/playwright.config.ts frontend/e2e/ frontend/package.json
git commit -m "test(e2e): Playwright covering register → login → settings → logout"
```

---

### Task 13: CI mínimo con GitHub Actions

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Crear workflow CI**

`/Users/owner/Development/ibkr-control/.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          version: "0.5.4"
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install deps
        run: |
          cd backend
          uv sync --frozen
      - name: Pytest
        run: |
          cd backend
          uv run pytest -v

  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
        with:
          version: 9
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: "pnpm"
          cache-dependency-path: frontend/pnpm-lock.yaml
      - name: Install deps
        run: |
          cd frontend
          pnpm install --frozen-lockfile
      - name: Build
        run: |
          cd frontend
          pnpm build
```

- [ ] **Step 2: Commit (E2E queda fuera del CI por requerir Postgres + servicios; corre local)**

```bash
git add .github/
git commit -m "ci: github actions backend pytest + frontend build"
```

---

### Task 14: Coolify deploy config + docs

**Files:**
- Create: `docs/deploy.md`
- Modify: `docker-compose.yml` (separar compose dev vs deploy)
- Create: `docker-compose.coolify.yml`

- [ ] **Step 1: Crear docker-compose.coolify.yml (con env de Coolify)**

`/Users/owner/Development/ibkr-control/docker-compose.coolify.yml`:

```yaml
services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 5s
      timeout: 5s
      retries: 10

  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    restart: unless-stopped
    environment:
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
      JWT_SECRET: ${JWT_SECRET}
      JWT_LIFETIME_SECONDS: ${JWT_LIFETIME_SECONDS:-3600}
      BACKEND_CORS_ORIGINS: ${BACKEND_CORS_ORIGINS}
    depends_on:
      postgres:
        condition: service_healthy
    labels:
      - "coolify.managed=true"

  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    restart: unless-stopped
    environment:
      NEXT_PUBLIC_API_URL: ${PUBLIC_API_URL}
    depends_on:
      - backend
    labels:
      - "coolify.managed=true"

volumes:
  postgres_data:
```

- [ ] **Step 2: Crear docs/deploy.md**

`/Users/owner/Development/ibkr-control/docs/deploy.md`:

```markdown
# Deploy a Coolify

## Pre-requisitos
- Coolify ≥ v4 corriendo en tu Hetzner
- Dominio configurado (ej. ibkr.tudominio.com)
- DNS apuntando al servidor Coolify

## Setup inicial en Coolify

1. **New Resource → Docker Compose** (no "Application").
2. **Source**: conectar a este repo (GitHub/GitLab/self-hosted git).
3. **Compose file**: `docker-compose.coolify.yml`.
4. **Branch**: `main`.
5. **Environment variables** (en Coolify, sección Secrets):
   - `POSTGRES_USER` = ibkr
   - `POSTGRES_PASSWORD` = <generar: `openssl rand -hex 24`>
   - `POSTGRES_DB` = ibkr_control
   - `JWT_SECRET` = <generar: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`>
   - `JWT_LIFETIME_SECONDS` = 3600
   - `BACKEND_CORS_ORIGINS` = https://ibkr.tudominio.com
   - `PUBLIC_API_URL` = https://ibkr.tudominio.com/api
6. **Domains**:
   - frontend → `ibkr.tudominio.com` (puerto 3000)
   - backend → `ibkr.tudominio.com/api` (puerto 8000, path `/api`)
   Coolify maneja SSL automático via Let's Encrypt.
7. **Deploy**: click Deploy. Primer build tarda ~5 min.

## Migraciones DB

Las migrations corren manualmente la primera vez:

```bash
# Desde el server con Coolify, identificar el contenedor backend
docker ps | grep backend
docker exec -it <backend_container_id> uv run alembic upgrade head
```

Para futuras releases con migrations nuevas, repetir el comando o
agregarlo como `pre-deploy command` en Coolify (V2).

## Health check post-deploy

```bash
curl https://ibkr.tudominio.com/api/health
# Esperar: {"status":"ok"}
```
```

- [ ] **Step 3: Commit + tag de release v0.1.0-foundation**

```bash
cd /Users/owner/Development/ibkr-control
git add docker-compose.coolify.yml docs/deploy.md
git commit -m "feat(deploy): docker-compose.coolify.yml + docs/deploy.md"
git tag v0.1.0-foundation
```

---

### Task 15: Smoke test deploy a Coolify (manual)

Esta tarea no agrega código — es la verificación end-to-end de que el deploy funciona.

- [ ] **Step 1: Push a tu remote git**

```bash
cd /Users/owner/Development/ibkr-control
# Si no hay remote configurado:
# gh repo create ibkr-control --private --source=. --remote=origin
git remote -v
git push -u origin main
git push --tags
```

- [ ] **Step 2: Configurar el resource en Coolify**

Seguir los pasos de `docs/deploy.md` Setup inicial.

- [ ] **Step 3: Esperar primer build**

Watch Coolify build logs. Si falla, revisar errores y aplicar fix (probable: env vars mal configuradas).

- [ ] **Step 4: Correr migrations**

Ver `docs/deploy.md` sección "Migraciones DB".

- [ ] **Step 5: Smoke test contra dominio real**

```bash
DOMAIN=https://ibkr.tudominio.com   # reemplazar
curl -s $DOMAIN/api/health
# Esperar: {"status":"ok"}

# Registrar usuario
curl -s -X POST $DOMAIN/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"Test Owner@tudominio.com","password":"<password>","name":"Test Owner"}'

# Login
curl -s -X POST $DOMAIN/api/auth/jwt/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=Test Owner@tudominio.com&password=<password>"
```

- [ ] **Step 6: Abrir el dominio en el browser**

```bash
open $DOMAIN
```

Verificar: login → dashboard → sidebar funcionando → settings editable.

- [ ] **Step 7: Documentar el URL de prod en README**

Editar `README.md` agregando una línea al inicio:

```markdown
**Prod:** https://ibkr.tudominio.com
```

Commit + push:

```bash
git add README.md
git commit -m "docs: add prod URL to README"
git push
```

---

## Phase 1 entregables (checklist final)

- [ ] Repo `ibkr-control/` creado con estructura backend + frontend
- [ ] FastAPI con `/health` + fastapi-users (register/login/me/users)
- [ ] Postgres 16 con users + user_settings tables (Alembic managed)
- [ ] UserSettings auto-creado en register, editable via PATCH /api/settings
- [ ] Next.js 14 con login + register + protected layout + sidebar + 10 placeholders
- [ ] Settings page funcional con marginal_rate editable
- [ ] Orval-generated TypeScript client del OpenAPI
- [ ] E2E Playwright test cubriendo register → login → settings → logout
- [ ] GitHub Actions CI corriendo pytest + next build
- [ ] docker-compose dev (`docker-compose.yml`) + Coolify (`docker-compose.coolify.yml`)
- [ ] App deployada en Coolify con SSL via Let's Encrypt
- [ ] Tag `v0.1.0-foundation`

## Siguiente

Phase 2 (Data Ingestion) tendrá su propio plan basado en lo aprendido en Phase 1. Cubrirá:
- Tablas `accounts`, `participations`, `flex_credentials`, `flex_imports`, `trades`, `closed_lots`, `open_position_lots`, `transfers`, `cash_transactions`, `trm_days`
- Cliente Flex Web Service (SendRequest + Poll)
- Cliente Socrata TRM con paginación
- APScheduler con jobs diarios (07:00 Flex + 19:30 TRM)
- Upload XML manual + dedup por hash
- Setup wizard (4 pasos)
- Botón "Actualizar ahora" con rate limit
