# W2 Securities Master — Implementation Plan (PR-2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Instrumento como entidad de primera clase: `instruments` + `instrument_identifiers` (control plane, multi-provider day-1 per T1-D7) con `instrument_id` FK en los hechos (NOT NULL fail-loud donde CR-1 lo avala), poblado por el persister vía `_ensure_instruments`.

**Architecture:** Mismo patrón W1: baseline mutable pre-deploy (T1-D14 amended) — amendment #4 del baseline `a9977ac077e5` + wipe & reload. Identidad externa por filas (`instrument_identifiers`, `UNIQUE(id_type, id_value)`) — el conid es UNA fila, no LA PK; los hechos referencian el surrogate `instruments.id` (aislamiento: cambiar el esquema de identidad después no toca hechos). `symbol`/`asset_class` SE QUEDAN en los hechos (T1-D8). Spec: `docs/specs/2026-06-10-tier1-worldclass-model-design.md` (T1-D7..D9, D14).

**Branch:** `saas/w2-securities-master` (ya creado desde `main` post-merge PR #11). NO crear branches nuevos.

**Reglas del repo (idénticas a W1):** TDD; tests desde el HOST (`cd backend && uv run pytest`); ruff antes de cada commit; baseline amendments canónicos (autogenerate temporal vía servicio migrate + splice entre markers, MISMO revision id `a9977ac077e5`); wipe dev (`down -v && make dev`) tras cada amendment; el lockstep guard (`test_org_scoped_snapshot_matches_live_ssot`) vigila `_ORG_SCOPED_TABLES` — **instruments/instrument_identifiers NO van en esa lista** (control plane, sin RLS, como `trm_days`/`institutions`).

## CR-1 — RESUELTO (2026-06-10, contra los 3 fixtures reales sanitizados)

| Tag | conid presente | `instrument_id` |
|---|---|---|
| `<Trade>` | 95/95 (2024) + 194/194 (2025) = **100%** | **NOT NULL** fail-loud |
| `<Lot>` (closed) | 5/5 + 146/146 = **100%** | **NOT NULL** fail-loud |
| `<OpenPosition>` | 96/96 + 131/131 = **100%** | **NOT NULL** fail-loud |
| `<ChangeInDividendAccrual>` | 97/97 = **100%** | **NOT NULL** fail-loud |
| `<OpenDividendAccrual>` | 1/1 = **100%** | **NOT NULL** fail-loud |
| `<CashTransaction>` | **0/23 (2024!)** · 80/115 (2025) | **nullable** — la Flex Query 2024 ni trae la columna; fees/intereses no tienen instrumento |
| `<Transfer>` | 4/6 · 6/22 · **0/2 (FOP 2026!)** | **nullable** — los FOP de GLOB (STK) tampoco traen conid; los CASH internos (`symbol="--"`) menos |

**Regla derivada (lockear en el código):** las 5 entidades 100% son **creators** (crean el instrument si no existe — tienen conid + assetCategory); cash/transfers son **resolvers** (lookup por conid si está presente y no-vacío; si falta → `instrument_id NULL`; NUNCA crean). La reconstrucción del linaje instrumento↔transfer FOP queda para el domain layer Phase 3 (join por symbol contra lots — documentado en spec).

## File Map

| Acción | Path | Responsabilidad |
|---|---|---|
| Create | `backend/src/ibkr_control/db/models/instruments.py` | `Instrument` + `InstrumentIdentifier` (control plane) |
| Modify | `backend/src/ibkr_control/db/models/flex_raw.py` | `instrument_id` en los 7 hechos + índices `(account_id, instrument_id)` |
| Modify | `backend/src/ibkr_control/db/__init__.py` | Exports |
| Modify | `backend/alembic/versions/a9977ac077e5_tier1_baseline.py` | Amendment #4 (autogen splice) |
| Modify | `backend/src/ibkr_control/ingest/flex/_models.py` | conid/isin/etc. en las dataclasses |
| Modify | `backend/src/ibkr_control/ingest/flex/parser.py` | extracción + `_require_conid` fail-loud |
| Modify | `backend/src/ibkr_control/ingest/flex/persister.py` | `_ensure_instruments` + threading `instruments_map` |
| Tests | `backend/tests/test_tier1_baseline.py` (extender), `backend/tests/ingest/flex/test_parser*.py`, `test_persister*.py`, replay suite, nuevos según task | |
| Docs | `CLAUDE.md`, roadmap (W2 ✅) | Task final |

---

### Task 1: Modelos instruments + baseline amendment #4

**Files:** Create `db/models/instruments.py` · Modify `db/models/flex_raw.py`, `db/__init__.py`, baseline · Test: `tests/test_tier1_baseline.py`

- [ ] **Step 1 (TDD):** extender `test_tier1_baseline.py`: (a) `test_instruments_tables_exist_no_rls` — upgrade head → `instruments`/`instrument_identifiers` existen y NO tienen RLS (query `pg_class.relrowsecurity = false`), espejo del estatus control-plane de `institutions`; (b) `test_instrument_identifiers_unique` — INSERT dos identifiers `('conid','265598')` → el segundo viola `uq_instrument_identifiers_id_type_id_value` (o el nombre que la convención produzca — verificar). Run → FAIL.

- [ ] **Step 2:** crear `backend/src/ibkr_control/db/models/instruments.py`:

```python
"""Securities master: instrumento como entidad de primera clase (W2, T1-D7).

Control plane (global, SIN RLS — como trm_days/institutions): AAPL es AAPL
para todos los tenants. Identidad externa por FILAS en instrument_identifiers
(el conid de IBKR es una fila, no la PK) — un provider futuro agrega filas,
no migra el master. Los hechos referencian el surrogate instruments.id, por lo
que el esquema de identidad puede evolucionar sin tocar hechos (T1-D7).

Escritura: solo el persister (_ensure_instruments), con valores parseados del
XML de IBKR — no input directo de usuario (T1-D9). symbol es last-seen: un
ticker change (FB->META, mismo conid) actualiza symbol y updated_at.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ibkr_control.db.base import Base

IDENTIFIER_TYPES = ("conid", "isin", "cusip", "figi")


class Instrument(Base):
    __tablename__ = "instruments"
    __table_args__ = (
        {
            "comment": (
                "Control plane (global, sin RLS): securities master. Identidad "
                "externa en instrument_identifiers; symbol/atributos last-seen "
                "del XML IBKR. Escrito solo por el persister (T1-D9)."
            )
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    asset_class: Mapped[str] = mapped_column(String, nullable=False)
    currency: Mapped[str | None] = mapped_column(String, nullable=True)
    multiplier: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class InstrumentIdentifier(Base):
    __tablename__ = "instrument_identifiers"
    __table_args__ = (
        CheckConstraint("id_type IN ('conid', 'isin', 'cusip', 'figi')", name="id_type"),
        UniqueConstraint("id_type", "id_value", name="uq_instrument_identifiers_id_type_id_value"),
        Index(None, "instrument_id"),
        {
            "comment": (
                "Identidad externa del instrumento, una fila por (tipo, valor). "
                "Multi-provider day-1 (T1-D7): conid IBKR hoy; isin cuando el "
                "XML lo trae; cusip/figi reservados."
            )
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False
    )
    id_type: Mapped[str] = mapped_column(String, nullable=False)
    id_value: Mapped[str] = mapped_column(String, nullable=False)
```

(Importar `Decimal` de `decimal`.) Exports en `db/__init__.py` (`Instrument`, `InstrumentIdentifier` + `__all__`).

- [ ] **Step 3:** `instrument_id` en los hechos (`flex_raw.py`). En `Trade`, `ClosedLot`, `OpenPositionLot`, `ChangeInDividendAccrual`, `OpenDividendAccrual` (NOT NULL) y `CashTransaction`, `Transfer` (nullable):

```python
    # W2 (T1-D8): FK al securities master. NOT NULL fail-loud — CR-1 verificó
    # conid 100% presente en este tag contra los 3 fixtures reales. symbol y
    # asset_class se conservan como fidelidad de fuente; el agrupado canónico
    # (FIFO Phase 3) es por (account_id, instrument_id) — inmune a ticker changes.
    instrument_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("instruments.id", ondelete="RESTRICT"), nullable=False
    )
```

(variante nullable con comment citando CR-1 para cash/transfers — la Flex Query 2024 ni trae conid en cash; los FOP STK tampoco). Índices: agregar `Index(None, "account_id", "instrument_id")` en los 5 hechos account-scoped con NOT NULL (conservar `(account_id, symbol)` — criterio db-hardening: remover índices se decide con evidencia de uso). Para cash/transfers: `Index(None, "instrument_id")` simple (FK con RESTRICT necesita índice para el delete-check).

- [ ] **Step 4:** baseline amendment #4 — procedimiento W1 (autogenerate temporal vía migrate service contra DB virgen, splice entre markers, MISMO revision id). Sin seed (instruments se puebla por ingest). SIN policies RLS para las 2 tablas nuevas (control plane). Verificar que el grep de `flex_credentials` no reviva nada y que `_ORG_SCOPED_TABLES` NO cambie.

- [ ] **Step 5:** suite + wipe + commit: `uv run pytest -q` (drift + lockstep + baseline asserts verdes — los tests de persister EXISTENTES van a ROMPER por el NOT NULL sin poblar → este task DEBE coordinarse con Tasks 2-3 si la suite no pasa; si es así, reportar el corte exacto y combinar el commit con Task 3, documentándolo). Si la suite rompe por NOT NULL: opción aceptada — dejar este task como commit conjunto con Task 3 (modelos + parser + persister en un solo commit verde), manteniendo los steps TDD internos. `down -v && make dev`. Commit `feat(w2): instruments + instrument_identifiers + instrument_id en hechos (baseline amendment #4)`.

---

### Task 2: Parser — extracción de instrumento + fail-loud

**Files:** Modify `ingest/flex/_models.py`, `ingest/flex/parser.py` · Test: tests de parser existentes (encontrarlos: `grep -rn "parse" backend/tests/ingest/flex/ -l`)

- [ ] **Step 1 (TDD):** tests contra los fixtures REALES (no sintéticos): (a) cada `ParsedTrade/ParsedClosedLot/ParsedOpenPositionLot` del fixture 2025 tiene `conid` no-vacío; (b) los accruals ya traían conid (sin regresión); (c) `ParsedCashTransaction.conid is None` para las rows 2024 (sin columna) y poblado para dividendos 2025; (d) `ParsedTransfer.conid is None` para los FOP 2026 y los CASH internos; (e) fail-loud: un XML sintético con `<Trade conid="">` → `ValueError` con mensaje accionable (patrón `_require_asset_class` de Phase 2.9 — leerlo primero).

- [ ] **Step 2:** `_models.py`: agregar a `ParsedTrade`, `ParsedClosedLot`, `ParsedOpenPositionLot`: `conid: str` + `isin: str | None` + `description: str | None` + `currency: str | None` + `multiplier: Decimal | None` (los atributos de instrumento que el creator aporta). A `ParsedCashTransaction` y `ParsedTransfer`: `conid: str | None` (resolver-only — sin más atributos). Los accruals ya tienen conid/isin.

- [ ] **Step 3:** `parser.py`: helper `_require_conid(elem, tag)` (espejo de `_require_asset_class`: atributo ausente O vacío → ValueError con tag + contexto). Aplicarlo en los 5 tags creators; en cash/transfers `elem.get("conid") or None`. Extraer `isin`/`description`/`currency`/`multiplier` con `or None` (verificar los nombres de atributo REALES contra el fixture — e.g. `description`, `currency`, `multiplier`; si multiplier viene en otro atributo o no viene, capturar lo que exista y documentar).

- [ ] **Step 4:** suite parser verde + ruff + commit `feat(w2): parser extrae identidad de instrumento (conid fail-loud en creators, CR-1)`.

---

### Task 3: Persister — `_ensure_instruments` + threading

**Files:** Modify `ingest/flex/persister.py` · Test: tests de persister + replay

- [ ] **Step 1 (TDD):** tests integration (testcontainers, patrón existente): (a) persist del fixture 2025 → `instruments` pobladas (count > 0), cada trade/lot/accrual con `instrument_id` NOT NULL apuntando a un instrument cuyo identifier `('conid', ...)` matchea su conid; (b) idempotencia: re-persist → cero instruments nuevos; (c) ticker change: persist XML sintético con conid X symbol "FB" → persist segundo XML mismo conid symbol "META" → UN instrument, `symbol == "META"`, `updated_at` avanzó; (d) cash 2024 (sin conid) → `instrument_id IS NULL`, sin instruments espurios; (e) resolver-no-creator: un cash con conid de instrument INEXISTENTE (sintético) → `instrument_id NULL` (no crea — log warning).

- [ ] **Step 2:** `_ensure_instruments(session, parsed) -> dict[str, int]` (conid → instrument_id), llamada en `persist()` después de `_ensure_accounts`:
  - Recolectar specs de instrumento de los CREATORS (trades, closed_lots, open_position_lots, accruals ×2): `{conid: (symbol, asset_class, isin?, description?, currency?, multiplier?)}` — last-seen gana dentro del batch.
  - SELECT identifiers existentes por `(id_type='conid', id_value IN ...)` (chunked `_BATCH_SIZE`).
  - Para conids faltantes: INSERT instruments (RETURNING id) + INSERT identifiers (conid siempre; isin si está y no choca — `ON CONFLICT (id_type, id_value) DO NOTHING` para la carrera cross-org, re-SELECT tras conflicto, patrón `_ensure_accounts` post-multihome).
  - Para existentes: DO UPDATE last-seen de `symbol/name/currency/multiplier/updated_at=NOW()` SOLO si cambió algo material (comparación en Python; evita churn de updated_at en cada ingest — y deja el hook limpio para el restatement log de W3).
  - Devolver el map completo.
- [ ] **Step 3:** threading en `_upsert_all_children`: firma gana `instruments_map: dict[str, int]`; cada row builder de creators agrega `"instrument_id": instruments_map[x.conid]`; cash/transfers: `instruments_map.get(x.conid)` si conid no-None (si conid presente pero NO está en el map → `None` + `logger.warning` una vez por conid — resolver nunca crea).
- [ ] **Step 4:** suite COMPLETA + replay suite (idempotencia ×2 fixtures + FIFO parity intacta) + wipe dev + smoke real: `make dev`, recargar un XML vía wizard/manual y verificar instruments en DB. Ruff + commit `feat(w2): persister _ensure_instruments + instrument_id en hechos`.

---

### Task 4: Verificación integral + docs + PR

- [ ] **Step 1:** suite completa backend + `pnpm lint && pnpm test && pnpm build` (el frontend NO cambia en W2 — verificar que el openapi no cambió shapes consumidas; si los schemas de respuesta de hechos no exponen instrument_id aún, NO agregarlo — los consumidores llegan en Phase 3, YAGNI) + `make prod-local` boot smoke.
- [ ] **Step 2:** docs: CLAUDE.md (bullet W2 + SIGUIENTE → W3 restatement log) + roadmap (W2 ✅ hecho).
- [ ] **Step 3:** push + `gh pr create` (título `W2: securities master — instruments + identifiers (re-baseline pre-deploy)`).
- [ ] **Step 4:** review holístico final (cross-cutting: cadena conid → identifier → instrument → FK; creators vs resolvers consistente parser↔persister; baseline coherente tras amendment #4).

## Self-Review (al escribir el plan)

- **Spec coverage:** T1-D7 (identifiers como filas + surrogate FK — Task 1) · T1-D8 (NOT NULL/nullable por CR-1, symbol/asset_class quedan, índices — Tasks 1-3) · T1-D9 (grants ya cubiertos por el blanket grant del baseline; sin RLS — Task 1) · CR-1 resuelto arriba. W3 fuera de scope (PR-3).
- **Riesgo principal:** el NOT NULL rompe la suite entre Task 1 y Task 3 — mitigado con la cláusula de commit conjunto documentada en Task 1/Step 5.
- **Sin API/UI nueva:** los consumidores de instruments son Phase 3 (FIFO) y Phase 6 (yfinance) — exponerlos hoy sería el anti-patrón del primitive sin consumidor.
