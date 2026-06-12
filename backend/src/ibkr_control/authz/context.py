"""AuthzContext: el resultado tipado de la decision de autorizacion (SP2-D3)."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class AuthzContext:
    """Quien actua, en que org, con que rol efectivo y que scope de party.

    party_ids is None => sin restriccion (member del org).
    party_ids set     => grantee: solo la data de esos grantor parties.
    """

    org_id: int
    user_id: int
    actor: Literal["member", "grantee"]
    role: str  # owner|admin|member (member) - read_only (grantee)
    party_ids: frozenset[int] | None
