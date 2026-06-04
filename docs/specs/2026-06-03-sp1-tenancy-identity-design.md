# SP1 — Tenancy & Identity (data model) — Design

> **Programa:** pivote a SaaS multi-tenant world-class (ver memoria `saas-pivot-target`). La calibración "app personal de 3 usuarios / sin maquinaria multi-tenant" fue **removida** por el usuario el 2026-06-03. El producto se construye como SaaS real (8 sub-proyectos SP1–SP8; ver §"Contexto del programa").
>
> **Este spec cubre solo SP1**, el keystone: el modelo de identidad y tenancy sobre el que se apoyan los otros 7. Cada SP tiene su propio ciclo spec → plan → implementación.
>
> **DB wipe autorizado.** La app nunca fue deployada; no hay datos de prod que preservar. SP1 arranca la DB **en vacío** sobre un baseline squasheado pristino.

## Contexto del programa (descomposición SaaS)

> **SSOT del roadmap del programa** (tabla completa con las "decisiones gordas" que cada SP va a forzar, dependencias y estado): `docs/specs/2026-06-03-saas-program-roadmap.md`. La tabla de abajo es el resumen que SP1 necesita.

| SP | Sub-proyecto | Depende de |
|---|---|---|
| **SP1** | **Tenancy & Identity (este spec)** | — |
| SP2 | Authorization (enforcement de grants, `require_*_scope`, ReBAC) | SP1 |
| SP3 | AuthN & onboarding (signup, MFA, IdP, invites, provisioning UX, wizard multi-party) | SP1 |
| SP4 | Secrets / encryption (KMS envelope, data-key por tenant) | SP1 |
| SP5 | Durable jobs (queue + rate-limit IBKR por org) | SP1 |
| SP6 | Billing (Stripe, planes, entitlements) | SP1, SP3 |
| SP7 | Ingest tenant-aware (cron org-iterante; TRM global) | SP1, SP5 |
| SP8 | Compliance/Observability (audit log de accesos, Habeas Data/GDPR) | SP1 |

## Decisiones locked (resueltas en brainstorming 2026-06-03)

1. **Tenant = Organization, org-based "ambos"** (`type: personal | firm`). Un hogar es un org personal (1–2 miembros); un estudio contable es un org firm (N staff). El acceso del contador es un **grant cross-org**, no co-tenancy.
2. **Party first-class, separada de User.** El dueño fiscal de una cuenta es un **Party** (contribuyente), nunca un User (login). Un Party se enlaza opcionalmente a un User (0..1): el cónyuge sin login o el cliente del estudio que nunca loguea son Parties sin User. La propiedad es `account ↔ party` (con `pct`, período SCD-2).
3. **`organization_id` denormalizado en TODA tabla org-scoped** + Postgres RLS keyed en una variable de sesión por request. Política trivial `USING (organization_id = current_org)`. TRM y dato de referencia público quedan **globales** (sin `organization_id`, sin RLS).
4. **Grants party-scoped.** El `access_grant` apunta a un **Party** (la data fiscal de ese contribuyente = todas las cuentas donde el Party tiene ownership). Grantee = un org-firm o un User. "Compartir todo el hogar" = grant a todos los Parties del org.
5. **Baseline squasheado pristino + wipe.** Una migración baseline nueva (`down_revision=None`) reemplaza toda la cadena (`cbeaac94933d` → `eb5ef6d36e06` → `8d69e795a517`). Sin migraciones reversibles ni preservación de datos. Las tablas de **hechos** (trades/closed_lots/open_position_lots/cash/accruals/transfers/counterparties) **conservan su estructura** (natural keys, precisión `Numeric`, `asset_class` fail-loud, lecciones Phase 2.5–2.9); solo ganan `organization_id` + RLS.

**Aclaración de dominio (usuario):** los `ibkr_account_id` son **únicos a nivel mundial por IBKR** → `UNIQUE(ibkr_account_id)` global. El UNIQUE (constraint, ve todas las filas) protege integridad; RLS oculta la fila de otros orgs → el ingest de un org que intente insertar una cuenta de otro org falla por integridad **sin** poder leer de quién es (error genérico app-layer, SP7). Integridad real sin leak de existencia.

## Modelo de entidades

