# Wizard Redesign — IBKR Account Auto-Detection

**Status**: Approved 2026-05-24 (brainstorming session)
**Target tag**: `v0.2.2-wizard-redesign`
**Spec author**: Test Owner + Claude
**Implementation skill**: `superpowers:writing-plans` → `superpowers:subagent-driven-development`

## Goal

Reemplazar el wizard actual (que pide al usuario que tipee manualmente IDs de cuenta + alias + % antes de tener visibilidad de qué cuentas tiene en IBKR) por un flujo "detect-first": pedir credenciales, fetchear el reporte actual de IBKR, auto-detectar las cuentas, y solo ahí pedir al usuario que confirme alias + % para las que se descubrieron.

Al mismo tiempo: eliminar la deuda existente de **shadow accounts F** (sufijo `F` IB-UK Limited regulatorias, NAV=$0, sin trades) que el persister actual inserta como cuentas separadas, contaminando la tabla `accounts` con 2x el número de filas que el usuario realmente tiene.

## Background

### Estado actual (problemas)

1. **Wizard step 2 pide IDs ciegos**: `frontend/src/components/wizard/Step2Accounts.tsx` arranca con `[{ibkr_account_id: "U", alias: "", pct: "1.0000"}]`. El usuario tiene que recordar/copiar los 3 IDs U######## de su cuenta IBKR. Fuente típica de typos.
2. **No hay validación contra cuentas reales**: si el usuario tipea `U99999999` que no existe en ninguno de sus XMLs, el wizard acepta y guarda un Account row + Participation huérfanos.
3. **F-accounts pueblan la tabla**: el persister actual (`backend/src/ibkr_control/ingest/flex/persister.py:259+`) hace `_ensure_accounts` con TODOS los `accountId` que aparezcan en el XML. IBKR XMLs reales tienen 6 accountIds para el usuario actual (3 main + 3 F shadow), resultando en 6 rows en `accounts`. Las F nunca pueden tener participations (regex `^U\d{8}$` del wizard las rechaza) y quedan orphan.
4. **Wizard fuerza re-validación contra IBKR aunque ya hayas guardado credenciales**: usuario reportó error `1001 Statement could not be generated at this time` cuando entró al wizard luego de haber guardado credenciales vía `/settings` directamente. El wizard volvió a hacer `SendRequest` aunque era redundante.
5. **Wizard state stored como flags JSONB en `users.setup_progress`** desync de la realidad: `step1_credentials=false` aunque `flex_credentials` tenga row. Bug observado durante smoke test.

### Cómo lo maneja el sibling `renta`

Investigación documentada vía subagent research (2026-05-24). Findings completos:

- **Allowlist pattern** en `/Users/owner/Development/renta/documentos/ibkr_flex/loader.py:28`:
  ```python
  _ACCT_PCT = {"U99999001": 0.50, "U99999002": 1.00, "U99999003": 1.00}
  # ...
  if acct not in acct_pct:
      continue  # F-accounts silently skipped
  ```
- **F-accounts son IB-UK Limited regulatory shadow** (`tests/test_doc_ibkr_flex_full.py:101`):
  ```python
  def test_f_accounts_son_shadow_uk(flex):
      f_accts = [a for a in flex.accounts if a.cuenta.endswith("F")]
      assert all(a.es_shadow_uk for a in f_accts)        # ib_entity == "IB-UKL"
      assert all(s.total_usd == 0 for s in f_snaps)       # NAV = $0
  ```
- F-accounts nunca contienen trades, open positions, ni closed lots. Solo cash transactions tipo fees/journals.

**Decisión derivada**: ibkr-control aplica el mismo filtro pero a **nivel persister** (no allowlist hardcoded — porque ibkr-control es multi-user-ready y las cuentas son user-defined). El persister rechaza cualquier `account_id` que termine en `F` antes de crear el `Account` row.

## Decisiones locked (durante brainstorming)

