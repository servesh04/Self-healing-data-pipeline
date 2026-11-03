# Self-Healing Data Pipeline: Comprehensive Knowledge Transfer (KT) & Architecture Reference

This document serves as the master technical reference and Knowledge Transfer (KT) manual for the **Self-Healing Data Pipeline** system. It details the internal mechanics, design constraints, concurrency models, database schemas, and state transitions across the platform.

---

## 1. System Mission & Core Paradigm

Traditional data ingestion pipelines operate on a binary outcome: **Pass** or **Fail**. When an upstream provider changes a column name, introduces unexpected format alterations, or reformats dates, the pipeline fails, human engineers are alerted, and downstream ETL halts.

**Self-Healing Data Pipeline** converts ingestion into an **adaptive state machine**:
1. When validation passes, data is committed normally.
2. When validation fails, the pipeline diagnoses the drift, assigns domain-specific LLM specialists, proposes bounded declarative transformations, applies the patch in-memory, and reruns validation.
3. If high-confidence ($\ge 0.80$), it auto-heals and commits the new mapping version.
4. If ambiguous ($< 0.80$), it durably pauses execution in Postgres for a human reviewer.
5. If unfixable or invalid, it safely escalates without corrupting destination storage.

---

## 2. Technology Stack & Component Map

| Layer | Technology | Primary Role |
| :--- | :--- | :--- |
| **Orchestration** | LangGraph / LangChain | Stateful, cyclic agent execution with native interrupt/resume primitives. |
| **Backend API** | FastAPI / Python 3.12+ | High-performance async REST API with Pydantic validation. |
| **Inference** | Groq (`llama-3.3-70b-versatile`) | Fast, low-latency LLM inference with strict JSON schema enforcement. |
| **Database** | Neon Serverless Postgres | Relational data, mapping ledger, LangGraph durable checkpoint storage. |
| **Database Driver**| `psycopg 3` (Async / Connection Pool) | Native async PostgreSQL driver with advisory locking and JSONB support. |
| **Data Processing**| Pandas | Vectorized validation assertions and declarative patch transformations. |
| **Frontend** | React 19 + Vite + Tailwind CSS | Responsive SPA dashboard with graph visualization and live polling. |
| **State Management**| Zustand | Reactive global stores (`useRunStore`, `useAuthStore`). |
| **Cloud Hosting** | Render (Backend) + Vercel (Frontend) | Fully serverless/cloud-managed production deployment. |

---

## 3. The LangGraph State Machine Architecture

The core pipeline execution is modelled as a stateful, cyclic directed graph in `backend/graph/build.py`.

### 3.1 The Shared Graph State (`HealState`)

The graph passes a single typed dictionary across all nodes defined in `backend/graph/state.py`:

```python
class HealState(TypedDict):
    run_id: str                      # Unique thread ID for LangGraph checkpointer
    source_path: str                 # Path to raw file or snapshot
    pipeline_env: str                # Isolation namespace ('default', 'dashboard-demo')
    contract_name: str               # Target contract ('default', 'ecommerce_v1')
    contract_version: str            # Sha256 pin of contract snapshot
    assertions_passed: bool          # Did the current data pass contract assertions?
    drift_items: list[dict]          # Granular assertion failure findings
    source_profile: dict             # Source column types, null counts, sample values
    drift_class: str | None          # 'rename', 'type', 'nullability', 'value', 'date_parse', 'unknown'
    candidates: list[str]            # Specialist candidates identified by diagnose
    proposed_patch: dict | None      # Validated MappingPatch structure
    patch_rationale: str | None      # LLM reasoning for the patch
    confidence: float | None         # Confidence score (0.00 to 1.00)
    heal_attempts: int               # Number of healing cycles attempted (capped at 2)
    history: list[dict]              # Attempt history (confidence, specialist, patch diff)
    awaiting_approval: bool          # Is execution paused waiting for human decision?
    human_decision: str | None       # 'approve' | 'reject'
    human_note: str | None           # Human reviewer justification
    final_message: str | None        # Summary status reason
    status: str                      # 'running', 'clean', 'healed', 'escalated', 'awaiting_approval'
```

---

### 3.2 Graph Topology & Node Breakdown

