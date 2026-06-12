# Transfer↔Instrument lineage — design

**Fecha:** 2026-06-11
**Estado:** aprobado (brainstorm 2026-06-11)
**Re-abre:** T1-D8 / CR-1 del spec W2 (`docs/specs/2026-06-10-tier1-worldclass-model-design.md`) — ver §SUPERSEDED abajo
**Programa:** mini-PR pre-SP2, bajo la política Tier 1 "baseline mutable hasta el primer deploy" (T1-D14)

## 1. Problema

W2 trazó el split creator/resolver del securities master **por nombre de tag**: Trade/Lot/OpenPosition/accruals = creators, CashTransaction/Transfer = resolvers (lookup, nunca crean). Tres defectos se componen:

1. **El criterio se calibró sobre el sub-caso equivocado.** El rationale "transfers no traen conid" es falso para transfers de securities. Censo contra los 30 `<Transfer>` reales de los 3 fixtures (2024/2025/2026_FOP):

   | Población | conid | isin | symbol |
   |---|---|---|---|
   | STK ×12 (IBIT, BROS, SBET, ZETA, GLOB — INTERNAL y FOP) | **100% presente** | 100% presente | real |
   | CASH ×18 (movimientos internos de plata) | 100% vacío | vacío | `--` |

   `assetCategory != 'CASH'` ⟺ conid presente, **sin excepciones**. El transfer FOP de GLOB trae spec completo de instrumento (conid `160756766` + isin `LU0974299876` + assetCategory STK + symbol + description) — tan rico como cualquier creator. Dos comentarios del código afirman lo contrario (`flex_raw.py` "Los FOP de GLOB (STK) no traen conid", `_models.py` ídem) — heredados del grep inicial que no vio los attrs multi-línea (el refinamiento CR-1 corrigió el dato pero no los comentarios ni la decisión).

2. **El "lookup" del resolver es batch-only, no contra el securities master.** `_resolve_instrument` consulta `instruments_map`, que solo contiene conids aportados por creators *de ese batch*; nunca hace SELECT a la DB. Un dividendo de enero por una posición cerrada en diciembre del año anterior resuelve a NULL aunque el instrument exista en `instruments`.

3. **First-seen congela el NULL.** El XML real 2026 contiene el transfer FOP **sin ningún creator GLOB en el mismo batch** (verificado empíricamente: el conid aparece solo en los 2 `<Transfer>`). Transfers y cash van por `_upsert_immutable` (DO NOTHING): cuando el ingest del día siguiente crea el instrument vía OpenPosition, la fila ya persistida nunca se actualiza. Y como `transfers` no guarda ni `conid` ni `raw_attrs`, el dato se descarta **irrecuperablemente** en parse-time.

**Consecuencia:** el linaje instrumento↔transfer FOP — exactamente el que Phase 3 necesita para el cost basis del RSU de GLOB — quedaba planeado como "join por symbol contra lots": la inferencia vulnerable a ticker-changes que W2 se construyó para eliminar. Misma lección de Phase 2.9: un discriminador debe capturarse de la fuente como hecho de primera clase, no inferirse después por un join opcional.

**Estado resultante es orden-dependiente:** el mismo conjunto de XMLs ingerido en distinto orden produce distinto `instrument_id` final — la antítesis de la convergencia idempotente que Phase 2.5 estableció como estándar.

## 2. Principio rector

Distinguir dos clases de columnas en un hecho ingerido:

- **Columnas de hecho** (montos, fechas, qty — el evento económico): first-seen inmutable, nunca mutan en silencio. Sin cambios.
- **Columnas de resolución** (`instrument_id` — el resultado de resolver una referencia del hecho contra el catálogo control-plane): enriquecimiento derivado. Un NULL congelado porque el catálogo estaba incompleto al first-seen es estado accidental, no semántica. La resolución conid→instrument es **determinística y monótona** (el catálogo solo crece; un conid mapea a exactamente un instrument por `UNIQUE(id_type, id_value)`): NULL→valor es convergencia; valor→valor′ es imposible por construcción.

## 3. Decisiones

