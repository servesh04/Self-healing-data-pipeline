# Data Contracts — App Expansion Proposal

**Agent:** contracts · **Worktree:** `C:\Stuff\shdp-contracts` · **Branch:** `feature/contracts`
**Base commit:** `e0567b7` (`main`)
**Status:** implemented in this branch; ready for orchestrator reconciliation.

---

## 1. Goal and non-goals

Allow an authenticated user to upload a Pandera contract definition (YAML or JSON),
validate it against the **existing** contract specification, preview its columns, register
it under a name, and manage it (list / detail / versions / delete) — without touching the
filesystem, without weakening the existing YAML registry, and without invalidating
historical runs.

**Non-goals**

- Editing/replacing the existing file contracts (`default`, `compact`) — they stay immutable.
- Editing `POST /api/runs`. Selecting an uploaded contract works through the existing
  `contract_name` field once the registry overlay below is loaded.
- Defining pipeline sources for uploaded contracts. Source resolution belongs to ingestion
  (`POST /api/runs` already rejects a source that does not belong to the selected contract).
- Production-grade RBAC. The existing shared bearer token is the only auth, as documented.

---

## 2. Existing behaviour this reuses

| Existing piece | How it is reused |
|---|---|
| `pipeline.contract._validated_spec` | The single source of truth for contract shape, dtype allow-list and constraints. Uploads call it directly; no second validator is written. |
| `pipeline.contract.contract_from_snapshot` | Builds a real Pandera `DataFrameSchema` from the uploaded spec. If Pandera cannot build it, registration fails. |
| `pipeline.contract.contract_version` | Canonical SHA-256 over `sort_keys` JSON — the same hash stored on `run_records.contract_version` and checkpointed in `contract_snapshot`. |
| `pipeline.contract._UniqueKeyLoader` | Safe YAML loader that already rejects duplicate mapping keys. It cannot construct arbitrary Python objects. |
| `pipeline.contract.get_registry()` | Extended with a process-local **uploaded-contract overlay**. |
| `run_records.contract_version` + `contract_snapshot` | Already written by `POST /api/runs`; historical runs keep replaying the pinned snapshot through `contract_for_state`, independent of the registry. |
| `services/store` conventions | `get_pool()`, `async with pool.connection()`, idempotent `create table if not exists`, `timestamptz default now()`, JSONB. |

### Registry overlay (the compatibility layer)

`backend/pipeline/contract.py` gains:

```python
_uploaded: dict[str, Contract] = {}

def set_uploaded_contracts(contracts: dict[str, Contract]) -> None: ...
def clear_uploaded_contracts() -> None: ...
def uploaded_contracts() -> dict[str, Contract]: ...

class ContractRegistry:
    def names(self) -> list[str]:
        return sorted(set(self._paths) | set(_uploaded))
    def get(self, name: str) -> Contract:
        if name in self._paths:            # file contracts always win
            return load_contract(self._paths[name], name)
        if name in _uploaded:              # uploaded overlay
            return _uploaded[name]
        raise ValueError(f"unknown contract: {name}")
```

- **File contracts remain immutable and authoritative.** A registration whose name matches a
  file contract is rejected with `409`, so an uploaded contract can never shadow one.
- `default` is a file contract, so it is never deletable.
- `services/contract_store.load_into_registry()` validates every stored spec through
  `contract_from_snapshot(spec, name, version)` and swaps the whole map atomically in memory.
  A corrupt DB row fails the load rather than silently registering a half-valid schema.

---

## 3. Endpoints

All endpoints are under the existing FastAPI app, require `Authorization: Bearer <token>`
(`Depends(require_token)`), and return FastAPI's standard `{"detail": "..."}` on error.
Base path: `/api/app/contracts`. Router registration (one line, orchestrator):

```python
from routers import contracts
app.include_router(contracts.router)
```

### 3.0 Shared JSON objects

`summary` (used by list and nested nowhere else):

```json
{
  "name": "orders_v2",
  "version": "5f0c…64-hex…",
  "source": "file",
  "deletable": false,
  "column_count": 4,
  "columns": ["order_id", "customer_id", "region", "revenue"],
  "created_at": "2026-11-03T09:12:44.120231+00:00"
}
```

