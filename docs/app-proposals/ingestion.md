# Ingestion proposal — CSV upload, preview, contract selection, run trigger

**Agent:** 3 — Data ingestion
**Branch/worktree:** `feature/ingestion` / `C:\Stuff\shdp-ingestion`
**Status:** scope pre-approved by the orchestrator (see `STATUS.md`, "Phase B approved: n/a (pre-approved scope)"); this document is the canonical contract for the ingestion surface and the reconciliation source for `API_CONTRACT.md` §3.4.

---

## 1. Summary

A single ingestion page (`/ingestion`) plus a small backend dataset store lets a user:

1. upload a bounded CSV,
2. see a 10–20 row preview with column names, inferred types and row count,
3. pick an existing registered data contract and see it before execution,
4. trigger the **existing** LangGraph pipeline via `POST /api/runs`,
5. land on the existing Run Detail page (`/runs/<run_id>`), with upload and pipeline errors surfaced clearly.

Uploaded bytes are stored **only** in Postgres (`bytea`), with the original bytes preserved and a SHA-256 digest. No arbitrary filesystem paths are accepted; no S3 integration is rebuilt (see §9).

---

## 2. Endpoints (new, owned by `backend/routers/datasets.py`)

Router prefix: `/api/app/datasets`, `tags=["datasets"]`, `dependencies=[Depends(require_token)]` (same bearer as every other `/api/*` route). All responses are concrete JSON, no envelope. Timestamps ISO-8601 UTC.

### POST `/api/app/datasets` — upload a CSV

- **Auth:** bearer (`require_token`).
- **Request:** `multipart/form-data` with exactly one file part named `file`.
  - `filename` is metadata only and is never used as a filesystem path. Only the basename is stored.
- **Success 200:** `{...}` dataset record:
  ```json
  {
    "dataset_id": "6f0a…-…",
    "filename": "orders_nov.csv",
    "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "byte_size": 10485,
    "row_count": 120,
    "columns": [{"name": "order_id", "type": "integer"}, {"name": "revenue", "type": "float"}],
    "created_at": "2026-11-03T12:00:00+00:00",
    "reused": false
  }
  ```
  - `reused: true` means an idempotent retry: identical bytes and identical filename were already uploaded; the existing record is returned and **no duplicate row is created** (unique `(sha256, filename)`). Different bytes under the same filename are a new dataset (different digest ⇒ different identity).
- **Errors:**
  - `401` — missing/invalid bearer token.
  - `413` — payload exceeds the byte cap (`MAX_BYTES = 5 MiB`) or the row cap (`MAX_ROWS = 5,000` data rows).
  - `422` — no file part; empty filename; extension is not `.csv` (case-insensitive); empty CSV (header-only or zero bytes); invalid UTF-8 (values are decoded as UTF-8 with BOM tolerance); malformed CSV (`csv` module `strict=True`); duplicate column names.
  - `503` — initial schema creation failed (store unavailable).
  - Wrong data types or missing contract columns are **deliberately accepted** — diagnosing exactly that drift is the pipeline's job, not the uploader's.

### GET `/api/app/datasets?limit=20` — recent uploads *(addition beyond the coordination draft §3.4; listed here for reconciliation)*

- **Auth:** bearer.
- **Response 200:** `{"datasets": [<same record shape as POST, without the payload>]}`, newest first, `limit` clamped to 1–50 (default 20).
- Reuses the catalog UX pattern of `GET /api/config/sources`; the upload page shows it so a user can re-preview or re-run a recent dataset without re-uploading.

### GET `/api/app/datasets/{dataset_id}` — metadata

- **Auth:** bearer.
- **Success 200:** record shape as above (no `reused` key, no payload bytes).
- **Errors:** `404` unknown id; `401`; `503` on schema init failure.

### GET `/api/app/datasets/{dataset_id}/preview?limit=20` — first N rows

