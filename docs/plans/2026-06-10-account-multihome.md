# Account Multi-home Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tenancy canónica per-org (patrón Plaid/Sharesight): dos orgs pueden conectar la misma cuenta IBKR, universos aislados — spec M1–M7.

**Architecture:** Flip atómico de unicidad global → per-org en modelos + UNA migración canónica; el persister se SIMPLIFICA (H2 entero se desmantela: `ON CONFLICT DO NOTHING` + re-select pasa a ser correcto porque el único conflicto posible es same-org y RLS sí lo ve); regen de OpenAPI; notas SUPERSEDED en docs.

**Tech Stack:** SQLAlchemy 2.x async, Alembic (autogenerate en container), Postgres 16 RLS, pytest + testcontainers (HOST), orval/pnpm para el cliente TS.

**Spec:** `docs/specs/2026-06-10-account-multihome-design.md` (M1–M7) — leerlo antes de empezar.
**Branch:** `saas/account-multihome` (ya creado desde `main` `2d6206a`). NO crear branches; verificar `git branch --show-current` antes de cada commit.
**Baseline:** 357 tests backend, head Alembic `544a0b2c362c`.

**Mapa de H2 (lo que se desmantela) — verificado contra el código actual:**
- `ingest/flex/persister.py`: comment block `_ACCOUNT_UNIQUE_CONSTRAINT` (~líneas 56-62), clase `AccountClaimedError` (~65-79), `_is_account_unique_violation` (~81), SAVEPOINT en `_ensure_accounts` (~665-738).
- `ingest/flex/job.py` ~154-159: `except flex_persister_mod.AccountClaimedError:` (cleanup + re-raise).
- `api/imports.py` ~104-113: except → 409 `ACCOUNT_CLAIMED`. **OJO: el 409 de hash duplicado (~66-74) NO se toca — es dedup per-org, correcto.**
- `api/setup.py`: helper `_account_claimed_http()` (~107-119) + 3 sitios `except ... raise _account_claimed_http() from exc` (~373, ~419, ~737). **OJO: los 409 de ~541/548 son de otra cosa (step3) — NO tocar.**
- `tests/ingest/flex/test_persister.py`: `test_ensure_accounts_cross_org_collision_raises_account_claimed` (~839) y `test_ensure_accounts_reraises_unrelated_integrity_error` (~903) — se REEMPLAZAN.
- Frontend: grep `ACCOUNT_CLAIMED`/"pertenece a otra" = 0 hits (verificado) → solo regen.

---

### Task 1: Modelos M1/M2/M4 + tests multihome (deja drift test y test nuevo en ROJO)

**Files:**
- Modify: `backend/src/ibkr_control/db/models/accounts.py`
- Modify: `backend/src/ibkr_control/db/models/flex_raw.py`
- Create: `backend/tests/test_account_multihome.py`

NO commitear este task — se commitea atómico con Task 2.

- [ ] **Step 1: Write the failing test (schema multihome)**

Crear `backend/tests/test_account_multihome.py`:

```python
"""Multi-home de cuentas broker (spec 2026-06-10-account-multihome, M1/M3).

Patrón Plaid/Sharesight: dos orgs conectan la misma cuenta IBKR, cada uno en
su universo aislado. Supersede H2 (AccountClaimedError): el conflicto
cross-org deja de existir por diseño.
"""

from pathlib import Path

from sqlalchemy import text

from ibkr_control.db.rls import apply_org_context, set_session_org_context

_FIXTURES = Path(__file__).parent / "fixtures" / "xml"


async def _count(s, table: str) -> int:
    return (await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()


async def test_two_orgs_can_connect_same_ibkr_account(rls_session_factory):
    """M1: la misma ibkr_account_id existe en dos orgs; RLS aísla cada universo."""
    app_factory, seed = rls_session_factory
    org_a = await seed("Org A", "U77770001")
    org_b = await seed("Org B", "U77770001")  # HOY: IntegrityError (UNIQUE global)

    async with app_factory() as s:
        await s.execute(
            text("SELECT set_config('app.current_org', :o, true)").bindparams(o=str(org_a))
        )
        assert await _count(s, "accounts") == 1
    async with app_factory() as s:
        await s.execute(
            text("SELECT set_config('app.current_org', :o, true)").bindparams(o=str(org_b))
        )
        assert await _count(s, "accounts") == 1
    async with app_factory() as s:
        # Sin contexto: default-deny, ninguna de las dos copias visible.
        assert await _count(s, "accounts") == 0


async def test_same_xml_ingested_by_two_orgs_isolated_universes(rls_session_factory):
    """M1+M3: el mismo XML ingerido por dos orgs = dos copias independientes.

    Corre el persister real bajo app_rls con contexto de org (paridad runtime),
    una vez por org, y verifica counts iguales y cero cross-talk.
    """
    from ibkr_control.ingest.flex import parser as flex_parser
    from ibkr_control.ingest.flex import persister as flex_persister

    app_factory, seed = rls_session_factory
    org_a = await seed("Org A", "U99999001")
    org_b = await seed("Org B", "U99999001")

    xml_bytes = sorted(_FIXTURES.glob("*.xml"))[0].read_bytes()
    parsed = flex_parser.parse(xml_bytes)

    counts: dict[int, int] = {}
    for org_id in (org_a, org_b):
        async with app_factory() as s:
            set_session_org_context(s, org_id=org_id, user_id=None)
            await apply_org_context(s, org_id=org_id, user_id=None)
            await flex_persister.persist(
                s,
                parsed=parsed,
                organization_id=org_id,
                xml_bytes=xml_bytes,
                source="manual_upload",
            )
            await s.commit()
        async with app_factory() as s:
            await s.execute(
                text("SELECT set_config('app.current_org', :o, true)").bindparams(o=str(org_id))
            )
            counts[org_id] = await _count(s, "trades")

    assert counts[org_a] == counts[org_b] > 0
    async with app_factory() as s:
        assert await _count(s, "trades") == 0  # sin contexto: nada visible


async def test_reingest_same_org_stays_idempotent(rls_session_factory):
    """M1: la idempotencia per-org sobrevive el re-scoping de conflict_cols."""
    from ibkr_control.ingest.flex import parser as flex_parser
    from ibkr_control.ingest.flex import persister as flex_persister

    app_factory, seed = rls_session_factory
    org_a = await seed("Org A", "U99999001")
    xml_bytes = sorted(_FIXTURES.glob("*.xml"))[0].read_bytes()

    for _ in range(2):
        async with app_factory() as s:
            set_session_org_context(s, org_id=org_a, user_id=None)
            await apply_org_context(s, org_id=org_a, user_id=None)
            await flex_persister.persist(
                s,
                parsed=flex_parser.parse(xml_bytes),
                organization_id=org_a,
                xml_bytes=xml_bytes,
                source="manual_upload",
            )
            await s.commit()

    async with app_factory() as s:
        await s.execute(
            text("SELECT set_config('app.current_org', :o, true)").bindparams(o=str(org_a))
        )
        n_accounts = await _count(s, "accounts")
        n_trades_first = await _count(s, "trades")
    # Una sola copia por org: el segundo persist dedupeó por (org, xml_hash)
    # y/o por las natural keys per-org. accounts: la seed + las del XML, sin dupes.
    assert n_trades_first > 0
    async with app_factory() as s:
        await s.execute(
            text("SELECT set_config('app.current_org', :o, true)").bindparams(o=str(org_a))
        )
        rows = (
            await s.execute(
                text(
                    "SELECT ibkr_account_id, count(*) FROM accounts "
                    "GROUP BY ibkr_account_id HAVING count(*) > 1"
                )
            )
        ).all()
    assert rows == []  # cero duplicados same-org


async def test_ensure_accounts_same_org_conflict_is_idempotent(db_session, sample_org):
    """M3: ON CONFLICT DO NOTHING + re-select cubre el race same-org (ex-H2).

    Pre-inserta una cuenta y llama _ensure_accounts con esa + una nueva: no
    explota, devuelve el map completo (la pre-existente con su id original).
    """
    from ibkr_control.db.models.accounts import Account
    from ibkr_control.ingest.flex.persister import _ensure_accounts

    pre = Account(organization_id=sample_org.id, ibkr_account_id="U88880001", currency="USD")
    db_session.add(pre)
    await db_session.flush()

    mapping = await _ensure_accounts(
        db_session, ["U88880001", "U88880002"], organization_id=sample_org.id
    )
    assert mapping["U88880001"] == pre.id
    assert set(mapping) == {"U88880001", "U88880002"}
```

