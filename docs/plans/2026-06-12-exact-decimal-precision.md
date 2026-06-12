# Exact Decimal Precision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Storage exacto de los valores numéricos de la fuente IBKR Flex — 27 columnas pasan de `Numeric(p,s)` a `NUMERIC` unconstrained (PD-1/PD-2), con test de fidelidad roundtrip que lockea la política (PD-5).

**Architecture:** Spec: `docs/specs/2026-06-12-exact-decimal-precision-design.md` (PD-1..PD-5). Único cambio de producto: los modelos. La maquinaria W3 (`_quantize_to_scale`/`_values_differ`) NO cambia de código — `scale=None` la vuelve pass-through, que con storage exacto es la semántica correcta (PD-3). Baseline amendment #7 canónico (T1-D14 vigente) + wipe & reload dev (PD-4).

**Tech Stack:** SQLAlchemy 2 async, Alembic (baseline amendment canónico en container), pytest (template-clone fixtures: `db_session` = `app_rls`, auto-scopeado por `sample_org`).

**Reglas del repo (idénticas a W1/W2/W3/lineage):** TDD; tests desde el HOST (`cd backend && uv run pytest`); `uv run ruff check . && uv run ruff format .` antes de cada commit; quedarse en el branch `tier1/exact-decimal-precision` (NO crear branches nuevos); baseline amendment canónico (autogenerate temporal contra DB virgen en container + splice entre markers, MISMO revision id `a9977ac077e5` — los hand-edits fueron rechazados en amendment #6: el DDL puede ser equivalente pero el ordering de emisión de alembic no se replica a mano); el drift test (`compare_metadata`) es el gate del schema; `_ORG_SCOPED_TABLES` NO cambia (no hay tablas nuevas ni cambios RLS).

---

### Task 1: Test de fidelidad de fuente (PD-5) — RED

El test que habría atrapado Migration F. Se escribe PRIMERO y debe FALLAR contra el schema actual (las columnas `(20,4)` redondean). **NO se commitea en este task** — va en el mismo commit que el schema change (Task 2), lección PR-B: un par acoplado no se divide en dos commits (CI corre sobre el tip).

**Files:**
- Create: `backend/tests/test_source_precision_fidelity.py`

- [ ] **Step 1: Escribir el test**

```python
"""Fidelidad de fuente (PD-5, spec 2026-06-12): roundtrip XML -> DB exacto.

Para cada columna NUMERIC proveniente del XML Flex (PD-2), el valor almacenado
en Postgres debe ser EXACTAMENTE el Decimal que emitió la fuente — sin redondeo
en el write path (PD-1: NUMERIC unconstrained). Este es el test que habría
atrapado Migration F ("match precisión del XML" con scale 4 vs fuente de 9
decimales en ibCommission) y los 114 falsos positivos del golden run W3.

Diseño:
- Subset check ``db_values <= source_values``: cada valor almacenado existe
  textualmente en el output del parser (que es ``Decimal(attr)`` crudo).
  Cualquier redondeo produce un valor que NO está en la fuente -> falla.
  Subset (no igualdad) porque el persister colapsa duplicados por natural key
  (e.g. transfers IBKR-mirror: n_observed != n_new documentado).
- Non-vacuity (test aparte, source-side): las columnas con pérdida probada en
  el censo 2026-06-12 deben exhibir el max de decimales censado — protege los
  dientes del test contra swaps/truncados de fixtures.
"""

from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ibkr_control.db.models.flex_raw import (
    CashTransaction,
    ChangeInDividendAccrual,
    ClosedLot,
    OpenDividendAccrual,
    OpenPositionLot,
    Trade,
    Transfer,
)
from ibkr_control.db.models.instruments import Instrument
from ibkr_control.ingest.flex.parser import parse
from ibkr_control.ingest.flex.persister import persist

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "xml"
FIXTURES = [
    "ACTIVITY_2024_sanitized.xml",
    "ACTIVITY_2025_sanitized.xml",
    "ACTIVITY_2026_FOP_sanitized.xml",
]

_ACCRUAL_COLS = [
    "quantity",
    "gross_rate_per_share",
    "gross_amount_usd",
    "tax_usd",
    "fee_usd",
    "net_amount_usd",
]

# (atributo de ParsedXML, modelo, columnas PD-2). Los nombres de campo de los
# dataclasses Parsed* espejan las columnas 1:1 (verificado contra _models.py).
ENTITIES = [
    ("trades", Trade, ["qty", "price_usd", "proceeds_usd", "commission_usd"]),
    ("closed_lots", ClosedLot, ["qty", "cost_basis_usd", "proceeds_usd", "fifo_pnl_usd"]),
    (
        "open_position_lots",
        OpenPositionLot,
        ["qty", "cost_basis_usd", "mark_price_usd", "mark_value_usd"],
    ),
    ("transfers", Transfer, ["qty"]),
    ("cash_transactions", CashTransaction, ["amount_usd"]),
    ("change_in_dividend_accruals", ChangeInDividendAccrual, _ACCRUAL_COLS),
    ("open_dividend_accruals", OpenDividendAccrual, _ACCRUAL_COLS),
]

# Censo 2026-06-12 contra los 3 XMLs reales: (entity, col) -> max decimales
# observados en la FUENTE. Si un fixture futuro baja de esto, el test de
# fidelidad se queda sin dientes -> este guard lo detecta.
_PROVEN_PRECISION = {
    ("trades", "commission_usd"): 9,
    ("trades", "proceeds_usd"): 8,
    ("open_position_lots", "mark_value_usd"): 9,
    ("open_position_lots", "cost_basis_usd"): 6,
    ("closed_lots", "cost_basis_usd"): 6,
    ("closed_lots", "fifo_pnl_usd"): 6,
    ("closed_lots", "proceeds_usd"): 6,
}


def _decimals(v: Decimal) -> int:
    exp = v.as_tuple().exponent
    return -exp if isinstance(exp, int) and exp < 0 else 0


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture_name", FIXTURES)
async def test_db_values_are_exact_source_values(
    db_session: AsyncSession, sample_org, fixture_name
):
    xml = (FIXTURES_DIR / fixture_name).read_bytes()
    parsed = parse(xml)
    await persist(
        db_session,
        parsed=parsed,
        organization_id=sample_org.id,
        xml_bytes=xml,
        source="manual_upload",
    )

    for attr, model, cols in ENTITIES:
        entities = getattr(parsed, attr)
        if not entities:
            continue  # e.g. 2024 no trae accruals
        for col in cols:
            source_values = {
                getattr(e, col) for e in entities if getattr(e, col) is not None
            }
            result = await db_session.execute(select(getattr(model, col)))
            db_values = {v for (v,) in result.all() if v is not None}
            rounded = db_values - source_values
            assert not rounded, (
                f"{fixture_name} {model.__tablename__}.{col}: {len(rounded)} valores "
                f"en DB que NO existen en la fuente (redondeo en el write path): "
                f"{sorted(rounded)[:5]}"
            )

    # instruments.multiplier (PD-2): el persister lo toma de los specs de los
    # creators — el valor almacenado debe existir en ALGÚN entity parseado.
    source_multipliers = set()
    for attr, _, _ in ENTITIES:
        for e in getattr(parsed, attr):
            m = getattr(e, "multiplier", None)
            if m is not None:
                source_multipliers.add(m)
    result = await db_session.execute(select(Instrument.multiplier))
    db_multipliers = {v for (v,) in result.all() if v is not None}
    rounded = db_multipliers - source_multipliers
    assert not rounded, f"instruments.multiplier redondeado: {sorted(rounded)[:5]}"


def test_proven_precision_columns_keep_teeth():
    """Non-vacuity source-side (no toca DB): el censo de decimales sigue vigente."""
    observed: dict[tuple[str, str], int] = {}
    for fixture_name in FIXTURES:
        parsed = parse((FIXTURES_DIR / fixture_name).read_bytes())
        for attr, col in _PROVEN_PRECISION:
            for e in getattr(parsed, attr):
                v = getattr(e, col)
                if v is not None:
                    key = (attr, col)
                    observed[key] = max(observed.get(key, 0), _decimals(v))
    for key, required in _PROVEN_PRECISION.items():
        assert observed.get(key, 0) >= required, (
            f"{key}: max decimales en fixtures = {observed.get(key, 0)} < {required} "
            f"censado — el test de fidelidad perdió los dientes (¿fixture truncado?)"
        )
```

- [ ] **Step 2: Correr y verificar RED**

Run: `cd backend && uv run pytest -n0 tests/test_source_precision_fidelity.py -v`
Expected: `test_db_values_are_exact_source_values` FAIL en los 3 fixtures (valores como `-0.3650` en DB que no existen en la fuente `-0.365021034`); `test_proven_precision_columns_keep_teeth` PASS (es source-side).

Si `test_proven_precision_columns_keep_teeth` FALLA: el censo del spec está mal o el campo de un dataclass no espeja la columna — STOP y reportar (no ajustar los números del censo para que pase).

---

### Task 2: 27 columnas → `Numeric()` + baseline amendment #7 — GREEN

**Files:**
- Modify: `backend/src/ibkr_control/db/models/flex_raw.py` (26 columnas: líneas 158-161, 228-231, 288-291, 369, 415, 480-485, 554-559)
- Modify: `backend/src/ibkr_control/db/models/instruments.py:52` (`multiplier`)
- Modify: `backend/alembic/versions/a9977ac077e5_tier1_baseline.py` (amendment #7, regen canónica)
- Test: `backend/tests/test_source_precision_fidelity.py` (Task 1, pasa a GREEN) + `backend/tests/test_migrations.py` (drift)

- [ ] **Step 1: Modelos — reemplazar `Numeric(p,s)` por `Numeric()`**

En `flex_raw.py`, para las 26 columnas listadas (PD-2 — tabla del spec §2), el cambio es uniforme. Ejemplo (trades, líneas 158-161):

```python
    qty: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    price_usd: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    proceeds_usd: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    commission_usd: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
```

Aplicar idéntico a: closed_lots (228-231), open_position_lots (288-291, `mark_price_usd`/`mark_value_usd` siguen `nullable=True`), transfers (369), cash_transactions (415), change_in_dividend_accruals (480-485), open_dividend_accruals (554-559). En `instruments.py:52`:

```python
    multiplier: Mapped[Decimal | None] = mapped_column(Numeric(), nullable=True)
```

**NO tocar:** `trm.py:19` (`value_cop Numeric(12,4)`) ni `participations.py:39` (`pct Numeric(5,4)`) — contratos documentados, fuera de alcance (spec §PD-2).

- [ ] **Step 2: Baseline amendment #7 — regen canónica en container**

Procedimiento canónico W1/W2/W3/lineage (autogenerate temporal contra DB virgen DENTRO del container → splice entre markers → MISMO revision id `a9977ac077e5` → borrar el archivo temporal):

```bash
make dev   # stack arriba (el servicio migrate aplica el baseline VIEJO al boot)
# DB virgen para que autogenerate emita el DDL completo desde Base.metadata:
docker compose -f compose.yaml -f compose.dev.yaml exec db \
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"'
docker compose -f compose.yaml -f compose.dev.yaml exec backend \
  uv run alembic revision --autogenerate -m "tmp_exact_precision_full_ddl"
```

Luego el splice en `a9977ac077e5_tier1_baseline.py`:
1. Reemplazar la sección autogenerada de `upgrade()` (desde el primer `op.create_table` hasta el primer marker `# ### end Alembic commands ###`, línea ~1441) por la del archivo temporal.
2. Ídem la sección autogenerada de `downgrade()` (hasta el segundo marker, línea ~1672).
3. Las secciones HAND-WRITTEN (apscheduler_jobs, rol app_rls, RLS loop, policy access_grants, system function, seed institutions) NO se tocan.
4. Agregar al docstring del baseline el bloque **Amendment #7** (después del #6):

```
**Amendment #7 (precisión decimal exacta, PD-1/PD-2 — spec 2026-06-12):** las
27 columnas NUMERIC cuyo valor proviene del XML Flex (hechos x7 +
``instruments.multiplier``) pasan de ``Numeric(p,s)`` a ``NUMERIC``
unconstrained — la fuente IBKR no documenta precisión (censo real: hasta 9
decimales en ``ibCommission``/``positionValue`` vs scale 4 declarada; Migration
F refutada) y la scale declarada redondeaba en el write path (114 falsos
positivos del golden W3). Política nueva: scale declarada solo con contrato de
fuente documentado (``trm.value_cop``, ``participations.pct`` se quedan).
Regenerado canónicamente en container (autogenerate temporal contra DB virgen,
splice entre marcadores, mismo revision id). SIN cambios RLS.
```

5. Borrar el archivo temporal `backend/alembic/versions/*tmp_exact_precision*.py`.

**Verificación del diff antes del splice:** el archivo temporal vs el baseline actual debe diferir SOLO en `sa.Numeric(precision=20, scale=X)` → `sa.Numeric()` en las 27 columnas. Si aparece CUALQUIER otro delta (tablas, índices, constraints), STOP — plan-vs-reality drift, reportar antes de seguir.

- [ ] **Step 3: Re-aplicar y verificar drift + fidelidad GREEN**

```bash
docker compose -f compose.yaml -f compose.dev.yaml down -v && make dev
cd backend && uv run pytest -n0 tests/test_migrations.py -v
cd backend && uv run pytest -n0 tests/test_source_precision_fidelity.py -v
```

Expected: drift test PASS (autogenerate-diff vacío contra `Base.metadata`); fidelidad PASS (los 4 tests — el template del test infra se reconstruye por corrida desde el baseline amendado).

- [ ] **Step 4: ruff + commit (modelos + baseline + test de Task 1 juntos)**

```bash
cd backend && uv run ruff check . && uv run ruff format .
git add backend/src/ibkr_control/db/models/flex_raw.py \
        backend/src/ibkr_control/db/models/instruments.py \
        backend/alembic/versions/a9977ac077e5_tier1_baseline.py \
        backend/tests/test_source_precision_fidelity.py
git commit -m "feat(precision): NUMERIC unconstrained en 27 columnas fuente-IBKR + test de fidelidad (PD-1/PD-2/PD-5, baseline amendment #7)"
```

---

### Task 3: Adaptar los 2 tests con premisa de scale + docstrings W3 (PD-3)

**Files:**
- Modify: `backend/tests/ingest/flex/test_restatements.py:206-231` (boundary HALF_UP)
- Modify: `backend/tests/test_flex_ingest_replay.py:186-242` (paridad FIFO)
- Modify: `backend/src/ibkr_control/ingest/flex/_upsert_helpers.py:87-115` (docstrings)

- [ ] **Step 1: Re-apuntar el boundary test**

`test_half_boundary_value_not_a_restatement` pierde su premisa (columna scale-4). Reemplazarlo COMPLETO (mismo archivo, reutiliza los helpers `_xml_with_open_lots`/`_open_lot`/`_count_restatements` existentes) por:

```python
@pytest.mark.asyncio
async def test_full_precision_value_roundtrips_and_is_not_a_restatement(
    db_session: AsyncSession, sample_org
):
    """PD-1/PD-3 (spec 2026-06-12): storage exacto => re-ingest idéntico da cero
    restatements POR CONSTRUCCIÓN, no por paridad de rounding-mode.

    Históricamente este test fijaba ROUND_HALF_UP vs HALF_EVEN contra la columna
    Numeric(20,4) (el boundary 255.86945 divergía entre PG y el default de
    Decimal.quantize). Con NUMERIC unconstrained la columna no redondea:
    _quantize_to_scale es pass-through (scale=None) y la comparación de
    _values_differ es exacta. Se preserva el valor boundary como input y se
    agrega el assert de roundtrip exacto."""
    value = Decimal("255.86945")
    await persist(
        db_session,
        parsed=_xml_with_open_lots([_open_lot(cost_basis=value)]),
        organization_id=sample_org.id,
        xml_bytes=b"<v1/>",
        source="manual_upload",
    )
    await persist(
        db_session,
        parsed=_xml_with_open_lots([_open_lot(cost_basis=value)]),
        organization_id=sample_org.id,
        xml_bytes=b"<v2/>",
        source="manual_upload",
    )
    stored = await db_session.scalar(select(OpenPositionLot.cost_basis_usd))
    assert stored == value, f"roundtrip no exacto: {stored} != {value}"
    assert await _count_restatements(db_session) == 0
```

Si `OpenPositionLot`/`select` no están importados en el archivo, agregarlos a los imports existentes.

- [ ] **Step 2: Paridad FIFO exacta en el replay**

En `test_closed_lots_sum_matches_pool_2025`, reemplazar el bloque de suma XML (el comentario "Each XML value is quantized to 4 decimal places..." + `_FOUR_DP` + el loop con `.quantize(...)`) por:

```python
    # Paridad EXACTA con la fuente (PD-1, spec 2026-06-12): la columna es
    # NUMERIC unconstrained — Postgres almacena el valor del XML tal cual, así
    # que la suma DB debe igualar la suma full-precision del XML sin cuantizar.
    tree = etree.fromstring(xml)
    xml_sum = Decimal("0")
    for el in tree.iter("Lot"):
        if el.get("levelOfDetail") != "CLOSED_LOT":
            continue
        v = el.get("fifoPnlRealized")
        if v:
            xml_sum += Decimal(v)
```

Quitar `ROUND_HALF_UP` del import de `decimal` (línea 21) SI no queda otro uso en el archivo (verificar con grep antes).

- [ ] **Step 3: Docstrings de `_upsert_helpers.py` (cero cambio de código)**

Reemplazar el docstring de `_quantize_to_scale` (líneas 88-94) por:

```python
    """Cuantiza un Decimal a la escala declarada de la columna (como lo guardaría PG).

    Post spec 2026-06-12 (PD-1/PD-3) las columnas fuente-IBKR son NUMERIC
    unconstrained -> scale=None -> pass-through: la comparación es exacta, que
    con storage exacto es la semántica correcta. El helper queda latente para
    columnas con scale documentada (e.g. si trm/participations se vuelven
    material cols algún día): "comparar a precisión de storage" sigue siendo
    el principio; hoy la precisión de storage es la de la fuente.
    """
```

Y en el docstring de `_values_differ` (líneas 104-109), reemplazar la frase "Numeric/Decimal: cuantiza ambos a la escala de la columna (lo que la DB guardaría) y compara por valor numérico" por: "Numeric/Decimal: compara a precisión de storage (con NUMERIC unconstrained = exacta; con scale declarada, cuantiza ambos como la DB)". El comentario inline de ROUND_HALF_UP (líneas 98-99) se queda.

- [ ] **Step 4: Suite completa + ruff + commit**

```bash
cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format .
git add backend/tests/ingest/flex/test_restatements.py \
        backend/tests/test_flex_ingest_replay.py \
        backend/src/ibkr_control/ingest/flex/_upsert_helpers.py
git commit -m "test(precision): boundary test -> roundtrip exacto, paridad FIFO sin tolerancia, docstrings W3 (PD-3)"
```

Expected: suite completa verde (438 + 4 nuevos ≈ 442; el conteo exacto lo fija la corrida). Si algún OTRO test rompe por la precisión nueva (e.g. asserts con valores redondeados a 4dp), arreglar el assert al valor exacto de la fuente — NUNCA re-redondear en el código de producto.

---

### Task 4: Boot smoke prod-local + wipe & reload dev

- [ ] **Step 1: Boot smoke prod-local (espejo Coolify)**

```bash
docker compose -f compose.yaml down -v && make prod-local
docker compose -f compose.yaml exec db \
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname='"'"'app_rls'"'"';"'
docker compose -f compose.yaml exec db \
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\d trades"' | grep -E "qty|price_usd|proceeds_usd|commission_usd"
```

Verificar: (1) servicio `migrate` aplica el baseline #7 sin error; (2) `backend` arranca como `app_rls` (`rolsuper=f`, `rolbypassrls=f` — boot guard); (3) `\d trades` muestra las 4 columnas como `numeric` SIN `(p,s)`.

- [ ] **Step 2: Volver a dev**

```bash
docker compose -f compose.yaml down -v && make dev
```

(El usuario recarga los XMLs por wizard cuando quiera — wipe & reload es la política T1-D14; al recargar, spot check del criterio de éxito §5.4 del spec: `commission_usd` de un trade real == valor XML con 9 decimales.)

- [ ] **Step 3: Commit (solo si hubo cambios — normalmente no los hay en este task)**

---

### Task 5: CLAUDE.md + PR

- [ ] **Step 1: CLAUDE.md**

1. En §"Estado del programa SaaS", agregar bullet después del de transfer-instrument-lineage (PR #20): resumen del PR de precisión (política PD-1, 27 columnas, amendment #7, test de fidelidad, paridad FIFO ahora exacta, conteo de tests, links a spec/plan).
2. En §"Phase 2 — Retrospectiva", bullet "**Formato de datos numéricos — decisión locked**": agregar al final ` **[SUPERSEDED 2026-06-12 para columnas fuente-IBKR: el censo real probó hasta 9 decimales (ibCommission) vs scale 4 — NUMERIC unconstrained, scale declarada solo con contrato documentado; ver spec exact-decimal-precision]**`.
3. El "⏯ SIGUIENTE" sigue siendo SP2 (este PR es un follow-up de Tier 1, no lo cambia).

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md refleja precisión decimal exacta (PD-1, baseline amendment #7)"
```

- [ ] **Step 2: Push + PR**

```bash
git push -u origin tier1/exact-decimal-precision
gh pr create --title "Precisión decimal exacta en hechos Flex (NUMERIC unconstrained, PD-1..PD-5)" --body "..."
```

(Cuerpo del PR: el problema —pérdida silenciosa probada en 7 columnas, censo de 9 decimales—, la política PD-1, los 27 cambios, amendment #7 canónico, test de fidelidad + paridad FIFO exacta, conteo de tests, nota wipe & reload. CI `backend` + `frontend` deben quedar verdes antes del merge — el usuario aprueba el merge.)