```
organizations            users (auth principal)        parties (taxpayer)
─────────────            ─────────────────────        ──────────────────
id                       id                            id
type personal|firm       email                         organization_id  FK NOT NULL
name                     hashed_pw / idp_subject       display_name
setup_completed_at       (sin rol de "dueño")          tax_id (NIT) nullable
setup_progress JSONB           │                       user_id  FK users.id (0..1, null)
created_at                     │                       created_at
   ▲                           │ memberships
   │                           │ ───────────
   │  user_id FK ──────────────┘ organization_id FK
   │  role owner|admin|member    PK(user_id, organization_id)
   │
   │  accounts (IBKR real-world)         participations (party-anchored)
   │  ─────────────────────────         ──────────────────────────────
   │  id                                party_id    FK parties.id (was user_id)
   └─ organization_id FK NOT NULL        account_id  FK accounts.id
      ibkr_account_id UNIQUE (global)    pct Numeric(5,4)
      alias, currency, created_at        valid_from / valid_to  (SCD-2)
           │                             organization_id FK NOT NULL
           │ owns                        PK(party_id, account_id, valid_from)
           ▼                             CHECK pct∈[0,1], valid_range
      facts: trades · closed_lots · open_position_lots · cash_transactions
             · change_in/open_dividend_accruals · transfers · counterparties
      ──────────────────────────────────────────────────────────────────
      + organization_id FK NOT NULL  (denormalizado, RLS)
      (resto INTACTO: natural keys, precision, asset_class fail-loud)

  flex_imports / flex_import_accounts / flex_credentials
  ──────────────────────────────────────────────────────
  + organization_id FK NOT NULL    (flex_credentials: org-scoped, ya no per-user)

  access_grants (cross-org delegated read — el contador)
  ─────────────────────────────────────────────────────
  id
  grantor_party_id        FK parties.id NOT NULL   (qué data)
  grantee_organization_id FK organizations.id  ┐ exclusive arc (CHECK XOR)
  grantee_user_id         FK users.id          ┘
  role  CHECK IN ('read_only')
  valid_from / valid_to, created_at
  organization_id = org del grantor party (ver §RLS: policy especial)

  trm_days / trm_imports  ──▶  GLOBAL (público DIAN; sin organization_id, sin RLS)
```

### Tablas nuevas

- **`organizations`** — `id`, `type` (CHECK `personal`/`firm`), `name`, `setup_completed_at`, `setup_progress` (JSONB; movido desde `users` — el setup es per-org), `created_at`.
- **`memberships`** — `(user_id, organization_id)` PK, `role` (CHECK `owner`/`admin`/`member` — semántica fina en SP2/SP3), `created_at`. FKs CASCADE.
- **`parties`** — `id`, `organization_id` FK NOT NULL (org "hogar" del party), `display_name`, `tax_id` nullable, `user_id` FK nullable (0..1 link a login), `created_at`.
- **`access_grants`** — ver §RLS para la policy especial.

### Tablas refactorizadas

- **`participations`** — PK `(user_id, account_id, valid_from)` → **`(party_id, account_id, valid_from)`**. + `organization_id`. La propiedad es de la persona fiscal, no del login. (La consistencia `party.org == account.org == participation.org` se asume invariante: la propiedad existe dentro de un org; cross-org es solo grants.)
- **`accounts`** — + `organization_id` NOT NULL. `ibkr_account_id` sigue UNIQUE global. Pierde el comentario de "identidad compartida entre users" (ahora es org-scoped + party-owned).
- **`users`** — queda como principal de auth puro. `setup_completed_at`/`setup_progress` migran a `organizations`. (El rediseño del flujo de auth/onboarding es SP3; SP1 solo mueve el estado de setup al org.)
- **`flex_credentials`** — de per-user a **per-org**. PK surrogate `id` + `organization_id` (permite >1 login IBKR por org). + RLS.
- **Hechos + `flex_imports` + `flex_import_accounts` + `counterparties`** — + `organization_id` NOT NULL + RLS. Estructura intacta. La procedencia de H1 (`flex_import_accounts`) se re-ancla al org; su rol anti-IDOR se subsume en RLS + el modelo de ownership (sigue siendo útil para "qué cuentas vio el import de este org").

## Mecanismo de enforcement RLS

