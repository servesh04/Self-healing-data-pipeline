# Self-Healing Data Pipeline — Architecture

> **For Claude Code:** This document is the single source of truth for this build.
> Follow phases in order. Each phase is time-boxed and independently demoable.
>
> **HARD CONSTRAINTS**
> - **3 days, firm.** Scope discipline beats feature count.
> - **Must be deployed** at a public URL a grader can visit.
> - If behind schedule, consult the Cut List (Appendix D). Do not improvise cuts.
>
> **The graded artifact is the healing graph** — its nodes, conditional routing, cycles,
> and human-in-the-loop interrupt. Pipeline infrastructure is deliberately minimal.

---

## Project Overview

A data pipeline that repairs itself when upstream data drifts.

A pipeline runs: load source → apply mapping → transform → validate against a contract.
On Monday it works. On Tuesday the upstream team renames `customer_id` to `cust_id`, or
`revenue` starts arriving as `"1,240.00"` strings. The pipeline breaks — or worse, silently
produces wrong numbers.

Today a human gets paged, reads the traceback, diffs the schema, patches the transform config,
and reruns. This agent does that: detects the failure, diffs actual schema against the declared
contract, classifies the drift, routes to a specialist, proposes a **config patch**, and either
applies it (high confidence) or **pauses and asks a human** (low confidence). Then it reruns
and verifies the data quality assertions actually pass.

### Thesis (README, first paragraph)

> Existing tools — Great Expectations, Monte Carlo, dbt tests — *detect* data drift and alert.
> A human still writes the patch. This agent closes the remaining loop: it diagnoses the drift,
> proposes a remediation patch, and escalates to a human when confidence is low rather than
> silently mutating production logic. We measure how many injected faults it heals unaided,
> how many it correctly escalates, and how many it misses.

### Why this domain

The repair cycle is **structurally necessary**: you cannot know whether a patch worked without
rerunning the pipeline and re-checking assertions. And the human gate is not decorative —
nobody wants an unattended agent silently rewriting production data logic. Both the cycle and
the interrupt exist because the problem demands them, not because a rubric rewards them.

---

## Rubric Mapping

| Expectation | Where it appears |
|---|---|
| Cycles (not a DAG) | `rerun_validate → diff_contract → specialist → propose_patch → rerun_validate` |
| Conditional edges | 4 routers: post-run, post-diagnose (drift class), post-confidence, post-rerun |
| Multiple specialised agents | 3 drift specialists + diagnostician + patch proposer + reporter |
| Human-in-the-loop | `interrupt` on low-confidence patches — halts, persists, resumes across HTTP requests |
| Checkpointing / persistence | Postgres checkpointer. Resume-after-approval **requires** it |
| Live state | Dashboard polls run state; graph nodes animate as they execute |
| Guardrails | Bounded heal attempts, confidence gate, escalation path |
| Measurable outcome | Eval: healed / correctly-escalated / missed across 6 injected faults |

> **Anti-pattern warning:** a fan-out/fan-in pipeline (N agents, one pass, concatenated
> results) scores poorly — it is reproducible with `asyncio.gather`. Every cycle and branch
> below exists for a functional reason. **Do not flatten the graph for simplicity.**

---

## Tech Stack

| Layer | Technology | Note |
|---|---|---|
| Agent framework | **LangGraph** | The graded core |
| LLM | gpt-oss-120b via Groq | Free tier, fast — the heal loop is iterative |
| Backend | FastAPI (Python 3.11+) | |
| Data quality | **Pandera** | The oracle. Declarative, deterministic, parseable errors |
| Data | pandas | |
| DB + checkpointer | **Neon Postgres** (free tier) | SQLite will NOT survive deployment |
| Frontend | React + Vite + TailwindCSS | |
| Frontend state | Zustand | |
| Backend hosting | Render (Docker web service) | |
| Frontend hosting | Vercel | |
| Auth | Single shared token in an env var | Not JWT. Not user accounts |

### Why Pandera, not Great Expectations
GE needs a context/config setup step that will cost an hour, and you need none of its extra
surface. Pandera schemas are ~15 lines and raise `SchemaError` with structured, parseable
failure cases — which matters because `diagnose` reads those errors directly.

### Why SQLite is excluded
Render's free tier has an **ephemeral filesystem** — wiped on every restart and redeploy. A
SQLite checkpointer would lose all run state and break resume-after-approval, the most
impressive feature in the project. Use Neon from the start; do not build on SQLite and migrate.

### Deliberately excluded (say so in the README)
**Airflow, Dagster, Prefect, Celery, cron, Prometheus, Grafana, Kubernetes.** Orchestration
infrastructure earns zero marks on a LangGraph rubric and will consume more than a day. The
pipeline runner here is ~120 lines of plain Python. Scheduling is orthogonal to the healing
logic — state that explicitly rather than leaving it as an apparent gap.

