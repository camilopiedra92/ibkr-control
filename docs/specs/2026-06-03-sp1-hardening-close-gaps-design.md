# SP1 Hardening — Close real gaps (cron silent-failure, account-collision, app_rls secret)

> Follow-up a SP1 (mergeado, PR #5). Cierra los **holes/deuda reales** que la convergencia dejó, SIN adelantar features de SP2–8 (eso sería su propio anti-patrón). Distingue deuda real de cortes de alcance legítimos.

## Contexto

El review holístico final + una auditoría honesta post-merge identificaron tres items que NO son "features sin construir" sino **deuda/holes dentro de la remit de SP1**:

1. **Cron Flex = silent failure bajo `app_rls`.** `_run_flex_for_all_orgs` enumera `DISTINCT organization_id FROM flex_credentials` en una sesión sin contexto RLS → default-deny → 0 orgs → el cron corre y no fetchea nada **en silencio**. El proyecto prohíbe los silent failures. (Antes etiquetado "gap SP7"; corregido: un cron que corre y no hace nada calladito es un hole, no un feature diferido.)
2. **Colisión de cuenta cross-org → `IntegrityError` crudo.** En `_ensure_accounts`, bajo RLS el org B nunca ve la cuenta del org A (RLS-filtrada) → siempre intenta INSERT → choca el `uq_accounts_ibkr_account_id` (UNIQUE global, por diseño: una cuenta broker es single-org) → excepción cruda sin manejar (HTTP 500 feo). `ON CONFLICT DO NOTHING` NO alcanza: el re-SELECT RLS-filtrado sigue sin ver la fila → los child rows fallan.
3. **Password `app_rls` hardcodeado** (`PASSWORD 'app_rls_pw'`) en el baseline + `db/rls.py`, en un repo **público**. Solo protege una DB de dev (prod overridea), pero es un credential fijo a la vista.

**NO en scope (cortes de alcance legítimos — construirlos ahora = adelantar SP2–8):** enforcement de grants (SP2) · cron tenant-aware sofisticado / cola durable / rate-limit por org (SP5/SP7) · observabilidad rica de fallas TRM (SP8) · multi-party/onboarding UI (SP3) · KMS (SP4). La suite 210s es **perf, no deuda** (elección de correctness) → cleanup de perf aparte.

## Decisiones

### H1 — Cron funcional vía `SECURITY DEFINER` (data plane / control plane)

La enumeración de "qué orgs tienen credenciales" es una operación de **control plane** (cross-tenant, de sistema). El request path sigue como `app_rls` (RLS-enforced). En vez de darle a `app_rls` un rol/conexión `BYPASSRLS` amplio (que podría leer/escribir cualquier cosa cross-tenant, y adelantaría la capa de secrets/roles de SP4), se expone **una capacidad cross-tenant acotada y auditable**:

- Función `system_credentialed_org_ids() RETURNS SETOF bigint`, **`SECURITY DEFINER`**, owned por un rol que bypassea RLS (el rol de migración/superuser que ya corre el baseline). Devuelve `SELECT DISTINCT organization_id FROM flex_credentials` (bypassa RLS porque corre como su definer). `GRANT EXECUTE` a `app_rls`.
- El scheduler llama la función (sobre su conexión `app_rls` existente) para enumerar; luego, **per org**, `flex_job.run(organization_id=...)` corre como `app_rls` con `SET LOCAL app.current_org` → el trabajo per-tenant queda **100% RLS-enforced** (defense-in-depth intacto).
- **Least-privilege:** `app_rls` gana exactamente UNA capacidad cross-tenant (esta función), nada más. Mecanismo estándar de Postgres, no hack. Cuando SP5/SP7 traigan workers de background reales, el rol de sistema dedicado es *su* fundación; esta función coexiste.
- Elimina el silent failure: el cron enumera de verdad y fetchea per org. Migración nueva (el baseline `05943d9efcdb` ya está mergeado).

### H2 — Colisión cross-org → error de dominio + 409 genérico

`_ensure_accounts` envuelve el INSERT de cuentas nuevas en un `SAVEPOINT`; ante `UniqueViolation` sobre `ibkr_account_id` levanta un error de dominio (`AccountClaimedError` o similar). Los endpoints que ingieren (wizard `step2/save`/`step3`, `imports/upload`) lo mapean a **HTTP 409 genérico** ("una de las cuentas ya pertenece a otra organización") — **sin leak de existencia** (no revela cuál org). Cierra el `IntegrityError` crudo/500.

### H3 — `app_rls` password por env

El rol se crea/altera con la password leída de `APP_RLS_PASSWORD` (default dev-only `'app_rls_pw'` para tests). El app + los tests leen el mismo env/default para construir el DSN `app_rls`. Saca el credential fijo del repo público; prod setea `APP_RLS_PASSWORD`. (Es un paso mínimo hacia SP4-secrets, no el KMS completo.)

## Testing

- H1: test bajo `app_rls` que prueba que `system_credentialed_org_ids()` devuelve org-ids cross-tenant (dos orgs sembrados como owner) — falla sin la función (default-deny); + el flujo del cron enumera+corre per org.
- H2: dos orgs, org B sube XML referenciando una cuenta del org A → 409 genérico, sin filas creadas para B, sin leak.
- H3: el role-create lee el env; default dev mantiene la suite verde.
- Full suite verde + ruff + boot smoke.
