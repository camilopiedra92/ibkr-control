# syntax=docker/dockerfile:1.7

# ---------- base: runtime común (Python, uv, user no-root) ----------
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

# ---------- dev: pytest/ruff incluidos, uvicorn --reload, source bind-mounted ----------
FROM base AS dev

RUN --mount=type=cache,target=/home/appuser/.cache/uv,uid=1001,gid=1001 \
    uv sync --frozen --no-install-project

# El source se bind-montea desde compose.dev.yaml. Copiamos un placeholder para
# que el container arranque incluso si alguien construye sin volumes.
COPY --chown=appuser:appuser src/ ./src/
COPY --chown=appuser:appuser alembic.ini ./
COPY --chown=appuser:appuser alembic/ ./alembic/

EXPOSE 8000

# --reload-dir limita el watcher a src/ para evitar restarts espurios por logs/cache.
CMD ["sh", "-c", "uv run alembic upgrade head && exec uv run uvicorn ibkr_control.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir src"]

# ---------- prod: solo runtime deps, source horneado, sin reload ----------
FROM base AS prod

RUN --mount=type=cache,target=/home/appuser/.cache/uv,uid=1001,gid=1001 \
    uv sync --frozen --no-install-project --no-dev

COPY --chown=appuser:appuser src/ ./src/
COPY --chown=appuser:appuser alembic.ini ./
COPY --chown=appuser:appuser alembic/ ./alembic/

RUN --mount=type=cache,target=/home/appuser/.cache/uv,uid=1001,gid=1001 \
    uv sync --frozen --no-dev

EXPOSE 8000

CMD ["sh", "-c", "uv run alembic upgrade head && exec uv run uvicorn ibkr_control.main:app --host 0.0.0.0 --port 8000"]