```
[run_pipeline] ──(fail)──> [profile_source] ──> [diagnose]
      │                                             │
   (pass)                                      (candidates)
      │                                             ▼
[commit_report]                          [Specialists / Multi]
                                                    │
                                                    ▼
                                             [propose_patch]
                                                    │
                                          (confidence check)
                                         ├── >= 0.80 ──> [apply_patch]
                                         │                    │
                                         └── < 0.80           ▼
                                              │        [rerun_validate]
                                              ▼               ├── (pass) ──> [commit_report]
                                      [human_approval]        └── (fail) ──> [diff_contract] (loop)
                                         ├── approve ─> [apply_patch]
                                         └── reject ──> [escalate]
```

1. **`run_pipeline`**: Executes initial assertions (column presence, data types, minimum row count, domain rules) against the raw dataset. If all pass $\rightarrow$ jumps straight to `commit_report` (`clean`).
2. **`profile_source`**: If assertions fail, extracts column summaries, non-null counts, detected dtypes, and representative value samples (sanitized of PII).
3. **`diagnose`**: First LLM call. Compares the source profile with the expected contract. Classifies failure into one or more categories: `rename`, `type`, `nullability`, `value`, `date_parse`, or `unknown`.
4. **Specialist Nodes (`spec_*`)**:
   * **`spec_rename`**: Matches missing contract columns to unexpected source columns.
   * **`spec_type`**: Resolves format mismatches using registered casting functions.
   * **`spec_nullability`**: Fills illegal nulls according to registered null policies (`fill_zero`, `fill_unknown`).
   * **`spec_value`**: Handles range/categorical bounds.
   * **`spec_date_parse`**: Identifies datetime formats and constructs parsing rules.
   * **`multi_specialist`**: Orchestrates consensus if multiple drift classes are diagnosed concurrently.
5. **`propose_patch`**: Assembles the specialist outputs into a strictly typed `MappingPatch` schema.
6. **`human_approval`**: Calls LangGraph's `interrupt()`. Freezes execution and yields control back to the caller while checkpointing full thread state to Postgres.
7. **`apply_patch`**: Transforms the pandas DataFrame using the proposed declarative patch and increments `heal_attempts`.
8. **`rerun_validate`**: Re-evaluates contract assertions against the patched DataFrame.
9. **`commit_report`**: Final success node. Commits the newly established schema version to the persistent mapping ledger.
10. **`escalate`**: Terminal failure node. Sets `status = 'escalated'` when drift is unfixable, human rejects the patch, or max healing attempts are reached.

---

### 3.3 The 5 Conditional Edge Routers

Located in `backend/graph/routing.py`:

* **`route_after_run`**:
  * `assertions_passed == True` $\rightarrow$ `"commit_report"`
  * `assertions_passed == False` $\rightarrow$ `"profile_source"`
* **`route_after_diagnose`**:
  * `drift_class == "unknown"` or no candidates $\rightarrow$ `"escalate"`
  * Multiple candidates $\rightarrow$ `"multi_specialist"`
  * Single candidate $\rightarrow$ `"spec_<drift_class>"`
* **`route_after_confidence`**:
  * Proposal error or empty patch $\rightarrow$ `"escalate"`
  * `confidence >= 0.80` $\rightarrow$ `"apply_patch"`
  * `confidence < 0.80` $\rightarrow$ `"human_approval"`
* **`route_after_human_approval`**:
  * `human_decision == "approve"` $\rightarrow$ `"apply_patch"`
  * `human_decision == "reject"` $\rightarrow$ `"escalate"`
* **`route_after_rerun`**:
  * `assertions_passed == True` $\rightarrow$ `"commit_report"`
  * `heal_attempts >= 2` $\rightarrow$ `"escalate"` (prevents infinite loops)
  * `assertions_passed == False` $\rightarrow$ `"diff_contract"` (repeats healing cycle)

---

## 4. Guardrails & Safety Architecture

A major danger of LLM-powered pipelines is hallucinated code or corrupted destination tables. The pipeline enforces 4 strict layers of safety:

```
[LLM Specialist Output]
       │
       ▼ (Guardrail 1: Pydantic extra='forbid' validation)
[Declarative MappingPatch]
       │
       ▼ (Guardrail 2: Fixed Function Registry Check)
[Transformation Applied in Memory]
       │
       ▼ (Guardrail 3: Strict Contract Re-Validation)
[Deterministic Re-Run]
       │
       ▼ (Guardrail 4: Postgres Advisory Lock & CAS Transaction)
[Postgres Mapping Ledger Commit]
```

