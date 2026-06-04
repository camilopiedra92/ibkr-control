"""Unit tests for ``app_rls_password()`` — the env-driven SSOT for the app_rls
login role password (SP1-hardening H3).

Pure-function tests (no DB): the dev/test default, the env override, the
fail-loud rejection of an unsafe (single-quote) password, and that the role-
create DDL embeds the value from the helper (wiring).
"""

import pytest

from ibkr_control.db.rls import APP_ROLE, app_role_grants_sql, app_rls_password


def test_default_when_env_unset(monkeypatch):
    monkeypatch.delenv("APP_RLS_PASSWORD", raising=False)
    assert app_rls_password() == "app_rls_pw"


def test_returns_env_value_when_set(monkeypatch):
    monkeypatch.setenv("APP_RLS_PASSWORD", "s3cret-from-env")
    assert app_rls_password() == "s3cret-from-env"


def test_rejects_single_quote(monkeypatch):
    # The password is interpolated into a SQL string literal in the CREATE ROLE
    # DDL. A single quote would break out of the literal -> reject fail-loud
    # rather than emit injectable DDL.
    monkeypatch.setenv("APP_RLS_PASSWORD", "pw'; DROP TABLE accounts; --")
    with pytest.raises(ValueError, match="single quote"):
        app_rls_password()


def test_role_grants_sql_embeds_helper_value(monkeypatch):
    monkeypatch.setenv("APP_RLS_PASSWORD", "wired-from-env")
    stmts = app_role_grants_sql()
    create_role = next(s for s in stmts if "CREATE ROLE" in s)
    assert "LOGIN PASSWORD 'wired-from-env'" in create_role
    assert f"rolname='{APP_ROLE}'" in create_role
