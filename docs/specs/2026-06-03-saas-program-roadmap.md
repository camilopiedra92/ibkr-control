# SaaS Program Roadmap — IBKR Control (SP1–SP8)

> **SSOT del programa.** El pivote a SaaS multi-tenant world-class (2026-06-03; ver memoria `saas-pivot-target`) descompone el producto en 8 sub-proyectos (SP1–SP8). Cada SP tiene su propio ciclo **spec → plan → implementación → PR**. Este doc es la fuente de verdad del roadmap: la descomposición, las dependencias, el estado, y — lo más importante — **las decisiones gordas que cada SP va a forzar** (las bifurcaciones tech abiertas que su brainstorming debe resolver). La calibración previa "app personal de 3 usuarios" fue removida.

## Principios transversales (locked)

- **Data plane vs control plane.** Todo recurso es de **tenant** (org-scoped, RLS, `organization_id NOT NULL`) o de **sistema** (global, sin RLS, una sola verdad — TRM, reference data, system jobs). No cruzar los planos (lección SP1: TRM fuera del `ingest_log` org-scoped; ver `[[trm-system-control-plane]]`).
- **Tenant = Organization** (`personal|firm`). **Party ≠ User ≠ Tenant**: el dueño fiscal es un Party (contribuyente, 0..1 User); el login es un User; el boundary es la Org. La conjunta es cross-party dentro de una org.
- **RLS es el piso de aislamiento**, no el techo: `app_rls` sin bypass + `FORCE` + `SET LOCAL app.current_org`. La autorización rica (grants, ReBAC) se construye **encima** (SP2), nunca reemplazando RLS.
- **Sin tech debt / workarounds / legacy / silent failures.** Distinguir **deuda** (se cierra ya) de **feature sin construir** (se construye en su SP; adelantarla es su propio anti-patrón).

## Descomposición + decisiones gordas

