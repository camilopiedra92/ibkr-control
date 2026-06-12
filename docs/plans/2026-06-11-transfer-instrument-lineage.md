# Transfer↔Instrument Lineage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Security transfers (`assetCategory != 'CASH'`) pasan a ser creators del securities master; el resolver de cash hace lookup DB-wide; `instrument_id` de cash converge monótonamente NULL→valor; `asset_class`+`conid` como fidelidad de fuente en transfers/cash; CHECK bicondicional en SQL.

**Architecture:** Mismo patrón W1/W2/W3: baseline mutable pre-deploy (T1-D14) — amendment #6 de `a9977ac077e5` + wipe dev. Split creator/resolver por **calidad de evidencia**, no por tag (TL-D1). Spec: `docs/specs/2026-06-11-transfer-instrument-lineage-design.md` (TL-D1..D6) — **supersede T1-D8/CR-1 parcialmente**.

**Tech Stack:** SQLAlchemy 2 async + pg_insert ON CONFLICT, Alembic (baseline amendment canónico), pytest + template-clone fixtures (`db_session` = `app_rls`, auto-scopeado por `sample_org`).

**Branch:** `tier1/transfer-instrument-lineage` (ya creada, spec commiteado). NO crear branches nuevos.

**Reglas del repo (idénticas a W1/W2/W3):** TDD; tests desde el HOST (`cd backend && uv run pytest`); `uv run ruff check . && uv run ruff format .` antes de cada commit; baseline amendment canónico (autogenerate temporal vía servicio migrate contra DB virgen + splice entre markers, MISMO revision id `a9977ac077e5`); wipe dev (`docker compose -f compose.yaml -f compose.dev.yaml down -v && make dev`) tras el amendment; `_ORG_SCOPED_TABLES` **NO cambia** (no hay tablas nuevas); el drift test (`compare_metadata`) es el gate del schema.

## Evidencia (censo 2026-06-11, no re-verificar)

30 `<Transfer>` reales en los 3 fixtures: STK ×12 (IBIT/BROS/SBET/ZETA/GLOB, INTERNAL y FOP) → conid+isin **100%**; CASH ×18 → conid/isin 100% vacíos, `symbol="--"`. El FOP GLOB trae `conid="160756766" isin="LU0974299876" description="GLOBANT SA"`. En el fixture `ACTIVITY_2026_FOP_sanitized.xml` el conid `160756766` aparece SOLO en los 2 `<Transfer>` (cero creators GLOB en ese XML) — por eso el first-seen congelaba NULL.

## File Map

| Op | File | Qué |
|---|---|---|
| Modify | `backend/src/ibkr_control/ingest/flex/_models.py` | `ParsedTransfer` + 3 campos; comentario falso |
| Modify | `backend/src/ibkr_control/ingest/flex/parser.py` | `_parse_transfers` creator fail-loud; docstring `_require_conid` |
| Modify | `backend/src/ibkr_control/db/models/flex_raw.py` | `Transfer.asset_class/conid` + CHECK; `CashTransaction.conid`; comentario falso |
| Modify | `backend/alembic/versions/a9977ac077e5_tier1_baseline.py` | Amendment #6 (autogen splice) |
| Modify | `backend/src/ibkr_control/ingest/flex/persister.py` | creators de transfers; resolver DB-wide; row builders; cash → variante con convergencia |
| Modify | `backend/src/ibkr_control/ingest/flex/_upsert_helpers.py` | `_upsert_immutable_with_resolution` |
| Modify | `backend/tests/ingest/flex/test_parser.py` | tests nuevos parser |
| Modify | `backend/tests/ingest/flex/test_persister_instruments.py` | flip FOP + tests nuevos |
| Modify | `backend/tests/ingest/flex/test_persister.py:547` + `test_persister_idempotent.py` | constructores `ParsedTransfer` (campo nuevo) |
| Modify | `backend/tests/test_tier1_baseline.py` | columnas + CHECK |
| Modify | `docs/specs/2026-06-10-tier1-worldclass-model-design.md`, `CLAUDE.md` | SUPERSEDED notes |

---

### Task 1: Parser + ParsedTransfer (aditivo, suite verde standalone)

