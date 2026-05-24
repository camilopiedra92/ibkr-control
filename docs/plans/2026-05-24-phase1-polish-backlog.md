# Phase 1 Polish Backlog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cerrar los 6 items pendientes del Polish Backlog de Phase 1 (CLAUDE.md) antes de tagear `v0.1.0-foundation` y arrancar Phase 2.

**Architecture:** Cambios quirúrgicos en código existente del backend (Pydantic schemas, FastAPI app factory, SQLAlchemy session, fastapi-users manager) más un test de migrations y endurecimiento del Dockerfile/compose. Cada task es independiente, TDD-first, y cierra en un commit propio.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.x async, Alembic, fastapi-users, pytest + testcontainers, Docker.

**Convenciones (heredadas de CLAUDE.md):**
- TDD: failing test → minimal impl → passing test → commit
- Decimal para dinero, no float
- No emojis en código
- No capturar `settings = get_settings()` a nivel módulo (esta convención se está haciendo cumplir en este plan)
- Cada task termina en `git commit`. Tras el último commit, marcar el item correspondiente como completado en CLAUDE.md ("Polish backlog" section).

**Working directory para todos los comandos:** `/Users/owner/Development/ibkr-control`

**Antes de empezar:** asegurarse de que el container de Postgres no esté corriendo si fuera a interferir (los tests levantan testcontainers propios). Verificar baseline:

```bash
cd backend && uv run pytest -v
```
Expected: todos los tests verdes (suite actual).

---

## Task 1: `marginal_rate` con `max_digits=5, decimal_places=4` (no silent rounding)

**Files:**
- Modify: `backend/src/ibkr_control/settings/schemas.py:13`
- Modify: `backend/tests/test_settings.py` (agregar 1 test)

**Contexto:** El modelo SQLAlchemy declara `Numeric(5, 4)` (5 dígitos totales, 4 decimales — rango efectivo 0.0000–9.9999). Pydantic actualmente solo valida `gt=0, lt=1`. Si el cliente envía `"0.123456"`, Pydantic lo acepta y SQLAlchemy/Postgres lo redondea silenciosamente a `0.1235` en INSERT/UPDATE — el usuario nunca ve el cambio. Queremos 422 en vez de silent rounding.

- [ ] **Step 1: Escribir el test que demuestra el silent rounding actual**

Agregar al final de `backend/tests/test_settings.py`:

```python
async def test_settings_patch_rejects_excess_decimal_places(client):
    # max_digits=5, decimal_places=4 en el schema debe rechazar 5+ decimales
    # antes de pegarle a Postgres, que de otro modo redondea silenciosamente.
    token = await _register_and_login(client)
    response = await client.patch(
        "/api/settings",
        json={"marginal_rate": "0.12345"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422
```

- [ ] **Step 2: Correr el test y verificar que falla**

```bash
cd backend && uv run pytest tests/test_settings.py::test_settings_patch_rejects_excess_decimal_places -v
```
Expected: FAIL — el response es 200 con `marginal_rate` redondeado a `"0.1235"`.

- [ ] **Step 3: Aplicar el constraint en el schema**

En `backend/src/ibkr_control/settings/schemas.py`, reemplazar la línea 13 actual:

```python
    marginal_rate: Decimal | None = Field(default=None, gt=Decimal("0"), lt=Decimal("1"))
```

por:

```python
    marginal_rate: Decimal | None = Field(
        default=None,
        gt=Decimal("0"),
        lt=Decimal("1"),
        max_digits=5,
        decimal_places=4,
    )
```

- [ ] **Step 4: Correr la suite de settings y confirmar PASS**