| # | Decisión | Rationale |
|---|---|---|
| D1 | Re-ingest desde cero (Migration H wipe DB) | Re-correr el wizard con la nueva lógica desde estado limpio. Más simple que patchear in-place. |
| D2 | Step 2 fallback: retry automático + opción "subir XML manual" | Cubre tanto throttling transient (1001) como IBKR offline indefinido. |
| D3 | XML del Step 2 detect se persiste como `flex_imports` (no descartar) | Evita re-fetch del cron del día siguiente; tablas pobladas con 2026 YTD desde día 1. |
| D4 | Full discipline: brainstorm → spec → plan → execute | Mismo patrón que D5+D2 persistent state. |
| D5 | Step 1 y Step 2 son **dos pantallas distintas** | Cleaner separation; Step 2 puede fallar y retomarse sin re-pedir creds. |
| D6 | TRM backfill auto-trigger silencioso post-Step 2 (background) | El usuario no debería esperar visiblemente por TRM. Si falla, el cron del día siguiente lo recupera. |
| D7 | `/setup` post-completion redirect a `/dashboard` | Settings es donde se edita; Setup es onboarding one-shot. |
| D8 | `suggested_alias` viene del `<AccountInformation accountAlias=...>` del XML | Pre-poblar alias en Step 2 para minimizar input del usuario. |
| D9 | F-account filter a nivel **persister** (no parser, no validator) | Última frontera de confianza antes de INSERT. Garantiza la invariante "`accounts` nunca contiene F" sin importar el código path que llegue. |
| D10 | Retry policy de IBKR 1001: **server-side**, 3 attempts con backoff 5s/15s/30s | Cliente bloquea ~60s en peor caso; alternativa polling es 10x más compleja para single-user. |
| D11 | Stash de XMLs en Step 3 upload: **in-memory backend con TTL 1h** | Mismo patrón que `JobTracker`. Container restart → user re-sube (aceptable). |
| D12 | `step1_credentials` + `step2_accounts` + `step3_n_xmls_uploaded` son **derived**; `step3_xmls` + `setup_completed_at` son **stored** | Elimina la clase de bugs "flag dice X pero DB dice Y". |

## Section 1: F-account filter at persister

### Archivo afectado

`backend/src/ibkr_control/ingest/flex/persister.py`

### Cambio

Agregar helper + aplicar filtro en cada loop de persist:

```python
def _is_shadow_account(ibkr_account_id: str) -> bool:
    """IB-UK Limited regulatory shadow account (NAV=0, no fiscal data).

    Per renta sibling spec: F-accounts only carry fees/journals;
    never trades, open positions, or closed lots. Filtered at persist
    time to keep `accounts` table free of accounts that shouldn't have
    participations.
    """
    return ibkr_account_id.endswith("F")
```

Aplicar en:

1. **`_ensure_accounts`**: filtrar la lista `ibkr_ids` antes del SELECT/INSERT.
2. **Trades persist loop**: `if _is_shadow_account(t.ibkr_account_id): continue`.
3. **CashTransactions persist loop**: idem.
4. **ClosedLots persist loop**: idem.
5. **OpenPositionLots persist loop**: idem.
6. **DividendAccruals (change_in + open)**: idem.
7. **Transfers**: skip entero si `src_ibkr_account_id` O `dst_ibkr_account_id` es shadow (defensive — no debería pasar pero validate).
8. **AccountInformation (en accounts table creation)**: skip — no crear `Account` row.

### Invariante

> Después de cualquier persist (cron Flex YTD, manual upload, Step 2 detect, Step 3 commit), la tabla `accounts` nunca contiene rows donde `ibkr_account_id LIKE '%F'`.

Validada por: `test_persister_f_filter.py` (sección 5).

### Edge cases

- XML donde el XML completo tiene solo F-accounts (caso degenerado: ingest no inserta nada, retorna n_rows=0, no es error).
- XML donde un trade referencia un F-account: skip ese trade. (No debería pasar en XMLs reales; defensive.)
- Account ID que termina en F pero no es shadow real: improbable. IBKR conventions usan `F` consistentemente para UK shadow. Si alguna vez aparece un caso real (ej. cuentas en otras jurisdicciones), reabrir spec.

