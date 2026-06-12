"""CRUD /api/grants (SP2-D7): el write-path del enforcement de SP2.

Revoke = valid_to (NUNCA DELETE — audit trail). Semántica half-open
[valid_from, valid_to). Sin UPDATE: cambiar vigencia/grantee = revocar + crear.
La policy RLS grant_visibility muestra ambas direcciones en el GET; el write
queda doblemente guardado (scope grants:write + WITH CHECK org = current_org).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.api._schemas import GrantCreate, GrantRead
from ibkr_control.authz import AuthzContext, require_scope
from ibkr_control.db.models.access_grants import AccessGrant
from ibkr_control.db.models.memberships import Membership
from ibkr_control.db.models.parties import Party
from ibkr_control.db.session import get_async_session

router = APIRouter(prefix="/grants", tags=["grants"])


def _ser(g: AccessGrant, *, org_id: int) -> GrantRead:
    return GrantRead(
        id=g.id,
        grantor_party_id=g.grantor_party_id,
        grantee_organization_id=g.grantee_organization_id,
        grantee_user_id=g.grantee_user_id,
        organization_id=g.organization_id,
        role=g.role,
        valid_from=g.valid_from,
        valid_to=g.valid_to,
        direction="granted" if g.organization_id == org_id else "received",
    )


@router.get("", response_model=list[GrantRead])
async def list_grants(
    ctx: AuthzContext = Depends(require_scope("grants:read")),
    session: AsyncSession = Depends(get_async_session),
) -> list[GrantRead]:
    # RLS (grant_visibility) scopea: grantor-org + grantee — ambas direcciones.
    stmt = select(AccessGrant).order_by(AccessGrant.id)
    # El brazo grantor-org de la policy es visibilidad de grado MEMBER. Un
    # grantee que entra al org del cliente (X-Organization-Id, legítimo para
    # data-plane) NO la hereda: sin este filtro enumeraría TODOS los grants
    # que el cliente otorgó (incl. a otros contadores — leak de grantee_user_id
    # y de su existencia). SP2-D5: el grantee lista solo los grants que LO
    # habilitan; el rol read_only conserva grants:read intacto.
    if ctx.party_ids is not None:  # actor == "grantee"
        stmt = stmt.where(
            or_(
                AccessGrant.grantee_user_id == ctx.user_id,
                AccessGrant.grantee_organization_id.in_(
                    select(Membership.organization_id).where(Membership.user_id == ctx.user_id)
                ),
            )
        )
    rows = await session.scalars(stmt)
    return [_ser(g, org_id=ctx.org_id) for g in rows.all()]


@router.post("", response_model=GrantRead, status_code=201)
async def create_grant(
    payload: GrantCreate,
    ctx: AuthzContext = Depends(require_scope("grants:write")),
    session: AsyncSession = Depends(get_async_session),
) -> GrantRead:
    if (payload.grantee_organization_id is None) == (payload.grantee_user_id is None):
        raise HTTPException(status_code=422, detail="GRANTEE_EXACTLY_ONE")
    if payload.grantee_organization_id == ctx.org_id:
        raise HTTPException(status_code=422, detail="SELF_GRANT")
    # El grantor party se resuelve bajo RLS del org activo: ajeno = no existe.
    party = await session.scalar(select(Party).where(Party.id == payload.grantor_party_id))
    if party is None:
        raise HTTPException(status_code=404, detail="PARTY_NOT_FOUND")
    today = (await session.execute(select(func.current_date()))).scalar_one()
    grant = AccessGrant(
        grantor_party_id=party.id,
        grantee_organization_id=payload.grantee_organization_id,
        grantee_user_id=payload.grantee_user_id,
        organization_id=ctx.org_id,
        valid_from=payload.valid_from or today,
        valid_to=payload.valid_to,
    )
    session.add(grant)
    try:
        await session.commit()
    except IntegrityError:
        # FK del grantee inexistente / CHECK de rango — sin leak de cuál.
        await session.rollback()
        raise HTTPException(status_code=422, detail="INVALID_GRANT") from None
    await session.refresh(grant)
    return _ser(grant, org_id=ctx.org_id)


@router.post("/{grant_id}/revoke", response_model=GrantRead)
async def revoke_grant(
    grant_id: int,
    ctx: AuthzContext = Depends(require_scope("grants:write")),
    session: AsyncSession = Depends(get_async_session),
) -> GrantRead:
    # Dirección explícita: solo grants OTORGADOS por el org activo (la policy
    # también muestra los recibidos, pero revocarlos no es de este org — y el
    # WITH CHECK lo rebotaría con un error DB; el WHERE da un 404 limpio).
    grant = await session.scalar(
        select(AccessGrant).where(
            AccessGrant.id == grant_id, AccessGrant.organization_id == ctx.org_id
        )
    )
    if grant is None:
        raise HTTPException(status_code=404, detail="GRANT_NOT_FOUND")
    today = (await session.execute(select(func.current_date()))).scalar_one()
    if grant.valid_to is not None and grant.valid_to <= today:
        raise HTTPException(status_code=409, detail="GRANT_ALREADY_INACTIVE")
    grant.valid_to = today  # half-open: inactivo desde YA (SP2-D7)
    await session.commit()
    await session.refresh(grant)
    return _ser(grant, org_id=ctx.org_id)