| # | Decisión | Resolución |
|---|---|---|
| **TL-D1** | Criterio creator para `<Transfer>` | `assetCategory != 'CASH'` → **creator fail-loud**: `_require_asset_class` + `_require_conid`, igual que los 5 creators existentes. Si IBKR alguna vez emite un transfer de security sin conid → error accionable, no NULL silencioso. CASH → `instrument_id NULL` por diseño (movimiento de plata, no instrumento). El split creator/resolver se traza por **calidad de evidencia**, no por nombre de tag. |
| **TL-D2** | Resolver (cash) DB-wide | `_ensure_instruments` también resuelve (SELECT-only, **nunca crea**) los conids referenciados por cash transactions. Cierra el caso cross-año. El hueco restante (instrument jamás visto por ningún creator en ningún import) queda NULL + warning — edge case de arranque sin histórico, aceptado. |
| **TL-D3** | Convergencia monótona en cash | ON CONFLICT de cash pasa a `DO UPDATE SET instrument_id = EXCLUDED.instrument_id WHERE cash_transactions.instrument_id IS NULL AND EXCLUDED.instrument_id IS NOT NULL`. Columnas de hecho intactas; solo la columna de resolución converge NULL→valor. El guard garantiza cero churn (filas ya resueltas o sin resolución nueva se comportan como DO NOTHING — sin bloat en el re-ingest YTD diario). **Sin entradas en `restatement_log`**: completar un enriquecimiento no es un restatement económico (W3 audita cambios materiales de P&L/qty/cost). **Conteo honesto:** `n_new` debe contar solo INSERTs estrictos, no convergencias — el rowcount de un upsert con DO UPDATE incluye filas actualizadas; la variante distingue insert vs update (`RETURNING (xmax = 0)`) para que `ingest_log` no infle `items_new`. |
| **TL-D4** | Fidelidad de fuente | `transfers.asset_class` NOT NULL (100% presente en data real; espejo de Phase 2.9 en lotes) + `transfers.conid` nullable (NULL solo para CASH) + `cash_transactions.conid` nullable. Simetría con los accruals, que ya conservan el conid crudo. Un `instrument_id NULL` pasa a ser auditable contra la fuente sin re-parsear `xml_bytes`. |
| **TL-D5** | Invariante en SQL | `CHECK ((asset_class = 'CASH') = (instrument_id IS NULL))` en `transfers` (naming convention: `name="transfer_cash_iff_no_instrument"` → `ck_transfers_transfer_cash_iff_no_instrument`). Precedente T1-D2 / exclusive-arc Phase 2.7: los invariantes estructurales viven en la DB, que sobrevive al código — el persister no será para siempre el único writer. |
| **TL-D6** | Rollout | Amendment #6 del baseline pristino `a9977ac077e5` (regenerado canónicamente con autogenerate dentro del container) + wipe & reload dev — política Tier 1 vigente (T1-D14). **El symbol-join planeado para Phase 3 muere**: el linaje FOP↔lots se hace por `instrument_id`. |

## 4. Diseño

### 4.1 Parser (`ingest/flex/parser.py`, `ingest/flex/_models.py`)

`ParsedTransfer` gana 3 campos:

```python
asset_class: str          # _require_asset_class(tr) — fail-loud, 100% presente
isin: str | None          # _instrument_isin(tr)
description: str | None   # _instrument_description(tr)
```

En `_parse_transfers`:

- `asset_class = _require_asset_class(tr)` — siempre.
- `asset_class != "CASH"` → `conid = _require_conid(tr, "<Transfer>")`.
- `asset_class == "CASH"` → `conid = tr.get("conid") or None` (normaliza `""` → None; en data real siempre None).

Corregir los dos comentarios falsos (`_models.py` ParsedTransfer, `flex_raw.py` Transfer.instrument_id) con la evidencia real del censo.

### 4.2 Persister (`ingest/flex/persister.py`)

