# Pre-Production Hardening Backlog — IBKR Control Center

> **Origen:** auditoría multi-agente read-only del 2026-06-03 (8 finders especializados + verificación adversarial de cada finding crítico/alto). 16 agentes, ~1.2M tokens. Cubre las 4 dimensiones pedidas: **seguridad aplicada, CI/CD, observabilidad/resiliencia, arquitectura/testing**.
>
> **Framing:** la app NUNCA fue deployada a prod. Este hardening es el **gate antes del primer deploy** a Coolify. El core (crypto AES-GCM, authz default-deny, persister idempotente, schema) ya está endurecido y verificado limpio — lo que falta es casi todo **operacional / pre-deploy**: el sistema fue endurecido *como código correcto*, no *como servicio expuesto a internet*.
>
> **Calibración (locked):** "mejores prácticas mundiales" acá = app personal de ~3 usuarios (Test Owner, Joint Holder, contador read-only) en un solo host Coolify/Hetzner. Cero superficie de ataque evitable, secrets bien manejados, CI que bloquea merges rotos, logs que explican fallos, deps sin CVEs. **NO**: Prometheus/Grafana/tracing distribuido, secret managers externos (Vault), blue-green, multi-region, maquinaria multi-tenant. Lo `possibly-overkill` se **acepta y documenta como V1 consciente**, no se implementa.

## Cómo usar este documento

- Cada finding tiene un **checkbox** `[ ]`/`[x]` y un campo **Estado**.
- Al empezar un item: `[ ]` → marcar Estado `🔨 En progreso`.
- Al terminar (con test + verificación): `[x]` + Estado `✅ Hecho` + commit SHA.
- Items `⚖️ Aceptado V1` y `🟦 Diferido` NO se implementan en este hardening — son decisiones registradas. No marcar checkbox.
- Actualizar la **tabla de progreso** de abajo a medida que se cierran items.

### Leyenda de Estado

| Símbolo | Significado |
|---|---|
| ⏳ Pendiente | Acordado, sin empezar |
| 🔨 En progreso | En implementación |
| ✅ Hecho | Implementado + tested + commiteado |
| 🟦 Diferido | A Phase 3/4 — decisión consciente |
| ⚖️ Aceptado V1 | No se implementa — over-engineering para 3 usuarios, registrado como decisión |

### Leyenda de Tipo (appropriateness)

| Símbolo | Significado |
|---|---|
| 🟢 core | Best practice real que toda app necesita |
| 🔵 contextual | Bueno para esta app específicamente |
| ⚪ overkill | Técnicamente cierto pero ceremonia enterprise para 3 usuarios |

---

## Tabla de progreso

**Total accionable: 34 items** (1 prereq + 1 HIGH + 20 MEDIUM + 12 LOW-que-vale) · **Aceptado V1: 11** · **Diferido: 1**

| Estado | Count |
|---|---|
| ✅ Hecho | 0 / 34 |
| 🔨 En progreso | 0 |
| ⏳ Pendiente | 34 |
| ⚖️ Aceptado V1 | 11 |
| 🟦 Diferido | 1 |

### Resumen por workstream

| WS | Workstream | Accionables | Hechos |
|---|---|---|---|
| WS0 | Genericización del repo (prerequisito) | 1 | 0 |
| WS1 | Seguridad de la aplicación (incl. H1, S14) | 11 | 0 |
| WS2 | Config de deploy / secrets | 2 | 0 |
| WS3 | Observabilidad / resiliencia | 3 | 0 |
| WS4 | CI/CD & automatización | 10 | 0 |
| WS5 | Arquitectura / Phase-3 readiness | 4 | 0 |
| WS6 | Testing | 3 | 0 |

---

## 🟣 WS0 — Genericización del repo (prerequisito: quita acoplamiento a datos de un usuario)

> **Origen:** decisión del usuario (2026-06-03). El repo es un **producto general**, no la instalación de Test Owner. Los datos de cuentas reales son runtime (wizard → DB), no deben vivir en el source. Esto además **desbloquea hacer el repo público gratis** (resuelve C3 sin pagar ni exponer PII) y cierra de raíz el riesgo que el audit ya había visto una vez ("real account IDs leaked into frontend defaults", Phase 2 polish).

### [ ] G1 — Quitar toda info específica de usuario del repo (cuentas, nombres, email, % participación)

- **Severidad:** N/A (decisión de diseño / privacidad) · **Tipo:** 🟢 core · **Effort:** M · **WS:** WS0
- **Ubicación:** `CLAUDE.md` (tabla "Cuentas IBKR", retrospectivas), `docs/specs/*` (5 archivos), `docs/plans/*`, `docs/references/renta-cross-references.md`, `backend/tests/**` (IDs reales en fixtures/tests), `backend/tests/fixtures/xml/*_sanitized.xml`, `backend/scripts/sanitize_xml.py`
- **Estado:** ⏳ Pendiente

**Problema:** números de cuenta IBKR reales (`U155xxxxx`, `CS-xxxxxx`) en 15+ archivos commiteados, nombre/email en CLAUDE.md + 5 specs, porcentajes de participación reales (50% Test Owner). Acopla el producto a un usuario y bloquea hacer el repo público de forma segura. Está además en **53 commits de historial** (solo CLAUDE.md).

**Fix (2 capas):**
1. **HEAD:** reemplazar todos los IDs reales por placeholders consistentes (`U10000001`/`U10000002`/`U10000003`, `CS-100000-01`), genericizar la tabla "Cuentas IBKR" de CLAUDE.md a un ejemplo neutro + nota "las cuentas reales se configuran en runtime vía el wizard"; quitar email/nombre donde sea info de usuario (mantener autoría de git es OK). Verificar que los fixtures sanitizados usen IDs fake y que los tests que asertan contra IDs sigan verdes con los nuevos valores.
2. **Historial:** `git filter-repo` purgando los IDs/PII de todos los commits (reescribe SHAs — seguro ahora que sos el único clon; re-push `--force` + re-crear tags).

**Verificación:** `git grep -E 'U155[0-9]{5}|CS-[0-9]{6}|owner@gmail'` → 0 matches en HEAD; `git log -p | grep -E 'U155...'` → 0 en historial; `uv run pytest -q` sigue verde con los IDs nuevos.

**Desbloquea:** C3 (repo público gratis → branch protection sin pagar ni exponer).

---

## 🔴 HIGH — el bug que importa (gate absoluto antes de deploy)

### [ ] H1 — IDOR en el setup wizard: cualquier usuario se auto-asigna participación a cualquier cuenta