## Section 2: Endpoints + state machine

### Nuevos endpoints

Todos bajo prefix `/api/setup/*`.

#### `POST /api/setup/step1/save`  [NEW — reemplaza step1/validate]

Solo guarda credenciales. NO toca IBKR.

```python
payload: {token: str, query_id: str}
response 200: {ok: True}
side effect:
  - Upsert flex_credentials (encrypt token con AES-GCM)
  - NO valida vs IBKR
  - step1_credentials se vuelve derived (siguiente call a /setup/state lo refleja)
```

#### `POST /api/setup/step2/detect`  [NEW]

Fetch IBKR YTD + parse + persist + return accounts detectadas (filtradas F).

```python
payload: {}  (usa flex_credentials del user)
response 200: {
  detected_accounts: [
    {
      ibkr_account_id: "U99999001",
      suggested_alias: "Joint Account",     # del <AccountInformation accountAlias=...>
      account_type: "Joint",                # del attr accountType (info read-only)
      account_holder: "TEST OWNER"   # del attr name (info read-only)
    },
    ...
  ],
  flex_import_id: 1,
  ingest_summary: {n_trades: ..., n_cash_tx: ..., ...}  # del 2026 YTD
}
response 503: {detail: {code: "IBKR_BUSY", attempts: 3}}  # post-retry exhaustion
response 401: {detail: "INVALID_TOKEN"}
response 400: {detail: "QUERY_NOT_FOUND"}
response 502: {detail: {code: "IBKR_ERROR", code: <ibkr_code>, message: <ibkr_msg>}}
response 504: {detail: "IBKR_TIMEOUT"}

retry policy (server-side, internal):
  attempts = [5s, 15s, 30s] delays between 4 tries
  IBKRBusyError (1001) → retry
  Other errors → propagate immediately

side effect (only on 200):
  - Inserta 1 row en flex_imports (source=web_service, anyo=2026, year_status=rolling)
  - Persiste trades/cash_tx/etc del 2026 YTD (con F-filter aplicado)
  - NO crea Account rows ni Participations (eso es Step 2 save)
```

#### `POST /api/setup/step2/detect_from_xml`  [NEW]

Fallback cuando IBKR no responde. Parse-only, no persiste.

```python
payload: multipart/form-data (XML file)
response 200: {
  detected_accounts: [...],  # mismo shape que step2/detect
  parsed_only: True
}
response 422: {detail: {code: "PARSE_ERROR", line: int, message: str}}

NO side effects en DB. El XML se descarta tras parse.
```

#### `POST /api/setup/step2/save`  [REWRITE]

```python
payload: {
  accounts: [
    {ibkr_account_id: "U99999001", alias: "Joint", pct: "0.5000"},
    ...
  ]
}
response 200: {ok: True}
response 400: {detail: "ACCOUNT_NOT_DETECTED", ibkr_account_id: "U99999999"}
  # rechaza account_ids no presentes en detected_accounts (anti-typo)
  # Backend valida contra accounts present in the user's flex_imports
response 422: pct fuera de rango [0, 1]

side effect:
  - Upsert accounts (alias)
  - Cerrar participations vigentes + crear nuevas con valid_from=today
  - step2_accounts se vuelve derived (siguiente call a /setup/state lo refleja)
  - background task: TRM full backfill (per D6)
    - dispatched via FastAPI BackgroundTasks (runs after response sent, same event loop)
    - errors logged via stdlib logging.exception, NOT propagated to client
    - idempotent (ON CONFLICT DO NOTHING per día) — safe en retries
    - se dispara aquí, NO en finish, porque D6 dice "post-Step 2"
```

#### `POST /api/setup/step3/upload`  [NEW]

Upload 1 XML, parse, stash. NO commit.

