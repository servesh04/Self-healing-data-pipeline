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
| **3** | Real nodes (LLM) | **done — tuned and verified against a real model, all 6 fault datasets** |
| **4** | Interrupt + Postgres checkpointer | **done — verified across a real process kill and restart, both approve and reject** |
| **5** | Dashboard | **done — hand-rolled SVG GraphCanvas, verified against 5 real run states** |
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
| `day6_ambiguous_rename` | `customer_id` → `acct_ref` (added in Phase 3 — see below) | fails: `column_in_schema` / `column_in_dataframe`, same shape as day2; passes after `renames: {acct_ref: customer_id}` — the fault is identical in kind to day2, only the new column's name is deliberately non-obvious |

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

(This phase's `scripts/graph_smoke_test.py` and `backend/graph/nodes/_phase2_stub_scenarios.py`
were removed once Phase 3 replaced the nodes they depended on — the `stub://` scenario strings
those real nodes no longer understand — leaving them in place would have silently produced
misleading output rather than a real regression check. The Phase 2 node sequence and coverage
table above are preserved from the verified run, not reproducible by re-running the repo as it
stands now.)

---

## Phase 3 — real nodes

Split into a deterministic half and an LLM half. Both are done and verified against a real
model — a `GROQ_API_KEY` arrived partway through this phase; everything below the deterministic
section was tuned and confirmed against `openai/gpt-oss-120b` on Groq, not mocked.

### Deterministic half — done, verified

`profile_source` and `diff_contract` are real; `run_pipeline`, `rerun_validate`, and
`apply_patch` now call the actual Phase 1 pipeline and persist to Postgres instead of returning
hardcoded dicts.

The one design decision worth explaining: `diff_contract` sits on the heal-cycle's loop-back edge
(`route_after_rerun` loops to it, not back to `profile_source`), so its output must genuinely
change between iterations — otherwise diagnose sees the same "everything is still wrong" picture
on attempt 2 as attempt 1, which is exactly how an agent ends up proposing overlapping or
redundant patches instead of converging. Verified against real `day4_combo` data before writing
the fix: with an empty mapping, Pandera reports 4 failure signals; after a rename-only patch, only
2 remain — a strict subset. So `diff_contract` derives `drift_items` from
`assertion_failure_details` (the structured form behind `assertion_failures`, refreshed by
whichever of `run_pipeline`/`rerun_validate` most recently ran), not from a one-time raw-file
snapshot.

Proven end-to-end with a rule-based stand-in for diagnose (correct-by-construction classification,
not the real LLM — this isolates "does my new code work" from "is the prompt any good"), using an
isolated `pipeline_env` throughout:

| Dataset | Result |
|---|---|
| `day1_clean` | `clean`, 0 attempts |
| `day2_renamed` | `healed`, exactly 1 attempt |
| `day3_type_drift` | `healed`, exactly 1 attempt |
| `day4_combo` | `healed`, **exactly 2 attempts** — matches the Phase 3 DoD's "not eventually passes" |

Also confirmed the real graph — the actual `build_graph()`, mixing sync (`profile_source`,
`diff_contract`) and async (everything touching Postgres or Groq) node functions in one
`StateGraph` for the first time — compiles and runs correctly with no LLM key configured at all:
`day1_clean` passes straight through with no LLM call needed, and `day2_renamed` (which does need
one) has `diagnose` fail cleanly on the missing key and the run escalate rather than crash —
`nodes must never raise`, holding even under a real, not simulated, failure.

### LLM half — done, tuned and verified against a real model

`backend/services/llm.py` (Groq, `openai/gpt-oss-120b`, JSON mode, retry with backoff on
`RateLimitError`), `backend/prompts/prompts.py`, and the real `diagnose` / `spec_rename` /
`spec_type` / `spec_nullability` / `propose_patch` nodes. Every LLM node validates its parsed
JSON against a Pydantic model in `backend/graph/schemas.py` before returning — required
regardless of JSON mode, which guarantees syntax, not schema.