- **Severidad:** HIGH (verificada — **el verificador la MANTUVO en high**, no la bajó) · **Tipo:** 🟢 core · **Effort:** M · **WS:** WS1
- **Ubicación:** `backend/src/ibkr_control/api/setup.py:354-405` (step2/save), `:487-540` (step3/save_new_accounts), `:543-612` (step3/commit)
- **Estado:** ⏳ Pendiente

**Problema:** `step2/save`, `step3/save_new_accounts` y `step3/commit` validan los `ibkr_account_id` entrantes contra `select(Account)` **de toda la tabla compartida, sin filtro de ownership/participación**. El único gate per-user es `flex_imports_count > 0` (cualquier import del usuario sirve, no que *ese* account_id venga de *su* import). Luego `step2/save` hace `acc.alias = item.alias` (sobre la fila compartida) e inserta `Participation(user_id=current_user, account_id=acc.id, pct=item.pct)` para cualquier cuenta que exista globalmente.

**Impacto:** un segundo usuario autenticado (el contador, o cualquier cuenta auto-registrada — ver S1) puede llamar `step2/save` con `pct=100` contra los `U########` del dueño (IDs adivinables, además devueltos a cualquiera que detecte) y **auto-asignarse 100% de participación**, haciendo visibles los trades/lotes/cash del dueño vía el resolver `visible_account_ids` de Phase 3 — y sobrescribir aliases para todos. Es un gap horizontal-privilege / IDOR sobre la relación de ownership. **Anula el modelo authz que Phase 2.8 construyó** (participations = la relación de ownership).

**Fix:** scopear la validación de existencia de cuentas a las cuentas que aparecen en un `FlexImport` con `user_id == current_user` (o donde el usuario ya tiene participación), no `select(Account)` global. Lo mismo para la mutación de alias. Cierra las participaciones auto-otorgadas sin cambiar el modelo de cuentas-compartidas.

**Veredicto del verificador (HIGH, no overkill):** *"Cadena IDOR real que llega a datos financieros: `authz/scope.py:24-32` resuelve visibilidad SOLO desde filas Participation. Severidad MANTENIDA en HIGH — los datos en riesgo son registros fiscales/financieros completos de otro usuario, el exploit es de bajo esfuerzo (un POST con account_id adivinado + pct=100), y anula completamente el modelo authz multi-user. El fix es un cambio mínimo de scoping `WHERE`, sin infra externa."*

---

## 🟠 MEDIUM — gaps reales pre-deploy

### WS1 — Seguridad de la aplicación

### [ ] S1 — Registro de usuarios abierto en una app de 3 usuarios fijos

- **Severidad:** MEDIUM (verificada — bajada de high; el blast radius solo es account-creation, NO lectura de datos ajenos por sí solo) · **Tipo:** 🟢 core · **Effort:** S · **WS:** WS1
- **Ubicación:** `backend/src/ibkr_control/auth/router.py:12-16`, `frontend/src/app/(auth)/register/page.tsx`
- **Estado:** ⏳ Pendiente
- **Confianza:** ⭐ **flaggeado independientemente por 3 finders** (secrets, authn-authz, api-surface)

**Problema:** el register router de fastapi-users está montado incondicionalmente en `POST /api/auth/register` sin gate de superuser, invite ni allowlist. fastapi-users crea usuarios con `is_active=True` y `current_active_user` no exige verificación → un usuario auto-registrado es funcional al instante. El frontend incluso tiene una página pública `/register`. La membresía es fija y conocida (dueño, esposa, contador = 3 personas).

**Impacto:** una vez expuesto el host, cualquier visitante anónimo puede registrarse y obtener una cuenta activa (correr el wizard, guardar credenciales Flex, disparar ingests). **Compone con H1** para un camino anónimo → lectura de datos. Pura superficie de ataque sin upside.

**Fix:** quitar el register router en prod y seedear los 3 usuarios vía script one-off (o gate con `current_user(superuser=True)`). Quitar/ocultar la página `/register`. Si alguna vez se quiere self-service: invite token. Cambio de ~1 línea de router.

### [ ] S2 — Key de cifrado + JWT secret no validados al boot

- **Severidad:** MEDIUM · **Tipo:** 🟢 core · **Effort:** S · **WS:** WS1
- **Ubicación:** `backend/src/ibkr_control/config.py:11`, `backend/src/ibkr_control/ingest/flex/crypto.py:13`
- **Estado:** ⏳ Pendiente

**Problema:** `TOKEN_ENCRYPTION_KEY` se lee vía `os.environ` en el primer uso, no al boot; `jwt_secret` es un `str` plano sin chequeo de min-len ni de placeholder. Una key mala pasa el healthcheck y falla recién en el cron; un JWT secret placeholder/corto es forjable.

**Fix:** validar al boot la key (32-byte base64 decodificable) y el `jwt_secret` (min-len, no placeholder) con el mismo patrón `field_validator` que ya usás para el CORS guard. Falla loud al arranque, no tarde en el cron.

### [ ] S3 — Login sin rate-limit / lockout (brute-force ilimitado)

- **Severidad:** MEDIUM · **Tipo:** 🟢 core · **Effort:** M · **WS:** WS1
- **Ubicación:** `backend/src/ibkr_control/auth/router.py:7-11`, `auth/backend.py:16-20`
- **Estado:** ⏳ Pendiente

**Problema:** `/api/auth/jwt/login` es la ruta stock de fastapi-users sin throttling. Ya tenés cooldown DB-backed para `POST /api/ingest/trigger` pero nada protege la autenticación. Brute-force de password contra 3 emails conocidos es ilimitado.

**Impacto:** el camino más realista de compromiso de credenciales una vez público. También CPU-DoS barato (cada intento corre un hash verify).

**Fix:** limiter per-IP y/o per-email frente al login (SlowAPI liviano, o reusar el patrón de cooldown existente con un contador de intentos fallidos + backoff exponencial). DB o in-memory alcanza para 3 usuarios.

### [ ] S4 — XML Flex parseado con parser lxml default (entity resolution ON)

- **Severidad:** LOW (mitigado hoy solo por la versión de lxml) · **Tipo:** 🟢 core · **Effort:** S · **WS:** WS1
- **Ubicación:** `backend/src/ibkr_control/ingest/flex/parser.py:117`, `ingest/flex/client.py:275`, `:311`
- **Estado:** ⏳ Pendiente

**Problema:** todo el parsing usa `etree.fromstring(bytes)` con el parser default implícito (DTD/entity processing habilitado). Verificado empíricamente contra lxml 6.1.1: entidades internas SÍ se expanden. Hoy mitigado por el límite de amplificación de libxml2 y porque entidades SYSTEM externas no resuelven por default — pero esa seguridad es **implícita en la versión de la librería, no asertada en código**. Un downgrade/cambio futuro de lxml puede reabrirlo silenciosamente en el path de XML controlado por el usuario.

