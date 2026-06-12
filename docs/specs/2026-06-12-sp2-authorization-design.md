# Spec — SP2 Authorization (choke-point + enforcement de grants cross-org)

**Fecha:** 2026-06-12
**Estado:** aprobado (brainstorming 2026-06-12)
**Programa:** SaaS multi-tenant SP1–SP8 · branch `saas/sp2-authorization`
**Specs relacionados:** `2026-06-03-saas-program-roadmap.md` (SSOT, fila SP2),
`2026-06-03-sp1-tenancy-identity-design.md` (access_grants shaped, policy RLS especial,
§RLS punto 2: "el switch ocurre después de chequear el grant — esa autorización vive en
el app (SP2)"), `2026-06-02-schema-hardening-multiuser-design.md` (predecesor pre-pivot
`data_access_grants`/`visible_account_ids`, subsumido)

---

## 1. Problema

SP1 dejó la autorización *shaped* pero sin *enforcement*:

- La tabla `access_grants` (grantor party → grantee org|user, role, vigencia) existe
  con su policy RLS especial (`grant_visibility`: grantor-org OR grantee la ven), pero
  **nada la lee para decidir acceso**. Un contador con grant vigente recibe hoy
  `403 NOT_A_MEMBER`.
- `resolve_current_org_id` (`api/_context.py`) honra solo memberships propias y recibe
  `requested_org_id=None` **hardcodeado** — el cable request-level de selección de org
  que D-CONV-3 declaró explícita no existe.
- `memberships.role` (`owner|admin|member`) existe en el schema pero **ningún endpoint
  lo lee**: cualquier member puede rotar tokens, borrar connections, disparar ingest.
- No hay choke-point: cada endpoint cuelga de `org_context` (aislamiento) pero ninguna
  capa responde "¿este actor puede esta acción sobre este recurso?".