```python
payload: multipart/form-data (XML file)
response 200: {
  flex_import_temp_id: "uuid",  # in-memory stash key
  detected_accounts: [...],     # mismo shape que step2/detect.detected_accounts
  new_accounts: ["U99999999"],  # accountIds NOT en accounts table (excl. F)
  period: {from: "2024-01-01", to: "2024-12-31"},
  anyo: 2024,
  sha256: "...",  # para dedup check
}
response 409: {detail: "DUPLICATE_XML", flex_import_id: <existing>}
  # XML con mismo SHA-256 ya existe en flex_imports (no importa si stashed o persisted)
response 422: PARSE_ERROR

side effect:
  - Stash en backend in-memory dict {temp_id: (parsed_data, sha256, ttl_expires_at)}
  - TTL 1h desde upload
  - NO toca DB hasta step3/commit
```

#### `POST /api/setup/step3/save_new_accounts`  [NEW]

Solo si Step 3 reveló new_accounts.

```python
payload: {
  accounts: [
    {ibkr_account_id: "U99999999", alias: "...", pct: "1.0000"},
    ...
  ]
}
response 200: {ok: True}

side effect:
  - Upsert accounts + create participations
  - Mismo behaviour que step2/save pero permite accounts no detectadas en YTD
    (porque pueden venir solo de histórico de años cerrados)
```

#### `POST /api/setup/step3/commit`  [NEW]

```python
payload: {temp_ids: ["uuid1", "uuid2", ...]}
response 200: {flex_import_ids: [2, 3], total_rows_inserted: 530}
response 400: {detail: "UNRESOLVED_NEW_ACCOUNTS", accounts: [...]}
  # si algún XML stashed tiene new_accounts no resueltas
response 410: {detail: "TEMP_ID_EXPIRED", temp_id: "..."}
  # stash venció (TTL 1h)

side effect:
  - Persiste cada XML stashed como flex_imports (source=manual_upload, year_status=sealed)
  - Persiste trades/cash_tx/etc con F-filter
  - Limpia el stash de los temp_ids procesados
  - setup_progress.step3_xmls = True
```

Commit con `temp_ids: []` (array vacío) → marca step3_xmls=True sin persistir nada. Equivale al "skip históricos".

#### `POST /api/setup/finish`  [NEW]

```python
payload: {}
response 200: {ok: True}
response 400: {detail: "INCOMPLETE_SETUP"}
  # si no hay participations OR step3_xmls=False

side effect:
  - users.setup_completed_at = now()
  - NO dispara TRM (eso ya se hizo en step2/save per D6)
```

Idempotente: si ya está completado, retorna 200 ok sin re-disparar TRM.

#### `GET /api/setup/state`  [MODIFIED]

```python
response: {
  step1_credentials: bool,        # derived: exists(flex_credentials where user_id)
  step2_accounts: bool,           # derived: exists(participations where user_id)
  step3_xmls: bool,               # stored: setup_progress.step3_xmls
  step3_n_xmls_uploaded: int,     # derived: count(flex_imports where source=manual_upload)
  setup_completed_at: datetime | null,  # stored: users.setup_completed_at
  # nuevos campos:
  detected_accounts: [...] | null,  # último resultado de step2/detect (cached en memoria, opcional)
  pending_stash_temp_ids: [...]     # XMLs en stash sin commit (TTL aware)
}
```

### State storage

```
DERIVED (computed on read):
  - step1_credentials      ← exists(flex_credentials WHERE user_id)
  - step2_accounts         ← exists(participations WHERE user_id)
  - step3_n_xmls_uploaded  ← count(flex_imports WHERE user_id AND source='manual_upload')

STORED (users.setup_progress JSONB):
  - step3_xmls             ← bool (explicit user decision: "I'm done uploading")

STORED (users column):
  - setup_completed_at     ← timestamp irremplazable
```

## Section 3: Frontend

### Reestructura de archivos

