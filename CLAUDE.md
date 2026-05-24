# CLAUDE.md — IBKR Control Center

## ⏯ Cómo continuar (próxima sesión)

Esta sesión termina aquí. La próxima sesión de Claude Code abierta en este
folder retoma así:

1. **Leer este CLAUDE.md completo** (lo cargás automáticamente)
2. **Mirar el estado actual** en la sección "Estado actual" abajo
3. **Para ejecutar el plan actual**:
   - Plan vigente: `docs/plans/2026-05-24-ibkr-control-phase1-foundation.md`
   - Método elegido: **subagent-driven** (dispatch un subagent fresco por task, review entre tasks)
   - Skill a invocar: `superpowers:subagent-driven-development`
   - Pedirle al usuario: "¿Arrancamos con Task 1 del Phase 1?" antes de ejecutar
   - Cada task del plan tiene checkboxes `- [ ]` — marcarlos `- [x]` al completar y commitearlos
4. **Para planificar la SIGUIENTE phase** (cuando Phase N termine):
   - Verificar que Phase N esté completa: todos los checkboxes marcados + commits hechos + entregables del "Phase N entregables" tildados
   - Invocar `superpowers:brainstorming` solo si hay decisiones abiertas para Phase N+1; si la phase es directa desde el spec, saltar a `superpowers:writing-plans`
   - El spec maestro tiene TODO el contexto: `docs/specs/2026-05-24-ibkr-control-center-design.md`
   - Guardar el nuevo plan en `docs/plans/2026-05-24-ibkr-control-phase{N+1}-{nombre}.md` (usar fecha del día)
   - Actualizar la tabla "Estado actual" abajo con el link al plan nuevo
5. **Convenciones**: TDD, frequent commits (cada step termina en commit), no emojis en código, Decimal para dinero, Postgres-specific

### Datos que el usuario va a pegar durante setup wizard (Phase 2)

- IBKR Flex Token (read-only, generado en Account Management → Flex Web Service)
- IBKR Flex Query ID del "Year to Date" del año actual
- Email + password para la cuenta del app
- Marginal rate (default 0.39, el usuario puede cambiar después en Settings)
- XMLs históricos opcionales — ya los tiene en `../renta/fuentes/2024/compartidos/ibkr/` y `../renta/fuentes/2025/compartidos/ibkr/` (subir vía UI en Settings → Importar XML histórico)

### Decisiones locked (no re-discutir)

Ver "Decisiones arquitectónicas" abajo. Las 12 decisiones fueron acordadas en la
sesión de brainstorming del 2026-05-24. Si surge una pregunta cuya respuesta ya
está en el spec o en estas decisiones, NO re-hacer la pregunta al usuario;
referenciá el spec/CLAUDE.md y avanzá.

---

## Qué es este proyecto

Web app personal de Test Owner para visualizar y operar
sobre la información de Interactive Brokers con foco en decisiones de
inversión e impuestos (subset IBKR de la declaración de renta colombiana).

Reemplaza la necesidad de abrir Excel + scripts Python + portales IB
para responder preguntas como:
- ¿Qué lotes tengo abiertos y cuántos días llevan?
- ¿Cuánto falta para que un lote cruce a Ganancia Ocasional (730d)?
- Si vendo este lote hoy, ¿cuánto impuesto pago?
- ¿Cuáles son los dividendos del año y el WHT pagado?
- ¿Qué números van en mi Form 210 cas.74/76/77/78 por IBKR?

**No reemplaza** el proyecto `renta` (sibling, `/Users/owner/Development/renta/`)
— ese sigue siendo source of truth de la declaración completa incluyendo
Globant, AFC, leasing, etc. Esta app es el **centro de control IBKR-only**.

## Estado actual

