# Deploy a Coolify

## Pre-requisitos
- Coolify ≥ v4 corriendo en tu Hetzner
- Dominio configurado (ej. ibkr.tudominio.com)
- DNS apuntando al servidor Coolify

## Setup inicial en Coolify

1. **New Resource → Docker Compose** (no "Application").
2. **Source**: conectar a este repo (GitHub/GitLab/self-hosted git).
3. **Compose file**: `compose.coolify.yaml`. (Antes era `docker-compose.coolify.yml` — si actualizás un Coolify existente, cambiar el nombre en la UI antes del próximo deploy.)
4. **Branch**: `main`.
5. **Environment variables** (en Coolify, sección Secrets):
   - `POSTGRES_USER` = ibkr
   - `POSTGRES_PASSWORD` = <generar: `openssl rand -hex 24`>
   - `POSTGRES_DB` = ibkr_control
   - `JWT_SECRET` = <generar: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`>
   - `JWT_LIFETIME_SECONDS` = 3600
   - `BACKEND_CORS_ORIGINS` = https://ibkr.tudominio.com
   - `PUBLIC_API_URL` = https://ibkr.tudominio.com/api
6. **Domains**:
   - frontend → `ibkr.tudominio.com` (puerto 3000)
   - backend → `ibkr.tudominio.com/api` (puerto 8000, path `/api`)
   Coolify maneja SSL automático via Let's Encrypt.
7. **Deploy**: click Deploy. Primer build tarda ~5 min.

## Migraciones DB

Las migrations corren manualmente la primera vez:

```bash
# Desde el server con Coolify, identificar el contenedor backend
docker ps | grep backend
docker exec -it <backend_container_id> uv run alembic upgrade head
```

Para futuras releases con migrations nuevas, repetir el comando o
agregarlo como `pre-deploy command` en Coolify (V2).

## Health check post-deploy

```bash
curl https://ibkr.tudominio.com/api/health
# Esperar: {"status":"ok"}
```
