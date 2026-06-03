"""Proof that the endpoint app runs as the non-bypass ``app_rls`` role (SP1, Task 14a).

The endpoint suite is wired (via ``app_with_db``) to a MIGRATED database and
connects as the non-superuser, non-owner ``app_rls`` login role, so the FORCE'd
RLS policies actually apply to every request. This test pins that invariant: if
someone reverts ``app_with_db`` to ``create_all`` on the owner connection (which
bypasses RLS), this fails loudly.
"""

from sqlalchemy import text


async def test_app_session_is_app_rls_role(app_rls_db_session):
    """The app's DB sessions authenticate as ``app_rls`` (non-bypass)."""
    role = (await app_rls_db_session.execute(text("SELECT current_user"))).scalar_one()
    assert role == "app_rls"
