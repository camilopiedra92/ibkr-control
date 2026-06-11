# W3 Restatement Log — Implementation Plan (PR-3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convertir el restatement silencioso en señal auditable: tabla `restatement_log` (org-scoped, RLS) poblada por diff Python pre-upsert en las tablas snapshot (`value_update`) + detección de sibling rows en `closed_lots` (`sibling_row`), con flag `sealed_year` ("tu declaración pudo haber cambiado") y surfacing completo (API + health + SSE + Settings UI).

**Architecture:** Mismo patrón W1/W2: baseline mutable pre-deploy (T1-D14) — amendment #5 de `a9977ac077e5` + wipe dev. Detección T1-D10: SELECT batched pre-upsert + diff en Python sobre columnas materiales (cero triggers). Spec: `docs/specs/2026-06-10-tier1-worldclass-model-design.md` §PR-3 (T1-D10..D13).

**Branch:** `saas/w3-restatement-log` (creado desde `main` post-merge PR #12). NO crear branches nuevos.

**Reglas del repo (idénticas a W1/W2):** TDD; tests desde el HOST; ruff; baseline amendment canónico (autogenerate vía migrate service + splice, mismo revision id); **`restatement_log` SÍ es org-scoped** → entra en `ORG_SCOPED_TABLES` (rls.py) + snapshot `_ORG_SCOPED_TABLES` del baseline + policy RLS frozen inline (el lockstep guard vigila); wipe dev tras amendment.

## CR-2 — RESUELTO POR CONSTRUCCIÓN (amendment, 2026-06-11)

El checkpoint original pedía diffear dos XMLs YTD reales consecutivos. No existen dos snapshots consecutivos del mismo año en los fixtures (uno por año) ni en la dev DB (wipeada). PERO el análisis de las natural keys lo resuelve estructuralmente: **las dimensiones que churnan a diario están DENTRO de las natural keys** (`snapshot_date` en open_position_lots; `report_date` en ambos accruals) → un YTD nuevo crea FILAS nuevas, no updates. El `DO UPDATE` solo dispara en re-ingest key-idéntico — exactamente el evento "restatement" que W3 quiere capturar. Las columnas materiales se validan con tests sintéticos (XML modificado) + el golden test de cero falsos positivos; la verificación contra dos snapshots reales consecutivos queda documentada como follow-up post-deploy (cuando el cron produzca dos).

**Amendment a T1-D11 (3 tablas snapshot, no 2 — `change_in_dividend_accruals` también es `_upsert_snapshot`):**

| Tabla snapshot | update_cols actuales (universo del DO UPDATE) | **Columnas MATERIALES** (restatement) | Excluidas (churn esperado / metadata) |
|---|---|---|---|
| `open_position_lots` | flex_import_id, asset_class, qty, cost_basis_usd, mark_price_usd, mark_value_usd | `qty`, `cost_basis_usd`, `asset_class` | mark_price_usd, mark_value_usd, flex_import_id. (`open_date` del spec original está EN la key — no puede cambiar; removida del set) |
| `change_in_dividend_accruals` | flex_import_id, symbol, isin, issuer_country, currency, quantity, amounts... (leer el call site completo) | `quantity`, `gross_rate_per_share`, `gross_amount_usd`, `tax_usd`, `fee_usd`, `net_amount_usd` | symbol/isin/issuer_country/currency (metadata), flex_import_id, raw_attrs, level_of_detail, asset_category, sub_category. (`ex_date`/`pay_date`/`accrual_date` del spec original están EN la key) |
| `open_dividend_accruals` | ídem patrón | `quantity`, `gross_rate_per_share`, `gross_amount_usd`, `tax_usd`, `fee_usd`, `net_amount_usd` | ídem |

`instrument_id` (W2) NO es material en ninguna (cambiaría solo por bug del master — el FK NOT NULL ya lo protege).

**sealed_year (T1-D13), definición operacional:** el año fiscal de la fila afectada (derivado por tabla: `snapshot_date` → open_position_lots; `report_date` → accruals; `close_date` → closed_lots siblings) tiene un `flex_imports` row del org con `anyo = <año>` y `year_status = 'sealed'`. Computado en detección (query batched por años distintos del batch).

## File Map

| Acción | Path | Responsabilidad |
|---|---|---|
| Create | `backend/src/ibkr_control/db/models/restatements.py` | `RestatementLog` (org-scoped, RLS) |
| Modify | `backend/src/ibkr_control/db/rls.py` + baseline (amendment #5) | ORG_SCOPED_TABLES + policy + DDL |
| Modify | `backend/src/ibkr_control/ingest/flex/_upsert_helpers.py` | `_upsert_snapshot_with_audit` |
| Modify | `backend/src/ibkr_control/ingest/flex/persister.py` | wiring 3 snapshots + sibling detection closed_lots + counters |
| Modify | `backend/src/ibkr_control/api/ingest.py` | `GET /api/ingest/restatements` + n_restatements en SSE |
| Modify | `backend/src/ibkr_control/api/health.py` | counts de restatements recientes |
| Modify | `frontend/src/components/settings/IngestHealthTable.tsx` (o panel nuevo) | badge + tabla de detalle |
| Tests | baseline, persister (golden + sintéticos + sibling), api, vitest | |

---

### Task 1: Backend core — modelo + baseline amendment #5 + detección

**Files:** Create `db/models/restatements.py` · Modify `db/__init__.py`, `db/rls.py`, baseline, `_upsert_helpers.py`, `persister.py` · Tests: `test_tier1_baseline.py` (extender), `tests/ingest/flex/test_restatements.py` (new)

- [ ] **Step 1 (TDD):** failing tests en `tests/ingest/flex/test_restatements.py` (testcontainers, fixtures reales, patrón de `test_persister_instruments.py`):
  - `test_golden_identical_reingest_zero_restatements`: persist fixture 2025 → re-persist el MISMO contenido con bytes distintos (agregar un comment XML `<!-- reingest -->` para esquivar el hash dedup fast-path — mismas filas parseadas) → `restatement_log` count == 0. **El test de oro contra falsos positivos.**
  - `test_value_update_detected_on_material_change`: persist fixture → re-persist con `cost_basis_usd` de un open_position_lot modificado en el XML (mutación quirúrgica del XML por string/lxml) → 1 row `kind='value_update'` con table_name/natural_key/column_name/old/new correctos.
  - `test_mark_price_churn_not_a_restatement`: re-persist con mark_price modificado → 0 rows (columna no-material).
  - `test_sibling_row_detected_closed_lots`: persist XML sintético con un `<Lot>` → re-persist variante con MISMO (transactionID, close_datetime, qty) y distinto fifo_pnl (el caso IBIT wash-sale) → ambas filas EXISTEN en closed_lots (nunca borra) + 1 row `kind='sibling_row'` (`column_name='*'`, old=pnl viejo, new=pnl nuevo).
  - `test_sealed_year_flag`: marcar el `flex_imports` del año como `year_status='sealed'` antes del re-ingest con cambio material → row con `sealed_year=True`; sin sealed → False.
  - RLS: cubierto automático por `ORG_SCOPED_TABLES` (verificar que test_identity_models/test_rls lo recogen).

- [ ] **Step 2:** `db/models/restatements.py`:

```python
"""Restatement log: mutación material de un hecho ya persistido (W3, T1-D10..D13).

Convierte el riesgo silencioso del DO UPDATE (snapshot tables) y de los sibling
rows de closed_lots en señal auditable org-scoped. Poblado SOLO por el persister
durante el ingest; detection-only (nunca bloquea ni revierte el upsert).
sealed_year = la fila afectada cae en un año con flex_imports.year_status='sealed'
("tu declaración pudo haber cambiado") — máxima severidad en UI.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base


class RestatementLog(Base):
    __tablename__ = "restatement_log"
    __table_args__ = (
        CheckConstraint("kind IN ('value_update', 'sibling_row')", name="kind"),
        Index(None, "organization_id", text("detected_at DESC")),
        Index(None, "flex_import_id"),
        {
            "comment": (
                "Org-scoped (RLS). Señal de restatement: IBKR cambió un valor "
                "material de un hecho ya persistido (value_update, snapshot "
                "tables) o emitió un sibling con distinto fifo_pnl (sibling_row, "
                "closed_lots). Detection-only; nunca borra hechos."
            )
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    flex_import_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("flex_imports.id", ondelete="SET NULL"), nullable=True
    )
    table_name: Mapped[str] = mapped_column(String, nullable=False)
    natural_key: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    column_name: Mapped[str] = mapped_column(String, nullable=False)  # '*' para sibling_row
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    sealed_year: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
```

Exports + `ORG_SCOPED_TABLES` += `"restatement_log"` (rls.py) + baseline amendment #5: DDL via autogenerate splice + entrada en `_ORG_SCOPED_TABLES` + su bloque RLS ENABLE/FORCE/POLICY frozen inline (¡los TRES en lockstep — el guard test lo exige!).

- [ ] **Step 3:** `_upsert_helpers.py` — nueva `_upsert_snapshot_with_audit(session, table, rows, conflict_cols, update_cols, *, material_cols, natural_key_cols, audit_sink)`: (1) SELECT existing rows por natural keys del batch (chunked `_BATCH_SIZE`, tuple-IN o AND/OR construido — elegir lo que el dialecto soporte limpio: `tuple_(...).in_(...)` funciona en PG); (2) diff Python: para cada row entrante cuyo key existe, comparar `material_cols` (Decimal-aware: comparar valores, no strings); (3) por cada col cambiada → `audit_sink(table_name, natural_key_dict, col, old, new)`; (4) delegar a `_upsert_snapshot` sin cambios. `_upsert_snapshot` original queda (TRM u otros usos no-auditados... verificar quién más la usa; si solo flex, reemplazar directo y renombrar).

- [ ] **Step 4:** `persister.py`: wiring de las 3 snapshot tables con sus `material_cols` de la tabla CR-2 de arriba; `audit_sink` acumula en memoria y al final del persist: (a) computa `sealed_year` por años afectados (query batched a flex_imports), (b) INSERT batched a `restatement_log` (org + flex_import_id del import actual), (c) counter `n_restatements` agregado al dict `_counters` que persist() devuelve. **Sibling detection closed_lots:** tras `_upsert_immutable_returning_inserted` de closed_lots (si hoy usa `_upsert_immutable` plano, cambiar a la variante returning), para las rows INSERTADAS buscar existentes con mismo `(organization_id, transaction_id, close_datetime, qty)` y `fifo_pnl_usd` distinto → `kind='sibling_row'`, `column_name='*'`, old/new = pnls. Nunca borra.

- [ ] **Step 5:** baseline amendment #5 (procedimiento W1/W2) + wipe + suite completa + ruff + commits lógicos (modelo+baseline / helpers+persister). Suite era 399 — el golden test es el gate.

---

### Task 2: API + SSE

**Files:** Modify `api/ingest.py`, `api/health.py`, `api/_schemas.py` · Tests: `tests/api/test_restatements_api.py` (new) + `test_health_endpoint.py` + `test_ingest.py`

- [ ] **Step 1 (TDD):** failing tests: GET `/api/ingest/restatements` → paginado (`limit`/`offset`), filtros `flex_import_id`/`table_name`/`sealed_only`, orden `detected_at DESC`, RLS (org B no ve los de A); health gana `restatements: {recent_count, sealed_count}` (ventana: últimos 7 días — aditivo, `sources`/`connections` intactos); el SSE del refresh manual agrega `n_restatements` al evento `flex_ytd` ok/partial (test estilo `_run_manual` real + tracker events — la lección de drift).
- [ ] **Step 2:** implementar: schema `RestatementRead` (id, table_name, natural_key, column_name, old_value, new_value, kind, sealed_year, detected_at, flex_import_id); endpoint en `api/ingest.py` (router existente, RLS scopea); health counts; `_run_manual` propaga el counter (viene en `FlexRunSummary`/counters de persist — threading mínimo necesario: persist counters → `_run_one_connection` → summary; decidir la forma más simple y documentarla).
- [ ] **Step 3:** suite + ruff + commit.

---

### Task 3: Frontend — surfacing en Settings

**Files:** orval regen (canónico) · Modify Settings (sección "Salud de ingesta") · Create `frontend/src/components/settings/RestatementsPanel.tsx` (+ vitest) · Modify `ManualRefreshButton.tsx` (mostrar n_restatements si > 0)

- [ ] **Step 1:** orval regen canónico (container + `pnpm openapi:gen`, prefijo nvm).
- [ ] **Step 2 (TDD vitest):** RestatementsPanel: badge "N restatements (M en año sealed)" — ámbar si N>0, ROJO si M>0, oculto si 0; tabla de detalle (tabla afectada, columna, old → new, fecha, kind) con paginación simple; estados loading/empty/error. ManualRefreshButton: si el evento ok/partial trae `n_restatements > 0`, línea adicional "N valores restateados — revisá Salud de ingesta".
- [ ] **Step 3:** implementar (patrones existentes: TanStack + shadcn, UI strings en español, switch exhaustivo si aplica) + montar en Settings junto a IngestHealthTable. `pnpm test && pnpm lint && pnpm build` + commit.

---

### Task 4: Cierre — verificación + docs + PR + review holístico

- [ ] Suite completa backend + frontend + `make prod-local` boot smoke (restatement_log existe, RLS activa — `relrowsecurity=true`).
- [ ] Docs: CLAUDE.md (bullet W3 + SIGUIENTE → **Tier 1 COMPLETO**; próximo = SP2 Authorization per roadmap) + roadmap (W3 ✅ — Tier 1 cerrado) + spec (amendment CR-2 + T1-D11 de 3 tablas, si no quedó ya en este plan referenciado).
- [ ] Push + PR (`W3: restatement log — la señal auditable de mutación material`) + review holístico final (cadena detección → log → API → UI; golden test; lockstep RLS de la tabla nueva).

## Self-Review (al escribir el plan)

- **Spec coverage:** T1-D10 (diff Python pre-upsert — Task 1) · T1-D11 AMENDED (3 tablas snapshot, sets ajustados a keys reales — tabla CR-2 arriba) · T1-D12 (sibling detection-only — Task 1) · T1-D13 (sealed_year operacional — Task 1) · surfacing completo (Tasks 2-3) · CR-2 resuelto por construcción con follow-up post-deploy documentado.
- **Riesgo principal:** falsos positivos (churn diario detectado como restatement) — mitigado estructuralmente (keys contienen las dimensiones diarias) + golden test como gate.
- **El hook W2:** `_ensure_instruments` DO UPDATE NO se toca — los cambios de atributos de instruments son control-plane global, NO restatements fiscales org-scoped (fuera de scope W3 por diseño; la nota del spec queda satisfecha al decidirlo explícitamente acá).
