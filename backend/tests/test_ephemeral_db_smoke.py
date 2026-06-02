"""Smoke test: ephemeral DB boots clean and applies Task 4's phase26 migration."""

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_ephemeral_db_has_phase26_columns_at_head(ephemeral_session_factory):
    async with ephemeral_session_factory() as session:
        result = await session.execute(
            text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name='flex_imports' AND column_name IN ('status', 'poison_reason')
            ORDER BY column_name
        """)
        )
        rows = [r[0] for r in result.fetchall()]
        assert rows == ["poison_reason", "status"]


@pytest.mark.asyncio
async def test_ephemeral_db_has_per_user_unique_constraint(ephemeral_session_factory):
    async with ephemeral_session_factory() as session:
        result = await session.execute(
            text("""
            SELECT constraint_name FROM information_schema.table_constraints
            WHERE table_name='flex_imports'
              AND constraint_name='flex_imports_user_xml_hash_key'
              AND constraint_type='UNIQUE'
        """)
        )
        assert result.fetchone() is not None
