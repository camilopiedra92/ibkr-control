"""require_scope (PEP, SP2-D2/D5): scope check + header + contexto RLS."""

import pytest

from ibkr_control.authz.scopes import ROLE_SCOPES, SCOPES, require_scope


def test_role_scope_map_matches_spec_table():
    """SP2-D5: la tabla del spec, lockeada como data."""
    assert ROLE_SCOPES["owner"] == SCOPES  # owner: todo
    assert "grants:write" not in ROLE_SCOPES["admin"]
    assert "connections:write" in ROLE_SCOPES["admin"]
    assert ROLE_SCOPES["member"] == frozenset({"ops:read", "data:read", "grants:read"})
    assert ROLE_SCOPES["read_only"] == frozenset({"data:read", "grants:read"})


def test_unknown_scope_fails_loud_at_factory():
    """Un typo en el scope rompe al IMPORT del módulo del endpoint, no en runtime."""
    with pytest.raises(ValueError):
        require_scope("connectons:write")  # typo