- **Auth:** bearer.
- **Query:** `limit` (default 20, clamped 1–50). Rejects larger values with `422` (query validation failure).
- **Success 200:**
  ```json
  {
    "dataset_id": "6f0a…",
    "row_count": 120,
    "rows": [{"order_id": "1", "revenue": "100.5"}],
    "rows_returned": 20,
    "columns": [{"name": "order_id", "type": "integer"}, {"name": "revenue", "type": "float"}],
    "truncated": true
  }
  ```
  - `rows` is the first `limit` **data rows** (10 rows when the dataset has 10; the UI asks for 15 so a 10-row upload renders fully). Cell values are the preserved original strings (never re-parsed), so the preview shows exactly what will enter the pipeline.
  - `truncated` is `true` when `row_count > limit`.
- **Errors:** `404`; `422` (limit out of range or stored row_count inconsistent with the payload); `401`; `503`.

### DELETE `/api/app/datasets/{dataset_id}` — delete an upload

- **Auth:** bearer.
- **Success 200:** `{"deleted": true, "dataset_id": "6f0a…"}`
- **Errors:** `404` unknown id; `401`; `503`.
- Deleting does not cascade to runs: materialization copies the bytes into the run-scoped immutable `source_snapshots` table *synchronously during trigger*, so an in-flight or resumable run is unaffected. A future `POST /api/runs` referencing a deleted dataset fails cleanly (see §4).

### Run trigger — **no new endpoint**

Reuse `POST /api/runs {source_id, contract_name}` verbatim, with `source_id = "upload_<dataset_id>"`:
```json
{ "source_id": "upload_6f0a0a8e-…-…", "contract_name": "default" }
```
Response: `{ "run_id": "<uuid>" }`; the UI navigates to `/runs/<run_id>`. Errors: `422` (unknown contract, unknown snapshot/dataset), `503` (source materialization failed).

---

## 3. Database

One new table, created idempotently (also readable, but not written, by anything else). No ALTERs to existing tables.

```sql
create table if not exists app_datasets (
    id          text primary key,               -- uuid4 hex-less text, generated in Python
    filename    text not null,                  -- sanitized basename; metadata only
    sha256      text not null,                  -- digest of the exact uploaded bytes
    payload     bytea not null,                 -- original bytes, never re-encoded
    byte_size   integer not null,
    row_count   integer not null,               -- data rows (excludes header)
    columns     jsonb not null default '[]'::jsonb,  -- [{name: str, type: str}]
    created_at  timestamptz not null default now()
);

create unique index if not exists app_datasets_content_identity
    on app_datasets (sha256, filename);
```

- **Idempotent migrations:** `upload_store.ensure_schema()` is `create table if not exists` + `create unique index if not exists` — safe to run on every cold start.
- **Long-lived row:** deleted datasets are removed; nothing changes shape.
- **Postgres-only, no filesystem, no object store.**

### Migration instructions for the integrator (the only `main.py` change requested)

This agent cannot edit `backend/main.py`. Two options, option 1 preferred:

1. **Preferred (startup hook, mirrors the other init calls):** in `lifespan`, in the non-production branch next to the other `store.init_*` calls, add:
   ```python
   from services import upload_store
   ...
   await upload_store.ensure_schema()
   ```
   and in `/api/ping`'s non-production branch the same one-liner (idempotent). Until it exists, the router lazily calls `ensure_schema()` on first request (request-time `_ensure_schema_ready` guard, same pattern `backend/routers/runs.py` uses for the checkpointer), so **the feature works end-to-end without any `main.py` change** — the hook just saves the first request's schema check.
2. **No change at all** is also valid; lazy init covers it on both dev and Render.

### Caps

| Cap | Value | Enforced | HTTP |
|---|---|---|---|
| Byte cap | `5 MiB` (5,242,880) | server-side in `upload_store` | 413 |
| Row cap | 5,000 data rows | server-side in `upload_store` | 413 |
| Preview limit | max 50 | server-side query clamp | 422 |

