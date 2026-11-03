# Demo Guide

A presenter's script: what this project is, how it's built, and exactly what to click, in what
order, to show it well. ARCHITECTURE.md and README.md are the reference docs (full spec, full
verification history); this file is the condensed, stage-facing version of the same project.

---

## The 30-second pitch

A CSV pipeline runs against a data contract every day. Real-world data drifts — a column gets
renamed upstream, a number format changes, a value falls outside its allowed set. Instead of
paging a human every time, a **LangGraph agent** diagnoses the drift, proposes a config patch
(never code), auto-applies it when confident, asks a human when it isn't, and gives up honestly
when the drift genuinely can't be fixed by any patch. Every decision — the diagnosis, the patch,
the confidence, the human's call — is visible in a live dashboard, explorable with plain-English
chat, and traced end-to-end.

---

## Architecture at a glance

```
  React/Vite dashboard  ──HTTP (Bearer token)──▶  FastAPI backend  ──┬─▶ Neon Postgres
  (Vercel)                                        (Render, Docker)   │   - LangGraph checkpointer
                                                                      │   - mapping_state (config)
                                                                      │   - run_records (index)
                                                        │             │
                                                        ▼             │
                                                  LangGraph agent     │
                                                  (13-node graph)     │
                                                        │             │
                                                        ▼             ▼
                                                   Groq (LLM)    LangSmith (traces, optional)
```

- **Backend**: FastAPI + LangGraph 1.2.11, Python 3.14, deployed on Render as a Docker container.
- **Agent**: a `StateGraph` — deterministic pipeline nodes, LLM diagnosis/specialist nodes, a
  real `interrupt()` for human approval, all wired in `backend/graph/build.py`.
- **LLM**: `openai/gpt-oss-120b` via Groq's free tier, called directly (not through LangChain).
- **Database**: Neon (serverless Postgres) — the LangGraph checkpointer (run state, resumable
  across restarts), the config mapping (append-only, so every patch is its own audit row), and a
  lightweight run index.
- **Frontend**: React 19 + Vite + Tailwind 4, deployed on Vercel.
- **Auth**: one shared bearer token, checked on every `/api/*` route (`Authorization: Bearer
  <token>`). Deliberately simple — no user accounts, this is a demo project.
- **Observability**: LangSmith, opt-in via 3 environment variables, zero code changes.

---

## The agent: 13 nodes, one cycle, three guardrails

```
run_pipeline ──pass──▶ commit_report (clean, no healing needed)
      │
      └─fail──▶ profile_source ──▶ diff_contract ──▶ diagnose
                                                          │
                              ┌───────────────┬───────────┴───────────┐
                          rename            type              nullability          unknown
                              │                │                     │                 │
                        spec_rename       spec_type          spec_nullability      escalate
                              └────────────────┴─────────────────────┘
                                                │
                                          propose_patch
                                                │
                                  ≥0.75 confidence?  ──no──▶ human_approval ──reject──▶ escalate
                                        │yes                        │approve
                                        ▼                            ▼
                                   apply_patch ◀──────────────────────
                                        │
                                  rerun_validate
                                   │         │
                                pass       fail
                                  │           │
                          commit_report   attempts < 3? ──yes──▶ diff_contract (◀ THE CYCLE)
                                              │no
                                              ▼
                                          escalate
```

**Guardrails** (non-negotiable, from the original spec):
- `CONFIDENCE_THRESHOLD = 0.75` — below this, a human decides, never the agent alone.
- `MAX_HEAL_ATTEMPTS = 3` — the cycle always terminates; a real counter, incremented in exactly
  one place (`apply_patch`), never in a router.
- **The agent emits config, never code.** Every patch is a `MappingPatch` fragment (renames /
  casts / null_policy / drops), validated against a schema with `extra="forbid"` and a fixed
  registry of named operations — there is no way for the model to make the pipeline execute
  arbitrary Python.

---