Pipeline runs are **trigger-based** (upload a source file, or click "Run"), not scheduled.

### LangGraph version warning
LangGraph's API has shifted across releases — checkpointer packages, `interrupt` semantics,
and `Command` routing especially. **Do not trust the import paths in this document.** After
installing, run `pip show langgraph`, check that version's docs, and prefer its actual API
over this spec. Record the version in the README.

---

## Repository Structure

```
self-healing-pipeline/
├── backend/
│   ├── main.py                      # FastAPI entry
│   ├── config.py                    # pydantic-settings
│   ├── db.py                        # Postgres engine + session
│   ├── Dockerfile
│   ├── requirements.txt
│   │
│   ├── pipeline/                    # ── deliberately dumb, ~120 lines ──
│   │   ├── runner.py                # load → apply_mapping → transform → validate
│   │   ├── contract.py              # Pandera schema built from contract.yaml
│   │   └── steps.py                 # the transform steps
│   │
│   ├── graph/                       # ── THE GRADED CORE ──
│   │   ├── state.py                 # HealState TypedDict
│   │   ├── build.py                 # StateGraph wiring
│   │   ├── routing.py               # 4 conditional edge functions
│   │   └── nodes/
│   │       ├── run_pipeline.py      # deterministic
│   │       ├── profile_source.py    # deterministic
│   │       ├── diff_contract.py     # deterministic — the drift report
│   │       ├── diagnose.py          # LLM — classifies drift, sets confidence
│   │       ├── spec_rename.py       # LLM specialist
│   │       ├── spec_type.py         # LLM specialist
│   │       ├── spec_nullability.py  # LLM specialist
│   │       ├── propose_patch.py     # LLM — emits YAML patch
│   │       ├── human_approval.py    # interrupt()
│   │       ├── apply_patch.py       # deterministic
│   │       ├── rerun_validate.py    # deterministic — THE ORACLE
│   │       ├── escalate.py          # terminal: needs a human
│   │       └── commit_report.py     # terminal: success
│   │
│   ├── prompts/prompts.py           # all prompts, module constants
│   ├── services/
│   │   ├── llm.py                   # Groq wrapper + retry
│   │   └── store.py                 # run records + mapping state in Postgres
│   └── routers/
│       ├── runs.py                  # trigger, poll, approve/reject
│       └── sources.py               # upload a source drop
│
├── config/
│   ├── contract.yaml                # declared expectations (the baseline)
│   └── mapping.yaml                 # ← WHAT THE AGENT PATCHES (seed only)
│
├── data/sources/                    # injected-fault datasets
│   ├── day1_clean.csv
│   ├── day2_renamed.csv
│   ├── day3_type_drift.csv
│   ├── day4_combo.csv
│   ├── day5_unfixable.csv
│   └── day6_ambiguous_rename.csv
│
├── eval/
│   ├── run_eval.py
│   └── results/
│
├── frontend/
│   └── src/
│       ├── App.jsx
│       ├── api/client.js
│       ├── store/useRunStore.js
│       ├── components/
│       │   ├── RunList/
│       │   ├── GraphCanvas/         # live node states — demo centerpiece
│       │   ├── AssertionBars/       # Pandera pass/fail
│       │   ├── SchemaDiff/          # expected vs actual, colour-coded
│       │   ├── PatchDiff/           # proposed YAML change
│       │   └── ApprovalPanel/       # Approve / Reject — the HITL moment
│       └── pages/RunDetail.jsx
│
├── docs/graph.png
└── README.md
```

---

## The Pipeline (keep it dumb)

`config/contract.yaml` — the declared truth:

```yaml
columns:
  order_id:     { dtype: int64,   nullable: false, unique: true }
  customer_id:  { dtype: int64,   nullable: false }
  region:       { dtype: object,  nullable: false, allowed: [North, South, East, West] }
  order_date:   { dtype: datetime64[ns], nullable: false }
  revenue:      { dtype: float64, nullable: false, min: 0 }
row_count_min: 20
```

`config/mapping.yaml` — **the only thing the agent modifies:**

```yaml
renames: {}          # e.g. { cust_id: customer_id }
casts: {}            # e.g. { revenue: strip_commas_to_float }
null_policy: {}      # e.g. { region: fill_unknown }
drops: []
```

`backend/pipeline/runner.py` — four steps, plain functions:

```
load(csv) → apply_mapping(df, mapping) → transform(df) → validate(df, contract)
```

`validate` uses Pandera and returns a **structured result**: `passed: bool`, plus a list of
failure cases (column, check, failure_count, example values). **Never raise out of the
runner** — return the failure structure. The graph routes on it.