**Fix:** parser explícito a nivel módulo `etree.XMLParser(resolve_entities=False, no_network=True, dtd_validation=False, load_dtd=False)` pasado a cada `fromstring`. Cero cambio de comportamiento para XML legítimo de IBKR; elimina la dependencia de los defaults de libxml2.

### [ ] S5 — Sin security-headers ni TrustedHost middleware

- **Severidad:** LOW · **Tipo:** 🔵 contextual · **Effort:** S · **WS:** WS1
- **Ubicación:** `backend/src/ibkr_control/main.py:32-57`
- **Estado:** ⏳ Pendiente

**Problema:** `create_app()` solo agrega CORSMiddleware. No hay `X-Content-Type-Options: nosniff`, `frame-ancestors`/CSP, ni `TrustedHostMiddleware` pineando el Host esperado.

**Fix:** o setearlos en el reverse-proxy de Coolify/Traefik (documentar esa decisión) **o** un middleware mínimo que estampe `nosniff` + CSP `frame-ancestors 'none'` + `TrustedHostMiddleware` con el hostname de prod. HSTS lo maneja el proxy (TLS layer).

### [ ] S6 — Texto de excepción IBKR/red devuelto al cliente en validación de credenciales

- **Severidad:** LOW · **Tipo:** 🔵 contextual · **Effort:** S · **WS:** WS1
- **Ubicación:** `backend/src/ibkr_control/api/credentials.py:57-58`
- **Estado:** ⏳ Pendiente

**Problema:** `PUT /api/credentials/flex` hace `except Exception as e: raise HTTPException(502, detail=f"No pude alcanzar IBKR: {e}")`, forwardeando el string de la excepción subyacente (errores httpx de conexión/SSL/DNS, URLs internas) verbatim. El resto del código trunca a `[:500]` (setup.py, ingest.py) — este handler no.

**Fix:** mensaje genérico al cliente + log completo server-side, o como mínimo el mismo truncado `str(e)[:500]`. Considerar un exception handler global para que ningún 500 renderice tracebacks en prod.

### [ ] S7 — `/docs`, `/redoc`, `/openapi.json` públicos sin auth

- **Severidad:** LOW · **Tipo:** 🔵 contextual · **Effort:** S · **WS:** WS1
- **Ubicación:** `backend/src/ibkr_control/main.py:34`
- **Estado:** ⏳ Pendiente

**Problema:** FastAPI se instancia sin `docs_url=None/redoc_url=None/openapi_url=None`, así que `/docs`, `/redoc`, `/openapi.json` son públicos y enumeran todo endpoint, schema y regla de validación (incluyendo la ruta de registro abierta). Viven en root, no bajo `/api`, así que no los gatea nada.

**Fix:** desactivar en prod (`docs_url=None`, etc.). El frontend usa orval contra un `openapi.json` generado en build-time, así que la exposición en runtime no se necesita.

### [ ] S13 — Dos endpoints de upload bufferean el body entero antes del check de tamaño

- **Severidad:** MEDIUM · **Tipo:** 🔵 contextual · **Effort:** S · **WS:** WS1
- **Ubicación:** `backend/src/ibkr_control/api/setup.py:303-306` (step2/detect_from_xml), `:426-429` (step3/upload)
- **Estado:** ⏳ Pendiente

**Problema:** `api/imports.py:upload_xml` hace lo correcto (chequea `file.size`, luego streamea en chunks de 64KB con cap acumulado). Pero `step2/detect_from_xml` y `step3/upload` hacen `content = await file.read()` y recién ahí testean `len > max_xml_size`. El `read()` sin argumento bufferea el body entero a RAM antes del cap → el guard de 50MB se bypassea. `step3/upload` además mete los bytes en el `Step3Stash` in-memory (sin cap global de count/bytes).

**Fix:** aplicar el patrón de streaming-con-cap-acumulado ya probado en `imports.py:34-58` a ambos handlers. Opcional: cap de max-entries/max-bytes al `Step3Stash`.

### [ ] S9 — Wiring de `require_account_scope` / `visible_account_ids` (forward-looking, gate para Phase 3)

- **Severidad:** LOW (no es vuln activa hoy — los endpoints de datos son Phase 3) · **Tipo:** 🟢 core · **Effort:** S · **WS:** WS1 (nota para Phase 3)
- **Ubicación:** `backend/src/ibkr_control/authz/dependencies.py:13-23`, `authz/scope.py:51-62`
- **Estado:** ⏳ Pendiente (verificación/guardrail, no cambio ahora)

**Problema:** `require_account_scope` y `visible_account_ids` están definidos y unit-tested pero referenciados por **cero rutas** (grep confirma solo self-references). Es correcto hoy (las pantallas de lectura son Phase 3). El riesgo es forward: si Phase 3 agrega `GET /api/lots/*` y olvida depender de `require_account_scope`, el contador/segundo-usuario vería todos los datos.

**Fix:** sin cambio ahora. Cuando Phase 3 traiga los endpoints de lots, hacer `require_account_scope` dependency obligatoria en cada ruta de hechos + test asertando que un usuario sin grant recibe vacío/403. Considerar un **test de CI tipo grep** que verifique que toda ruta `/api/lots` incluye la scope dependency. Registrar como guardrail en el spec de Phase 3.

### [ ] S14 — JWT en localStorage → cookie httpOnly + CSRF (promovido de DF1)

- **Severidad:** MEDIUM (la aceptación de D3 expira al exponer a internet) · **Tipo:** 🔵 contextual · **Effort:** L · **WS:** WS1
- **Ubicación:** `frontend/src/lib/auth/storeToken.ts`, `frontend/src/lib/api/mutator.ts:9-16`, `frontend/src/proxy.ts:16-31`, backend transport (`auth/backend.py`)
- **Estado:** ⏳ Pendiente · **Decisión:** el usuario eligió adelantarlo (2026-06-03), no diferir a Phase 4

**Problema:** el access token se guarda en `localStorage` e inyecta como Bearer por el interceptor de axios. `proxy.ts` es un no-op documentado y el `baseURL` apunta directo al backend → no hay path de cookie httpOnly. D3 se aceptó con el rationale "app personal (sin usuarios no confiables)" + "sin exposición" — **ambas premisas cambian al exponer a internet** para el contador sobre DNS público.

**Impacto:** cualquier XSS (una dep con bug de inyección, un feature futuro que renderice data de IBKR/usuario sin escapar, un paquete npm comprometido) puede leer el token de localStorage y exfiltrarlo → account takeover completo, incluyendo el endpoint de rotación de credenciales Flex. Una cookie httpOnly lo haría ilegible para JS.

