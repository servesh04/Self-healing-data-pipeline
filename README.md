# Self-Healing Data Pipeline

> Existing tools — Great Expectations, Monte Carlo, dbt tests — *detect* data drift and alert.
> A human still writes the patch. This agent closes the remaining loop: it diagnoses the drift,
> proposes a remediation patch, and escalates to a human when confidence is low rather than
> silently mutating production logic. We measure how many injected faults it heals unaided,
> how many it correctly escalates, and how many it misses.

A LangGraph agent that repairs a data pipeline when upstream data drifts. See
[ARCHITECTURE.md](ARCHITECTURE.md) for the full design — it is the source of truth for this build.

---

## Build status

| Phase | Scope | Status |
|---|---|---|
| **0** | Deployment skeleton — public frontend to public backend, bearer auth, Neon | **done — verified live at the deployed URLs** |
| **1** | Pipeline + contract + fault datasets | **done** |
| **2** | Graph skeleton with stub nodes | **done** |
| 3 | Real nodes (LLM) | not started |
| 4 | Interrupt + Postgres checkpointer | not started |
| 5 | Dashboard | not started |
| 6 | Eval, docs, freeze | not started |

Phase 0 contains **no pipeline code and no LangGraph usage** by design. `langgraph` is installed
and its version recorded, but nothing imports it yet. Phase 1 adds the deterministic pipeline,
contract, and fault datasets — still zero LLM calls and zero LangGraph.

---

## Phase 1 — pipeline, contract, fault datasets

`backend/pipeline/` implements `load -> apply_mapping -> transform -> validate` exactly as
specified. Pandera (`pandera.pandas`) is the oracle; `validate()` never raises — it converts
`SchemaErrors` into a structured, parseable `FailureCase` list (column, check, failure count,
example values).

The mapping — the only thing the healing agent will ever be allowed to write — is a
`MappingPatch` Pydantic model (`backend/pipeline/mapping.py`) with `extra="forbid"` and
registry-checked op names, so a patch naming an unknown cast or an out-of-section key fails
validation rather than being silently accepted. It is persisted in Postgres
(`backend/services/store.py`, table `mapping_state`) as an **append-only** log — each save is a
new row, so the previous mapping is never lost, which is also what
"`apply_patch` backs up the current mapping" (ARCHITECTURE.md) means in practice. This was live
data on the deployed backend from the moment Phase 1 landed, not something added later.

**Verify it yourself:**

```bash
cd backend
../.venv/Scripts/python.exe ../scripts/gen_fault_datasets.py   # (re)generates data/sources/*.csv
../.venv/Scripts/python.exe ../scripts/check_pipeline.py       # runs the Phase 1 DoD checks
```

`check_pipeline.py` talks to the real `DATABASE_URL` from `.env` — the same Postgres the
deployed backend uses — so it is exercising actual persistence, not a mock. It prints, for every
fault dataset, the expected vs. actual pass/fail and the full structured failure list; then it
manually patches `day2_renamed`'s rename fault, saves it to Postgres, reloads it, and confirms
the pipeline now passes — proving the mapping is a real healing target, not just a schema.

| Dataset | Injected fault | Result |
|---|---|---|
| `day1_clean` | none | passes |
| `day2_renamed` | `customer_id` → `cust_id` | fails: `column_in_schema` / `column_in_dataframe`; passes after `renames: {cust_id: customer_id}` |
| `day3_type_drift` | `revenue` as `"1,240.00"` strings | fails: `dtype('float64')` |
| `day4_combo` | rename + type drift together | fails both signatures at once; passes with both patches applied together |
| `day5_unfixable` | `region` abbreviated to `N/S/E/W` + new `currency` column | fails: `column_in_schema` (currency) + `isin(...)` (region) — confirmed **not** healable by any single mapping op, by design |

---

## Phase 2 — graph skeleton with stub nodes

