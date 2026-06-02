# Schema Hardening + Multi-User Identity Model — Design

**Fecha:** 2026-06-02
**Estado:** Aprobado (brainstorming) — listo para writing-plans
**Autor:** Test Owner + Claude
**Predecesor de schema:** `1702589703e1` (counterparties + drop transfer_lots)

## Contexto

Auditoría pre-Phase-3 del schema detectó tres items candidatos a "tech debt":

1. `Base` sin `naming_convention` → `alembic --autogenerate` puede emitir renames
   espurios; el drift test (solo-tablas) no los atrapa.
2. UNIQUE global en `accounts.ibkr_account_id` y `trades.transaction_id`
   (no per-user) — documentado en Phase 2.6 como "bug latente multi-user".
3. `counterparties` sin `user_id` — hereda el mismo techo del #2.

La investigación reveló que **#2 y #3 NO son bugs**: son correctos para el modelo
real de identidad-compartida + `participations`. El "fix" documentado en Phase 2.6
(UNIQUE compuesto con `user_id`) era un **misdiagnóstico**. Solo #1 es un fix de
correctitud real.

Adicionalmente se aclaró el modelo multi-user objetivo a largo plazo: **Joint Holder (y
otros) loguean** y ven su participación; **un contador loguea con permisos
read-only** sobre los datos del owner. Esa última relación (leer-sin-poseer) no la
expresa `participations` — es un modelo de grants que hoy no existe. Se construye
acá (H5) como primitiva de autorización, ANTES de que Phase 3 cree las pantallas
que la consumen, para que esas pantallas nazcan ya scopeadas en vez de parchearse.

## Hallazgo central: el modelo de identidad ya es correcto

Verificado contra la DB viva (2026-06-02):

- **7 tablas de hechos** (`trades`, `closed_lots`, `open_position_lots`,
  `cash_transactions`, `transfers`, `change_in_dividend_accruals`,
  `open_dividend_accruals`) linkean por `account_id` (o el arc src/dst para
  transfers) + `flex_import_id`. **Ninguna tiene `user_id`.**
- Las **5 tablas con `user_id`** son exactamente las que deben ser user-scoped:
  `flex_credentials`, `flex_imports`, `ingest_log`, `participations`,
  `user_settings`.

### La distinción que sostiene el diseño

| Columna | Semántica | Resuelve |
|---|---|---|
| `account_id` | **Ownership / visibilidad** | "de quién es el hecho y quién lo ve" → vía `participations` (Joint Holder ve la conjunta al 50%) |
| `flex_import_id` | **Provenance / audit** | "en qué fetch se vio primero" → trazabilidad, `ON DELETE SET NULL` |

Un trade es account-scoped, pero su `flex_import_id` apunta a una fila con
`user_id`. No hay contradicción: el import es la auditoría de *quién fetcheó*
(acción user-scoped); el hecho derivado es de la *cuenta* (compartido). Joint Holder no
necesita que el import sea "suyo" para ver los trades de la conjunta — los lee por
`account_id` filtrado por SUS participations.

**Conclusión:** cambiar accounts/trades a user-scoped rompería el modelo (la
conjunta necesitaría filas duplicadas). El trabajo correcto es **dejar la
estructura intacta y hacer la invariante explícita y durable**.

## Decisiones locked

### H1 — `naming_convention` determinística en `Base.metadata`

Convención estándar Alembic/SQLAlchemy:

```python
naming_convention = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
```

`Base.metadata = MetaData(naming_convention=...)`. Toda constraint/índice sin
nombre explícito pasa a tener nombre predecible derivado de tabla + columnas,
eliminando la clase de deriva donde autogenerate quiere renombrar constraints
auto-nombradas por Postgres.

**Nombres explícitos coexisten con la convención (decisión deliberada):**

- **Natural keys compuestas largas** (`change_in_dividend_accruals` tiene 8
  columnas en su UNIQUE): la convención `uq_%(table_name)s_%(column_0_N_name)s`
  generaría un identificador de **>63 chars** que Postgres **trunca
  silenciosamente** → colisiones/ambigüedad. Para estas se mantiene un nombre
  explícito semántico alineado al prefijo de la convención:
  `uq_<table>_natural_key` (reemplaza los actuales `<table>_natural_key` sin
  prefijo). Una sola fuente de verdad por constraint: o la convención, o un
  nombre explícito corto y semántico — nunca un truncado opaco.
- **CHECK constraints**: el token `ck_%(table_name)s_%(constraint_name)s` ya
  requiere un `constraint_name` semántico (ej. `ck_transfers_src_arc`) — se
  mantienen como están, ya matchean la convención.

**Robustez confirmada:** los upserts del persister usan
`on_conflict_do_*(index_elements=[columnas])`, NO `constraint=<nombre>`. Postgres
infiere la constraint por las columnas → renombrar constraints **no rompe** los
upserts. El único `name=` referenciado por código son los de los modelos mismos,
que se actualizan al prefijo de convención en un solo lugar.