```
frontend/src/components/wizard/
├── Stepper.tsx                  [KEEP]
├── WizardPage.tsx               [NEW — orchestrator]
├── Step1Credentials.tsx         [REWRITE — solo guarda]
├── Step2Detect.tsx              [NEW — spinner + fallback]
├── Step2ConfigureAccounts.tsx   [NEW — tabla edit alias+pct]
├── Step3Upload.tsx              [REWRITE — multi-file drag-drop]
├── Step3NewAccountsModal.tsx    [NEW]
├── Step3Commit.tsx              [NEW]
└── StepFinish.tsx               [NEW — splash + redirect]

frontend/src/hooks/
├── useSetupState.ts             [REWRITE — derived fields from new /api/setup/state]
├── useWizardNav.ts              [NEW — central state, screen transitions]
└── useStep2Detect.ts            [NEW — retry polling, fallback toggle]

frontend/src/app/(setup)/setup/page.tsx
└── delega a WizardPage.tsx; redirect a /dashboard si setup_completed_at != null
```

### Wizard screen state machine

```typescript
type WizardScreen =
  | "step1"
  | "step2_detect"
  | "step2_configure"
  | "step3_upload"
  | "step3_new_accounts"
  | "step3_commit"
  | "finish";

function deriveScreen(state: SetupState, transient: TransientState): WizardScreen {
  if (state.setup_completed_at) return "finish";  // → redirect
  if (!state.step1_credentials) return "step1";
  if (!state.step2_accounts) {
    return transient.detected_accounts ? "step2_configure" : "step2_detect";
  }
  if (!state.step3_xmls) {
    if (transient.unresolved_new_accounts.length > 0) return "step3_new_accounts";
    if (transient.pending_stash_temp_ids.length > 0) return "step3_commit";
    return "step3_upload";
  }
  return "finish";
}
```

### Pre-poblado de Step 2 ConfigureAccounts

```tsx
const [rows, setRows] = useState<Row[]>(
  detected_accounts.map(a => ({
    ibkr_account_id: a.ibkr_account_id,  // readonly input
    alias: a.suggested_alias || "",       // editable, pre-filled
    pct: "1.0000",                        // editable
    _hint: a.account_type || a.account_holder
      ? `IBKR: ${[a.account_type, a.account_holder].filter(Boolean).join(" — ")}`
      : ""
  }))
);
```

`ibkr_account_id` es **readonly** (no input editable) — viene del XML, no del usuario.

### Multi-file upload UX (Step 3)

```tsx
<Dropzone onDrop={handleFiles} accept=".xml" multiple>
  Drop XMLs aquí o click para elegir
</Dropzone>

<UploadList>
  {files.map(f => (
    <FileRow
      key={f.name}
      name={f.name}
      status={f.status}  // "uploading" | "parsed" | "error" | "duplicate"
      detectedAccounts={f.detected_accounts}
      newAccounts={f.new_accounts}
      error={f.error}
      onRemove={() => removeFile(f.name)}
    />
  ))}
</UploadList>

{hasUnresolvedNewAccounts && (
  <Step3NewAccountsModal
    accounts={collectedNewAccounts}
    onSave={handleSaveNewAccounts}
  />
)}

<div className="flex justify-between">
  <Button variant="link" onClick={skipHistory}>
    Saltar — no tengo XMLs históricos
  </Button>
  <Button onClick={commitAll} disabled={!canCommit}>
    Importar {parsedFiles.length} XMLs
  </Button>
</div>
```

Cada `step3/upload` call es independiente (paralelo). FileRow muestra status individual. El commit final es batch (un solo `step3/commit` con array de temp_ids).

### Skip históricos

Link "Saltar" llama `step3/commit` con `temp_ids: []` → step3_xmls=True → finish.

## Section 4: Error handling

### Mapeo IBKR → HTTP → UI

| IBKR error | Backend HTTP | UI |
|---|---|---|
| 1001 | 503 IBKR_BUSY (post-retry) | Retry visible (countdown) + link "Subir XML manual" después de 30s |
| 1003/1004 | 401 INVALID_TOKEN | Mensaje rojo + link "Volver a Step 1" |
| 1005 | 400 QUERY_NOT_FOUND | Mensaje rojo + link "Volver a Step 1" |
| Otros | 502 IBKR_ERROR | Mensaje rojo + botón Reintentar + link "Manual upload" |
| Timeout | 504 IBKR_TIMEOUT | Mensaje rojo + botón Reintentar + link "Manual upload" |