`created_at` is `null` for file contracts. `source` is `"file"` or `"upload"`.
`deletable` is `true` only for uploaded contracts.

`column` preview object (inside `column_preview`):

```json
{
  "name": "revenue",
  "dtype": "float64",
  "nullable": false,
  "unique": false,
  "allowed": null,
  "min": 0
}
```

`allowed` is `null` when the contract does not declare `allowed`; otherwise a list of strings.
`min` is `null` when not declared. Defaults mirror `_build_column`: `nullable=true`,
`unique=false`.

`detail` (used by `GET /{name}` and the `POST` success response):

```json
{
  "name": "orders_v2",
  "version": "5f0c…",
  "source": "upload",
  "deletable": true,
  "row_count_min": 20,
  "columns": ["order_id", "revenue"],
  "column_count": 2,
  "column_preview": [
    {"name": "order_id", "dtype": "int64", "nullable": false, "unique": true, "allowed": null, "min": null},
    {"name": "revenue", "dtype": "float64", "nullable": false, "unique": false, "allowed": null, "min": 0}
  ],
  "spec": {
    "columns": {
      "order_id": {"dtype": "int64", "nullable": false, "unique": true},
      "revenue": {"dtype": "float64", "nullable": false, "min": 0}
    },
    "row_count_min": 20
  },
  "created_at": "2026-11-03T09:12:44.120231+00:00",
  "updated_at": "2026-11-03T09:12:44.120231+00:00",
  "versions": [
    {
      "version": "5f0c…",
      "source": "upload",
      "column_count": 2,
      "current": true,
      "created_at": "2026-11-03T09:12:44.120231+00:00"
    }
  ]
}
```

For file contracts: `source="file"`, `deletable=false`, `created_at`/`updated_at` are `null`,
and `versions` has exactly one entry with `created_at: null`, `current: true`.

> PostgreSQL `jsonb` normalises object key order (it sorts keys). Column order in `columns`
> therefore follows jsonb's ordering after a DB round-trip; the pipeline's Pandera schema is
> order-independent (`strict=True` is set-based), so this is display-only.

---

### 3.1 `GET /api/app/contracts`

- **Auth:** bearer.
- **Request:** none.
- **Success `200`:**
  ```json
  {"contracts": [ /* summary objects, sorted by name */ ]}
  ```
  Contains **both** file contracts and uploaded contracts. Called by the Data Contracts page
  and can also be used by the shell's contract selector.
- **Errors:** `401`, `503` if the contract tables cannot be reached.

Notes: lazily runs `ensure_schema()` + reloads the overlay on the first call per process, so
the list self-heals if the startup hook was missed.

### 3.2 `POST /api/app/contracts/preview`

Preview/validate an upload **without persisting it**. This is what powers requirement 4
(column preview) and requirement 2/8 (validate before register).

- **Auth:** bearer.
- **Request:** `multipart/form-data`
  | field | type | required | notes |
  |---|---|---|---|
  | `file` | file | yes | YAML or JSON; max 256 KiB. The format is detected from the content, not the filename; content is parsed in memory and never written to disk. |
  | `name` | text | no | Proposed name; if present it is validated and `name_available` is computed. |
- **Success `200`:**
  ```json
  {
    "name": "orders_v2",
    "name_available": true,
    "format": "yaml",
    "version": "5f0c…",
    "row_count_min": 20,
    "columns": ["order_id", "revenue"],
    "column_count": 2,
    "column_preview": [ /* column objects */ ],
    "spec": { "columns": { "...": {} }, "row_count_min": 20 }
  }
  ```
  `name` is `null` and `name_available` is `null` when the field is omitted.
- **Errors:**
  - `401` missing/invalid token.
  - `413` payload exceeds 256 KiB (checked after reading `cap + 1` bytes).
  - `422` invalid name (`^[a-z][a-z0-9_-]*$`, ≤ 64 chars), malformed YAML/JSON, duplicate
    mapping keys, empty/missing `columns`, more than 200 columns, unknown dtype, invalid
    `nullable`/`unique`/`allowed`/`min`, non-finite `min`, or a non-UTF-8 upload.
  - `503` contract tables unavailable (not needed for preview in practice; included for
    consistency because the router dependency initialises schema).

### 3.3 `POST /api/app/contracts`

