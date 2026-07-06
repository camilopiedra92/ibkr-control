# Ingest-completeness + Integrity Hardening (pre-domain) — Design

**Fecha:** 2026-07-06
**Branch:** `feat/ingest-completeness-hardening`
**Tipo:** pasada de captura-completa + integridad en la capa de datos, pre-Phase-3.
**Origen:** gap analysis renta → ibkr-control (`docs/references/renta-domain-gap-analysis.md`).

---

## 0. Contexto y motivación

El gap analysis reveló que la fundación de ibkr-control está lista para el domain
layer fiscal (Phase 3+) **excepto** por tres huecos de capa de datos que hoy tienen
**valor presente** (no son primitives especulativos):

1. **`cash_transactions` descarta source data fiscal** que el parser ya ve. Sin
   `action_id` no se puede linkear un dividendo con su Withholding Tax → el
   descuento Art. 254 ET es **estructuralmente incomputable**. Y como la retención
   de `xml_bytes` es latest-1 (R1), los imports viejos **ya no se replayan** →
   cada día de ingesta acumula hechos que no podrán computar WHT. Es
   captura-completa de dato **efímero**, no preparación.
2. **`instruments` no tiene país del emisor** — necesario para tarifa de tratado
   (US 30% vs NL vía CDI) y códigos de país del Form 160. Source data disponible
   ahora (`issuerCountryCode`).
3. **`participations` no impide solapamientos** — `apply_pct(account, at_date)`
   (Phase 3) debe ser fail-loud, pero el write-path del wizard puede escribir hoy
   participaciones con vigencia solapada → doble-conteo silencioso. Guard de
   integridad sobre una tabla **ya escrita**.

### Por qué AHORA (y por qué solo estos tres)

- **Baseline mutable pre-deploy** (política Tier 1, guardada por HD-4): cambios de
  schema entran como amendment canónico sin migración transicional. La ventana se
  cierra al primer deploy.
- Los tres tienen **valor presente**: #1/#2 capturan dato efímero que se pierde;
  #3 protege integridad de data viva.
