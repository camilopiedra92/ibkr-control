# syntax=docker/dockerfile:1.7

# ---------- deps: install pnpm + node_modules (compartido por dev + prod build) ----------
FROM node:22-alpine AS deps
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@latest --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile

# ---------- dev: next dev con HMR; source bind-mounted desde compose.dev.yaml ----------
FROM deps AS dev
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
# WATCHPACK_POLLING=true se setea en compose.dev.yaml para que macOS/Docker Desktop
# detecte file events del bind mount (inotify nativo no se propaga al container).
EXPOSE 3000
CMD ["pnpm", "dev"]

# ---------- builder: produce el standalone bundle para prod ----------
FROM deps AS builder
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@latest --activate
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

# ---------- prod: minimal runner (node + standalone bundle), sin source ni devDeps ----------
FROM node:22-alpine AS prod
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
RUN addgroup -S app && adduser -S app -G app
COPY --from=builder --chown=app:app /app/.next/standalone ./
COPY --from=builder --chown=app:app /app/.next/static ./.next/static
COPY --from=builder --chown=app:app /app/public ./public
USER app
EXPOSE 3000
CMD ["node", "server.js"]
