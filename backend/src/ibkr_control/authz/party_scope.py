"""visible_account_ids — la barrera 3 del grantee (SP2-D6).

None = sin restricción (member). Para grantees: cuentas con CUALQUIER
participación (histórica o vigente) de los grantor parties — revisar el año
fiscal N exige cuentas que el party ya vendió/cerró (SCD-2: un valid_to pasado
sigue habilitando lectura). Corre bajo el contexto RLS del org ya seteado.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.authz.context import AuthzContext
from ibkr_control.db.models.participations import Participation

__all__ = ["visible_account_ids"]


async def visible_account_ids(session: AsyncSession, ctx: AuthzContext) -> set[int] | None:
    if ctx.party_ids is None:
        return None
    rows = await session.scalars(
        select(Participation.account_id).distinct().where(Participation.party_id.in_(ctx.party_ids))
    )
    return set(rows.all())