1. **Contexto de org por request.** Una dependency/middleware FastAPI resuelve el org activo del principal autenticado y emite `SET LOCAL app.current_org = <org_id>` + `SET LOCAL app.current_user = <user_id>` dentro de la transacción. `SET LOCAL` (no `SET`) → scoped a la TX, auto-reset en commit/rollback. **Crítico con pooling:** sin `LOCAL`, una conexión reciclada arrastra el org del request anterior → leak cross-tenant silencioso.
2. **Cross-org (contador).** El switch de `current_org` al org del cliente ocurre **después** de chequear el `access_grant` — esa autorización vive en el app (SP2), arriba de RLS. RLS solo enforça "ves el org que está en contexto"; el grant decide a cuáles podés hacer switch.
3. **Rol de DB sin bypass.** El app corre como rol NO-superuser, NO-owner (esos bypassean RLS). `FORCE ROW LEVEL SECURITY` en cada tabla org-scoped (ni el owner se salta la policy). Migraciones corren con un rol privilegiado aparte (DDL). `current_org` sin setear → la policy no matchea → **0 filas (default-deny)**.
4. **Policy estándar** (toda tabla org-scoped salvo `access_grants`): `USING (organization_id = current_setting('app.current_org')::bigint) WITH CHECK (organization_id = current_setting('app.current_org')::bigint)`.
5. **Policy especial de `access_grants`** (es el puente cross-org; no puede ocultarse por single-org RLS o el grantee nunca descubriría sus grants): `USING (organization_id = current_org OR grantee_organization_id = current_org OR grantee_user_id = current_user)`. Por eso el contexto setea **también** `app.current_user`.
6. **Tablas globales** (`trm_days`, `trm_imports`): sin `organization_id`, sin RLS, lectura libre. El persister TRM no setea org. La TRM **no** escribe `ingest_log` (control plane, no data plane — ver D-CONV-1); su observabilidad es `trm_imports`.
7. **Path de ingest/cron** (fuera de request, sin JWT): itera por org, `SET LOCAL app.current_org = org.id` por iteración. SP1 entrega el persister org-aware **y** el cron Flex iterando por org (D-CONV-2); la sofisticación (cola durable + rate-limit por org) la construyen SP5/SP7 *encima* del loop.

## Cambios en write-paths (SP1, data-level)

- **Persister** (`ingest/flex/persister.py`): recibe `organization_id` del contexto del import y lo estampa en `flex_imports`, todos los hechos, `flex_import_accounts`, `counterparties`. (El stamping reemplaza al `user_id`-only scoping previo.)
- **Wizard** (`api/setup.py`): opera dentro del contexto de un org; `participations` pasan a party-anchored. SP1 usa un **default mínimo**: el founding user ↔ su Party auto-creado; capturar al cónyuge co-titular como segundo Party en la UI se difiere a SP3.
- **Session context plumbing**: la dependency que setea `SET LOCAL app.current_org`/`app.current_user`, consumida por todas las rutas autenticadas.

## Decisiones de convergencia (2026-06-03 — descubiertas implementando, locked)

El green-up del app-layer (originalmente "Task 19: adaptar tests") resultó ser una conversión org-aware de ~11 módulos source, no solo de tests. Dos decisiones arquitectónicas que el plan original no había resuelto:

### D-CONV-1: TRM es control plane — fuera de `ingest_log`

Principio rector: **data plane (org-scoped, RLS) vs control/reference plane (global, sin RLS)**. `ingest_log` es data-plane (`organization_id` NOT NULL + RLS): registra corridas de ingest *de un tenant* (flex, upload manual, acciones del wizard — todas tienen org). La TRM es **control plane** (reference data que todo el sistema comparte, una sola verdad). El bug latente era que el job TRM escribía su fila en `ingest_log` con `user_id=None` — un evento de sistema en una tabla de tenant, ahora imposible (`organization_id` NOT NULL).

**Decisión:** TRM **nunca** toca `ingest_log`. Su única fuente de verdad + observabilidad es **`trm_imports`** (global, ya registra `date_from`/`date_to`/conteos/timestamp). `api/health.py` lee frescura TRM de `trm_imports` (global, igual para todo org) y frescura Flex de `ingest_log` (per-org). `ingest_log` queda **100% org-scoped NOT NULL** — sin nullable (se rechazó: un `organization_id` NULL sería invisible bajo RLS `USING(organization_id=current_org)` y rompería el invariante). El refresh manual/wizard de TRM mantiene feedback SSE vía JobTracker (in-memory, UX), pero su registro persistente es `trm_imports`. Un `system_job_log` genérico (para futuros system-jobs no-TRM) se difiere a **SP8**; no se gold-platea ahora.

### D-CONV-2: Cron Flex itera por org (mínimo, no stub)

El cron Flex iteraba `FlexCredentials.user_id` — columna eliminada. **Decisión:** `_run_flex_for_all_orgs` itera `distinct FlexCredentials.organization_id` → `flex_job.run(organization_id=...)`; cada `run` se auto-setea `SET LOCAL app.current_org` (probado bajo `app_rls`, `test_job_rls.py`). TRM cron sigue siendo una corrida global única. El **cuerpo** del loop (la unidad de trabajo per-org) es RLS-correcto y SP5 (durable jobs) + SP7 lo evolucionan de `run inline` a `enqueue(org)` + rate-limit por org, sin reescribir un hack.

