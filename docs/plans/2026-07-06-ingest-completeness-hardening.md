# Ingest-completeness + Integrity Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cerrar tres huecos de captura/integridad en la capa de datos (IC-1 cash capture, IC-2 instrument country, IC-3 participations non-overlap) antes de que Phase 3 los pise.

**Architecture:** Cambios aditivos a 3 modelos + UN baseline amendment canónico (política Tier 1, pre-deploy) + lógica de parser/persister. Cero cambio de API/frontend. IC-3 es una integrity guard completa; IC-1/IC-2 son captura de source data hoy descartada.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x async, Alembic, Postgres 16 (`btree_gist`), pytest (`-n auto`, template-clone infra bajo `app_rls` + FORCE RLS), lxml parser.

**Spec:** `docs/specs/2026-07-06-ingest-completeness-hardening-design.md`

## Global Constraints

- **Baseline mutable pre-deploy:** amendment canónico regenerado con `alembic revision --autogenerate` **dentro del container backend** (el host no alcanza Postgres). NUNCA hand-edit del DDL autogenerable (lección D12/Phase 2.9). DDL que autogenerate no expresa (`CREATE EXTENSION`, `EXCLUDE`) se agrega manual al mismo archivo.
- **UN solo baseline amendment** para todo el PR (no migraciones transicionales). Downgrade reversible.
- Tests corren bajo `app_rls` + FORCE RLS. Un test que crea org inline o abre sesión aparte tocando tablas org-scoped DEBE `await scope_session_to_org(session, org_id)` antes de la primera query. Tests owner-only usan `owner_session`/`owner_engine`.
- `Decimal` para dinero, nunca float. No emojis en código. English identifiers, Spanish en UI/docstrings.
- Ruff clean (`uv run ruff check .` + `uv run ruff format .`) al cierre de cada task.
- Comandos de test: `cd backend && uv run pytest -q` (paralelo) o `-n0` para serial/un archivo.
- Verificar la revisión del baseline actual antes de amendar: `cd backend && uv run alembic heads` y listar `backend/alembic/versions/`. El baseline pristino está en amendment #8 (SP2). Este PR produce amendment #9.

---

### Task 1: Schema amendment (IC-1 columns + IC-2 column + IC-3 EXCLUDE + migración canónica)

Los tres cambios de schema van juntos porque comparten UN baseline amendment. IC-3 queda **funcionalmente completo** aquí (constraint + test de comportamiento); las columnas IC-1/IC-2 quedan creadas pero sin poblar (las llenan Task 2/3).

**Files:**
- Modify: `backend/src/ibkr_control/db/models/flex_raw.py` (clase `CashTransaction`, ~373-418)
- Modify: `backend/src/ibkr_control/db/models/instruments.py` (clase `Instrument`)
- Modify: `backend/src/ibkr_control/db/models/participations.py` (clase `Participation`)
- Modify: `backend/alembic/versions/<baseline>.py` (el amendment canónico)
- Test: `backend/tests/test_participations_no_overlap.py` (nuevo)

**Interfaces:**
- Produces (Task 2 consume): `CashTransaction` con columnas nullable `action_id: str|None`, `issuer_country: str|None`, `settle_date: date|None`, `report_date: date|None`, `ex_date: date|None`, y `raw_attrs: dict` (JSONB, server_default `'{}'`).
- Produces (Task 3 consume): `Instrument.issuer_country: str|None`.
- Produces: constraint `participations_no_overlap` (EXCLUDE gist) sobre `(organization_id, party_id, account_id, daterange(valid_from, valid_to, '[)'))`.

- [ ] **Step 1: Write the failing test (IC-3 overlap rejected)**

`backend/tests/test_participations_no_overlap.py`:

```python
import pytest
from datetime import date
from sqlalchemy.exc import IntegrityError
from ibkr_control.db.models.participations import Participation


@pytest.mark.asyncio
async def test_overlapping_participation_rejected(db_session, sample_org, sample_party, sample_account):
    # Primera participación: [2024-01-01, 2025-01-01)
    db_session.add(Participation(
        party_id=sample_party.id, account_id=sample_account.id,
        organization_id=sample_org.id, pct=Decimal("0.50"),
        valid_from=date(2024, 1, 1), valid_to=date(2025, 1, 1),
    ))
    await db_session.flush()
    # Segunda participación solapada: [2024-06-01, NULL) → debe fallar
    db_session.add(Participation(
        party_id=sample_party.id, account_id=sample_account.id,
        organization_id=sample_org.id, pct=Decimal("1.00"),
        valid_from=date(2024, 6, 1), valid_to=None,
    ))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_disjoint_participations_accepted(db_session, sample_org, sample_party, sample_account):
    db_session.add(Participation(
        party_id=sample_party.id, account_id=sample_account.id,
        organization_id=sample_org.id, pct=Decimal("0.50"),
        valid_from=date(2024, 1, 1), valid_to=date(2025, 1, 1),
    ))
    # Adyacente half-open: [2025-01-01, NULL) NO solapa con [.., 2025-01-01)
    db_session.add(Participation(
        party_id=sample_party.id, account_id=sample_account.id,
        organization_id=sample_org.id, pct=Decimal("1.00"),
        valid_from=date(2025, 1, 1), valid_to=None,
    ))
    await db_session.flush()  # no raise
```

Verificar los fixtures `sample_party`/`sample_account` existentes en `conftest.py`; si no existen con esos nombres, crear las filas inline y llamar `await scope_session_to_org(db_session, sample_org.id)` antes del primer `add`. Importar `Decimal`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest -n0 tests/test_participations_no_overlap.py -v`
Expected: `test_overlapping_participation_rejected` FALLA (no se levanta IntegrityError — el constraint aún no existe).

- [ ] **Step 3: Add the three model changes**

En `flex_raw.py`, clase `CashTransaction`, tras `symbol` (línea ~418). Reusar los imports existentes (`Date`, `String`, `text` ya están; `JSONB`/`Any` los usan los accruals en el mismo archivo):

```python
    settle_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    report_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    ex_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    issuer_country: Mapped[str | None] = mapped_column(String, nullable=True)
    action_id: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_attrs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
```

En `instruments.py`, clase `Instrument`, tras `multiplier`:

```python
    issuer_country: Mapped[str | None] = mapped_column(String, nullable=True)
```

En `participations.py`, agregar el import y el constraint en `__table_args__`:

```python
from sqlalchemy import literal_column, text
from sqlalchemy.dialects.postgresql import ExcludeConstraint
# ... dentro de __table_args__, ANTES del dict de comment:
        ExcludeConstraint(
            ("organization_id", "="),
            ("party_id", "="),
            ("account_id", "="),
            (literal_column("daterange(valid_from, valid_to, '[)')"), "&&"),
            using="gist",
            name="participations_no_overlap",
        ),
```

- [ ] **Step 4: Regenerate the baseline amendment canónicamente en container**

Levantar el stack dev y autogenerar DENTRO del container (el host no alcanza Postgres):

```bash
make dev
docker compose exec backend uv run alembic revision --autogenerate -m "ingest-completeness IC-1/2/3"
```

Esto emite un archivo nuevo. **Política Tier 1 = UN baseline canónico:** en vez de dejar una revisión transicional, fusionar estos cambios en el baseline pristino (amendment #9) — mover el DDL autogenerado (ADD COLUMN de cash + instruments) al baseline y borrar la revisión transicional, o regenerar el baseline según el flujo de amendment usado en SP2 (ver `2026-06-12-sp2-authorization.md` cómo se hizo el amendment #8). Verificar que el `down_revision` del baseline no cambia.

- [ ] **Step 5: Add the manual DDL que autogenerate NO expresa**

Autogenerate NO emite `CREATE EXTENSION` ni (confiablemente) el `EXCLUDE`. Agregar al `upgrade()` del baseline, y su reverso en `downgrade()`:

```python
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    # ... tras crear la tabla participations:
    op.execute(
        "ALTER TABLE participations ADD CONSTRAINT participations_no_overlap "
        "EXCLUDE USING gist ("
        "organization_id WITH =, party_id WITH =, account_id WITH =, "
        "daterange(valid_from, valid_to, '[)') WITH &&)"
    )