1. **Declarative Config, Never Code:**
   * LLMs are **never** permitted to generate Python code or SQL strings. They output structured JSON declaring mappings.
2. **Strict Pydantic Registry Enforcement:**
   * In `backend/pipeline/mapping.py`, operations are validated against static registries (`CAST_REGISTRY`, `NULL_POLICY_REGISTRY`). If an LLM hallucinates an unknown operation (e.g. `strip_currency`), Pydantic immediately rejects the candidate.
3. **No Direct Production Mutation:**
   * The patch transforms an in-memory DataFrame snapshot. Destination tables are never updated until `rerun_validate` proves 100% of contract assertions pass.
4. **Finite Healing Loop (`MAX_HEAL_ATTEMPTS = 2`):**
   * The pipeline is hard-capped at two healing cycles. If a second patch fails validation, it immediately halts and triggers an automated CAS rollback.

---

## 5. Database Schema & Concurrency Model

All persistence is backed by Neon Postgres with zero in-memory state leakage across processes.

### 5.1 Key Database Tables

| Table | Purpose |
| :--- | :--- |
| `app_contracts` | Dynamic registry of uploaded contracts (`name`, `version`, `spec_hash`). |
| `app_contract_versions` | Immutable version history of every contract update. |
| `app_datasets` | Metadata for uploaded CSVs with SHA-256 digest and reuse deduplication. |
| `source_snapshots` | Bytea raw payload storage ensuring historical runs can be reproduced. |
| `run_records` | Queryable index of all pipeline executions, scopes, and durations. |
| `mapping_state` | Append-only ledger of schema patches with parent version IDs and rollback tracking. |
| `mapping_current` | Single-row-per-scope pointer to the active `current_version_id`. |
| `approval_decisions` | Durable audit log of human approval/rejection decisions with reviewer notes. |
| `checkpoints` & `checkpoint_blobs` | Native LangGraph checkpointer tables storing serialized thread snapshots. |

---

### 5.2 Concurrency Control: Advisory Locking & CAS (Compare-And-Swap)

To prevent race conditions when two concurrent runs attempt to update the live mapping simultaneously, `backend/services/store.py` implements **Compare-And-Swap (CAS)** with PostgreSQL advisory locks:

```sql
-- 1. Acquire transaction-level advisory lock hashed to the mapping scope
SELECT pg_advisory_xact_lock(hashtext('mapping:dashboard-demo:ecommerce_v1'));

-- 2. Lock current version row and verify expected_id hasn't changed
SELECT current_version_id FROM mapping_current 
WHERE pipeline_env = %s AND contract_name = %s FOR UPDATE;

-- If current_version_id != expected_id:
-- RAISE ValueError("mapping changed since this run read it") -> Abort commit!

-- 3. If verified, append new mapping version
INSERT INTO mapping_state (mapping, updated_by, pipeline_env, contract_name, run_id, parent_id, event_type)
VALUES (%s, %s, %s, %s, %s, %s, 'patch') RETURNING id;

-- 4. Advance the current version pointer
UPDATE mapping_current SET current_version_id = %s WHERE pipeline_env = %s AND contract_name = %s;
```

If an applied patch causes an unexpected failure in downstream processing, `rollback_mapping_cas` rolls back the pointer to `parent_id` and records a `rollback` event in `mapping_state`.

---

## 6. Durable Human-in-the-Loop (HITL) Mechanics

### **How LangGraph `interrupt()` Works**
When `route_after_confidence` sees `confidence < 0.80`:
1. It enters node `human_approval`.
2. `interrupt({"proposed_patch": ..., "confidence": ...})` raises an internal LangGraph GraphInterrupt exception.
3. LangGraph intercepts this, saves the entire execution frame and variables into `checkpoints` in Postgres, and returns control to the FastAPI router.
4. The API marks the run record status as `awaiting_approval`.