**Fix:** migrar el transport de fastapi-users de Bearer/localStorage a **cookie httpOnly + Secure + SameSite** + protección CSRF (double-submit token o SameSite=strict según el flujo). Tocar: backend transport (`CookieTransport` de fastapi-users), `mutator.ts` (quitar inyección de Bearer, `withCredentials: true`), `storeToken.ts` (eliminar), `proxy.ts` (resolver el no-op de Next 16 — ahora sí necesario para same-origin). Tests E2E del login/logout + un test de que el token no es legible desde JS.

### WS2 — Config de deploy / secrets

### [ ] D-CFG1 — `compose.coolify.yaml` omite `TOKEN_ENCRYPTION_KEY` y el DNS pin

- **Severidad:** MEDIUM (verificada — bajada de high; pre-deploy, fail loud documentado en Camino B) · **Tipo:** 🟢 core · **Effort:** S · **WS:** WS2
- **Ubicación:** `compose.coolify.yaml:23` (env block) — comparar con `compose.yaml:28` + DNS `:32-34`
- **Estado:** ⏳ Pendiente

**Problema:** el compose target de prod lista solo `DATABASE_URL`, `JWT_SECRET`, `JWT_LIFETIME_SECONDS`, `BACKEND_CORS_ORIGINS` — **sin `TOKEN_ENCRYPTION_KEY` y sin bloque `dns:`**. `crypto.py:14` hace `os.environ["TOKEN_ENCRYPTION_KEY"]` (bare dict access → KeyError sin fallback). Sin la var: el save de credenciales del wizard 500ea, y el cron Flex diario crashea silencioso. El DNS faltante es la clase SERVFAIL documentada (memoria `macos-docker-dns-resolver`).

**Fix:** agregar las 2 líneas (`TOKEN_ENCRYPTION_KEY` mapping + bloque `dns:`) espejando `compose.yaml`. Nota: Coolify puede inyectar la var desde la UI aunque falte en el compose, pero el lugar canónico es el compose — confiar en un paso manual de UI que el compose no declara es frágil.

### [ ] D-CFG2 — `compose.yaml` (genérico) publica `8000`/`3000` directo al host

- **Severidad:** LOW · **Tipo:** 🔵 contextual · **Effort:** S · **WS:** WS2
- **Ubicación:** `compose.yaml:35-36` (backend), `:55-56` (frontend)
- **Estado:** ⏳ Pendiente

**Problema:** `compose.yaml` mapea `8000:8000` y `3000:3000` al host (bind `0.0.0.0`). `compose.coolify.yaml` correctamente NO publica puertos (usa labels del proxy de Coolify). El riesgo es solo si alguien corre `compose.yaml` (target `make prod-local`/`coolify-local`) en el host expuesto en vez de vía Coolify.

**Fix:** comentario en `compose.yaml` aclarando que es solo para laptop local, nunca `docker compose up` en el host público. Opcional: bindear a `127.0.0.1:8000:8000`. Confirmar que el Hetzner Cloud Firewall solo permite 80/443 (defense-in-depth).

### WS3 — Observabilidad / resiliencia

### [ ] O1 — Sin config de logging: los INFO del cron se descartan en silencio, WARN/ERROR sin contexto

- **Severidad:** MEDIUM (verificada — bajada de high; uvicorn igual loguea errores de request, pero la narrativa del cron se pierde) · **Tipo:** 🟢 core · **Effort:** S · **WS:** WS3
- **Ubicación:** `backend/src/ibkr_control/__init__.py`, `main.py:32-57`, `Dockerfile:62`, `scheduler/jobs.py:13`, `ingest/flex/job.py:34`
- **Estado:** ⏳ Pendiente

**Problema:** no hay `logging.basicConfig`/`dictConfig`/`--log-config` en ningún lado. Todo módulo hace `getLogger(__name__)` y propaga a un root sin handler → cae en `logging.lastResort` (StreamHandler a stderr, fijo en WARNING, formato `%(message)s`). Consecuencias: (a) **todos los `logger.info` se descartan** — `jobs.py:136` "Registered 3 ingest jobs", cleanup counts, los skip de hash duplicado en `flex/job.py:106/214`; (b) los WARN/ERROR/exception SÍ salen pero **sin timestamp, sin level name, sin logger name** — solo el mensaje crudo.

**Impacto:** cuando el cron Flex skipea un usuario, expira un token o crashea un background task, el operador recibe nada (INFO) o una línea sin contexto que no puede correlacionar a un tiempo/job. Mina la historia de debugging "leé los logs del container" — el mismo riesgo de background-tasks que tu CLAUDE.md repite ("fire-and-forget = bug invisible").

**Fix:** setup one-time en `create_app()`: `logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')`, o un `--log-config` dictConfig a uvicorn. Texto estructurado con timestamp+level+logger alcanza para 3 usuarios (JSON opcional). ~10 líneas. **NO** agregar agregador externo.

### [ ] O2 — `/health` es un literal estático: nunca prueba DB ni scheduler (false-green)

- **Severidad:** MEDIUM (verificada — bajada de high; postgres tiene su propio pg_isready + IngestHealthBanner detecta stale) · **Tipo:** 🟢 core · **Effort:** S · **WS:** WS3
- **Ubicación:** `backend/src/ibkr_control/main.py:45-47`, `compose.yaml:40-46`
- **Estado:** ⏳ Pendiente

**Problema:** el endpoint de liveness cableado al healthcheck de Docker (`compose.yaml:41` → `curl /health`) devuelve un `{"status":"ok"}` hardcodeado, sin query a DB ni inspección del scheduler. El `/api/health/ingest` más rico requiere auth (no sirve de probe) y solo lee `ingest_log` (no chequea el pool ni el thread del scheduler).

**Impacto:** si Postgres se vuelve inalcanzable, el pool se agota, o el thread de APScheduler muere, el proceso sigue respondiendo `/health` 200 → Coolify/Docker `restart: unless-stopped` nunca reinicia → container verde mientras los ingests paran en silencio.

**Fix:** `/health` (o `/health/ready`) ejecuta un `SELECT 1` barato contra el engine async + opcionalmente asserta `scheduler.running` (exponer el scheduler vía `app.state` en el lifespan). Devolver 503 al fallar para que el healthcheck flipee y el restart kicke. Sin auth, rápido. ~15 líneas.

### [ ] O3 — Engine async sin `pool_pre_ping`/`pool_recycle`: conexiones muertas tras restart de Postgres

