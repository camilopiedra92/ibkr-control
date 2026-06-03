"""Resolver de visibilidad: qué account_ids puede ver un usuario.

Visibilidad propia = sus participations vigentes. Visibilidad delegada (contador)
= las participations del grantor, si existe un grant read-only vigente. G3: el
contexto es explícito vía on_behalf_of (stateless), nunca mergeado."""

from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.db.models.participations import Participation

# TODO(sp1-cleanup): DataAccessGrant removed; scope.py is rewritten in the SP1
# cleanup task. Stub to avoid collection-time failure.
DataAccessGrant = None  # type: ignore[assignment]


class GrantRequiredError(Exception):
    """on_behalf_of seteado pero sin grant read-only vigente del grantor."""

    def __init__(self, grantor_user_id: int):
        self.grantor_user_id = grantor_user_id
        super().__init__(f"No hay grant de lectura vigente del usuario {grantor_user_id}")


async def _participation_account_ids(session: AsyncSession, user_id: int, at: date) -> set[int]:
    rows = await session.scalars(
        select(Participation.account_id).where(
            Participation.user_id == user_id,
            Participation.valid_from <= at,
            or_(Participation.valid_to.is_(None), Participation.valid_to > at),
        )
    )
    return set(rows)


async def has_valid_grant(
    session: AsyncSession, grantor_user_id: int, grantee_user_id: int, at: date
) -> bool:
    """True si grantor otorgó a grantee acceso read_only vigente en `at`."""
    g = await session.scalar(
        select(DataAccessGrant).where(
            DataAccessGrant.grantor_user_id == grantor_user_id,
            DataAccessGrant.grantee_user_id == grantee_user_id,
            DataAccessGrant.role == "read_only",
            DataAccessGrant.valid_from <= at,
            or_(DataAccessGrant.valid_to.is_(None), DataAccessGrant.valid_to > at),
        )
    )
    return g is not None


async def visible_account_ids(
    session: AsyncSession,
    user_id: int,
    on_behalf_of: int | None = None,
    at: date | None = None,
) -> set[int]:
    at = at or date.today()
    if on_behalf_of is None or on_behalf_of == user_id:
        return await _participation_account_ids(session, user_id, at)
    if not await has_valid_grant(session, on_behalf_of, user_id, at):
        raise GrantRequiredError(on_behalf_of)
    return await _participation_account_ids(session, on_behalf_of, at)