**Silent-failure de la enumeración — CERRADO (SP1-hardening H1, `2026-06-03-sp1-hardening-close-gaps-design.md`).** La **enumeración** `SELECT DISTINCT organization_id FROM flex_credentials` es una lectura **cross-tenant de control plane**. Bajo `app_rls` (FORCE RLS) sin contexto, default-deny → 0 orgs → el cron corría y no fetcheaba nada **en silencio** (un hole, no un feature diferido — el proyecto prohíbe silent failures). **Cerrado** exponiéndole a `app_rls` **una** capacidad cross-tenant acotada y auditable: la función `SECURITY DEFINER` `system_credentialed_org_ids()` (search_path fijo, EXECUTE revocado a PUBLIC + sólo a `app_rls`, read-only, devuelve sólo org-ids). El scheduler enumera vía la función; el trabajo per-org sigue 100% RLS-enforced (`flex_job.run` + `SET LOCAL`). Se eligió la función sobre un rol `BYPASSRLS`/`system_database_url` por **least-privilege** (no adelanta la capa de secrets/roles de SP4). Lo que SIGUE siendo SP5/SP7 es la **sofisticación**: cola durable + rate-limit por org (la función + loop son la base sobre la que se construye, no un hack a reemplazar).

### D-CONV-3: Org-pure — purga del scaffolding user-céntrico legacy (2026-06-03, directiva usuario)

El org es la **unidad de tenancy y operación**. No se preserva el "caso común legacy" (single-user resuelto implícitamente) ni se arrastran campos `user_id` vestigiales en la capa operacional/dato. Purga:

1. **`resolve_current_org_id` deja de devolver `rows[0]` silenciosamente.** Exactamente una membership → se resuelve (inequívoco). Varias memberships sin org pedida explícita → **400 `ORG_SELECTION_REQUIRED`** (sin pick silencioso). No rompe al user single-org; elimina la asunción legacy. El selector multi-org explícito (UI) es SP3.
2. **Throttle del manual-trigger pasa de per-user a per-org.** `users.last_ingest_trigger_at` → `organizations.last_ingest_trigger_at`. La relación con IBKR es per-org (credenciales per-org; pacing 1/día por cuenta = por org); un throttle per-user dejaba que dos miembros doble-dispararan a IBKR.
3. **`flex_imports.user_id` se DROPEA.** Solo se estampaba en filas poison; el scope/dedup es org. Las poison rows ya son org-scoped.
4. **`ingest_log.user_id` (+ su índice) se DROPEA.** `ingest_log` es observabilidad operacional org-scoped; el actor ("qué miembro disparó") es auditoría que pertenece al audit log dedicado de **SP8** (Habeas Data/GDPR), no a un FK nullable medio-poblado. Índice reemplazado por `(organization_id, started_at DESC)`.

**Efecto:** todo el threading de `user_id` audit en el ingest path desaparece — `flex_job.run`/`ingest_xml`/`ingest_log_entry`/`_insert_poison_row` quedan puramente org-scoped (sin parámetro `user_id`). Legítimamente user-level (NO se tocan): `job_tracker` ownership (D1, in-memory, UX del SSE), `parties.user_id`/`memberships.user_id`/`access_grants.grantee_user_id` (núcleo del modelo), auth. El baseline squasheado se **edita** para reflejar el schema final (no se apila migración — DB wipe autorizado).

### Alcance real de la conversión org-aware

Además de persister (hecho) + wizard (hecho), la convergencia convierte: `ingest/log.py` (`+organization_id`), `ingest/hash_dedup.py` (`check_hash_status` keyed en org), `ingest/flex/job.py` (`run`/`ingest_xml`/`_insert_poison_row` toman `organization_id`; dedup `on_conflict` → `(organization_id, xml_hash)`), `api/credentials.py` · `api/imports.py` · `api/health.py` · `api/ingest.py` (org-context vía la dependency), `scheduler/jobs.py` (D-CONV-2). Legítimamente per-user (sin cambio): `job_tracker` (ownership SSE, D1), el rate-limit en `users.last_ingest_trigger_at`, `ingest/lock.py` (granularidad de lock).

## Provisioning & bootstrap

- **Arranque en vacío.** El baseline crea el schema; cero filas. Sin seed de data vieja.
- **`scripts/provision_org.py`** (seam mínimo para dev/local/tests): crea `organization` + founding `user` + `party` + `membership(owner)`. Resuelve el chicken-and-egg (crear un org necesita un user) sin construir el onboarding self-service (que es SP3).