## Feature tour

### The dashboard (3 pages + a run-detail view)

- **Overview** — KPI strip (total/healed/escalated/mean attempts), a confidence-vs-outcome
  scatter (calibration — is 0.75 the right line?), a drift-class bar chart, specialist
  success-rate bars, and a **global chat** panel at the bottom.
- **Runs** — every run, filterable by status/dataset, click through to any one.
- **Mapping** — the append-only patch history: every config change the agent (or a seed script)
  ever made, in order, with a toggle to hide routine seed resets.
- **Run detail** (click any run) — a hand-drawn SVG graph of the topology with the **visited path
  highlighted and a `↺ Heal attempt N` badge** (the single most important pixel in the project),
  an attempt timeline, the approval panel (only appears when a run is actually paused), assertion
  bars that flip red→green, a schema diff, a patch diff, the per-run mapping audit trail, and a
  **per-run chat** panel.

### Read-only chat (two scopes, strictly read-only)

- **Per-run** (on Run detail): "Why did this escalate?", "What did the specialist propose?".
  Sees only that run's state.
- **Global** (on Overview): "Which drift class is hardest to heal?", "Is the confidence threshold
  well calibrated?", "Summarize the last 20 runs". Sees every run's aggregates *and* a lean
  per-run list — and is specifically prompted to answer counting questions from the precomputed
  aggregates, not by recounting rows, so it can never contradict the KPI strip on the same screen.
- Neither scope can trigger, approve, or reject anything — it's one LLM call over assembled
  context, no tool-calling, no relation to the healing graph at all.

### Human-in-the-loop

A real LangGraph `interrupt()` — the run genuinely pauses (verified by killing the backend process
mid-pause and confirming it resumes from `human_approval`, not from the start, after restart). The
approval panel shows the proposed patch and the model's own rationale before a human decides.

### Observability (LangSmith)

Opt-in, default off, zero code changes — env vars only (`LANGSMITH_TRACING`, `LANGSMITH_API_KEY`,
`LANGSMITH_PROJECT`). When on, every node and router shows up as a real trace with per-node
latency; the interrupt shows up as a span with `status: "interrupted"`, and the resume starts a
second linked trace. (One honest gap, worth knowing if asked: per-node **token counts** aren't
populated, because the LLM calls go through the raw Groq SDK, not a LangChain-wrapped model —
documented in README, not silently hidden.)

### Engineering rigor

`tests/` — 103 tests, ~3 seconds, zero network/LLM/DB calls, 71% coverage on the graph/pipeline/
analytics code. `test_regressions.py` pins 5 real bugs found during the build (not hypothetical
ones) — e.g. the dict-spread-order bug that meant the approval panel had never actually rendered
for a real interrupted run, until it was found and fixed.

---

## ⚠️ Before you present: the live deployed dashboard's current data

Worth knowing so nothing catches you off guard on stage. The deployed backend's default namespace
(what the live dashboard actually shows — it never filters by namespace) currently has **11
runs**, not the larger 20-run seeded set from earlier dashboard work (that set still exists in the
database under a different namespace, but the live UI doesn't display it). Of those 11:

| Run | Dataset | Shows | Use it? |
|---|---|---|---|
| `d1a3e642…` | day4_combo | **healed, 2 attempts** (4.9s, real LLM calls) | ✅ great — the real money shot, already sitting there |
| `090061ea…` | day1_clean | clean | ✅ fine — the baseline |
| `8f70cbff…`, `9998626f…`, `a1e1548a…`, `33a31670…` | day5/day2/day2/day4 | escalated, `unknown`, all ~130–225ms | ⚠️ **don't lead with these.** All 4 finished in ~200ms — too fast for even one real LLM call (this project's real `diagnose` calls run 0.9–1.9s; the one healed run above took 4.9s). That duration is `services/llm.py` failing *instantly*, with no network call at all — the exact signature of a missing `GROQ_API_KEY`, a real misconfiguration this build hit and fixed once already (README/session history). These are not evidence the agent can't handle a plain rename or classify day5 correctly — Phase 3 proved 25/25 on exactly these cases with a working key. If asked, say so plainly rather than presenting them as real outcomes. |
| 5 rows | day1/day2 | stuck `running` | 🚫 **don't click into these** — leftover from a Dockerfile path bug fixed weeks ago; the backing process died mid-run and these will poll forever. Harmless to leave, confusing to open live. |