- **Se difiere #3 del gap analysis (parties fiscal identity)**: es el único que
  agrega un primitive **sin consumidor presente** y con dato **no efímero** — el
  anti-patrón del `field.tsx` ("un primitive sin consumidor es deuda disfrazada de
  preparación"). Se diseñará en el domain spec de Phase 3, junto a la capa de
  config legal (UVT/tarifa Art.241) que le da sentido a una "tarifa marginal".

### Decisiones cerradas en brainstorming

- Empaquetado: **un solo spec/PR** cohesivo (patrón HD-*), no dos PRs separados.
- Scope: **IC-1 + IC-2 + IC-3**; parties fiscal (IC-ex) **diferido a Phase 3**.

---

## 1. IC-1 — `cash_transactions` capture-completeness

### Estado actual (verificado)

`CashTransaction` (`db/models/flex_raw.py:373-418`) tiene: `transaction_id,
account_id, instrument_id, conid, type, currency, amount_usd, description, date,
symbol`. **Sin `raw_attrs`** (a diferencia de los accruals, que sí lo tienen).
El parser (`ingest/flex/parser.py:477-503`) extrae exactamente eso y **descarta**
`actionID`, `issuerCountryCode`, `exDate`, `settleDate`, `reportDate` — aunque el
XML los trae y el parser de accruals (mismo archivo) sí los tipa.

### Cambio

Agregar a `CashTransaction`, `ParsedCashTransaction` (`ingest/flex/_models.py:96`),
el parser `_parse_cash_transactions`, y el persister de cash:

| Columna | Tipo | Fuente XML | Por qué |
|---|---|---|---|
| `action_id` | `str \| None` | `actionID` | Correlación **Dividend ↔ Withholding Tax**. Sin esto Art. 254 es incomputable. Sin hogar canónico alternativo. |
| `issuer_country` | `str \| None` | `issuerCountryCode` | País emisor (fallback de fidelidad; canónico va en instruments, IC-2). Tarifa de tratado + Form 160. |
| `settle_date` | `date \| None` | `settleDate` | Fecha fiscal canónica base caja (Art. 27 + 288 ET). |
| `report_date` | `date \| None` | `reportDate` | Reconcilia con extractos IB / 1042-S. |
| `ex_date` | `date \| None` | `exDate` | Ex-dividend record date; base de causación/propiedad. |
| `raw_attrs` | `JSONB` (server_default `'{}'`) | atributos no tipados | Red de seguridad zero-loss, **espejando el patrón de los accruals**. |

### Reglas

- **`date` se conserva** (`dateTime ‖ settleDate`) — lo usan el índice `Index(None,
  "date")` y el orden. Las 3 fechas explícitas son **puramente aditivas**.
- **La decisión causación-vs-caja NO se toma aquí.** Se capturan ambas bases
  (`ex_date`/`report_date`/`settle_date`) y **Phase 5 elige** el criterio fiscal.
  Documentado como no-goal.
- **Todos nullable.** La Flex Query 2024 no trae `conid` ni algunos de estos
  campos en cash; fees/intereses no tienen `action_id`/país. Fail-loud NO aplica a
  source parcial legítimo (a diferencia de los creators del securities master).
- El persister sigue el patrón existente de cash (resolver-only para
  `instrument_id`; los campos nuevos son pass-through directo).

---

## 2. IC-2 — `instruments.issuer_country` (canónico + fallback)

### Estado actual (verificado)

`Instrument` (`db/models/instruments.py`) tiene: `symbol, name, asset_class,
currency, multiplier`. **Sin país.** El `issuerCountryCode` solo vive hoy en los
accruals por-hecho; `cash_transactions` ni lo captura (lo arregla IC-1).

### Cambio

Patrón **canónico + fidelidad de fuente**, idéntico al que ya usan con `conid`
(TL-D4):

- `issuer_country: str | None` (ISO-2) en `instruments`. El país del emisor es
  atributo **estable del instrumento** (país de constitución/listado), no del
  hecho-en-el-tiempo → pertenece al securities master canónico.
- Poblado en `_ensure_instruments` desde los creators que traigan
  `issuerCountryCode`, con **anti-churn material-change `DO UPDATE`** (misma
  mecánica que `symbol`: solo avanza en cambio real).
- **Resolver-never-creates** también para país: un tag resolver con país pero sin
  instrument no crea nada.

### Censo obligatorio (patrón CR-1)

Antes de cerrar la regla, **censar los 3 XML reales sanitizados** para confirmar
qué tags creators traen `issuerCountryCode`:

- Si los creators lo traen → canónico poblado en instruments.
- Si solo lo traen resolvers (cash/accruals) → el canónico queda **parcial** y el
  **fallback por-hecho** (IC-1 en cash + accruals que ya lo tienen) cubre el resto.

Phase 5 resuelve: `país = instrument.issuer_country ?? fact.issuer_country`.

### Alternativas descartadas

- **Solo-en-hecho:** repite el país en N filas sin fuente canónica (viola el
  principio del securities master de W2).
- **Solo-en-instrumento:** pierde el país cuando `instrument_id` es NULL, que en
  cash 2024 es el 100% de las filas.

---

## 3. IC-3 — `participations` non-overlap `EXCLUDE`

### Estado actual (verificado)

`Participation` (`db/models/participations.py`): PK `(party_id, account_id,
valid_from)`, CHECK `pct_range` (0..1), CHECK `valid_range` (`valid_to IS NULL OR
valid_to > valid_from`). `valid_from`/`valid_to` son `Date`. Org-scoped con FORCE
RLS. **Nada impide dos participaciones con vigencia solapada** para el mismo
`(party, account)` — la PK solo bloquea igual `valid_from` exacto.

### Cambio

**Diseño world-class: tipar el rango como columna generada, NO computarlo inline
en el constraint.** El vector de fragilidad de un `EXCLUDE` es meter una expresión
funcional (`daterange(valid_from, valid_to, '[)')`) *adentro* del constraint —
Postgres la normaliza a su gusto (`'[)'::text`, reordenamientos) y el drift test
(`compare_metadata` con `compare_server_default: True`) marca drift espurio. La
mitigación es **eliminar la expresión del constraint**, no pinnearla: una columna
`daterange` **generada** por Postgres, y el `EXCLUDE` operando sobre esa columna
plana (que SQLAlchemy/Alembic representan y comparan sin ambigüedad). Es el mismo
ethos que rechazar JSONB por detail tables tipadas: **tipá el invariante, no lo
computes en el borde.** (Verificado: SQLAlchemy 2.0.36+ / Alembic 1.18.4 soportan
`Computed(persisted=True)` + `DATERANGE` nativos; no se usan aún en el repo.)

- **Columna generada** en `participations`:
  ```
  validity daterange GENERATED ALWAYS AS (daterange(valid_from, valid_to, '[)')) STORED
  ```
  La mantiene Postgres desde `valid_from`/`valid_to` (single source of truth, cero
  desnormalización). Aditiva: `valid_from`/`valid_to` y la PK quedan intactos, no
  toca el write-path del wizard. Queda consultable para el `apply_pct` de Phase 3.
- **Migración:** `CREATE EXTENSION IF NOT EXISTS btree_gist` (no se usa en ningún
  lado hoy; corre como **owner** en el container one-shot `migrate`, no `app_rls`).
- **`ExcludeConstraint` column-based** (NO `literal_column`):
  ```
  EXCLUDE USING gist (
      organization_id WITH =,
      party_id        WITH =,
      account_id      WITH =,
      validity        WITH &&
  )
  ```
  → **imposible** tener dos participaciones con vigencia solapada para el mismo
  `(org, party, account)`. `valid_to` NULL = límite superior ilimitado (Postgres
  nativo). Half-open `[)` consistente con la semántica SCD-2 (mismo criterio que
  los grants de SP2).
- Convive con la PK y los CHECK existentes; compatible con FORCE RLS (los EXCLUDE
  funcionan bajo RLS).

### Criterio de aceptación anti-drift (retirar el riesgo primero)

Antes de escribir el constraint definitivo, un **spike de idempotencia** en el
container: crear la columna + constraint en una DB desechable y correr
`alembic revision --autogenerate` **dos veces** bajo el env real del repo
(`compare_server_default=True`). **El segundo run DEBE producir un diff vacío** —
esa es la verdad de fondo del drift, más fuerte que "el test pasó una vez". Si no
es vacío, iterar la definición del modelo ahí (ciclo de segundos) antes de
construir parser/persister encima.

**Ladder de fallback** (si aun con la columna generada hubiera drift residual por
`compare_server_default`): (1) fijar la expresión del `Computed` al texto que
reporta `pg_get_expr` en PG16; (2) último recurso NO recomendado — `include_object`
para saltar el constraint + aserción estructural dedicada vía `pg_constraint`
(contradice "nunca silenciar la comparación" y debilita el drift-hardening H3;
solo emergencia).

### Límite explícito: gaps NO los cubre este constraint

Un **gap** (una fecha de hecho sin ninguna participación que la cubra) no lo puede
atacar un constraint de DB — no hay ventana de cobertura de referencia a nivel
schema. Los gaps son concern del **domain guard `apply_pct` fail-loud** en Phase 3
(al resolver el pct de un hecho, si no hay fila que cubra la fecha → error, no
default silencioso a 100%). **Se documenta explícito** para que no sea un hueco
silencioso; el `EXCLUDE` cubre overlaps (el vector introducible hoy), el guard de
Phase 3 cubre gaps (que requieren el consumidor `apply_pct`).

---

## 4. Migración, baseline y rollout

- **UNA** baseline amendment (la siguiente tras la #8 de SP2), **regenerada
  canónicamente** con `alembic revision --autogenerate` **dentro del container
  backend** (el host no alcanza el Postgres) — no hand-edit (lección D12 / Phase
  2.9: el camino valida boot/drift/formato/orden topológico).
- 100% aditiva (columns nullable + JSONB con default + extensión + constraint).
  Downgrade reversible (drop constraint → drop extensión → drop columns).
- **Wipe & reload dev** (`down -v`, recarga XMLs por wizard) — política Tier 1,
  baseline mutable pre-deploy.
- **Boot smoke prod-local** OK: `migrate` aplica el baseline amendado, `backend`
  arranca como `app_rls` (`rolsuper=f/rolbypassrls=f`).

---

## 5. Testing (TDD)

| Test | Cubre |
|---|---|
| parser cash | extrae `action_id`/`issuer_country`/`settle_date`/`report_date`/`ex_date` + `raw_attrs` de un fixture real |
| roundtrip fidelidad | XML→DB exacto de los 5 campos (subset/extensión de `test_source_precision_fidelity.py`) |
| `_ensure_instruments` país | puebla `issuer_country` desde creator + anti-churn `DO UPDATE` (no churn sin cambio material) |
| censo IC-2 (non-vacuity) | pinnea qué creators traen `issuerCountryCode` en los 3 fixtures (fail-loud si el censo cambia) |
| `EXCLUDE` overlaps | rechaza vigencia solapada; acepta rangos disjuntos + `valid_to` NULL adyacente |
| drift test | `compare_metadata` verde tras el amendment |

Suite completa verde bajo `app_rls` + FORCE RLS (paralelo `-n auto`). Ruff clean.

---

## 6. No-goals (explícitos)

- **#3 parties fiscal identity** (residencia/tarifa marginal) → domain spec Phase 3.
- **Cómputo de dividendos / WHT / descuento Art. 254** → Phase 5.
- **Decisión fiscal causación-vs-caja** → Phase 5 (aquí se capturan ambas bases).
- **`apply_pct` + gap-guard fail-loud** → Phase 3 domain layer.
- **Capa de config legal** (UVT, tramos Art. 241, umbrales, reajuste) → Phase 3.
- **Cualquier cambio de API o frontend** — esta pasada es capa de datos pura.

---

## 7. Ejecución

Subagent-driven (patrón Tier 1): implementer + spec-review + code-quality-review
por task, review holístico final. Branch `feat/ingest-completeness-hardening` →
PR a `main` → CI verde (`backend` + `frontend`) → merge.
