"""SP2-D6 barrera 2: contexto grantee -> SET LOCAL transaction_read_only=on.

Self-healing: el listener after_begin re-aplica el flag en CADA transaccion
nueva (igual que los GUCs org/user) -- un commit intra-request no lo pierde.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from ibkr_control.db.rls import apply_org_context, set_session_org_context


async def test_read_only_context_blocks_writes_at_db(db_session, sample_org):
    """Un INSERT en contexto read_only muere en Postgres (25006), sin capa app."""
    set_session_org_context(db_session, org_id=sample_org.id, user_id=None, read_only=True)
    await apply_org_context(db_session, org_id=sample_org.id, user_id=None, read_only=True)
    with pytest.raises(DBAPIError) as exc_info:
        await db_session.execute(
            text(
                "INSERT INTO counterparties (external_id, organization_id) VALUES ('RO-TEST', :o)"
            ),
            {"o": sample_org.id},
        )
        await db_session.commit()
    assert "read-only" in str(exc_info.value).lower()
    await db_session.rollback()


async def test_read_only_survives_commit(db_session, sample_org):
    """Self-healing: tras un commit, la PROXIMA transaccion sigue read-only
    (listener after_begin re-aplica el flag stashed)."""
    set_session_org_context(db_session, org_id=sample_org.id, user_id=None, read_only=True)
    await apply_org_context(db_session, org_id=sample_org.id, user_id=None, read_only=True)
    await db_session.execute(text("SELECT 1"))
    await db_session.commit()  # cierra la tx; la proxima autobegins
    val = await db_session.scalar(text("SELECT current_setting('transaction_read_only')"))
    assert val == "on"


async def test_default_context_remains_read_write(db_session, sample_org):
    """Un contexto member (read_only=False, el default) NO bloquea writes."""
    set_session_org_context(db_session, org_id=sample_org.id, user_id=None)
    await apply_org_context(db_session, org_id=sample_org.id, user_id=None)
    val = await db_session.scalar(text("SELECT current_setting('transaction_read_only')"))
    assert val == "off"
