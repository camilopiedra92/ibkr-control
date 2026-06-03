"""Per-request RLS context. SET LOCAL (transaction-scoped, pooling-safe).

Public surface:
- resolve_current_org_id — pure logic: pick org from memberships.
- org_context        — FastAPI Depends: resolve + SET LOCAL + return org_id.

The low-level GUC writer ``apply_org_context`` now lives in ``db/rls.py`` (the
DB-layer SSOT for the RLS GUC contract). It is re-exported here for backward
compatibility, but the inner ingest layer imports it from ``db/rls.py`` directly
so it never depends on this web-layer module.
"""

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.auth.backend import current_active_user
from ibkr_control.auth.models import User
from ibkr_control.db.models.memberships import Membership
from ibkr_control.db.rls import apply_org_context
from ibkr_control.db.session import get_async_session

__all__ = ["apply_org_context", "resolve_current_org_id", "org_context"]


async def resolve_current_org_id(
    session: AsyncSession, *, user_id: int, requested_org_id: int | None
) -> int:
    """The org the request acts in. Org selection is EXPLICIT (D-CONV-3): there
    is no silent first-pick "common case".

    - exactly one membership + no requested org → resolve it.
    - multiple memberships + no requested org → 400 ORG_SELECTION_REQUIRED
      (the caller must pick; we never guess).
    - requested org not in memberships → 403 NOT_A_MEMBER.
    - no memberships → 403 NO_ORG_MEMBERSHIP.

    Cross-org access via grants is SP2 — here we honor only the user's own
    memberships.
    """
    rows = (
        await session.scalars(
            select(Membership.organization_id).where(Membership.user_id == user_id)
        )
    ).all()
    if not rows:
        raise HTTPException(status_code=403, detail="NO_ORG_MEMBERSHIP")
    if requested_org_id is None:
        if len(rows) > 1:
            raise HTTPException(status_code=400, detail="ORG_SELECTION_REQUIRED")
        return rows[0]
    if requested_org_id not in rows:
        raise HTTPException(status_code=403, detail="NOT_A_MEMBER")
    return requested_org_id


async def org_context(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> int:
    """FastAPI dependency: resolve org, SET LOCAL the RLS context, return org_id."""
    org_id = await resolve_current_org_id(session, user_id=user.id, requested_org_id=None)
    await apply_org_context(session, org_id=org_id, user_id=user.id)
    return org_id
