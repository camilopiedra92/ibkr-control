"""resolve_authz - el PDP (SP2-D1/D3). Decide, no setea RLS - los errores van
como HTTPException directo (PEP/PDP conviven en el monolito; el swap futuro
reemplaza este modulo entero).

Memberships primero (sin RLS - identidad); grants despues via la funcion
SECURITY DEFINER authz_grant_party_ids (el resolver corre ANTES de que exista
contexto RLS; ver spec SP2-D3 mecanismo). El contexto cross-org via grant es
SIEMPRE explicito - sin header no se adivina.
"""

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.authz.context import AuthzContext
from ibkr_control.db.models.memberships import Membership

__all__ = ["resolve_authz"]


async def _grant_party_ids(session: AsyncSession, *, user_id: int, org_id: int) -> frozenset[int]:
    rows = await session.execute(
        text("SELECT * FROM authz_grant_party_ids(:u, :o)"), {"u": user_id, "o": org_id}
    )
    return frozenset(rows.scalars().all())


async def resolve_authz(
    session: AsyncSession, *, user_id: int, requested_org_id: int | None
) -> AuthzContext:
    memberships = (
        await session.execute(
            select(Membership.organization_id, Membership.role).where(Membership.user_id == user_id)
        )
    ).all()
    role_by_org = {org_id: role for org_id, role in memberships}

    if requested_org_id is None:
        if not role_by_org:
            raise HTTPException(status_code=403, detail="NO_ORG_MEMBERSHIP")
        if len(role_by_org) > 1:
            raise HTTPException(status_code=400, detail="ORG_SELECTION_REQUIRED")
        org_id, role = next(iter(role_by_org.items()))
        return AuthzContext(
            org_id=org_id, user_id=user_id, actor="member", role=role, party_ids=None
        )

    if requested_org_id in role_by_org:
        return AuthzContext(
            org_id=requested_org_id,
            user_id=user_id,
            actor="member",
            role=role_by_org[requested_org_id],
            party_ids=None,
        )

    party_ids = await _grant_party_ids(session, user_id=user_id, org_id=requested_org_id)
    if not party_ids:
        raise HTTPException(status_code=403, detail="NO_ORG_ACCESS")
    return AuthzContext(
        org_id=requested_org_id,
        user_id=user_id,
        actor="grantee",
        role="read_only",
        party_ids=party_ids,
    )
