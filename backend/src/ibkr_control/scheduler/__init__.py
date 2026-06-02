"""Scheduler factory — single source of truth for AsyncIOScheduler config."""

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ibkr_control.config import get_settings


def create_scheduler() -> AsyncIOScheduler:
    """Create the production scheduler with persistent SQLAlchemyJobStore.

    Jobs are stored in `apscheduler_jobs` (auto-created by APScheduler on
    first start — no Alembic migration needed). Restart-safe: cron triggers
    re-register via register_jobs() with replace_existing=True, and missed
    runs within misfire_grace_time get caught up at boot.
    """
    settings = get_settings()
    jobstores = {
        "default": SQLAlchemyJobStore(
            url=settings.database_url_sync,
            tablename="apscheduler_jobs",
        ),
    }
    return AsyncIOScheduler(jobstores=jobstores, timezone="UTC")