NOTA fixture: `rls_session_factory` vive en `backend/tests/conftest_ephemeral_db.py` (DB efímera migrada por Alembic per-test). Si el archivo nuevo no lo resuelve automáticamente, verificar cómo lo importan los tests existentes que lo usan (`grep -rn rls_session_factory backend/tests/*.py`) y replicar el mecanismo (probablemente plugin/conftest import — NO copiar el fixture).

- [ ] **Step 2: Run para verificar ROJO por la razón correcta**

Run: `cd backend && uv run pytest tests/test_account_multihome.py -x -q`
Expected: el primer test FALLA en el segundo `seed(...)` con `IntegrityError ... uq_accounts_ibkr_account_id` (UNIQUE global vigente). Si falla por import/fixture, arreglar eso primero — el rojo válido es el de la constraint.

- [ ] **Step 3: `accounts.py` — UNIQUE per-org (M1) + comment nuevo (M4)**

Reemplazar la clase `Account` para que quede así (cambios: `unique=True` fuera de la columna; `UniqueConstraint` + import; comment reescrito; el `Index(None, "organization_id")` SE ELIMINA — M2, la composite lo cubre):

```python
"""Cuenta IBKR (Uxxxxxxxx) — multi-home: una fila POR ORG que la conecta."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "ibkr_account_id", name="uq_accounts_org_ibkr_account_id"
        ),
        {
            "comment": (
                "Multi-home (patrón Plaid, spec 2026-06-10): la misma cuenta "
                "broker puede existir en N orgs, una fila por org — universos "
                "aislados, el SaaS no verifica exclusividad de propiedad. "
                "Dentro de un org sigue siendo identidad compartida: sin "
                "user_id, propiedad vía participations (la conjunta es 50/50)."
            )
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    ibkr_account_id: Mapped[str] = mapped_column(String, nullable=False)
    alias: Mapped[str | None] = mapped_column(String, nullable=True)
    currency: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'USD'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
```

(`Index` ya no se importa si no queda otro uso en el archivo.)

- [ ] **Step 4: `flex_raw.py` — uniques per-org en los 3 hechos (M1) + drops M2 + comments M4**

**`Trade`**: en la columna, quitar `unique=True`:
```python
    transaction_id: Mapped[str] = mapped_column(String, nullable=False)
```
En `__table_args__`: agregar la composite, ELIMINAR `Index(None, "organization_id")`, reescribir comment:
```python
    __table_args__ = (
        CheckConstraint("open_close IS NULL OR open_close IN ('O', 'C')", name="open_close"),
        CheckConstraint("buy_sell IN ('BUY', 'SELL')", name="buy_sell"),
        UniqueConstraint("organization_id", "transaction_id", name="uq_trades_org_transaction_id"),
        Index(None, "account_id", "symbol"),
        Index(None, "trade_date"),
        Index(None, "flex_import_id"),
        {
            "comment": (
                "Account-scoped. Visibilidad vía participations; sin user_id. "
                "transaction_id único POR TENANT (multi-home, spec 2026-06-10): "
                "la misma cuenta broker puede existir en N orgs, cada org tiene "
                "su copia de los hechos."
            )
        },
    )
```

**`Transfer`**: ídem — quitar `unique=True` de `transaction_id`, agregar `UniqueConstraint("organization_id", "transaction_id", name="uq_transfers_org_transaction_id")`, eliminar `Index(None, "organization_id")`, mismo texto de comment nuevo (reemplaza el "UNIQUE global correcto").

**`CashTransaction`**: ídem — `uq_cash_transactions_org_transaction_id`, eliminar `Index(None, "organization_id")`, mismo comment nuevo.