### H2 — Squash a baseline pristino (NO append de migración de rename)

**Precondición habilitante:** no hay prod desplegado + datos dev descartables
(confirmado por el usuario). Es la última ventana limpia para squashear antes del
primer deploy a Coolify.

- Borrar las 18 migraciones actuales.
- Generar **un solo baseline** vía `alembic revision --autogenerate` contra DB
  vacía con los modelos importados → crea todo el schema con nombres de convención
  desde el nacimiento.
- Revisar a mano el baseline para asegurar que autogenerate no omitió:
  CHECK constraints, `server_default`, `comment=`, índices con expresiones
  (`ingest_log_user_started_idx` usa `started_at DESC`).
- Downgrade del baseline = drop de todo el schema (estándar para migración inicial).

**Rechazado — Approach 1A (append rename migration):** dominado una vez que no hay
datos/prod que preservar. Dejaría el schema naciendo con nombres "feos" y
corrigiéndolos después — la arqueología legacy que se quiere evitar.

**Qué NO se reescribe:** los specs/plans dated (`2026-05-25-*`, `2026-06-02-*`) que
referencian revision ids viejos son **registro histórico veraz** — quedan intactos.
Solo se actualiza `CLAUDE.md` (doc vivo).

### H3 — Drift test endurecido (compara constraints + índices, no solo tablas)

El hueco que dejó pasar la deriva original: el test solo comparaba existencia de
tablas. Se extiende para que `alembic.autogenerate.compare_metadata` (contra un
container fresco con `upgrade head`) incluya constraints, índices, server_defaults,
y falle ante cualquier diff. **Este test es el guard que hace seguro el squash de
H2** y previene que la deriva vuelva.

### H4 — Invariante de identidad compartida, explícita y durable

Sin cambio estructural. Codificar la intención para que no se re-misdiagnostique:

- **`comment=` a nivel tabla** (persiste en `pg_description`, reborn con el squash):
  - `accounts`: identidad COMPARTIDA; sin `user_id` a propósito; ownership en
    `participations`; `ibkr_account_id` UNIQUE global es correcto.
  - `counterparties`: identidad compartida externa (espeja accounts); sin `user_id`.
  - Las 7 tablas de hechos: account-scoped; visibilidad vía `participations`; sin
    `user_id`; `transaction_id` UNIQUE global correcto (hecho pertenece a la cuenta).
- **Nombres de constraint** post-squash codifican intención
  (`uq_accounts_ibkr_account_id`, `uq_trades_transaction_id`).
- **`CLAUDE.md`**: retirar la lección de Phase 2.6 ("bug latente / UNIQUE compuesto
  con user_id") y reemplazar con el rationale correcto (misdiagnóstico; el modelo
  shared-identity + participations es el correcto).

### H5 — Multi-user + RBAC del contador (BUILD)

Se construye ahora como **primitiva de autorización**, ANTES de que Phase 3 cree
las pantallas que la consumen. Justificación de secuenciación: no existe hoy
ningún endpoint que lea hechos (`/api/lots`, `/api/trades` son Phase 3+), así que
no hay retrofit — Phase 3 nace ya scopeada. La primitiva es 100% testeable en
aislado con usuarios sintéticos.

**G1 — Tabla `data_access_grants` (en el baseline H2):**

- `grantor_user_id` → `grantee_user_id`, rol con CHECK `IN ('read_only')`.
- `valid_from` (NOT NULL) / `valid_to` (nullable) → revocable + acotable a
  temporada fiscal. Vigencia evaluada contra `CURRENT_DATE`.
- PK `(grantor_user_id, grantee_user_id, valid_from)` (espeja `participations`).
- FK a `users` con `ON DELETE CASCADE` en ambos lados.
- CHECK `grantor_user_id <> grantee_user_id` (no auto-grant).
- CHECK `valid_to IS NULL OR valid_to > valid_from`.
- **Tabla separada de `participations`**, NO un flag: mezclarlas forzaría un `pct`
  nullable ("contador participa 0%") — estado sin sentido semántico. Poseer vs
  poder-leer son dos relaciones con invariantes propias → dos tablas.

**G2 — Resolver de autorización (módulo domain puro):**

```python
visible_account_ids(session, user, on_behalf_of=None) -> set[int]
```

- `on_behalf_of=None` → participations propias de `user` vigentes (hoy).
- `on_behalf_of=X` → valida grant read-only vigente `X → user`; devuelve las
  participations de `X`. Sin grant vigente → 403.

Función pura sobre la DB → los tests de aislamiento son tabla de verdad.

**G3 — Modelo de contexto: parámetro `on_behalf_of` explícito, stateless.**

Decisión locked (clase mundial, sin estado oculto):

