"""Orchestrator TRM: lock + client + parser + persister.

Patron de transaccion:
    TRM es control plane (referencia global compartida por todo el sistema), no
    data plane per-tenant. Por eso NO escribe en ingest_log (org-scoped + RLS):
    un run TRM global no tiene organization_id. Su fuente unica de verdad +
    observabilidad es trm_imports (global, sin RLS), via record_import().

    El job abre una transaccion plana y la commitea solo en exito. Si fetch o
    bulk_upsert_days fallan, la excepcion propaga y el context manager de la
    sesion (`async with session_factory()`) hace rollback de toda la tx → no
    quedan trm_days parciales. No se usa SAVEPOINT porque ya no hay log_row que
    preservar a traves de un fallo.

TRM es global (no per-org): advisory_lock con scope_id=None (el lock TRM global).
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ibkr_control.db.models.trm import TrmDay
from ibkr_control.ingest.lock import advisory_lock
from ibkr_control.ingest.trm.client import TrmClient
from ibkr_control.ingest.trm.parser import expand_vigencias
from ibkr_control.ingest.trm.persister import bulk_upsert_days, record_import


async def run(
    session_factory: async_sessionmaker,
    *,
    trigger: str,  # 'cron' | 'wizard' | 'manual'
    full_backfill: bool = False,
) -> dict:
    """Pull incremental desde Socrata DIAN. Si full_backfill=True, ignora MAX(vigencia_desde).

    Args:
        session_factory: async_sessionmaker para crear la sesion de DB.
        trigger: 'cron' | 'wizard' | 'manual' — informativo (TRM no escribe ingest_log).
        full_backfill: Si True, descarga todo el historico (wizard initial backfill).
                       Si False (default), solo desde MAX(vigencia_desde) en DB.

    Returns:
        Dict con keys: status, n_rows_api, n_days.

    Raises:
        httpx.HTTPStatusError: Si Socrata devuelve un status HTTP no-2xx.
        LockHeldError: Si otro proceso ya tiene el lock TRM.
    """
    async with session_factory() as session:
        async with advisory_lock(session, scope_id=None, source="trm"):
            if full_backfill:
                since = None
            else:
                last_vd = await session.scalar(select(func.max(TrmDay.vigencia_desde)))
                since = last_vd  # None si DB vacia → backfill total implicito

            client = TrmClient()
            rows = await client.fetch(since=since)

            if not rows:
                # nada que traer -> commit del read tx (libera lock limpio) y return
                await session.commit()
                return {"status": "ok", "n_rows_api": 0, "n_days": 0}

            expanded = list(expand_vigencias(rows))
            n_days = await bulk_upsert_days(session, expanded)

            date_from = min(d["date"] for d in expanded)
            date_to = max(d["date"] for d in expanded)
            await record_import(
                session,
                date_from=date_from,
                date_to=date_to,
                n_rows_api=len(rows),
                n_days_expanded=n_days,
            )

            await session.commit()
            return {"status": "ok", "n_rows_api": len(rows), "n_days": n_days}