## Estrategia de migración / wipe

- **Una migración baseline** `<rev>_saas_baseline` con `down_revision=None`. Se **borran** las 3 migraciones de la cadena vieja. Genera todo el schema (identidad + hechos-con-org_id + RLS policies + roles).
- **RLS + roles en la migración**: crear el rol de app sin bypass, `ENABLE`/`FORCE ROW LEVEL SECURITY` + las policies por tabla. (Las policies son DDL versionado, no config suelta.)
- **Generación canónica** dentro del container backend (`alembic revision --autogenerate` + edición manual de las policies/roles que autogenerate no infiere), removiendo el falso-positivo `apscheduler_jobs` (lección Phase 2.9). El drift test (`compare_metadata`) debe quedar verde; las policies/roles se verifican con un test dedicado (RLS smoke).

## Estrategia de testing

- **Refactor de fixtures** (`tests/conftest.py`): `sample_user` → `sample_org` + `sample_party` + `membership`; `sample_account` gana org; `sample_flex_import` gana org. Los ~tests existentes que crean user+account+participation se adaptan al modelo org/party (la suite debe quedar verde).
- **RLS smoke tests (red nueva):** con dos orgs A y B sembrados, asertar que con `app.current_org = A` una query de `accounts`/`trades`/`participations` **no** ve filas de B; que sin `current_org` seteado se ven 0 filas (default-deny); que el rol de app no bypassea (`FORCE RLS`). Estos tests corren con el rol sin-bypass, no el owner.
- **`access_grants` policy test:** el grantor org y el grantee (org/user) ven el grant; un tercer org no.
- **Write-path tests:** persister estampa `organization_id` correcto en todos los hechos; wizard crea participation party-anchored.
- **Naming convention + table comments + drift test** endurecidos (heredados de Phase 2.8) cubren las tablas nuevas.

## Frontera de SP1

| ✅ DENTRO | ⏭ DIFERIDO |
|---|---|
| Baseline migration (orgs/parties/memberships/access_grants + org_id everywhere + RLS policies + rol sin bypass) | Onboarding self-service, MFA, IdP, wizard multi-party → **SP3** |
| Plumbing de contexto RLS (`SET LOCAL` por request/tx; `current_org` + `current_user`) | Enforcement de grants, `require_*_scope`, ReBAC engine → **SP2** |
| Write-paths org/party-aware (persister + wizard + ingest jobs + endpoints credentials/imports/health/ingest) | Cola durable + rate-limit por org → **SP5/SP7** (SP1 hace el cron-loop por org mínimo, D-CONV-2) |
| TRM como control plane (fuera de `ingest_log`; observabilidad vía `trm_imports`, D-CONV-1) | `system_job_log` genérico → **SP8** |
| `provision_org.py` + refactor de fixtures + RLS smoke tests | KMS envelope encryption → **SP4** · Billing → **SP6** · Audit log → **SP8** |
| Modelos ORM + `db/__init__` + naming + drift + table comments | — |

## Invariantes (para no re-discutir en SPs posteriores)

- La propiedad de una cuenta es de un **Party**, dentro de **un** org. Cross-org es exclusivamente vía `access_grants`. No existe co-tenancy de recursos.
- `organization_id` es NOT NULL en toda tabla org-scoped y es la **única** llave de aislamiento RLS (default-deny si falta contexto).
- El app **nunca** corre como rol con bypass de RLS. `FORCE RLS` en todas las tablas.
- `SET LOCAL` (nunca `SET`) para el contexto de sesión — invariante anti-leak con pooling.
- TRM y dato de referencia público son **globales**, fuera de RLS.
- Los `ibkr_account_id` son únicos globalmente (hecho de IBKR) → `UNIQUE` global; el aislamiento de existencia lo da RLS + error genérico app-layer.

## Referencias

- Procedencia H1 (`flex_import_accounts`): `docs/plans/2026-06-03-pre-deploy-hardening-backlog.md` §H1 + commit `9b15822`.
- Authz multi-user previo (Phase 2.8, ahora subsumido): `docs/specs/2026-06-02-schema-hardening-multiuser-design.md` (`data_access_grants` + `visible_account_ids` — su rol se reimplementa party/org-aware en SP1/SP2).
- Naming convention + drift test + squash baseline (precedente): Phase 2.8.
- Hechos preservados (natural keys, amendments A3, asset_class): specs Phase 2.5/2.9.