**Do not add complexity here.** No DAG engine, no retries, no scheduling. This exists only to
break in interesting ways.

---

## Fault Datasets — author these by hand

Write them yourself so you know exactly what fails and how. Do not hunt for broken data in the
wild; that makes the demo a gamble. 40–60 rows each.

Six datasets, not five — `day6_ambiguous_rename` was added after building Phase 3 and finding
that this section's original claim for `day5` ("Ambiguous → low confidence → human interrupt")
contradicted `route_after_diagnose`'s own routing table (Routing Functions, below), which sends
an `"unknown"` classification straight to `escalate`, bypassing `human_approval` entirely. That
routing is correct and unchanged; `day5`'s row here was wrong, not the code. `human_approval`
needed a fault where diagnose is confident about *which class* of fix applies but not confident
in the *specific* patch it would propose — day5's fault (two unrelated, non-mechanical problems)
isn't that; day6's is. See `diagnose`'s node spec below for how the prompt treats "is there a
class" and "how confident am I" as two separate questions rather than one conflated judgement.

| File | Injected fault | Expected behaviour |
|---|---|---|
| `day1_clean.csv` | none — control | Passes first run. No healing triggered |
| `day2_renamed.csv` | `customer_id` → `cust_id` | Rename specialist, high confidence, **auto-heals** |
| `day3_type_drift.csv` | `revenue` as `"1,240.00"` strings | Type specialist, high confidence, auto-heals |
| `day4_combo.csv` | rename **and** type drift together | Two heal iterations — **proves the cycle** |
| `day5_unfixable.csv` | `region` values become `["N","S","E","W"]` **and** a new `currency` column appears | Two unrelated, non-mechanical drifts → `"unknown"`, low confidence → **routes straight to `escalate`, no patch proposed** |
| `day6_ambiguous_rename.csv` | `customer_id` → `acct_ref` (same shape as day2, but the new name is not an obvious abbreviation of the old one) | Rename-shaped drift, so the *class* is confident — but the *specific* mapping isn't obvious enough to trust unattended → rename, low confidence → **human interrupt**, with a real patch to review |

`day4` and `day6` are the demo. `day4` proves the loop iterates; `day6` proves the agent knows
when to ask rather than guess, and that its guess is worth reviewing once it does. `day5` proves
the agent recognizes a genuinely unfixable case and says so directly, without pretending a patch
review would have helped. **Build these three first if time compresses.**

---

## Graph Topology

```
                                START
                                  │
                                  ▼
                          ┌───────────────┐
                          │  run_pipeline │ ◀── deterministic
                          └───────┬───────┘
                                  │
                   ┌────── route_after_run ──────┐
              assertions pass                 failed
                   │                             │
                   ▼                             ▼
            ┌─────────────┐            ┌─────────────────┐
            │commit_report│            │ profile_source  │
            └──────┬──────┘            └────────┬────────┘
                   ▼                             ▼
                  END                   ┌─────────────────┐
                                        │  diff_contract  │ ◀── deterministic drift report
                                        └────────┬────────┘
                                                 ▼
                                        ┌─────────────────┐
                                   ┌───▶│    diagnose     │ (LLM: class + confidence)
                                   │    └────────┬────────┘
                                   │             │
                                   │   ┌── route_after_diagnose ──┐
                                   │  rename    type   nullab.  unknown
                                   │    │        │        │        │
                                   │    ▼        ▼        ▼        ▼
                                   │  spec_    spec_    spec_   escalate
                                   │  rename   type     null       │
                                   │    │        │        │        ▼
                                   │    └────────┼────────┘       END
                                   │             ▼
                                   │     ┌───────────────┐
                                   │     │ propose_patch │
                                   │     └───────┬───────┘
                                   │             │
                                   │    ┌ route_after_confidence ┐
                                   │   high                    low
                                   │    │                        ▼
                                   │    │              ┌───────────────────┐
                                   │    │              │  human_approval   │ ◀── interrupt()
                                   │    │              └─────────┬─────────┘
                                   │    │                approve │ reject
                                   │    │                        │  └──▶ escalate ─▶ END
                                   │    ▼                        ▼
                                   │   ┌────────────────────────────┐
                                   │   │        apply_patch         │ (writes mapping)
                                   │   └─────────────┬──────────────┘
                                   │                 ▼
                                   │       ┌──────────────────┐
                                   │       │  rerun_validate  │ ◀── THE ORACLE
                                   │       └────────┬─────────┘
                                   │                │
                                   │    ┌─ route_after_rerun ─┐
                                   │   pass                 fail
                                   │    │                     │
                                   │    ▼          heal_attempts < 3 ?
                                   │ commit_report      │         │
                                   │    │              yes        no
                                   │    ▼               │         ▼
                                   │   END              │      escalate
                                   └────────────────────┘         │
                                        ↑ HEAL CYCLE              ▼
                                                                 END
```

