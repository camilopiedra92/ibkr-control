# Renta → ibkr-control — Análisis de gap de dominio fiscal

**Fecha:** 2026-07-06
**Propósito:** inventario exhaustivo de la lógica de dominio fiscal que `renta`
(sibling, `/Users/camilopiedra/Development/renta/`) ya implementa y que
`ibkr-control` **todavía no tiene** (Phases 3–6 sin empezar). Insumo directo del
brainstorming de Phase 3 (domain layer + lotes).

**Método:** lectura directa del spec FIFO, `schema.py` (`OperacionCierre`),
`loader.py`, `_fifo_replay.py`, `docs/tax_rules_co.md`, `CLAUDE.md` de renta +
exploración de las funciones de agregación fiscal en `renta2025.py` + inventario
con evidencia del estado actual de `backend/` en ibkr-control.

> **Regla de oro (decisión locked #1):** `ibkr-control` **reimplementa** su propia
> lógica; **lee** renta como referencia de correctitud. Nunca importa código.
> Este doc mapea QUÉ reimplementar y con qué fórmula, no autoriza copiar.

---

## 0. TL;DR

Los dos repos son **complementos exactos**:

- `ibkr-control` = toda la **capa de ingesta** (Flex XML → Postgres, TRM Socrata,
  persister idempotente, multi-tenant/RLS/authz) + **cero** dominio fiscal.
- `renta` = todo el **dominio fiscal** maduro (307 tests, 22 bugs sangrados,
  validado número-a-número contra el Form 210 real 2024 y 2025) sobre ingesta
  SQLite mono-usuario.

**El gap = prácticamente toda la lógica fiscal IBKR de renta = Phases 3–6.**
La fundación de ibkr-control ya tiene **todos los insumos crudos** listos; renta
es la **implementación de referencia validada** que sirve de oráculo de paridad.

---

## 1. Lo que YA está en pie (fundación, NO es gap)

Confirmado con evidencia en `backend/src/ibkr_control/db/models/`:

| Insumo | Dónde | Estado |
|---|---|---|
| Hechos crudos USD | `trades`, `closed_lots`, `open_position_lots`, `transfers`, `cash_transactions`, `change_in/open_dividend_accruals` | ✅ persistidos, idempotentes |
| `asset_class` + `instrument_id` en lotes (NOT NULL) | `closed_lots` / `open_position_lots` | ✅ discriminador fiscal de primera clase (Phase 2.9) |
| TRM diaria COP | `trm_days` (`value_cop`, `vigencia_desde/hasta`) | ✅ poblada por Socrata |
| Participación con vigencia | `participations` SCD-2 (`pct` Numeric(5,4), `valid_from/to`, CHECK 0..1) | ✅ write-path canónico (`db/participations.py`) |
| Cierres ya resueltos | `closed_lots.open_date`/`close_date`/`cost_basis_usd`/`proceeds_usd`/`fifo_pnl_usd` | ✅ vía pool CLOSED_LOT en el persister |

**Confirmación global del gap:** las cadenas `730`, `Art.288`, `1797`, `cost_cop`,
`pnl_cop`, `regime`, `apply_pct`, `form160`, `form210` aparecen en el backend
**únicamente en comentarios/docstrings** ("Phase 3 domain"), nunca en código
ejecutable. No existe `backend/src/ibkr_control/domain/`.

### 1.1 Consecuencia arquitectónica: ¿replayar FIFO o leer `closed_lots`?

El persister de ibkr-control ya persiste los cierres con `open_date` autoritativo
del pool CLOSED_LOT, que **ya refleja** selección manual de lotes y transfers que
preservan la fecha original. Es decir: los `OperacionCierre` de renta ≈ filas de
`closed_lots`.

→ **El FIFO replay completo de renta (`_fifo_replay.py`) probablemente NO hay que
reimplementarlo.** Phase 3 puede leer `closed_lots` directo.
**Verificar contra data real primero:** que `closed_lots.open_date` preserva el
linaje cross-cuenta (caso **BROS**: comprado en una cuenta, transferido a otra,
vendido ahí). Si el CLOSED_LOT de IBKR conserva ese `open_date`, el replay es
redundante. Si no, hay que reconstruir el linaje vía `transfers` + `instrument_id`
(el symbol-join de Phase 3 ya murió — ver spec transfer-instrument-lineage).

---

## 2. Catálogo de lógica faltante (por área + Phase)

Fórmulas verificadas contra renta. Todo lo siguiente **no existe** en ibkr-control.

### Phase 3 — Domain layer + lotes

#### A. Clasificación 730 días RO/GO — Art. 300 ET
`renta2025.py:3361` (inline, no hay función dedicada)
- `days_held = (fecha_cierre − fecha_compra).days` — aritmética de `date`,
  **exclusiva** (comprar y vender el mismo día = 0 días).
- `< 730` → **RO** (Renta Ordinaria, tarifa Art.241 progresiva).
- `≥ 730` → **GO** (Ganancia Ocasional, **15% flat**).
- **Solo aplica a STK.** FUT/OPT nunca son GO (régimen propio, ver E).
- ⚠ IBKR marca L/T con umbral ≥365 días (US); Colombia exige ≥730 → **contar
  días propio, no confiar en el flag de IBKR**. Renta tiene un validador que solo
  **advierte** si IBKR reporta L/T (`_validate_lt_from_csv`).

#### B. Conversión a COP bajo Art. 288 ET (activos no monetarios)
`schema.py:52-122` — es el corazón, encapsulado en `OperacionCierre`.
Convención de signos IBKR: `proceeds>0`, `cost<0`, `comm<0`, `pnl≈proceeds+cost+comm`.
Cada componente a la **TRM de su propia fecha de causación**, NO `pnl_usd × TRM_venta`:

```
cost_cop       = cost_usd     × TRM(fecha_compra)     # negativo; ya incluye comisión de apertura capitalizada
proceeds_cop   = proceeds_usd × TRM(fecha_cierre)     # positivo
close_comm_cop = comm_usd     × TRM(fecha_cierre)     # negativo
pnl_cop        = proceeds_cop + cost_cop + close_comm_cop
fx_diff_cop    = -cost_usd × (TRM_venta − TRM_compra) # diferencia en cambio, informativo
```

- Impacto real: el método ingenuo (`pnl_usd × TRM_venta`) sobreestimó la utilidad
  2025 en **$4.09M COP**. La diferencia en cambio queda implícita en el P&L COP y
  no se reconoce hasta la realización (Art. 288 modificado por Ley 1819/2016).
- **Dividendos NO siguen Art. 288** (no son activos no monetarios): cada pago a su
  TRM única (fecha de pago).

#### C. TRM lookup con fallback
`renta2025.py:197` (`_trm_lookup`)
- Match exacto por fecha; si es fin de semana/festivo → **día hábil anterior más
  cercano** (`bisect_right − 1`).
- Fecha anterior al primer día de la serie → `ValueError` fail-loud.
- En ibkr-control: `trm_days` ya tiene `vigencia_desde/hasta` → el lookup puede ser
  `WHERE date <= :d ORDER BY date DESC LIMIT 1` o usar la vigencia directamente.
  Se inyecta como `Callable[[date], Decimal]` a las funciones de conversión.

#### D. `apply_pct` / participación temporal
→ **Aquí ibkr-control necesita MÁS que renta.** Ver §4.1.

#### E. Régimen DUAL STK vs FUT/OPT — Decreto 1797/2008
`renta2025.py:453` (`_ib_ingresos_brutos_cop`), `:493` (`_ib_costos_cop`)
- **STK (Art. 288):**
  - bruto cas.74 = `proceeds × TRM_venta`
  - costo cas.77 = `|cost × TRM_compra|` + `|comm_cierre × TRM_venta|`
  - Pérdidas quedan implícitas en `cas.74 − cas.77`.
- **FUT/OPT (Decreto 1797/2008 + Art. 26):** netear P&L por `(cuenta, símbolo)` =
  "contrato" (el símbolo IB de un futuro codifica tipo+subyacente+vencimiento,
  p.ej. MESU5):
  - neto del contrato **positivo → cas.74** (`max(0, neto)`)
  - neto del contrato **negativo → cas.77** (`max(0, −neto)`, deducción)
  - No se descompone proceeds/cost del derivado, solo el P&L neto.
- **Postura permisiva consistente** (pérdidas FUT compensan dentro de la cédula) —
  decisión fiscal ya tomada en renta; replicar o re-decidir explícitamente.
- Identidad verificable: `brutos − costos = P&L_COP fiscal neto`.
- ⚠ FUT **proceeds = notional, NO ingreso fiscal**: un MESU5 reporta ~$32K USD de
  proceeds pero P&L real ~$50 — nunca usar proceeds de futuros como bruto (infla
  cas.74 y el test de medios magnéticos).

### Phase 5 — Dividendos, patrimonio, reportes

#### F. Dividendos IB
`renta2025.py:237` (`_gross_dividendos_cop`)
- `gross_cop = Σ gross_usd × TRM(fecha_pago) × pct`. Van a **subcédula dividendos
  cas.81** (Art. 242 Ley 2277/2022, tarifa Art. 241).
- ⚠ **Gap de camino propio:** renta los saca de un **CSV oficial IBKR** que la
  decisión #10 de ibkr-control excluye (Flex-only). Hay que derivarlos de
  `cash_transactions` (`type='Dividends'`) + `change_in/open_dividend_accruals`.
  Renta lo tiene como pendiente sin implementar → **no hay referencia**, diseñar.

#### G. WHT Art. 254 (descuento tributario)
`renta2025.py:218` (`_descuento_art254_cop`)
- `descuento = Σ wht_usd × TRM(fecha_pago) × pct`. Es **descuento** (resta al
  impuesto), NO deducción de la base. `_IMPUESTO_NETO = _IMPUESTO_241 + descuento`.
- ⚠ Mismo gap: renta del CSV; ibkr-control debe linkear
  `cash_transactions type='Withholding Tax'` al dividendo vía `action_id`, o usar
  `tax_usd` de los accruals.
- ⚠ Precisión: renta reconcilia RIC/IRD reclassification (Flex vs CSV vs 1042-S,
  ~$1.60 en ICSH 871(k) exempt). Flex-only pierde esa corrección — imprecisión
  menor documentable.

#### H. Interés IB — Art. 338 ET
`renta2025.py:898` (`_interes_ib_capital_cop`)
- `Σ amount_usd × TRM(fecha) × pct` para `cash_transactions type='Broker Interest
  Received'`. **100% gravable** (sin componente inflacionario Art. 38, que solo
  aplica a deuda interna). Va a **cédula capital cas.38**.

#### I. Patrimonio IB al 31-dic — Art. 261-263 ET
`renta2025.py:761` (`_patrimonio_ib_yearend`)
```
trm_ye = TRM(31-dic)
stock_cop = Σ stock_usd × trm_ye × pct
cash_cop  = Σ (cash_usd + dividend_accruals_usd) × trm_ye × pct   # accruals con cash, NO con stock
```
- Insumo: `open_position_lots` / equity snapshots al 31-dic.
- ⚠ **default pct = 0.0 aquí** (excluye cuentas sin participación explícita), a
  diferencia de P&L (default 1.0 en renta).

#### J. Form 160 (activos exterior) — Art. 607 ET
`renta2025.py:2707` (inline)
- `total_exterior = patrimonio_IB + cripto`; **obligado si `> 2000 × UVT`**
  (estricto `>`). Cripto fuera de IBKR → fuera del scope IBKR-only salvo modelar
  aparte. Códigos DIAN: 01 efectivo, 02 acciones/valores, 06 otros/cripto.

#### K. Medios magnéticos — Res. 162/2023 + 227/2025
`renta2025.py:1458` (`_validacion_medios_magneticos`)
- Test **conjuntivo (AMBAS)**:
  1. brutos totales `> 11800 UVT` (año actual **O** anterior), y
  2. `(capital + no laborales + dividendos) > 2400 UVT` (año actual).
- Requiere brutos de TODAS las cédulas → parcialmente fuera del scope IBKR-only
  (ibkr-control solo aporta el subset IBKR de los brutos).

#### L. Comparación patrimonial — Art. 236-239 ET
`renta2025.py:1413` (`_comparacion_patrimonial`)
- `A = patrimonio_líquido_actual − anterior`;
  `B = RLG + INCR − impuestos_pagados − retenciones`;
  si `A > B` el exceso es renta presunta (Art. 239). Decrementos nunca generan
  renta presunta (`max(0, A−B)`). Requiere cifras del año anterior (fuera de IBKR).

#### M. Tarifa Art. 241 progresiva
`renta2025.py:962` (`_impuesto_art241`)
- Impuesto **marginal por tramos** UVT: cada tramo aporta
  `(ancho en UVT dentro del tramo) × tarifa_marginal × UVT_cop`. Necesita la tabla
  de tramos por año (ver §3).

#### N. Reajuste fiscal Art. 70 ET (opcional, avanzado)
`renta2025.py:629` + `schema.py:86`
- Uplift al costo de lotes **STK que cruzan año calendario**:
  `factor = Π(1+tasa_año)` sobre `[año_compra .. año_venta]` **inclusive**;
  `uplift = |cost_cop| × (factor − 1)`. Lote mismo año → 0.
- **Interruptor OFF por defecto** (informativo). 4 riesgos fiscales sin resolver
  (recaracterización activo movible, coexistencia Art. 288, quirk día-conteo,
  pérdida que compensa renta laboral). Feature avanzada, no MVP.

### Phase 4 — Simulador
- Modo STK ("si vendo hoy, ¿cuánto impuesto?") y modo FUT con neteo YTD. Reusa
  E + A + B + M. **Lógica nueva** (renta reporta lo pasado; el simulador es
  prospectivo) — construida sobre las primitivas de renta, sin referencia directa.

---

## 3. Capa de configuración/referencia que falta ENTERA

ibkr-control no tiene nada del `config/legal_co.py` de renta. Falta como **datos
por año gravable** (en multi-tenant: **reference data global sin RLS**, patrón
`trm`/`instruments` que ibkr-control ya conoce):

| Config | Valores | Usado por |
|---|---|---|
| UVT por año | 2023 $42,412 · 2024 $47,065 · 2025 $49,799 · 2026 TBD | todos los umbrales |
| Tramos tarifa Art.241 | 7 tramos (0/19/28/33/35/37/39%) en UVT | impuesto |
| Umbrales | 730d · 2000 UVT (Form 160) · 11800/2400 UVT (medios mag.) · 1340 UVT (Art.336) · 790 UVT (exención) | clasificación/reportes |
| Tasa reajuste Art.70 | 2024 10,97% (Dec.174/2025) · 2025 5,81% (Dec.449/2026) | reajuste |
| Régimen por asset | STK→Art.288+730d · FUT/OPT→Decreto 1797 | régimen dual |

---

## 4. Dónde ibkr-control necesita MÁS que renta (no solo portar)

Puntos donde copiar renta 1:1 sería un error — el pivote SaaS multi-tenant cambia
los invariantes.

### 4.1 Participación temporal SCD-2 vs pct estático
Renta usa `pct` fijo por cuenta (50/100/100, `_ACCT_PCT_CAMILO`). ibkr-control
tiene `participations` con `valid_from/valid_to` → `apply_pct(amount, account,
party, at_date)` debe elegir el pct **vigente a la fecha del hecho**:
`open_date` para costo, `close_date` para proceeds, `fecha_pago` para dividendos,
31-dic para patrimonio. Decisión de diseño real (roadmap Phase 3 decisiones #2/#3).

### 4.2 Dividendos/WHT desde Flex, no CSV
Renta depende del CSV IBKR (con RIC reclassification). ibkr-control es Flex-only →
reconstruir dividendos y WHT desde `cash_transactions` + accruals, linkeando por
`action_id`. Renta lo tiene como pendiente sin resolver → **sin implementación de
referencia**, hay que diseñarlo.

### 4.3 Residencia/régimen por party
Renta asume Camilo residente CO año completo, tarifa marginal en config.
Multi-tenant: `parties` **no tiene país/residencia/tarifa marginal** (confirmado en
el inventario) — gap de schema para Phase 3+. `users.marginal_rate` existe (Phase 1)
pero a nivel user, no party.

### 4.4 Default pct fail-loud, no 1.0
Renta usa `pct default 1.0` como conveniencia mono-usuario. Bajo el quality bar de
ibkr-control (zero silent data), una cuenta sin participación explícita debe
**fallar loud**, no asumir 100%.

### 4.5 Sealed years
`restatement_log.sealed_year` ya existe en ibkr-control (señal de que un año
declarado cambió). Renta no tiene ese concepto — capacidad **superior** que el
dominio debe consumir (alertar "tu Form 210 pudo cambiar").

---

## 5. Edge-cases y bugs a NO re-sangrar (lecciones de renta)

De los 22 bugs de renta, los relevantes a IBKR:

- **Doble conteo de caja IB en patrimonio** (bug #22): stock y caja separados;
  accruals van con caja, no en ambos.
- **Snapshot 31-dic contado 2×** (bug #2): el cierre año N reaparece como
  PriorPeriod en N+1 → filtrar por import/año. (ibkr-control lo maneja distinto vía
  multihome per-org — verificar.)
- **FIFO edge-cases verificados** (spec v4): BROS (transfer preserva open_date),
  AMD (compra+venta mismo día auto-cancelada), ASTS (selección manual ≠ FIFO →
  CLOSED_LOT autoritativo), PYPL/OSCR (dos ventas mismo día → ordenar por
  transactionID).
- **Redondeo:** renta redondea a int COP por cuenta y globalmente (puede diferir
  por centavos). ibkr-control: `Decimal` exacto (PD-1 exact-decimal-precision),
  redondear solo en el read path.
- **FUT proceeds = notional** (ver E): nunca como bruto fiscal.
- **Dividendos IB pueden faltar** (2024 no hubo): tolerar ausencia, no fallar.

---

## 6. Secuencia recomendada + criterio de paridad

Mapea limpio al roadmap existente:

1. **Config layer** (UVT/tramos/umbrales/reajuste como reference data global) —
   pre-requisito de todo.
2. **Phase 3** (domain): `trm_lookup`, `apply_pct` temporal, `days_held` +
   clasificación 730d RO/GO, `cost_cop`/`pnl_cop` Art.288, régimen DUAL → + 3
   pantallas de lotes. **Leer `closed_lots` directo, no replayar FIFO** (verificar
   linaje transfer primero, §1.1).
3. **Phase 5** (reportes): dividendos + WHT **desde Flex** (diseño nuevo), interés,
   patrimonio, Form 160, tarifa Art.241, Form 210 desagregado cas.74/76/77/78/81.
   Reajuste Art.70 opcional.
4. **Phase 4** (simulador): prospectivo sobre las primitivas.
5. **Paridad:** test que corra el dominio sobre los mismos XMLs 2024/2025 y matchee
   `renta/tests/_invariants.py` (tolerancia < $1K COP). Ese es el criterio de éxito
   de renta como oráculo.

---

## 7. Referencias de renta (leer antes de implementar cada área)

| Área | Archivo en renta |
|---|---|
| FIFO (spec completo) | `docs/flex_fifo_loader_spec.md` |
| FIFO (replay) | `documentos/ibkr_flex/_fifo_replay.py`, `loader.py` |
| Modelo de dominio (Art.288, reajuste) | `documentos/ibkr_flex/schema.py` (`OperacionCierre`) |
| Reglas fiscales detalladas | `docs/tax_rules_co.md` (§2 730d, §10 Art.288, §12 Art.70) |
| Funciones de agregación fiscal | `renta2025.py` (líneas citadas en §2) |
| Config legal (UVT/tramos/reajuste) | `config/legal_co.py` |
| Paridad (invariantes pinned) | `tests/_invariants.py` |
| Bugs históricos (evitar regresiones) | `renta/CLAUDE.md` § "Bugs detectados y corregidos" |

Mapping regla→archivo también en `docs/references/renta-cross-references.md`.
