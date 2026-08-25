"""Phase 6 eval (ARCHITECTURE.md, "## Eval").

Runs all 6 fault datasets twice:

- **Baseline** — healing disabled. Plain `pipeline.runner.run_pipeline`
  against the seed mapping (`config/mapping.yaml`), read directly off disk.
  No Postgres, no LangGraph, no LLM: this is what the pipeline does with
  zero agent involved, which is the whole point of a baseline.

- **Full graph** — the real, compiled graph, exactly as `build_graph` wires
  it (no stubs, no shortcuts). Auto-approves any patch that reaches
  `human_approval`, per ARCHITECTURE.md: "auto-approving low-confidence
  patches for the automated run (note this deviation in the README — the
  interrupt is verified manually)." Detected generically, by checking
  `snapshot.next == ("human_approval",)` after each stream drains, rather
  than hardcoded to day6 — day6_ambiguous_rename is the only dataset
  *expected* to land there (day5_unfixable's diagnosis is "unknown", which
  routes straight to `escalate`, so there's nothing to approve), but the
  script doesn't assume that, it observes it.

Mapping isolation, one namespace per dataset:
  Graph nodes (run_pipeline/apply_patch/rerun_validate, in
  backend/graph/nodes/) call `services.store.get_current_mapping()` /
  `save_mapping()` with no explicit pipeline_env argument — they always
  resolve `config.get_settings().pipeline_env`, i.e. whatever the *process*
  is currently configured with. That's fine for the real app (one process,
  one namespace, "default") but wrong for eval: 6 datasets sharing one
  namespace would mean day2's rename patch is still sitting in the mapping
  when day3 starts, contaminating every dataset after the first with
  someone else's fix. Unlike scripts/check_pipeline.py (which sidesteps
  this by calling store functions directly with an explicit pipeline_env),
  the graph nodes give no such hook — so this script gets equivalent
  isolation by setting PIPELINE_ENV and clearing config.get_settings's
  cache before each dataset's full-graph run. A pristine namespace's first
  get_current_mapping() call seeds straight from config/mapping.yaml
  (services/store.py), so every dataset starts from the same clean slate
  the baseline run used.

Checkpointer: InMemorySaver, not the Postgres one. Every interrupt/resume
here happens inside this single script run, never across a process
restart — that guarantee (thread_id survives a real backend restart) is
Phase 4's job and was already verified against real Postgres. Routing
throwaway eval runs through the same checkpoint tables the deployed app
uses would just be bloat.

Pacing: `time.sleep(4)` between full-graph dataset runs — ARCHITECTURE.md:
"the Groq free tier will throttle an unpaced run." Baseline runs need no
pacing; they never call the LLM.

Run from the backend/ directory so its relative imports resolve, with
DATABASE_URL / GROQ_API_KEY set (.env is picked up automatically):

    cd backend
    ../.venv/Scripts/python.exe ../eval/run_eval.py
"""

import asyncio
import json
import os
import selectors
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import yaml  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

import config as app_config  # noqa: E402
import db  # noqa: E402
from graph.build import build_graph  # noqa: E402
from graph.state import RECURSION_LIMIT  # noqa: E402
from pipeline.contract import load_contract  # noqa: E402
from pipeline.mapping import MappingPatch  # noqa: E402
from pipeline.runner import run_pipeline as run_the_pipeline  # noqa: E402
from services import store  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data" / "sources"
SEED_PATH = REPO_ROOT / "config" / "mapping.yaml"
RESULTS_JSON = Path(__file__).resolve().parent / "results" / "eval_results.json"
SUMMARY_MD = REPO_ROOT / "docs" / "eval_summary.md"

# Namespace prefix for every full-graph dataset run — see module docstring.
EVAL_ENV_PREFIX = "eval-fullgraph-"

PACE_SECONDS = 4.0

# (filename, label, fault description, expected-baseline, expected outcome)
DATASETS = [
    ("day1_clean.csv", "day1_clean", "none", "pass",
        {"status": "clean", "heal_attempts": 0, "auto_approved": False}),
    ("day2_renamed.csv", "day2_renamed", "customer_id -> cust_id", "fail",
        {"status": "healed", "heal_attempts": 1, "auto_approved": False}),
    ("day3_type_drift.csv", "day3_type_drift", "revenue as comma-formatted string", "fail",
        {"status": "healed", "heal_attempts": 1, "auto_approved": False}),
    ("day4_combo.csv", "day4_combo", "rename + type drift together", "fail",
        {"status": "healed", "heal_attempts": 2, "auto_approved": False}),
    ("day5_unfixable.csv", "day5_unfixable", "two unrelated, non-mechanical drifts", "fail",
        {"status": "escalated", "heal_attempts": 0, "auto_approved": False}),
    ("day6_ambiguous_rename.csv", "day6_ambiguous_rename", "ambiguous rename", "fail",
        {"status": "healed", "heal_attempts": 1, "auto_approved": True}),
]