`backend/graph/` wires the full topology from ARCHITECTURE.md's "Graph Topology" section — 13
nodes, 4 named routers (`route_after_run`, `route_after_diagnose`, `route_after_confidence`,
`route_after_rerun`) plus one necessary fifth conditional edge (`route_after_human_approval`,
dispatching the human's own decision rather than pipeline/agent state — not one of the rubric's
named four, but the topology diagram's approve/reject fork requires a callable regardless).
Every node body is a hardcoded dict, keyed by a scenario string smuggled through `source_path`
(`stub://heals-in-3`, `stub://never-heals`, ...) — see
`backend/graph/nodes/_phase2_stub_scenarios.py`, deliberately marked as scaffolding that Phase 3
replaces. **Zero LLM calls, zero prompts.**

Two things are real already, not stubbed, because they're graph-topology guardrails rather than
pipeline logic: `apply_patch` increments `heal_attempts` from actual prior state (never a
router), and `human_approval` calls the real `interrupt()` — compiled against an in-memory
checkpointer for now; Phase 4 swaps in the Postgres one without touching this code.

**Verify it yourself:**

```bash
cd backend
../.venv/Scripts/python.exe ../scripts/graph_smoke_test.py
```

Runs five scenarios, printing the live node sequence for each, then reports router/branch
coverage and the Definition of Done explicitly:

| Scenario | Proves |
|---|---|
| `clean` | `route_after_run`'s pass branch — no healing at all |
| `heals-in-3` | the heal cycle itself: fails attempts 1–2, passes on 3, cycling `spec_rename → spec_type → spec_nullability` across the three specialist branches, `history` accumulating 3 distinct entries (not overwritten) |
| `never-heals` | `MAX_HEAL_ATTEMPTS` cap → `escalate` |
| `unknown-drift` | `route_after_diagnose`'s direct-to-`escalate` branch |
| `low-confidence-approve` / `-reject` | `interrupt()` actually halts (`next == ("human_approval",)`, payload inspectable), `Command(resume=...)` actually resumes at `human_approval` — not from `START` — and both the approve and reject forks route correctly |

All 11 (router, branch) pairs report `[OK]`; all 6 Definition of Done checks report `[PASS]`.

---

## Recorded versions

Verified against the installed packages, not against documentation.

| Package | Version |
|---|---|
| **langgraph** | **1.2.11** |
| langgraph-checkpoint | 4.2.0 |
| langgraph-checkpoint-postgres | 3.1.2 |
| langchain-core | 1.6.0 |
| fastapi | 0.141.1 |
| uvicorn | 0.52.4 |
| pydantic | 2.13.4 |
| psycopg | 3.3.4 |
| pandas | 3.0.5 |
| pandera | 0.32.1 |
| Python | 3.14 (local 3.14.3 / container 3.14.7) |
| Postgres (Neon) | 18.6 |

### LangGraph API verification

ARCHITECTURE.md warns that its import paths may be stale. They were checked against 1.2.11 and
**all still resolve**: `langgraph.graph.{StateGraph,START,END}`,
`langgraph.types.{interrupt,Command}`, `langgraph.checkpoint.postgres.aio.AsyncPostgresSaver`.

Behaviour was verified by running a real graph, not just by importing names:

- `interrupt(value)` halts; `Command(resume=...)` resumes **at the interrupted node**, not at START.
- `Annotated[list, operator.add]` appends across resumes (`[{attempt: 1}, {attempt: 2}]`).
- A resumed node **re-executes from the top of its function body**, so `human_approval` must have
  no side effects before its `interrupt()` call.

Two findings that change how the code must be written:

1. **`AsyncPostgresSaver.from_conn_string()` is an async context manager** and closes its
   connection on block exit. A checkpointer opened that way cannot resume a graph across HTTP
   requests. The pool is therefore owned by the FastAPI lifespan ([backend/db.py](backend/db.py))
   and the checkpointer will be built around it in Phase 4.
2. **uvicorn hard-codes `ProactorEventLoop` on Windows** via a loop *factory*, which ignores
   `asyncio.set_event_loop_policy()`. psycopg's async mode refuses that loop, so a bare
   `uvicorn main:app` on Windows starts fine and then every DB call times out with no clear cause.
   Local Windows dev must use [backend/run_local.py](backend/run_local.py). The Linux container
   uses uvloop and is unaffected.

### Deviation from ARCHITECTURE.md

The document's `contract.yaml` declares `region: { dtype: object }`. On pandas 3, a string column
infers as `str`, not `object`, so that contract would fail on `day1_clean.csv` — the control case —
and spuriously trigger the heal loop. The contract will use `str`. Keeping pandas 3 also keeps the
local and container runtimes identical, which matters more on a deploy-first schedule than matching
the document verbatim. Also note `pandera.pandas` is the current import namespace.

---

## Local development

Requires Python 3.14, Node 24, and a Neon connection string.

```bash
# secrets
cp .env.example .env          # then fill in DATABASE_URL and SHARED_TOKEN
                              # token: python -c "import secrets;print(secrets.token_hex(24))"

# backend
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
cd backend && ../.venv/Scripts/python.exe run_local.py 8123
```

Use `run_local.py`, not `uvicorn`, on Windows — see the event-loop note above.

```bash
# frontend
cd frontend
cp .env.example .env.local    # set VITE_API_BASE_URL + VITE_SHARED_TOKEN
npm install
npm run dev                   # http://localhost:5173
```

The frontend renders a Phase 0 status page that runs all four Definition-of-Done checks in the
browser, including a deliberately unauthenticated request that must be rejected with 401.

### Running the container locally

Build from the repository root, not from `backend/`:

```bash
docker build -f backend/Dockerfile -t shp-backend:phase0 .
docker run --rm -p 8199:10000 -e PORT=10000 \
  -e DATABASE_URL="..." -e SHARED_TOKEN="..." \
  -e CORS_ORIGINS="http://localhost:5173" -e APP_ENV=container \
  shp-backend:phase0
```

---

## API (Phase 0)

| Route | Auth | Purpose |
|---|---|---|
| `GET /health` | none | Liveness. Deliberately does **not** touch the DB, so a suspended Neon compute cannot make the deploy look unhealthy. |
| `GET /` | none | Service banner. |
| `GET /api/ping` | Bearer | Authenticated round-trip. Writes a row to `deploy_probe` and reports the Postgres version plus recorded package versions. |

All `/api/*` routes require `Authorization: Bearer <SHARED_TOKEN>`; anything else returns 401.

---

## Deployment

Backend on Render (Docker), frontend on Vercel, Postgres on Neon.

**Render** — either apply [render.yaml](render.yaml) as a blueprint, or create a Docker web
service manually with:

- Dockerfile path `./backend/Dockerfile`
- **Docker build context `.` (the repository root, which is Render's default) —
  not `./backend`.** The Dockerfile's `COPY` paths are repo-relative, because from
  Phase 1 the backend also needs `config/` and `data/`, which live outside `backend/`.
  Pointing the context at `backend/` fails the build with `"/requirements.txt": not found`
  and an exit code of 1.
- Health check path `/health`
- Env vars: `DATABASE_URL`, `SHARED_TOKEN`, `CORS_ORIGINS`, `APP_ENV=render`.
  A missing required variable stops the service with an explicit
  `CONFIGURATION ERROR` block naming it, rather than a bare pydantic traceback.

**Vercel** — import the repo and set **Root Directory to `frontend`** (the repo root is not the
frontend). Framework preset Vite. Env vars `VITE_API_BASE_URL` (the Render URL, no trailing slash)
and `VITE_SHARED_TOKEN`.

Then add the real Vercel origin to `CORS_ORIGINS` on Render and redeploy.

### Operational caveats

- **Render free tier sleeps** after ~15 min and takes ~50s to wake. Warm it before a live demo.
- **Neon free tier suspends** its compute when idle; the first connection can exceed 30s. The pool
  is opened with `wait=False` so the app boots immediately rather than blocking on a cold compute.
- **Render's filesystem is ephemeral.** The mapping config lives in Postgres from Phase 1 onward;
  `config/mapping.yaml` is only a seed.

---

## Known limitations

- `VITE_SHARED_TOKEN` is inlined into the client bundle, so the shared token is public to anyone
  who loads the page. This is inherent to the single-shared-token design in ARCHITECTURE.md
  (no user accounts, no JWT) and is acceptable only for a demo deployment.
- Further limitations are recorded per phase as the build proceeds.
