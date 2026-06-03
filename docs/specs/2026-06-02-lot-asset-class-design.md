# Spec — `asset_class` como hecho de primera clase en los lotes

**Fecha:** 2026-06-02
**Branch:** `fix/lot-asset-class`
**Estado:** diseño aprobado, pendiente plan + implementación
**Motiva:** Phase 2.x cleanup pre-Phase-3. Decisión derivada del análisis fiscal manual del 2026-06-02 (caso bono RSU Globant).

## Problema

`closed_lots` no tiene columna `asset_class`. El régimen fiscal (STK → Art. 288 + regla 730d; FUT/OPT → Decreto 1797 DUAL) se infería vía join `closed_lots.source_trade_id → trades.asset_class`. Eso es frágil y **ya está roto** contra datos reales:

- `source_trade_id` es `nullable=True` y apunta **siempre al trade de apertura** (`buy_sell=BUY, open_close=O` en las 152 filas no-nulas).
- Los activos adquiridos por **FOP / transferencia / corporate action no tienen trade de apertura** → `source_trade_id = NULL`.
- Evidencia: de 154 closed_lots, **2 tienen `source_trade_id` NULL** — son exactamente los 2 lotes de GLOB del bono RSU de Globant (entraron por FOP el 2026-04-28, sin compra). Para esos lotes la inferencia de `asset_class` devuelve nada → quedarían **sin clasificar fiscalmente** (silenciosamente excluidos o mal clasificados) en un análisis 2026.

El discriminador fiscal más importante depende de una relación opcional, y el patrón `ondelete=SET NULL` del schema (append-only ledger) agrava el riesgo: un re-ingest que reemplace el trade de apertura puede dejar el lote vivo con el FK huérfano y el régimen evaporado, sin error.

`open_position_lots` tiene el mismo problema (sin `asset_class`, sin `source_trade_id` siquiera): las 19 GLOB abiertas no tendrían cómo clasificarse.

El valor autoritativo **ya está en la fuente**: el XML trae `assetCategory` directamente en `<Lot levelOfDetail="CLOSED_LOT">` y en `<OpenPosition>`. Verificado 100% presente en los 3 XMLs reales (2024/2025/2026): categorías observadas `STK` y `FUT` (el futuro MES aparece como CLOSED_LOT con `assetCategory=FUT`).

## Principio rector

`asset_class` es un **hecho crudo** que la fuente provee en la propia fila del lote. Se captura en la ingesta; la **interpretación fiscal** (mapeo a régimen Art. 288 vs Decreto 1797) se deriva en el domain de Phase 3 (`regime.py::classify_asset`), no en la ingesta ni en la DB. Esto respeta el principio del proyecto: *capturar el hecho crudo en la ingesta, derivar la interpretación en el domain*.

`source_trade_id` **deja de ser el clasificador fiscal** y queda exclusivamente para linaje/auditoría (trazar el lote cerrado a su trade de compra para cost-basis traceability). No se modifica.

## Decisiones (aprobadas 2026-06-02)

- **D1 — Alcance:** agregar `asset_class` a **ambas** tablas: `closed_lots` + `open_position_lots`. Simetría: las posiciones abiertas necesitan el discriminador fiscal igual que las cerradas (patrimonio FUT vs STK, lotes FOP abiertos).
- **D2 — Qué se guarda:** `asset_class` **crudo** (`STK`/`FUT`/`OPT`/…), espejando `assetCategory` del XML. NO se guarda un régimen fiscal derivado (eso sería interpretación en la ingesta, acoplaría a reglas mutables, y viola el principio rector).
- **D3 — Tipo/constraint:** `String, NOT NULL`, **sin CHECK** — idéntico a `trades.asset_class` (consistencia con lo existente). IBKR emite muchas categorías; un CHECK restrictivo sería frágil. El dominio de regímenes fiscales válidos se valida en Phase 3 (domain), no en la DB.
- **D4 — Migración/datos:** revisión nueva (`down_revision = cbeaac94933d`), `add_column` NOT NULL en ambas tablas. Rollout = wipe & reload (datos dev descartables, pre-deploy). **Sin script de backfill** (cero legacy code). Downgrade reversible.
- **D5 — Fail-loud en parser:** si `assetCategory` falta o está vacío en un `<Lot CLOSED_LOT>` o `<OpenPosition>`, el parser levanta error claro (coherente con la filosofía `_known_tags`). Verificado 100% presente → guarda defensiva, no se espera que dispare.