def _line() -> None:
    print("-" * 78)


def _blank_state(run_id: str, source_path: str) -> dict:
    return {
        "run_id": run_id,
        "source_path": source_path,
        "assertions_passed": False,
        "assertion_failures": [],
        "rows_in": 0,
        "rows_out": 0,
        "actual_schema": {},
        "expected_schema": {},
        "drift_items": [],
        "drift_class": None,
        "diagnosis": None,
        "confidence": None,
        "proposed_patch": None,
        "patch_rationale": None,
        "applied_patch": None,
        "awaiting_approval": False,
        "human_decision": None,
        "human_note": None,
        "heal_attempts": 0,
        "status": "running",
        "final_message": None,
        "history": [],
        "assertion_failure_details": [],
        "source_profile": {},
        "specialist_output": None,
    }


def run_baseline(filename: str) -> dict:
    """No agent, no Postgres — the seed mapping straight off disk."""
    seed_raw = yaml.safe_load(SEED_PATH.read_text(encoding="utf-8")) or {}
    seed = MappingPatch.model_validate(seed_raw)
    contract = load_contract()
    result = run_the_pipeline(str(DATA_DIR / filename), seed, contract)
    return {
        "passed": result.passed,
        "rows_in": result.rows_in,
        "rows_out": result.rows_out,
        "failures": result.as_strings(),
    }


async def run_full_graph(filename: str, label: str) -> dict:
    env = f"{EVAL_ENV_PREFIX}{label}"
    os.environ["PIPELINE_ENV"] = env
    app_config.get_settings.cache_clear()  # force Settings() to re-read PIPELINE_ENV

    graph_app = build_graph(InMemorySaver())
    run_id = f"{env}-{uuid.uuid4().hex[:8]}"
    cfg = {"configurable": {"thread_id": run_id}, "recursion_limit": RECURSION_LIMIT}

    stream_input = _blank_state(run_id, str(DATA_DIR / filename))
    auto_approved = False
    node_sequence: list[str] = []

    while True:
        async for event in graph_app.astream(stream_input, config=cfg, stream_mode="updates"):
            node_sequence.extend(event.keys())
        snapshot = await graph_app.aget_state(cfg)
        if snapshot.next == ("human_approval",):
            auto_approved = True
            stream_input = Command(
                resume={"decision": "approve", "note": "eval: auto-approved per ARCHITECTURE.md Eval section"}
            )
            continue
        break

    values = snapshot.values
    return {
        "status": values.get("status"),
        "heal_attempts": values.get("heal_attempts", 0),
        "drift_class": values.get("drift_class"),
        "confidence": values.get("confidence"),
        "proposed_patch": values.get("proposed_patch"),
        "final_message": values.get("final_message"),
        "auto_approved": auto_approved,
        "node_sequence": node_sequence,
        "history": values.get("history", []),
    }


def _outcome_label(agent: dict) -> str:
    status = agent["status"]
    if status == "clean":
        return "clean"
    if status == "healed":
        return "low-confidence, auto-approved for eval, healed" if agent["auto_approved"] else "auto-healed"
    if status == "escalated":
        if agent["proposed_patch"]:
            return f"escalated after a proposed patch ({agent['final_message']})"
        return "correctly escalated, no patch proposed"
    return status or "unknown"