**Specialist scope leakage** — each specialist's raw output is validated against its *own*
narrowly-scoped model (`RenameSpecialistOutput`, `TypeSpecialistOutput`,
`NullabilitySpecialistOutput`), each `extra="forbid"` and declaring only its one mapping section.
This is deliberately not just a `MappingPatch` check: `MappingPatch` declares all four sections as
optional, so a rename specialist that also emits a stray `casts` key would pass general validation
silently and just look like a small, unrelated, harmless patch fragment — exactly the failure mode
that's expensive to debug later. `propose_patch` gets the same protection in the other direction:
its own model validates only `{"rationale": ...}`, so if it tries to invent its own patch content
instead of describing the specialist's, that's also caught immediately. Confirmed with the LLM
call mocked before a real key existed (10 cases: valid responses, a missing field, an out-of-range
confidence, a leaked `casts` key from the rename specialist, an unknown cast op name, a leaked
`patch` key from `propose_patch`) — every case caught and handled without raising.

**A day5/day6 split, not one dataset.** The original single ambiguous-case dataset conflated two
different questions diagnose has to answer separately: *is there a viable class of fix at all*,
and *how much do I trust my specific guess*. `day5_unfixable` (two unrelated, non-mechanical
drifts) answers the first one "no" — `drift_class="unknown"`, which routes straight to
`escalate` with no patch proposed at all. `day6_ambiguous_rename` (a real rename, but to a
non-obvious column name) answers the first one "yes, rename" and the second one "not confidently"
— it routes through a specialist to `human_approval` with a real patch to review. Conflating them
into one dataset either auto-applies a wrong guess or escalates a case a human could have quickly
approved; see `prompts.py`'s `DIAGNOSE_SYSTEM` and ARCHITECTURE.md's Fault Datasets section for
the full reasoning (found empirically, mid-build, and corrected in both places).

