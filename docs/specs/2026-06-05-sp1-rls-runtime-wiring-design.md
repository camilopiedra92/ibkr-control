# SP1 follow-up — RLS runtime wiring (connect the safety net)

**Fecha:** 2026-06-05
**Branch destino:** `saas/sp1-rls-runtime-wiring` (desde `main`, post-merge SP1 + SP1-hardening)
**Depende de:** SP1 (PR #5) + SP1-hardening (PR #6) — ambos en `main`.
**Estado:** spec aprobado, pendiente plan.

## 1. Problema

SP1 construyó RLS multi-tenant de clase mundial — políticas `org_isolation` con
`FORCE ROW LEVEL SECURITY`, rol `app_rls` sin bypass, `SET LOCAL app.current_org`
por request, default-deny sin contexto, función `system_credentialed_org_ids()`
SECURITY DEFINER para la enumeración control-plane del cron. La suite de tests
corre **bajo RLS como `app_rls`** y valida el aislamiento cross-tenant.

**Pero la app desplegada se conecta como el superusuario bootstrap, no como
`app_rls`.** En los tres entornos:

```
.env                  DATABASE_URL=postgresql+asyncpg://ibkr:changeme@postgres:5432/ibkr_control
compose.coolify.yaml  DATABASE_URL: ...://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/...
backend/Dockerfile    CMD ["sh","-c","uv run alembic upgrade head && exec uv run uvicorn ..."]
session.py            get_engine() -> create_async_engine(get_settings().database_url)
```

`POSTGRES_USER` (=`ibkr`) es el **superusuario bootstrap** que crea la imagen
oficial de Postgres. En Postgres, un superusuario (y cualquier rol con
`BYPASSRLS`) **ignora RLS incondicionalmente** — `FORCE ROW LEVEL SECURITY` solo
somete al *owner* de la tabla a sus políticas, no al superusuario.

`grep` sobre `src/` confirma: ningún path de runtime se conecta como `app_rls`
ni hace `SET ROLE`. `app_rls` existe **solo en `tests/conftest.py`** (líneas 99,
212, que construyen el DSN `app_rls` vía `swap_dsn_credentials`).

### Consecuencia

- En dev y en Coolify, las políticas RLS **no se evalúan**: el GUC que
  `apply_org_context` setea no lo lee nadie porque la política que lo consume
  está bypasseada para el superusuario.
- El aislamiento de tenant en runtime descansa hoy **100% en que cada query
  recuerde su `WHERE organization_id = org_id`** — precisamente la fragilidad
  que RLS existe para eliminar (defensa en profundidad). La mayoría de los
  endpoints sí filtran explícitamente (mitigante real), pero un filtro olvidado
  = leak cross-tenant silencioso, y las escrituras del persister / tablas de
  lotes que delegan en RLS quedan sin el segundo cinturón.
- **Brecha de test/prod parity:** la suite valida el aislamiento en una
  configuración (`app_rls`) que producción no usa → CI verde, falsa confianza.
  Es la misma lección que el proyecto ya documentó ("tests por unidad NO atrapan
  drift de contrato cross-stack", "fire-and-forget = bug invisible").

No es legacy ni un atajo consciente: es el **último cable sin conectar** de la
pieza central de SP1.

## 2. Principio rector

El error de fondo no es "falta cambiar un DSN" — es que **el proceso
long-running de la app posee y usa credenciales que bypassean RLS**. La
arquitectura de clase mundial elimina esa posibilidad *por construcción*: el
proceso de la app **nunca debe siquiera poseer** credenciales con privilegio de
bypass. Todo el diseño deriva de ahí.

## 3. Decisiones

### D1 — Separación de privilegios por contenedor, no por env var (LOCKED)

La separación de privilegios se hace a nivel **proceso/contenedor**, no
degradando un DSN en runtime:

- **Servicio `migrate` (one-shot):** corre `alembic upgrade head` con
  `DATABASE_URL` = DSN del **owner** (`ibkr`). Crea schema, el rol `app_rls`,
  políticas RLS y la función SECURITY DEFINER. Termina (`restart: "no"`).
- **Servicio `backend` (long-running):** corre **solo** `uvicorn` con
  `DATABASE_URL` = DSN de **`app_rls`**. `depends_on: { migrate: { condition:
  service_completed_successfully } }`. Este contenedor **jamás** tiene las
  credenciales del owner en su env.

Rechazado "derivar el DSN de app_rls desde un único DATABASE_URL owner": con la
derivación, el proceso de la app tendría las creds del owner en su env/memoria y
solo se *comprometería* a no usarlas. La separación por contenedor las elimina
físicamente → blast radius mínimo, y la frontera de privilegio es explícita y
auditable en el compose.

`alembic/env.py` y `db/session.py` siguen leyendo `DATABASE_URL` **sin cambios**
— cada uno corre en su contenedor con el DSN correcto para su rol. Cero
derivación, cero "magia". La única config compartida es `APP_RLS_PASSWORD`
(secret), que el servicio `migrate` usa para CREAR el rol y el DSN de `app_rls`
del servicio `backend` embebe para CONECTARSE — ya es el SSOT de la password
(`db/rls.py::app_rls_password()`).

Aplica a: `compose.yaml`, `compose.dev.yaml`, `compose.coolify.yaml`,
`backend/Dockerfile` (el `CMD` deja de encadenar migrate+serve), `Makefile`,
`.env` / `.env.example`.

### D2 — El owner/migrador sigue siendo el superusuario bootstrap (LOCKED, scope cut explícito)

El owner/migrador **sigue siendo `POSTGRES_USER`** (superusuario bootstrap) en
este spec. Lo que mueve la aguja de seguridad es que **la app sea `app_rls`**: el
rol migrador no recibe tráfico de requests, corre DDL en un paso controlado y
efímero (servicio `migrate`), y nunca está en el env del proceso long-running. La
función SECURITY DEFINER necesita un owner exento de RLS, que el superusuario ya
provee (asunción que el diseño de SP1-hardening ya documenta).

El split de 3 roles —bootstrap-superuser que *solo* crea el rol owner;
owner/migrador = rol **non-superuser** con `CREATEROLE`+`BYPASSRLS`; `app_rls`=
app— es el siguiente nivel y pertenece a **SP4** (capa secrets/roles/KMS que el
roadmap ya difiere): requiere provisioning de roles vía init acoplado al ciclo de
vida del volumen Postgres. Hacerlo acá adelantaría SP4 y agregaría maquinaria de
provisioning fuera de alcance. **No es deuda — es trabajo correctamente
secuenciado.** Se registra como ítem explícito de SP4.

### D3 — Boot guard fail-closed (LOCKED)

En el `lifespan` de `main.py`, antes de servir tráfico, la app consulta su propio
rol contra su propio engine:

```sql
SELECT current_setting('is_superuser')::bool AS is_su,
       COALESCE((SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user), false) AS bypass
```

Si `is_su` **o** `bypass` es true → **raise (la app no levanta).** Cierra la
clase entera de bug de forma permanente: es imposible deployar con RLS apagado
sin que el boot falle ruidosamente. Es el control que hubiera atrapado el bug
actual el día cero.

- Mensaje de error explícito nombrando el rol actual y por qué se rechaza.
- Corre una sola vez, en startup, contra `get_engine()` (el mismo engine que sirve
  requests) → prueba exactamente el rol que usará el tráfico.
- Severidad: **refuse-to-boot**, no warning. Un guard de aislamiento de tenant
  que solo loguea deja la puerta abierta a repetir el bug en silencio.

### D4 — `SET LOCAL` self-healing vía listener `after_begin` (LOCKED)

`SET LOCAL` es transaction-scoped: muere en el `COMMIT`. Hoy `org_context` lo
setea una sola vez por request → un endpoint que escribe-luego-lee perdería el
contexto bajo RLS real (default-deny silencioso: lecturas vacías, writes que
fallan el `WITH CHECK`). Hoy no se nota porque el superusuario ignora RLS; al
conectar como `app_rls` se activaría.

Fix estructural (robusto por diseño, no por disciplina del dev):

- `org_context` deja de emitir el `SET LOCAL` directamente; en su lugar **solo
  guarda** `org_id` y `user_id` en `session.info`. El listener pasa a ser el
  **único** writer del GUC.
- Un listener `@event.listens_for(Session, "after_begin")` re-aplica
  `set_config('app.current_org', ..., true)` + `set_config('app.current_user',
  ..., true)` en **cada** transacción nueva de la sesión que tenga contexto en
  `session.info`. Como `after_begin` corre al abrir la primera transacción, la
  primera query del request ya lleva el GUC — no hace falta un `SET LOCAL`
  separado de arranque.

Así el GUC está garantizado en toda transacción del request, sobreviva o no a
commits intermedios. El helper que aplica las GUCs desde `session.info` vive en
`db/rls.py` (junto a `apply_org_context`, que queda para los callers no-web —
ingest/cron — que pasan el contexto explícito); el contrato de las GUCs
(`NULLIF(...,'')::bigint`, `''` para no-user) no cambia.

Verificación complementaria: auditar los endpoints write-then-read existentes y
confirmar que ninguno asumía el comportamiento viejo de forma que el listener
rompa (no debería — el listener solo agrega garantías).

### D5 — Tests que prueban la config de runtime, no una paralela (LOCKED)

El gap existió porque los tests corren como `app_rls` pero prod como superuser.
Se cierra esa brecha de parity:

1. **Boot guard, rechazo:** conectando como superuser, el guard raisea.
2. **Boot guard, paso:** conectando como `app_rls`, el guard pasa.
3. **Invariante de runtime:** el rol con que la app conecta tiene
   `is_superuser=off` y `rolbypassrls=off`.
4. **Listener self-healing:** una sesión con contexto que hace `commit()`
   intra-request mantiene `app.current_org` legible en la transacción siguiente
   (prueba que el aislamiento sobrevive al commit).
5. Los tests de endpoints bajo RLS existentes (conftest `app_rls`) se mantienen —
   ahora reflejan la config real de prod.

## 4. Componentes y archivos afectados

| Archivo | Cambio |
|---|---|
| `compose.yaml` | Nuevo servicio `migrate` (one-shot, owner DSN); `backend` pasa a `app_rls` DSN + `depends_on: migrate (service_completed_successfully)` |
| `compose.dev.yaml` | Mismo split; `backend` dev solo `uvicorn --reload` |
| `compose.coolify.yaml` | Mismo split para prod; `migrate` con owner DSN, `backend` con `app_rls` DSN |
| `backend/Dockerfile` | `CMD` deja de encadenar `alembic && uvicorn`; cada servicio del compose pasa su comando (o dos targets/commands) |
| `.env` / `.env.example` | Documentar `DATABASE_URL` (owner, usado por `migrate`), el DSN `app_rls` del `backend`, y `APP_RLS_PASSWORD` |
| `Makefile` | `make dev` / `make prod-local` levantan `migrate` antes de `backend` (lo da `depends_on`); sin cambios de UX |
| `src/ibkr_control/main.py` | Boot guard en `lifespan` (D3) |
| `src/ibkr_control/db/session.py` | Sin cambios estructurales (sigue leyendo `DATABASE_URL`); posible helper para el guard |
| `src/ibkr_control/api/_context.py` | `org_context` guarda contexto en `session.info` (D4) |
| `src/ibkr_control/db/rls.py` | Helper que aplica GUCs desde `session.info`; registro del listener `after_begin` (o módulo hermano) |
| `tests/` | 4 tests nuevos (D5) + ajustes de fixtures si el split cambia el boot |

## 5. Scope OUT (explícito)

- **Split de 3 roles / owner non-superuser** → SP4 (D2).
- **Cola durable + rate-limit por org del cron** → SP5/SP7 (ya difered en SP1).
- **Enforcement de grants cross-org** → SP2.
- **Rotación de secrets / KMS para `APP_RLS_PASSWORD`** → SP4.

## 6. Criterios de aceptación

1. `backend` (dev, prod-local, Coolify) conecta como `app_rls`; el servicio
   `migrate` corre las migraciones como owner y termina exitosamente.
2. El boot guard rechaza arranque como superuser/BYPASSRLS (test verde).
3. Un request que escribe-y-lee mantiene aislamiento RLS a través del commit
   (test verde).
4. Suite completa verde **bajo `app_rls`** (sin regresión vs el baseline
   pre-branch; el conteo crece con los tests nuevos — cerró en 351).
5. `ruff check .` limpio, boot smoke OK como `app_rls`, `make prod-local`
   arranca con el split de servicios.
6. Aislamiento cross-tenant sin filtro explícito: cubierto de forma durable por
   `tests/test_rls.py::test_org_a_cannot_see_org_b_accounts` (query crudo bajo
   `app_rls` con contexto → solo la org propia), que ahora refleja la config real
   de runtime. Un smoke end-to-end por HTTP con dos orgs queda como verificación
   manual opcional (belt-and-suspenders sobre la prueba automatizada).