- **Severidad:** MEDIUM · **Tipo:** 🟢 core · **Effort:** S · **WS:** WS3
- **Ubicación:** `backend/src/ibkr_control/db/session.py:14-16`, `scheduler/jobs.py:36`, `:70`
- **Estado:** ⏳ Pendiente

**Problema:** todos los engines async se crean con defaults (`create_async_engine(url, echo=False)`), sin `pool_pre_ping=True` ni `pool_recycle`. En un host donde Postgres y backend comparten red Docker, un restart de Postgres (upgrade/OOM/reboot) deja el pool con conexiones TCP muertas. Sin pre-ping, la próxima request/cron que saca una conexión stale tira error en vez de reconectar transparente.

**Impacto:** tras cualquier bounce de Postgres, las primeras request(s) y el próximo cron tick fallan con error de conexión crudo hasta que el pool cicla. Para los crons diarios = una corrida perdida que aparece solo como fila `failed` en `ingest_log`.

**Fix:** `pool_pre_ping=True` (+ `pool_recycle=1800`) en el engine de `db/session.py` y en los dos de `scheduler/jobs.py`.

### WS4 — CI/CD & automatización

### [ ] C1 — Backend CI corre `pytest` pero no `ruff check` ni `ruff format --check`

- **Severidad:** MEDIUM · **Tipo:** 🟢 core · **Effort:** S · **WS:** WS4
- **Ubicación:** `.github/workflows/ci.yml:24-27`
- **Estado:** ⏳ Pendiente

**Problema:** el job backend solo corre `cd backend && uv run pytest -v`. Tu CLAUDE.md define el bar como `ruff check .` limpio + `ruff format --check .` 0 drift, y el checklist pre-Phase-3 los verifica a mano. Ninguno corre en CI → drift de lint/formato se acumula silencioso.

**Fix:** 2 steps al job backend tras `uv sync`: `uv run ruff check .` + `uv run ruff format --check .` (desde `backend/`). Rápidos, sin DB.

### [ ] C2 — Frontend CI corre `pnpm build` pero no `pnpm lint` ni `pnpm test:run` (vitest)

- **Severidad:** MEDIUM (verificada — bajada de high; el maintainer corre los gates a mano por fase, red de seguridad humana) · **Tipo:** 🟢 core · **Effort:** S · **WS:** WS4
- **Ubicación:** `.github/workflows/ci.yml:41-48`
- **Estado:** ⏳ Pendiente
- **Confianza:** ⭐ flaggeado por 2 finders (cicd, testing)

**Problema:** el job frontend solo corre `pnpm build`. Existen 2 suites vitest (7 `it()`: IngestHealthBanner + IngestHealthTable), 6 specs Playwright, y un eslint flat config — **ninguno corre en CI**. `next build` typechequea pero no corre una sola aserción ni regla de lint. La lógica de severidad del health-banner y el wizard happy-path + IBKR-busy fallback pueden regresar y mergear con check verde. Es exactamente la clase de bug cross-stack (wire format backend vs consumer frontend) que el CLAUDE.md marca como hazard recurrente (incidente TRM SSE).

**Fix:** agregar `pnpm lint` + `pnpm test:run` (vitest non-watch) al job frontend. Playwright e2e como job separado (necesita backend+postgres; gatear a PRs-a-main si el runtime preocupa). E2E puede quedar fuera de CI por D4 (necesita IBKR mocks) — eso está OK.

### [ ] C3 — CI advisory-only: sin branch protection, un run rojo no bloquea el merge a main

- **Severidad:** MEDIUM · **Tipo:** 🟢 core · **Effort:** S · **WS:** WS4
- **Ubicación:** `.github/workflows/ci.yml:3-6`
- **Estado:** ⏳ Pendiente

**Problema:** el workflow dispara en push-a-main y pull_request, pero el repo es privado free-tier → `branches/main/protection` devuelve 403 "Upgrade to GitHub Pro or make public". No hay required-status-check → un run fallido (o un push directo a main que rompe tests) no previene el estado roto. El workflow del equipo (commits frecuentes directos a main, per git log) lo hace concreto: CI corre pero es puramente informativo.

**Resolución (decidida 2026-06-03):** hacer el **repo público** tras completar G1 (genericización + scrub de historial) → desbloquea rulesets/branch protection **gratis**, con required status checks reales. Sin costo y sin exposición de PII (G1 la elimina). **Depende de G1** — no hacer público antes de que el scrub esté verificado.

### [ ] C4 — Sin automatización de updates de dependencias (Dependabot/Renovate)

- **Severidad:** MEDIUM · **Tipo:** 🟢 core · **Effort:** S · **WS:** WS4
- **Ubicación:** repo-wide (sin `.github/dependabot.yml`, sin `renovate.json`)
- **Estado:** ⏳ Pendiente
- **Confianza:** ⭐ flaggeado por 2 finders (deps, cicd)

**Problema:** nada surfacea deps transitivas con CVE en el tiempo. El snapshot actual está fresco (proyecto nuevo) pero no hay mecanismo de mantenerlo. Para una app "long-term" en un host, las deps driftean detrás de los parches de seguridad sin señal.

**Fix:** `.github/dependabot.yml` mínimo con 2 ecosistemas (`pip`/uv en `/backend`, `npm` en `/frontend`), cadencia semanal, agrupando minor/patch en un PR. Habilitar también las Dependabot security alerts en settings. Set-and-forget; los PRs los valida el pytest/build existente.

### [ ] C5 — Sin secret scanning antes del primer deploy de una app que maneja keys AES-GCM + credenciales IBKR

- **Severidad:** MEDIUM · **Tipo:** 🟢 core · **Effort:** S · **WS:** WS4
- **Ubicación:** repo-wide (sin `.gitleaks.toml`, sin workflow gitleaks/trufflehog)
- **Estado:** ⏳ Pendiente

**Problema:** el threat model entero gira sobre `TOKEN_ENCRYPTION_KEY`, `JWT_SECRET`, `DATABASE_URL`, tokens Flex IBKR. Una key leakeada en un commit (un `.env` staged por accidente, un fixture con token real) sería catastrófica y hoy queda indetectada. El codebase ya tuvo IDs de cuenta reales leakeados a defaults del frontend una vez (fixed en Phase 2 polish).

**Fix:** (1) habilitar GitHub free secret scanning + push protection en settings (disponible en repos privados free para patterns partner), y/o (2) un step gitleaks (`gitleaks/gitleaks-action`) en el workflow que escanee en cada push/PR. Low-effort, zero-infra.

### [ ] C6 — CI sin step de vulnerability scanning de dependencias

- **Severidad:** MEDIUM · **Tipo:** 🟢 core · **Effort:** S · **WS:** WS4
- **Ubicación:** `.github/workflows/ci.yml` (solo pytest + build)
- **Estado:** ⏳ Pendiente

