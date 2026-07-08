"""IC-3: EXCLUDE gist impide participaciones con vigencia solapada.

Un mismo (org, party, account) no puede tener dos filas cuyos rangos
[valid_from, valid_to) se solapen — el constraint participations_no_overlap
lo enforce en Postgres. Adyacencia half-open ([.., X) y [X, ..)) NO solapa.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError

from ibkr_control.db.models.participations import Participation


@pytest.mark.asyncio
async def test_overlapping_participation_rejected(
    db_session, sample_org, sample_party, sample_account
):
    # Primera participación: [2024-01-01, 2025-01-01)
    db_session.add(
        Participation(
            party_id=sample_party.id,
            account_id=sample_account.id,
            organization_id=sample_org.id,
            pct=Decimal("0.50"),
            valid_from=date(2024, 1, 1),
            valid_to=date(2025, 1, 1),
        )
    )
    await db_session.flush()
    # Segunda participación solapada: [2024-06-01, NULL) → debe fallar
    db_session.add(
        Participation(
            party_id=sample_party.id,
            account_id=sample_account.id,
            organization_id=sample_org.id,
            pct=Decimal("1.00"),
            valid_from=date(2024, 6, 1),
            valid_to=None,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_disjoint_participations_accepted(
    db_session, sample_org, sample_party, sample_account
):
    db_session.add(
        Participation(
            party_id=sample_party.id,
            account_id=sample_account.id,
            organization_id=sample_org.id,
            pct=Decimal("0.50"),
            valid_from=date(2024, 1, 1),
            valid_to=date(2025, 1, 1),
        )
    )
    # Adyacente half-open: [2025-01-01, NULL) NO solapa con [.., 2025-01-01)
    db_session.add(
        Participation(
            party_id=sample_party.id,
            account_id=sample_account.id,
            organization_id=sample_org.id,
            pct=Decimal("1.00"),
            valid_from=date(2025, 1, 1),
            valid_to=None,
        )
    )
    await db_session.flush()  # no raise


@pytest.mark.asyncio
async def test_reversed_range_rejected_by_generated_validity(
    db_session, sample_org, sample_party, sample_account
):
    """valid_to < valid_from is rejected at the generated `validity` daterange layer
    (Postgres: "range lower bound must be less than or equal to range upper bound")
    BEFORE the ck_participations_valid_range CHECK — pins the earlier of the two
    defense layers the EXCLUDE design relies on. asyncpg surfaces this as a plain
    DataError that SQLAlchemy's dialect does not reclassify, so it arrives as the
    generic DBAPIError (parent of IntegrityError/DataError)."""
    db_session.add(
        Participation(
            party_id=sample_party.id,
            account_id=sample_account.id,
            organization_id=sample_org.id,
            pct=Decimal("0.50"),
            valid_from=date(2025, 1, 1),
            valid_to=date(2024, 1, 1),
        )
    )
    with pytest.raises(DBAPIError):
        await db_session.flush()