### Retry policy (server-side, step2/detect)

```python
DELAYS = [5, 15, 30]  # seconds

async def step2_detect_with_retry(creds):
    for attempt_idx, delay in enumerate(DELAYS + [None]):
        try:
            return await fetch_and_parse_accounts(creds)
        except IBKRBusyError:
            if delay is None:
                raise HTTPException(503, detail={
                    "code": "IBKR_BUSY",
                    "attempts": attempt_idx + 1
                })
            await asyncio.sleep(delay)
```

Max blocking time client: ~60s.

### UI feedback Step2Detect

```tsx
<div className="text-center space-y-4 py-12">
  <Spinner />
  <h2>Descargando reporte de IBKR…</h2>
  <p className="text-sm">
    IBKR puede tardar 30 segundos a 3 minutos.
    {attemptCount > 1 && ` Reintento ${attemptCount}/3.`}
  </p>
  {elapsedSeconds > 30 && (
    <p className="text-xs">
      ¿Sigue tardando?{" "}
      <Button variant="link" onClick={cancelAndShowUploadFallback}>
        Subir XML manual en lugar
      </Button>
    </p>
  )}
</div>
```

El link "Subir XML manual" aparece después de 30s reales (no después del primer retry).

### Step 3 partial failure

- Cada upload es independiente. Un parse error en file3 no rompe file1/file2.
- `commit` solo procesa los temp_ids stashed exitosamente.
- User puede remover el roto o ignorar y commitear los buenos.

### Race conditions

| Scenario | Behavior |
|---|---|
| User refresca durante step2/detect | `/setup/state` retorna `step2_accounts=false` → vuelve a `step2_detect`. Backend NO está corriendo nada (el HTTP call murió). User clickea "Iniciar detección" otra vez. |
| User abre `/setup` en 2 tabs simultáneas | Ambas tabs intentan `step2/detect`. IBKR rate-limita la segunda (1001 → server retry → eventualmente 200 o 503). Resultado idempotente vía SHA-256 dedup en `flex_imports`. |
| TRM backfill falla post-finish | Loggeado en `ingest_log`. Cron del día siguiente retoma (TRM idempotente con ON CONFLICT). User puede ver en `/settings` log table. |

### Atomicidad por step

| Step | Transactions | Side effects en failure |
|---|---|---|
| step1/save | 1 (upsert flex_credentials) | None |
| step2/detect | 1 (insert flex_imports + persist all) | Rollback completo si falla a la mitad |
| step2/save | 1 (insert accounts + participations) + background TRM (best effort) | Rollback completo de DB; TRM errors logged |
| step3/upload | 0 (solo memoria) | Stash queda parcial — user puede re-subir |
| step3/save_new_accounts | 1 (insert accounts + participations) | Rollback completo |
| step3/commit | 1 (insert flex_imports + persist all XMLs) | Rollback completo todos los XMLs |
| finish | 1 (UPDATE users.setup_completed_at) | UPDATE rollbacks |

## Section 5: Testing + Migration

### Backend tests

