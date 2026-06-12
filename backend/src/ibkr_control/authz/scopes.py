"""Taxonomía de scopes + mapeo rol->scopes + require_scope (PEP) — SP2-D2/D5.

El mapeo es DATA (un dict), no ifs dispersos. require_scope(scope) devuelve la
dependency FastAPI que: autentica -> resuelve AuthzContext (PDP) -> chequea scope
-> setea contexto RLS (GUCs + read_only si grantee, SP2-D6) -> devuelve el ctx.
Marca la dependency con ._authz_scope para el route-sweep guard.
"""

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.auth.backend import current_active_user
from ibkr_control.auth.models import User
from ibkr_control.authz.context import AuthzContext
from ibkr_control.authz.resolver import resolve_authz
from ibkr_control.db.rls import apply_org_context, set_session_org_context
from ibkr_control.db.session import get_async_session

__all__ = ["SCOPES", "ROLE_SCOPES", "require_scope"]

SCOPES: frozenset[str] = frozenset(
    {
        "connections:write",
        "setup:write",
        "ingest:trigger",
        "ops:read",
        "data:read",
        "grants:write",
        "grants:read",
    }
)

# SP2-D5 — la tabla del spec como data. owner = todo; admin = todo menos
# grants:write (compartir data fiscal es del dueño); member = read-only;
# read_only (grantee) = data-plane + descubrir sus grants.
ROLE_SCOPES: dict[str, frozenset[str]] = {
    "owner": SCOPES,
    "admin": SCOPES - {"grants:write"},
    "member": frozenset({"ops:read", "data:read", "grants:read"}),
    "read_only": frozenset({"data:read", "grants:read"}),
}


def require_scope(scope: str):
    """Factory de la dependency PEP. Fail-loud en import si el scope no existe."""
    if scope not in SCOPES:
        raise ValueError(f"Unknown authz scope: {scope!r}")

    async def dependency(
        user: User = Depends(current_active_user),
        session: AsyncSession = Depends(get_async_session),
        x_organization_id: int | None = Header(None, alias="X-Organization-Id"),
    ) -> AuthzContext:
        ctx = await resolve_authz(session, user_id=user.id, requested_org_id=x_organization_id)
        if scope not in ROLE_SCOPES[ctx.role]:
            raise HTTPException(status_code=403, detail="INSUFFICIENT_SCOPE")
        read_only = ctx.actor == "grantee"
        set_session_org_context(
            session, org_id=ctx.org_id, user_id=ctx.user_id, read_only=read_only
        )
        await apply_org_context(
            session, org_id=ctx.org_id, user_id=ctx.user_id, read_only=read_only
        )
        return ctx

    dependency._authz_scope = scope  # marker del route-sweep guard (Task 7)
    return dependency
