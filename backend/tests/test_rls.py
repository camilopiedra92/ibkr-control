"""RLS smoke suite — PROVES cross-tenant isolation (SP1, Task 11).

These tests connect as the non-superuser, non-bypass ``app_rls`` login role (via
``rls_session_factory``), so the FORCE'd row-level security policies created by
the baseline migration ``05943d9efcdb`` actually apply. They exercise:

  1. default-deny without context (no error, zero rows) + visibility once
     ``app.current_org`` is set;
  2. org A cannot see org B's rows (and vice-versa);
  3. there is NO way for the app_rls role to see all rows (no bypass);
  4. the special ``grant_visibility`` policy on ``access_grants`` (grantor-org OR
     grantee-org arms).

RLS context is per-transaction: ``set_config('app.current_org', X, true)`` is
SET LOCAL and resets on commit, so each context-set + query MUST share one
transaction. We open ``async with factory() as s:`` and run set_config + query
before any commit.
"""

from datetime import date

from sqlalchemy import text


async def _set_org(s, org_id: int) -> None:
    await s.execute(
        text("SELECT set_config('app.current_org', :o, true)").bindparams(o=str(org_id))
    )


async def _set_user(s, user_id: int) -> None:
    await s.execute(
        text("SELECT set_config('app.current_user', :u, true)").bindparams(u=str(user_id))
    )


async def _account_count(s) -> int:
    return (await s.execute(text("SELECT count(*) FROM accounts"))).scalar_one()


async def test_app_role_default_deny_without_context(rls_session_factory):
    """No context set -> the app_rls role sees ZERO accounts (no error)."""
    app_factory, seed = rls_session_factory
    org_a = await seed("Org A", "U10000001")

    async with app_factory() as s:
        # No app.current_org set: default-deny, clean (NULLIF makes ''::bigint -> NULL).
        assert await _account_count(s) == 0
        # Same transaction: set context to A -> the seeded row becomes visible.
        await _set_org(s, org_a)
        assert await _account_count(s) == 1


async def test_org_a_cannot_see_org_b_accounts(rls_session_factory):
    """With context = A, only A's account is visible; with B, only B's."""
    app_factory, seed = rls_session_factory
    org_a = await seed("Org A", "U10000001")
    org_b = await seed("Org B", "U20000002")

    async with app_factory() as s:
        await _set_org(s, org_a)
        rows = (await s.execute(text("SELECT ibkr_account_id FROM accounts"))).scalars().all()
        assert rows == ["U10000001"]

    async with app_factory() as s:
        await _set_org(s, org_b)
        rows = (await s.execute(text("SELECT ibkr_account_id FROM accounts"))).scalars().all()
        assert rows == ["U20000002"]


async def test_app_rls_cannot_bypass(rls_session_factory):
    """Even with two orgs seeded, app_rls without context sees NOTHING.

    There is no way to read across tenants as the app role: absent context is
    default-deny, and any context restricts to exactly one org (covered above).
    """
    app_factory, seed = rls_session_factory
    await seed("Org A", "U10000001")
    await seed("Org B", "U20000002")

    async with app_factory() as s:
        # No set_config at all -> zero rows despite TWO orgs existing.
        assert await _account_count(s) == 0


async def test_access_grants_visible_to_grantor_and_grantee(rls_session_factory):
    """grant_visibility: grantor-org (A) and grantee-org (B) see it; org C does not."""
    app_factory, seed = rls_session_factory
    org_a = await seed("Org A grantor", "U10000001")
    org_b = await seed("Org B firm", "U20000002")
    org_c = await seed("Org C unrelated", "U30000003")

    from ibkr_control.db.models.access_grants import AccessGrant
    from ibkr_control.db.models.parties import Party

    # Seed a Party in A + an AccessGrant (grantor party in A, grantee org = B,
    # organization_id = A). app_rls has INSERT privilege; running under context A
    # makes the parties + access_grants WITH CHECK (organization_id = A) pass.
    async with app_factory() as s:
        await _set_org(s, org_a)
        party = Party(organization_id=org_a, display_name="Grantor Party")
        s.add(party)
        await s.flush()
        grant = AccessGrant(
            grantor_party_id=party.id,
            grantee_organization_id=org_b,
            grantee_user_id=None,
            organization_id=org_a,
            role="read_only",
            valid_from=date(2026, 1, 1),
        )
        s.add(grant)
        await s.commit()

    async def _grant_count(org_id, user_id=0):
        async with app_factory() as s:
            await _set_org(s, org_id)
            await _set_user(s, user_id)
            return (await s.execute(text("SELECT count(*) FROM access_grants"))).scalar_one()

    # Grantor org (A) sees it via the organization_id arm.
    assert await _grant_count(org_a) == 1
    # Grantee org (B) sees it via the grantee_organization_id arm.
    assert await _grant_count(org_b) == 1
    # Unrelated org (C) does NOT see it.
    assert await _grant_count(org_c) == 0
