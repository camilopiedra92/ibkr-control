# Cross-references al proyecto `renta`

Este documento lista los archivos y secciones del proyecto sibling `renta`
(`/Users/owner/Development/renta/`) que `ibkr-control` debe consultar
durante implementación.

**Principio:** `ibkr-control` **reimplementa** su propia lógica (decisión locked
#1 del spec), pero **lee** `renta` como referencia de correctitud. Nunca
importa código Python de renta. Nunca hace `from renta.* import ...`.

Si necesitás verificar que tu implementación produce los mismos números, escribí
un test de paridad que pinte valores y comparalo contra `renta/tests/_invariants.py`.

---

## 1. Reglas fiscales (cuándo: siempre que toques `backend/domain/`)

### Lectura recomendada

| Archivo en renta | Cuándo consultar |
|---|---|
| `/Users/owner/Development/renta/CLAUDE.md` § "Reglas fiscales Colombia aplicadas" | Vista de pájaro de todas las reglas + bugs históricos detectados (lecciones) |
| `/Users/owner/Development/renta/docs/tax_rules_co.md` | Detalle por regla con cifras concretas año por año |
| `/Users/owner/Development/renta/CLAUDE.md` § "Bugs detectados y corregidos" | 20 bugs históricos con causa raíz — evita repetirlos en ibkr-control |

### Mapeo regla → archivo de implementación referencia en renta

| Regla | Implementación referencia en renta |
|---|---|
| Art. 288 ET (TRM compra/venta) | `renta2025.py` buscar `"Art.288"` y `_ib_ingresos_brutos_cop_<titular>`, `_ib_costos_cop_<titular>` |
| Art. 300 ET (730 días → GO) | `renta2025.py` buscar `_clasificar_dias_held` y `Art.300` |
| Art. 254 ET (WHT US descuento) | `renta2025.py` buscar `Art.254` y `_wht_descuento` |
| Decreto 1797/2008 (FUT netting) | `renta2025.py` § A.5 "régimen DUAL"; `CLAUDE.md` § "Derivados financieros" |
| Art. 261-263 (patrimonio IBKR) | `renta2025.py` hoja "Patrimonio" sección IBKR |
| Art. 607 (Form 160) | `renta2025.py` hoja "Formulario 160" |
| Medios Magnéticos | `renta2025.py` test de obligación en hoja RESUMEN |

---

## 2. Lógica FIFO (cuándo: implementando Phase 3 domain layer)

| Archivo en renta | Por qué |
|---|---|
| `/Users/owner/Development/renta/docs/flex_fifo_loader_spec.md` | **Spec completo** del FIFO. Lee este antes de escribir una sola línea de FIFO. Cubre lot book seeding, transfers internos preservando `open_date`, override manual del trader via pool CLOSED_LOT |
| `/Users/owner/Development/renta/documentos/ibkr_flex/loader.py` | Implementación Python de referencia (~600 líneas). Reescribir su lógica en tu propio módulo; NO importar |
| `/Users/owner/Development/renta/documentos/ibkr_flex/_fifo_replay.py` | Lógica de replay FIFO durante backfill |

**Validación de paridad:** después de implementar, correr tu FIFO sobre los mismos
XMLs (`/Users/owner/Development/renta/fuentes/2025/compartidos/ibkr/ACTIVITY_2025.xml`)
y verificar que el conteo de cierres + cost basis matchee. Spec menciona
"58/58 cierres coinciden con pool CLOSED_LOT (0 diffs)" en 2025 — usar como pinned.

---

## 3. Ingest Flex XML (cuándo: Phase 2)

| Archivo en renta | Por qué |
|---|---|
| `/Users/owner/Development/renta/documentos/ibkr_flex/db_ingest.py` | Orquestador de ingest, ~150 líneas |
| `/Users/owner/Development/renta/documentos/ibkr_flex/_ingest_*.py` | 7 sub-módulos (metadata, trading, positions, cash, dividends, nav, perf) — patrón a replicar |
| `/Users/owner/Development/renta/documentos/ibkr_flex/_known_tags.py` | Catálogo exhaustivo de tags del XML (los conocidos + los explícitamente descartados) |
| `/Users/owner/Development/renta/documentos/ibkr_flex/_audit.py` | Audit pre-ingest que aborta si encuentra tag desconocido. **Patrón crítico** — replicar |
| `/Users/owner/Development/renta/db/ibkr_models.py` | Schema SQLite con 33 tablas tipadas. El schema Postgres de ibkr-control simplifica esto pero las tablas core (trades, lots, transfers, cash_tx) deben preservar los campos clave |
| `/Users/owner/Development/renta/documentos/_hash.py` | SHA-256 dedup helper. Reescribir trivialmente |

---

## 4. TRM DIAN (cuándo: Phase 2)

| Archivo en renta | Por qué |
|---|---|
| `/Users/owner/Development/renta/documentos/trm_dian/` | Loader actual. Usa CSV histórico, NO la API Socrata. **En ibkr-control usamos Socrata `ceyp-9c7c`** según decisión locked #8 — no copiar el loader, solo entender qué campos persiste |
| `/Users/owner/Development/renta/db/trm_models.py` | Modelo `trm_dian_day` — buena referencia para nuestro `trm_days` |

---

## 5. Validación de paridad (cuándo: Phase 3+)

| Archivo en renta | Por qué |
|---|---|
| `/Users/owner/Development/renta/tests/_invariants.py` | **Pinned values verificados manualmente**: tolerancias, conteos esperados, WHT pinned contra Activity Statement PDF. Cuando ibkr-control llegue al Reporte Form 210, debe matchear estos números para 2024 y 2025 |
| `/Users/owner/Development/renta/tests/test_cross_validation_exhaustive.py` | 13 audit gaps A-M (cash, dividendos, NAV, transfers, FDIC, etc.) — patrón de tests de consistencia |
| `/Users/owner/Development/renta/tests/test_wht_reconciliation.py` | Cadena WHT verificada Flex → CSV → 1042-S. ibkr-control solo usa Flex (decisión #10), así que para paridad basta el primer eslabón |

### Métrica de éxito de paridad (Phase 5 entregable)

El Reporte Form 210 de ibkr-control para AG 2024 y AG 2025 debe matchear,
dentro de las tolerancias de `_invariants.py`, los valores que produce
`renta2025.py` corriendo con `RENTA_ANYO=2024` y `RENTA_ANYO=2025`.

Tolerancia esperada: **diferencias < $1K COP por redondeo** (criterio §12.3 del spec).

---

## 6. Datos crudos para tests

### XMLs reales (sanitizar account IDs antes de commitear)

```
/Users/owner/Development/renta/fuentes/2024/compartidos/ibkr/ACTIVITY_2024.xml
/Users/owner/Development/renta/fuentes/2024/compartidos/ibkr/TRADE_CONFIRMATION_2024.xml
/Users/owner/Development/renta/fuentes/2025/compartidos/ibkr/ACTIVITY_2025.xml
/Users/owner/Development/renta/fuentes/2025/compartidos/ibkr/TRADE_CONFIRMATION_2025.xml
```

**Sanitización:** reemplazar `U99999001`, `U99999002`, `U99999003` por
`U99999001`, `U99999002`, `U99999003` (o similar). NIT `1234567890` por
`1234567890`. Antes de commitear cualquier XML como fixture de test, hacer
este pass de sanitización.

### TRM histórico

```
/Users/owner/Development/renta/fuentes/referencia/TRM_historico.csv
```

Útil para validar que tu pull de Socrata produce los mismos valores día-por-día.

---

## 7. Anti-patrones (NO hacer)

- ❌ `from renta.* import ...` — son repos independientes; nunca dependencia de código
- ❌ `cp renta/documentos/ibkr_flex/loader.py backend/src/...` — reescribir, no copiar
- ❌ Tocar archivos en `/Users/owner/Development/renta/` desde una sesión Claude en ibkr-control. Si necesitás cambiar algo en renta, abrí una sesión Claude en renta
- ❌ Asumir que renta es la verdad absoluta — si encontrás un bug fiscal en renta, documentarlo en `docs/references/renta-discrepancies.md` y discutirlo con el usuario (NO arreglar renta desde acá)

## 8. Cuándo NO consultar renta

Renta tiene mucho código que NO aplica a IBKR (cédula trabajo, Globant Form 220,
AFC, leasing, SURA medicina, A&V Bursátil BVC, Skandia pensión, etc.).
**Ignorar todo eso**. Si una sección de renta no menciona "IB", "IBKR", "Art.288"
o "Decreto 1797", probablemente no aplica.

Lista de archivos a IGNORAR:
- `inputs/anyos/*.py` campos `afc`, `leasing_bbva`, `sura_medicina`, `skandia_*`, `av_bursatil`, `av_fondos`, `action_black`, `otros_bancos`, `form220`
- `documentos/{form220,form210,bbva_*,sura_medicina,bancolombia_*,skandia_*,action_black,otros_bancos,acciones_valores}/`
- `config/legal_co.py` campos NO IBKR (UVT sí aplica)
- `config/contribuyente.py` (es de Test Owner, ibkr-control usa users multi-tenant)
- `config/cuentas_ib.py` (es de Test Owner, ibkr-control acepta accounts por DB)