Brecha semántica clave: el grant es **party-scoped** (`grantor_party_id` = qué data,
lockeado en SP1 decisión #4), pero RLS aísla a nivel **org**. Si el grantee entra al
org del cliente, RLS le muestra el org entero — el filtrado al subset del party es
responsabilidad de la capa de aplicación, y hoy no existe.

## 2. Decisiones

### SP2-D1 — Motor: autorización app-interna Postgres-native (NO OpenFGA/SpiceDB)

La decisión gorda del roadmap se resuelve por **Opción A**: módulo `authz/` interno
con funciones puras sobre `memberships` + `access_grants`, encima de RLS.

Rationale (por qué es la opción world-class y no la conformista):

- **El grafo de relaciones tiene 2 saltos máximo** (`user → membership → org`;
  `party → grant → grantee`). Los motores Zanzibar pagan cuando el grafo es
  profundo/recursivo o cuando N servicios necesitan decisiones consistentes. Ninguna
  de las dos aplica a un monolito modular (decisión arquitectónica #12; el anti-scope
  del roadmap prohíbe microservicios sin señal).
- **Cero dual-write.** El costo operacional #1 de OpenFGA/SpiceDB es sincronizar el
  store de relaciones con la DB de dominio; divergencia = niega acceso legítimo o
  concede acceso revocado. Con el modelo en Postgres, revocar un grant es
  transaccional e instantáneo. Los Zookies/consistency tokens de SpiceDB resuelven un
  problema ("new enemy") que esta arquitectura no tiene por construcción.
- **La suite corre bajo `app_rls`** (test infra PR-C): cada test de SP2 verifica RLS
  en profundidad gratis.
- **El swap futuro queda contenido por diseño** (ver SP2-D2): si en el futuro hay
  señal real (grafo profundo, multi-servicio), se reemplaza el interior del decisor
  sin tocar endpoints.

La cadena de confianza queda en dos capas (principio del roadmap "RLS es el piso, no
el techo"): RLS enforce "ves el org que está en contexto"; `authz/` enforce "a qué
contextos podés entrar y qué podés hacer adentro". El contexto RLS se setea **solo
después** de que el resolver autoriza (SP1 spec §RLS punto 2, ahora implementado).

### SP2-D2 — Choke-point obligatorio: PEP/PDP, `org_context` muere

- **PDP (decisión):** `authz/resolver.py` — `resolve_authz(session, user_id,
  requested_org_id) -> AuthzContext`. UN solo módulo computa decisiones; sin ifs de
  autorización dispersos en endpoints.
- **PEP (enforcement):** factory `require_scope("recurso:accion")` → dependency
  FastAPI que resuelve el contexto, chequea el scope contra el rol efectivo, setea el
  contexto RLS (GUCs org+user, mismo contrato `db/rls.py`) y devuelve `AuthzContext`.
- La dependency `org_context` actual **se elimina** (no deprecación, no alias): el
  100% de los endpoints org-scoped migra a `require_scope`. Cero rutas con el
  mecanismo viejo.
- **Route-sweep guard estructural:** test que introspecciona `app.routes` y FALLA si
  cualquier ruta carece de `require_scope`, contra un allowlist explícito y nombrado
  (auth de fastapi-users, docs/openapi, health liveness, SSE user-scoped — cada
  entrada con su porqué). Un endpoint nuevo sin autorización es estructuralmente
  imposible — misma clase de guard que el boot guard RLS de PR #7.

### SP2-D3 — `AuthzContext` tipado

```python
@dataclass(frozen=True)
class AuthzContext:
    org_id: int                      # org activa (contexto RLS ya aplicado)
    user_id: int
    actor: Literal["member", "grantee"]
    role: str                        # owner|admin|member · read_only (grantee)
    party_ids: frozenset[int] | None # None = sin restricción; set = scope del grant
```

Resolución:

1. Org solicitada ∈ memberships propias → `member` con su rol.
2. Si no: grants **vigentes** (`valid_from <= hoy < valid_to`, half-open; `valid_to
   IS NULL` = sin vencimiento) donde `grantee_user_id = user` **o**
   `grantee_organization_id ∈ orgs con membership del user`, y `organization_id =
   org solicitada` → `grantee` con `role="read_only"` y `party_ids` = unión de los
   grantor parties de los grants que matchean.
3. Sin org solicitada → auto-resolve **solo sobre memberships** (exactamente una →
   esa; varias → `400 ORG_SELECTION_REQUIRED`). Entrar a un org ajeno vía grant es
   **siempre explícito** — nunca se adivina un contexto cross-org, aunque el user
   tenga un único grant y cero memberships.
4. La fecha de vigencia se evalúa con `CURRENT_DATE` de la DB (UTC). Granularidad
   DATE es deliberada (el grant es una relación fiscal, no una sesión).

Errores (default-deny, sin leak de existencia):

| Caso | Código |
|---|---|
| Sin memberships y sin org solicitada | `403 NO_ORG_MEMBERSHIP` (igual que hoy) |
| Multi-membership sin header | `400 ORG_SELECTION_REQUIRED` (igual que hoy) |
| Org solicitada sin membership NI grant vigente | `403 NO_ORG_ACCESS` (**reemplaza** `NOT_A_MEMBER`, que ya no describe el caso) |
| Scope insuficiente para el rol efectivo | `403 INSUFFICIENT_SCOPE` |

### SP2-D4 — Selección explícita de org: header `X-Organization-Id`

Header opcional, bigint; malformado → 422. Es el cable que D-CONV-3 anticipó. El
usuario single-org no lo manda y todo resuelve igual que hoy → **cero cambio frontend
en SP2** (el org switcher UI llega con SP3, cuando existan usuarios reales multi-org).

### SP2-D5 — Taxonomía de scopes y mapeo rol → scope

Dos planos de recursos: **admin-plane** (operar la conexión con el broker) vs
**data-plane** (leer la data fiscal — lo que el grant comparte). El mapeo vive como
data en `authz/scopes.py` (dict rol → frozenset de scopes), no como ifs:

| Recurso | Scope | owner | admin | member | grantee |
|---|---|---|---|---|---|
| Connections CRUD/rotate/enable/disable | `connections:write` | ✓ | ✓ | — | — |
| Setup wizard (todos los steps) | `setup:write` | ✓ | ✓ | — | — |
| Ingest trigger manual | `ingest:trigger` | ✓ | ✓ | — | — |
| Connections list + health + ingest logs | `ops:read` | ✓ | ✓ | ✓ | — |
| Imports + restatements (+ Phase 3: lots, dividends, reports) | `data:read` | ✓ | ✓ | ✓ | ✓ |
| Grants crear/revocar | `grants:write` | ✓ | — | — | — |
| Grants listar | `grants:read` | ✓ | ✓ | ✓ | ✓* |

\* El grantee lista sus grants desde su **propio** org — la policy `grant_visibility`
ya se los muestra; no necesita entrar al org del cliente para descubrirlos.

Decisiones finas dentro de la tabla:

- `grants:write` es **owner-only**: compartir la data fiscal del hogar es decisión del
  dueño, no del staff admin.
- El grantee NO tiene `ops:read`: el estado de conexiones/credenciales del cliente es
  operación interna del org, no parte de la data fiscal compartida.
- `member` lee todo pero escribe nada — la semántica fina adicional de roles
  (gestión de memberships, transferencia de ownership) es SP3.

### SP2-D6 — Grantee: tres barreras independientes

1. **App (scope):** `read_only` solo tiene `data:read` + `grants:read`. Ningún write
   ni admin-plane pasa `require_scope`.
2. **DB (defensa en profundidad):** en contexto grantee, el applier de contexto emite
   `SET TRANSACTION READ ONLY` junto a los GUCs, **incluido el listener
   `after_begin`** (el stash de `session.info` gana un flag `read_only` — mismo patrón
   self-healing de PR #7, sobrevive commits intra-request). Un write que se escape de
   la capa app muere en Postgres con error 25006, no silenciosamente. Nota: la policy
   RLS `WITH CHECK (organization_id = current_org)` NO frena writes del grantee (su
   contexto ES el org del cliente) — por eso esta barrera no es opcional.
3. **Party scope:** `visible_account_ids(session, ctx) -> set[int] | None` — `None`
   para members (sin filtro); para grantees, `SELECT DISTINCT account_id FROM
   participations WHERE party_id IN ctx.party_ids`. **Participación histórica, no
   solo vigente**: revisar el año fiscal N exige cuentas que el party ya
   vendió/cerró/transfirió (SCD-2: una participation con `valid_to` pasado sigue
   habilitando lectura). Los endpoints `data:read` aplican el filtro; un resultado
   account-dimensionado fuera del set no se emite.

### SP2-D7 — CRUD de grants (`/api/grants`)

- `POST /api/grants` (`grants:write`): `{grantor_party_id, grantee_organization_id |
  grantee_user_id (XOR — el CHECK grantee_arc ya lo enforce), valid_from? (default
  hoy), valid_to? (default NULL)}`. El grantor party se resuelve bajo RLS del org
  activo — un party ajeno simplemente no se encuentra (404, sin leak). Grantee
  inexistente → la FK rebota → 422 genérico.
- `GET /api/grants` (`grants:read`): ambas direcciones (la policy ya las muestra),
  serializadas con `direction: "granted" | "received"`.
- `POST /api/grants/{id}/revoke` (`grants:write`): set `valid_to = hoy`. **Nunca
  DELETE** — el grant revocado es audit trail (quién tuvo acceso a qué data y
  cuándo). Un grant ya vencido/revocado → 409.
- **Semántica half-open `[valid_from, valid_to)`** en TODO el sistema (resolver +
  CRUD): `valid_to = hoy` ⇒ inactivo desde ya. Permite el revoke same-day de un grant
  creado por error sin DELETE.
- No hay UPDATE de grants: cambiar vigencia/grantee = revocar + crear (cada relación
  de acceso es un registro inmutable con su ciclo de vida).

### SP2-D8 — Amendment del CHECK de vigencia

`ck_access_grants_valid_range`: `valid_to > valid_from` → **`valid_to >=
valid_from`**. El intervalo vacío (`valid_to == valid_from`) es legal y significa
"nunca activo" — necesario para el revoke same-day bajo half-open. Sin esto, revocar
un grant creado hoy violaría el CHECK y forzaría un DELETE (rompiendo audit trail) o
un workaround.

### SP2-D9 — `restatement_log.account_id` de primera clase

Columna `account_id` BigInteger FK→accounts `ondelete=RESTRICT` NOT NULL + índice
`(organization_id, account_id)`. Mismo patrón `ondelete=RESTRICT` de los 7 hechos
(SP1-db-hardening #4); la interacción con el tenant-wipe multi-path (`DELETE FROM
organizations`) ya está lockeada por su test — la suite verifica que el wipe sigue
funcionando con la FK nueva. Las natural keys de las 3 tablas snapshot
auditadas por W3 ya contienen `account_id` — el persister lo tiene en la mano al
poblar (`_upsert_snapshot_with_audit`); hoy queda enterrado en el JSONB
`natural_key`. Promoverlo:

- habilita el filtro party-scoped del grantee sin extraer del JSONB (sin índice,
  workaround prohibido);
- la señal `sealed_year` ("tu Form 210 pudo cambiar") es exactamente lo que el
  contador del party necesita ver — filtrada a SU cliente;
- consistente con la lección Phase 2.9: un discriminador de acceso/clasificación es
  hecho de primera clase capturado en la fuente, no inferido de un blob.

`GET /api/ingest/restatements` aplica el filtro cuando `ctx.party_ids` no es None.
(El summary de restatements del health endpoint NO lo necesita: health es `ops:read`,
inalcanzable para el grantee.)

### SP2-D10 — Migración: un amendment al baseline (T1-D14 vigente)

UN amendment al baseline pristino `a9977ac077e5` con SP2-D8 + SP2-D9, **regenerado
canónicamente dentro del container** (lección Phase 2.9 / transfer-lineage: el camino
valida formato y ordering topológico; hand-edit prohibido) + wipe & reload dev
(`down -v`, recarga por wizard). La política expira al primer deploy.

## 3. Componentes

```
backend/src/ibkr_control/authz/
  __init__.py        # exporta AuthzContext, require_scope, visible_account_ids
  context.py         # AuthzContext (frozen dataclass)
  resolver.py        # resolve_authz — PDP puro (memberships + access_grants)
  scopes.py          # taxonomía + mapeo rol→scopes (data) + require_scope (PEP)
  party_scope.py     # visible_account_ids
backend/src/ibkr_control/api/grants.py   # CRUD /api/grants
```

- `api/_context.py` se elimina (su contenido evoluciona a `authz/`); `db/rls.py` gana
  el flag `read_only` en el stash/applier (sigue siendo el SSOT del contrato GUC).
- `authz/` importa de `db/` y `auth/` (modelos), nunca de `api/` — misma dirección
  inner→outer que el resto.
- Frontend: orval regen canónico (endpoints nuevos de grants aparecen en el cliente
  TS); sin consumo UI en SP2.

## 4. Flujo de request (data flow)

1. Request llega con JWT (+ opcional `X-Organization-Id`).
2. `require_scope("X")` → `current_active_user` → `resolve_authz(session, user_id,
   requested_org_id)` (PDP: memberships primero, grants después).
3. Denegado → 400/403 ANTES de tocar contexto RLS (default-deny: sin contexto, RLS
   devuelve cero filas incluso ante un bug posterior).
4. Autorizado → scope check contra rol efectivo → fail `403 INSUFFICIENT_SCOPE`.
5. Contexto RLS: stash + apply GUCs (org, user) + flag read-only si grantee
   (self-healing en cada transacción del request).
6. Endpoint corre con `AuthzContext`; si es data-plane y `party_ids` no es None,
   aplica `visible_account_ids`.

Los paths de sistema (cron Flex, `system_credentialed_org_ids()`) NO cambian: son
control-plane sin user; `AuthzContext` es exclusivamente request-layer.

## 5. Testing

- **Matriz del resolver** (unit): {member single/multi, grantee user-directo, grantee
  vía org-firm, sin acceso} × {org solicitada propia/ajena/None} × vigencia {futura,
  activa, sin vencimiento, revocada hoy, vencida, intervalo vacío}.
- **Integration bajo `app_rls`** (suite ya corre así — regresión RLS gratis):
  - grantee entra al org del cliente y ve imports/restatements filtrados al party
    (cuentas del party visibles; cuentas de otros parties del mismo org NO);
  - grantee rebota admin-plane (`403 INSUFFICIENT_SCOPE`) y todo write;
  - **barrera DB:** write forzado en contexto grantee SIN pasar por la capa app →
    error Postgres 25006 (read-only transaction) — prueba la defensa en profundidad
    aislada;
  - revoke → el siguiente request del grantee → `403 NO_ORG_ACCESS` (revocación
    inmediata, sin caché).
- **Route-sweep guard:** toda ruta tiene `require_scope` o está en el allowlist
  nombrado; agregar endpoint sin autorización rompe la suite.
- **Header:** multi-org sin header → 400; con header válido → resuelto; malformado →
  422; org ajeno sin grant → `403 NO_ORG_ACCESS`.
- **CRUD grants:** create owner-only (admin → 403); revoke same-day legal (SP2-D8);
  GET muestra ambas direcciones; party ajeno en POST → 404.
- **Persister:** `restatement_log.account_id` poblado en los 3 caminos snapshot
  (golden run W3 sigue verde con la columna nueva).

## 6. Fuera de alcance (consumidor en SP futuro — NO es deuda)

| Corte | Dónde aterriza |
|---|---|
| UI org switcher + pantalla de grants | SP3 (onboarding/invites trae usuarios reales y el handshake de descubrimiento grantee) |
| Invite-flow para crear grants por email | SP3 |
| Audit log de decisiones de autorización | SP8 |
| Rol de sistema dedicado (residual `SECURITY DEFINER`) | SP5/SP7 |
| Semántica fina adicional de membership (gestión de members, transfer ownership) | SP3 |
| Roles de grant adicionales (`role` hoy CHECK `IN ('read_only')` — queda así) | cuando exista el caso de uso (write delegation no tiene demanda) |