### **How Resumption Works**
When the operator clicks **Approve** or **Reject** in the UI:
1. Frontend fires `POST /api/runs/{run_id}/approve` (or `/reject`) with `{ "note": "..." }`.
2. Backend validates bearer credentials and checks lease locks.
3. Backend calls `graph_app.aresume({"configurable": {"thread_id": run_id}}, Command(resume={"decision": "approve", "note": note}))`.
4. LangGraph reloads the frozen state from Neon Postgres, injects the decision into the `human_approval` node return, and continues execution seamlessly to `route_after_human_approval`.

---

## 7. Knowledge Transfer FAQ: Clearing Common Doubts

### **Q1: Why did my run escalate directly without showing Approve/Reject buttons?**
> **Answer:** Approval buttons only render when the pipeline has entered the **`awaiting_approval`** state. A run only enters `awaiting_approval` if a patch was **successfully proposed** and has a confidence score **$< 0.80$**.  
> If an LLM specialist cannot propose any valid patch (for example, if the drift requires an operation not present in `CAST_REGISTRY`, like stripping dollar signs), `proposed_patch` is `None`. When no patch exists, there is nothing for a human to approve, so the pipeline escalates directly to failure.

### **Q2: Can the LLM execute arbitrary code or corrupt our production database?**
> **Answer:** **No, impossible by design.** The LLM has zero execution privileges. It outputs declarative JSON data matching a Pydantic model (`MappingPatch`). The actual transformation is executed by hardcoded Python functions in `CAST_REGISTRY` and `NULL_POLICY_REGISTRY`. Furthermore, mutations happen in memory on a pandas DataFrame, and are only committed to Postgres if contract assertions pass 100%.

### **Q3: What happens if the server crashes or restarts while a run is waiting for approval?**
> **Answer:** **Zero state is lost.** State is not stored in server memory; it is serialized via LangGraph's checkpointer into Neon Postgres (`checkpoints` and `checkpoint_blobs` tables). When the server comes back up, the user can click Approve hours or days later; the backend rehydrates the execution thread from the database and resumes exactly where it halted.

### **Q4: How does the pipeline prevent multiple runs from overwriting each other's patches?**
> **Answer:** Through **PostgreSQL Advisory Locks and CAS (Compare-And-Swap)**. When a run attempts to commit a patch, it acquires an advisory transaction lock (`pg_advisory_xact_lock`) on the contract scope and verifies that the `current_version_id` still matches the version it read when it started. If another run committed a patch in the meantime, the version check fails, the transaction aborts, and the run triggers a re-read or reconciliation.

### **Q5: Why did we need `asyncio.WindowsSelectorEventLoopPolicy` on Windows local dev?**
> **Answer:** On Windows, Python 3.8+ defaults to `ProactorEventLoop`, which uses I/O Completion Ports (IOCP). However, `psycopg 3`'s async connection pool requires socket selector support (`SelectorEventLoop`). Running bare `uvicorn` on Windows forces the Proactor loop, causing database queries to hang indefinitely. `backend/run_local.py` explicitly installs the `SelectSelector` event loop. In production Linux containers, this is irrelevant because Linux uses `epoll`.

### **Q6: Why are environment variables split between Render and Vercel?**
> **Answer:** The frontend is a static React Single Page Application (SPA) built with Vite and hosted on **Vercel**. Vite inlines all `VITE_*` variables directly into the compiled JavaScript bundle at build time. Therefore, `VITE_API_BASE_URL` and `VITE_SHARED_TOKEN` must be present in Vercel's build environment. The backend runs as a FastAPI Python process on **Render**, requiring runtime variables like `DATABASE_URL`, `SHARED_TOKEN`, `GROQ_API_KEY`, and `CORS_ORIGINS`.

---

## 8. Summary Architecture Cheat-Sheet

```
Contract Drift Occurs
        │
        ├── Diagnosed Drift?
        │     ├── Known Drift -> Specialist Proposes Bounded JSON Patch
        │     └── Unknown / Out of Bounds -> Escalate to Operator
        │
        ├── Proposal Confidence?
        │     ├── >= 0.80 -> Apply Automatically in Memory
        │     └── < 0.80  -> Halt at interrupt() -> Persist to Neon Postgres -> Wait for Human Approval
        │
        ├── Re-Validation?
        │     ├── Passed -> CAS Commit Version to Mapping Ledger -> Success
        │     └── Failed (< 2 attempts) -> Loop back to Diagnose
        │     └── Failed (>= 2 attempts) -> Auto-Rollback to Parent Version -> Escalate
```
