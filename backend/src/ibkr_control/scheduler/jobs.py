"""APScheduler setup: 3 jobs idempotentes (Flex YTD diario + TRM diario + cleanup)."""
import logging
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ibkr_control.config import get_settings

logger = logging.getLogger(__name__)


async def _run_flex_for_all_users() -> None:
    """Itera sobre cada user con flex_credentials y corre flex_job.run.

    Cada run crea su propio engine + SessionLocal y lo dispone al final.
    LockHeldError  -> skip user con warning (manual trigger ya corriendo).
    FlexAuthError  -> log + continuar (credenciales invalidas para este user).
    httpx.HTTPError -> log + continuar (red / IBKR down).
    Exception (outer) -> log con traceback completo; no propaga para no matar
                         el scheduler. Se mantiene broad porque este es el
                         catch-all de ultimo recurso de una tarea background:
                         mejor loguear que crashear silenciosamente.
    """
    import httpx

    from ibkr_control.db.models.flex_credentials import FlexCredentials
    from ibkr_control.ingest.flex import job as flex_job
    from ibkr_control.ingest.flex.client import FlexAuthError
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
            except FlexAuthError as e:
                logger.error(
                    "flex_daily auth error for user_id=%s — %s", uid, e.error_message
                )
            except httpx.HTTPError as e:
                logger.error(
                    "flex_daily network error for user_id=%s — %s", uid, e
                )
            except Exception:
                # Outer catch-all: must stay broad to log unexpected failures without
                # crashing the per-user loop or the scheduler.
                logger.exception("flex_daily unexpected failure for user_id=%s — continuing", uid)
    finally:
        await engine.dispose()


async def _run_trm_global() -> None:
    """Cron TRM — un job global, no per-user.

    Crea su propio engine + SessionLocal y lo dispone al final.
    Exception -> log completo (ingest_log row ya tiene el error);
    se mantiene broad porque es el catch-all de ultimo recurso del job TRM.
    """
    from ibkr_control.ingest.trm import job as trm_job

    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    session_local = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await trm_job.run(session_local, trigger="cron")
    except Exception:
        # Must stay broad: TRM job can fail for network, parse, or DB reasons;
        # we log the full traceback and let the next cron run retry.
        logger.exception("trm_daily failed")
    finally:
        await engine.dispose()


async def _cleanup_old_jobs() -> None:
    """Limpia entradas antiguas del JobTracker para prevenir memory leak.

    Corre cada hora. Remueve jobs que terminaron hace mas de 1 hora.
    """
    from ibkr_control.ingest.job_tracker import get_tracker

    tracker = get_tracker()
    n = tracker.cleanup_old(older_than=timedelta(hours=1))
    if n > 0:
        logger.info("Cleaned up %d old job tracker entries", n)


def register_jobs(scheduler: AsyncIOScheduler) -> None:
    """Registra los 3 cron jobs en el scheduler. Idempotente — borra los existentes primero.

    Flex    daily:   07:00 COT = 12:00 UTC (Bogota no tiene DST).
    TRM     daily:   19:30 COT = 00:30 UTC del dia siguiente.
    Cleanup hourly:  cada hora en :00 UTC para limpiar JobTracker stale entries.
    Todos con max_instances=1 + coalesce=True para evitar solapamiento y backlog.
    """
    for job_id in ("flex_daily", "trm_daily", "cleanup_job_tracker"):
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

    scheduler.add_job(
        _cleanup_old_jobs,
        CronTrigger(minute=0, timezone="UTC"),  # every hour at :00
        id="cleanup_job_tracker",
        max_instances=1,
        coalesce=True,
    )

    logger.info("Registered 3 ingest jobs: flex_daily, trm_daily, cleanup_job_tracker")