- Param de query opcional en endpoints de lectura; **default = uno mismo** (el
  owner no lo pasa — caso 99%, sin verbosidad de URL).
- El contador, al omitirlo, obtiene set **vacío** (sus participations propias son
  vacías) → lo empuja a fijar contexto e imposibilita merge accidental de clientes.
- **Rechazado:** merged view (mezclar declaraciones de dos personas = hazard
  fiscal) y contexto en sesión/token (stateful, oculto, driftea).

**G4 — Read-only enforced por diseño, no por chequeos dispersos:**

`on_behalf_of` se honra **solo en endpoints de lectura**. Todos los writes
(ingest, upload, setup, settings, credentials) operan sobre los recursos propios
de `current_user`. El contador no tiene flex_credentials ni datos propios → no
puede escribir en los del owner porque no existe endpoint que escriba "datos de
otro usuario". El read-only es estructural, no un guard que se pueda olvidar.

**G5 — Visibilidad binaria; `pct` es capa de dominio:**

Tener participation (cualquier `pct`) → se ve toda la actividad de la cuenta. El
50% de Joint Holder se aplica en la matemática fiscal (Phase 3+), no en qué filas ve (no
se puede ver "medio trade").

**G6 — CRUD de grants (owner-only) + dependency:**

- `POST /api/grants` (crear: referencia al grantee por email), `GET /api/grants`
  (los que otorgué + los que me otorgaron), `DELETE /api/grants/{...}` (revocar).
  Solo el grantor crea/revoca sus grants.
- Dependency FastAPI `require_account_scope` envuelve G2 → contrato que Phase 3
  consume. Resuelve `(current_user, on_behalf_of)` a un `set[account_id]` o 403.
- **Tests de aislamiento:** A no ve nada de B sin grant; grantee read-only no
  escribe; grant expirado (`valid_to < hoy`) no da acceso; `on_behalf_of` sin
  grant → 403; owner omite param → ve lo suyo.

## Alcance

### IN (lo que ejecuta el plan de implementación)

- H1: `naming_convention` en `Base.metadata`.
- H2: squash a baseline pristino + wipe DB dev.
- H3: drift test endurecido (constraints + índices + defaults).
- H4: `comment=` en tablas + corrección de `CLAUDE.md`.
- H5: tabla `data_access_grants` + resolver `visible_account_ids` + dependency
  `require_account_scope` + CRUD de grants + tests de aislamiento (G1-G6).

### OUT (consumido en fases futuras)

- Endpoints de lectura de hechos (`/api/lots/*`, etc.) que scopean vía el resolver
  → Phase 3.
- Login/registro y UI real de Joint Holder / contador (la primitiva queda lista y
  testeada; la UI la activa su fase).
- Aplicar `pct` en matemática fiscal → Phase 3+ (domain layer).

## Plan de verificación

- `alembic upgrade head` en container fresco → schema idéntico a modelos.
- Drift test endurecido (H3) → 0 diffs de constraints/índices/defaults.
- `uv run pytest -q` → suite verde (los tests existentes que referencian nombres
  de constraint hand-named — ej. `closed_lots_natural_key` en upserts — se
  actualizan a los nombres de convención si aplica).
- `\d+` en psql muestra los `comment=` de H4.
- **H5 — tests de aislamiento** (usuarios sintéticos A, B, contador C):
  - A sin grant no ve cuentas de B (`visible_account_ids` disjunto).
  - C con grant read-only A→C ve las cuentas de A vía `on_behalf_of=A`.
  - C con `on_behalf_of=B` (sin grant) → 403.
  - C omitiendo `on_behalf_of` → set vacío (no error).
  - Grant expirado (`valid_to < hoy`) → 403.
  - C no puede crear/revocar grants de A; no accede a writes.
- Re-ingesta de XMLs (acción del usuario, post-wipe) reproduce los datos sanos.

## Riesgos

| Riesgo | Severidad | Mitigación |
|---|---|---|
| Autogenerate del baseline omite CHECK/default/comment | Media | Review manual + drift test H3 como guard objetivo |
| Natural key compuesta larga truncada a 63 chars por la convención | Media | Nombre explícito `uq_<table>_natural_key` (ver H1); el baseline no aplica la convención a estas |
| Upserts del persister rompen por rename de constraint | **Baja (descartada)** | Verificado: usan `index_elements=[columnas]`, no `constraint=`; Postgres infiere por columnas |
| Tests referencian nombres hand-named viejos (`<table>_natural_key`) | Baja | Los `name=` viven solo en los modelos; se actualizan al prefijo de convención en un lugar |
| Squash pierde historial granular | Aceptado | Pre-1.0, bajo valor; specs dated preservan la narrativa |
| `apscheduler_jobs` no está en migraciones (runtime-created) | N/A | Sin cambio — sigue auto-creándose al boot, igual que hoy |