There is **no existing human-approval example** in this set — day6 never ran here. That's fine:
the live-triggered walkthrough below produces one fresh, and watching it happen live is more
convincing than a pre-existing row anyway.

If you'd rather present against the fuller, cleaner 20-run set instead, that just needs
`PIPELINE_ENV=dashboard-demo` set on Render alongside the existing env vars — ask and it can be
verified before you go on.

---

## Demo script (~10–15 minutes)

**Warm the backend first** — Render's free tier sleeps after ~15 min idle and takes up to ~50s to
wake. Hit `https://self-healing-data-pipeline-1fwy.onrender.com/health` a minute before you start.

### Part A — orient (2 min, no live LLM calls, nothing can go wrong here)

1. Open the deployed dashboard. Point at the KPI strip, the drift-class chart, the specialist
   bars — "this is real historical data, not mocked."
2. Click into `d1a3e642…` (day4_combo, healed). Point at the **`↺ Heal attempt 2` badge** on the
   graph, the two entries in the attempt timeline, the assertion bars (all green now), the patch
   diff (two merged fixes: a rename + a cast).
3. Ask the **per-run chat**: *"Why did this take two attempts?"* — watch it correctly explain the
   combo drift from the attempt history, not from a stale top-level field.

### Part B — live, in order (the actual money shots)

4. **`+ New run` → day1 — clean (control).** Passes immediately, no healing. Establishes the
   baseline: not every run needs the agent at all.
5. **day2 — renamed column.** `diff_contract` finds the missing column, the rename specialist
   proposes `{cust_id: customer_id}`, auto-applies (confidence ≥ 0.75), reruns — **watch the
   assertion bars flip red → green live.**
6. **day4 — combo (the cycle). This is the project.** Two heal iterations. The `↺ Heal attempt 2`
   badge appears live, the graph highlights the second loop through `diff_contract`.
7. **day6 — ambiguous (human review).** The agent correctly identifies a rename but isn't
   confident in the specific mapping — it halts with a real patch diff and a plain-language
   rationale to review, not auto-applying. **Approve it** in the panel. Watch it resume mid-graph
   (`apply_patch` → `rerun_validate` → healed) — it does not restart from the top.
8. **day5 — unfixable.** Two unrelated, non-mechanical drifts (an undeclared column, abbreviated
   region codes no cast can expand). The agent escalates *directly*, proposing nothing. This is
   the other correct outcome — distinct from step 7's "unsure, ask a human" — and just as
   important to show: **it doesn't guess when it shouldn't.**

### Part C — bonus, if time allows

