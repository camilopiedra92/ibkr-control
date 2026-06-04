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
| **SP3** | **AuthN & onboarding** | SP1 | ⏳ | **fastapi-users vs IdP (Auth0/Clerk/WorkOS/Keycloak)**; MFA; invites; tenant provisioning UX; wizard multi-party |
| **SP4** | **Secrets / encryption** | SP1 | ⏳ | **qué KMS (Vault transit vs cloud KMS)**; data-key por tenant; migrar el AES-GCM single-key actual |
| **SP5** | **Durable jobs** | SP1 | ⏳ | **Temporal vs Celery vs Arq**; rate-limit IBKR por tenant; idempotencia |
| **SP6** | **Billing** | SP1, SP3 | ⏳ | Stripe; planes; metering; entitlements |
| **SP7** | **Ingest tenant-aware** | SP1, SP5 | ⏳ | refactor del pipeline Flex; cron org-iterante sobre cola durable; **TRM queda global** (dato público, no tenant-scoped) |
| **SP8** | **Compliance / Observability** | SP1 | ⏳ | audit log de accesos; retención / erasure (Habeas Data/GDPR); **absorbe O1-O3, S5, S7 del pre-deploy-hardening backlog** |

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
