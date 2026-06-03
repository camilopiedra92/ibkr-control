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
    # disable_existing_loggers=False: fileConfig defaults to True, which would
    # DISABLE every logger not declared in alembic.ini — including the app's
    # (ibkr_control.*). That silences app logging after any in-process
    # `alembic upgrade` (tests run migrations via fixtures), which broke caplog
    # assertions on app loggers. We only want to ADD alembic's logging config,
    # not tear down the app's.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

import ibkr_control.db  # noqa: F401, E402  (carga modelos tras setup de config)

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