```bash
cd backend && uv run pytest tests/test_settings.py -v
```
Expected: todos PASS, incluyendo el nuevo `test_settings_patch_rejects_excess_decimal_places`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ibkr_control/settings/schemas.py backend/tests/test_settings.py
git commit -m "fix(settings): reject excess decimal places in marginal_rate (no silent rounding)"
```

---

## Task 2: `timezone` validado contra `zoneinfo.available_timezones()`

**Files:**
- Modify: `backend/src/ibkr_control/settings/schemas.py`
- Modify: `backend/tests/test_settings.py` (agregar 2 tests)

**Contexto:** El campo `timezone` hoy es `str | None` libre. Phase 2 va a usar este valor para localizar el job APScheduler de las 07:00 — un string inválido (`"Foo/Bar"`) hace que el scheduler **crashee silenciosamente al boot**, no al guardar. Mejor validar en la frontera Pydantic.

- [ ] **Step 1: Escribir los tests (válido + inválido)**

Agregar al final de `backend/tests/test_settings.py`:

```python
async def test_settings_patch_accepts_valid_timezone(client):
    token = await _register_and_login(client)
    response = await client.patch(
        "/api/settings",
        json={"timezone": "Europe/Madrid"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["timezone"] == "Europe/Madrid"


async def test_settings_patch_rejects_invalid_timezone(client):
    # Prerequisito de Phase 2: APScheduler crashea silenciosamente al boot si
    # el TZ no es reconocido por zoneinfo. Validar en la frontera (422), no
    # en runtime.
    token = await _register_and_login(client)
    response = await client.patch(
        "/api/settings",
        json={"timezone": "Foo/Bar"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422
```

- [ ] **Step 2: Correr los tests nuevos y verificar que `rejects_invalid_timezone` falla**

```bash
cd backend && uv run pytest tests/test_settings.py::test_settings_patch_accepts_valid_timezone tests/test_settings.py::test_settings_patch_rejects_invalid_timezone -v
```
Expected: `accepts_valid_timezone` PASS, `rejects_invalid_timezone` FAIL (porque el schema acepta cualquier string).

- [ ] **Step 3: Agregar el validator al schema**

Reemplazar el contenido completo de `backend/src/ibkr_control/settings/schemas.py` con:

```python
from decimal import Decimal
from zoneinfo import available_timezones

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UserSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    marginal_rate: Decimal
    timezone: str


class UserSettingsUpdate(BaseModel):
    marginal_rate: Decimal | None = Field(
        default=None,
        gt=Decimal("0"),
        lt=Decimal("1"),
        max_digits=5,
        decimal_places=4,
    )
    timezone: str | None = None

    @field_validator("timezone")
    @classmethod
    def _timezone_must_be_zoneinfo(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v not in available_timezones():
            raise ValueError(f"unknown timezone: {v!r}")
        return v
```

- [ ] **Step 4: Correr la suite de settings completa y confirmar PASS**

```bash
cd backend && uv run pytest tests/test_settings.py -v
```
Expected: todos PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ibkr_control/settings/schemas.py backend/tests/test_settings.py
git commit -m "fix(settings): validate timezone against zoneinfo.available_timezones (prereq APScheduler)"
```

---

## Task 3: CORS wildcard guard (rechazar `BACKEND_CORS_ORIGINS=*` en boot)

**Files:**
- Modify: `backend/src/ibkr_control/config.py`
- Create: `backend/tests/test_config.py`

**Contexto:** CORSMiddleware con `allow_origins=["*"]` + `allow_credentials=True` hace que los browsers fallen silenciosamente (la spec prohíbe la combinación). En `main.py` actualmente filtramos por `cors_origins_list` que parsea CSV — si alguien setea `BACKEND_CORS_ORIGINS=*` (caso típico en Coolify mal configurado), la lista contiene `["*"]` y se monta el middleware con esa config rota. Mejor fallar al boot (uvicorn no arranca) que en runtime.

- [ ] **Step 1: Escribir los tests en archivo nuevo**

Crear `backend/tests/test_config.py` con:

```python
import pytest
from pydantic import ValidationError

from ibkr_control.config import Settings, get_settings


def test_settings_rejects_cors_wildcard(monkeypatch):
    # CORSMiddleware con allow_origins=["*"] + allow_credentials=True es spec-
    # invalid (browsers fallan silenciosamente). Fallar al boot en vez de en la
    # primera request.
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")
    monkeypatch.setenv("BACKEND_CORS_ORIGINS", "*")
    get_settings.cache_clear()
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_settings_rejects_cors_wildcard_within_csv(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")
    monkeypatch.setenv("BACKEND_CORS_ORIGINS", "http://localhost:3000,*")
    get_settings.cache_clear()
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_settings_accepts_concrete_origins(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")
    monkeypatch.setenv("BACKEND_CORS_ORIGINS", "http://localhost:3000,https://app.example.com")
    get_settings.cache_clear()
    s = Settings()  # type: ignore[call-arg]
    assert s.cors_origins_list == ["http://localhost:3000", "https://app.example.com"]


def test_settings_accepts_empty_cors(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")
    monkeypatch.setenv("BACKEND_CORS_ORIGINS", "")
    get_settings.cache_clear()
    s = Settings()  # type: ignore[call-arg]
    assert s.cors_origins_list == []
```

- [ ] **Step 2: Correr los tests y verificar que los 2 de wildcard fallan**

```bash
cd backend && uv run pytest tests/test_config.py -v
```
Expected: `accepts_concrete_origins` y `accepts_empty_cors` PASS; `rejects_cors_wildcard` y `rejects_cors_wildcard_within_csv` FAIL (no se lanza ValidationError).

- [ ] **Step 3: Agregar el validator en config.py**

Reemplazar el contenido completo de `backend/src/ibkr_control/config.py` con:

```python
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str
    jwt_secret: str
    jwt_lifetime_seconds: int = 3600
    backend_cors_origins: str = ""

    @field_validator("backend_cors_origins")
    @classmethod
    def _no_cors_wildcard(cls, v: str) -> str:
        # CORSMiddleware con allow_origins=["*"] y allow_credentials=True es
        # spec-invalid: el browser rechaza la response silenciosamente. Fallar
        # al boot en vez de en runtime. Si alguna vez se quiere wildcard,
        # también hay que desactivar credentials — decisión consciente, no
        # accidental por env var mal seteado.
        origins = [o.strip() for o in v.split(",") if o.strip()]
        if "*" in origins:
            raise ValueError(
                "BACKEND_CORS_ORIGINS='*' is incompatible with allow_credentials=True; "
                "list explicit origins instead"
            )
        return v

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.backend_cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
```

- [ ] **Step 4: Correr la suite completa y confirmar nada se rompió**

```bash
cd backend && uv run pytest -v
```
Expected: todos PASS (incluyendo los 4 nuevos de test_config.py).

- [ ] **Step 5: Commit**

```bash
git add backend/src/ibkr_control/config.py backend/tests/test_config.py
git commit -m "feat(config): reject CORS wildcard at boot (incompatible with credentials)"
```

---

## Task 4: Refactor module-level `get_settings()` capture

**Files:**
- Modify: `backend/src/ibkr_control/db/session.py`
- Modify: `backend/src/ibkr_control/auth/manager.py`
- Modify: `backend/src/ibkr_control/auth/backend.py`

**Contexto:** Los 3 archivos hacen `settings = get_settings()` a nivel módulo. El bug: `@lru_cache` cachea la primera invocación; si los tests hacen `monkeypatch.setenv` + `get_settings.cache_clear()`, los **módulos ya importados siguen viendo el objeto Settings viejo** capturado en sus globals — `cache_clear()` solo afecta la próxima llamada, no las referencias ya tomadas. Hoy los tests pasan por coincidencia (valores idénticos). En Phase 2 esto va a producir bugs sutiles.

**Patrones de fix por archivo:**

- `db/session.py`: convertir `engine` y `async_session_maker` a factories con `@lru_cache` (singleton lazy por proceso, pero re-evaluable después de un `cache_clear()`).
- `auth/manager.py`: `reset_password_token_secret` y `verification_token_secret` son **atributos de clase evaluados a definition-time** — convertir a `@property` (lookup por instancia).
- `auth/backend.py`: `get_jwt_strategy()` ya es función; basta inlinearle `get_settings()`.

**No hay test nuevo dedicado** — la suite completa actual es el regression test (especialmente los flows de auth y settings que ejercen JWT y session).

- [ ] **Step 1: Refactor `db/session.py`**

Reemplazar contenido completo de `backend/src/ibkr_control/db/session.py` con:

```python
from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ibkr_control.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, echo=False, future=True)


@lru_cache
def get_session_maker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with get_session_maker()() as session:
        yield session
```

- [ ] **Step 2: Refactor `auth/manager.py`**

Reemplazar contenido completo de `backend/src/ibkr_control/auth/manager.py` con:

```python
from collections.abc import AsyncGenerator

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, IntegerIDMixin
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.auth.models import User
from ibkr_control.config import get_settings
from ibkr_control.db.session import get_async_session
from ibkr_control.settings.models import UserSettings


async def get_user_db(
    session: AsyncSession = Depends(get_async_session),
) -> AsyncGenerator[SQLAlchemyUserDatabase, None]:
    yield SQLAlchemyUserDatabase(session, User)


class UserManager(IntegerIDMixin, BaseUserManager[User, int]):
    # Properties (no class attributes): leer get_settings() en cada acceso para
    # que monkeypatch en tests no caiga sobre referencias capturadas a import-time.
    @property
    def reset_password_token_secret(self) -> str:  # type: ignore[override]
        return get_settings().jwt_secret

    @property
    def verification_token_secret(self) -> str:  # type: ignore[override]
        return get_settings().jwt_secret

    async def on_after_register(self, user: User, request: Request | None = None) -> None:
        session: AsyncSession = self.user_db.session  # type: ignore[attr-defined]
        session.add(UserSettings(user_id=user.id))
        await session.commit()


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
) -> AsyncGenerator[UserManager, None]:
    yield UserManager(user_db)
```

- [ ] **Step 3: Refactor `auth/backend.py`**

Reemplazar contenido completo de `backend/src/ibkr_control/auth/backend.py` con:

```python
from fastapi_users import FastAPIUsers
from fastapi_users.authentication import AuthenticationBackend, BearerTransport, JWTStrategy

from ibkr_control.auth.manager import get_user_manager
from ibkr_control.auth.models import User
from ibkr_control.config import get_settings

bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")


def get_jwt_strategy() -> JWTStrategy:
    s = get_settings()
    return JWTStrategy(secret=s.jwt_secret, lifetime_seconds=s.jwt_lifetime_seconds)


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, int](get_user_manager, [auth_backend])

current_active_user = fastapi_users.current_user(active=True)
```

- [ ] **Step 4: Correr la suite completa**

```bash
cd backend && uv run pytest -v
```
Expected: todos PASS. Si test_auth o test_settings fallan, es señal de que el refactor de manager.py rompió algo — verificar que `@property` esté bien decorado y que no haya colisión con atributos de la base class.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ibkr_control/db/session.py backend/src/ibkr_control/auth/manager.py backend/src/ibkr_control/auth/backend.py
git commit -m "refactor(backend): defer get_settings() to call-time (avoid stale module-level capture)"
```

---

## Task 5: Test de migrations (`alembic upgrade head` contra container fresh)

**Files:**
- Create: `backend/tests/test_migrations.py`

**Contexto:** El plan Phase 1 dijo "first migration applies cleanly", pero solo se verificó manualmente con `alembic current`. Necesitamos un test automático que:
1. Levante un Postgres fresco (vacío).
2. Corra `alembic upgrade head` programáticamente apuntándolo a ese container.
3. Reflexione el schema resultante y verifique que las tablas declaradas en `Base.metadata` existen.

**Decisiones de diseño:**
- Usar `alembic.command.upgrade` con un `Config` construido en memoria (evita subprocess y heredamos el venv).
- Usar `PostgresContainer` con scope function (NO compartir con la fixture de session — porque las migrations crean tablas, y queremos un container limpio).
- Reflejar via `inspect()` síncrono (alembic ya usa engine síncrono internamente para `op.create_table` — no necesitamos async aquí).

- [ ] **Step 1: Agregar `psycopg2-binary` como dev dep**

`testcontainers[postgres]>=4.8.0` NO trae psycopg2 transitivamente (verificado: `import psycopg2` falla en el venv actual). El test usa el driver sync para inspeccionar el schema reflejado post-migration, así que necesitamos psycopg2-binary explícito.

```bash
cd backend && uv add --dev psycopg2-binary
```
Verificar:
```bash
cd backend && uv run python -c "import psycopg2; print('OK', psycopg2.__version__)"
```
Expected: `OK 2.9.x`.

- [ ] **Step 2: Escribir el test (sync, no async)**

**Importante:** el test es `def` SIN `async`, aunque pytest-asyncio esté en `auto` mode. Razón: `command.upgrade()` (alembic) invoca `asyncio.run(run_async_migrations())` dentro de `env.py`. Si el test es `async def`, ya hay un event loop corriendo → `asyncio.run` lanza `RuntimeError: cannot be called from a running event loop`. Manteniéndolo sync evitamos el deadlock y obtenemos un test más simple.

Crear `backend/tests/test_migrations.py` con:

```python
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from testcontainers.postgres import PostgresContainer

import ibkr_control.db  # noqa: F401  (carga modelos en Base.metadata)
from ibkr_control.db.base import Base


@pytest.fixture
def fresh_postgres():
    # Container dedicado al test de migrations: garantizamos DB vacía.
    # No reusar el session-scoped del conftest porque ese crea tablas via
    # Base.metadata.create_all (otra ruta, no alembic).
    with PostgresContainer("postgres:16-alpine", driver="psycopg2") as pg:
        yield pg


def test_migrations_apply_cleanly_and_match_metadata(fresh_postgres, monkeypatch):
    sync_url = fresh_postgres.get_connection_url()
    async_url = sync_url.replace("+psycopg2", "+asyncpg")

    # env.py lee get_settings().database_url y se lo pasa a async_engine_from_config.
    monkeypatch.setenv("DATABASE_URL", async_url)
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")
    from ibkr_control.config import get_settings
    get_settings.cache_clear()

    backend_root = Path(__file__).resolve().parents[1]
    alembic_ini = backend_root / "alembic.ini"
    assert alembic_ini.exists(), f"alembic.ini missing at {alembic_ini}"

    cfg = Config(str(alembic_ini))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))

    command.upgrade(cfg, "head")

    # Inspect via sync driver — más simple que async run_sync para esta verificación.
    engine = create_engine(sync_url)
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    engine.dispose()

    expected = set(Base.metadata.tables.keys())
    extra_alembic_tables = existing - expected - {"alembic_version"}
    missing = expected - existing

    assert not missing, f"tablas en Base.metadata faltantes tras upgrade head: {missing}"
    assert not extra_alembic_tables, (
        f"tablas creadas por migrations pero NO declaradas en Base.metadata "
        f"(drift): {extra_alembic_tables}"
    )
```

- [ ] **Step 3: Correr el test y verificar PASS**

```bash
cd backend && uv run pytest tests/test_migrations.py -v
```
Expected: PASS. Si falla con "tablas faltantes" o "drift", revisar `alembic/versions/` — significa que las revisions existentes no representan el schema actual de `Base.metadata` (problema real que el test acaba de descubrir).

Posibles fallas tempranas:
- `Config` no encuentra `script_location`: revisar que la ruta sea correcta relativa a `backend/`.
- Container no arranca: Docker no está corriendo o puerto ocupado.

- [ ] **Step 4: Correr toda la suite (para verificar que no rompimos nada)**

```bash
cd backend && uv run pytest -v
```
Expected: todos PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_migrations.py backend/pyproject.toml backend/uv.lock
git commit -m "test(migrations): assert alembic upgrade head matches Base.metadata on fresh DB"
```

---

## Task 6: Dockerfile USER non-root + quitar host-port de Postgres en dev

**Files:**
- Modify: `backend/Dockerfile`
- Modify: `docker-compose.yml`

**Contexto:** Dos hardenings para deploy a Coolify:
1. El container del backend corre como root — recomendación de code reviewer (Task 3 Phase 1) fue diferir a Task 14 (deploy). Lo cerramos ahora.
2. `docker-compose.yml` expone `5432` al host (`ports: - "${POSTGRES_PORT:-5432}:5432"`), lo cual está bien para psql local pero **NO debe quedar así en `docker-compose.coolify.yml`** (ya no lo tiene — verificar). En `docker-compose.yml` de dev también lo quitamos: si querés conectar con un cliente SQL local, levantá un override personal o usá `docker compose exec postgres psql`.

**Validación:** este task no tiene test automático — verificamos manualmente que `docker compose up --build` levanta y `curl http://localhost:8000/health` responde 200.

- [ ] **Step 1: Editar `backend/Dockerfile` para correr como `appuser`**

Reemplazar contenido completo de `backend/Dockerfile` con:

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

RUN groupadd --system --gid 1001 appuser \
    && useradd --system --uid 1001 --gid appuser --create-home \
       --home-dir /home/appuser --shell /usr/sbin/nologin appuser

WORKDIR /app
RUN chown appuser:appuser /app

USER appuser

COPY --chown=appuser:appuser pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/home/appuser/.cache/uv,uid=1001,gid=1001 \
    uv sync --frozen --no-install-project --no-dev

COPY --chown=appuser:appuser src/ ./src/

RUN --mount=type=cache,target=/home/appuser/.cache/uv,uid=1001,gid=1001 \
    uv sync --frozen --no-dev

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "ibkr_control.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Notas sobre los cambios:
- `groupadd`/`useradd` con UID/GID fijos 1001 (más predecible que dejar al OS asignar).
- `chown` de `/app` antes de cambiar a `USER appuser`, sino los `COPY` no podrían escribir.
- `--mount=type=cache` ahora apunta a `/home/appuser/.cache/uv` con `uid=1001,gid=1001` para que el cache sea accesible por el user no-root.
- `--chown=appuser:appuser` en los `COPY` para que los archivos copiados sean propiedad del user no-root.

- [ ] **Step 2: Editar `docker-compose.yml` para no exponer Postgres al host**

Reemplazar contenido completo de `docker-compose.yml` con:

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
      DATABASE_URL: ${DATABASE_URL}
      JWT_SECRET: ${JWT_SECRET}
      JWT_LIFETIME_SECONDS: ${JWT_LIFETIME_SECONDS}
      BACKEND_CORS_ORIGINS: ${BACKEND_CORS_ORIGINS}
    ports:
      - "8000:8000"
    depends_on:
      postgres:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8000/health"]
      interval: 10s
      timeout: 3s
      retries: 5
      start_period: 15s

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

volumes:
  postgres_data:
```

Único cambio: quitar el bloque `ports: - "${POSTGRES_PORT:-5432}:5432"` del service `postgres`.

- [ ] **Step 3: Build y smoke test del stack completo**

```bash
docker compose down -v
docker compose up -d --build
```
Esperar ~30s a que arranquen, luego:

```bash
docker compose ps
curl -fsS http://localhost:8000/health
docker compose exec backend whoami
```
Expected:
- `docker compose ps`: los 3 services en estado `running`/`healthy`.
- `curl`: imprime `{"status":"ok"}`.
- `whoami`: imprime `appuser`.

Si `whoami` imprime `root` o si el container falla con permisos al hacer `uv sync`, revisar el Dockerfile (probablemente el `chown` quedó mal o el `--mount=type=cache` no respeta el UID).

- [ ] **Step 4: Verificar que Postgres no es accesible desde el host**

```bash
nc -z -w 2 localhost 5432 && echo "ACCESIBLE (mal)" || echo "no accesible (ok)"
```
Expected: `no accesible (ok)`.

- [ ] **Step 5: Tear down**

```bash
docker compose down
```

- [ ] **Step 6: Commit**

```bash
git add backend/Dockerfile docker-compose.yml
git commit -m "chore(deploy): backend container runs as non-root + remove postgres host-port binding"
```

---

## Task 7: Actualizar CLAUDE.md y tagear `v0.1.0-foundation`

**Files:**
- Modify: `CLAUDE.md` (sección "Polish backlog" y tabla "Estado actual")

**Contexto:** Cerrar el polish backlog en el doc maestro y tagear la phase. La Task 15 (deploy manual a Coolify) sigue pendiente del usuario — eso NO es un blocker para el tag (el tag es del código, el deploy se hace cuando el usuario lo decida).

- [ ] **Step 1: Marcar los 6 items como completados en `CLAUDE.md`**

En la sección "Polish backlog (antes de tagear v0.1.0-foundation)", reemplazar el contenido (los 6 bullets) por una nota de cierre tipo:

```markdown
## Polish backlog (cerrado, ver `docs/plans/2026-05-24-phase1-polish-backlog.md`)

Los 6 items detectados durante Phase 1 fueron resueltos en el plan de polish del 2026-05-24:

- ✓ CORS wildcard guard (config-level, fail al boot)
- ✓ Test `test_migrations_apply_cleanly_and_match_metadata`
- ✓ Refactor module-level `settings = get_settings()` (lazy factories en db/session, properties en auth/manager, inline en auth/backend)
- ✓ Dockerfile USER non-root + remove postgres host-port binding
- ✓ `UserSettingsUpdate.timezone` validado contra `zoneinfo.available_timezones()`
- ✓ `UserSettingsUpdate.marginal_rate` con `max_digits=5, decimal_places=4`
```

En la tabla "Estado actual" (sección Phase 1), cambiar la fila a:

```markdown
| 1. Foundation | ✅ código completo (Tasks 1-14) + polish backlog cerrado + tag `v0.1.0-foundation` puesto. Task 15 (deploy manual a Coolify) pendiente del usuario | `docs/plans/2026-05-24-ibkr-control-phase1-foundation.md` + `docs/plans/2026-05-24-phase1-polish-backlog.md` | `v0.1.0-foundation` ✓ |
```

- [ ] **Step 2: Confirmar suite verde**

```bash
cd backend && uv run pytest -v
```
Expected: todos PASS.

- [ ] **Step 3: Commit el cambio de docs**

```bash
git add CLAUDE.md
git commit -m "docs(claude): close Phase 1 polish backlog (6/6 items)"
```

- [ ] **Step 4: Mover el tag `v0.1.0-foundation` al HEAD actual**

El tag actual está en `4d12f5e` (antes del polish). Hay 2 opciones:

(a) **Mover el tag** (lo que recomienda CLAUDE.md tabla "código completo + tag puesto"): force-update el tag al HEAD post-polish para que el tag represente el código realmente listo para producción.

(b) **Crear un tag nuevo** `v0.1.1-foundation-polish` y dejar `v0.1.0-foundation` intacto.

**Esta decisión es del usuario** — pausá acá y preguntale cuál prefiere antes de ejecutar. Si elige (a):

```bash
git tag -f v0.1.0-foundation
git tag -ln v0.1.0-foundation
```
Y avisar al usuario que si ya pushearon el tag a remoto, hay que `git push --force origin v0.1.0-foundation` (acción destructiva — confirmar primero).

Si elige (b):

```bash
git tag -a v0.1.1-foundation-polish -m "Phase 1 polish backlog complete"
git tag -ln v0.1.1-foundation-polish
```

---

## Self-Review

**Spec coverage (los 6 items del Polish backlog de CLAUDE.md):**
- CORS wildcard guard → Task 3 ✓
- Test de migrations → Task 5 ✓
- Refactor module-level settings capture → Task 4 ✓
- Dockerfile USER non-root + remove postgres host-port → Task 6 ✓
- timezone zoneinfo validator → Task 2 ✓
- marginal_rate max_digits/decimal_places → Task 1 ✓

Más Task 7 (housekeeping del doc + tag) para cerrar formalmente.

**Placeholder scan:** ninguna referencia a "TODO", "TBD", "fill in", "similar a Task N", "implement later". Todo el código está completo.

**Type consistency:**
- `Settings._no_cors_wildcard` retorna `str` en Task 3 ✓
- `UserSettingsUpdate._timezone_must_be_zoneinfo` retorna `str | None` y el field es `str | None` ✓
- `get_engine` retorna `AsyncEngine`, `get_session_maker` retorna `async_sessionmaker[AsyncSession]`, `get_async_session` consume `get_session_maker()()` ✓
- `UserManager` properties `reset_password_token_secret`/`verification_token_secret` retornan `str` (matchea el tipo de `BaseUserManager`) ✓
- `get_jwt_strategy` retorna `JWTStrategy` ✓

Plan listo.