```
backend/tests/
├── ingest/
│   └── test_persister_f_filter.py          [NEW]
│       Fixture XML con U99999999 + U99999999F en TODAS las secciones.
│       Asserts:
│         - accounts: only 1 row (U99999999)
│         - trades, cash_tx, closed_lots, open_lots, transfers, dividend_accruals:
│           CERO rows referenciando U99999999F
│         - n_rows_inserted en flex_imports excluye las F-rows
│
├── api/
│   ├── test_setup_step1.py                 [REWRITE]
│   │   - step1/save no hace ningún HTTP a IBKR (mock FlexClient, verify no calls)
│   │   - Token persiste encrypted (verify decrypt round-trip)
│   │   - Idempotente (re-save sobreescribe sin error)
│   │
│   ├── test_setup_step2_detect.py          [NEW]
│   │   - Mock FlexClient.send_request + get_statement
│   │   - happy path: returns detected_accounts con suggested_alias del XML
│   │   - retry: 1001 → 1001 → ok (3 attempts, verifica delays)
│   │   - permanent failure: 1001 x4 → 503 con attempts=4
│   │   - 401 invalid token → 401 INVALID_TOKEN
│   │   - F-accounts en XML → filtered out de detected_accounts
│   │   - persistence side effect: 1 flex_import row creada con anyo=current_year
│   │   - no Account rows creadas (eso es step2/save)
│   │
│   ├── test_setup_step2_detect_from_xml.py [NEW]
│   │   - upload XML válido → returns detected_accounts (sin F)
│   │   - upload XML solo F-accounts → returns []
│   │   - upload XML malformed → 422 PARSE_ERROR
│   │   - parsed_only: True (verify no flex_import creada)
│   │
│   ├── test_setup_step2_save.py            [REWRITE]
│   │   - happy path: 3 accounts → 3 rows + 3 participations
│   │   - account_id no detectada antes → 400 ACCOUNT_NOT_DETECTED
│   │   - pct fuera [0,1] → 422
│   │   - idempotent: re-save cierra participaciones viejas + crea nuevas
│   │   - background task TRM dispatched (mock asyncio.create_task / executor;
│   │     verify called once with full_backfill=True)
│   │
│   ├── test_setup_step3.py                 [NEW]
│   │   - upload 3 XMLs → 3 temp_ids, todos stashed
│   │   - upload con new_accounts → response los lista
│   │   - upload dup SHA-256 → 409 con flex_import_id existing
│   │   - save_new_accounts antes de commit
│   │   - commit array vacío → step3_xmls=true sin persist
│   │   - commit con unresolved new_accounts → 400 UNRESOLVED_NEW_ACCOUNTS
│   │   - commit con temp_id expirado → 410 TEMP_ID_EXPIRED
│   │
│   ├── test_setup_finish.py                [NEW]
│   │   - sin participations → 400 INCOMPLETE_SETUP
│   │   - sin step3_xmls → 400 INCOMPLETE_SETUP
│   │   - happy path: setup_completed_at = now()
│   │   - re-call finish → noop idempotente
│   │   - NO dispara TRM (eso lo hace step2/save)
│   │
│   └── test_setup_state.py                 [REWRITE]
│       - state derivado: step1_credentials reflects flex_credentials exists
│       - state derivado: step2_accounts reflects participations exists
│       - state stored: step3_xmls from setup_progress JSONB
│       - state stored: setup_completed_at from users column
│       - n_xmls_uploaded derived from flex_imports count
│
└── e2e_api/
    └── test_wizard_full_flow.py            [NEW]
        Integration test:
          register user
          → step1/save
          → mock IBKR for step2/detect (returns 3 accounts + F-accounts in XML)
          → step2/save (3 accounts con alias + pct)
          → step3/upload (2 historical XMLs)
          → step3/commit
          → finish
        Asserts:
          - 3 accounts (NO F-accounts)
          - 3 participations
          - 3 flex_imports (1 YTD + 2 historical)
          - setup_completed_at != null
          - 0 rows con F-account en cualquier tabla derivada
```

### Frontend tests (Playwright + MSW)

```
frontend/e2e/
├── wizard-happy-path.spec.ts                [NEW]
│   Mock backend con MSW.
│   Step 1: fill creds → continue
│   Step 2: spinner → mock returns 3 detected_accounts → tabla rows pre-filled
│   User edita pct U99999001 (0.50) → continue
│   Step 3: skip → finish → redirect /dashboard
│
├── wizard-ibkr-busy-fallback.spec.ts        [NEW]
│   Step 2: mock 503 IBKR_BUSY x3
│   Verify retry UI countdown
│   After 30s, fallback link aparece
│   User clickea → upload form aparece
│   Mock /step2/detect_from_xml returns accounts → flow continúa
│
└── wizard-new-accounts-in-history.spec.ts   [NEW]
    Setup completo con 2 main accounts
    Step 3 upload XML con U99999999 no en accounts
    Modal "Nuevas cuentas" aparece
    Llenar alias + pct → modal cierra → commit habilitado
```