**`ClosedLot`**: la natural key gana `organization_id` al frente y se elimina `Index(None, "organization_id")`:
```python
        UniqueConstraint(
            "organization_id",
            "transaction_id",
            "close_datetime",
            "qty",
            "fifo_pnl_usd",
            name="uq_closed_lots_natural_key",
        ),
```
y en su comment, reemplazar la frase final "transaction_id NO es único global — múltiples ejecuciones de cierre lo comparten (amendment A3)." por "transaction_id NO es único ni global ni per-org — múltiples ejecuciones de cierre lo comparten (amendment A3); la key es per-tenant (multi-home, spec 2026-06-10)."

**NO tocar**: `OpenPositionLot`, los 2 accruals, `FlexImport`, `FlexImportAccount`, `counterparties` (sus keys ya son per-org directas o transitivas vía `account_id`) — conservan sus `Index(None, "organization_id")`.

- [ ] **Step 5: Drift test ROJO (red de modelos)**

Run: `cd backend && uv run pytest tests/test_migrations.py -x -q 2>&1 | tail -5`
Expected: FAIL listando los cambios de uniques/índices. Ruff: `uv run ruff check .` limpio.

---

### Task 2: El flip atómico — migración canónica + persister + desmantelar H2

**Files:**
- Create: `backend/alembic/versions/<hash>_account_multihome.py` (autogenerado)
- Modify: `backend/src/ibkr_control/ingest/flex/persister.py`
- Modify: `backend/src/ibkr_control/ingest/flex/job.py`
- Modify: `backend/src/ibkr_control/api/imports.py`
- Modify: `backend/src/ibkr_control/api/setup.py`
- Modify: `backend/tests/ingest/flex/test_persister.py`

- [ ] **Step 1: Migración canónica (M6) — autogenerate DENTRO del container**

```bash
make dev
docker compose -f compose.yaml -f compose.dev.yaml run --rm migrate uv run alembic upgrade head
docker compose -f compose.yaml -f compose.dev.yaml run --rm migrate \
  uv run alembic revision --autogenerate -m "account multihome: per-org uniqueness"
```
(El servicio `migrate` corre como owner y bind-mountea `backend/alembic/` — patrón validado en sp1-db-hardening. Sanity check previo: `docker compose -f compose.yaml -f compose.dev.yaml run --rm migrate grep -c uq_accounts_org_ibkr_account_id /app/src/ibkr_control/db/models/accounts.py` debe dar 1.)

Auditar el archivo generado:
1. Remover falso positivo `apscheduler_jobs` si aparece (upgrade Y downgrade).
2. Esperado en `upgrade()`: drop de `uq_accounts_ibkr_account_id` (o el unique index `ix_`/constraint que autogenerate detecte para el `unique=True` viejo), drop de los uniques de `transaction_id` en trades/transfers/cash_transactions, drop del `uq_closed_lots_natural_key` viejo; create de los 5 composites nuevos; drop de los 5 índices `ix_*_organization_id` (accounts, trades, transfers, cash_transactions, closed_lots); 2 `create_table_comment`/`alter_column` de comments (accounts + los 3 hechos + closed_lots — autogenerate detecta comments; si falta alguno, agregarlo a mano con el texto EXACTO de los modelos).
3. `down_revision = "544a0b2c362c"`. Downgrade simétrico. Docstring: documentar que el downgrade recrea uniques GLOBALES → solo ejecutable sin datos multi-home (si dos orgs comparten cuenta, el downgrade falla en la constraint — comportamiento correcto, documentarlo).
4. Verificar reversibilidad:
```bash
docker compose -f compose.yaml -f compose.dev.yaml run --rm migrate uv run alembic upgrade head
docker compose -f compose.yaml -f compose.dev.yaml run --rm migrate uv run alembic downgrade -1
docker compose -f compose.yaml -f compose.dev.yaml run --rm migrate uv run alembic upgrade head
```

- [ ] **Step 2: Persister — `_ensure_accounts` canónico + remover H2 (M3)**

En `backend/src/ibkr_control/ingest/flex/persister.py`:

ELIMINAR: el comment block + `_ACCOUNT_UNIQUE_CONSTRAINT` (~56-62), la clase `AccountClaimedError` (~65-79), `_is_account_unique_violation` (~81-…), y el import de `IntegrityError` si queda sin uso (ruff lo marca).

REEMPLAZAR `_ensure_accounts` completo por:

```python
async def _ensure_accounts(
    session: AsyncSession,
    ibkr_ids: list[str],
    *,
    organization_id: int,
) -> dict[str, int]:
    """Ensure per-org rows exist for the given IBKR account IDs. Returns ibkr_id -> db id.

    Multi-home (spec 2026-06-10): accounts son únicos POR ORG
    (uq_accounts_org_ibkr_account_id) — la misma cuenta broker puede existir en
    N orgs. El SELECT scopea por organization_id EXPLÍCITAMENTE (no confía en
    el RLS-blinding: correcto bajo cualquier rol, tests con owner incluidos).

    Concurrencia same-org (ex-H2, ahora trivial): INSERT ... ON CONFLICT
    (organization_id, ibkr_account_id) DO NOTHING + re-select. El conflicto
    cross-org dejó de existir por diseño; el same-org (dos uploads paralelos,
    spec D9 sin advisory lock) lo absorbe el ON CONFLICT y el re-select SÍ ve
    la fila (mismo org) — el razonamiento de H2 sobre por qué esto no
    alcanzaba aplicaba SOLO a la unicidad global (SUPERSEDED).
    """
    if not ibkr_ids:
        return {}

    scoped = select(Account).where(
        Account.organization_id == organization_id,
        Account.ibkr_account_id.in_(ibkr_ids),
    )
    existing: dict[str, int] = {
        a.ibkr_account_id: a.id for a in (await session.scalars(scoped)).all()
    }

    missing = sorted(set(ibkr_ids) - set(existing))
    if missing:
        stmt = (
            pg_insert(Account.__table__)
            .values(
                [
                    {
                        "organization_id": organization_id,
                        "ibkr_account_id": ibkr_id,
                        "alias": None,
                        "currency": "USD",
                    }
                    for ibkr_id in missing
                ]
            )
            .on_conflict_do_nothing(index_elements=["organization_id", "ibkr_account_id"])
        )
        await session.execute(stmt)
        existing = {
            a.ibkr_account_id: a.id for a in (await session.scalars(scoped)).all()
        }

    return existing
```

(`pg_insert` ya está importado en el módulo vía `_upsert_helpers`; si no, `from sqlalchemy.dialects.postgresql import insert as pg_insert`.)

ACTUALIZAR conflict_cols (3 sitios — los números de línea pueden correr, anclar por contenido):
- Trades (~313): `["transaction_id"]` → `["organization_id", "transaction_id"]`
- CashTransaction (~385): ídem
- Transfer (~439): ídem
- ClosedLot natural key (~355-360): la lista de conflict cols gana `"organization_id"` al frente.

- [ ] **Step 3: Desmantelar handlers (M3)**

`ingest/flex/job.py` ~154-159: eliminar el bloque `except flex_persister_mod.AccountClaimedError:` entero (deja que el flujo siga normal — el error ya no existe).

`api/imports.py` ~104-113: eliminar `except flex_persister_mod.AccountClaimedError as exc:` + el raise del 409 `ACCOUNT_CLAIMED`. NO tocar el 409 de hash duplicado (~66-74).

`api/setup.py`: eliminar el helper `_account_claimed_http()` (~107-119) y desenvolver los 3 sitios — donde dice:
```python
    except flex_persister_mod.AccountClaimedError as exc:
        raise _account_claimed_http() from exc
```
queda la llamada a `persist(...)` sin try/except (en los 3: ~373, ~419, ~737). NO tocar los 409 de ~541/548 (step3, otra semántica).

Verificar: `grep -rn "AccountClaimedError\|ACCOUNT_CLAIMED\|_account_claimed" backend/src` → 0 hits.

- [ ] **Step 4: Reemplazar los tests H2**

En `backend/tests/ingest/flex/test_persister.py`: ELIMINAR `test_ensure_accounts_cross_org_collision_raises_account_claimed` (~839-901) y `test_ensure_accounts_reraises_unrelated_integrity_error` (~903-919+) completos (su semántica está superseded; los reemplazos viven en `tests/test_account_multihome.py` de Task 1). Si otros tests del archivo referencian `AccountClaimedError`, actualizarlos (grep).

- [ ] **Step 5: Verificación del flip**