| Phase | Status | Plan | Tag al completar |
|---|---|---|---|
| 1. Foundation | 📋 plan escrito, **listo para ejecutar** | `docs/plans/2026-05-24-ibkr-control-phase1-foundation.md` | `v0.1.0-foundation` |
| 2. Data ingestion (Flex WS + TRM Socrata + scheduler + upload XML + setup wizard) | ⏳ por planificar | — | `v0.2.0-ingest` |
| 3. Domain layer + lotes (FIFO, classification, lotes abiertos/cerrados/alertas) | ⏳ por planificar | — | `v0.3.0-lotes` |
| 4. Simulador (STK + FUT con neteo YTD) | ⏳ por planificar | — | `v0.4.0-simulator` |
| 5. Dividendos + Patrimonio + Form 160 + Reporte Form 210 | ⏳ por planificar | — | `v0.5.0-reports` |
| 6. Polish (yfinance + composición dashboard + multi-year report) | ⏳ por planificar | — | `v1.0.0` |

**Spec maestro** (autoridad final sobre QUÉ se construye): `docs/specs/2026-05-24-ibkr-control-center-design.md`

### Cómo actualizar esta tabla
- Al iniciar una phase: cambiar status a `⚙ ejecutando · task N/M`
- Al completar una phase: cambiar status a `✅ completado · <tag>` y tagear `git tag <tag>`
- Al escribir el plan de phase N+1: status `📋 plan escrito, listo para ejecutar` + link al archivo

## Tech stack

| Capa | Tecnología |
|---|---|
| Backend | Python 3.12 + FastAPI + SQLAlchemy 2.x async + asyncpg + Alembic + fastapi-users + APScheduler + uvicorn |
| Frontend | Next.js 14 (App Router) + TypeScript + Tailwind + shadcn/ui + TanStack Query + orval |
| DB | Postgres 16 |
| Hosting | Coolify self-hosted en Hetzner (Docker Compose) |
| Tooling | uv (Python), pnpm (Node), Playwright, GitHub Actions |
| External APIs | IBKR Flex Web Service (auto-fetch YTD diario), Socrata DIAN `ceyp-9c7c` (TRM), yfinance (precios) |

## Cuentas IBKR

| Cuenta | Descripción | % Test Owner |
|---|---|---|
| U99999001 | Conjunta Test Owner & Joint Holder | **50%** |
| U99999002 | Personal swing trading | **100%** |
| U99999003 | Personal micro-futuros | **100%** |

## Reglas fiscales IBKR (referencia rápida)

Cobertura completa en `docs/specs/2026-05-24-ibkr-control-center-design.md` §6.1.

### Aplicables

| Regla | Resumen | Cita |
|---|---|---|
| Residencia fiscal CO | Residente año completo, grava fuente extranjera | Art. 9 + 10 ET |
| Fuente extranjera | Todo IBKR califica; sustenta Art.254 | Art. 21-1 ET |
| Costo fiscal | Adquisición + comisiones capitalizadas | Art. 69 + 71 + 90 ET |
| Costo a TRM compra | `cost_cop = cost_usd × TRM(open_date)` | Art. 288 ET |
| Proceeds a TRM venta | `proceeds_cop = proceeds_usd × TRM(close_date)` | Art. 288 ET |
| 730 días → GO 15% | <730 = Renta Ordinaria, ≥730 = Ganancia Ocasional 15% | Art. 300 + 313 ET |
| Tarifa RO marginal | Configurable por usuario (default 0.39, top bracket Art.241) | Art. 241 ET |
| FUT/OPT netting per-contrato | Régimen DUAL: agrupar por `(cuenta, símbolo)`, netear, positivo→cas.74, negativo→cas.77 (postura permisiva) | Decreto 1797/2008 Art. 8 + DUR 1625/2016 Art. 1.2.4.2.74 |
| WHT US dividendos | Descuento Art.254 = WHT × pct_participación | Art. 254 ET |
| Return of Capital | NO es ingreso fiscal; reduce cost basis (V2) | Art. 46-1 ET |
| Patrimonio bruto 31-dic | `Σ mark_value_usd × TRM(31-dic) × pct` | Art. 261-263 ET |
| Form 160 (activos exterior) | Obligado si > 2000 UVT al 1-ene | Art. 607 ET |
| Medios Magnéticos | Brutos cas.74 IB contribuyen al umbral 11800 UVT | Res. 162/2023 + 227/2025 |

