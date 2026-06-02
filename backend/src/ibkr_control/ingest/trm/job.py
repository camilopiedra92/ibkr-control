"""Orchestrator TRM: lock + log + client + parser + persister.

Patron de transaccion:
    El context manager ingest_log_entry crea el log_row, hace flush, y en el
    finally() hace commit (tanto exito como falla). Para que una falla en
    bulk_upsert_days() no deje datos parciales *pero* si deje el log_row con
    status='failed', usamos un SAVEPOINT alrededor del fetch + persist.
    Si el SAVEPOINT falla, sus writes se revierten pero el log_row sigue vivo
    en la sesion para que ingest_log_entry lo marque 'failed' y lo commitee.

TRM es global (no per-user): advisory_lock con user_id=None.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ibkr_control.db.models.ingest_log import IngestLog
from ibkr_control.db.models.trm import TrmDay
from ibkr_control.ingest.lock import advisory_lock
from ibkr_control.ingest.log import ingest_log_entry
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
        trigger: 'cron' | 'wizard' | 'manual' — registrado en ingest_log.
        full_backfill: Si True, descarga todo el historico (wizard initial backfill).
                       Si False (default), solo desde MAX(vigencia_desde) en DB.

    Returns:
        Dict con keys: status, n_rows_api, n_days.

    Raises:
        httpx.HTTPStatusError: Si Socrata devuelve un status HTTP no-2xx.
        LockHeldError: Si otro proceso ya tiene el lock TRM.
    """
    async with session_factory() as session:
        async with advisory_lock(session, user_id=None, source="trm"):
            async with ingest_log_entry(session, "trm", user_id=None, trigger=trigger) as log_id:
                if full_backfill:
                    since = None
                else:
                    last_vd = await session.scalar(select(func.max(TrmDay.vigencia_desde)))
                    since = last_vd  # None si DB vacia → backfill total implicito

                # SAVEPOINT: si client.fetch o bulk_upsert falla, se revierten
                # sus writes pero el log_row sigue vivo para ser marcado 'failed'.
                sp = await session.begin_nested()
                try:
                    client = TrmClient()
                    rows = await client.fetch(since=since)

                    if not rows:
                        log_row = await session.scalar(
                            select(IngestLog).where(IngestLog.id == log_id)
                        )
                        log_row.items_processed = 0
                        await sp.commit()
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

                    log_row = await session.scalar(select(IngestLog).where(IngestLog.id == log_id))
                    log_row.items_processed = n_days
                    await sp.commit()
                except Exception:
                    await sp.rollback()
                    raise

                return {"status": "ok", "n_rows_api": len(rows), "n_days": n_days}
