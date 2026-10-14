"""Phase 6 eval (ARCHITECTURE.md, "## Eval").

Runs the P0 fixture matrix twice:

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
  Each fixture checkpoints a unique `pipeline_env` plus the default contract
  snapshot/hash. The graph passes that scope explicitly to mapping reads,
  guarded writes, and outcome records. `run_records` and `component_events`
  are initialized before the graph; each fixture registers its run so its
  audit writes are real. The script still sets PIPELINE_ENV for compatibility
  with older helpers, but the checkpointed scope is authoritative. A fresh
  namespace seeds an empty mapping, keeping fixtures independent.

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
from pipeline.contract import get_registry, load_contract  # noqa: E402
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
        {"status": "healed", "heal_attempts": 1, "auto_approved": False}),
    ("day5_unfixable.csv", "day5_unfixable", "two unrelated, non-mechanical drifts", "fail",
        {"status": "escalated", "heal_attempts": 0, "auto_approved": False}),
    ("day6_ambiguous_rename.csv", "day6_ambiguous_rename", "ambiguous rename", "fail",
        {"status": "healed", "heal_attempts": 1, "auto_approved": True}),
    ("day7_value_drift.csv", "day7_value_drift", "N/S/E/W categorical labels", "fail",
        {"status": "healed", "heal_attempts": 1, "auto_approved": False}),
    ("day7_mixed.csv", "day7_mixed", "rename + type + value", "fail",
        {"status": "healed", "heal_attempts": 1, "auto_approved": False}),
    ("negative_new_category.csv", "negative_new_category", "new business category", "fail",
        {"status": "escalated", "heal_attempts": 0, "auto_approved": False}),
    ("negative_minimum.csv", "negative_minimum", "numeric minimum violation", "fail",
        {"status": "escalated", "heal_attempts": 0, "auto_approved": False}),
    ("negative_unique.csv", "negative_unique", "uniqueness violation", "fail",
        {"status": "escalated", "heal_attempts": 0, "auto_approved": False}),
]


def _line() -> None:
    print("-" * 78)


def _blank_state(run_id: str, source_path: str, pipeline_env: str) -> dict:
    contract = get_registry().get("default")
    return {
        "run_id": run_id,
        "source_path": source_path,
        "pipeline_env": pipeline_env,
        "contract_name": contract.name,
        "contract_version": contract.version,
        "contract_snapshot": contract.spec,
        "mapping_scope": f"mapping:{pipeline_env}:{contract.name}",
        "baseline_failures": [],
        "rollback_version": None,
        "rollback_reason": None,
        "audit_error": None,
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
        "specialist_outputs": None,
        "candidates": [],
        "components": [],
        "mapping_version_read": None,
        "mapping_version_written": None,
        "source_sha256": None,
        "patch_error": None,
        "proposal_error": None,
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
        "failure_ids": [f"{f.column}:{f.check}" for f in result.failures],
    }


async def run_full_graph(filename: str, label: str) -> dict:
    env = f"{EVAL_ENV_PREFIX}{label}-{uuid.uuid4().hex[:8]}"
    os.environ["PIPELINE_ENV"] = env
    app_config.get_settings.cache_clear()  # force Settings() to re-read PIPELINE_ENV

    graph_app = build_graph(InMemorySaver())
    run_id = f"{env}-{uuid.uuid4().hex[:8]}"
    cfg = {"configurable": {"thread_id": run_id}, "recursion_limit": RECURSION_LIMIT}

    source_path = str(DATA_DIR / filename)
    contract = get_registry().get("default")
    await store.record_run(run_id, source_path, env, contract.name, contract.version,
                           {"kind": "bundled_csv", "source_id": filename})
    stream_input = _blank_state(run_id, source_path, env)
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
    mapping, version = await store.get_current_mapping_version(env, contract.name, contract)
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
        "final_mapping": mapping.model_dump(),
        "final_mapping_version": version,
        "final_failure_ids": [f"{f['column']}:{f['check']}"
                              for f in values.get("assertion_failure_details", [])],
        "human_decision": values.get("human_decision"),
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
    await store.init_runs_table()
    await store.init_component_tables()

    print("=" * 78)
    print(f"P0 EVAL — baseline vs. full graph, {len(DATASETS)} datasets")
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
            "failure_delta": {
                "resolved": sorted(set(baseline["failure_ids"]) - set(agent["final_failure_ids"])),
                "introduced": sorted(set(agent["final_failure_ids"]) - set(baseline["failure_ids"])),
            },
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
    n_escalated = sum(1 for r in rows if r["agent_status"] == "escalated")
    n_baseline_fail = sum(1 for r in rows if r["baseline"] == "fail")
    n_unattended = sum(1 for r in rows if r["agent_status"] == "healed"
                       and not r["raw_agent"]["auto_approved"])
    n_reviewed = sum(1 for r in rows if r["raw_agent"]["auto_approved"])

    lines = [
        "# Eval Summary",
        "",
        "Generated by `eval/run_eval.py`. Baseline runs once with the seed mapping. "
        "The graph run uses an isolated mapping namespace per fixture and evaluation. "
        "Low-confidence proposals are auto-approved only for this eval and are reported separately.",
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
        f"**Measured:** {n_unattended} unattended heals, {n_reviewed} eval auto-approval{'s' if n_reviewed != 1 else ''}, "
        f"{n_escalated} escalations, {n_baseline_fail} baseline failures across {len(rows)} fixtures.",
        "",
        "Raw results include baseline and final failure IDs, exact proposal, mapping snapshot, "
        "attempts, components, and approval decisions for each fixture.",
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
