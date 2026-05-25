# Top-level wrapper de comandos. Lingua franca neutral entre backend (Python/uv)
# y frontend (Node/pnpm). Para scripts específicos de cada lado, ver
# backend/pyproject.toml y frontend/package.json.
#
# Stacks disponibles:
#   make dev          → compose.yaml + compose.dev.yaml (HMR backend + frontend)
#   make prod-local   → compose.yaml solo (espejo de Coolify, sin reload)
#   make coolify-local → compose.coolify.yaml (validar compose de prod localmente)

.PHONY: help dev dev-down dev-logs dev-build prod-local prod-local-down coolify-local coolify-local-down logs ps clean

help:
	@echo "Targets:"
	@echo "  make dev            - Stack dev (HMR backend + frontend, bind mounts)"
	@echo "  make dev-down       - Para el stack dev"
	@echo "  make dev-build      - Rebuild de imágenes dev sin levantar"
	@echo "  make dev-logs       - Tail de logs del stack dev"
	@echo "  make prod-local     - Stack prod-like (espejo de Coolify)"
	@echo "  make prod-local-down - Para el stack prod-like"
	@echo "  make coolify-local  - Levanta compose.coolify.yaml localmente (debug deploy)"
	@echo "  make logs           - Tail de logs del stack actualmente activo"
	@echo "  make ps             - docker compose ps"
	@echo "  make clean          - down + remove volumes (CUIDADO: borra postgres data)"

# ---------- Dev stack ----------
dev:
	docker compose -f compose.yaml -f compose.dev.yaml up -d --build

dev-down:
	docker compose -f compose.yaml -f compose.dev.yaml down

dev-build:
	docker compose -f compose.yaml -f compose.dev.yaml build

dev-logs:
	docker compose -f compose.yaml -f compose.dev.yaml logs -f

# ---------- Prod-like local (espejo de Coolify) ----------
prod-local:
	docker compose -f compose.yaml up -d --build

prod-local-down:
	docker compose -f compose.yaml down

# ---------- Coolify compose (validar deploy de prod localmente) ----------
coolify-local:
	docker compose -f compose.coolify.yaml up -d --build

coolify-local-down:
	docker compose -f compose.coolify.yaml down

# ---------- Utilidades ----------
logs:
	docker compose logs -f

ps:
	docker compose ps

clean:
	docker compose -f compose.yaml -f compose.dev.yaml down -v
	docker compose -f compose.coolify.yaml down -v 2>/dev/null || true
