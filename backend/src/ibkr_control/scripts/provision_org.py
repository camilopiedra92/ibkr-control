"""Bootstrap a new organization with owner user, party, and membership.

Usage (from backend/):
    uv run python -m ibkr_control.scripts.provision_org \\
        --org-name "Hogar" --org-type personal \\
        --email owner@example.com --password "s3cr3t" \\
        --party-name "Test Owner"

This script is safe to run once per deployment. It does NOT commit inside
provision_org() — the caller (or __main__) owns the transaction.
"""

import argparse
import asyncio
from dataclasses import dataclass

from fastapi_users.password import PasswordHelper
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.auth.models import User
from ibkr_control.db.models.memberships import Membership
from ibkr_control.db.models.organizations import Organization
from ibkr_control.db.models.parties import Party
from ibkr_control.settings.models import UserSettings


@dataclass(frozen=True)
class ProvisionResult:
    org_id: int
    user_id: int
    party_id: int


async def provision_org(
    session: AsyncSession,
    *,
    org_name: str,
    org_type: str,  # 'personal' | 'firm'
    user_email: str,
    user_password: str,
    party_name: str,
) -> ProvisionResult:
    """Create an Organization with an owner User, Party, and Membership.

    Steps:
    1. Organization(type, name)
    2. User(email, hashed_password, is_active=True, name=party_name)
    3. UserSettings(user_id) — mirrors on_after_register
    4. Membership(user_id, organization_id, role='owner')
    5. Party(organization_id, display_name=party_name, user_id=user.id)

    Does NOT commit — caller owns the transaction.
    """
    password_helper = PasswordHelper()

    # 1. Organization
    org = Organization(type=org_type, name=org_name)
    session.add(org)
    await session.flush()

    # 2. User
    hashed = password_helper.hash(user_password)
    user = User(
        email=user_email,
        hashed_password=hashed,
        is_active=True,
        name=party_name,
    )
    session.add(user)
    await session.flush()

    # 3. UserSettings — mirrors UserManager.on_after_register
    session.add(UserSettings(user_id=user.id))

    # 4. Membership
    session.add(Membership(user_id=user.id, organization_id=org.id, role="owner"))

    # 5. Party
    party = Party(organization_id=org.id, display_name=party_name, user_id=user.id)
    session.add(party)
    await session.flush()

    return ProvisionResult(org_id=org.id, user_id=user.id, party_id=party.id)


def main() -> None:
    """CLI entry point. Opens a session, calls provision_org, commits."""
    parser = argparse.ArgumentParser(
        description="Bootstrap a new organization with owner user and party."
    )
    parser.add_argument("--org-name", required=True, help="Organization display name")
    parser.add_argument(
        "--org-type",
        default="personal",
        choices=["personal", "firm"],
        help="Organization type (default: personal)",
    )
    parser.add_argument("--email", required=True, help="Owner user email")
    parser.add_argument("--password", required=True, help="Owner user password")
    parser.add_argument("--party-name", required=True, help="Party (taxpayer) display name")
    args = parser.parse_args()

    async def _run() -> None:
        from ibkr_control.db.session import get_session_maker

        session_maker = get_session_maker()
        async with session_maker() as session:
            result = await provision_org(
                session,
                org_name=args.org_name,
                org_type=args.org_type,
                user_email=args.email,
                user_password=args.password,
                party_name=args.party_name,
            )
            await session.commit()
        print(f"org_id={result.org_id} user_id={result.user_id} party_id={result.party_id}")

    asyncio.run(_run())


if __name__ == "__main__":
    main()