**Files:**
- Modify: `backend/src/ibkr_control/ingest/flex/_models.py` (ParsedTransfer, ~línea 111)
- Modify: `backend/src/ibkr_control/ingest/flex/parser.py` (`_parse_transfers` ~línea 653, docstring `_require_conid` ~línea 311)
- Modify: `backend/tests/ingest/flex/test_persister.py` (~línea 547) y `backend/tests/ingest/flex/test_persister_idempotent.py` (1 constructor c/u)
- Test: `backend/tests/ingest/flex/test_parser.py`

- [ ] **Step 1 (TDD): failing tests en `test_parser.py`**

Agregar (adaptar imports a los existentes del archivo; si no existe `FIXTURE_DIR`, definirlo como abajo):

```python
from pathlib import Path

from lxml import etree

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "xml"


def test_fop_fixture_transfer_carries_instrument_spec():
    """TL-D1: el <Transfer> FOP real trae spec completo de instrumento."""
    xml = (FIXTURE_DIR / "ACTIVITY_2026_FOP_sanitized.xml").read_bytes()
    parsed = parse(xml)
    fop = next(t for t in parsed.transfers if t.transaction_id == "39584831194")
    assert fop.asset_class == "STK"
    assert fop.conid == "160756766"
    assert fop.isin == "LU0974299876"
    assert fop.description == "GLOBANT SA"


def test_security_transfer_without_conid_fails_loud():
    """TL-D1: transfer de security sin conid = drift de IBKR -> error accionable."""
    elem = etree.fromstring(
        b'<Transfers><Transfer accountId="U99999001" assetCategory="STK" symbol="ZZZZ"'
        b' conid="" date="20260430" type="FOP" direction="IN" account="CS-999999-99"'
        b' quantity="10" transactionID="T1" /></Transfers>'
    )
    with pytest.raises(ValueError, match="<Transfer> missing required conid"):
        _parse_transfers(elem)


def test_cash_transfer_parses_with_null_conid():
    """TL-D1: CASH interno (symbol='--') -> sin instrumento por diseño."""
    elem = etree.fromstring(
        b'<Transfers><Transfer accountId="U99999001" assetCategory="CASH" symbol="--"'
        b' conid="" date="20260430" type="INTERNAL" direction="OUT" account="U99999002"'
        b' quantity="0" cashTransfer="100" transactionID="T2" /></Transfers>'
    )
    (tr,) = _parse_transfers(elem)
    assert tr.asset_class == "CASH"
    assert tr.conid is None
    assert tr.isin is None
```

Importar `_parse_transfers` y `parse` del módulo parser (seguir el patrón de imports privados ya usado en el archivo si existe; si el archivo solo testea `parse()`, importar `_parse_transfers` explícitamente).

- [ ] **Step 2: Run para verificar FAIL**

Run: `cd backend && uv run pytest -n0 tests/ingest/flex/test_parser.py -v -k "transfer"`
Expected: FAIL — `TypeError: ParsedTransfer.__init__() missing ... asset_class` o `AttributeError: asset_class`.

- [ ] **Step 3: `_models.py` — ParsedTransfer**

Reemplazar el bloque del campo `conid` (y su comentario falso, líneas ~120-122) por:

```python
    # TL-D1 (spec 2026-06-11, supersede T1-D8/CR-1): el split creator/resolver
    # va por CALIDAD DE EVIDENCIA, no por tag. Censo contra los 30 <Transfer>
    # reales: STK x12 -> conid+isin 100% presentes; CASH x18 -> 100% vacíos
    # (symbol="--"). asset_class != 'CASH' -> creator fail-loud (conid REQUIRED
    # via _require_conid); CASH -> conid None e instrument_id NULL por diseño.
    # (El comentario anterior "los FOP de GLOB no traen conid" era un error del
    # grep inicial que no vio attrs multi-línea.)
    asset_class: str
    conid: str | None = None
    isin: str | None = None
    description: str | None = None
```

(`asset_class` sin default DEBE ir antes de `conid` que tiene default — regla de dataclass.)

- [ ] **Step 4: `parser.py` — `_parse_transfers`**

Dentro del loop, antes de construir `ParsedTransfer`:

```python
        asset_class = _require_asset_class(tr, "<Transfer>")
        if asset_class == "CASH":
            # Movimiento interno de plata: sin instrumento por diseño (TL-D1).
            conid = _attr(tr, "conid")  # data real: siempre None (censo 100%)
        else:
            # Security transfer: spec completo de instrumento en el tag -> creator.
            conid = _require_conid(tr, "<Transfer>")
```

