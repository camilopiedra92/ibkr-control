"""Orchestrator del flex ingest: lock + log + client + parser + persister.

Dos entry points:
- run(): hace SendRequest + Poll al Flex WS, luego ingiere
- ingest_xml(): recibe bytes ya descargados (upload manual o test)

Patron de transaccion en ingest_xml:
    El context manager ingest_log_entry crea el log_row, hace flush, y en el
    finally() hace commit (tanto exito como falla). Para que una falla en
    persist() no deje datos parciales en DB *pero* si deje el log_row
    con status='failed', usamos un SAVEPOINT alrededor del parseo + persistencia.
    Si el SAVEPOINT falla, sus writes se revierten pero el log_row sigue vivo
    en la sesion para que ingest_log_entry lo marque como 'failed' y lo commitee.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ibkr_control.db.models.flex_credentials import FlexCredentials
from ibkr_control.db.models.flex_raw import FlexImport  # noqa: F401 — kept for symmetry
from ibkr_control.db.models.ingest_log import IngestLog
from ibkr_control.ingest.flex import client as flex_client_mod
from ibkr_control.ingest.flex import crypto as flex_crypto_mod
from ibkr_control.ingest.flex import parser as flex_parser_mod
from ibkr_control.ingest.flex import persister as flex_persister_mod
from ibkr_control.ingest.hash_dedup import is_known_hash, xml_hash
from ibkr_control.ingest.lock import advisory_lock
from ibkr_control.ingest.log import ingest_log_entry


async def ingest_xml(
    session: AsyncSession,
    *,
    user_id: int,
    xml_bytes: bytes,
    source: str,    # 'manual_upload' | 'web_service'
    trigger: str,   # 'cron' | 'manual' | 'wizard'
) -> int:
    """Ingiere bytes XML pre-descargados. Usa el log + dedup + persister.

    NO toma el advisory_lock global de 'flex' porque los uploads manuales
    pueden ocurrir concurrentemente con el cron (cada upload tiene su propio
    XML con hash distinto). Si dos uploads pegan el MISMO XML al mismo tiempo,
    el UNIQUE constraint en xml_hash gana la carrera y el segundo recibe el
    existing_id.

    Usa un SAVEPOINT para aislar los writes del persister: si persist() falla,
    el SAVEPOINT revierte las escrituras del persister pero el log_row sigue
    pendiente en la sesion para que ingest_log_entry lo marque 'failed'.
    """
    log_kind = "manual_upload" if source == "manual_upload" else "flex"

    async with ingest_log_entry(session, log_kind, user_id, trigger) as log_id:
        # Envolver parse + persist en un SAVEPOINT para que fallos del persister
        # reviertan solo sus writes, preservando el log_row para el commit final.
        sp = await session.begin_nested()
        try:
            parsed = flex_parser_mod.parse(xml_bytes)
            flex_import_id, _counters = await flex_persister_mod.persist(
                session,
                parsed=parsed,
                user_id=user_id,
                xml_bytes=xml_bytes,
                source=source,
            )
            await sp.commit()
        except Exception:
            # Must stay broad: the SAVEPOINT catch-all must roll back partial
            # persister writes regardless of exception type (DB error, parse
            # error, unexpected) so the outer ingest_log_entry context can still
            # mark the log row as 'failed' and commit it cleanly.
            await sp.rollback()
            raise

        # Update items_processed antes del exit del log context.
        # Usamos n_observed_* (rows que llegaron en el XML) en vez de len(parsed.*)
        # para mantener el cálculo en un solo lugar (el persister). Si hubo
        # hash_dedup, _counters solo trae {"hash_dedup": True} y items_processed
        # queda en 0 (no se procesó nada nuevo).
        log_row = await session.scalar(select(IngestLog).where(IngestLog.id == log_id))
        if _counters.get("hash_dedup"):
            log_row.items_processed = 0
        else:
            log_row.items_processed = (
                _counters["n_observed_trades"]
                + _counters["n_observed_lots_closed"]
                + _counters["n_observed_open_lots"]
                + _counters["n_observed_cash_tx"]
                + _counters["n_observed_transfers"]
            )

        return flex_import_id


async def run(
    session_factory: async_sessionmaker,
    *,
    user_id: int,
    trigger: str,   # 'cron' | 'manual' | 'wizard'
) -> int | None:
    """Hace fetch al Flex WS + ingiere. Devuelve flex_import_id o None si no hubo cambios.

    Toma advisory_lock por (source='flex', user_id) — bloquea concurrent runs
    para el mismo user. Si esta tomado, lanza LockHeldError (caller decide que hacer).
    """
    async with session_factory() as session:
        async with advisory_lock(session, user_id=user_id, source="flex"):
            async with ingest_log_entry(session, "flex", user_id, trigger) as log_id:
                creds = await session.scalar(
                    select(FlexCredentials).where(FlexCredentials.user_id == user_id)
                )
                if creds is None:
                    raise RuntimeError(f"No flex_credentials for user_id={user_id}")

                token = flex_crypto_mod.decrypt_token(creds.token_encrypted)
                client = flex_client_mod.FlexClient(token=token)
                reference = await client.send_request(query_id=creds.ytd_query_id)
                xml_bytes = await client.poll_statement(reference_code=reference)

                h = xml_hash(xml_bytes)
                if await is_known_hash(session, h):
                    # No changes — solo update log items_processed
                    log_row = await session.scalar(
                        select(IngestLog).where(IngestLog.id == log_id)
                    )
                    log_row.items_processed = 0
                    return None

                # Usar SAVEPOINT igual que en ingest_xml para aislar fallas del persister
                sp = await session.begin_nested()
                try:
                    parsed = flex_parser_mod.parse(xml_bytes)
                    flex_import_id, _counters = await flex_persister_mod.persist(
                        session,
                        parsed=parsed,
                        user_id=user_id,
                        xml_bytes=xml_bytes,
                        source="web_service",
                    )
                    await sp.commit()
                except Exception:
                    # Must stay broad: same SAVEPOINT pattern as ingest_xml — must
                    # rollback partial persister writes for any exception type so
                    # ingest_log_entry can mark the row 'failed' and commit it.
                    await sp.rollback()
                    raise

                log_row = await session.scalar(
                    select(IngestLog).where(IngestLog.id == log_id)
                )
                if _counters.get("hash_dedup"):
                    log_row.items_processed = 0
                else:
                    log_row.items_processed = (
                        _counters["n_observed_trades"]
                        + _counters["n_observed_lots_closed"]
                        + _counters["n_observed_open_lots"]
                        + _counters["n_observed_cash_tx"]
                        + _counters["n_observed_transfers"]
                    )
                return flex_import_id
