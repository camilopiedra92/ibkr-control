"""APScheduler setup: 3 jobs idempotentes (Flex YTD diario + TRM diario + cleanup)."""

import logging
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ibkr_control.config import get_settings

logger = logging.getLogger(__name__)


async def _run_flex_for_all_orgs() -> None:
    """Itera sobre cada org con flex_credentials y corre flex_job.run.

    flex_credentials es per-org (no per-user) → el cron itera organizaciones.
    El ingest es puramente org-scoped (D-CONV-3): no hay usuario disparador.

    LIMITACION RLS (gap documentado, diferido a SP7): la enumeracion de orgs
    (SELECT DISTINCT organization_id FROM flex_credentials) es una lectura
    CROSS-TENANT de sistema. Si el app corre como el rol sin-bypass `app_rls`
    (FORCE RLS) sin `app.current_org` seteado, esta query default-deny → 0 orgs
    → el cron no fetchea nada en prod. El `flex_job.run` per-org SI es
    RLS-correcto (se auto-setea contexto). Lo que falta es darle a la
    enumeracion una conexion de SISTEMA (rol owner/bypass o contexto bootstrap):
    eso es SP7 (cron tenant-aware) + SP5 (durable jobs). Hasta entonces el
    auto-fetch diario requiere que el scheduler conecte con un rol que vea
    cross-tenant. NO es un bug oculto: es un corte de alcance explicito.

    Cada run crea su propio engine + SessionLocal y lo dispone al final.
    LockHeldError  -> skip org con warning (manual trigger ya corriendo).
    FlexAuthError  -> log + continuar (credenciales invalidas para este org).
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
            org_ids = (
                await s.scalars(select(func.distinct(FlexCredentials.organization_id)))
            ).all()

        for org_id in org_ids:
            try:
                await flex_job.run(session_local, organization_id=org_id, trigger="cron")
            except LockHeldError:
                logger.warning("flex_daily skipped org_id=%s — lock held", org_id)
            except FlexAuthError as e:
                logger.error("flex_daily auth error for org_id=%s — %s", org_id, e.error_message)
            except httpx.HTTPError as e:
                logger.error("flex_daily network error for org_id=%s — %s", org_id, e)
            except Exception:
                # Outer catch-all: must stay broad to log unexpected failures without
                # crashing the per-org loop or the scheduler.
                logger.exception("flex_daily unexpected failure for org_id=%s — continuing", org_id)
    finally:
        await engine.dispose()


async def _run_trm_global() -> None:
    """Cron TRM — un job global de control plane (no per-org).

    Crea su propio engine + SessionLocal y lo dispone al final.
    Exception -> log completo (TRM no escribe ingest_log; su registro es
    trm_imports). Se mantiene broad porque es el catch-all de ultimo recurso
    del job TRM.
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
    """Registra los 3 cron jobs en el scheduler. Idempotente — usa replace_existing=True.

    Flex    daily:   07:00 COT = 12:00 UTC (Bogota no tiene DST).
    TRM     daily:   19:30 COT = 00:30 UTC del dia siguiente.
    Cleanup hourly:  cada hora en :00 UTC para limpiar JobTracker stale entries.

    Todos con max_instances=1 + coalesce=True para evitar solapamiento y backlog.
    misfire_grace_time=21600 (6h) para que un container restart en la ventana
    del cron diario recupere el run perdido al boot (jobs son idempotentes).
    """
    scheduler.add_job(
        _run_flex_for_all_orgs,
        CronTrigger(hour=12, minute=0, timezone="UTC"),
        id="flex_daily",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=21600,
        replace_existing=True,
    )

    scheduler.add_job(
        _run_trm_global,
        CronTrigger(hour=0, minute=30, timezone="UTC"),
        id="trm_daily",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=21600,
        replace_existing=True,
    )

    scheduler.add_job(
        _cleanup_old_jobs,
        CronTrigger(minute=0, timezone="UTC"),  # every hour at :00
        id="cleanup_job_tracker",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=21600,
        replace_existing=True,
    )

    logger.info("Registered 3 ingest jobs: flex_daily, trm_daily, cleanup_job_tracker")