Y en el constructor reemplazar la línea `conid=_attr(tr, "conid"),  # resolver-only...` por:

```python
            asset_class=asset_class,
            conid=conid,
            isin=_instrument_isin(tr),
            description=_instrument_description(tr),
```

Actualizar el docstring de `_require_conid` (~línea 311): la frase "Pure resolvers (cash, transfers) use ``elem.get("conid") or None`` instead and never reach here" pasa a mencionar solo cash y CASH-transfers (los security transfers AHORA llegan acá).

- [ ] **Step 5: actualizar los 2 constructores `ParsedTransfer(` en tests**

`test_persister.py` ~547 y `test_persister_idempotent.py` (buscar `ParsedTransfer(`): agregar `asset_class="STK", conid="160756766",` (el de test_persister.py es el GLOB FOP; usar el conid real). El persister TODAVÍA resuelve via `_resolve_instrument` (batch map) en este task → `instrument_id` queda NULL → ninguna otra aserción cambia.

- [ ] **Step 6: Run suite + ruff**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS completo (~430 tests + 3 nuevos).

- [ ] **Step 7: Commit**

```bash
git add backend/src/ibkr_control/ingest/flex/_models.py backend/src/ibkr_control/ingest/flex/parser.py backend/tests/ingest/flex/
git commit -m "feat(lineage): parser captura spec de instrumento en transfers (TL-D1, fail-loud para securities)"
```

---

### Task 2: Schema + persister write-path (commit coordinado — el NOT NULL exige que el persister pueble en el mismo commit)