**A genuine model quirk, found and fixed.** `openai/gpt-oss-120b` on Groq reliably (not
occasionally) wraps its JSON response as a list of 1-2 near-duplicate candidate objects instead
of one top-level object — confirmed not a leaked reasoning trace (`reasoning_format="hidden"`
made no difference) and not a temperature artifact (temperature 0 made it *worse*, 5/8 vs 7/8
correct — this model's serving isn't fully deterministic even at temperature 0). An explicit
"respond with exactly one JSON object, never a list" instruction in every system prompt fixed it
outright; `graph/schemas.py:normalize_llm_json` remains as a defensive backstop (diagnose breaks
ties on the lowest confidence among candidates — consistent with "prefer low confidence when
uncertain" — other nodes take the first valid one, having no confidence field to compare).

**Calibration, verified against the real model, 25/25 across a repeatability sweep** (5 runs
each for day2/3/5/6, 10 for day4 — day4 needed a worked example added to the prompt after an
initial 7/8, since abstract rules alone under-generalized to "two drift items present" reading
as automatically ambiguous):

| Dataset | drift_class | confidence | Result |
|---|---|---|---|
| `day2_renamed` | rename | 0.92-0.96 | auto-heals, 1 attempt |
| `day3_type_drift` | type | 0.90-0.92 | auto-heals, 1 attempt |
| `day4_combo` | rename, then type | 0.90-0.93 each | auto-heals, **exactly 2 attempts** |
| `day5_unfixable` | unknown | 0.2 | escalates directly, no patch proposed |
| `day6_ambiguous_rename` | rename | 0.3 | halts at `human_approval` with a real, **correct** patch (`{renames: {acct_ref: customer_id}}`); approving it heals in 1 attempt; rejecting it escalates with `heal_attempts` still 0 |

All five confirmed via the real, complete `build_graph()` — no rule-based stand-in, no mocked
LLM call — including `day6`'s full interrupt → real patch review → `Command(resume="approve")`
→ heal round trip, and its reject counterpart.

---

## Phase 4 — interrupt + Postgres checkpointer

`backend/services/checkpointer.py` wraps `AsyncPostgresSaver` around the same lifespan-owned
pool `db.py` already manages — confirmed against `langgraph-checkpoint-postgres` 3.1.2 before
writing it: the constructor accepts either a bare connection or an `AsyncConnectionPool`, and
every internal cursor opens with its own explicit `row_factory=dict_row` regardless of the
pool's default, so there's no conflict with `db.py`'s pool-wide setting. `main.py`'s lifespan
calls `setup_checkpointer()` (idempotent — a version-tracked migrations table, safe on every
boot) and compiles the graph once per process onto `app.state.graph_app`.

`backend/routers/runs.py` adds the four endpoints this phase needs to prove over real HTTP:
`POST /api/runs` (trigger), `GET /api/runs/{id}` (poll — synthesizes `api_status:
"awaiting_approval"` from the checkpointer's own pending-interrupt state, since `human_approval`
can't have written that into `HealState` itself: the node never returns while parked inside
`interrupt()`), `POST /api/runs/{id}/approve`, `POST /api/runs/{id}/reject`. Upload and
run-listing are Phase 5 dashboard concerns, not needed to prove this phase's mechanism.

**The test that matters: a real process kill, not a reload.** Triggered `day6_ambiguous_rename`
over HTTP, polled until `awaiting_approval`, confirmed the exact state (`next: ["human_approval"]`,
the real proposed patch, `heal_attempts: 0`) — then hard-killed the backend process outright
(`Stop-Process -Force`, port confirmed released) and, before starting anything new, queried
Postgres directly and independently of the application: **8 checkpoint rows existed for that
`thread_id`, written by a process that no longer existed.** Started a completely fresh process
(new PID, new `AsyncPostgresSaver` instance, zero in-memory knowledge of the run) and confirmed
`GET /api/runs/{id}` returned the identical state from Postgres alone. Then approved.

The rigorous proof isn't "it healed" — a broken checkpointer that silently restarted from
`START` could plausibly reach the same end state by re-running everything and getting lucky
with the same diagnosis. The proof is grepping **the fresh process's own log, from its very
first line**, and finding no `run_pipeline`/`diagnose` entries for that run at all:

```
run <id>: node 'human_approval' fired
run <id>: node 'apply_patch' fired
run <id>: node 'rerun_validate' fired
run <id>: node 'commit_report' fired
```

Repeated identically for reject, on a second run and a third fresh process (different resume
value, different downstream route — approve resuming correctly doesn't imply reject does; they
share nothing except the interrupt itself). The fresh process's log:

```
run <id>: node 'human_approval' fired
run <id>: node 'escalate' fired
```

`heal_attempts` stayed `0` and `applied_patch` stayed `null` — confirming `apply_patch` never
fired on the reject path, exactly the guardrail it must never violate. Both runs' checkpoints
were also confirmed present in Postgres by direct query, independent of the API layer, both
before and after their respective process kills.

All of this ran under an isolated `PIPELINE_ENV=phase4-test` (reset between the two test runs,
since the first run's approved patch would otherwise have made the second run's data pass
cleanly with no drift to heal) — the live `default` mapping was confirmed untouched throughout.

---

## Phase 5 — dashboard

Two backend additions, both needed before the frontend could show anything real: a lightweight
run registry (`run_records` — the checkpointer has no listing API, so `GET /api/runs` needs
*something* to enumerate before it can ask the checkpointer for each one's live status), and a
`run_id` column on `mapping_state` so `GET /api/runs/{id}` can return a per-run patch audit trail
instead of the whole namespace's history with every run mixed together — the addition suggested
for this phase, and cheap precisely because `mapping_state` was already append-only from Phase 1.

**GraphCanvas is the priority panel**, per explicit project direction: the `↺ Heal attempt N`
badge is the single most important pixel in the project, prioritized over every other panel.
Hand-rolled SVG (`components/GraphCanvas/topology.js` + `GraphCanvas.jsx`) — no React Flow, same
reasoning as everywhere else in this build: the topology is fixed and known, a layout library is
a dependency and a bundle-size cost for zero marks. Node "visited" status is inferred from the
polled `HealState` snapshot (missing_column/unexpected_column pairs, `history` entries, terminal
status) rather than tracked live — REST polling has no per-node event stream (`next` only tells
you the interrupt's pause point precisely; ARCHITECTURE.md already notes SSE as a known
limitation), so the canvas is honest about that rather than faking finer-grained animation.

**A real bug found via testing, not inspection**: the first `deriveVisited` pass only looked at
the *current* `drift_class`/`confidence` fields, which reflect the most recent `diagnose` call —
for `day4_combo`, a second diagnose call failing after the first one's rename fix succeeded
erased the rename attempt from the graph's visited-node coloring entirely, even though it had
genuinely happened. Fixed by also walking `history`, whose entries never get overwritten. Caught
by screenshotting a real escalated run and asking why half the nodes were idle-colored when the
`history` array clearly showed a completed attempt — not by re-reading the component code.

**Verified against 5 real run states**, captured via Playwright driving actual Chrome against the
Vite dev server and the local backend (screenshots, not just component code review):
escalated-via-cap (`day4_combo`, rate-limited mid-cycle — see the note below), healed-via-auto-apply,
healed-via-human-approval, escalated-via-rejection, and clean (no drift). Each state's GraphCanvas
coloring, AssertionBars, SchemaDiff, PatchDiff, and (where applicable) MappingAudit and
AttemptTimeline were checked against the actual JSON the backend returned, not assumed from the
component code. Found and fixed a second real bug this way: `PatchDiff` showed the *current*
`confidence`/`drift_class` fields next to an *already-applied* patch — for the same reason as the
GraphCanvas bug, a later diagnose call's confidence had overwritten the value that actually
justified the currently-displayed patch, making a correctly-applied high-confidence fix look like
a 0.00-confidence guess. Fixed by sourcing both fields from the `history` entry that produced the
displayed patch, falling back to the live fields only when showing a not-yet-applied proposal.

**A Groq free-tier daily quota exhaustion happened live during this phase's verification** (visible
in `backend`'s own logs: rate-limited, retried with backoff, then degraded to
`unknown`/`confidence: 0.0` and escalated) — not a bug, but worth being transparent about: it's
why some of this phase's live-trigger screenshots show an escalation where a clean auto-heal was
expected. The dashboard rendered every one of those states correctly, including the guardrail
working exactly as designed under a real, not simulated, failure — `day2`/`day3`/`day4`'s
consistent auto-heal behavior was already exhaustively verified against a fresh quota in Phase 3
(25/25 across a repeatability sweep) and is not being re-litigated here.

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

## API

| Route | Auth | Purpose |
|---|---|---|
| `GET /health` | none | Liveness. Deliberately does **not** touch the DB, so a suspended Neon compute cannot make the deploy look unhealthy. |
| `GET /` | none | Service banner. |
| `GET /api/ping` | Bearer | Authenticated round-trip. Writes a row to `deploy_probe` and reports the Postgres version plus recorded package versions. (Phase 0) |
| `POST /api/runs` | Bearer | `{source_path}` → `{run_id}`. Runs the graph in a background task; poll rather than WebSocket. (Phase 4) |
| `GET /api/runs/{id}` | Bearer | Full `HealState`, plus `api_status`/`awaiting_approval` synthesized from the checkpointer's own pause state. (Phase 4) |
| `POST /api/runs/{id}/approve` | Bearer | `{note?}` → resumes from `human_approval`, not `START`. (Phase 4) |
| `POST /api/runs/{id}/reject` | Bearer | `{note?}` → resumes and routes to `escalate`; `apply_patch` never fires. (Phase 4) |

All `/api/*` routes require `Authorization: Bearer <SHARED_TOKEN>`; anything else returns 401.
Upload and run-listing (`POST /api/sources/upload`, `GET /api/runs`) are Phase 5 dashboard
concerns and not yet built.

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
- `GET /api/runs` enriches every listed run with a live checkpointer read (`aget_state`) on every
  call — fine at demo scale (a handful of runs), but each read goes through the checkpointer's own
  serializing lock plus a Neon round-trip, so list latency grows linearly with run count. Not
  optimized (e.g. caching terminal-status runs) since the demo never approaches a scale where it
  matters.
- GraphCanvas's node coloring is inferred from the polled snapshot (which nodes were visited,
  ever), not a live per-node execution stream — REST polling has no such stream to show. This
  matches ARCHITECTURE.md's own "poll rather than WebSocket... note SSE as a known limitation".
- Further limitations are recorded per phase as the build proceeds.