9. On Overview, ask the **global chat**: *"Which drift class is hardest to heal?"* — it should
   reason from the aggregates (rename vs type's real healed/escalated split), not just point at
   `unknown`'s trivial 0%-by-construction rate. Then ask *"How many runs escalated?"* and point out
   the number matches the KPI strip exactly — it's reading the same aggregate, not recounting.
10. Open LangSmith, find the trace for the day4 or day6 run just triggered. Show the node-by-node
    latency, and for day6, the `human_approval` span's `status: "interrupted"` followed by a
    second linked trace for the resume.
11. Run `pytest` in a terminal. 103 tests, ~3 seconds, zero network. If asked "how do you know the
    routing logic is right, not just the demo data" — this is the answer.

**If time is short: cut 1, 9, 10, 11 first. Never cut 6, 7, or 8** — those three are the actual
argument (auto-heal, ask-a-human, correctly-refuse), and step 6 is the literal headline feature.

---

## If something goes wrong

- **Backend 500s or times out on the first request** — cold Neon compute or cold Render instance.
  Wait ~15s and retry once; `/health` is deliberately DB-free so it won't help diagnose this, but
  `/api/ping` will show `database.connected`.
- **A run sits at "running" and never progresses** — check the backend actually started (this
  exact failure mode, permanently-stuck runs, is what the Dockerfile bug in the table above looks
  like). Don't debug live; fall back to a pre-existing healed run (`d1a3e642…`) and narrate from
  there.
- **Groq rate-limits mid-demo** (free tier, shared across all testing) — the run will correctly
  degrade to `drift_class: unknown` / escalate rather than crash or guess. That degradation
  *is* the guardrail working as designed — say so, and reference the Phase 3 25/25 repeatability
  sweep (README) as the real capability number, since this exact quota issue is why
  `docs/eval_summary.md` is still marked pending rather than a fabricated complete table.
- **Chat returns a 503** — a known, transient Groq per-minute rate limit under back-to-back large
  requests (global scope's context is ~5x a per-run one). The frontend already retries once
  silently; if it still fails, wait ~20s and ask again.
- **Network dies entirely** — narrate from screenshots / the README's "Testing" and
  "Observability" sections, which contain the actual verification transcripts (real numbers, real
  trace excerpts) rather than claims.

---

## Anticipated questions

- **"Is this scheduled / does it watch for new files?"** No — trigger-based by design
  (`POST /api/runs`), no cron/Airflow/Dagster. Explicitly out of scope; adding orchestration
  tooling was a guardrail to avoid, not an oversight.
- **"What stops the agent from writing arbitrary code?"** It can't, structurally — every output is
  validated against a Pydantic schema (`extra="forbid"`) naming only pre-registered operations
  (`strip_commas_to_float`, `fill_unknown`, …). There's no code path from LLM output to `exec`.
- **"Does every drift class work equally well?"** Rename and type are proven end-to-end against
  real fault data. Nullability's patch mechanism is implemented and schema-validated but no fault
  dataset in this build exercises it live — stated in README's Known Limitations, not hidden.
- **"How do you know the dashboard numbers are honest, not cherry-picked?"** Point to
  `test_regressions.py` — 5 tests, each pinning a real bug (wrong denominator, wrong bucketing,
  the approval-panel bug) that was found by cross-checking dashboard numbers against a direct
  query and fixed, with the wrong-vs-right math still in the test today.
- **"What happens if the backend restarts mid-approval?"** Verified directly, once, the hard way:
  killed the process (`Stop-Process -Force`, not a reload) between an interrupt and its resume,
  for both approve and reject, and confirmed via Postgres that the checkpoint was there and a
  fresh process resumed from `human_approval` with zero re-executed nodes before it.

---

## Quick reference

- **Frontend**: https://self-healing-data-pipeline-r3dt.vercel.app
- **Backend**: https://self-healing-data-pipeline-1fwy.onrender.com (`/health`, `/api/ping`)
- **Auth**: shared bearer token — already inlined into the deployed frontend; for `curl`, it's the
  `SHARED_TOKEN` value in your own `.env` (never printed in this file).
- **LangSmith project**: `self-healing-data-pipeline` (only shows traces from runs made while
  `LANGSMITH_TRACING=true` was set on Render).
- **If a live-triggered demo run needs cleaning up afterward**: it lands in whatever namespace
  Render's `PIPELINE_ENV` currently resolves to (see the caveat above) — delete via the run's
  `run_id` from `checkpoints`/`checkpoint_blobs`/`checkpoint_writes`/`run_records`/`mapping_state`,
  the same way every verification run in this project's history has been cleaned up. Don't use
  `scripts/reset_seed_data.py` against a namespace you didn't create yourself — it wipes
  everything in it.