Register a validated contract in Postgres.

- **Auth:** bearer.
- **Request:** `multipart/form-data`, same caps as preview.
  | field | type | required | notes |
  |---|---|---|---|
  | `file` | file | yes | YAML or JSON contract. |
  | `name` | text | yes | `^[a-z][a-z0-9_-]*$`, 1–64 chars, not an existing file or uploaded contract name. |
- **Success `201`:** a `detail` object (same shape as `GET /{name}`), timestamps populated.
- **Errors:**
  - `401` unauthenticated.
  - `409` `contract name already exists` — matches an existing file contract (`default`,
    `compact`, …) or an already-registered upload. Also returned if a concurrent insert wins
    the unique constraint.
  - `413` upload too large.
  - `422` any validation failure listed under preview.
  - `503` database unavailable.
- **Side effects:** inserts one `app_contracts` row + one `app_contract_versions` row in a
  single transaction, then reloads the in-process registry overlay so the contract is
  immediately selectable by `POST /api/runs` and visible in `GET /api/config/contracts`.
- **Never:** writes a file, executes uploaded content, accepts a path, or trusts the filename.

### 3.4 `GET /api/app/contracts/{name}`

- **Auth:** bearer.
- **Success `200`:** `detail` object. File contracts are resolved through the registry;
  uploaded contracts come from `app_contracts` + `app_contract_versions`.
- **Errors:** `401`; `404 unknown contract: <name>`; `503`.

### 3.5 `DELETE /api/app/contracts/{name}`

- **Auth:** bearer.
- **Success `200`:**
  ```json
  {"deleted": true, "name": "orders_v2"}
  ```
- **Errors:**
  - `401` unauthenticated.
  - `403` the target is a file-based contract (`file-based contracts are immutable`) —
    covers `default` and `compact`. No file is ever touched.
  - `404` unknown contract.
  - `503` database unavailable.
- **Side effects:** `delete from app_contracts where name = %s`; `app_contract_versions` rows
  cascade. The overlay is reloaded. Historical runs are unaffected: they replay the
  checkpointed `contract_snapshot` + `contract_version`, not the live registry.

---

## 4. Database

New tables created idempotently by `services.contract_store.ensure_schema()`.
No existing table is altered.

```sql
create table if not exists app_contracts (
    name       text        primary key
        check (name ~ '^[a-z][a-z0-9_-]*$'),
    spec       jsonb       not null,
    version    text        not null,
    source     text        not null default 'upload'
        check (source in ('upload')),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists app_contract_versions (
    name       text        not null references app_contracts(name) on delete cascade,
    version    text        not null,
    spec       jsonb       not null,
    source     text        not null default 'upload'
        check (source in ('upload')),
    created_at timestamptz not null default now(),
    primary key (name, version)
);

create index if not exists idx_app_contract_versions_name_created
    on app_contract_versions (name, created_at desc);
```

- `app_contracts` is the current pointer/resolved spec; `app_contract_versions` is the
  append-only history (unique `(name, version)`, exactly as proposed). The DELETE cascade
  means removing a contract removes its history; historical **runs** keep their own snapshot.
- `version` is the existing `contract_version(spec)` SHA-256, so it matches
  `run_records.contract_version` byte-for-byte.
- On `insert`, `source`/`created_at` defaults are used explicitly by the service rather than
  trusting client input.
- Migration: run `scripts/migrate_app_contracts.py` under the owner role (new file in this
  branch), or call `await contract_store.ensure_schema()` during a migration. In demo mode the
  router also calls it lazily on first request. Render production uses a read-only runtime
  role, so the owner migration is required there.

---

## 5. Frontend

**Route:** `/contracts` — owned by the integration agent.
**Page component:** `frontend/src/pages/ContractsPage.jsx` (`export default`), designed to sit
inside the existing shell, no sidebar/nav of its own.

**Files**

| File | Purpose |
|---|---|
| `frontend/src/pages/ContractsPage.jsx` | Page layout, list/detail state, upload flow orchestration. |
| `frontend/src/components/Contracts/ContractsList.jsx` | Registered contracts list with source/version/column metadata. |
| `frontend/src/components/Contracts/ContractUploader.jsx` | File picker, name field, preview → register flow, validation errors. |
| `frontend/src/components/Contracts/ContractPreview.jsx` | Column preview table (name, dtype, nullable, unique, allowed, min). |
| `frontend/src/components/Contracts/ContractDetail.jsx` | Selected contract detail, version history, delete action. |
| `frontend/src/api/contracts.js` | Self-contained API module. |