- **`_collect_instrument_specs`**: suma los transfers con `asset_class != 'CASH'` como creators al merge last-seen-wins existente — aportan `symbol`, `asset_class`, `name` ← `description`, `isin`; `currency`/`multiplier` quedan None (nullable en `instruments`).
- **`_ensure_instruments`**: acepta además el set de conids resolver (de cash) y los incluye en el lookup chunked por identifiers (SELECT-only). La separación creator/resolver se mantiene estricta: **solo specs de creators llegan al INSERT**; un conid resolver sin instrument existente simplemente no aparece en el map.
- **Row builder de transfers**: securities usan `instruments_map[tr.conid]` directo (KeyError = bug del persister → fail loud, igual que trades); CASH → `None`. `_resolve_instrument` (con su warning de conid-sin-instrument) queda solo para cash.
- **Upsert de cash**: variante `_upsert_immutable_with_resolution` que implementa TL-D3. Transfers siguen `_upsert_immutable` puro — como creators nunca necesitan converger.

### 4.3 Schema (modelos + baseline)

Declarado en los modelos (protegido por el drift test):

- `transfers.asset_class` `String NOT NULL`
- `transfers.conid` `String NULL`
- `transfers` CHECK TL-D5
- `cash_transactions.conid` `String NULL`

Baseline: amendment #6 de `a9977ac077e5`, autogenerado canónicamente dentro del container backend, wipe & reload dev por wizard (T1-D14).

### 4.4 Error handling

- Transfer de security sin conid o sin assetCategory → `ValueError` accionable en parse-time (mismo contrato que los 5 creators). El CHECK TL-D5 es la red estructural si un writer futuro viola el invariante por otro camino.
- Conid resolver (cash) sin instrument en master → NULL + `logger.warning` (comportamiento actual, ahora solo para el caso genuinamente irresoluble).

## 5. Testing

- **Flip** de `test_fop_transfer_instrument_id_null_resolver_no_creator` → `test_fop_transfer_creates_instrument_and_resolves`: contra el fixture FOP real, el persist **crea** el instrument GLOB (identifier conid `160756766` + isin) y el transfer `39584831194` queda con `instrument_id` non-NULL.
- Nuevos:
  - CASH transfer → `instrument_id NULL` + no crea instrument.
  - Transfer STK sin conid → `ValueError` fail-loud (parse-time).
  - Resolver cash DB-wide cross-batch: persist #1 con creator (trade), persist #2 con solo cash del mismo conid → resuelve contra el master.
  - Convergencia: persist cash con conid irresoluble (NULL) → persist posterior con el instrument ya creado + la misma fila cash → `instrument_id` rellenado.
  - Guard monótono: fila cash ya resuelta re-ingerida → no se toca (sin churn); 0 filas en `restatement_log` por convergencia.
  - CHECK TL-D5 rechaza violación directa por SQL.
- Replay suite existente (idempotencia × fixtures) corre sin cambios semánticos: segunda pasada sigue `items_processed=0` (el hash fast-path corta antes; y aún forzando re-persist, el guard de TL-D3 no toca filas ya convergidas).

## 6. Docs

- Nota **SUPERSEDED** en el spec W2 (T1-D8 y CR-1): el split creator/resolver por tag fue reemplazado por split por calidad de evidencia; referencia a este spec.
- Update de CLAUDE.md (bullet W2 + sección "⏯ SIGUIENTE").
- Update de la memoria del linaje GLOB (`globant-rsu-cost-basis-phase3`): el linaje FOP↔lots ya no requiere symbol-join.

## 7. SUPERSEDED — qué cambia exactamente de W2

| W2 (T1-D8 / CR-1) | Este spec |
|---|---|
| Transfers = resolvers (lookup por conid si presente, nunca crean); "la regla resolver-never-creates deja `instrument_id` NULL igual" para FOP | Transfers de securities (`assetCategory != 'CASH'`) = **creators fail-loud**; CASH = NULL por diseño (ni resolver: no hay conid) |
| Lookup del resolver = map in-batch de creators | Lookup DB-wide contra `instrument_identifiers` (SELECT-only) |
| `instrument_id` first-seen congelado en hechos inmutables | Columnas de resolución convergen monótonamente NULL→valor (solo cash; transfers resuelven al first-seen por ser creators) |
| Linaje FOP↔lots para Phase 3: "join por symbol" | Linaje por `instrument_id` |

Lo que **no** cambia: cash sigue sin crear instruments jamás; los 5 creators existentes intactos; `symbol`/`asset_class` siguen viviendo en los hechos (T1-D8 segunda mitad); la semántica first-seen de las columnas de hecho intacta.