Match the P6 staged-batch caps in spirit (`external_store.MAX_BYTES`, `MAX_ROWS = 5000`).

---

## 4. Source resolution design (`backend/pipeline/sources.py` — the one pipeline file this agent is authorized to touch)

- New constant `UPLOAD_PREFIX = "upload_"` and a `uuid` format regex for the remainder; anything with the prefix but a malformed remainder raises `ValueError` (→ 422 from `POST /api/runs`).
- `materialize_source()` gains one early branch **before** the database-table/JSON/CSV fallbacks:
  ```python
  if source_id.startswith(UPLOAD_PREFIX):
      return await _upload_source(source_id, contract_name, run_id)
  ```
- `_upload_source(source_id, contract_name, run_id)`:
  1. `dataset_id = source_id.removeprefix(UPLOAD_PREFIX)`; validate uuid shape (fail closed).
  2. `row = await upload_store.get_dataset(dataset_id)`; missing → `ValueError("uploaded dataset not found")` (422 via runs router).
  3. `payload = await upload_store.get_bytes(dataset_id)`; the store re-verifies the byte-level SHA-256 against the stored digest.
  4. Persists the per-run immutable snapshot through the **existing** mechanism: `await store.save_source_snapshot(run_id, source_id, payload, digest, row_count, profile_rows)`.
  5. Writes the per-run snapshot file under `SNAPSHOT_DIR` exactly like `_json_source`/the database-table path do.
  6. `MaterializedSource(source_id=same "upload_…" id, path=snapshot file, family=None, ref={"kind": "upload", …}, sha256=digest)` with `ref` = `{"kind": "upload", "source_id": source_id, "dataset_id": dataset_id, "filename": row["filename"], "sha256": digest, "row_count": row["row_count"], "profile_rows": min(row_count, 1000)}`.
  7. `family=None` is the existing precedent (`compact_clean.csv` resolves the same way) and means no distribution baseline / temporal rule apply for the upload itself — the contract's own checks still run, and a curated baseline can be introduced later per `(contract, family)` without changing this code.
- **Contract binding is enforced, minimally:** existence and integrity of the dataset (digest re-verified), plus the contract itself (already validated in `runs.py` before materialization). Column-name/type strictness is *not* enforced at upload/materialization time on purpose: missing columns and wrong types are real input classes this pipeline is built to heal, and rejecting them here would disable the core feature. The selected contract is recorded on the run (`run_records.contract_name`, `source_ref`) and the user is shown it before triggering (UI contract preview, §6).
- `ensure_source_path()` recognizes `"upload"` the same way as `database_table`/`local_json`/`external_batch`: on resume-after-restart the bytes are restored from `source_snapshots` via `store.load_source_snapshot(run_id, expected)` and the digest of the restored file is verified. Deletions of `app_datasets` rows therefore never break a resumable run — onlynew triggers.
- `list_sources()` is untouched: uploaded datasets are cataloged by the datasets router (§2), not by the YAML/file-backed source catalog.

**What this agent does not change:** `backend/routers/runs.py` (reads `source_id` as-is and passes it into `materialize_source`, so `upload_…` flows through unchanged), LangGraph, Pandera, checkpointer, approval flow — nothing.

---

## 5. Backend module layout

| File | Status | Contents |
|---|---|---|
| `backend/services/upload_store.py` | new | `ensure_schema()`, `analyze_csv(payload)` (pure: header/dup-check/row-count/type inference, raises `IngestionError`), `create_dataset(filename, payload) -> (record, reused)` idempotent on `(sha256, filename)`, `get_dataset`, `get_bytes` (digest-verified), `delete_dataset`, `list_datasets`, `MAX_BYTES`, `MAX_ROWS`, `PREVIEW_MAX_LINES` |
| `backend/routers/datasets.py` | new | the four/ five endpoints of §2; `Depends(require_token)`; lazy schema guard; maps `IngestionError` → HTTP status |
| `backend/pipeline/sources.py` | edited (authorized) | `UPLOAD_PREFIX`, `_upload_source`, one routed branch in `materialize_source`, `"upload"` added to `ensure_source_path` |
| `tests/test_app_ingestion.py` | new | offline unit tests (no Postgres required; fake pool objects, the same style as `test_p6.py`) |