**Problema:** nada falla el build cuando una dep desarrolla un CVE conocido. Para una app internet-facing que descifra credenciales IBKR e ingiere XML/JSON externo, un CVE regresado en lxml/cryptography/starlette podría pasar meses inadvertido. El "world-class supply chain" pedido descansa en que esto sea automatizado, no manual.

**Fix:** job de audit en `ci.yml`: backend `uvx pip-audit` (o `uv export | osv-scanner -`) + frontend `pnpm audit --audit-level=high`. Correr en PR + en un `schedule:` semanal (los CVEs nuevos surfacean sin cambios de código). Allowlist documentado para el postcss transitivo conocido (ver C7) para mantener el job verde.

### [ ] C7 — CVE moderado en postcss 8.4.31 transitivo (pineado por Next.js)

- **Severidad:** LOW · **Tipo:** 🔵 contextual · **Effort:** S · **WS:** WS4
- **Ubicación:** `frontend/pnpm-lock.yaml:3302`, `:7065`
- **Estado:** ⏳ Pendiente

**Problema:** `pnpm audit` reporta 1 moderate: postcss <8.5.10 tiene un XSS de CSS-stringify (GHSA-qx2v-qp2m-jg93). El lockfile resuelve postcss dos veces — 8.5.15 (patched, usado por @tailwindcss/postcss) y 8.4.31 (vulnerable, pineado como dep directa de `next@16.2.6`). Impacto casi-nulo en práctica: requiere correr postcss sobre CSS controlado por atacante; esta app solo procesa su propio Tailwind/styled-jsx en build-time, sin path de CSS de usuario.

**Fix:** `overrides` de pnpm forzando `postcss@^8.5.10` + re-`pnpm install`/`build` para confirmar que Next sigue buildeando. Si genera fricción, documentar como deuda V1 aceptada con el rationale "sin CSS de usuario" — pero aceptación **consciente y escrita**, no un unknown sin escanear. Auto-visible una vez exista C6.

### [ ] C8 — Backend job no cachea el env de uv (velocidad/robustez de CI)

- **Severidad:** LOW · **Tipo:** 🔵 contextual · **Effort:** S · **WS:** WS4
- **Ubicación:** `.github/workflows/ci.yml:13-23`
- **Estado:** ⏳ Pendiente

**Problema:** el job backend corre `uv sync --frozen` sin cache (el frontend sí cachea pnpm). Cada run re-resuelve y descarga el árbol entero → más lento y más expuesto a flakiness transitoria de PyPI (runs rojos espurios).

**Fix:** `enable-cache: true` al step `astral-sh/setup-uv` (cache built-in keyed en `uv.lock`). Una línea.

### [ ] C9 — Sin `concurrency` group: pushes superados siguen corriendo

- **Severidad:** LOW · **Tipo:** 🔵 contextual · **Effort:** S · **WS:** WS4
- **Ubicación:** `.github/workflows/ci.yml:1-8`
- **Estado:** ⏳ Pendiente

**Problema:** sin bloque `concurrency:`. Pushes sucesivos rápidos spawnan un run completo cada uno (testcontainers + build) sin auto-cancel del run en vuelo. Gasta minutos free-tier y un run viejo más lento puede reportar status después de un commit nuevo.

**Fix:** `concurrency: { group: ${{ github.workflow }}-${{ github.ref }}, cancel-in-progress: true }` al tope del workflow.

### [ ] C11 — Sin coverage gate en CI: el 86% es informativo, las regresiones no fallan el build

- **Severidad:** MEDIUM · **Tipo:** 🔵 contextual · **Effort:** S · **WS:** WS4
- **Ubicación:** `.github/workflows/ci.yml:24-27`, `backend/pyproject.toml` (sin `--cov-fail-under`, sin `[tool.coverage]`)
- **Estado:** ⏳ Pendiente

**Problema:** CI corre `pytest -v` sin `--cov` ni threshold. Un PR que baja coverage en un path crítico (ej. borra un test negativo de authz) mergea verde mientras los tests restantes pasen. El 86% queda sin enforcement → erosión silenciosa de la cobertura auth/authz/crypto que es la fortaleza principal de la suite.

**Fix:** `--cov=src/ibkr_control --cov-report=term-missing --cov-fail-under=85` en la invocación pytest de CI (o `[tool.pytest.ini_options] addopts`). Floor en el 86% actual para que ratchee, no un número aspiracional. 100% sería overkill. **Nota:** `api/grants.py` reporta 51% por un artefacto de atribución del fixture ASGI (ver T4) — está bien tested; excluirlo de cualquier threshold per-file o correr coverage con el mismo app in-process.

### WS5 — Arquitectura / Phase-3 readiness

### [ ] A1 — Lógica de negocio (participation SCD-2 + account upsert) inline en handlers y duplicada

- **Severidad:** MEDIUM · **Tipo:** 🟢 core · **Effort:** S · **WS:** WS5
- **Ubicación:** `backend/src/ibkr_control/api/setup.py:377-405`, `:494-538`
- **Estado:** ⏳ Pendiente

**Problema:** la lógica SCD-2 de participations (encontrar fila abierta, cerrarla con `valid_to=today`, insertar nueva con el pct nuevo) + el get-or-create de account está hand-written inline en `step2_save` y otra vez en `step3_save_new_accounts`. Los dos bloques son casi idénticos (385-405 vs 518-538). Es domain logic genuino — el modelado temporal de participaciones que `domain/participation.py` (`apply_pct`) y el FIFO/classification de Phase 3 van a necesitar — sentado en handlers HTTP sin seam de servicio.

**Impacto:** Phase 3 agrega un domain layer que debe leer/escribir participations consistentemente. Con el write path duplicado en 2 handlers y sin función compartida, el domain layer o re-implementa una 3ra copia o las 3 driftean. Es justo la fundación que Phase 3 quiere limpia.

**Fix:** extraer un helper de write de participations (`db/participations.py` o futuro `domain/participation.py`): `upsert_participation(session, user_id, account_id, pct, at)` + `ensure_account(session, ibkr_id)`. Ambos handlers lo llaman y el domain layer de Phase 3 consume el mismo primitive. Handlers quedan en validación + orquestación.

### [ ] A2 — Singletons in-memory correctos solo bajo invariante de 1-proceso no documentado

- **Severidad:** MEDIUM · **Tipo:** 🔵 contextual · **Effort:** S · **WS:** WS5
- **Ubicación:** `backend/src/ibkr_control/api/_step3_stash.py:26-85`, `ingest/job_tracker.py:1-102`, `Dockerfile:62`, `main.py:20-30`
- **Estado:** ⏳ Pendiente

