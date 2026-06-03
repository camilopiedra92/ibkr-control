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

import logging
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ibkr_control.db.models.flex_credentials import FlexCredentials
from ibkr_control.db.models.flex_raw import FlexImport
from ibkr_control.db.models.ingest_log import IngestLog
from ibkr_control.ingest.flex import client as flex_client_mod
from ibkr_control.ingest.flex import crypto as flex_crypto_mod
from ibkr_control.ingest.flex import parser as flex_parser_mod
from ibkr_control.ingest.flex import persister as flex_persister_mod
from ibkr_control.ingest.hash_dedup import check_hash_status, xml_hash
from ibkr_control.ingest.lock import advisory_lock
from ibkr_control.ingest.log import ingest_log_entry

logger = logging.getLogger(__name__)


async def _insert_poison_row(
    session: AsyncSession,
    *,
    user_id: int,
    organization_id: int,
    xml_hash: str,
    xml_bytes: bytes,
    source: str,
    exc: Exception,
) -> None:
    """Inserts flex_imports row with status='poison' after a parse/persist failure.

    Called from inside the catch block of the SAVEPOINT-wrapped persist, so the
    SAVEPOINT rollback runs first (reverting partial persister writes) and the
    poison INSERT happens on the outer session — which then gets commited by
    ingest_log_entry's finally clause.

    organization_id is the dedup scope (uq_flex_imports_org_xml_hash). user_id
    stays as nullable audit metadata (which user triggered the failed import).

    ON CONFLICT DO NOTHING because the same poison XML may be retried before
    the first poison row is committed (race between concurrent uploads).
    """
    reason = str(exc)[:2000]
    stmt = (
        pg_insert(FlexImport)
        .values(
            organization_id=organization_id,
            user_id=user_id,
            xml_hash=xml_hash,
            xml_bytes=xml_bytes,
            xml_size_bytes=len(xml_bytes),
            anyo=0,
            source=source,
            year_status="rolling",
            period_covered_from=date(1970, 1, 1),
            period_covered_to=date(1970, 1, 1),
            status="poison",
            poison_reason=reason,
            fetched_at=datetime.now(timezone.utc),
        )
        .on_conflict_do_nothing(index_elements=["organization_id", "xml_hash"])
    )
    await session.execute(stmt)


