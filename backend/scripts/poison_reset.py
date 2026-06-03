"""Manual recovery script for poison XML imports.

Usage:
    docker compose exec backend uv run python -m scripts.poison_reset --org-id 1 --xml-hash abc123...

Deletes the flex_imports row with status='poison' for the given (organization_id,
xml_hash) — the per-org dedup scope (uq_flex_imports_org_xml_hash). The next
ingest of the same XML for that org will reprocess from scratch (presumably
after the underlying parser bug was fixed).
"""

import argparse
import asyncio
import sys

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ibkr_control.db.models.flex_raw import FlexImport
from ibkr_control.db.session import get_engine


async def reset_poison(session: AsyncSession, *, organization_id: int, xml_hash: str) -> int:
    """Delete poison row. Returns count of rows deleted (0 or 1).

    Safety: only deletes rows with status='poison'. Never touches 'ok' rows.
    Scoped to (organization_id, xml_hash) — the dedup natural key.
    """
    result = await session.execute(
        delete(FlexImport).where(
            FlexImport.organization_id == organization_id,
            FlexImport.xml_hash == xml_hash,
            FlexImport.status == "poison",
        )
    )
    await session.commit()
    return result.rowcount


async def _main(organization_id: int, xml_hash: str) -> int:
    engine = get_engine()
    session_local = async_sessionmaker(engine, expire_on_commit=False)
    async with session_local() as session:
        n = await reset_poison(session, organization_id=organization_id, xml_hash=xml_hash)
    print(
        f"Deleted {n} poison row(s) for organization_id={organization_id} "
        f"xml_hash={xml_hash[:12]}..."
    )
    return 0 if n > 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-id", type=int, required=True)
    parser.add_argument("--xml-hash", type=str, required=True)
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args.org_id, args.xml_hash)))


if __name__ == "__main__":
    main()
