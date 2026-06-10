"""Scheduler factory — single source of truth for AsyncIOScheduler config."""

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ibkr_control.config import get_settings


def create_scheduler() -> AsyncIOScheduler:
    """Create the production scheduler with persistent SQLAlchemyJobStore.

    Jobs are stored in `apscheduler_jobs` (pre-created by Alembic migration
    7fdaf6528762 as owner — app_rls has no CREATE). Restart-safe: cron
    triggers re-register via register_jobs() with replace_existing=True, and
    missed runs within misfire_grace_time get caught up at boot.
    pool_pre_ping: el jobstore sync mantiene su conexión psycopg viva entre
    wake-ups del scheduler; sin pre-ping, un restart de Postgres rompe el
    siguiente wake-up.
    """
    settings = get_settings()
    jobstores = {
        "default": SQLAlchemyJobStore(
            url=settings.database_url_sync,
            tablename="apscheduler_jobs",
            engine_options={"pool_pre_ping": True},
        ),
    }
    return AsyncIOScheduler(jobstores=jobstores, timezone="UTC")
