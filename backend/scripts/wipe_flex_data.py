"""Wipe Flex data — gate manual entre Alembic Revision 1 y Revision 2.

USO: docker compose exec backend uv run python -m scripts.wipe_flex_data

Borra flex_imports + children (CASCADE en TRUNCATE wipea referenced tables
regardless del ON DELETE action del FK). Preserva: flex_credentials, accounts,
users, user_settings, apscheduler_jobs, ingest_log, trm_days, trm_imports.

Después de correr este script, ejecutar `alembic upgrade head` para aplicar
Revision 2 (promote transaction_id NOT NULL + UNIQUE + xml_bytes NOT NULL).
"""

import asyncio
import sys

from sqlalchemy import text

from ibkr_control.db.session import get_engine


PRESERVED = (
    "flex_credentials, accounts, users, user_settings, "
    "apscheduler_jobs, ingest_log, trm_days, trm_imports"
)


async def main() -> None:
    print("=" * 70)
    print("WIPE FLEX DATA")
    print("=" * 70)
    print()
    print("Este script va a BORRAR todos los rows de:")
    print("  flex_imports + trades + closed_lots + open_position_lots +")
    print("  cash_transactions + transfers + transfer_lots +")
    print("  change_in_dividend_accruals + open_dividend_accruals")
    print()
    print(f"Preserva: {PRESERVED}")
    print()
    print("Después tenés que:")
    print("  1. docker compose exec backend uv run alembic upgrade head")
    print("     (Revision 2 agrega NOT NULL + UNIQUE constraints)")
    print("  2. Re-uploadear XMLs históricos manualmente desde el wizard")
    print("  3. Trigger Flex WS fetch para YTD del año actual (Settings)")
    print()
    resp = input("Confirmar wipe [y/N]: ").strip().lower()
    if resp != "y":
        print("Cancelado.")
        sys.exit(1)

    engine = get_engine()
    async with engine.begin() as conn:
        # TRUNCATE ... CASCADE wipea flex_imports + todas las tablas con FK
        # referenciando flex_imports en una operación atómica, regardless del
        # ON DELETE action del FK (que ahora es SET NULL post-Rev1).
        await conn.execute(text("TRUNCATE flex_imports CASCADE"))
    print()
    print("Wipe completado.")
    print("Ahora corré: docker compose exec backend uv run alembic upgrade head")


if __name__ == "__main__":
    asyncio.run(main())
