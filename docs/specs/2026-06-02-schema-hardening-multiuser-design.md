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
expresa `participations` — es un modelo de grants que hoy no existe y se diseña
acá para una fase futura.

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

### H5 — Modelo multi-user + RBAC del contador (DISEÑO, no build)

Capturado para una fase futura de enforcement. **No se construye nada hoy** — una
tabla vacía sin lector sería tech debt, y su forma exacta depende de decisiones de
enforcement aún no tomadas.

**Primitivas:**

- **`participations`** (ya existe): `user ↔ account` con `pct` temporal
  (`valid_from`/`valid_to`). Ownership + visibilidad. Joint Holder 50% en la conjunta.
- **`data_access_grants`** (futuro): delegación de lectura.
  - `grantor_user_id` → `grantee_user_id`, rol `read_only`.
  - Scope = las participations del grantor (el contador ve todo lo del owner).
  - `valid_from` / `valid_to` nullable → revocable + acotable a temporada fiscal.
  - PK/UNIQUE `(grantor_user_id, grantee_user_id, valid_from)` (espeja participations).
  - **Tabla separada de participations**, NO un flag: mezclarlas forzaría un `pct`
    nullable ("contador participa 0%") — estado sin sentido semántico. Poseer vs
    poder-leer son dos relaciones con invariantes propias → dos tablas.

**Enforcement (fase futura):**

- Dependency FastAPI `visible_account_ids(current_user)` =
  `participations propias ∪ (grants donde grantee=yo → participations del grantor)`.
- Grants read-only bloquean writes (el contador no dispara ingest ni edita).
- Cada query de hechos se scopea por ese set de `account_id`.
- Tests de aislamiento: usuario A no ve cuentas de B salvo grant/participation.

## Alcance

### IN (lo que ejecuta el plan de implementación)

- H1: `naming_convention` en `Base.metadata`.
- H2: squash a baseline pristino + wipe DB dev.
- H3: drift test endurecido (constraints + índices + defaults).
- H4: `comment=` en tablas + corrección de `CLAUDE.md`.

### OUT (diseñado en H5, construido en fase futura)

- Tabla `data_access_grants`.
- Enforcement de authz per-user en la API.
- Login real de Joint Holder / contador.
- Tests de aislamiento multi-user.

## Plan de verificación

- `alembic upgrade head` en container fresco → schema idéntico a modelos.
- Drift test endurecido (H3) → 0 diffs de constraints/índices/defaults.
- `uv run pytest -q` → suite verde (los tests existentes que referencian nombres
  de constraint hand-named — ej. `closed_lots_natural_key` en upserts — se
  actualizan a los nombres de convención si aplica).
- `\d+` en psql muestra los `comment=` de H4.
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
