"""Fixtures compartidas para los tests del flex job/persister (W1).

`_seed_connection` reemplaza el viejo seeding de FlexCredentials: crea una
Connection ibkr_flex (estado del sync) + su detail ConnectionIbkrFlex (token +
query_id) bajo el mismo org. El job ahora itera connections, no flex_credentials.
"""

from sqlalchemy import select


async def _seed_connection(
    session,
    org_id: int,
    *,
    query_id: str = "12345",
    status: str = "active",
) -> int:
    """Crea una Connection ibkr_flex activa + su detail para `org_id`.

    Devuelve el connection_id. Mirror del seeding que antes hacía FlexCredentials
    — el caller commitea por dentro (igual que los fixtures viejos).
    """
    from ibkr_control.db.models.connections import Connection, ConnectionIbkrFlex
    from ibkr_control.db.models.institutions import Institution
    from ibkr_control.ingest.flex.crypto import encrypt_token

    # The `institutions` catalog is seeded by the baseline migration. The
    # create_all-backed db_session world does NOT run migrations, so ensure the
    # 'ibkr' row exists here (idempotent) — harmless on migrated DBs.
    inst_id = await session.scalar(select(Institution.id).where(Institution.code == "ibkr"))
    if inst_id is None:
        inst = Institution(code="ibkr", name="Interactive Brokers")
        session.add(inst)
        await session.flush()
        inst_id = inst.id
    conn = Connection(
        organization_id=org_id,
        institution_id=inst_id,
        provider_type="ibkr_flex",
        status=status,
    )
    session.add(conn)
    await session.flush()
    session.add(
        ConnectionIbkrFlex(
            connection_id=conn.id,
            organization_id=org_id,
            token_encrypted=encrypt_token("test-token-1234567890"),
            query_id=query_id,
        )
    )
    await session.commit()
    return conn.id