```

Si autogenerate SÍ emitió el `ExcludeConstraint` (SQLAlchemy a veces lo hace vía el dialect postgresql), usar esa versión y NO duplicar. `CREATE EXTENSION` siempre es manual y debe ir antes del primer uso de `gist` en la tabla.

- [ ] **Step 6: Wipe & reload dev + drift + boot smoke**

```bash
docker compose down -v && make dev
docker compose exec backend uv run alembic upgrade head
```

Recargar los XMLs por el wizard (o el flujo de reload documentado). Correr el drift test:

Run: `cd backend && uv run pytest -n0 tests/test_migrations.py -v`
Expected: PASS — `compare_metadata` sin drift (los modelos matchean el baseline amendado). **Si el EXCLUDE genera drift espurio** (autogenerate no round-trip-ea la expresión daterange), ajustar la definición del `ExcludeConstraint` en el modelo hasta que `compare_metadata` quede limpio — NO excluir el constraint de la comparación (rompería la pureza del drift test). Este es el punto de mayor riesgo del task; iterar hasta verde.

Boot smoke: confirmar en logs que `backend` arranca como `app_rls` (`rolsuper=f/rolbypassrls=f`).

- [ ] **Step 7: Run the IC-3 tests to verify they pass**

Run: `cd backend && uv run pytest -n0 tests/test_participations_no_overlap.py -v`
Expected: ambos PASS (overlap rechazado, disjuntos aceptados).

- [ ] **Step 8: Verify template-clone preserva el EXCLUDE + extensión**

Agregar en `tests/test_template_clone_fidelity.py` (o el archivo de fidelidad existente) una aserción de que el clon del template tiene el constraint `participations_no_overlap`. Si el patrón existente ya verifica constraints/FORCE RLS, extenderlo; si no, aserción mínima consultando `pg_constraint`.

Run: `cd backend && uv run pytest -n0 tests/test_template_clone_fidelity.py -v`
Expected: PASS.

- [ ] **Step 9: Ruff + commit**

```bash
cd backend && uv run ruff format . && uv run ruff check .
git add backend/ && git commit -m "feat(ingest): IC-3 participations non-overlap EXCLUDE + IC-1/2 schema columns (baseline amendment)"
```

---

### Task 2: IC-1 — cash_transactions capture logic

Poblar las columnas IC-1 (ya existen desde Task 1) extrayendo del XML lo que el parser hoy descarta.

**Files:**
- Modify: `backend/src/ibkr_control/ingest/flex/_models.py` (`ParsedCashTransaction`, ~96-107)
- Modify: `backend/src/ibkr_control/ingest/flex/parser.py` (`_parse_cash_transactions`, ~477-503)
- Modify: el persister de cash (buscar donde se construye el INSERT de `cash_transactions`, p.ej. `ingest/flex/persister*.py` o `_ensure`/`_upsert` de cash)
- Test: `backend/tests/test_doc_ibkr_flex.py` o el archivo de tests del parser cash + un roundtrip en `tests/test_source_precision_fidelity.py`

**Interfaces:**
- Consumes (de Task 1): columnas `action_id/issuer_country/settle_date/report_date/ex_date/raw_attrs` en `CashTransaction`.
- Produces: `ParsedCashTransaction` con `action_id: str|None`, `issuer_country: str|None`, `settle_date: date|None`, `report_date: date|None`, `ex_date: date|None`, `raw_attrs: dict`.

- [ ] **Step 1: Write the failing test (parser extrae los 5 campos + raw_attrs)**

En el archivo de tests del parser cash, con un `<CashTransaction>` real (dividendo con `actionID`, `issuerCountryCode`, `exDate`, `settleDate`, `reportDate`) — usar un fragmento de los fixtures sanitizados reales:

```python
def test_cash_transaction_captures_fiscal_fields():
    parsed = parse(SAMPLE_XML_WITH_DIVIDEND)
    tx = next(t for t in parsed.cash_transactions if t.type == "Dividends")
    assert tx.action_id is not None
    assert tx.issuer_country == "US"
    assert tx.settle_date is not None
    assert tx.report_date is not None
    assert tx.ex_date is not None
    # raw_attrs captura atributos no tipados (red de seguridad)
    assert isinstance(tx.raw_attrs, dict)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest -n0 <archivo>::test_cash_transaction_captures_fiscal_fields -v`
Expected: FALLA con AttributeError (`ParsedCashTransaction` no tiene `action_id`).

- [ ] **Step 3: Add fields to `ParsedCashTransaction`**

En `_models.py`, clase `ParsedCashTransaction`, tras `conid`:

```python
    settle_date: date | None = None
    report_date: date | None = None
    ex_date: date | None = None
    issuer_country: str | None = None
    action_id: str | None = None
    raw_attrs: dict = field(default_factory=dict)
