# IBKR Control Center — Design Spec

**Fecha:** 2026-05-24
**Autor:** Test Owner
**Status:** Diseño aprobado, pendiente plan de implementación

---

## 1. Visión

Web app personal de Test Owner para visualizar y operar sobre la
información de Interactive Brokers con foco en decisiones de inversión
e impuestos (subset IBKR de la declaración de renta colombiana).

Reemplaza la necesidad de abrir hojas Excel + scripts Python + portales
de IBKR para responder preguntas como:

- ¿Qué lotes tengo abiertos y cuántos días llevan?
- ¿Cuánto falta para que un lote cruce a Ganancia Ocasional (730d)?
- Si vendo este lote hoy, ¿cuánto impuesto pago?
- ¿Cuáles son los dividendos del año y el WHT pagado?
- ¿Qué números van en mi Form 210 cas.74/76/77/78 por IBKR?

**No reemplaza** el proyecto `renta` actual (que sigue siendo source of
truth de la declaración completa, incluyendo Globant, AFC, leasing,
etc.). Esta app es el **centro de control IBKR-only**.

---

## 2. Decisiones arquitectónicas (locked)

| # | Decisión | Valor |
|---|---|---|
| 1 | Repo y relación con `renta` | Repo nuevo independiente, re-ingiere el Flex XML por su cuenta |
| 2 | Hosting | Coolify self-hosted en Hetzner (Docker) |
| 3 | Stack backend | Python 3.12 + FastAPI + SQLAlchemy + Alembic + APScheduler |
| 4 | Stack frontend | Next.js 14 (App Router) + TypeScript + TanStack Query + Tremor/shadcn |
| 5 | DB | Postgres 16 (un contenedor en docker-compose) |
| 6 | Auth | `fastapi-users` con JWT (single-user V1, schema multi-user-ready) |
| 7 | Ingest IBKR | Flex Web Service auto-fetch del año actual (YTD) + upload manual de XMLs históricos |
| 8 | Ingest TRM | Socrata API DIAN dataset `ceyp-9c7c`, automático, sin uploads |
| 9 | Precios actuales | yfinance con cache TTL 15min in-memory |
| 10 | Docs IBKR en V1 | Solo Flex XML (sin CSV de dividends ni 1042-S; dif fiscal <$10K COP/año) |
| 11 | Tarifa marginal RO | Configurable por usuario en settings, default 0.39 |
| 12 | Arquitectura | Monolito modular (Opción A): API + scheduler + ingest en un solo proceso |

---

## 3. Topología de despliegue

```
docker-compose.yml (Coolify)
├── backend           Python 3.12 / FastAPI / APScheduler / uvicorn
├── frontend          Next.js 14 production build
└── postgres          Postgres 16 con volume persistente
```

