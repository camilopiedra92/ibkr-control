# Spec — Precisión decimal exacta en hechos Flex (NUMERIC unconstrained)

**Fecha:** 2026-06-12
**Estado:** aprobado (brainstorming 2026-06-12)
**Programa:** follow-up de Tier 1 world-class model (re-abre parcialmente la decisión de
precisión de Migration F, Phase 2 polish) · branch `tier1/exact-decimal-precision`
**Specs relacionados:** `2026-06-10-tier1-worldclass-model-design.md` (W3 / T1-D10),
`docs/plans/2026-05-24-phase2-polish-backlog.md` (Migration F)

---

## 1. Problema

El golden test de W3 (restatement log) destapó que el persister recibe valores
full-precision del XML pero las columnas `NUMERIC(20,4)` los redondean en el write:
114 falsos positivos en `open_position_lots.cost_basis_usd` (e.g. `255.869378` →
almacenado `255.8694`). W3 lo **compensó** cuantizando el incoming a la scale de la
columna antes de comparar (`_quantize_to_scale`, ROUND_HALF_UP) — correcto dado el
schema, pero la pérdida de precisión respecto a la fuente siguió existiendo.

El censo empírico contra los 3 fixtures XML reales sanitizados (2024 + 2025 + 2026 FOP)
probó **pérdida silenciosa en 7 columnas**, todas de la familia "totales USD" `(20,4)`:

| Columna `(20,4)` | Attr fuente | Max decimales reales | Ejemplo |
|---|---|---|---|
| `trades.commission_usd` | `ibCommission` | **9** | `-0.365021034` |
| `open_position_lots.mark_value_usd` | `positionValue` | **9** | `508.740212668` |
| `open_position_lots.cost_basis_usd` | `costBasisMoney` / fallback `costBasisPrice` | 6 / **9** | `207.104329` |
| `trades.proceeds_usd` | `proceeds` | 8 | `-49.09788959` |
| `closed_lots.cost_basis_usd` | `cost` | 6 | `512.142145` |
| `closed_lots.fifo_pnl_usd` | `fifoPnlRealized` | 6 | `-12.227166` |
| `closed_lots.proceeds_usd` | computado `cost + fifoPnl` | 6 | — |

Los 9 decimales de `ibCommission` no son ruido: IBKR cobra fees regulatorios por acción
(FINRA TAF, SEC fee proporcional) — la comisión real de un fill ES un número de 9
decimales. Redondearla corrompe la suma de comisiones capitalizables (Art. 69 ET).

Las familias `(20,6)`/`(20,8)` hoy no pierden contra los fixtures, pero
`gross_rate_per_share (20,6)` está **exactamente en el límite** (6 observados, cero
margen) e IBKR **no documenta** una scale máxima para ningún atributo. El rationale de
Migration F ("match precisión del XML IBKR source") quedó refutado empíricamente.

## 2. Decisiones

### PD-1 — Política de precisión (la regla durable)

> **La scale declarada en una columna NUMERIC es un contrato con la fuente. Se declara
> solo cuando la fuente documenta su precisión. Si la fuente no la documenta, el
> storage es exacto: `NUMERIC` unconstrained.**

Postgres `NUMERIC` sin `(p,s)` almacena el valor exactamente como llegó — sin
rescaling, sin redondeo, sin trailing zeros. La `(p,s)` no es un formato de storage
(el motor almacena ambos idéntico, base-10000): es un **constraint de redondeo en el
write path**. Cuando la fuente es externa y no documenta su precisión, ese constraint
es una afirmación sobre datos ajenos que no podemos garantizar — y ya falló dos veces
(Migration F, golden run W3).

El redondeo es regla de negocio del dominio (Phase 3+: conversión COP, redondeos
DIAN) — vive en el **read path**, nunca en el write path. Patrón estándar de capas de
hechos: el raw fact layer espeja la fuente exacto; el dominio redondea con regla
explícita.

**Enfoques rechazados:**
- *Scale uniforme generosa `(28,12)` + guard fail-loud:* compensación, no solución —
  conserva la maquinaria de quantize activa y un ritual de widening cuando IBKR exceda.
- *Widen quirúrgico al max observado por columna:* repite el error de Migration F con
  más datos; el censo es de 3 cuentas, cero margen, scales ad-hoc heterogéneas.
- *Integer minor units (Stripe-style):* ya rechazado en Migration F por razones que
  siguen válidas (multi-currency, interop `Decimal`).

### PD-2 — Alcance: 27 columnas (familia fuente-IBKR completa)

Todas las columnas `Numeric(20,x)` cuyo valor proviene del XML Flex pasan a
`Numeric()` sin args — incluidas quantities/prices que hoy no pierden (política
uniforme, sin excepciones que recordar):

| Tabla | Columnas → `Numeric()` |
|---|---|
| `trades` | `qty`, `price_usd`, `proceeds_usd`, `commission_usd` |
| `closed_lots` | `qty`, `cost_basis_usd`, `proceeds_usd`, `fifo_pnl_usd` |
| `open_position_lots` | `qty`, `cost_basis_usd`, `mark_price_usd`, `mark_value_usd` |
| `transfers` | `qty` |
| `cash_transactions` | `amount_usd` |
| `change_in_dividend_accruals` | `quantity`, `gross_rate_per_share`, `gross_amount_usd`, `tax_usd`, `fee_usd`, `net_amount_usd` |
| `open_dividend_accruals` | `quantity`, `gross_rate_per_share`, `gross_amount_usd`, `tax_usd`, `fee_usd`, `net_amount_usd` |
| `instruments` | `multiplier` |

