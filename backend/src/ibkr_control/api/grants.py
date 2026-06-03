"""CRUD de grants de lectura. Owner-only: cada user crea/revoca SUS grants."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.api._schemas import GrantCreate, GrantListResponse, GrantRead
from ibkr_control.auth.backend import current_active_user
from ibkr_control.auth.models import User
from ibkr_control.db.models.grants import DataAccessGrant
from ibkr_control.db.session import get_async_session

router = APIRouter(prefix="/grants", tags=["grants"])


@router.post("", response_model=GrantRead, status_code=201)
async def create_grant(
    payload: GrantCreate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> GrantRead:
    grantee = await session.scalar(select(User).where(User.email == payload.grantee_email))
    if grantee is None:
        raise HTTPException(status_code=404, detail="No existe un usuario con ese email")
    if grantee.id == user.id:
        raise HTTPException(status_code=400, detail="No podés otorgarte acceso a vos mismo")
    grant = DataAccessGrant(
        grantor_user_id=user.id,
        grantee_user_id=grantee.id,
        valid_from=payload.valid_from,
        valid_to=payload.valid_to,
    )
    session.add(grant)
    try:
        await session.commit()
    except IntegrityError as e:
        raise HTTPException(status_code=409, detail="Ese grant ya existe") from e
    return GrantRead(
        grantor_user_id=user.id,
        grantee_user_id=grantee.id,
        grantor_email=user.email,
        grantee_email=grantee.email,
        role=grant.role,
        valid_from=grant.valid_from,
        valid_to=grant.valid_to,
    )


@router.get("", response_model=GrantListResponse)
async def list_grants(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> GrantListResponse:
    granted = (
        await session.scalars(
            select(DataAccessGrant).where(DataAccessGrant.grantor_user_id == user.id)
        )
    ).all()
    received = (
        await session.scalars(
            select(DataAccessGrant).where(DataAccessGrant.grantee_user_id == user.id)
        )
    ).all()

    # Resolve every referenced user's email in a single query instead of two
    # lookups per grant. Grant cardinality is tiny (personal app), but a flat IN
    # keeps the endpoint O(1) in round-trips regardless of grant count.
    user_ids = {
        uid for g in (*granted, *received) for uid in (g.grantor_user_id, g.grantee_user_id)
    }
    emails: dict[int, str] = (
        dict(
            (await session.execute(select(User.id, User.email).where(User.id.in_(user_ids)))).all()
        )
        if user_ids
        else {}
    )

    def _ser(g: DataAccessGrant) -> GrantRead:
        return GrantRead(
            grantor_user_id=g.grantor_user_id,
            grantee_user_id=g.grantee_user_id,
            grantor_email=emails[g.grantor_user_id],
            grantee_email=emails[g.grantee_user_id],
            role=g.role,
            valid_from=g.valid_from,
            valid_to=g.valid_to,
        )

    return GrantListResponse(
        granted=[_ser(g) for g in granted],
        received=[_ser(g) for g in received],
    )


@router.delete("/{grantee_user_id}/{valid_from}", status_code=204)
async def revoke_grant(
    grantee_user_id: int,
    valid_from: date,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> None:
    grant = await session.get(DataAccessGrant, (user.id, grantee_user_id, valid_from))
    if grant is None:
        raise HTTPException(status_code=404, detail="Grant no encontrado")
    await session.delete(grant)
    await session.commit()