### The cycle, stated plainly for your report

`rerun_validate → diff_contract → diagnose → specialist → propose_patch → apply_patch →
rerun_validate`

**Terminating conditions:** assertions pass, OR `heal_attempts >= 3`, OR a human rejects.
**Oracle:** Pandera assertions — deterministic, not an LLM judging an LLM.

This is why `day4_combo.csv` matters: it heals the rename on iteration 1, reruns, still fails
on the type drift, loops back, heals that, reruns, passes. Two full cycles, visible in the UI.

---

## State Schema

`backend/graph/state.py`:

```python
from typing import TypedDict, Literal, Optional
from typing_extensions import Annotated
import operator


class DriftItem(TypedDict):
    kind: Literal["missing_column", "unexpected_column", "dtype_mismatch",
                  "null_violation", "value_violation"]
    column: str
    expected: str
    actual: str
    example_values: list[str]


class HealAttempt(TypedDict):
    attempt_no: int
    drift_summary: str
    specialist: str
    patch: dict                       # the YAML fragment proposed
    confidence: float
    approved_by: Optional[str]        # "auto" | "human" | None
    result: Literal["passed", "failed", "rejected"]
    assertion_failures: list[str]


class HealState(TypedDict):
    # ── Inputs ──
    run_id: str
    source_path: str

    # ── Pipeline result ──
    assertions_passed: bool
    assertion_failures: list[str]
    rows_in: int
    rows_out: int

    # ── Profile + diff (deterministic) ──
    actual_schema: dict[str, str]
    expected_schema: dict[str, str]
    drift_items: list[DriftItem]

    # ── Diagnosis (LLM) ──
    drift_class: Optional[Literal["rename", "type", "nullability", "unknown"]]
    diagnosis: Optional[str]
    confidence: Optional[float]       # 0.0 – 1.0

    # ── Patch ──
    proposed_patch: Optional[dict]
    patch_rationale: Optional[str]
    applied_patch: Optional[dict]

    # ── Human gate ──
    awaiting_approval: bool
    human_decision: Optional[Literal["approve", "reject"]]
    human_note: Optional[str]

    # ── Guardrail counter — REQUIRED ──
    heal_attempts: int

    # ── Terminal ──
    status: Literal["running", "healed", "escalated", "clean", "awaiting_approval"]
    final_message: Optional[str]

    # ── Audit trail — append-only, drives the UI ──
    history: Annotated[list[HealAttempt], operator.add]
```

**Critical:** `history` must use `Annotated[..., operator.add]` so LangGraph appends rather
than overwrites. Every other field overwrites by default. Getting this wrong destroys the
iteration trail that makes `day4_combo` demoable.

### Constants

```python
MAX_HEAL_ATTEMPTS = 3
CONFIDENCE_THRESHOLD = 0.75      # below this → human_approval
RECURSION_LIMIT = 30             # LangGraph backstop, set on invoke
```

---

## Node Specifications

### `run_pipeline` — deterministic
Executes the pipeline with the current mapping. Populates `assertions_passed`,
`assertion_failures`, row counts. **Never raises** — catches everything and records it.

### `profile_source` — deterministic
Reads the source CSV; records actual column names, inferred dtypes, null counts, and 3 sample
values per column. No LLM — giving the model real facts prevents hallucinated column names.

### `diff_contract` — deterministic
Compares `actual_schema` against `contract.yaml` and emits a structured `drift_items` list.
**This is a diff, not a judgement.** Doing it deterministically means the LLM classifies facts
rather than inventing them.

### `diagnose` — LLM (JSON mode)
**In:** `drift_items`, `assertion_failures`, sample values
**Out:** `drift_class`, `diagnosis` (1–2 sentences), `confidence` (0.0–1.0)

`drift_class` and `confidence` are two SEPARATE questions, not one conflated judgement — found
by actually running this against `day6_ambiguous_rename`. Conflating them either auto-applies a
wrong guess or escalates a case a human could have quickly approved:

- **Question 1 — is there a viable class of fix at all**, based on the *shape* of the drift,
  regardless of how sure the model is about the specifics: one missing column + one unexpected
  column with matching dtype → `rename`-shaped, no matter what the new column is actually
  called. A dtype mismatch recoverable by an obvious mechanical transform (`"1,240.00"` → float)
  → `type`-shaped. Anything that doesn't fit one of the three shapes — an unpaired unexpected
  column, a semantic change in allowed values, more than one independent problem where none
  individually fits a shape — → `unknown`. `unknown` requires that *no* drift item matches a
  shape; it is not triggered merely by having more than one drift item (`day4_combo` has two
  drift items and is still confidently `rename` on the first pass).