`IngestionError` subclasses nothing special: `detail` + `status_code`, caught by the datasets router; inside the pipeline it is re-raised as plain `ValueError` so `runs.py` maps it to 422 without any change there.

---

## 6. Frontend

- **Route:** `/ingestion` (added by the orchestrator to the shell's route table; this agent ships the page only).
- **Page component:** `frontend/src/pages/IngestPage.jsx` — **default export**, renders inside the shell (no own sidebar/nav/topbar). Uses only Tailwind utility classes already used by the existing pages/components (`bg-canvas`, `border-line-*`, `bg-surface`, `text-ink*`, `text-signal`, `text-fail`, mono type scale).
- **Components:** `frontend/src/components/Ingest/PreviewTable.jsx` (`DatasetPreview`: columns metadata table + first N rows table + truncated note).
- **API module:** `frontend/src/api/ingestion.js` — self-contained; does **not** import the shared `client.js` (shared-file rule; token behavior is pinned below). Per the pinned contract:
  - token: read `localStorage["shdp.session"]` first (`{token, user}` JSON), fall back to `import.meta.env.VITE_SHARED_TOKEN`;
  - base URL: `import.meta.env.VITE_API_BASE_URL ?? ''`.
  - multipart requests must not set `Content-Type` manually (browser provides the boundary).
- **Flow in the page:**
  1. drop zone / file picker → `POST /api/app/datasets` (client-side pre-check for extension and size for fast feedback; **all caps re-enforced server-side**);
  2. preview panel: filename, size, digest (short), row count, columns with inferred types, first rows (limit 15);
  3. contract selector fed by `GET /api/config/contracts` (existing shape `{contracts: [{name, version, columns}]}`); uploaded contracts appear there automatically once the contracts agent lands — no cross-branch dependency;
  4. selected-contract summary (name, version, column names) shown **before** execution;
  5. `Run pipeline` → `POST /api/runs {source_id: upload_<id>, contract_name}` → on `{run_id}`, navigate to `/runs/<run_id>`; any error renders in a red error block with the backend `detail`;
  6. recent datasets (`GET /api/app/datasets`) with re-preview and delete actions.
- **Navigation detail for the integrator** (documented because `frontend/package.json` is integration-owned and React Router is not installed on this branch): `ingestion.js` exports `navigateToRunPath(path)` which performs `window.history.pushState` + a `popstate` dispatch — this drives the shell's `BrowserRouter` without needing the `react-router-dom` import at build time. If preferred, the integrator can replace the one-liner with the shell's `useNavigate`; the needed change is documented here, not made in contract-editing files.
- **No Zustand store** is added: the upload flow is page-local `useState`; the run list/overview state remains owned by `useRunStore`.

---

## 7. Shared-file changes required (orchestrator applies in Phase C)

| File | Exact change |
|---|---|
| `frontend/src/App.jsx` | 1 route: `<Route path="/ingestion" element={<IngestPage />} />` (page import only) |
| `frontend/src/api/client.js` | none required (ingestion is self-contained). Optional: swap `ingestion.js`'s request helper for the shared one once it honors the `shdp.session`-first token priority |
| `backend/main.py` | optional one-line `await upload_store.ensure_schema()` in lifespan + `/api/ping` (§3.1); the feature also works without it (lazy init) |
| `backend/routers/datasets.py` | registration: `app.include_router(datasets.router)` |
| `frontend/package.json` | nothing new needed |

---

## 8. Test plan

All offline (the suite has no Postgres; fake pool/cursor objects like `test_p6.py`). File: `tests/test_app_ingestion.py`.

| # | Case | Level |
|---|---|---|
| 1 | valid CSV upload → columns, inferred types, row count, digest | store/pure |
| 2 | invalid extension (`.txt`, uppercase `.CSV` accepted; `.txt`/`.json` rejected) | router/pure |
| 3 | empty CSV (header-only and zero-byte) → 422 | store |
| 4 | missing column relative to a contract → upload succeeds (pipeline's job) | store |
| 5 | wrong data types (numeric column of strings) → upload succeeds; types inferred as `integer`/`float`/`string` | store |
| 6 | large file → byte cap → 413 | store |
| 7 | >5,000 rows → row cap → 413 | store |
| 8 | contract list/selection shapes → `GET /api/config/contracts` reuse (shape asserted against `pipeline.contract.get_registry`) | contract |
| 9 | successful pipeline trigger → `source_id=upload_<uuid>` routes through `_upload_source`, snapshot saved, digest verified | sources w/ monkeypatched store |
| 10 | failed pipeline trigger: unknown dataset; malformed `upload_` id; deleted dataset; digest mismatch → `ValueError` paths | sources |
| 11 | idempotent retry: duplicate POST with identical bytes+filename returns the same `dataset_id`, `reused: true`, one row in the fake table | store |
| 12 | different bytes/new content under an existing filename → new dataset (not silently deduped) | store |
| 13 | resume: `ensure_source_path` restores bytes from `source_snapshots` and verifies digest for `kind: upload` | sources |
| 14 | DELETE removes the dataset; GET after delete → 404 path | store |
| 15 | preview limit clamping (`limit` > 50) → 422; first-N rows + `truncated` flag | router |

Browser-level cases (real upload click-through, `/runs/<run_id>` landing, error banner rendering) are verified in QA integration (`scripts/verify_app_e2e.py`, shell-owned browser), not in this offline suite; the exact request shapes above pin them.

---

## 9. S3 findings (documentation only — P6 is not rebuilt)

- **Where P6 lives today:** `scripts/object_orders_feed.py` + `scripts/orders_loader.py` (loader-side, `boto3` optional and *never* imported by the backend), versioned object identity (`obj_<sha>` batch ids over `(feed_id, bucket, key, version_id)`), version-pinned GET with byte caps, staging into `external_order_batches` Postgres bytes, and bridge/approval endpoints `POST/GET /api/external/orders/{feed_id}/{batch_id}/*` with separate loader/approver credentials (`auth.require_loader`/`require_approver`).
- **Why it is not exposed through the UI with minimal changes:** every `/api/external/*` route is scoped to machine roles (`loader`/`approver`), there is **no list endpoint** for staged batches (only per-`(feed_id, batch_id)` status), and the S3 path requires **explicit object version IDs** — a user picking an S3 object in a browser would need bucket/key/version discovery plus new list/search endpoints with `require_browser` scoping. That is a real auth-surface change to files this agent may not touch (`backend/routers/external.py`, `backend/auth.py`).
- **Recommendation:** keep S3 ingestion server-side (the existing Airflow/loader flow already stages immutable, digest-verified bytes in Postgres). The pragmatic future extension is reusing *this* ingestion view to show `external_order_batches` status (staged → released → published) read-only, not re-implementing S3 in the browser. Optional, not part of this prototype.

---

## 10. Risks / notes for reconciliation

- `API_CONTRACT.md` §3.5 draft says `source_id = "upload:<dataset_id>"`; this implementation uses the orchestrator-pinned **`"upload_<dataset_id>"`** (hyphenless prefix, uuid remainder). Pick one string at reconciliation; the store uses a text column so either parses.
- `GET /api/app/datasets` (list) is an addition to §3.4 — needed by the "select an existing registered dataset" UX; conflict-free (different method on the same prefix).
- Uploaded *contracts* are a contracts-agent concern; this page reads `GET /api/config/contracts` and needs no new cross-route.
- Row-count semantics everywhere = data rows excluding the header (consistent with `external_order_batches.row_count`).
