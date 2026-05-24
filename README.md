# IBKR Control Center

Centro de control personal de inversiones e impuestos para cuentas
Interactive Brokers (Colombia). Visualiza lotes abiertos, cerrados,
clasificación GO/RO (Art.300 ET), simula ventas, agrega dividendos
con WHT (Art.254 ET), genera el subset IBKR del Form 210.

## Documentación

- **Spec**: `docs/specs/2026-05-24-ibkr-control-center-design.md`
- **Plan Phase 1 (Foundation)**: `docs/plans/2026-05-24-ibkr-control-phase1-foundation.md`
- **Contexto para Claude**: `CLAUDE.md`

## Quick start (dev local)

```bash
docker compose up -d --build
# Backend: http://localhost:8000/docs
# Frontend: http://localhost:3000
```

## Deploy Coolify

Ver `docs/deploy.md` (se crea en Task 14 del plan Phase 1).

## Estructura

```
ibkr-control/
├── backend/          FastAPI + SQLAlchemy + Alembic + APScheduler (creado en Phase 1)
├── frontend/         Next.js 14 + TypeScript (creado en Phase 1)
├── docs/
│   ├── specs/        Diseño del producto
│   └── plans/        Planes de implementación por phase
├── docker-compose.yml             Stack dev (creado en Phase 1)
├── docker-compose.coolify.yml     Stack deploy (creado en Phase 1)
└── .github/workflows/             CI (creado en Phase 1)
```

## Sibling project

`../renta/` — generador del Form 210 completo del usuario. Source of truth
para lógica fiscal más allá de IBKR (cédula trabajo, AFC, leasing,
patrimonio inmuebles, etc.).