Nota sobre `instruments.multiplier`: censado contra los 3 XMLs reales da `{0, 1, 5}`
(hoy enteros), pero es fuente IBKR sin contrato documentado → entra en PD-2 por
política (multipliers fraccionales existen en derivados internacionales).

**Se quedan con scale declarada (contrato documentado, fuera de alcance):**
- `trm.value_cop (12,4)` — la TRM oficial se certifica con 2 decimales
  (Superfinanciera/DIAN); margen 2×. Auditado: la fuente Socrata emite 2 decimales.
- `participations.pct (5,4)` — input de dominio propio (wizard), no fuente externa;
  el invariante de 4 decimales es nuestro.

### PD-3 — La maquinaria W3 se queda, sin cambio de código

`_values_differ` lee `table.c[col].type.scale` → ahora `None` → `_quantize_to_scale`
es pass-through → comparación exacta, que con storage exacto es la semántica correcta
("comparar a precisión de storage" sigue siendo el principio; la precisión de storage
ahora es exacta). El helper y su ROUND_HALF_UP quedan latentes por si alguna columna
con scale documentada se vuelve material col algún día. Solo se actualizan docstrings.

Los 114 falsos positivos quedan resueltos **por construcción** (storage == incoming),
no por compensación.

**Consecuencia aceptada:** los restatements futuros son más sensibles — un cambio real
en el 9° decimal de `fifo_pnl_usd` ahora cuenta como restatement. Correcto: la fuente
cambió; antes lo enmascarábamos.

### PD-4 — Rollout: baseline amendment #7 (política T1-D14 vigente)

Amendment #7 del baseline pristino Tier 1 (`a9977ac077e5`), **regenerado canónicamente
en container** (`alembic revision --autogenerate` dentro del backend container — el
host no alcanza el postgres; lección Phase 2.9/amendment #6: hand-edits rechazados, el
camino canónico valida formato/orden de emisión) + wipe & reload de dev (`down -v`,
recarga XMLs por wizard). Cero migración transicional. La política T1-D14 (baseline
mutable hasta el primer deploy) sigue vigente.

### PD-5 — Test de fidelidad de fuente (el que habría atrapado Migration F)

Nuevo test roundtrip XML→DB→SELECT contra los 3 fixtures reales: para cada columna de
PD-2, assert `valor_DB == Decimal(attr_XML)` exacto (comparación `Decimal`, no float).
Lockea PD-1 para siempre: cualquier columna futura que redondee fuente rompe el test.

## 3. Cambios de código (resumen)

- **Modelos** (`db/models/flex_raw.py`, `db/models/instruments.py`): 27 columnas
  `Numeric(p,s)` → `Numeric()`. Único cambio de producto.
- **Parser**: sin cambios (ya produce `Decimal` exactos vía `_dec`).
- **Persister / W3**: sin cambios de código; docstrings de `_quantize_to_scale` /
  `_values_differ` actualizados (PD-3).
- **Migración**: baseline amendment #7 (PD-4).
- **Tests:**
  - Nuevo: fidelidad roundtrip exacta (PD-5).
  - Golden restatement test: sigue en 0 — ahora por construcción.
  - `tests/ingest/flex/test_restatements.py` (boundary HALF_UP, premisa columna
    scale-4 desaparece): se re-apunta a documentar el pass-through con `scale=None`.
  - `tests/test_flex_ingest_replay.py` (paridad FIFO con tolerancia de redondeo a
    4dp): la comparación se vuelve **exacta** — test más fuerte.
  - Drift test + boot smoke prod-local como siempre.

## 4. Impacto colateral (verificado durante brainstorming)

- **API/frontend: ninguno.** Los hechos USD no se exponen por API todavía (pantallas
  de lotes = Phase 3); `participations.pct` (que sí se expone) no cambia. OpenAPI sin
  cambios.
- **Storage/perf:** NUMERIC unconstrained almacena idéntico internamente (base-10000);
  volumen irrelevante. Agregaciones (`SUM`) exactas.
- **CLAUDE.md:** la decisión locked "Formato de datos numéricos" de Phase 2 polish
  (Migration F: `(20,4)`/`(20,8)`/`(20,6)` "match precisión del XML IBKR source")
  queda **SUPERSEDED** para columnas fuente-IBKR — anotar en el update de cierre.

## 5. Criterio de éxito

1. Suite completa verde (incl. nuevo test de fidelidad + replay exacto).
2. `ruff check` limpio; drift test verde contra el baseline amendado.
3. Boot smoke prod-local: migrate aplica baseline #7, backend arranca como `app_rls`.
4. Wipe & reload dev: re-ingest de los 3 XMLs reales; golden restatement = 0; spot
   check `commission_usd` de un trade real == valor XML con 9 decimales.