**API module contract (pinned by orchestrator)**

```js
const BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')
// token: localStorage['shdp.session'].token first, then import.meta.env.VITE_SHARED_TOKEN
```

Exports: `listContracts()`, `previewContract(file, name)`, `registerContract(file, name)`,
`getContract(name)`, `deleteContract(name)`. Errors throw an `Error` carrying `.status` and
`.detail` so the page can distinguish `409`/`422`/`413`.

**Design system:** Tailwind v4 tokens already in `frontend/src/index.css`
(`bg-surface`, `border-line`, `text-ink-muted`, `text-signal`, `text-fail`, `text-pass`,
`font-display`, `font-mono`). No new dependency, no chart library.

**UX flow**

1. Page loads → `GET /api/app/contracts` → list of file + uploaded contracts.
2. “Upload contract” opens the uploader.
3. Choose file + enter name → **Preview** → `POST /preview`.
   - Success: green “valid contract” line, SHA-256 version, column preview table, **Register** button.
   - Failure: the backend `detail` string is shown in `text-fail`; register stays disabled.
4. **Register** → `POST /api/app/contracts` → 201 detail → list refreshes, the new contract is selected.
5. Clicking a contract → `GET /api/app/contracts/{name}` → detail + version history.
6. Uploaded contracts show a **Delete** button with a confirm step; file contracts show an
   “immutable” note instead.

No polling. No route wiring in this branch.

---

## 6. Shared-file changes required (orchestrator)

1. **`backend/main.py`** — register the router and load the overlay at startup.
   ```python
   from routers import contracts          # add to the existing routers import
   from services import contract_store    # add to the existing services import
   app.include_router(contracts.router)   # next to the other include_router calls

   # inside lifespan, inside the existing try block, after external_store init:
   await contract_store.ensure_schema()
   await contract_store.load_into_registry()
   ```
   `load_into_registry()` must run in **both** auth modes; `ensure_schema()` is owner-only in
   production and can be omitted there once the migration has run.
2. **`backend/services/store.py`** (optional, production hardening) — add `app_contracts` and
   `app_contract_versions` to `verify_runtime_schema()`'s `required` tuple.
3. **`backend/routers/config.py`** — **no change needed**. `GET /api/config/contracts` already
   projections `get_registry().names()`; the overlay makes uploaded contracts appear with the
   exact existing shape `{name, version, columns}`. This was verified by test.
4. **`frontend/src/App.jsx`** — add the route `/contracts` → `ContractsPage`.
5. **`frontend/src/api/client.js`** — unchanged. `ContractsPage` uses its own module so the
   session-token rule is honoured without touching the shared client.
6. **`POST /api/runs`** — unchanged. It calls `get_registry().get(contract_name)` and snapshots
   `contract.spec` / `contract.version`, so an overlay-loaded contract works as-is. Selection
   in the UI is the existing `contractName` selector plus the run trigger's `contract_name`.

---

## 7. Security

- YAML is parsed with `_UniqueKeyLoader` (a `yaml.SafeLoader` subclass); `!!python/...` tags
  raise a constructor error. JSON uses `json.loads` with a duplicate-key hook.
- Uploaded bytes exist only in request memory. Nothing is written to disk; no filename, path,
  URL or SQL string is accepted.
- Server-side caps: 256 KiB payload, 200 columns, 64-char name.
- Names are constrained to `^[a-z][a-z0-9_-]*$` and cannot collide with file contracts.
- Only uploaded contracts are deletable; DELETE never touches `config/contracts/`.
- No `eval`, `exec`, `pickle`, `yaml.unsafe_load` or dynamic import anywhere in the flow.

---

## 8. Test plan (`tests/test_app_contracts.py`, offline)

All tests run with the existing zero-network conftest (fake pool, no Postgres).

1. **Valid YAML** → parsed, `_validated_spec` passes, version == `contract_version(spec)`,
   preview lists dtype/nullable/unique/allowed/min correctly.