**Problema:** `Step3Stash` y `JobTracker` son singletons de módulo per-proceso. El SSE depende de que el background task y el `/stream/{job_id}` compartan una instancia de `JobTracker`; el wizard step3 depende de un `Step3Stash`. El CMD de prod corre un solo `uvicorn` sin `--workers`. Hoy está bien — pero el invariante "exactamente 1 proceso" es load-bearing y solo está en comentarios de código, no en el deploy boundary. Un `--workers 2` futuro (instinto natural de escalar) rompe **en silencio**: cada worker arranca su propio APScheduler (sin lock inter-worker) → crons doble-firean; los SSE pegan al JobTracker vacío del worker equivocado; el step3 stasheado en worker A es invisible al commit en worker B.

**Fix:** documentar el requisito de proceso único en el deploy boundary (comentario en el CMD del Dockerfile y/o CLAUDE.md: "DEBE correr exactamente 1 proceso — scheduler/JobTracker/Step3Stash son in-process; no agregar `--workers` ni replicas sin mover el scheduler a un job con leader-election/lock y el stash/tracker a DB/Redis"). Sin cambio de código para 3 usuarios — solo hacer el invariante explícito para que no se viole por accidente.

### [ ] A3 — Sin React error boundaries: las pantallas de datos de Phase 3 no tienen UI de fallo

- **Severidad:** MEDIUM · **Tipo:** 🔵 contextual · **Effort:** S · **WS:** WS5
- **Ubicación:** `frontend/src/app/(app)/layout.tsx`, `frontend/src/app/layout.tsx`
- **Estado:** ⏳ Pendiente

**Problema:** no hay `error.tsx`, `global-error.tsx` ni ErrorBoundary en ningún lado (grep = 0 matches). La app depende enteramente del error handling per-component de TanStack Query + el interceptor 401 de axios. Phase 3 agrega 3 pantallas data-heavy (Lotes Abiertos/Cerrados/Alertas) que fetchean y renderizan datos fiscales derivados que pueden tirar en render (un Decimal malformado, un null inesperado en un cálculo 730d).

**Impacto:** un error de render no atrapado en una tabla de lots = overlay default en dev y **pantalla blanca sin recuperación en prod** — malo para una herramienta de la que dueño/esposa/contador dependen para decisiones fiscales. Invisible hoy solo porque las pantallas son stubs.

**Fix:** `app/(app)/error.tsx` (route-segment error boundary) + opcional `global-error.tsx` **antes** de construir las pantallas de Phase 3, para que un fallo de render en una tabla muestre una card recuperable en vez de página blanca.

### [ ] A5 — Comentario stale contradictorio en el check de ownership del SSE stream (rot D1)

- **Severidad:** LOW · **Tipo:** 🟢 core · **Effort:** S · **WS:** WS5
- **Ubicación:** `backend/src/ibkr_control/api/ingest.py:133`
- **Estado:** ⏳ Pendiente

**Problema:** la línea 133 dice "NOTE: V1 no verifica que job_id pertenezca al usuario — single-user." pero las líneas 138-139 SÍ verifican ownership y 404ean en mismatch (el fix D1 reciente). El comentario pre-fix nunca se borró → ahora contradice directamente el código de abajo. Rot de documentación justo en el spot security-sensitive.

**Fix:** borrar la línea stale 133; el comentario D1 correcto de las líneas 135-137 ya documenta bien el behavior.

### WS6 — Testing

### [ ] T1 — Cuerpos de los jobs del scheduler sin tests (fan-out multi-user + aislamiento de errores) — 30%

- **Severidad:** MEDIUM (verificada — bajada de high; código visiblemente correcto, es regression-protection) · **Tipo:** 🟢 core · **Effort:** M · **WS:** WS6
- **Ubicación:** `backend/src/ibkr_control/scheduler/jobs.py:16-92`, `backend/tests/test_scheduler.py:18-127`
- **Estado:** ⏳ Pendiente

**Problema:** coverage de `scheduler/jobs.py` = 30%; las líneas faltantes (28-57, 67-79, 87-92) son los cuerpos enteros de `_run_flex_for_all_users`, `_run_trm_global`, `_cleanup_old_jobs`. `test_scheduler.py` solo asserta **registración** (trigger times, ids, max_instances, coalesce, misfire, jobstore). El aislamiento de errores 4-vías del loop per-user (`LockHeldError`→warn+skip, `FlexAuthError`→log+continue, `httpx.HTTPError`→log+continue, broad `Exception`→log+continue sin matar el loop, jobs.py:44-55) **nunca se ejercita**.

**Impacto:** este es el cron diario desatendido que es el valor entero de la app para un hogar multi-user. El riesgo que el código está escrito para manejar — el token Flex inválido de un usuario o un IBKR 5xx transitorio NO debe frenar el ingest de los demás ni crashear el scheduler — es el behavior con cobertura cero. Un refactor que mueva el boundary del `try` o deje escapar una excepción frenaría el ingest de todos tras el primer fallo, sin que nada lo atrape hasta que alguien note datos viejos días después. El rationale de D11 ("needs live infra") **no aplica**: las ramas de error-isolation son alcanzables monkeypatcheando `flex_job.run`, sin red ni DB.

**Fix:** tests que monkeypatchean `ibkr_control.ingest.flex.job.run` + la query de `FlexCredentials` para correr `_run_flex_for_all_users` con (a) 2 usuarios donde el primero tira `FlexAuthError`/`HTTPError`/`LockHeldError`/`Exception` genérica y assert que el segundo igual corre, y (b) assert que ninguna excepción se propaga. Igual con `_run_trm_global`. Deja los IBKR calls reales fuera de scope.

### [ ] T2 — Defensa de oversize por streaming (Content-Length mentido/ausente) sin test

- **Severidad:** LOW · **Tipo:** 🔵 contextual · **Effort:** S · **WS:** WS6
- **Ubicación:** `backend/src/ibkr_control/api/imports.py:43-55`, `backend/tests/api/test_imports.py:72-79`
- **Estado:** ⏳ Pendiente

**Problema:** `imports.py` tiene 2 checks de tamaño: el 413 barato por `file.size` (Content-Length) y el cap acumulado del chunk loop (existe precisamente porque "el cliente puede mentir/omitir Content-Length"). El único test cubre el path barato. El path defense-in-depth de streaming — donde `file.size` es `None` y el cap debe tripear mid-stream — no tiene test (coverage muestra imports.py 69%, líneas 66-99 missed).