```

Verificar que `field` y `dict` estén importados (usar `from dataclasses import dataclass, field`).

- [ ] **Step 4: Extract en el parser**

En `parser.py`, `_parse_cash_transactions`, dentro del `ParsedCashTransaction(...)` (línea ~491). Definir el set de atributos tipados y volcar el resto a `raw_attrs` (espejar el patrón de accruals con `_CASH_TYPED_ATTRS`):

```python
                settle_date=_parse_date(tx.get("settleDate")),
                report_date=_parse_date(tx.get("reportDate")),
                ex_date=_parse_date(tx.get("exDate")),
                issuer_country=_attr(tx, "issuerCountryCode"),
                action_id=_attr(tx, "actionID"),
                raw_attrs={k: v for k, v in tx.attrib.items() if k not in _CASH_TYPED_ATTRS},
```

Definir `_CASH_TYPED_ATTRS: frozenset[str]` con todos los atributos que ya mapean a columnas (`transactionID, accountId, type, currency, amount, description, dateTime, settleDate, reportDate, exDate, issuerCountryCode, actionID, symbol, conid, levelOfDetail`), espejando `_DIV_ACCRUAL_TYPED_ATTRS`.

- [ ] **Step 5: Map en el persister**

En el persister de cash, agregar los 6 campos al dict/valores del INSERT de `cash_transactions` (pass-through directo desde el `ParsedCashTransaction`). Seguir el estilo del INSERT existente.

- [ ] **Step 6: Run parser test to verify it passes**

Run: `cd backend && uv run pytest -n0 <archivo>::test_cash_transaction_captures_fiscal_fields -v`
Expected: PASS.

- [ ] **Step 7: Write + run the roundtrip test (XML→DB)**

En `tests/test_source_precision_fidelity.py` (o análogo), extender para verificar que tras ingestar un fixture real, las filas de `cash_transactions` tienen `action_id`/`issuer_country`/`settle_date` no-nulos donde el XML los trae (subset por columna, con guard de non-vacuity: al menos 1 dividendo con action_id).

Run: `cd backend && uv run pytest -n0 tests/test_source_precision_fidelity.py -v`
Expected: PASS.

- [ ] **Step 8: Ruff + full suite + commit**

```bash
cd backend && uv run ruff format . && uv run ruff check . && uv run pytest -q
git add backend/ && git commit -m "feat(ingest): IC-1 cash_transactions capta action_id/issuer_country/settle/report/ex_date + raw_attrs"
```

---

### Task 3: IC-2 — instruments.issuer_country population

Poblar `Instrument.issuer_country` (columna de Task 1) desde los creators, con censo y anti-churn.

**Files:**
- Modify: `backend/src/ibkr_control/ingest/flex/parser.py` (los creators que puedan traer `issuerCountryCode`)
- Modify: el persister `_ensure_instruments` (buscar en `ingest/flex/persister*.py`)
- Modify: `ingest/flex/_models.py` (agregar `issuer_country` al spec de instrument que recolectan los creators, si aplica)
- Test: `backend/tests/test_doc_ibkr_flex.py` (censo) + test de `_ensure_instruments`

**Interfaces:**
- Consumes (de Task 1): `Instrument.issuer_country: str|None`.
- Produces: `_ensure_instruments` puebla `issuer_country` desde el spec del creator con anti-churn material-change `DO UPDATE`.

- [ ] **Step 1: Write the failing test (censo: qué creators traen país)**

```python
def test_census_which_creators_carry_issuer_country():
    parsed = parse(REAL_FIXTURE_2025)
    # Pinnea el censo: al menos los accruals traen issuerCountryCode.
    # Si un creator STK lo trae, la aserción sube; si no, documenta el fallback.
    specs_with_country = [s for s in parsed.instrument_specs if s.issuer_country]
    assert len(specs_with_country) >= 1, "censo vacío — el fallback por-hecho es la única fuente de país"