async def ingest_xml(
    session: AsyncSession,
    *,
    organization_id: int,
    user_id: int,
    xml_bytes: bytes,
    source: str,  # 'manual_upload' | 'web_service'
    trigger: str,  # 'cron' | 'manual' | 'wizard'
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

    async with ingest_log_entry(
        session, log_kind, user_id=user_id, organization_id=organization_id, trigger=trigger
    ) as log_id:
        # Fast-path: check hash before entering SAVEPOINT so poison rows are
        # short-circuited without any parse/persist work.
        h = xml_hash(xml_bytes)
        status = await check_hash_status(session, organization_id, h)
        if status == "ok":
            logger.info("flex: duplicate hash %s..., skipped", h[:12])
            log_row = await session.scalar(select(IngestLog).where(IngestLog.id == log_id))
            log_row.items_processed = 0
            existing_id = await session.scalar(
                select(FlexImport.id).where(
                    FlexImport.organization_id == organization_id,
                    FlexImport.xml_hash == h,
                )
            )
            return existing_id
        if status == "poison":
            logger.warning(
                "flex: previously poisoned hash %s..., skipped. "
                "Run scripts/poison_reset.py --org-id %d --xml-hash %s to retry.",
                h[:12],
                organization_id,
                h,
            )
            log_row = await session.scalar(select(IngestLog).where(IngestLog.id == log_id))
            log_row.items_processed = 0
            existing_id = await session.scalar(
                select(FlexImport.id).where(
                    FlexImport.organization_id == organization_id,
                    FlexImport.xml_hash == h,
                )
            )
            return existing_id
        # status == "absent": proceed with SAVEPOINT + parse + persist

        # Envolver parse + persist en un SAVEPOINT para que fallos del persister
        # reviertan solo sus writes, preservando el log_row para el commit final.
        sp = await session.begin_nested()
        try:
            parsed = flex_parser_mod.parse(xml_bytes)
            flex_import_id, _counters = await flex_persister_mod.persist(
                session,
                parsed=parsed,
                organization_id=organization_id,
                xml_bytes=xml_bytes,
                source=source,
            )
            await sp.commit()
        except Exception as exc:
            # Must stay broad: the SAVEPOINT catch-all must roll back partial
            # persister writes regardless of exception type (DB error, parse
            # error, unexpected) so the outer ingest_log_entry context can still
            # mark the log row as 'failed' and commit it cleanly.
            await sp.rollback()
            # Insert poison row OUTSIDE the rolled-back savepoint so it survives
            # the rollback and gets commited by ingest_log_entry's finally.
            await _insert_poison_row(
                session,
                user_id=user_id,
                organization_id=organization_id,
                xml_hash=h,
                xml_bytes=xml_bytes,
                source=source,
                exc=exc,
            )
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
    organization_id: int,
    user_id: int,
    trigger: str,  # 'cron' | 'manual' | 'wizard'
) -> int | None:
    """Hace fetch al Flex WS + ingiere. Devuelve flex_import_id o None si no hubo cambios.

    Toma advisory_lock por (source='flex', scope_id=organization_id) — bloquea
    concurrent runs para el mismo org (flex es per-org). Si esta tomado, lanza
    LockHeldError (caller decide que hacer). user_id es audit (qué usuario
    disparó el run); las creds y el dedup son per-org.
    """
    async with session_factory() as session:
        async with advisory_lock(session, scope_id=organization_id, source="flex"):
            async with ingest_log_entry(
                session, "flex", user_id=user_id, organization_id=organization_id, trigger=trigger
            ) as log_id:
                creds = await session.scalar(
                    select(FlexCredentials).where(
                        FlexCredentials.organization_id == organization_id
                    )
                )
                if creds is None:
                    raise RuntimeError(f"No flex_credentials for organization_id={organization_id}")

                token = flex_crypto_mod.decrypt_token(creds.token_encrypted)
                client = flex_client_mod.FlexClient(token=token)
                reference = await client.send_request(query_id=creds.ytd_query_id)
                xml_bytes = await client.poll_statement(reference_code=reference)

                h = xml_hash(xml_bytes)
                status = await check_hash_status(session, organization_id, h)
                if status == "ok":
                    logger.info("flex: duplicate hash %s..., skipped (items_processed=0)", h[:12])
                    log_row = await session.scalar(select(IngestLog).where(IngestLog.id == log_id))
                    log_row.items_processed = 0
                    return None
                if status == "poison":
                    logger.warning(
                        "flex: previously poisoned hash %s..., skipped. "
                        "Run scripts/poison_reset.py --org-id %d --xml-hash %s to retry.",
                        h[:12],
                        organization_id,
                        h,
                    )
                    log_row = await session.scalar(select(IngestLog).where(IngestLog.id == log_id))
                    log_row.items_processed = 0
                    return None
                # status == "absent": proceed with normal flow

                # Usar SAVEPOINT igual que en ingest_xml para aislar fallas del persister
                sp = await session.begin_nested()
                try:
                    parsed = flex_parser_mod.parse(xml_bytes)
                    flex_import_id, _counters = await flex_persister_mod.persist(
                        session,
                        parsed=parsed,
                        organization_id=organization_id,
                        xml_bytes=xml_bytes,
                        source="web_service",
                    )
                    await sp.commit()
                except Exception as exc:
                    # Must stay broad: same SAVEPOINT pattern as ingest_xml — must
                    # rollback partial persister writes for any exception type so
                    # ingest_log_entry can mark the row 'failed' and commit it.
                    await sp.rollback()
                    # Insert poison row OUTSIDE the rolled-back savepoint so it
                    # survives the rollback and gets commited by ingest_log_entry's
                    # finally.
                    await _insert_poison_row(
                        session,
                        user_id=user_id,
                        organization_id=organization_id,
                        xml_hash=h,
                        xml_bytes=xml_bytes,
                        source="web_service",
                        exc=exc,
                    )
                    raise

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