- **Question 2 — given the class, how much to trust the specific guess.** High confidence means
  the specific mapping is obvious (`"cust_id"` for `"customer_id"` — a human glancing at both
  names would agree immediately). Low confidence means the class is right but the guess deserves
  a second look (`"acct_ref"` for `"customer_id"` — plausible, not obvious). **Explicitly
  instruct: when the specific guess is uncertain, output low confidence rather than withholding
  the guess** — a human reviews it either way; a wrongly auto-applied patch is far worse than an
  unnecessary human review, but an empty proposal gives that human nothing to review. Put that
  sentence in the prompt.

`unknown` always routes straight to `escalate` (see `route_after_diagnose`) — there is no
specialist for it, so there is nothing to propose and nothing for a human to approve or reject.
Low-confidence `rename`/`type`/`nullability` is what reaches `human_approval`, with a real patch
attached.

### `spec_rename` / `spec_type` / `spec_nullability` — LLM specialists
Each receives only the drift items for its class and proposes a **narrow** fix. They differ by
more than tone: each has a different output schema constrained to its own section of the
mapping (`renames`, `casts`, `null_policy`). A specialist must not emit keys outside its
section — validate this.

### `propose_patch` — LLM (JSON mode)
Merges specialist output into a valid mapping fragment plus a one-sentence rationale.
**Emits config only — never Python.** Validate against a Pydantic model before it goes
anywhere near persistence.

### `human_approval` — `interrupt()`
Halts the graph. State persists to the Postgres checkpointer. See the HITL section — this is
the hardest and most valuable part of the build.

### `apply_patch` — deterministic
Backs up the current mapping, merges the patch, persists it. Records `applied_patch`.
Increments `heal_attempts` **here, inside the node** — never in a routing function.

### `rerun_validate` — deterministic, THE ORACLE
Reruns the pipeline with the patched mapping. Pandera passes or fails. Appends a `HealAttempt`
to `history`. This is ground truth; nothing here is an LLM opinion.

### `escalate` / `commit_report` — terminal
`escalate` writes a human-readable summary: what drifted, what was tried, why it stopped.
**Never fabricate success.** `commit_report` records the healed run and the final mapping diff.

---

## Routing Functions

`backend/graph/routing.py` — an evaluator will read this file first. Keep it pure and obvious.

```python
def route_after_run(state) -> str:
    return "commit_report" if state["assertions_passed"] else "profile_source"


def route_after_diagnose(state) -> str:
    if state["drift_class"] in ("rename", "type", "nullability"):
        return f"spec_{state['drift_class']}"
    return "escalate"


def route_after_confidence(state) -> str:
    if state["confidence"] >= CONFIDENCE_THRESHOLD:
        return "apply_patch"
    return "human_approval"


def route_after_rerun(state) -> str:
    if state["assertions_passed"]:
        return "commit_report"
    if state["heal_attempts"] >= MAX_HEAL_ATTEMPTS:
        return "escalate"
    return "diff_contract"           # ← THE HEAL CYCLE
```

Routing functions **read** state and return a string. They never mutate it.

---

## Human-in-the-Loop Over HTTP

The hardest piece and the most impressive. Locally, `interrupt` pauses and you type. Deployed,
it must survive the user going to lunch.

**Flow:**
1. Graph reaches `human_approval`, calls `interrupt()` with the proposed patch
2. LangGraph persists full state to the Postgres checkpointer under `thread_id = run_id`
3. The background task returns; `GET /api/runs/{id}` reports `status: "awaiting_approval"`
   plus the patch and rationale
4. Dashboard renders `ApprovalPanel` with the YAML diff
5. User clicks Approve → `POST /api/runs/{id}/approve`
6. Backend resumes the graph from the checkpoint with the human's decision
7. Execution continues **from `human_approval`, not from START**

**Verify explicitly:** trigger `day6_ambiguous_rename` (not `day5` — see the Fault Datasets
section; `day5` escalates directly and never reaches `human_approval`), wait for the interrupt,
**restart the backend process**, then approve. It must resume correctly. If it restarts from the
beginning, the checkpointer is misconfigured. Test this before building any UI.

`thread_id` must equal `run_id` and be stable across requests. Getting this wrong is the single
most likely point of failure in the project.

---

## API

```
POST /api/sources/upload      multipart → { source_path, columns[], row_count }
POST /api/runs                { source_path } → { run_id }
GET  /api/runs                → recent runs
GET  /api/runs/{id}           → full HealState (dashboard polls every 1s)
POST /api/runs/{id}/approve   { note? } → resumes graph
POST /api/runs/{id}/reject    { note? } → resumes graph → escalate
GET  /api/config/mapping      → current mapping (show it changing live)
```

