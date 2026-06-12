"""visible_account_ids (SP2-D6 barrera 3): None para members; set histórico
de cuentas del party para grantees (participación pasada cuenta — el año
fiscal N exige cuentas ya cerradas/vendidas)."""

from datetime import date

from ibkr_control.authz.context import AuthzContext
from ibkr_control.authz.party_scope import visible_account_ids


def _member_ctx(org_id: int) -> AuthzContext:
    return AuthzContext(org_id=org_id, user_id=1, actor="member", role="owner", party_ids=None)


def _grantee_ctx(org_id: int, party_ids: set[int]) -> AuthzContext:
    return AuthzContext(
        org_id=org_id,
        user_id=1,
        actor="grantee",
        role="read_only",
        party_ids=frozenset(party_ids),
    )


async def test_member_is_unrestricted(db_session, sample_org):
    assert await visible_account_ids(db_session, _member_ctx(sample_org.id)) is None


async def test_grantee_sees_only_party_accounts_including_expired(
    db_session, sample_org, sample_party, sample_account
):
    from decimal import Decimal

    from ibkr_control.db.models.accounts import Account
    from ibkr_control.db.models.participations import Participation

    other_acc = Account(
        ibkr_account_id="U88888888", organization_id=sample_org.id, alias="other", currency="USD"
    )
    db_session.add(other_acc)
    await db_session.flush()
    # Participación EXPIRADA del party en sample_account: igual cuenta (histórico).
    db_session.add(
        Participation(
            party_id=sample_party.id,
            account_id=sample_account.id,
            organization_id=sample_org.id,
            pct=Decimal("1.0"),
            valid_from=date(2024, 1, 1),
            valid_to=date(2025, 1, 1),
        )
    )
    await db_session.commit()

    got = await visible_account_ids(db_session, _grantee_ctx(sample_org.id, {sample_party.id}))
    assert got == {sample_account.id}  # other_acc NO (el party nunca participó)


async def test_grantee_restatements_filtered_by_party(
    db_session, sample_org, sample_party, sample_account, sample_flex_import
):
    """El endpoint-level filter: restatements de cuentas fuera del party no se emiten.
    Se prueba la QUERY (no el endpoint HTTP — eso es Task 10): insertar dos
    restatement rows (una en sample_account con participación del party, otra en
    una cuenta ajena al party) y verificar que el WHERE account_id IN (...) del
    endpoint las separa."""
    from decimal import Decimal

    from sqlalchemy import select

    from ibkr_control.db.models.accounts import Account
    from ibkr_control.db.models.participations import Participation
    from ibkr_control.db.models.restatements import RestatementLog

    other_acc = Account(
        ibkr_account_id="U77777777", organization_id=sample_org.id, alias="o2", currency="USD"
    )
    db_session.add(other_acc)
    await db_session.flush()
    db_session.add(
        Participation(
            party_id=sample_party.id,
            account_id=sample_account.id,
            organization_id=sample_org.id,
            pct=Decimal("1.0"),
            valid_from=date(2024, 1, 1),
        )
    )
    for acc in (sample_account, other_acc):
        db_session.add(
            RestatementLog(
                organization_id=sample_org.id,
                flex_import_id=sample_flex_import.id,
                account_id=acc.id,
                table_name="open_position_lots",
                natural_key={"account_id": acc.id},
                column_name="qty",
                old_value="1",
                new_value="2",
                kind="value_update",
            )
        )
    await db_session.commit()

    visible = await visible_account_ids(db_session, _grantee_ctx(sample_org.id, {sample_party.id}))
    rows = (
        await db_session.scalars(
            select(RestatementLog).where(RestatementLog.account_id.in_(visible))
        )
    ).all()
    assert {r.account_id for r in rows} == {sample_account.id}