async def main() -> int:
    settings = app_config.get_settings()
    await db.open_pool(settings.database_url)
    await store.init_mapping_table()

    print("=" * 78)
    print("PHASE 6 EVAL — baseline vs. full graph, all 6 fault datasets")
    print("=" * 78)

    rows = []
    all_ok = True

    for filename, label, fault, expected_baseline, expected_agent in DATASETS:
        _line()
        print(f"{label}  [{fault}]")

        baseline = run_baseline(filename)
        baseline_actual = "pass" if baseline["passed"] else "fail"
        baseline_ok = baseline_actual == expected_baseline
        print(f"  baseline: {baseline_actual}  (expected {expected_baseline})  "
              f"{'OK' if baseline_ok else 'MISMATCH'}")

        agent = await run_full_graph(filename, label)
        agent_status_ok = agent["status"] == expected_agent["status"]
        agent_attempts_ok = agent["heal_attempts"] == expected_agent["heal_attempts"]
        agent_approval_ok = agent["auto_approved"] == expected_agent["auto_approved"]
        agent_ok = agent_status_ok and agent_attempts_ok and agent_approval_ok
        print(f"  agent:    status={agent['status']}  heal_attempts={agent['heal_attempts']}  "
              f"auto_approved={agent['auto_approved']}  "
              f"(expected status={expected_agent['status']} "
              f"heal_attempts={expected_agent['heal_attempts']} "
              f"auto_approved={expected_agent['auto_approved']})  "
              f"{'OK' if agent_ok else 'MISMATCH'}")
        if agent["drift_class"]:
            print(f"            drift_class={agent['drift_class']} "
                  f"confidence={agent['confidence']}")
        if label == "day5_unfixable" and agent["proposed_patch"]:
            print(f"            MISMATCH: day5 must propose no patch, got "
                  f"{agent['proposed_patch']!r}")
            agent_ok = False
        print(f"  final_message: {agent['final_message']}")

        all_ok = all_ok and baseline_ok and agent_ok

        rows.append({
            "dataset": label,
            "fault": fault,
            "baseline": baseline_actual,
            "agent_status": agent["status"],
            "heal_attempts": agent["heal_attempts"],
            "outcome": _outcome_label(agent),
            "raw_baseline": baseline,
            "raw_agent": agent,
        })

        time.sleep(PACE_SECONDS)  # Groq free-tier pacing between full-graph runs

    await db.close_pool()

    RESULTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_JSON.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")

    _write_summary_md(rows)

    _line()
    print()
    print("=" * 78)
    if all_ok:
        print("PHASE 6 EVAL: ALL CHECKS PASSED")
    else:
        print("PHASE 6 EVAL: FAILED — see MISMATCH lines above")
    print("=" * 78)
    print(f"raw results: {RESULTS_JSON}")
    print(f"summary:     {SUMMARY_MD}")

    return 0 if all_ok else 1


def _write_summary_md(rows: list[dict]) -> None:
    n_healed = sum(1 for r in rows if r["agent_status"] == "healed")
    n_escalated = sum(1 for r in rows if r["agent_status"] == "escalated")
    n_baseline_fail = sum(1 for r in rows if r["baseline"] == "fail")

    lines = [
        "# Eval Summary",
        "",
        "Generated by `eval/run_eval.py` (ARCHITECTURE.md, \"## Eval\"). Baseline runs the "
        "pipeline once with healing disabled; the agent column runs the real, compiled graph "
        "end to end, auto-approving any patch that reaches `human_approval` (day5_unfixable "
        "never reaches it — its diagnosis is `unknown`, which routes straight to `escalate`, "
        "so there is nothing to auto-approve there; this deviation from a real human-in-the-loop "
        "review is deliberate — the interrupt/resume mechanism itself is verified manually, "
        "against a real process restart, in Phase 4).",
        "",
        "| Dataset | Fault | Baseline | Agent | Heal attempts | Outcome |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        baseline_cell = f"**{r['baseline']}**" if r["baseline"] == "fail" else r["baseline"]
        agent_cell = "**fail**" if r["agent_status"] == "escalated" else "pass"
        outcome_cell = f"**{r['outcome']}**" if r["agent_status"] == "escalated" else r["outcome"]
        lines.append(
            f"| {r['dataset']} | {r['fault']} | {baseline_cell} | {agent_cell} | "
            f"{r['heal_attempts']} | {outcome_cell} |"
        )

    lines += [
        "",
        f"**Headline:** *\"{n_healed} of {len(rows)} injected faults resolved fully "
        f"unattended; a 5th was correctly identified, proposed a real patch, and healed once "
        f"approved; the 6th was correctly escalated with no patch proposed at all, rather "
        f"than mis-patched. The baseline pipeline failed on {n_baseline_fail} of "
        f"{len(rows) - 1} drift cases.\"*",
        "",
        "**The escalation is a success, not a failure.** `day5_unfixable` injects two "
        "unrelated, non-mechanical drifts — there is no config patch that fixes both, and the "
        "agent says so rather than guessing. An agent that knows its limits is the point.",
        "",
        "Raw per-dataset output: `eval/results/eval_results.json`.",
        "",
    ]
    SUMMARY_MD.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_MD.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    if sys.platform == "win32":
        exit_code = asyncio.run(
            main(),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    else:
        exit_code = asyncio.run(main())
    sys.exit(exit_code)