Auth: `Authorization: Bearer <SHARED_TOKEN>` on all `/api/*`. One env var. Nothing more.

Run the graph in a FastAPI `BackgroundTask`. Poll rather than WebSocket — simpler, adequate,
one less thing to break on stage. Note SSE as a known limitation.

---

## Dashboard

Three screens. `GraphCanvas` is the demo centerpiece.

**Run list** — source file, status badge (clean / healed / escalated / awaiting approval),
heal attempt count, timestamp.

**Run detail** — four stacked panels:

1. **GraphCanvas** — the topology as fixed-position SVG nodes with live status colouring
   (idle / running / done / skipped). When the heal cycle fires, show an explicit badge:
   **`↺ Heal attempt 2`**. Make cycles impossible to miss.
   *Hand-roll the SVG — the topology is fixed and known. Do not add React Flow; it is a
   dependency and a time sink for zero extra marks.*

2. **AssertionBars** — each Pandera check as a pass/fail bar with failure counts. Watching red
   bars turn green after a heal is the most satisfying moment in the demo.

3. **SchemaDiff** — expected vs actual columns, colour-coded: missing (red), unexpected
   (amber), type mismatch (orange), matching (green).

4. **PatchDiff + ApprovalPanel** — the proposed mapping change as a `+`/`-` diff, with the
   agent's rationale and confidence score. Approve / Reject buttons when awaiting.

Below: **attempt timeline** — per iteration, the drift found, specialist used, patch applied,
result.

Dark theme, monospace for diffs. Skip responsive polish.

---

## Deployment

**Deploy on Day 1 morning, before writing real code.** Non-negotiable, and the single decision
that makes 3 days survivable. Deployment failures are cheap to debug when the app is a
hello-world and brutal when it is finished.

- **Neon** — create the Postgres project, copy the connection string
- **Render** — Docker web service from `backend/Dockerfile`, env vars in the dashboard
- **Vercel** — Vite preset, `VITE_API_BASE_URL` pointing at Render

```env
DATABASE_URL=postgresql://...        # Neon, include ?sslmode=require
GROQ_API_KEY=gsk_...
SHARED_TOKEN=...                     # openssl rand -hex 24
CORS_ORIGINS=http://localhost:5173,https://<your-app>.vercel.app
```

**Cold start caveat:** Render's free tier sleeps after ~15 minutes and takes ~50 seconds to
wake. For a live demo, ping the backend a minute beforehand, or budget $7 for one month of
Render Starter. Do not discover this on stage.

**Filesystem caveat:** Render's disk is ephemeral. Mapping edits written to disk will not
survive a restart. **Store the current mapping in Postgres** — `config/mapping.yaml` is only
the initial seed. Easy to get wrong, and it will look like the agent "forgetting" its patches.

---

## Eval

`eval/run_eval.py` runs all 6 fault datasets, twice:
- **Baseline:** healing disabled — pipeline runs once, reports pass/fail
- **Full graph:** healing enabled, auto-approving low-confidence patches for the automated run
  (note this deviation in the README — the interrupt is verified manually). This only affects
  `day6`: `day5` never reaches `human_approval` at all (see Fault Datasets), so there is no
  patch for the automated run to auto-approve there.

Output into `docs/eval_summary.md`:

| Dataset | Fault | Baseline | Agent | Heal attempts | Outcome |
|---|---|---|---|---|---|
| day1_clean | none | pass | pass | 0 | clean |
| day2_renamed | rename | **fail** | pass | 1 | auto-healed |
| day3_type_drift | type | **fail** | pass | 1 | auto-healed |
| day4_combo | rename + type | **fail** | pass | 2 | auto-healed |
| day5_unfixable | two unrelated drifts | **fail** | **fail** | 0 | **correctly escalated, no patch proposed** |
| day6_ambiguous_rename | ambiguous rename | **fail** | pass | 1 | low-confidence, auto-approved for eval, healed |

Headline: *"4 of 6 injected faults resolved fully unattended; a 5th was correctly identified,
proposed a real patch, and healed once approved; the 6th was correctly escalated with no patch
proposed at all, rather than mis-patched. The baseline pipeline failed on all 5 drift cases."*

**The escalation is a success, not a failure.** Frame it that way — an agent that knows its
limits is the point.

Add `time.sleep(4)` between eval cases; the Groq free tier will throttle an unpaced run.

---

---

# Phase 0 — Deploy a Skeleton (Day 1, 08:00–11:00) ⭐

**Goal:** a public URL returning `{"ok": true}`. No real code.

1. Repo, venv, `requirements.txt`, minimal `Dockerfile`
2. FastAPI with `GET /health` and one authed `GET /api/ping`
3. Neon project created; `DATABASE_URL` working from the deployed backend
4. Vite + Tailwind app that calls `/api/ping` and renders the response
5. Push both. Confirm the Vercel URL talks to the Render URL. Fix CORS **now**.

