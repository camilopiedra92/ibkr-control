import inspect

import pytest
from sqlalchemy import select

from ibkr_control.db.models.counterparties import Counterparty
from ibkr_control.ingest.flex.persister import _ensure_counterparties
from tests.conftest import scope_session_to_org


def test_ensure_counterparties_uses_on_conflict():
    """HD-3 (driver): _ensure_counterparties debe usar el patrón endurecido
    ON CONFLICT DO NOTHING (race-safe) igual que _ensure_accounts/_ensure_instruments,
    no el add+flush racy. Guard de source porque la carrera real no es testeable
    determinísticamente (lock del índice cuelga un test secuencial).
    """
    src = inspect.getsource(_ensure_counterparties)
    assert "on_conflict_do_nothing" in src, (
        "_ensure_counterparties aún usa add+flush racy — migrar a ON CONFLICT + re-select"
    )


@pytest.mark.asyncio
async def test_ensure_counterparties_is_idempotent(db_session, sample_org):
    """HD-3 (companion): dos corridas del mismo external_id en el mismo org devuelven
    el mismo id y dejan UNA fila (no double-insert)."""
    org_id = sample_org.id
    await scope_session_to_org(db_session, org_id)

    first = await _ensure_counterparties(db_session, ["CS-999999-99"], organization_id=org_id)
    await db_session.flush()
    second = await _ensure_counterparties(db_session, ["CS-999999-99"], organization_id=org_id)
    await db_session.flush()

    assert first["CS-999999-99"] == second["CS-999999-99"]
    rows = (
        await db_session.scalars(select(Counterparty).where(Counterparty.organization_id == org_id))
    ).all()
    assert len([c for c in rows if c.external_id == "CS-999999-99"]) == 1