| SP | Sub-proyecto | Depende de | Estado | Decisiones gordas que va a forzar |
|---|---|---|---|---|
| **SP1** | **Tenancy & Identity (schema)** | — | ✅ **MERGEADO** (PR #5, `2a90fca`) + hardening (PR #6) | Party≠User≠Tenant; cómo se hace tenant-scope en accounts/facts/flex_imports; la conjunta cross-party; el wipe — **RESUELTAS** (ver §"SP1 — resuelto") |
| **SP2** | **Authorization** | SP1 | ⏳ **SIGUIENTE** | **OpenFGA vs SpiceDB vs RLS-solo**; choke-point de autorización obligatorio; el accountant cross-tenant (enforcement del grant) |
| **SP3** | **AuthN & onboarding** | SP1 | ⏳ | **fastapi-users vs IdP (Auth0/Clerk/WorkOS/Keycloak)**; MFA; invites; tenant provisioning UX; wizard multi-party; **sandbox/demo org seedeable con data sintética** (W8 — los fixtures sanitizados ya existen; convertirlos en feature de onboarding) |
| **SP4** | **Secrets / encryption** | SP1 | ⏳ | **qué KMS (Vault transit vs cloud KMS)**; data-key por tenant; migrar el AES-GCM single-key actual |
| **SP5** | **Durable jobs** | SP1 | ⏳ | **Temporal vs Celery vs Arq**; rate-limit IBKR por tenant; idempotencia; **eventos de dominio + transactional outbox** (W5 — decidir junto con el motor de cola; retrofitear outbox post-producción es doloroso) |
| **SP6** | **Billing** | SP1, SP3 | ⏳ | Stripe; planes; metering; entitlements |
| **SP7** | **Ingest tenant-aware** | SP1, SP5 | ⏳ | refactor del pipeline Flex; cron org-iterante sobre cola durable; **TRM queda global** (dato público, no tenant-scoped); **abstracción `connections`/provider** (W1 — `flex_credentials` → `connections` con discriminador, IBKR como primera implementación); **state machine por conexión** (W4 — `active/degraded/reauth_required/disabled`); **reconciliación semántica post-ingest como stage de producción** (W6) |
| **SP8** | **Compliance / Observability** | SP1 | ⏳ | audit log de accesos; retención / erasure (Habeas Data/GDPR); **absorbe O1-O3, S5, S7 del pre-deploy-hardening backlog**; **export completo per-org como mecanismo único** (W9 — portabilidad Habeas Data/GDPR + JSON para `renta` + backup verificable, diseñado una vez); **observabilidad per-tenant OTel** (W10 — traces/métricas etiquetadas por org, noisy-neighbor detection) |

**Orden sugerido:** SP1 (hecho) → SP2 (authorization, el choke-point que todo lo demás asume) → SP3 (authN/onboarding, habilita usuarios reales + SP6) en paralelo conceptual con SP4 (secrets) y SP5 (durable jobs) → SP7 (ingest tenant-aware, necesita SP5) → SP6 (billing, necesita SP3) → SP8 (compliance, absorbe deuda de observabilidad). Las dependencias de la tabla mandan; el orden exacto se decide al cerrar cada SP.

## SP1 — resuelto (referencia para los SPs que dependen de él)

SP1 cerró sus decisiones gordas; los SPs siguientes parten de acá (no re-discutir):

- **Tenancy = `organizations` (`personal|firm`).** Ownership = `account ↔ party` (SCD-2 con `pct`). Membership = `user ↔ org` con rol. Grant cross-org = `access_grants` (party-scoped, grantee org|user). Spec: `docs/specs/2026-06-03-sp1-tenancy-identity-design.md`.
- **Tenant-scope:** `organization_id NOT NULL` en toda tabla org-scoped + **Postgres RLS** (`app_rls` sin bypass, `FORCE`, `SET LOCAL` por request, default-deny sin contexto). TRM/`trm_imports` quedan globales (control plane). Plan: `docs/plans/2026-06-03-sp1-tenancy-identity.md`.
- **Wipe:** baseline squasheado pristino `05943d9efcdb` (`down_revision=None`); sin reversibilidad, DB arranca vacía.
- **Decisiones de convergencia (D-CONV-1/2/3):** TRM control-plane; cron Flex por org; org-pure (sin vestigios `user_id` en la capa operacional). En el spec de SP1.
- **Hardening (PR #6, `docs/specs/2026-06-03-sp1-hardening-close-gaps-design.md`):** cerró 3 holes reales — cron silent-failure (función `SECURITY DEFINER` least-privilege), colisión de cuenta cross-org (→ 409 genérico sin leak), password `app_rls` por env.

### Qué dejó SP1 *shaped* para los SPs siguientes

- **SP2 (Authorization):** la tabla `access_grants` (grantor party → grantee org|user, role, vigencia) + su policy RLS especial ya existen. SP2 construye el **enforcement** (quién puede hacer switch a qué org), el choke-point, y elige el motor (OpenFGA/SpiceDB/RLS-solo). El residual del grant `EXECUTE` a nivel-rol de `system_credentialed_org_ids()` se mueve a un rol de sistema dedicado cuando SP5/SP7 lo traigan.
- **SP4 (Secrets):** el `app_rls` password ya es env-driven (`APP_RLS_PASSWORD`) — SP4 lo absorbe en el KMS/secret manager. El AES-GCM single-key de los Flex tokens migra a data-key por tenant.
- **SP5 (Durable jobs) + SP7 (Ingest tenant-aware):** el cron Flex ya itera por org (enumeración vía `SECURITY DEFINER`, per-org job RLS-correcto). SP5 mete la cola durable + rate-limit IBKR por tenant; SP7 refactoriza el pipeline sobre esa cola (el cron inline → enqueue). TRM sigue global.
- **SP8 (Compliance/Observability):** `ingest_log` quedó 100% org-scoped (sin `user_id`); el "qué actor disparó" + audit de accesos + retención/erasure son SP8 (absorbe O1-O3/S5/S7 del `docs/plans/2026-06-03-pre-deploy-hardening-backlog.md`).

## Cómo arranca cada SP

1. Branch `saas/spN-<nombre>` desde `main` (post-merge del SP anterior si hay dependencia).
2. `superpowers:brainstorming` partiendo de las **decisiones gordas** de la tabla → resolver una a una.
3. `superpowers:writing-plans` → `docs/plans/YYYY-MM-DD-spN-*.md`.
4. `superpowers:subagent-driven-development` (TDD + review de dos etapas por task + review holístico final).
5. PR → CI verde (`backend` + `frontend`) → merge. Actualizar la fila "Estado" de este roadmap.

## Ampliaciones world-class W1–W10 (gap analysis vs Plaid/Sharesight, 2026-06-10)

> Análisis de qué le falta al modelo/arquitectura para ser una plataforma de datos financieros clase Plaid/Sharesight, **más allá** de lo ya cubierto por SP2–SP8. Cada item está mapeado al SP o Phase donde aterriza (las filas de la tabla de arriba ya referencian su W#). Son **ampliaciones de alcance de trabajo ya planeado, NO proyectos nuevos** — el principio "no adelantar features" sigue vigente: cada W se construye cuando su SP/Phase llegue, pero su *decisión de modelo* se toma en el brainstorming de ese SP.
>
> Criterio de priorización: no "qué feature falta" sino **"qué decisión de modelo es cara de retrofitear"**. Plaid es world-class porque su modelo (Institution → Item → Account → Transaction) abstrajo el proveedor desde el día 1, no por sus features. Este repo ya aplicó ese criterio dos veces (RLS antes de tenants reales; multihome antes de la segunda org).

### Tier 1 — Decisiones de modelo (modelar ahora / en su ventana natural; retrofitear es caro)

- **W1 — Abstracción Conexión/Proveedor** _(→ SP7, decisión gorda)_ — **✅ hecho (PR W1, branch `saas/w1-connections`; ver `docs/specs/2026-06-10-tier1-worldclass-model-design.md` + `docs/plans/2026-06-10-w1-connections.md`)**. Hoy el modelo es IBKR-hardcoded de punta a punta (`flex_credentials`, parser/persister/cron Flex). El corazón del modelo Plaid es que el broker es un *dato*, no una premisa: `institutions` (control plane, como TRM) → `connections` (org-scoped: credenciales + estado de sync + `provider_type`) → `sync_runs` (cada corrida: qué trajo, qué cambió, status). **NO** es "soportar Schwab ya" (sería adelantar features): es reshapear `flex_credentials` → `connections` con discriminador de provider y colgar `flex_imports` de una connection, con IBKR como única implementación. Costo hoy: una migración chica. Costo en 2 años: reescribir el pipeline entero. Sharesight vive de esto (~200 brokers detrás de una interfaz uniforme). Ventana natural: el refactor del pipeline en SP7.
- **W2 — Securities master (instrumento como entidad, no string)** _(→ Phase 3 domain layer, antes o durante)_ — **✅ hecho (PR W2, branch `saas/w2-securities-master`; ver `docs/specs/2026-06-10-tier1-worldclass-model-design.md` T1-D7..D9 + `docs/plans/2026-06-10-w2-securities-master.md`)**. Hoy `symbol` es TEXT del XML. Toda plataforma de portafolios seria tiene tabla de **instrumentos** global (control plane, mismo patrón data-plane/control-plane ya lockeado para TRM): `instruments(id, symbol, isin/cusip/conid, asset_class, exchange, currency)`; los hechos referencian el instrumento, no el string. Por qué acá: (a) IBKR ya emite `conid`/`isin` en los XMLs (hoy muere en `raw_attrs`); (b) ticker changes y corporate actions (FB→META, splits) rompen el agrupado por símbolo string — y el FIFO de Phase 3 agrupa por `(account, symbol)`; (c) yfinance (Phase 6) y `regime.py` necesitan un punto canónico para metadata del instrumento. Primer consumidor real: el FIFO domain layer.
- **W3 — Restatement log (bitemporalidad pragmática)** _(→ Phase 3 / persister)_. La semántica snapshot actual es `DO UPDATE last-updated-by`: si IBKR restatea un valor histórico (ya observado: wash-sale adjustments, pool CLOSED_LOT cambiando), el valor viejo se pisa **silenciosamente**. Para software fiscal es grave: "el número que declaré en el Form 210 ya no es reproducible". Versión rigurosa-pero-lean (NO bitemporalidad completa): un **`restatement_log`** poblado por el persister — cuando un `DO UPDATE` cambia un valor *material* (no mark prices; sí pnl, quantity, cost basis), registrar `(tabla, natural_key, columna, old, new, flex_import_id)`. Convierte riesgo silencioso en señal auditable y es la fundación del feature diferenciador "tu declaración 2025 cambió desde que la presentaste". Es la extensión natural de dos lecciones ya pagadas: "fire-and-forget = bug invisible" (TRM) y "hash de documento entero es engañoso para fuentes mutables" (D13) — misma clase de bug (mutación sin señal) a nivel de *valor*, no de *fila*.

### Tier 2 — Patrones de plataforma (ampliar el alcance de SPs ya planeados)

- **W4 — State machine por conexión** _(→ SP7)_ — **✅ hecho (PR W1, branch `saas/w1-connections`; `ingest/connection_state.py`, estados `active/degraded/reauth_required/disabled`)**. Plaid modela el Item con estados user-facing: `healthy / degraded / reauth_required / disconnected`. El health endpoint actual (R4) es telemetría de *corridas* (`ingest_log`), no estado de la *conexión*. Cuando el token Flex expire, el usuario debe ver "tu conexión necesita re-autenticación" con CTA — no un banner genérico "ingest failed hace 48h". Mecánica: columna `status` + transiciones en la tabla `connections` de W1.
- **W5 — Eventos de dominio + transactional outbox** _(→ SP5, decidir junto con el motor de cola)_. SP5 elige motor pensando en *jobs*; la decisión gemela es **eventos de dominio de primera clase** (`sync.completed`, `lot.crossed_730d`, `restatement.detected`) con patrón outbox (el evento se escribe en la misma transacción que el hecho; un dispatcher lo entrega después). Un solo mecanismo habilita: alertas 730d push (Phase 3), webhooks para terceros (futuro), y export a `renta` por evento en vez de polling. Retrofitear outbox con la cola ya en producción es doloroso.
- **W6 — Reconciliación semántica post-ingest** _(→ SP7 o Phase 3 recompute job)_. El pipeline valida **sintaxis** (fail-loud parsers, poison pill, known tags); lo que Sharesight tiene encima es validación **semántica** como stage de producción: ¿suma de open lots cuadra con `<OpenPosition>`? ¿el cash ledger cierra? ¿realized PnL del pool CLOSED_LOT cuadra con closed_lots? Ya existe en tests (FIFO parity del replay suite) — promoverlo de test a **stage del pipeline** que corre en cada ingest y marca la corrida `ok_with_warnings` si no cuadra.
- **W7 — API pública versionada + idempotency keys** _(→ post-SP6, **explícitamente todavía no**)_. Central en Plaid pero solo paga cuando hay terceros consumiendo. Cuando llegue: versioning por fecha estilo Plaid/Stripe + header `Idempotency-Key` en writes + webhooks firmados con retries/backoff/dead-letter. No construir antes de SP6.

### Tier 3 — Producto / operación

- **W8 — Sandbox/demo org con data sintética** _(→ SP3 onboarding)_. El sandbox de Plaid es de sus features más queridas. El 80% ya existe: los fixtures sanitizados (`U99999001/2/3`, Test Owner). Convertirlos en una org demo seedeable transforma un artefacto de testing en feature de onboarding/demo/ventas — costo marginal mínimo.
- **W9 — Export completo per-org (data portability)** _(→ SP8)_. Un solo mecanismo sirve tres amos: el JSON para `renta` (spec maestro, pantalla Reporte), el derecho de portabilidad (Habeas Data/GDPR → erasure de SP8), y backups verificables por el usuario. Diseñarlo **una vez** en SP8, no tres veces.
- **W10 — Observabilidad per-tenant (OTel)** _(→ SP8)_. SP8 hoy dice "audit log"; la ampliación world-class es traces/métricas etiquetadas por org (OpenTelemetry). Sin eso no se puede responder "¿por qué el tenant X tiene ingests lentos?" ni detectar noisy neighbors. Complementa (no reemplaza) el audit log de accesos.

### Anti-scope explícito (decidido NO hacer — no re-discutir sin señal nueva)

- **Microservicios / split del monolito.** El monolito modular es correcto hasta señales reales de escala (Plaid mismo arrancó monolito). Decisión arquitectónica #12 sigue vigente.
- **Sharding / cell-based / multi-region.** RLS en un Postgres rinde hasta miles de orgs.
- **Bitemporalidad completa** (system-time + valid-time en cada tabla). W3 (restatement log) da el 90% del valor con el 10% del costo.
- **Adelantar W-items fuera de su SP/Phase.** Cada W se decide en el brainstorming de su SP y se construye ahí — adelantarlos es el mismo anti-patrón que adelantar SP2–SP8.

### Fundación existente que YA es world-class (no necesita más)

RLS fail-closed con boot guard (PR #7) · UPSERT idempotente por natural key (Phase 2.5) · append-only + replay capability (`xml_bytes`, A0) · poison pill + dead-letter (R2) · drift tests endurecidos (H3) · multihome per-org (PR #10). No gold-platear lo que ya cumple el estándar.
