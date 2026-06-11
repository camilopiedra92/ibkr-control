"""Provisioning world-class: un template migrado por worker, clon barato por test.

Reemplaza el patron "bootear contenedor + alembic upgrade head POR TEST"
(_migrated_app_db) por una DB ``template_migrated`` migrada UNA vez, y un
``CREATE DATABASE ... TEMPLATE`` por test (~100ms, copia filesystem). Todo el
provisioning (CREATE/DROP DATABASE, alembic) es SINCRONO (psycopg2 + alembic
directo) -> sin fixtures async session-scoped; solo las sesiones del test son
async. Ver docs/specs/2026-06-11-test-infra-worldclass-design.md (D1, D5, D6).
"""

import itertools
import os

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from ibkr_control.config import get_settings
from tests.conftest_ephemeral_db import build_alembic_config, swap_dsn_database

TEMPLATE_DB = "template_migrated"
_JWT = "test-secret-32-chars-minimum-please-ok"

# Contador per-PROCESO. Bajo xdist cada worker es un proceso distinto (arranca en
# 0) y dentro de un worker los tests son seriales -> sin carrera, unico al
# combinar con PYTEST_XDIST_WORKER.
_clone_counter = itertools.count()


def _sync_maint_url(async_dsn: str) -> str:
    """asyncpg DSN del contenedor -> psycopg2 DSN sobre la DB `postgres` (DDL)."""
    return swap_dsn_database(async_dsn.replace("+asyncpg", "+psycopg2"), "postgres")


@pytest.fixture(scope="session")
def _maintenance_engine(postgres_container):
    """Engine SINCRONO AUTOCOMMIT sobre la DB `postgres` para CREATE/DROP DATABASE.

    AUTOCOMMIT porque CREATE/DROP DATABASE no corren en transaccion. Conecta a
    `postgres` (no al `test` del model world, no al template) para no bloquear un
    CREATE ... TEMPLATE ni colisionar con el create_all del model world.
    """
    engine = create_engine(
        _sync_maint_url(postgres_container.get_connection_url()),
        isolation_level="AUTOCOMMIT",
    )
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def template_db(postgres_container, _maintenance_engine):
    """Construye la DB template migrada UNA vez por sesion/worker.

    CREATE DATABASE template_migrated + `alembic upgrade head` (crea el rol
    app_rls, policies FORCE RLS, la fn SECURITY DEFINER y los seeds de
    control-plane). Devuelve el DSN asyncpg OWNER del template. El teardown del
    contenedor lo dropea; sin teardown explicito.
    """
    async_url = postgres_container.get_connection_url()
    with _maintenance_engine.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{TEMPLATE_DB}"'))

    template_dsn = swap_dsn_database(async_url, TEMPLATE_DB)

    mp = pytest.MonkeyPatch()
    try:
        mp.setenv("DATABASE_URL", template_dsn)
        mp.setenv("JWT_SECRET", _JWT)
        get_settings.cache_clear()
        command.upgrade(build_alembic_config(), "head")
    finally:
        mp.undo()
        get_settings.cache_clear()

    # command.upgrade dispone su engine, pero por defensa cerramos cualquier
    # backend residual contra el template: CREATE ... TEMPLATE exige cero
    # conexiones a la fuente.
    with _maintenance_engine.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :d"
            ).bindparams(d=TEMPLATE_DB)
        )
    return template_dsn


@pytest.fixture
def test_db(postgres_container, template_db, _maintenance_engine, monkeypatch):
    """Una DB pristina, totalmente migrada, clonada del template para UN test.

    CREATE DATABASE test_<worker>_<n> TEMPLATE template_migrated (copia
    filesystem, ~100ms) -> yield del DSN asyncpg OWNER -> DROP ... WITH (FORCE).
    Setea DATABASE_URL para que el boot/lifespan de la app vea esta DB.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    # DB identifiers can't be bind params (only values can) -> identifiers must be
    # interpolated. Safe here: worker is xdist's PYTEST_XDIST_WORKER (always
    # "gw<N>", [a-z0-9]) and the counter is an int -> dbname is provably [a-z0-9_].
    dbname = f"test_{worker}_{next(_clone_counter)}"

    with _maintenance_engine.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{dbname}" TEMPLATE "{TEMPLATE_DB}"'))

    owner_dsn = swap_dsn_database(postgres_container.get_connection_url(), dbname)
    monkeypatch.setenv("DATABASE_URL", owner_dsn)
    monkeypatch.setenv("JWT_SECRET", _JWT)
    get_settings.cache_clear()

    try:
        yield owner_dsn
    finally:
        with _maintenance_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)'))
