"""Multi-home de cuentas broker (spec 2026-06-10-account-multihome, M1/M3).

Patrón Plaid/Sharesight: dos orgs conectan la misma cuenta IBKR, cada uno en
su universo aislado. Supersede H2 (AccountClaimedError): el conflicto
cross-org deja de existir por diseño.
"""

from pathlib import Path

from sqlalchemy import text

from ibkr_control.db.rls import apply_org_context, set_session_org_context

_FIXTURES = Path(__file__).parent / "fixtures" / "xml"


async def _count(s, table: str) -> int:
    return (await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()


async def test_two_orgs_can_connect_same_ibkr_account(rls_session_factory):
    """M1: la misma ibkr_account_id existe en dos orgs; RLS aísla cada universo."""
    app_factory, seed = rls_session_factory
    org_a = await seed("Org A", "U77770001")
    org_b = await seed("Org B", "U77770001")  # HOY: IntegrityError (UNIQUE global)

    async with app_factory() as s:
        await s.execute(
            text("SELECT set_config('app.current_org', :o, true)").bindparams(o=str(org_a))
        )
        assert await _count(s, "accounts") == 1
    async with app_factory() as s:
        await s.execute(
            text("SELECT set_config('app.current_org', :o, true)").bindparams(o=str(org_b))
        )
        assert await _count(s, "accounts") == 1
    async with app_factory() as s:
        # Sin contexto: default-deny, ninguna de las dos copias visible.
        assert await _count(s, "accounts") == 0


async def test_same_xml_ingested_by_two_orgs_isolated_universes(rls_session_factory):
    """M1+M3: el mismo XML ingerido por dos orgs = dos copias independientes.

    Corre el persister real bajo app_rls con contexto de org (paridad runtime),
    una vez por org, y verifica counts iguales y cero cross-talk.
    """
    from ibkr_control.ingest.flex import parser as flex_parser
    from ibkr_control.ingest.flex import persister as flex_persister

    app_factory, seed = rls_session_factory
    org_a = await seed("Org A", "U99999001")
    org_b = await seed("Org B", "U99999001")

    xml_bytes = sorted(_FIXTURES.glob("*.xml"))[0].read_bytes()
    parsed = flex_parser.parse(xml_bytes)

    counts: dict[int, int] = {}
    for org_id in (org_a, org_b):
        async with app_factory() as s:
            set_session_org_context(s, org_id=org_id, user_id=None)
            await apply_org_context(s, org_id=org_id, user_id=None)
            await flex_persister.persist(
                s,
                parsed=parsed,
                organization_id=org_id,
                xml_bytes=xml_bytes,
                source="manual_upload",
            )
            await s.commit()
        async with app_factory() as s:
            await s.execute(
                text("SELECT set_config('app.current_org', :o, true)").bindparams(o=str(org_id))
            )
            counts[org_id] = await _count(s, "trades")

    assert counts[org_a] == counts[org_b] > 0
    async with app_factory() as s:
        assert await _count(s, "trades") == 0  # sin contexto: nada visible


async def test_reingest_same_org_stays_idempotent(rls_session_factory):
    """M1: la idempotencia per-org sobrevive el re-scoping de conflict_cols."""
    from ibkr_control.ingest.flex import parser as flex_parser
    from ibkr_control.ingest.flex import persister as flex_persister

    app_factory, seed = rls_session_factory
    org_a = await seed("Org A", "U99999001")
    xml_bytes = sorted(_FIXTURES.glob("*.xml"))[0].read_bytes()

    for _ in range(2):
        async with app_factory() as s:
            set_session_org_context(s, org_id=org_a, user_id=None)
            await apply_org_context(s, org_id=org_a, user_id=None)
            await flex_persister.persist(
                s,
                parsed=flex_parser.parse(xml_bytes),
                organization_id=org_a,
                xml_bytes=xml_bytes,
                source="manual_upload",
            )
            await s.commit()

    async with app_factory() as s:
        await s.execute(
            text("SELECT set_config('app.current_org', :o, true)").bindparams(o=str(org_a))
        )
        n_trades_first = await _count(s, "trades")
    # Una sola copia por org: el segundo persist dedupeó por (org, xml_hash)
    # y/o por las natural keys per-org. accounts: la seed + las del XML, sin dupes.
    assert n_trades_first > 0
    async with app_factory() as s:
        await s.execute(
            text("SELECT set_config('app.current_org', :o, true)").bindparams(o=str(org_a))
        )
        rows = (
            await s.execute(
                text(
                    "SELECT ibkr_account_id, count(*) FROM accounts "
                    "GROUP BY ibkr_account_id HAVING count(*) > 1"
                )
            )
        ).all()
    assert rows == []  # cero duplicados same-org


async def test_ensure_accounts_same_org_conflict_is_idempotent(db_session, sample_org):
    """M3: ON CONFLICT DO NOTHING + re-select cubre el race same-org (ex-H2).

    Pre-inserta una cuenta y llama _ensure_accounts con esa + una nueva: no
    explota, devuelve el map completo (la pre-existente con su id original).
    """
    from ibkr_control.db.models.accounts import Account
    from ibkr_control.ingest.flex.persister import _ensure_accounts

    pre = Account(organization_id=sample_org.id, ibkr_account_id="U88880001", currency="USD")
    db_session.add(pre)
    await db_session.flush()

    mapping = await _ensure_accounts(
        db_session, ["U88880001", "U88880002"], organization_id=sample_org.id
    )
    assert mapping["U88880001"] == pre.id
    assert set(mapping) == {"U88880001", "U88880002"}