2. **Valid JSON** → parsed with detected format `json`.
3. **Duplicate JSON keys** → `422`-class `ValueError`.
4. **Malformed document** (unclosed bracket / bad indentation) → rejected.
5. **Missing columns** (`columns` absent, empty, or not a mapping) → rejected.
6. **Invalid / unsupported dtype** (`dtype: decimal`) → rejected with the existing message.
7. **Invalid constraints**:
   - `nullable: "no"` (non-bool), `unique: 1`
   - `allowed` on an `int64` column
   - `allowed` with duplicates / non-strings / empty list
   - negative / non-int `row_count_min`
   - `min: true` (bool) and `min: nan`
8. **Invalid name** (uppercase, leading digit, `../x`, > 64 chars) → rejected.
9. **Oversize payload** → `ContractTooLarge` / HTTP 413.
10. **Duplicate names**: name of a file contract (`default`) and an already-registered upload
    → `ContractConflict` / HTTP 409.
11. **DB lifecycle** with an in-memory fake pool: `ensure_schema` DDL emitted, register →
    list → get (versions) → delete → 404/immutability errors, cascade delete SQL.
12. **Registry overlay**:
    - `load_into_registry()` makes `get_registry().get(name)` resolve the uploaded spec;
    - `names()` includes the uploaded name;
    - `/api/config/contracts` (the existing function) includes it with the unchanged shape;
    - `default` still resolves to the file contract and an upload cannot shadow it;
    - `contract_for_state` with a **historical typed snapshot** still validates after the
      overlay is cleared and after the contract is deleted (pinned hash compatibility);
    - a pinned hash mismatch still raises.
13. **YAML safety**: `!!python/object/apply:os.system` payload is rejected and no file is
    created.
14. **Router**: `preview_contract` / `register_contract` / `delete_contract` functions invoked
    directly with a fake `UploadFile` and monkeypatched store, asserting status codes/shapes.

Frontend: `npm run build` in `frontend/` (no new deps).

---

## 9. Integration notes / limitations

- The overlay is **process-local**. With more than one uvicorn worker, an upload handled by
  worker A is not visible to worker B until it restarts or serves a contracts request
  (each contracts request reloads the overlay). Render runs a single process; documented
  rather than adding polling/notifications.
- Uploaded contracts have **no bundled CSV sources**. `GET /api/config/sources?contract_name=`
  returns the sources declared in `config/sources.yaml` for that name (normally none), and
  `pipeline.sources._csv_source` only accepts `default`/`compact`. The ingestion agent's
  `upload:<dataset_id>` resolver is the intended source path for these contracts.
- **Shell contract selector refresh.** The existing `TopBar` fetches `/api/config/contracts`
  once on mount. After registering a contract on `/contracts`, the new name is visible to the
  API immediately (overlay reload) but the already-mounted selector needs a refetch to show it.
  Integration should call the shell's `fetchContracts()` after a successful registration, on
  route change to `/contracts`, or on window focus. This page deliberately imports no shell
  store, so it cannot do that itself without coupling to the app-shell agent's store.
- `app_contract_versions` currently holds one row per name because duplicate registration is
  rejected (requirement 8). The table exists so a future `PUT`/re-upload flow can append
  versions without a schema change, and so the current version is queryable by hash.

---

## 10. Verification performed in this branch

| Check | Result |
|---|---|
| `python -m pytest tests/test_app_contracts.py -q` | **36 passed** (offline fake pool, no Postgres/network) |
| `python -m pytest -q` (whole existing suite) | **271 passed**, 1 pre-existing warning |
| `npm install` + `npm run build` (frontend) | success (only the pre-existing >500 kB chunk warning) |
| Isolated Vite compile of `ContractsPage` + all `components/Contracts/*` | success (they are not yet imported by `App.jsx`) |
| FastAPI TestClient probe (router + fake pool) | preview 200, register 201, list 200 with `/api/config/contracts` unchanged, delete file 403, delete upload 200, unknown 404, invalid dtype 422, no auth 401 |

**Environment limitation:** no live Postgres was available to the agent, so the DDL and SQL
were exercised through the fake pool and a TestClient probe, not against Neon. Run
`scripts/migrate_app_contracts.py` once against the target database before enabling the
startup hook in production.