## Cambios por capa

### Modelos (`db/models/flex_raw.py`)
- `ClosedLot.asset_class: Mapped[str] = mapped_column(String, nullable=False)` — posicionado tras `symbol` (matchea el orden `symbol → asset_class` de `Trade`).
- `OpenPositionLot.asset_class: Mapped[str] = mapped_column(String, nullable=False)` — idem.
- Sin CHECK, sin column comment (consistente con `trades.asset_class`).

### Parser (`ingest/flex/parser.py` + `ingest/flex/_models.py`)
- Agregar `asset_class: str` a `ParsedClosedLot` y `ParsedOpenPositionLot` (tras `symbol`).
- Extraer `assetCategory` del `<Lot CLOSED_LOT>` y `<OpenPosition>`.
- Fail-loud (D5): error claro si el atributo falta/está vacío.

### Persister (`ingest/flex/persister.py`)
- Mapear `asset_class` en los insert dicts de ambas tablas.
- **Natural keys sin cambios** — `asset_class` es atributo estable del instrumento, no discriminador de fila. `closed_lots` sigue UPSERT immutable (ON CONFLICT DO NOTHING); `open_position_lots` sigue snapshot (DO UPDATE; `asset_class` incluido en el SET, estable per símbolo).

### Migración (`alembic/versions/`)
- Revisión nueva, `down_revision = cbeaac94933d`.
- `upgrade()`: `op.add_column('closed_lots', sa.Column('asset_class', sa.String(), nullable=False))` + idem `open_position_lots`. Corre contra tablas de lotes vacías (post-wipe).
- `downgrade()`: drop de ambas columnas.

## Rollout

1. `docker compose exec backend uv run python -m scripts.wipe_flex_data` (script existente).
2. `alembic upgrade head`.
3. Usuario recarga los 3 XMLs por el wizard.
4. Verificación: los lotes GLOB (cerrados + abierto) tienen `asset_class='STK'` pese a `source_trade_id` NULL; el closed_lot del futuro MES tiene `asset_class='FUT'`.

## Testing (TDD)

- Parser extrae `asset_class` en closed + open lots (assert `STK`/`FUT` contra fixtures 2024/2025).
- Parser fail-loud: lote sintético sin `assetCategory` → levanta.
- Persister persiste `asset_class` en ambas tablas.
- **Regresión FOP/Globant**: lotes GLOB (cerrados + abierto) obtienen `asset_class` sin trade de apertura (`source_trade_id` NULL).
- Drift test endurecido de Phase 2.8 (`compare_metadata`) verde.
- Suite completa 304 → +N, ruff clean.

## Fuera de alcance

- Derivación de régimen fiscal (`regime.py`) — es Phase 3 domain.
- Modificar `source_trade_id` (queda para linaje).
- `trades.asset_class` (sin cambios).
- Poblar `participations` (decisión abierta de Phase 3, no relacionada).

## Follow-ups conocidos (no bloquean; decisión consciente)

- **Asimetría `trades.asset_class` vs lotes (detectada en code review) — RESUELTO:**
  Harmonizado a fail-loud en el mismo branch `fix/lot-asset-class` como follow-up
  inmediato post code review. `_parse_trade` ahora usa `_require_asset_class(elem,
  "<Trade>")` igual que los parsers de lotes. Un `<Trade>` sin `assetCategory` levanta
  `ValueError` (no persiste `""`). Verificado 100% presente en 302 filas reales
  (fixtures 2024/2025 + XML 2026). Test de regresión:
  `test_parse_trade_fails_loud_when_asset_category_missing`.
- **Precondición de la migración:** `eb5ef6d36e06` agrega NOT NULL sin `server_default`
  → exige tablas de lotes vacías al `upgrade`. Documentado en el docstring de la
  migración + Task 4 del plan. Para el deploy a prod: mergear esta branch ANTES del
  primer deploy a Coolify (la primera corrida de migraciones será contra una DB vacía),
  o si prod ya tuviera datos, wipear los lotes antes del upgrade.

## Referencias

- Análisis fiscal manual 2026-06-02 (sesión que motivó esto).
- Memoria `globant-rsu-cost-basis-phase3` (caso de validación).
- Precedente de columna `asset_class`: `trades.asset_class` (`db/models/flex_raw.py`).
- Precedente de baseline único: Phase 2.8 (`cbeaac94933d`), spec `docs/specs/2026-06-02-schema-hardening-multiuser-design.md`.