**Fix:** test que postea un multipart sin/bajo Content-Length pero con body que excede `max_xml_size_bytes` (o bajar `max_xml_size_bytes` vía settings) y assert 413 desde la rama de streaming. Cierra el hueco de cobertura de un guard de seguridad.

### [ ] T4 — `api/grants.py` reporta 51% pero está bien tested (artefacto de atribución, no gap real)

- **Severidad:** LOW (informativo — **no es gap, es nota para no re-flaggearlo**) · **Tipo:** 🟢 core (documentación) · **Effort:** S · **WS:** WS6
- **Ubicación:** `backend/src/ibkr_control/api/grants.py:1-110`, `backend/tests/test_grants_api.py:1-136`
- **Estado:** ⏳ Pendiente (solo documentar)

**Problema:** el coverage report muestra `api/grants.py` 51% (missing 26-41 create body, 62-82 list body), que a primera vista parece un CRUD de authz sin testear. En realidad `test_grants_api.py` tiene 4 tests que ejercitan create (201), list (serialización granted+received con dirección grantor/grantee correcta), delete (204), unknown-email 404, self-grant 400, y un flow end-to-end de aislamiento del contador. El número bajo es **artefacto de cómo el fixture ASGI `client` ejecuta los handlers**, no tests ausentes.

**Fix:** sin trabajo de test. Documentar en el spec/CI que el número de grants.py subestima la realidad; si se agrega coverage gate (C11), excluirlo del threshold per-file o correr coverage con el app in-process. Registrado acá para que no se re-flaggee como gap en futuras auditorías.

---

## ⚖️ Aceptado V1 — decisiones conscientes (NO se implementan)

> Estos findings son técnicamente ciertos pero `possibly-overkill` para una app personal de 3 usuarios en un host. Se registran como **decisión consciente** (no olvido). Revisar solo si la app crece a más usuarios o cambia el threat model.

| ID | Finding | Por qué se acepta | Re-evaluar si... |
|---|---|---|---|
| AC1 | **Sin revocación JWT / denylist** (`auth/backend.py`) | JWT stateless 1h; el lifetime corto es la mitigación correcta. Una tabla de denylist es overkill para 3 usuarios. | Hay un incidente real de token robado |
| AC2 | **Sin verificación de email** (`auth/manager.py`) | Con registro cerrado (S1) los 3 usuarios se provisionan a mano; verificación agrega SMTP infra para nada. | Se reabre self-service registration |
| AC3 | **Sin error-tracking externo (Sentry-like)** | Los fallos de ingest (la clase operacionalmente importante) ya quedan en `ingest_log` + banner UI. Sentry para 3 usuarios = ceremonia. O1 (logging) cubre los tracebacks de request-path. | La app se expone a más usuarios |
| AC4 | **Sin request/job correlation IDs** | La app ya threadea `user_id` en casi todos los mensajes de log. Para 3 usuarios y baja concurrencia es molestia menor, no blocker. | Concurrencia alta hace los logs ilegibles |
| AC5 | **Base images por tag flotante, no digest** | Los lockfiles (uv.lock, pnpm-lock) ya pinean la capa de app determinísticamente; solo flota la base OS/runtime. Reproducibilidad nice-to-have, no security must. | Se quiere build 100% reproducible (Dependabot puede automatizar los digest pins) |
| AC6 | **Frontend container sin healthcheck Docker** | Coolify hace sus propios checks vía proxy; un frontend trabado para 3 usuarios es un debug manual, no outage con blast radius. | — |
| AC7 | **BackgroundTasks no draineados al shutdown** | Jobs idempotentes (SAVEPOINT + UPSERT por natural key) → un kill mid-run deja una fila `running` colgada pero el próximo cron rehace seguro. Sin corrupción. | Aparece data corruption por shutdowns |
| AC8 | **`grantee_email` como `str` sin `EmailStr`** | Lookup parametrizado con 404 uniforme → sin injection ni enumeración. Nit de higiene de input, impacto negligible. | Trivial — se puede hacer junto a otro cambio de `_schemas.py` |
| AC9 | **Sin pre-commit hooks** | Una vez que CI corre lint/format/secret-scan (C1/C2/C5), el pre-commit es conveniencia, no requisito. | El equipo crece y los runs rojos de CI molestan |
| AC10 | **Scheduler crea engines vía `create_async_engine` directo (no el seam `get_engine`)** | Parcialmente intencional (engine throwaway per-run para no holdear conexiones idle entre crons diarios). Inconsistencia cosmética. | Se agrega statement_timeout/pool tweak que deba aplicar a todos |
| AC11 | **Sin test de JWT expirado/tampered** | La expiry la maneja fastapi-users (bien tested upstream); una misconfig de lifetime no la atraparía este test igual. | Cheap insurance — se puede agregar si sobra tiempo |

---

## 🟦 Diferido — a Phase 3 / Phase 4

| ID | Finding | Adónde | Por qué |
|---|---|---|---|
| ~~DF1~~ → **S14** | **D3: JWT en localStorage → httpOnly cookie + CSRF** | **PROMOVIDO al hardening (WS1)** — ver S14 abajo | Decisión del usuario (2026-06-03): adelantarlo. La aceptación de D3 ("sin usuarios no confiables, sin exposición") expira al exponer a internet; se cierra el riesgo XSS *antes* del deploy en vez de diferirlo. |
| DF2 | **Wiring de `require_account_scope` en endpoints de datos** (ver S9) | **Phase 3** | Los endpoints de hechos (lots) no existen hasta Phase 3. El guardrail (toda ruta `/api/lots` depende de `require_account_scope` + test de denial + test CI grep) se incorpora al spec de Phase 3. |

---

## Notas de implementación

- **Orden sugerido:** **WS0 (G1 genericización + scrub de historial) primero** — desbloquea el repo público gratis y limpia la PII antes de cualquier otra cosa. Luego WS1-H1 + S1 (el camino crítico compuesto), WS2 (deploy config, barato), WS3 (observabilidad), **WS4 (CI/CD — habilita los gates que protegen todo lo demás; hacer público + branch protection acá)**, WS5 (arquitectura Phase-3-readiness), WS6 (testing). S14 (cookie auth, effort L) puede ir en su propia tanda dentro de WS1.
- **TDD:** cada item con cambio de comportamiento → failing test → impl → passing test → commit (convención del repo).
- **Método de ejecución:** subagent-driven-development (mismo que Phase 2.7/2.8/2.9), o ejecución directa por workstream.
- **Verificación final:** `cd backend && uv run pytest -q` (debe subir de 313) + `ruff check .` + `ruff format --check .` + `cd frontend && pnpm lint && pnpm test:run && pnpm build`.
- **No marcar un item ✅ sin:** test verde + comando de verificación corrido + commit SHA registrado.