Coolify maneja:
- Reverse proxy + SSL (Caddy/Traefik con Let's Encrypt)
- Secrets (Flex Token, JWT secret, DB password)
- Auto-deploy on git push
- Backups del volume Postgres

---

## 4. Features V1

### 4.1 Pantallas (10 total)

```
┌──────────────────┬──────────────────────────────────────────────┐
│  📊 Dashboard    │  Header global: 'Última act: hace 6h · 🔄'   │
│  📦 Lotes        │                                                │
│      abiertos    │  (contenido por pantalla, ver §4.2)           │
│  ✓  Cerrados    │                                                │
│  ⚠  Alertas      │                                                │
│      730d        │                                                │
│  🧮 Simulador    │                                                │
│  💰 Dividendos   │                                                │
│  🏦 Patrimonio   │                                                │
│  🌍 Form 160     │                                                │
│  📋 Reporte      │                                                │
│      Form 210    │                                                │
│  ⚙  Settings    │                                                │
└──────────────────┴──────────────────────────────────────────────┘
```

Layout: sidebar fija a la izquierda + content principal. Header global
en todas las pantallas con timestamp de última actualización + botón
"Actualizar ahora".

### 4.2 Detalle por pantalla

**Dashboard** — Tarjetas resumen: total lotes abiertos, valor mercado
USD/COP, P&L unrealized COP del año, total dividendos YTD, cuántos
lotes cruzan GO en próximos 90 días, banner discreto "Form 160:
obligado (margen $X COP)" si aplica. **Tarjeta de composición**: donut
chart por símbolo (top 10 + "otros") y treemap mini por cuenta. Datos
de `open_position_lots.mark_value_usd` agrupados.

**Lotes abiertos** — Tabla central. Columnas: Símbolo, Cuenta (con %
participación), Open Date, Días tenidos, → 730d (semáforo), Qty, Costo
USD/COP, Mark USD, P&L COP, Régimen (STK/FUT), 🧮. Filtros: cuenta,
régimen, búsqueda símbolo. Sort default: → 730d ascendente. Background
de fila resalta urgencia (rojo si ya ≥730d, amarillo si ≤90d). Hover en
columnas COP muestra TRM usada y fecha. P&L USD oculto por default,
toggle visible.

**Cerrados** — Tabla similar con columnas adicionales `close_date`,
`proceeds_usd`, `pnl_cop_realizado`, `clasificación_final` (RO/GO).
Filtros: año + cuenta + régimen. Footer con totales cas.74 / cas.77 /
GO del año filtrado.

**Alertas 730d** — Subset filtrado de lotes abiertos donde
`días_hasta_730 ≤ 90`. Ordenado por urgencia ascendente. Banner arriba
con conteo agregado.

**Simulador** — Pantalla única con dos modos según el `regime` del
lote, despachado automáticamente al abrir:

- *Modo STK* (regime `STK_ART288`): layout split. Inputs: lote
  pre-llenado, cantidad (presets 25/50/100%), precio USD (con botón
  "⟳ yfinance"), fecha hipotética. Output: badge gigante de
  clasificación (GO 15% o RO X%), tabla con cadena USD → COP → P&L →
  impuesto, comparación contra-fáctica ("si hubieses vendido hace N
  días").
- *Modo FUT* (regime `FUT_DEC1797`): inputs similares (sin fecha
  hipotética, FUT se cierra hoy o no se cierra). Output adicional al
  P&L del cierre: P&L acumulado YTD del mismo símbolo, neto resultante
  con este cierre hipotético, impacto en cas.74 vs cas.77 según postura
  permisiva del usuario, precio en que el neto YTD cruza el cero.

**Dividendos** — Tabla por año con `payment_date`, `symbol`, `gross_usd`,
`wht_usd`, `gross_cop`, `wht_cop`, `art254_descuento_estimado`. Aplica
% participación por cuenta. Total agregado anual. Solo entradas que
pasan `classify_cash_tx_type() == 'dividend'` (Return of Capital y
Special Distribution excluidos automáticamente).

**Patrimonio** — Vista del valor patrimonial al 31-dic por año (Art.261-263
ET). Tres sub-vistas:

- *Por cuenta* — tabla con cada IBKR account: mark_value_usd,
  TRM(31-dic), mark_value_cop, pct_participación, valor_patrimonial_cop.
  Total al pie.
- *Por símbolo* — desglose granular para audit (qty, mark_price,
  mark_value, cuenta).
- *Histórico multi-año* — line chart "Patrimonio IBKR 31-dic por año"
  (ej. 2024: $97M, 2025: $129M, 2026: …). Permite visualizar el
  crecimiento del net worth IBKR.

**Form 160** — Pantalla dedicada a la obligación Art.607 ET (activos en
el exterior > 2000 UVT al 1-ene del año siguiente). Contenido:

- *Status del año actual* — banner verde/rojo: "Obligado (margen
  $5,234K COP arriba del umbral)" o "No obligado (margen $2,100K COP
  debajo)".
- *Listado de activos a reportar* — tabla con cada posición + cripto
  (si se modela en V2) + saldo cash. Columnas: tipo, descripción,
  cuenta, valor_cop. Total y comparación con umbral.
- *Histórico* — tabla por año: monto total, umbral UVT, obligado sí/no.

**Reporte Form 210** — Vista "para copiar a tu declaración". Dos modos
de visualización con toggle:

- *Modo single year* (default) — un año seleccionado, con 3 secciones:
  1. **Cédula no laborales IBKR** — cas.74/76/77/78 desagregados,
     régimen DUAL aplicado (STK Art.288 + FUT netting Decreto 1797/2008)
  2. **Dividendos + descuento Art.254** — agregado anual con WHT por
     cuenta × pct participación
  3. **Status Medios Magnéticos** (Res. 162/2023 + Res. 227/2025):
     contribución IBKR a brutos cas.74, indicador "requiere completar
     con otros ingresos para evaluar obligación final" con input
     opcional para evaluar localmente
- *Modo multi-year* — tabla pivot con años en columnas y métricas en
  filas: cas.74, cas.76, cas.77, cas.78, dividendos_cop,
  wht_descuento_art254, patrimonio_31dic, form160_obligado_si_no.
  Permite ver tendencias e identificar outliers.

(Patrimonio y Form 160 tienen pantallas dedicadas — el Reporte solo los
referencia para que el usuario sepa dónde encontrarlos.)

Botón "Exportar JSON" para consumo del proyecto `renta` (exporta el modo
seleccionado).

**Settings** — Form con `marginal_rate` (default 0.39), `timezone`
(default `America/Bogota`), botón "Rotar Flex Token", botón
"Re-ingerir todo (forzar)", sección "Importar XML histórico" con
drag&drop, log de últimos 10 runs de cron (status, timestamp, errores).

---

## 5. Modelo de datos (Postgres)

### 5.1 Identidad y configuración

```sql
users (
  id BIGINT PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL
)

user_settings (
  user_id BIGINT PRIMARY KEY REFERENCES users(id),
  marginal_rate NUMERIC(5,4) NOT NULL DEFAULT 0.39,
  timezone TEXT NOT NULL DEFAULT 'America/Bogota',
  updated_at TIMESTAMPTZ NOT NULL
)

accounts (
  id BIGINT PRIMARY KEY,
  ibkr_account_id TEXT UNIQUE NOT NULL,    -- 'U99999001' etc.
  alias TEXT,                              -- 'Conjunta Joint Holder', 'Personal Swing', ...
  currency TEXT NOT NULL DEFAULT 'USD',
  created_at TIMESTAMPTZ NOT NULL
)

participations (
  user_id BIGINT REFERENCES users(id),
  account_id BIGINT REFERENCES accounts(id),
  pct NUMERIC(5,4) NOT NULL,               -- 0.0000 .. 1.0000
  valid_from DATE NOT NULL,
  valid_to DATE,                           -- NULL = vigente
  PRIMARY KEY (user_id, account_id, valid_from)
)

flex_credentials (
  user_id BIGINT PRIMARY KEY REFERENCES users(id),
  token_encrypted BYTEA NOT NULL,          -- AES-GCM con key del env
  ytd_query_id TEXT NOT NULL,              -- query 'Year to Date' del año actual
  last_rotated_at TIMESTAMPTZ NOT NULL
)
```

### 5.2 Datos crudos del Flex XML

```sql
flex_imports (
  id BIGINT PRIMARY KEY,
  user_id BIGINT REFERENCES users(id),
  anyo INT NOT NULL,
  xml_hash TEXT UNIQUE NOT NULL,           -- SHA-256
  xml_size_bytes INT NOT NULL,
  source TEXT NOT NULL,                    -- 'web_service' | 'manual_upload'
  period_covered_from DATE NOT NULL,       -- parseado del header XML
  period_covered_to DATE NOT NULL,
  year_status TEXT NOT NULL DEFAULT 'rolling',  -- 'rolling' | 'sealed'
  fetched_at TIMESTAMPTZ NOT NULL,
  n_trades INT, n_lots_closed INT, n_open_lots INT,
  n_cash_tx INT, n_dividends INT, n_transfers INT,
  status TEXT NOT NULL                     -- 'ok' | 'failed'
)

trades (
  id BIGINT PRIMARY KEY,
  flex_import_id BIGINT REFERENCES flex_imports(id),
  transaction_id TEXT UNIQUE NOT NULL,     -- asignado por IBKR
  account_id BIGINT REFERENCES accounts(id),
  symbol TEXT NOT NULL,
  asset_class TEXT NOT NULL,               -- 'STK' | 'ETF' | 'FUT' | 'OPT' | 'CASH'
  trade_date DATE NOT NULL,
  settle_date DATE,
  qty NUMERIC(20,6) NOT NULL,              -- positivo BUY, negativo SELL
  price_usd NUMERIC(20,6) NOT NULL,
  proceeds_usd NUMERIC(20,2) NOT NULL,
  commission_usd NUMERIC(20,4) NOT NULL,
  open_close TEXT,                         -- 'O' | 'C'
  buy_sell TEXT NOT NULL,                  -- 'BUY' | 'SELL'
  raw_attrs JSONB                          -- atributos del XML no tipados
)

closed_lots (
  id BIGINT PRIMARY KEY,
  flex_import_id BIGINT REFERENCES flex_imports(id),
  account_id BIGINT REFERENCES accounts(id),
  symbol TEXT NOT NULL,
  open_date DATE NOT NULL,
  close_date DATE NOT NULL,
  qty NUMERIC(20,6) NOT NULL,
  cost_basis_usd NUMERIC(20,2) NOT NULL,
  proceeds_usd NUMERIC(20,2) NOT NULL,
  fifo_pnl_usd NUMERIC(20,2) NOT NULL,
  source_trade_id BIGINT REFERENCES trades(id)
)

open_position_lots (
  id BIGINT PRIMARY KEY,
  flex_import_id BIGINT REFERENCES flex_imports(id),
  account_id BIGINT REFERENCES accounts(id),
  symbol TEXT NOT NULL,
  open_date DATE NOT NULL,
  qty NUMERIC(20,6) NOT NULL,
  cost_basis_usd NUMERIC(20,2) NOT NULL,
  mark_price_usd NUMERIC(20,6),
  mark_value_usd NUMERIC(20,2),
  snapshot_date DATE NOT NULL
)

transfers (
  id BIGINT PRIMARY KEY,
  flex_import_id BIGINT REFERENCES flex_imports(id),
  transfer_date DATE NOT NULL,
  direction TEXT NOT NULL,                 -- 'IN' | 'OUT'
  src_account_id BIGINT,
  dst_account_id BIGINT,
  symbol TEXT NOT NULL,
  qty NUMERIC(20,6) NOT NULL,
  transfer_type TEXT NOT NULL
)

transfer_lots (
  id BIGINT PRIMARY KEY,
  transfer_id BIGINT REFERENCES transfers(id),
  original_open_date DATE NOT NULL,
  qty NUMERIC(20,6) NOT NULL,
  cost_basis_usd NUMERIC(20,2) NOT NULL
)

cash_transactions (
  id BIGINT PRIMARY KEY,
  flex_import_id BIGINT REFERENCES flex_imports(id),
  account_id BIGINT REFERENCES accounts(id),
  type TEXT NOT NULL,                      -- 'Dividends' | 'Withholding Tax' | 'Interest' | ...
  currency TEXT NOT NULL,
  amount_usd NUMERIC(20,2) NOT NULL,
  description TEXT,
  date DATE NOT NULL,
  symbol TEXT                              -- dividendo: ticker; otros: NULL
)
```

### 5.3 TRM (DIAN Socrata `ceyp-9c7c`)

```sql
trm_days (
  date DATE PRIMARY KEY,
  value_cop NUMERIC(12,4) NOT NULL,
  vigencia_desde DATE NOT NULL,            -- fila original del API (audit)
  vigencia_hasta DATE NOT NULL,
  source TEXT DEFAULT 'dian_socrata_ceyp_9c7c',
  fetched_at TIMESTAMPTZ NOT NULL
)

CREATE INDEX trm_days_date_idx ON trm_days(date);

trm_imports (
  id BIGINT PRIMARY KEY,
  date_range_from DATE NOT NULL,
  date_range_to DATE NOT NULL,
  n_rows_api INT NOT NULL,
  n_days_expanded INT NOT NULL,
  fetched_at TIMESTAMPTZ NOT NULL
)
```

### 5.4 Datos derivados

```sql
-- TABLE para campos derivados del XML (no cambian sin ingest)
lot_classifications (
  id BIGINT PRIMARY KEY,
  lot_id BIGINT NOT NULL,
  lot_kind TEXT NOT NULL,                  -- 'open' | 'closed'
  user_id BIGINT REFERENCES users(id),
  account_id BIGINT REFERENCES accounts(id),
  regime TEXT NOT NULL,                    -- 'STK_ART288' | 'FUT_DEC1797'
  cost_basis_cop NUMERIC(20,2) NOT NULL,
  proceeds_cop NUMERIC(20,2),
  pnl_cop NUMERIC(20,2),
  trm_open NUMERIC(12,4) NOT NULL,
  trm_close NUMERIC(12,4),
  trm_used_at_open_fallback BOOLEAN NOT NULL DEFAULT FALSE,
  computed_at TIMESTAMPTZ NOT NULL,
  source_flex_import_id BIGINT REFERENCES flex_imports(id),
  UNIQUE (lot_id, lot_kind, source_flex_import_id)
)

-- VIEW para campos que dependen de NOW() (siempre fresh sin job)
CREATE VIEW lot_status_v AS
SELECT
  lc.*,
  CASE
    WHEN lc.lot_kind = 'open'
      THEN EXTRACT(DAY FROM CURRENT_DATE - opl.open_date)::INT
    ELSE EXTRACT(DAY FROM cl.close_date - cl.open_date)::INT
  END AS days_held,
  CASE
    WHEN lc.lot_kind = 'closed'
      THEN CASE WHEN (EXTRACT(DAY FROM cl.close_date - cl.open_date)::INT >= 730)
                THEN 'GO' ELSE 'RO' END
    WHEN lc.regime = 'FUT_DEC1797'
      THEN 'PENDING_FUT'
    WHEN EXTRACT(DAY FROM CURRENT_DATE - opl.open_date)::INT >= 730
      THEN 'GO'
    ELSE 'RO'
  END AS classification,
  CASE
    WHEN lc.lot_kind = 'open' AND lc.regime = 'STK_ART288'
      THEN GREATEST(0, 730 - EXTRACT(DAY FROM CURRENT_DATE - opl.open_date)::INT)
    ELSE NULL
  END AS days_until_go
FROM lot_classifications lc
LEFT JOIN open_position_lots opl
  ON lc.lot_kind = 'open' AND lc.lot_id = opl.id
LEFT JOIN closed_lots cl
  ON lc.lot_kind = 'closed' AND lc.lot_id = cl.id;
```

### 5.5 Observabilidad

```sql
ingest_log (
  id BIGINT PRIMARY KEY,
  job_kind TEXT NOT NULL,                  -- 'flex' | 'trm' | 'manual_refresh' | 'manual_upload'
  user_id BIGINT REFERENCES users(id),
  started_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ,
  status TEXT NOT NULL,                    -- 'running' | 'ok' | 'failed'
  items_processed INT,
  error_message TEXT,
  trigger TEXT NOT NULL                    -- 'cron' | 'manual'
)
```

---

## 6. Domain layer

Módulos puros (sin DB ni HTTP), encapsulan toda la lógica fiscal:

```
backend/domain/
├── fifo.py             Lot book: open + close + transfer (port del proyecto renta)
├── trm_lookup.py       get_trm(date) → Decimal, fallback via vigencia DB
├── regime.py           classify_asset(asset_class, symbol) → 'STK_ART288' | 'FUT_DEC1797'
├── classification.py   compute_lot_status(lot) → ClassificationResult
├── simulator.py        simulate_sale_stk() y simulate_sale_fut()
├── dividends.py        per_year_dividends() con Art.254 descuento
├── participation.py    apply_pct(amount, user_id, account_id, at_date)
└── fiscal_report.py    form210_summary(user_id, year) → Form210Summary
```

### 6.1 Reglas fiscales codificadas

Cobertura exhaustiva de toda regla CO con relación a operaciones IBKR
(aunque no todas se rendericen en UI V1). El domain layer expone
funciones que materializan cada una; consumidores deciden qué mostrar.

#### A. Base normativa (sustento conceptual)

| Regla | Implementación | Cita |
|---|---|---|
| Residente fiscal Colombia | Asunción del modelo (un usuario = un residente) | Art. 9 + Art. 10 ET |
| Renta de fuente extranjera | Todo IBKR califica; sustento del Art.254 | Art. 21-1 ET |
| Base normativa netting derivados | Postura permisiva del FUT netting | Art. 26 + Art. 33 + Art. 330 ET |

#### B. Costo fiscal y P&L

| Regla | Implementación | Cita |
|---|---|---|
| FIFO con override manual | Pool `closed_lots` del XML es autoritativo para `open_date` | `flex_fifo_loader_spec.md` |
| Costo fiscal = adquisición + comisiones capitalizadas | `cost_basis_usd` ya incluye comm capitalizada en buy | Art. 69 ET |
| Utilidad enajenación = precio venta − costo fiscal | `pnl_usd = proceeds_usd − cost_basis_usd` | Art. 71 + Art. 90 ET |
| Costo a TRM compra | `cost_basis_cop = cost_basis_usd × TRM(open_date)` | Art. 288 ET |
| Proceeds a TRM venta | `proceeds_cop = proceeds_usd × TRM(close_date)` | Art. 288 ET |

#### C. Clasificación y tarifa

| Regla | Implementación | Cita |
|---|---|---|
| 730 días → GO 15% | `classification = 'GO' if days_held >= 730 else 'RO'` | Art. 300 ET |
| Tarifa GO | flat 15% | Art. 313 ET |
| Tarifa RO marginal | `user_settings.marginal_rate` (default 0.39) | Art. 241 ET |
| Régimen DUAL FUT/OPT | per-contrato (group by account+symbol) netting | Decreto 1797/2008 Art. 8 + DUR 1625/2016 Art. 1.2.4.2.74 |

#### D. Dividendos y descuentos

| Regla | Implementación | Cita |
|---|---|---|
| WHT US dividendos | descuento = WHT × pct_participación | Art. 254 ET |
| Distinguir Return of Capital de Dividends | `classify_cash_tx_type()` filtra `type IN ('Return of Capital','Special Distribution')` para que NO cuenten como ingreso fiscal y, en V2, reduzcan cost basis del lote del símbolo | Art. 46-1 ET |

#### E. Patrimonio y obligaciones paralelas

| Regla | Implementación | Cita |
|---|---|---|
| Patrimonio bruto IBKR al 31-dic | `compute_patrimonio_ibkr(user_id, anyo)` → `Σ mark_value_usd × TRM(31-dic) × pct` por cuenta y total | Art. 261 + Art. 262 + Art. 263 ET |
| Form 160 — activos exterior > 2000 UVT | `compute_form160_threshold(user_id, anyo)` → `(obligado: bool, total_cop, umbral_cop, margen, listado_activos)` | Art. 607 ET |
| Medios Magnéticos — umbral 11800 UVT brutos | `compute_medios_magneticos_obligation(user_id, anyo)` → contribución IBKR a brutos cas.74 + chequeo conjuntivo con K+NL > 2400 UVT (otros ingresos vienen del consumidor del API, ej. proyecto `renta`) | Res. 162/2023 num.4 (AG 2024) · Res. 227/2025 num.4 (AG 2025) |

#### F. Infraestructura

| Regla | Implementación | Cita |
|---|---|---|
| TRM fallback fin de semana / festivo | resuelto en DB via expansión de rangos `vigencia_desde..vigencia_hasta` | regla operativa DIAN |

### 6.1.1 Funciones del domain layer (resumen)

```python
# Clasificación y simulación
compute_lot_status(lot) → ClassificationResult
simulate_sale_stk(lot, qty, price, date, settings) → SimulatorResult
simulate_sale_fut(lot, qty, price, ytd_closures, settings) → SimulatorResult

# Cash transactions
classify_cash_tx_type(tx) → 'dividend' | 'wht' | 'return_of_capital' | 'interest' | 'fee' | 'other'
per_year_dividends(user_id, year) → DividendReport  # solo type=='dividend'

# Patrimonio y obligaciones paralelas
compute_patrimonio_ibkr(user_id, anyo) → PatrimonioReport
compute_form160_threshold(user_id, anyo, uvt_value) → Form160Report
compute_medios_magneticos_obligation(
    user_id, anyo, uvt_value, otros_brutos_cop, otros_k_nl_cop
) → MMReport

# Reporte agregado anual
form210_summary(user_id, year) → Form210Summary
  # Contiene: cas.74/76/77/78 desagregadas + patrimonio + Form 160 status
  #          + MM status (con campo "requiere completar con otros ingresos")
```

### 6.1.2 Reglas NO aplicables a IBKR (explicitas)

Para evitar confusión futura, queda registro de qué reglas del Estatuto
NO se aplican a operaciones IBKR (aunque sí se aplican a otros tipos de
activos del proyecto `renta` general):

- **Art. 36-1 ET** (acciones BVC <3% emisor → INCR): solo BVC y MGC; IBKR offshore no califica
- **Art. 153 ET** (pérdidas BVC no deducibles): mismo motivo
- **Art. 242 ET + Art. 254-1 ET** (dividendos sociedades nacionales, descuento >1090 UVT): IBKR son dividendos extranjeros
- **Art. 38-40 ET** (componente inflacionario): solo títulos COP de Colombia
- **Art. 115 ET** (GMF deducible): N/A IBKR (no hay 4×1000 en operaciones US)
- **Conceptos DIAN 008706/2025 + 003517/2025** (ETFs MGC): IBKR offshore no es MGC
- **Art. 408 ET** (retención en la fuente): IBKR no es agente retenedor colombiano

### 6.2 Fuera de scope (queda en proyecto `renta`)

- Tope 1340 UVT Art. 336 (recorte de deducciones+exenciones cédula trabajo)
- Anticipo Art. 807 (75% impuesto neto)
- Comparación patrimonial Art. 236-239 ET
- Renta exenta 25% Art. 206 num.10
- Cédula trabajo completa (Globant Form 220, AFC, leasing, SURA)

Esta app produce **solo el subset IBKR** del Form 210 (cas.74/76/77/78 +
descuento Art.254 + patrimonio bruto Art.261-263 + listado y status
Form 160 Art.607 + status Medios Magnéticos). El **export oficial del
Form 160 en XML DIAN** queda fuera de V1 (los DATOS y el listado de
activos sí están en la pantalla Form 160 V1; lo que queda V2 es el
serializer al schema XML oficial DIAN).

---

## 7. Flujos

### 7.1 Setup wizard (una vez)

```
1. Crear usuario (email + password)
2. Pegar Flex Token + ytd_query_id del año actual
3. Configurar participaciones por cuenta
4. (Opcional) Drag&drop de XMLs históricos
5. Click "Iniciar carga"

Background:
  a. Backfill TRM completo (1991-hoy) desde Socrata
  b. Fetch YTD via Web Service del año actual
  c. Ingest cada XML histórico subido
  d. Recompute lot_classifications para todos los años
  e. Progress visible en UI (polling cada 2s)
```

### 7.2 Cron diario Flex (07:00 COT)

```
APScheduler trigger → for each user:
  1. POST SendRequest(token, ytd_query_id) → reference_code
  2. Poll GetStatement con backoff 1s, 2s, 4s, 8s, 16s (max 5 min)
  3. Recibido XML → hash SHA-256
  4. Si hash existe en flex_imports → skip + log "sin cambios"
  5. Si nuevo → parse + insert dentro de TX Postgres
  6. Recompute lot_classifications del año actual
  7. INSERT ingest_log SUCCESS
  Si error → INSERT ingest_log FAILED con stack trace, retry next day
```

### 7.3 Cron diario TRM (19:30 COT)

```
APScheduler trigger →
  1. SELECT MAX(vigencia_desde) FROM trm_days → last_known
  2. GET /resource/ceyp-9c7c.json?$where=vigenciadesde > '{last_known}'
                                 &$order=vigenciadesde ASC
                                 &$limit=50000
  3. Si no hay filas → log "TRM up to date", exit
  4. Por cada fila → expandir vigenciadesde..vigenciahasta en N días
  5. INSERT ... ON CONFLICT (date) DO UPDATE SET value_cop = EXCLUDED.value_cop
  6. INSERT trm_imports + ingest_log SUCCESS
```

### 7.4 Sealing de año cerrado (event-driven, no time-driven)

No hay cron especial para el 31-dic. El sealing es derivado del
contenido del XML, robusto ante caídas del server:

```
Dentro del flujo de ingest (§7.2 paso 5), después de parsear el XML:
  Si flex_imports.period_covered_to >= DATE('YYYY-12-31') de su anyo:
    flex_imports.year_status = 'sealed'
  Caso contrario:
    flex_imports.year_status = 'rolling'

Cron diario YTD continúa corriendo después del sealing.
Si un YTD post-sealing trae datos nuevos (correcciones tardías de IB)
se ingiere normalmente (hash distinto) y reemplaza la versión sellada.
```

Beneficio: si el server cae el 31-dic, al volver el primer ingest YTD
del año nuevo NO sella el año pasado (el período `to` será del año
nuevo). El sealing del año pasado se hace cuando manualmente se sube su
XML completo o cuando el último YTD del año aún en DB tenía `to >= Dec 31`.

### 7.5 Botón "Actualizar ahora"

```
POST /api/ingest/trigger
  - Rate limit: 1 invocación / 5 min / user
  - Mismo job code que cron 07:00, ejecutado en background task
  - Devuelve job_id para tracking
  - UI: botón → spinner "Actualizando…" → banner con resultado
GET /api/ingest/status/{job_id} → polling cada 2s hasta status != 'running'
```

### 7.6 Upload manual de XML histórico

```
POST /api/imports/upload  (multipart/form-data)
  1. Save XML temporal
  2. Parse header → derivar period_covered_from/to → anyo
  3. Hash SHA-256
  4. Si hash ya existe → 409 Conflict con flex_import_id existente
  5. Si nuevo → ingest dentro de TX (mismo código del cron)
  6. Marcar source='manual_upload'
  7. year_status derivado igual que §7.4:
       'sealed' si period_covered_to >= Dec 31 del anyo
       'rolling' si parcial
  8. Recompute lot_classifications del año
  9. Return summary
```

### 7.7 Simulador (request síncrono)

```
POST /api/simulator/sell
Body: { lot_id, lot_kind: 'open'|'closed', qty, price_usd, sale_date }

Backend:
  1. Load lot del DB con check user_id
  2. Validar qty (warn si excede)
  3. trm_sale = trm_lookup.get(sale_date)
  4. Si regime = STK_ART288 → STK logic (clasificación 730d, comparación contra-fáctica)
  5. Si regime = FUT_DEC1797 → cargar YTD closures same symbol → compute neto
                                + projection con este cierre hipotético
  6. Apply marginal_rate del user_settings para RO
  7. Return SimulatorResult JSON
```

---

## 8. Testing

```
backend/tests/
├── unit/
│   ├── test_fifo.py              FIFO con transfers, casos edge
│   ├── test_classification.py    Boundary 729/730/731 días
│   ├── test_regime.py            STK vs FUT dispatch
│   ├── test_simulator_stk.py     Caminos STK con GO y RO
│   ├── test_simulator_fut.py     Neteo YTD por símbolo, swing en cas.74/77
│   ├── test_trm_lookup.py        Fines de semana, festivos, fechas faltantes
│   ├── test_participation.py     100%, 50%, valid_from/to
│   └── test_fiscal_report.py     form210_summary snapshot vs pinned values
├── integration/
│   ├── test_flex_parser.py       Parse de XMLs reales (fixtures de fuentes/, sanitizados)
│   ├── test_trm_client.py        Mock Socrata, paginación + expansión rangos
│   ├── test_flex_client.py       Mock IBKR Flex WS SendRequest + Poll
│   └── test_recompute.py         Re-ingest mismo XML = dedup por hash
├── api/
│   ├── test_lots_endpoints.py    GET con filtros, auth, multi-tenant
│   ├── test_simulator_api.py     Full path FE → BE
│   ├── test_setup_wizard.py      Bootstrap completo
│   └── test_manual_refresh.py    Rate limit + concurrent calls
└── conftest.py                   Postgres testcontainers + seed fixtures
```

**Decisiones de testing:**

- **Fixtures con XMLs reales** copiados de `fuentes/2024/` y `2025/`,
  sanitizando account IDs reales a placeholders.
- **Postgres testcontainers** en vez de SQLite mock (modelo usa JSONB,
  ON CONFLICT, expansión de rangos).
- **Sin mock de yfinance** en unit tests (simulador recibe `price_usd`
  directo). Integración yfinance se testea con VCR cassettes.
- **Snapshot tests** del Reporte Form 210 contra pinned values del
  proyecto `renta` (paridad numérica entre ambos proyectos para
  años 2024 y 2025).
- **Cobertura objetivo**: domain 95%+, API endpoints 80%+, ingest 70%.

---

## 9. Estructura de repo

```
ibkr-control/
├── backend/
│   ├── pyproject.toml
│   ├── alembic/                  Migraciones Postgres
│   ├── src/ibkr_control/
│   │   ├── api/                  Routers FastAPI
│   │   │   ├── auth.py
│   │   │   ├── lots.py
│   │   │   ├── simulator.py
│   │   │   ├── dividends.py
│   │   │   ├── patrimonio.py     GET /patrimonio/{year} y multi-year
│   │   │   ├── form160.py        GET /form160/{year} + histórico
│   │   │   ├── reports.py        Form 210 single + multi-year
│   │   │   ├── trm.py
│   │   │   ├── imports.py        upload + status manual
│   │   │   └── settings.py
│   │   ├── ingest/
│   │   │   ├── flex_client.py    IBKR Flex Web Service
│   │   │   ├── flex_parser.py    XML → DB (port del proyecto renta)
│   │   │   ├── trm_client.py     Socrata API DIAN
│   │   │   └── hash_dedup.py
│   │   ├── domain/               Lógica fiscal pura
│   │   ├── prices/               yfinance + cache
│   │   ├── scheduler/
│   │   │   └── jobs.py           3 jobs APScheduler
│   │   ├── auth/                 fastapi-users JWT
│   │   ├── db/                   SQLAlchemy models + repository
│   │   └── main.py               FastAPI app factory
│   └── tests/
├── frontend/
│   ├── package.json
│   ├── next.config.js
│   ├── src/
│   │   ├── app/                  App Router pages
│   │   │   ├── (auth)/login/
│   │   │   ├── (app)/dashboard/
│   │   │   ├── (app)/lots/
│   │   │   ├── (app)/closed/
│   │   │   ├── (app)/alerts/
│   │   │   ├── (app)/simulator/
│   │   │   ├── (app)/dividends/
│   │   │   ├── (app)/patrimonio/
│   │   │   ├── (app)/form160/
│   │   │   ├── (app)/report/
│   │   │   ├── (app)/settings/
│   │   │   └── (setup)/wizard/
│   │   ├── components/           shadcn/ui + Tremor charts
│   │   ├── lib/                  api client generado de openapi.json
│   │   └── hooks/                useLots, useSimulator, etc.
│   └── tests/
├── docker-compose.yml            backend + frontend + postgres
├── Dockerfile.backend
├── Dockerfile.frontend
├── coolify.json                  Config Coolify
└── README.md
```

---

## 10. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| IBKR cambia formato del XML | Parser tolera nuevos atributos vía `raw_attrs JSONB`. Test fixtures pinned con XMLs reales detectan regresiones. |
| DIAN cambia dataset Socrata `ceyp-9c7c` | Encapsulado en `trm_client.py`. Fallback manual: importar CSV histórico (queda como TODO V2 si pasa). |
| yfinance rompe (scraping inestable) | Cache TTL + degrada simulador a "input manual de precio" sin bloquear el resto del app. |
| Flex Token filtrado | Token cifrado en DB con AES-GCM (key del env). Coolify env vars cifradas. Token es read-only en IBKR (no permite trades). |
| Postgres backup | Coolify backups automáticos del volume + manual `pg_dump` mensual a Backblaze B2 (config futuro). |
| App caída → ingest perdido | Cron es idempotente vía hash. Si pasa un día sin correr, al día siguiente captura todo (YTD es acumulativo). |
| Año cerrado sin haber estado corriendo la app | Fallback: usuario descarga XML manualmente del portal IBKR y lo sube via Settings → Importar XML histórico. |
| Cambio % participación (Joint Holder) | Schema `participations.valid_from/valid_to` ya lo soporta. UI para editar queda como V2. |

---

## 11. No-goals V1 (futuros)

- Mobile app nativa
- Charts de performance histórica del portafolio
- Multi-usuario real (Joint Holder con su login)
- Alertas por email/push
- **Export oficial Form 160 en XML DIAN** (los DATOS sí están en V1; solo
  falta el serializer al schema oficial)
- Reducción automática de cost basis por Return of Capital (V1 los
  detecta y los excluye de dividendos; ajuste a cost basis es V2)
- Importación de CSV de dividends post-IRD reclassification (impacto
  fiscal <$10K COP/año, ver §2 decisión #10)
- Parser de Form 1042-S PDF
- Soporte de otros brokers (Schwab, Robinhood, etc.)
- Override manual de FIFO desde la UI (selección de lote a vender)
- Comparación con benchmark (S&P 500, etc.)

---

## 12. Métricas de éxito V1

- ✅ Test Owner abre el app, ve estado actual de los 3 IBKR accounts sin abrir Excel ni el portal IBKR
- ✅ Toma una decisión "vender hoy vs esperar a 730d" en < 1 minuto desde la UI
- ✅ Genera el subset IBKR del Form 210 anual con un click, paridad numérica vs proyecto `renta` (diferencias <$1K COP por redondeo)
- ✅ App self-sufficient: 30 días sin tocarla y los datos siguen frescos (cron + TRM auto)
- ✅ Si cambia el porcentaje de participación o la tarifa marginal, la app refleja el cambio sin código nuevo
