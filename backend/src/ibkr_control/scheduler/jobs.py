"""APScheduler setup: 2 jobs idempotentes (Flex YTD diario + TRM diario)."""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ibkr_control.config import get_settings

logger = logging.getLogger(__name__)


async def _run_flex_for_all_users() -> None:
    """Itera sobre cada user con flex_credentials y corre flex_job.run.

    Cada run crea su propio engine + SessionLocal y lo dispone al final.
    LockHeldError -> skip user con warning (manual trigger ya corriendo).
    Otras excepciones -> log + continuar con el siguiente user.
    """
    from ibkr_control.db.models.flex_credentials import FlexCredentials
    from ibkr_control.ingest.flex import job as flex_job
    from ibkr_control.ingest.lock import LockHeldError

    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    session_local = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_local() as s:
            user_ids = (await s.scalars(select(FlexCredentials.user_id))).all()

        for uid in user_ids:
            try:
                await flex_job.run(session_local, user_id=uid, trigger="cron")
            except LockHeldError:
                logger.warning("flex_daily skipped user_id=%s — lock held", uid)
            except Exception:
                logger.exception("flex_daily failed for user_id=%s — continuing", uid)
    finally:
        await engine.dispose()


async def _run_trm_global() -> None:
    """Cron TRM — un job global, no per-user.

    Crea su propio engine + SessionLocal y lo dispone al final.
    Excepciones -> log (el ingest_log row ya tiene el error).
    """
    from ibkr_control.ingest.trm import job as trm_job

    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    session_local = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await trm_job.run(session_local, trigger="cron")
    except Exception:
        logger.exception("trm_daily failed")
    finally:
        await engine.dispose()


def register_jobs(scheduler: AsyncIOScheduler) -> None:
    """Registra los 2 cron jobs en el scheduler. Idempotente — borra los existentes primero.

    Flex  daily: 07:00 COT = 12:00 UTC (Bogota no tiene DST).
    TRM   daily: 19:30 COT = 00:30 UTC del dia siguiente.
    Ambos con max_instances=1 + coalesce=True para evitar solapamiento y backlog.
    """
    for job_id in ("flex_daily", "trm_daily"):
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass  # job no existia — OK

    scheduler.add_job(
        _run_flex_for_all_users,
        CronTrigger(hour=12, minute=0, timezone="UTC"),
        id="flex_daily",
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        _run_trm_global,
        CronTrigger(hour=0, minute=30, timezone="UTC"),
        id="trm_daily",
        max_instances=1,
        coalesce=True,
    )

    logger.info("Registered 2 ingest jobs: flex_daily, trm_daily")