### Migration H

```python
# backend/alembic/versions/<hash>_wipe_for_wizard_redesign.py
"""wipe for wizard redesign

Wipes ALL ingested data per user; preserves flex_credentials so user
doesn't have to re-paste Flex Token. The new wizard re-ingests YTD
in Step 2 and re-uploads historical XMLs in Step 3.

Required because old persister created F-suffix shadow accounts
(IB-UK Limited regulatory) as separate Account rows. The new persister
filters them at INSERT time, but legacy rows would remain orphaned
otherwise.

Revision ID: <autogen>
Revises: e39428dc5cd5
Create Date: 2026-05-24
"""
from alembic import op

revision = "<autogen>"
down_revision = "e39428dc5cd5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Order matters: dependents first.
    op.execute("DELETE FROM open_dividend_accruals")
    op.execute("DELETE FROM change_in_dividend_accruals")
    op.execute("DELETE FROM transfers")
    op.execute("DELETE FROM transfer_lots")
    op.execute("DELETE FROM cash_transactions")
    op.execute("DELETE FROM open_position_lots")
    op.execute("DELETE FROM closed_lots")
    op.execute("DELETE FROM trades")
    op.execute("DELETE FROM flex_imports")
    op.execute("DELETE FROM participations")
    op.execute("DELETE FROM accounts")
    op.execute("UPDATE users SET setup_progress = '{}'::jsonb, setup_completed_at = NULL")
    # Preserve: flex_credentials (token + query_id), apscheduler_jobs.


def downgrade() -> None:
    # Destructive migration — downgrade is no-op (data is lost forever).
    # To recover, re-run the wizard with the new code.
    pass
```

## Implementation notes

### Out of scope

- **Multi-tenant participation model** (Phase 3+): hoy single-user; participations per (user, account) ya support multi-user but UI no expone.
- **TRM data quality monitoring**: TRM job already exists, this spec only changes when it's triggered.
- **Sub-account aliasing** (ej. mostrar U99999001F en `/settings` como info read-only): no para V1. Las F simplemente no existen del lado de la app.
- **Edit accounts post-finish**: no via wizard. Settings ya tiene flow para editar (vía `/api/settings/accounts/*` — TBD si se implementa en Phase 3).

### Tag al cerrar

`v0.2.2-wizard-redesign` — patch bump porque la API pública para Phase 3 no cambia (sigue siendo `accounts` + `participations` con el mismo schema). El UX rewrite + migration destructiva no rompen contratos externos.

### Quién hereda

- **Phase 3 (lotes + classification)** debería poder asumir: `accounts` solo contiene main accounts (no F), todas con participations. Esto simplifica `apply_pct()` (no manejo de "account sin participation").
- **Coolify deploy**: la migration H corre automáticamente al boot del container con el nuevo código (Dockerfile CMD ya hace `alembic upgrade head` desde fix `0c06399`). User experience: el primer login post-deploy ve `/setup` redirige (porque `setup_completed_at` es NULL), wizard arranca desde cero.

### Riesgos conocidos

1. **In-memory stash de Step 3 se pierde con restart**: aceptable (heredada del `JobTracker`). Mitigación V2 = persistir stash en tabla `flex_imports_pending`.
2. **TRM backfill fire-and-forget post-finish**: si falla silenciosamente, user no se entera hasta abrir `/settings`. Mitigación: badge "TRM up to date / pending" en navbar (V2).
3. **Stash temp_id collision**: UUIDs random, colisión probability ~0. No mitigation needed.
4. **Coolify deploy mid-wizard**: si user está mitad-wizard cuando se deploya el nuevo código, su stash se pierde. Mitigación: deploy off-hours.