```

Ajustar `parsed.instrument_specs` al nombre real de la colección de specs de creators que recolecta el parser (buscar cómo `_ensure_instruments` recibe los specs).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest -n0 <archivo>::test_census_which_creators_carry_issuer_country -v`
Expected: FALLA (el spec de instrument aún no tiene `issuer_country`).

- [ ] **Step 3: Extract issuer_country en los creators + spec**

Agregar `issuer_country` al spec de instrument que arman los creators (donde ya recolectan conid/assetCategory/isin/description/currency/multiplier), extrayendo `issuerCountryCode` donde el tag lo traiga. Resolver-never-creates: un resolver con país pero sin instrument no crea.

- [ ] **Step 4: Populate + anti-churn en `_ensure_instruments`**

En `_ensure_instruments`, agregar `issuer_country` al INSERT y al `DO UPDATE` de material-change (misma mecánica que `symbol`: solo avanza en cambio real, no churn si igual/None). Confirmar que un `issuer_country` nuevo desde None se completa (convergencia monótona) pero no se pisa con None.

- [ ] **Step 5: Run census test to verify it passes**

Run: `cd backend && uv run pytest -n0 <archivo>::test_census_which_creators_carry_issuer_country -v`
Expected: PASS.

- [ ] **Step 6: Write + run anti-churn test**

```python
def test_ensure_instruments_populates_and_no_churn_on_country(db_session, ...):
    # 1er ingest: crea instrument con issuer_country="US"
    # 2do ingest mismo conid, mismo país: updated_at NO avanza (no churn)
    # ingest con país nuevo desde None: completa (convergencia)
    ...
```

Run: `cd backend && uv run pytest -n0 <archivo>::test_ensure_instruments_populates_and_no_churn_on_country -v`
Expected: PASS.

- [ ] **Step 7: Ruff + full suite + commit**

```bash
cd backend && uv run ruff format . && uv run ruff check . && uv run pytest -q
git add backend/ && git commit -m "feat(ingest): IC-2 instruments.issuer_country canónico (creators + anti-churn) + censo"
```

---

## Self-Review (completado)

- **Spec coverage:** IC-1 → Task 1 (columns) + Task 2 (logic). IC-2 → Task 1 (column) + Task 3 (logic + censo). IC-3 → Task 1 completo (constraint + tests + template fidelity). Migración/baseline → Task 1 steps 4-6. Tests del spec §5 → cubiertos (parser, roundtrip, ensure/anti-churn, censo, exclude, drift). No-goals respetados (sin API/frontend, sin cómputo fiscal, sin apply_pct/gap-guard, sin parties fiscal).
- **Placeholders:** los `<archivo>` y nombres de fixtures (`sample_party`, `parsed.instrument_specs`, ruta del persister) son punteros a verificar contra el código real en cada task — cada step dice explícitamente qué buscar. No hay lógica sin código mostrado.
- **Type consistency:** `action_id/issuer_country/settle_date/report_date/ex_date/raw_attrs` idénticos entre `CashTransaction` (Task 1), `ParsedCashTransaction` (Task 2) y parser. `issuer_country` idéntico entre `Instrument` (Task 1) y `_ensure_instruments` (Task 3).

## Riesgo conocido

El punto más frágil es el **round-trip del `ExcludeConstraint` con la expresión `daterange` en el drift test** (Task 1 Step 6). Si `compare_metadata` reporta drift espurio, iterar la definición del constraint en el modelo hasta que matchee el DDL de la DB — nunca silenciar la comparación. Es el único step que puede requerir varias vueltas.