**Files:**
- Modify: `backend/src/ibkr_control/db/models/flex_raw.py` (Transfer ~345-361, CashTransaction ~393-400)
- Modify: `backend/alembic/versions/a9977ac077e5_tier1_baseline.py` (amendment #6)
- Modify: `backend/src/ibkr_control/ingest/flex/persister.py` (`_collect_instrument_specs` ~929, transfer rows ~644-660, cash rows ~587-604)
- Test: `backend/tests/test_tier1_baseline.py`, `backend/tests/ingest/flex/test_persister_instruments.py`

- [ ] **Step 1 (TDD): failing tests**

(a) `test_tier1_baseline.py` — seguir el patrón de los tests existentes del archivo (que corren contra el clon migrado):

```python
@pytest.mark.asyncio
async def test_transfer_cash_iff_no_instrument_check(owner_session):
    """TL-D5: CHECK bicondicional (asset_class='CASH') = (instrument_id IS NULL)."""
    constraint = await owner_session.scalar(
        text(
            "SELECT conname FROM pg_constraint"
            " WHERE conrelid = 'transfers'::regclass"
            " AND conname = 'ck_transfers_transfer_cash_iff_no_instrument'"
        )
    )
    assert constraint is not None
```

(b) `test_persister_instruments.py` — **flip** de `test_fop_transfer_instrument_id_null_resolver_no_creator` (reemplazarlo entero):

```python
@pytest.mark.asyncio
async def test_fop_transfer_creates_instrument_and_resolves(db_session: AsyncSession, sample_org):
    """TL-D1 end-to-end contra el fixture FOP real: el transfer de security ES
    creator — crea el instrument GLOB (conid 160756766 + isin) y el transfer
    queda con instrument_id non-NULL. Flip del test pre-spec-2026-06-11 que
    lockeaba el NULL (supersede T1-D8/CR-1)."""
    from sqlalchemy import text

    xml = (FIXTURE_DIR / "ACTIVITY_2026_FOP_sanitized.xml").read_bytes()
    parsed = parse(xml)
    await persist(
        db_session, parsed=parsed, organization_id=sample_org.id,
        xml_bytes=xml, source="manual_upload",
    )
    ident = await db_session.scalar(
        select(InstrumentIdentifier).where(InstrumentIdentifier.id_value == "160756766")
    )
    assert ident is not None
    isin_ident = await db_session.scalar(
        select(InstrumentIdentifier).where(InstrumentIdentifier.id_value == "LU0974299876")
    )
    assert isin_ident is not None
    assert isin_ident.instrument_id == ident.instrument_id
    rows = (
        await db_session.execute(
            text("SELECT instrument_id, asset_class, conid FROM transfers ORDER BY transaction_id")
        )
    ).all()
    assert len(rows) == 2  # FOP IN + INTERNAL OUT, ambos GLOB
    for instrument_id, asset_class, conid in rows:
        assert instrument_id == ident.instrument_id
        assert asset_class == "STK"
        assert conid == "160756766"


@pytest.mark.asyncio
async def test_cash_transfer_no_instrument_no_creation(db_session: AsyncSession, sample_org):
    """TL-D1: CASH interno -> instrument_id NULL, conid NULL, cero instruments."""
    transfer = ParsedTransfer(
        transaction_id="XFER-CASH-1",
        transfer_date=date(2026, 5, 1),
        direction="OUT",
        src_ibkr_account_id="U99999001",
        dst_ibkr_account_id="U99999002",
        symbol="--",
        qty=Decimal("0"),
        transfer_type="INTERNAL",
        asset_class="CASH",
        conid=None,
    )
    p = _xml("U99999001", [])
    p.accounts.append(ParsedAccount(ibkr_account_id="U99999002", currency="USD"))
    p.transfers = [transfer]
    await persist(
        db_session, parsed=p, organization_id=sample_org.id,
        xml_bytes=b"<cash-xfer/>", source="manual_upload",
    )
    n_instruments = await db_session.scalar(select(func.count()).select_from(Instrument))
    assert n_instruments == 0
    row = (
        await db_session.execute(
            text("SELECT instrument_id, conid FROM transfers WHERE transaction_id = 'XFER-CASH-1'")
        )
    ).one()
    assert row.instrument_id is None
    assert row.conid is None
```

(Importar `ParsedTransfer`, `ParsedAccount`, `text` si faltan en el archivo.)

- [ ] **Step 2: Run para verificar FAIL**

Run: `cd backend && uv run pytest -n0 tests/test_tier1_baseline.py tests/ingest/flex/test_persister_instruments.py -v`
Expected: FAIL — constraint inexistente / columna `asset_class` inexistente en transfers.

- [ ] **Step 3: modelos `flex_raw.py`**

En `Transfer.__table_args__` agregar (la naming convention de Phase 2.8 produce `ck_transfers_transfer_cash_iff_no_instrument` — el `name=` es SOLO el sufijo semántico):

```python
        CheckConstraint(
            "(asset_class = 'CASH') = (instrument_id IS NULL)",
            name="transfer_cash_iff_no_instrument",
        ),
```

Reemplazar el comentario falso de `Transfer.instrument_id` (líneas ~352-355) por:

```python
    # TL-D1 (spec 2026-06-11, supersede T1-D8/CR-1): los transfers de securities
    # (asset_class != 'CASH') son CREATORS del securities master — el tag trae
    # conid+isin+description 100% en data real (el comentario anterior "los FOP
    # no traen conid" era un error del grep inicial). instrument_id es NULL SOLO
    # para los CASH internos (symbol="--", sin conid): plata, no instrumento.
    # Invariante lockeado por el CHECK bicondicional (TL-D5).
```

Después de `instrument_id` agregar:

```python
    # TL-D4: fidelidad de fuente (simetría con accruals — conid crudo conservado).
    asset_class: Mapped[str] = mapped_column(String, nullable=False)
    conid: Mapped[str | None] = mapped_column(String, nullable=True)
```

(Nota: `symbol`, `qty`, `transfer_type` ya existen — `asset_class`/`conid` van junto a ellos. Verificar que `CheckConstraint` esté importado en el archivo — ya lo está para los arcs de Phase 2.7.)

En `CashTransaction`, después de `instrument_id`:

```python
    # TL-D4: conid crudo, fidelidad de fuente — un instrument_id NULL es
    # auditable sin re-parsear xml_bytes. Nullable real: la Flex Query 2024 ni
    # trae la columna; fees/intereses no tienen instrumento.
    conid: Mapped[str | None] = mapped_column(String, nullable=True)
```

- [ ] **Step 4: persister write-path**

(a) `_collect_instrument_specs` — actualizar docstring (creators = 5 tags + security transfers; cash sigue resolver) y agregar al final, antes del `return specs`:

```python
    # TL-D1 (spec 2026-06-11): transfers de securities son CREATORS — traen
    # spec completo (conid/isin/description/assetCategory 100% en data real).
    # Los CASH (asset_class='CASH') no aportan: sin conid, sin instrumento.
    for tr in parsed.transfers:
        if tr.asset_class == "CASH":
            continue
        _merge(tr.conid, symbol=tr.symbol, asset_class=tr.asset_class, name=tr.description)
        _set_isin(tr.conid, tr.isin)
```

(b) Transfer row builder (~línea 649) — reemplazar `"instrument_id": _resolve_instrument(tr.conid),` por:

```python
                # TL-D1: securities resuelven contra el map de creators (ellos
                # mismos lo poblaron); KeyError = bug del persister, fail loud
                # igual que trades. CASH -> NULL por diseño (CHECK TL-D5).
                "instrument_id": (
                    None if tr.asset_class == "CASH" else instruments_map[tr.conid]
                ),
                "asset_class": tr.asset_class,
                "conid": tr.conid,
```

(c) Cash row builder (~línea 593): agregar `"conid": ct.conid,` al dict.

(d) Actualizar el comentario del bloque W2 (~líneas 223-226): "cash/transfers son resolver-only" → "cash es resolver-only; los transfers de securities son creators (TL-D1)".

- [ ] **Step 5: baseline amendment #6**

Procedimiento canónico W1/W2/W3 (autogenerate temporal vía servicio migrate contra DB virgen → splice del diff entre los markers del baseline → MISMO revision id `a9977ac077e5`). El diff esperado: `transfers.asset_class` (NOT NULL), `transfers.conid`, `cash_transactions.conid`, CHECK `ck_transfers_transfer_cash_iff_no_instrument`. SIN cambios RLS (`_ORG_SCOPED_TABLES` intacto). Verificar después con el drift test:

Run: `cd backend && uv run pytest -n0 tests/test_migrations.py -v`
Expected: PASS (autogenerate-diff vacío contra `Base.metadata`).

- [ ] **Step 6: Run suite completa + ruff**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS. Si algún test existente de transfers rompe por el NOT NULL, es porque construye rows de transfer sin `asset_class` — arreglar el constructor (agregar `asset_class=`/`conid=`), NO relajar el schema.

- [ ] **Step 7: Commit**

```bash
git add backend/src/ibkr_control/db/models/flex_raw.py backend/alembic/versions/a9977ac077e5_tier1_baseline.py backend/src/ibkr_control/ingest/flex/persister.py backend/tests/
git commit -m "feat(lineage): security transfers como creators + fidelidad de fuente + CHECK (TL-D1/D4/D5, baseline amendment #6)"
```

---

### Task 3: Resolver DB-wide + convergencia monótona en cash (TL-D2/D3)

**Files:**
- Modify: `backend/src/ibkr_control/ingest/flex/persister.py` (`_ensure_instruments` ~1015-1050, cash upsert ~605-612)
- Modify: `backend/src/ibkr_control/ingest/flex/_upsert_helpers.py` (helper nuevo después de `_upsert_immutable_returning_inserted` ~206)
- Test: `backend/tests/ingest/flex/test_persister_instruments.py`

- [ ] **Step 1 (TDD): failing tests**

```python
@pytest.mark.asyncio
async def test_resolver_resolves_against_master_cross_batch(db_session: AsyncSession, sample_org):
    """TL-D2: el lookup del resolver es contra el MASTER (DB), no solo el batch.
    Batch 1 crea el instrument (trade creator); batch 2 trae SOLO un cash con el
    mismo conid -> resuelve aunque no haya creator en ese batch."""
    trade = _trade("265598", "AAPL", txn="TX-AAPL-1")
    await persist(
        db_session, parsed=_xml("U99999001", [trade]), organization_id=sample_org.id,
        xml_bytes=b"<batch1/>", source="manual_upload",
    )
    cash = ParsedCashTransaction(
        transaction_id="CASH-CROSS-BATCH",
        ibkr_account_id="U99999001",
        type="Dividends",
        currency="USD",
        amount_usd=Decimal("5.00"),
        description="AAPL dividend, posicion ya cerrada",
        date=date(2026, 2, 1),
        symbol="AAPL",
        conid="265598",
    )
    await persist(
        db_session, parsed=_xml("U99999001", [], cash_transactions=[cash]),
        organization_id=sample_org.id, xml_bytes=b"<batch2/>", source="manual_upload",
    )
    ct = await db_session.scalar(
        select(CashTransaction).where(CashTransaction.transaction_id == "CASH-CROSS-BATCH")
    )
    assert ct.instrument_id is not None


@pytest.mark.asyncio
async def test_cash_instrument_id_converges_monotonically(db_session: AsyncSession, sample_org):
    """TL-D3: una fila cash congelada con NULL converge cuando el re-ingest trae
    la resolucion; la convergencia NO cuenta como fila nueva (n_new honesto) y
    NO genera restatements (enriquecimiento != restatement economico)."""
    from sqlalchemy import text

    cash = ParsedCashTransaction(
        transaction_id="CASH-CONV",
        ibkr_account_id="U99999001",
        type="Dividends",
        currency="USD",
        amount_usd=Decimal("7.00"),
        description="dividendo de instrument aun no visto",
        date=date(2026, 3, 1),
        symbol="AAPL",
        conid="265598",
    )
    # Batch 1: conid irresoluble (sin creator en batch ni master) -> NULL.
    await persist(
        db_session, parsed=_xml("U99999001", [], cash_transactions=[cash]),
        organization_id=sample_org.id, xml_bytes=b"<conv1/>", source="manual_upload",
    )
    ct = await db_session.scalar(
        select(CashTransaction).where(CashTransaction.transaction_id == "CASH-CONV")
    )
    assert ct.instrument_id is None
    # Batch 2 (re-ingest YTD): creator + LA MISMA fila cash -> converge.
    trade = _trade("265598", "AAPL", txn="TX-AAPL-2")
    _, counters = await persist(
        db_session, parsed=_xml("U99999001", [trade], cash_transactions=[cash]),
        organization_id=sample_org.id, xml_bytes=b"<conv2/>", source="manual_upload",
    )
    assert counters["n_new_cash_tx"] == 0  # convergencia != fila nueva
    await db_session.refresh(ct)
    assert ct.instrument_id is not None
    assert ct.amount_usd == Decimal("7.00")  # columnas de hecho intactas
    n_restatements = (
        await db_session.execute(text("SELECT count(*) FROM restatement_log"))
    ).scalar_one()
    assert n_restatements == 0
    n_rows = await db_session.scalar(
        select(func.count())
        .select_from(CashTransaction)
        .where(CashTransaction.transaction_id == "CASH-CONV")
    )
    assert n_rows == 1


@pytest.mark.asyncio
async def test_resolved_cash_row_untouched_on_reingest(db_session: AsyncSession, sample_org):
    """TL-D3 guard monotono: fila ya resuelta re-ingerida -> cero cambios, cero new."""
    trade = _trade("265598", "AAPL", txn="TX-AAPL-3")
    cash = ParsedCashTransaction(
        transaction_id="CASH-STABLE",
        ibkr_account_id="U99999001",
        type="Dividends",
        currency="USD",
        amount_usd=Decimal("3.00"),
        description="ya resuelto",
        date=date(2026, 4, 1),
        symbol="AAPL",
        conid="265598",
    )
    await persist(
        db_session, parsed=_xml("U99999001", [trade], cash_transactions=[cash]),
        organization_id=sample_org.id, xml_bytes=b"<stable1/>", source="manual_upload",
    )
    ct = await db_session.scalar(
        select(CashTransaction).where(CashTransaction.transaction_id == "CASH-STABLE")
    )
    original_iid = ct.instrument_id
    assert original_iid is not None
    _, counters = await persist(
        db_session, parsed=_xml("U99999001", [], cash_transactions=[cash]),
        organization_id=sample_org.id, xml_bytes=b"<stable2/>", source="manual_upload",
    )
    assert counters["n_new_cash_tx"] == 0
    await db_session.refresh(ct)
    assert ct.instrument_id == original_iid
```

(Importar `ParsedCashTransaction` si falta; `_trade` y `_xml` ya existen en el archivo.)

- [ ] **Step 2: Run para verificar FAIL**

Run: `cd backend && uv run pytest -n0 tests/ingest/flex/test_persister_instruments.py -v -k "cross_batch or converges or untouched"`
Expected: FAIL — cross_batch: `instrument_id` None; converges: sigue None tras batch 2.

- [ ] **Step 3: `_upsert_helpers.py` — helper nuevo**

Después de `_upsert_immutable_returning_inserted`:

```python
async def _upsert_immutable_with_resolution(
    session: AsyncSession,
    table: Table,
    rows: Sequence[dict[str, Any]],
    conflict_cols: list[str],
    returning_cols: list[str],
    resolution_col: str = "instrument_id",
) -> list[Row]:
    """Como _upsert_immutable_returning_inserted + convergencia monótona (TL-D3).

    Las columnas de HECHO siguen first-seen inmutables; la columna de RESOLUCIÓN
    (enriquecimiento contra el catálogo control-plane) converge NULL->valor
    cuando un re-ingest trae la resolución que faltaba. El WHERE hace el update
    monótono y cero-churn: filas ya resueltas o sin resolución nueva se
    comportan como DO NOTHING (sin bloat en el re-ingest YTD diario).

    Devuelve SOLO los INSERTs estrictos (xmax = 0): una convergencia no es una
    fila nueva — n_new/ingest_log no se inflan (conteo honesto, spec TL-D3).

    Dedupe intra-batch por conflict key (last-seen gana): a diferencia del
    DO NOTHING, el DO UPDATE rechaza afectar la misma fila dos veces en un
    statement (CardinalityViolationError).
    """
    if not rows:
        return []
    deduped: dict[tuple, dict[str, Any]] = {}
    for r in rows:
        deduped[tuple(r[c] for c in conflict_cols)] = r
    inserted: list[Row] = []
    for batch in _chunks(list(deduped.values()), _BATCH_SIZE):
        stmt = pg_insert(table).values(batch)
        excluded = stmt.excluded
        stmt = stmt.on_conflict_do_update(
            index_elements=conflict_cols,
            set_={resolution_col: excluded[resolution_col]},
            where=table.c[resolution_col].is_(None)
            & excluded[resolution_col].is_not(None),
        )
        stmt = stmt.returning(
            *[table.c[col] for col in returning_cols],
            text("(xmax = 0) AS strictly_inserted"),
        )
        result = await session.execute(stmt)
        inserted.extend(row for row in result.all() if row.strictly_inserted)
    return inserted
```

(Los imports `text`, `Row`, `pg_insert`, `_chunks`, `_BATCH_SIZE` ya existen en el módulo.)

- [ ] **Step 4: `persister.py` — resolver DB-wide + switch del upsert de cash**

(a) `_ensure_instruments`: reemplazar el inicio (`specs = ...` hasta `conids = list(specs.keys())`) por:

```python
    specs = _collect_instrument_specs(parsed)
    # TL-D2: el lookup es contra el MASTER (DB), no solo el batch — los conids
    # referenciados por resolvers (cash) se resuelven SELECT-only acá. NUNCA se
    # crean: solo los specs de creators llegan al INSERT de abajo.
    resolver_conids = {ct.conid for ct in parsed.cash_transactions if ct.conid}
    conids = list(specs.keys() | resolver_conids)
    if not conids:
        return {}
```

Y cambiar la línea `missing = [c for c in conids if c not in conid_to_iid]` por:

```python
    missing = [c for c in conids if c not in conid_to_iid and c in specs]
```

(El re-SELECT post-INSERT ya itera `conids` completo — cubre resolver hits sin cambios. Actualizar el docstring de `_ensure_instruments`: "garantiza instruments para cada conid de los creators **y resuelve, sin crear, los conids de los resolvers**".)

(b) Cash upsert (~línea 605): reemplazar la llamada `_upsert_immutable_returning_inserted` por:

```python
    inserted_cash = await _upsert_immutable_with_resolution(
        session,
        CashTransaction.__table__,
        cash_rows,
        ["organization_id", "transaction_id"],
        ["transaction_id", "type"],
    )
```

Actualizar el import desde `_upsert_helpers` y el comentario del bloque (`# === CashTransactions (immutable, resolución convergente TL-D3) ===`). Si `_upsert_immutable_returning_inserted` queda sin consumidores tras el switch, BORRARLO (junto con su export) — cero dead code.

(c) `_resolve_instrument`: actualizar el comentario — el warning ahora dispara solo para conids genuinamente irresolubles (sin instrument en batch NI en master).

- [ ] **Step 5: Run suite completa + ruff**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS. Atención al test existente `test_resolver_with_unknown_conid_does_not_create` — debe seguir verde (conid sin instrument en batch NI master → NULL, sin creación).

- [ ] **Step 6: Commit**

```bash
git add backend/src/ibkr_control/ingest/flex/persister.py backend/src/ibkr_control/ingest/flex/_upsert_helpers.py backend/tests/ingest/flex/test_persister_instruments.py
git commit -m "feat(lineage): resolver DB-wide + convergencia monotona NULL->valor en cash (TL-D2/D3, n_new honesto via xmax)"
```

---

### Task 4: Docs + wipe & reload + boot smoke prod-local

**Files:**
- Modify: `docs/specs/2026-06-10-tier1-worldclass-model-design.md` (T1-D8 + CR-1)
- Modify: `CLAUDE.md` (bullet W2 + estado del programa)

- [ ] **Step 1: SUPERSEDED notes en el spec W2**

En la fila T1-D8 de la tabla de decisiones y en la fila CR-1 de checkpoints, agregar al final: `**[PARTIALLY SUPERSEDED 2026-06-11: security transfers = creators fail-loud; resolver cash = DB-wide + convergencia monótona — ver docs/specs/2026-06-11-transfer-instrument-lineage-design.md]**`. En la línea ~129 (hechos → instrumento) anotar igual que `transfers` dejó de ser "según CR-1" → NOT NULL para securities/NULL para CASH con CHECK.

- [ ] **Step 2: CLAUDE.md**

(a) En el bullet **W2** (§Estado del programa SaaS), después de "la regla resolver-never-creates deja `instrument_id NULL` igual, testeado contra el fixture real": agregar `**[SUPERSEDED 2026-06-11: el censo completo probó bimodalidad perfecta (STK 100% conid vs CASH 0%) — security transfers ahora son creators fail-loud, resolver DB-wide + convergencia monótona; el symbol-join de Phase 3 murió — ver spec transfer-instrument-lineage]**`. Ídem en la frase "La reconstrucción del linaje instrumento↔transfer FOP queda para el domain layer Phase 3 (join por symbol contra lots)".

(b) Agregar bullet nuevo al estado del programa (después del bullet de test infra) resumiendo este PR: problema (3 gaps compuestos), decisiones TL-D1..D6, conteo de tests, spec + plan paths.

- [ ] **Step 3: wipe & reload + boot smoke prod-local**

```bash
docker compose -f compose.yaml -f compose.dev.yaml down -v
make prod-local
```

Verificar: (1) servicio `migrate` aplica el baseline amendado sin error; (2) `backend` arranca como `app_rls` (`rolsuper=f`, `rolbypassrls=f` — el boot guard lo enforcea); (3) `docker compose exec db psql -U $POSTGRES_USER -d $POSTGres_DB -c "\d transfers"` muestra `asset_class NOT NULL`, `conid`, y el CHECK `ck_transfers_transfer_cash_iff_no_instrument`; ídem `\d cash_transactions` muestra `conid`. Después `make dev` para dejar el entorno en dev (el usuario recarga XMLs por wizard cuando quiera).

- [ ] **Step 4: Commit + push + PR**

```bash
git add docs/ CLAUDE.md
git commit -m "docs(lineage): SUPERSEDED notes T1-D8/CR-1 + CLAUDE.md (transfer-instrument lineage)"
git push -u origin tier1/transfer-instrument-lineage
gh pr create --title "Transfer-instrument lineage: security transfers como creators (supersede T1-D8/CR-1)" --body "..."
```

(Cuerpo del PR: resumen de los 3 gaps + TL-D1..D6 + conteo de tests + nota de baseline amendment #6 + wipe & reload. CI `backend` + `frontend` deben quedar verdes antes del merge — el usuario aprueba el merge.)

---

## Self-review del plan (hecho al escribirlo)

- **Cobertura del spec:** TL-D1 → Tasks 1+2 · TL-D2 → Task 3 Step 4a · TL-D3 (incl. conteo xmax) → Task 3 Steps 3-4b · TL-D4 → Task 2 Step 3 · TL-D5 → Task 2 Steps 1a+3 · TL-D6 → Task 2 Step 5 + Task 4 Step 3 · §6 docs → Task 4. Replay suite (spec §5 último bullet) corre dentro de las suites completas de Tasks 2-3.
- **Sin placeholders:** todo step de código tiene el código.
- **Consistencia de tipos:** `ParsedTransfer.asset_class: str` (Task 1) ↔ `tr.asset_class` en persister (Task 2) ↔ columna NOT NULL (Task 2); `_upsert_immutable_with_resolution` definido en Task 3 Step 3 antes de su uso en Step 4b; `counters["n_new_cash_tx"]` verificado contra el contrato real de `persist()` (`n_new_{...,cash_tx,...}`).