### NO aplicables (no confundir con reglas del sibling `renta`)

- **Art. 36-1, 153** — solo BVC, IBKR offshore no califica
- **Art. 242, 254-1** — dividendos sociedades nacionales (IBKR son extranjeros)
- **Art. 38-40** — componente inflacionario solo títulos COP
- **Art. 115** — GMF, no aplica a operaciones US
- **Conceptos DIAN 008706/2025, 003517/2025** — MGC BVC, no IBKR
- **Art. 408** — IBKR no es agente retenedor colombiano

## Decisiones arquitectónicas (locked, ver spec §2)

1. Repo nuevo independiente del `renta`, re-ingiere XML por su cuenta
2. Hosting: Coolify self-hosted en Hetzner (Docker)
3. Stack backend: Python 3.12 + FastAPI + SQLAlchemy async + Alembic + APScheduler
4. Stack frontend: Next.js 14 + TypeScript + TanStack Query + Tremor/shadcn
5. DB: Postgres 16
6. Auth: `fastapi-users` con JWT (single-user V1, schema multi-user-ready)
7. Ingest IBKR: Flex Web Service auto-fetch YTD diario + upload manual XMLs históricos
8. Ingest TRM: Socrata DIAN dataset `ceyp-9c7c`, automático, sin uploads
9. Precios actuales: yfinance con cache TTL 15min
10. Docs IBKR V1: solo Flex XML (sin CSV dividends ni 1042-S)
11. Tarifa marginal RO: configurable por usuario, default 0.39
12. Arquitectura: Monolito modular (API + scheduler + ingest en un proceso)

## 10 pantallas V1 (ver spec §4)

1. 📊 Dashboard — resumen + composición + banner Form 160
2. 📦 Lotes abiertos — tabla central con semáforo 730d
3. ✓ Cerrados — historial GO/RO
4. ⚠ Alertas 730d — subset filtrado urgente
5. 🧮 Simulador — modo STK y modo FUT
6. 💰 Dividendos — filtrados ROC vs dividend real
7. 🏦 Patrimonio — por cuenta + por símbolo + histórico
8. 🌍 Form 160 — status + listado activos exterior
9. 📋 Reporte Form 210 — single + multi-year
10. ⚙ Settings — config + import histórico

## Convenciones de código

- **TDD**: failing test → minimal impl → passing test → commit
- **Frequent commits**: cada step del plan termina en commit
- **DRY, YAGNI**: no over-engineering
- **No emojis en código** salvo iconografía UI explícita
- **Spanish para identifiers de UI/UX**, English para identifiers técnicos
- **Decimal para dinero**, nunca float
- **Postgres-specific** (JSONB, ON CONFLICT, ranges) — no abstraer a SQLite
- **Dedup de imports por SHA-256** (mismo patrón que `renta/documentos/_hash.py`)

## Comandos comunes

```bash
# Dev local
docker compose up -d --build

# Backend tests
cd backend && uv run pytest -v

# Frontend dev
cd frontend && pnpm dev

# Regenerar cliente TS del OpenAPI (cuando cambia el backend)
cd frontend && pnpm openapi:gen

# E2E
cd frontend && pnpm e2e

# Migration nueva
cd backend && uv run alembic revision --autogenerate -m "descripción"
cd backend && uv run alembic upgrade head
```

## Sibling project

`/Users/owner/Development/renta/` — generador del Form 210 completo
de Test Owner. Sigue siendo el source of truth para:
- Cédula trabajo (Globant Form 220, AFC, leasing, SURA)
- Tope 1340 UVT Art.336
- Anticipo Art.807
- Comparación patrimonial Art.236-239
- Patrimonio fuera de IBKR (apto, vehículo, etc.)

El proyecto `ibkr-control` produce el subset IBKR del Form 210; el output JSON
(via "Exportar JSON" en la pantalla Reporte) se puede consumir desde `renta`
para complementar la declaración completa.