```bash
cd backend && uv run pytest tests/test_account_multihome.py tests/test_migrations.py -q
```
Expected: TODOS PASS (los multihome de Task 1 pasan a verde; drift verde).
```bash
cd backend && uv run pytest -q
```
Expected: suite completa verde — count esperado 357 − 2 (H2) + 4 (multihome) = **359** (verificar el real).
```bash
cd backend && uv run ruff check . && uv run ruff format --check .
```

- [ ] **Step 6: Commit atómico**

```bash
git add backend/src backend/tests backend/alembic
git commit -m "feat(account-multihome): M1-M4+M6 — unicidad per-org, H2 desmantelado, persister simplificado

Patrón Plaid/Sharesight: la misma cuenta broker puede existir en N orgs,
universos aislados. AccountClaimedError eliminado (el conflicto cross-org
dejó de existir); _ensure_accounts pasa a ON CONFLICT DO NOTHING + re-select
explícitamente org-scoped. 5 índices org redundantes eliminados (las
composites cubren el prefijo RLS). Migración canónica reversible."
```

---

### Task 3: OpenAPI + frontend (M5)

**Files:**
- Modify: `frontend/openapi.json` + `frontend/src/lib/api/generated.ts` (o el path que produzca `pnpm openapi:gen`)

- [ ] **Step 1: Regen canónico (lección D12 — container corriendo, flujo documentado)**

```bash
docker compose -f compose.yaml -f compose.dev.yaml up -d --build backend
export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"
cd frontend && pnpm openapi:gen
```

- [ ] **Step 2: Verificar el diff y el build**

```bash
git diff --stat frontend/
grep -rn "ACCOUNT_CLAIMED" frontend/ --include="*.ts" --include="*.tsx" --include="*.json" | grep -v node_modules
cd frontend && pnpm lint && pnpm build 2>&1 | tail -5
```
Expected: el diff remueve las respuestas 409 de los endpoints afectados; grep = 0; lint/build verdes. Si `pnpm build` falla por PATH, usar `make prod-local` para validar el build dentro del container.

- [ ] **Step 3: Commit**

```bash
git add frontend/
git commit -m "chore(account-multihome): M5 — regen cliente TS (409 ACCOUNT_CLAIMED eliminado del contrato)"
```

---

### Task 4: Docs M7 + verificación final + PR

**Files:**
- Modify: `docs/specs/2026-06-03-sp1-hardening-close-gaps-design.md`
- Modify: `CLAUDE.md`
- Modify: `docs/specs/2026-06-03-saas-program-roadmap.md` (si menciona el UNIQUE global)
- Modify: `docs/specs/2026-06-10-account-multihome-design.md` (línea Estado)

- [ ] **Step 1: Notas SUPERSEDED (no reescribir historia)**

1. En `docs/specs/2026-06-03-sp1-hardening-close-gaps-design.md`, inmediatamente después del título/encabezado del punto H2 (la sección que arranca "Colisión de cuenta cross-org"), insertar:
   > **⚠️ SUPERSEDED (2026-06-10):** H2 fue desmantelado por `docs/specs/2026-06-10-account-multihome-design.md` (M3) — la unicidad pasó a per-org (patrón Plaid/Sharesight) y el conflicto cross-org dejó de existir por diseño. El razonamiento de abajo era correcto BAJO unicidad global; se preserva como registro histórico.