**Definition of Done**
- [ ] Public Vercel URL renders data fetched from the public Render URL
- [ ] Bearer token auth rejects an unauthenticated request
- [ ] Backend connects to Neon and can write to a table
- [ ] `pip show langgraph` version recorded in the README

> **Do not proceed until this is green.** Everything after is an increment on a working deploy.

---

# Phase 1 — Pipeline + Contract + Faults (Day 1, 11:00–19:00)

**Goal:** a pipeline that breaks in known ways. **No LLM, no LangGraph yet.**

1. `contract.yaml` and the Pandera schema builder
2. Mapping storage in Postgres + `runner.py` (load → apply_mapping → transform → validate)
3. Structured validation result — never raise out of the runner
4. **Author all 6 fault datasets.** Do this while fresh, not at 2am
5. Manually verify: `day1` passes; `day2`–`day5` each fail with the *expected* assertion error

**Definition of Done**
- [ ] `day1_clean` passes end to end
- [ ] Each fault dataset fails with a distinguishable, parseable Pandera error
- [ ] Manually patching the mapping makes `day2` pass — proves the healing target works

---

# Phase 2 — Graph Skeleton with Stubs (Day 2, 08:00–11:00) ⭐

**The most important phase. Zero LLM calls.**

Every node returns a hardcoded dict. Stub `diagnose` to cycle through drift classes, and
`rerun_validate` to fail on the first two attempts then pass — so the heal cycle demonstrably
fires. Stub one path with `confidence=0.3` to reach `human_approval`.

**Definition of Done**
- [ ] `build_graph()` compiles
- [ ] All 4 routers exercised; every specialist branch reachable
- [ ] **Heal cycle observably fires** in the printed node sequence
- [ ] `MAX_HEAL_ATTEMPTS` cap terminates into `escalate`
- [ ] `history` appends across attempts (verifies the `operator.add` annotation)

> If everything else collapses, **this phase alone is a submittable LangGraph project.**

---

# Phase 3 — Real Nodes (Day 2, 11:00–16:00)

1. `profile_source` and `diff_contract` — deterministic, real
2. `services/llm.py` — Groq, `asyncio.to_thread`, JSON mode, `RateLimitError` retry
3. Real prompts: `diagnose`, three specialists, `propose_patch`
4. Pydantic validation on every LLM output before use
5. `apply_patch` + `rerun_validate` against the real pipeline

**Definition of Done**
- [ ] `day2_renamed` auto-heals in 1 attempt
- [ ] `day3_type_drift` auto-heals in 1 attempt
- [ ] `day4_combo` heals in exactly 2 attempts — **the cycle proven on real data**
- [ ] `day5_unfixable` produces low confidence (class `unknown`) and routes directly to `escalate`
- [ ] `day6_ambiguous_rename` produces the right class at low confidence and routes toward
      `human_approval` with a real, correct patch proposed

---

# Phase 4 — Interrupt + Checkpointer (Day 2, 16:00–19:00)

1. Postgres checkpointer wired, `thread_id = run_id`
2. `human_approval` calls `interrupt()` — done since Phase 2; reused as-is, only the checkpointer
   backing it changes (`InMemorySaver` → `AsyncPostgresSaver`)
3. `POST /approve` and `/reject` resume the graph
4. **Kill the backend process entirely between interrupt and approval — not a reload, a real
   process restart — for both the approve and the reject path. Verify each resumes correctly.**

**Definition of Done**
- [ ] `day6_ambiguous_rename` halts with `status: "awaiting_approval"` and returns the patch
      (not `day5` — see Fault Datasets; `day5` escalates directly and never reaches this node)
- [ ] Approve resumes from `human_approval`, not from START, **after a full process kill and
      restart** — not merely proven against the in-memory checkpointer from Phase 2
- [ ] Reject routes to `escalate` with the human's note recorded, **also verified after a full
      process kill and restart** — a different resume value and a different downstream route
      than approve; one working does not imply the other does, and `apply_patch` must not fire
      on this path (`heal_attempts` stays at its pre-interrupt value)

> **Cut point:** if this is not working by 19:00 Day 2, auto-apply all patches and make the
> approval panel a read-only "here's what it did" log. Decide at 19:00 — not at 1am.

---

# Phase 5 — Dashboard (Day 3, 08:00–13:00)

1. Run list + trigger
2. **GraphCanvas** with live node states and the `↺ Heal attempt N` badge
3. AssertionBars, SchemaDiff, PatchDiff
4. ApprovalPanel wired to approve/reject
5. Attempt timeline

