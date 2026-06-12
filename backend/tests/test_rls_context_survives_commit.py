"""The require_scope PEP's RLS GUC survives an intra-request commit: after
committing, a subsequent query in the same session still sees app.current_org
(re-applied by the after_begin listener), so org-scoped reads remain isolated.
"""

from sqlalchemy import text

from ibkr_control.db.rls import set_session_org_context


async def test_org_guc_reapplied_after_commit(app_rls_db_session):
    session = app_rls_db_session
    # Establish context the way require_scope does: stash on session.info.
    set_session_org_context(session, org_id=4242, user_id=7)

    # Open a tx and read the GUC -> present (listener applied it on begin).
    before = (await session.scalars(text("SELECT current_setting('app.current_org', true)"))).one()
    assert before == "4242"

    # Commit ends the tx (SET LOCAL would normally be lost) ...
    await session.commit()

    # ... but the next tx re-applies it via the after_begin listener.
    after = (await session.scalars(text("SELECT current_setting('app.current_org', true)"))).one()
    assert after == "4242"