2. En `CLAUDE.md`:
   - En el bullet de SP1-hardening, la frase "`accounts.ibkr_account_id` UNIQUE-global = cuenta broker single-org por diseño" → agregarle ` (**SUPERSEDED 2026-06-10** → multi-home per-org, ver spec account-multihome)`.
   - En la lección Phase 2.6 ("`accounts.ibkr_account_id` es UNIQUE global — CORRECTO, no un bug (corregido en Phase 2.8)") y donde Phase 2.8/H4-H5 afirman "ibkr_account_id UNIQUE global es correcto" / "transaction_id UNIQUE global también es correcto": agregar al final de cada una ` **[SUPERSEDED 2026-06-10: multi-home per-org — spec account-multihome]**` (grep "UNIQUE global" CLAUDE.md para no dejar ninguna).
   - Bullet nuevo en "Estado del programa SaaS" (después del de SP1-db-hardening), con el count real de tests del Task 2 Step 5:
   > - **Account-multihome (branch `saas/account-multihome`, PR #N, CI verde — pending merge):** invirtió la decisión "cuenta broker single-org" → **tenancy canónica per-org (patrón Plaid/Sharesight)**: la misma cuenta IBKR puede existir en N orgs, universos aislados (cada org su copia de los hechos, trade-off de duplicación/divergencia aceptado explícitamente). Unicidad org-scoped uniforme (`(org, ibkr_account_id)`, `(org, transaction_id)` ×3, natural key de closed_lots + org); 5 índices org redundantes eliminados (las composites cubren el prefijo RLS); **H2 desmantelado entero** (AccountClaimedError/409 — el conflicto cross-org dejó de existir; `_ensure_accounts` se SIMPLIFICÓ a ON CONFLICT + re-select org-scoped explícito, que además cierra el race same-org de uploads paralelos y el vector de squatting del upload manual). `access_grants`/participations intactos (SP2 igual). Migración canónica reversible. Tests 357 → **35X**. Spec: `docs/specs/2026-06-10-account-multihome-design.md` · Plan: `docs/plans/2026-06-10-account-multihome.md`.
   - En el bullet "SIGUIENTE — SP2": "(hardening + rls-runtime-wiring + db-hardening)" → "(hardening + rls-runtime-wiring + db-hardening + account-multihome)".
3. `grep -n "single-org\|UNIQUE global\|UNIQUE-global" docs/specs/2026-06-03-saas-program-roadmap.md` — si hay mención, misma nota SUPERSEDED.
4. En el spec multihome, línea Estado → "implementado (PR #N, CI verde — pending merge)".

- [ ] **Step 2: Verificación final**

```bash
cd backend && uv run pytest -q          # suite completa, count = el de Task 2
cd backend && uv run ruff check . && uv run ruff format --check .
make dev && docker compose -f compose.yaml -f compose.dev.yaml logs backend --tail 20  # boot guard OK
docker compose -f compose.yaml -f compose.dev.yaml exec postgres \
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\d accounts"' | grep -E "uq_accounts|ix_accounts"
```
Expected: suite verde; boot limpio; `uq_accounts_org_ibkr_account_id` presente y `ix_accounts_organization_id` AUSENTE.

- [ ] **Step 3: Commit docs + push + PR**

```bash
git add CLAUDE.md docs/
git commit -m "docs(account-multihome): M7 — notas SUPERSEDED (H2, single-org) + registro de programa"
git push -u origin saas/account-multihome
gh pr create --title "Account multi-home: tenancy canónica per-org (patrón Plaid/Sharesight)" --body "$(cat <<'EOF'
Invierte la decisión "cuenta broker single-org por diseño" → patrón SaaS
canónico (spec M1-M7, docs/specs/2026-06-10-account-multihome-design.md):
dos orgs pueden conectar la misma cuenta IBKR, cada uno en su universo
aislado.

- M1: unicidad org-scoped uniforme — (org, ibkr_account_id), (org,
  transaction_id) x3, natural key de closed_lots + org. Lots/accruals sin
  cambios (per-org transitivos vía account_id).
- M2: 5 índices ix_*_organization_id redundantes eliminados (las composites
  nuevas cubren el prefijo que usa la política RLS).
- M3: H2 desmantelado entero — AccountClaimedError/SAVEPOINT/409 eliminados;
  _ensure_accounts simplificado a ON CONFLICT DO NOTHING + re-select
  org-scoped explícito (cubre además el race same-org y elimina el vector de
  squatting del upload manual).
- M4: 4 comments de schema reescritos (afirmaban "UNIQUE global correcto").
- M5: cliente TS regenerado (409 ACCOUNT_CLAIMED fuera del contrato).
- M6: UNA migración canónica reversible (downgrade documentado: requiere no
  tener datos multi-home).
- M7: notas SUPERSEDED en spec H2 + CLAUDE.md + roadmap.

access_grants y participations intactos — SP2 (enforcement de delegación)
sigue igual. Trade-off aceptado explícitamente: cada org tiene su copia de
los hechos (duplicación/divergencia por timing de ingest).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
