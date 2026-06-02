"""Backfill closed_lots.close_datetime + recover collapsed rows (A3 amendment #3).

Para cada flex_import existente:
  1. Lee xml_bytes (A0!)
  2. Re-parsea via el parser nuevo (que ya extrae close_datetime)
  3. DELETE existing closed_lots WHERE flex_import_id = X
  4. Re-INSERT con close_datetime poblado (sin UNIQUE constraint todavia — eso
     lo agrega Revision 7 post-backfill)

USO:
  docker compose exec backend uv run python -m scripts.backfill_closed_lots

Idempotente: corre N veces, siempre deja el estado correcto. Conta rows
recovered (los closed_lots que estaban perdidos por collapse del transaction_id
solo).
"""
import asyncio

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from ibkr_control.db.models.flex_raw import ClosedLot, FlexImport
from ibkr_control.db.session import get_engine
from ibkr_control.ingest.flex import parser as flex_parser_mod
from ibkr_control.ingest.flex.persister import _is_shadow_account


async def main() -> None:
    engine = get_engine()
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    print("=" * 70)
    print("BACKFILL closed_lots.close_datetime (A3 amendment #3)")
    print("=" * 70)
    print()

    async with SessionLocal() as session:
        imports = (
            await session.scalars(select(FlexImport).order_by(FlexImport.id))
        ).all()
        print(f"Encontrados {len(imports)} flex_imports para procesar.")
        print()

        total_before = 0
        total_after = 0

        for fi in imports:
            # Map ibkr_account_id -> accounts.id (necesario para FK)
            from ibkr_control.db.models.accounts import Account
            acc_rows = (await session.scalars(select(Account))).all()
            acc_map = {a.ibkr_account_id: a.id for a in acc_rows}

            # Trade transaction_id -> trade.id map for source_trade_id linking
            from ibkr_control.db.models.flex_raw import Trade
            trades = (
                await session.scalars(
                    select(Trade).where(Trade.flex_import_id == fi.id)
                )
            ).all()
            trade_id_map = {t.transaction_id: t.id for t in trades}

            # Parse xml_bytes (A0)
            parsed = flex_parser_mod.parse(fi.xml_bytes)

            # Filter parsed.closed_lots same as the persister does
            new_rows = []
            for i, cl in enumerate(parsed.closed_lots):
                if _is_shadow_account(cl.ibkr_account_id):
                    continue
                if cl.ibkr_account_id not in acc_map:
                    print(
                        f"  WARNING fi={fi.id}: closed_lot referencia account "
                        f"{cl.ibkr_account_id} que no existe — skip"
                    )
                    continue
                new_rows.append({
                    "flex_import_id": fi.id,
                    "transaction_id": (
                        cl.transaction_id
                        or f"NO-TX-{cl.symbol}-{cl.close_date}-{i}"
                    ),
                    "account_id": acc_map[cl.ibkr_account_id],
                    "symbol": cl.symbol,
                    "open_date": cl.open_date,
                    "close_date": cl.close_date,
                    "close_datetime": cl.close_datetime,
                    "qty": cl.qty,
                    "cost_basis_usd": cl.cost_basis_usd,
                    "proceeds_usd": cl.proceeds_usd,
                    "fifo_pnl_usd": cl.fifo_pnl_usd,
                    "source_trade_id": (
                        trade_id_map.get(cl.transaction_id) if cl.transaction_id else None
                    ),
                })

            # Count current rows + delete + re-insert
            before = await session.scalar(
                select(text("count(*)"))
                .select_from(ClosedLot)
                .where(ClosedLot.flex_import_id == fi.id)
            )
            await session.execute(
                delete(ClosedLot).where(ClosedLot.flex_import_id == fi.id)
            )

            # Plain INSERT (no UPSERT — UNIQUE will exist only post-Rev7)
            if new_rows:
                from sqlalchemy.dialects.postgresql import insert as pg_insert
                stmt = pg_insert(ClosedLot.__table__).values(new_rows)
                await session.execute(stmt)

            await session.commit()

            after = len(new_rows)
            recovered = after - before
            total_before += before
            total_after += after
            print(
                f"  fi={fi.id} anyo={fi.anyo}: "
                f"before={before} after={after} (recovered {recovered:+d})"
            )

        print()
        print(f"TOTAL: {total_before} -> {total_after} "
              f"(recovered {total_after - total_before:+d} closed_lots)")
        print()
        print("Backfill completado. Ahora ejecutar Revision 7 para promote NOT NULL + UNIQUE:")
        print("  docker compose exec backend uv run alembic upgrade head")


if __name__ == "__main__":
    asyncio.run(main())
