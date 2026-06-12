"""SP2 schema: CHECK half-open de access_grants (SP2-D8), restatement_log.account_id
(SP2-D9) y la función SECURITY DEFINER authz_grant_party_ids (SP2-D3)."""

from datetime import date

from sqlalchemy import text

from ibkr_control.db.models.access_grants import AccessGrant

# pytest-asyncio en modo AUTO (pyproject asyncio_mode=auto) maneja los tests
# async — igual que el resto de la suite. NO usar pytest.mark.anyio: mezclaría el
# runner de anyio (test) con los fixtures async de pytest-asyncio (db_session /
# owner_session), corriéndolos en event loops distintos -> "another operation is
# in progress" al compartir conexiones asyncpg.


async def _mk_grantee_org(owner_session):
    from ibkr_control.db.models.organizations import Organization

    org = Organization(type="firm", name="Estudio Contable")
    owner_session.add(org)
    await owner_session.commit()
    await owner_session.refresh(org)
    return org


async def test_check_allows_empty_interval_same_day_revoke(
    db_session, sample_org, sample_party, owner_session
):
    """SP2-D8: valid_to == valid_from es legal (intervalo vacío = nunca activo)."""
    firm = await _mk_grantee_org(owner_session)
    g = AccessGrant(
        grantor_party_id=sample_party.id,
        grantee_organization_id=firm.id,
        organization_id=sample_org.id,
        valid_from=date(2026, 6, 12),
        valid_to=date(2026, 6, 12),  # mismo día — viola el CHECK viejo (>)
    )
    db_session.add(g)
    await db_session.commit()  # no debe levantar IntegrityError
    await db_session.refresh(g)
    assert g.valid_to == g.valid_from


async def test_restatement_log_has_account_id(db_session):
    """SP2-D9: account_id es columna de primera clase NOT NULL."""
    from ibkr_control.db.models.restatements import RestatementLog

    cols = RestatementLog.__table__.c
    assert "account_id" in cols
    assert cols.account_id.nullable is False


async def test_authz_grant_party_ids_returns_only_vigentes(
    db_session, sample_org, sample_party, sample_user, owner_session
):
    """SP2-D3: la función SECURITY DEFINER devuelve grantor parties de grants
    vigentes hacia el user (directo), corriendo como app_rls. La función no
    depende del GUC (definer = owner, exenta de RLS) — el bootstrap del resolver
    la llama ANTES de que exista contexto org."""
    from ibkr_control.auth.models import User

    accountant = User(email="cpa@t.com", hashed_password="x", is_active=True, name="CPA")
    owner_session.add(accountant)
    await owner_session.commit()
    await owner_session.refresh(accountant)

    today = date.today()
    db_session.add_all(
        [
            AccessGrant(  # vigente, directo al user
                grantor_party_id=sample_party.id,
                grantee_user_id=accountant.id,
                organization_id=sample_org.id,
                valid_from=today,
            ),
        ]
    )
    await db_session.commit()

    rows = (
        await db_session.execute(
            text("SELECT * FROM authz_grant_party_ids(:u, :o)"),
            {"u": accountant.id, "o": sample_org.id},
        )
    ).scalars()
    assert set(rows.all()) == {sample_party.id}

    # Grant revocado hoy (valid_to=hoy, half-open) NO aparece.
    await db_session.execute(
        text("UPDATE access_grants SET valid_to = CURRENT_DATE WHERE grantor_party_id = :p"),
        {"p": sample_party.id},
    )
    await db_session.commit()
    rows = (
        await db_session.execute(
            text("SELECT * FROM authz_grant_party_ids(:u, :o)"),
            {"u": accountant.id, "o": sample_org.id},
        )
    ).scalars()
    assert set(rows.all()) == set()


async def test_authz_grant_party_ids_via_firm_membership(
    db_session, sample_org, sample_party, owner_session
):
    """Grant a un org-firm: cualquier member del firm resuelve los party_ids."""
    from datetime import date as _date

    from ibkr_control.auth.models import User
    from ibkr_control.db.models.memberships import Membership

    firm = await _mk_grantee_org(owner_session)
    staff = User(email="staff@firm.com", hashed_password="x", is_active=True, name="Staff")
    owner_session.add(staff)
    await owner_session.flush()
    owner_session.add(Membership(user_id=staff.id, organization_id=firm.id, role="member"))
    await owner_session.commit()
    await owner_session.refresh(staff)

    db_session.add(
        AccessGrant(
            grantor_party_id=sample_party.id,
            grantee_organization_id=firm.id,
            organization_id=sample_org.id,
            valid_from=_date.today(),
        )
    )
    await db_session.commit()

    rows = (
        await db_session.execute(
            text("SELECT * FROM authz_grant_party_ids(:u, :o)"),
            {"u": staff.id, "o": sample_org.id},
        )
    ).scalars()
    assert set(rows.all()) == {sample_party.id}