**Definition of Done**
- [ ] A `day4_combo` run is watchable end to end in the browser
- [ ] The heal cycle is visually obvious to someone who has never seen the project
- [ ] Approval works from the deployed frontend against the deployed backend

---

# Phase 6 — Eval, Docs, Freeze (Day 3, 13:00–19:00)

1. `run_eval.py`; generate `docs/eval_summary.md`
2. Export `docs/graph.png`
3. README: thesis, topology, eval table, **known limitations**
4. Rehearse the demo script (Appendix C) three times
5. **Feature freeze at 18:00.** No exceptions.

**Known limitations to state explicitly** — this section earns marks, it does not lose them:
- Trigger-based, not scheduled; production would use Airflow/cron
- Two drift classes auto-healed; nullability specialist is partial
- Patches config, not transform code — a deliberate safety boundary
- Polling, not streaming transport
- 6 hand-authored faults, not a standard benchmark
- Render free-tier cold starts

---

---

# Appendix A — Conventions for Claude Code

### Always
- Node functions are **pure**: `(state) -> dict`. Return only changed keys.
- Every LLM node uses JSON mode **and** validates with Pydantic before returning. JSON mode
  guarantees syntax, not schema.
- Wrap synchronous Groq calls in `asyncio.to_thread()`.
- All prompts in `backend/prompts/prompts.py` — never inline.
- Increment `heal_attempts` **inside a node**, never in a router.
- Pass `{"recursion_limit": 30}` on invoke as a backstop beyond the explicit cap.
- Persist mapping state in Postgres, never rely on the container filesystem.

### Never
- Never let a node raise. Catch, record in state, route to `escalate`.
- Never remove a cycle or collapse the specialists into one node to "simplify". They are the
  graded feature.
- Never let the agent emit or execute Python. Config patches only.
- Never auto-apply a patch below `CONFIDENCE_THRESHOLD`.
- Never add Airflow, Celery, Kubernetes, Prometheus, or Grafana.
- Never fabricate success in `escalate` — say what was tried and why it stopped.

### Node prompt template
```
You are the [NodeName] node in a self-healing data pipeline.

Your job: [one sentence]

You receive:
[fields from state]

Respond ONLY with a valid JSON object matching this schema:
[exact schema with example values]

Rules:
- When uncertain, report LOW confidence. A wrong patch is worse than an escalation.
- [rule 2]
```

---

# Appendix B — Highest-Risk Items

| Risk | Mitigation |
|---|---|
| `thread_id` unstable → resume restarts from START | Set `thread_id = run_id`; test with a process restart in Phase 4 |
| Ephemeral filesystem loses mapping edits | Store mapping in Postgres from Phase 1 |
| `history` overwrites instead of appends | `Annotated[..., operator.add]`; verify in Phase 2 |
| `diagnose` over-confident on ambiguous drift | Prompt explicitly rewards low confidence; tune against `day5` (no class) and `day6` (right class, unsure guess) — they are different failure modes and need separate prompt handling, not one |
| Render cold start kills the live demo | Warm it beforehand, or one month of Starter |
| LangGraph import paths differ from this doc | Verify against the installed version in Phase 0 |

---

# Appendix C — Demo Script (rehearse 3×)

1. Run `day1_clean` — passes, no healing. Establishes the baseline.
2. Run `day2_renamed` — `diff_contract` finds the missing column, the rename specialist
   proposes `{cust_id: customer_id}`, auto-applies, reruns, **assertion bars flip red → green**.
3. Run `day4_combo` — **the money shot.** Two heal iterations, the `↺ Heal attempt 2` badge
   fires, cycle visible in GraphCanvas.
4. Run `day6_ambiguous_rename` — agent correctly identifies a rename but is unsure of the
   specific mapping, halts with a real patch diff and rationale to review. **Restart the
   backend.** Then approve. It resumes mid-graph, not from START.
5. Run `day5_unfixable` — agent reports two unrelated, non-mechanical drifts and escalates
   *directly*, with no patch proposed — the correct-escalation moment, distinct from step 4's
   correct-review moment. There is nothing to approve here; that absence is the point.
6. Show `docs/eval_summary.md`.

Steps 3 and 4 are the project. If time runs short, cut 1 and 6 — never 3, 4, or 5.

---

# Appendix D — Cut List

Cut **in this order**. Do not improvise.

1. Frontend polish → unstyled but functional
2. Eval baseline column → report agent results only
3. `spec_nullability` → route nullability drift to `escalate`
4. Human approval → auto-apply all patches; approval panel becomes a read-only log
5. `day4_combo` multi-drift → single-drift datasets only

**Never cut, under any circumstances:**
- The heal cycle
- `MAX_HEAL_ATTEMPTS` and the confidence gate
- The fault datasets
- Deployment
- The README thesis and limitations sections

---

*End of Architecture Document*
