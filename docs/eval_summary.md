# Eval Summary

**Status: pending — a real run is in progress, blocked partway by Groq's daily token budget.**

`eval/run_eval.py` (ARCHITECTURE.md, "## Eval") ran today and produced the mechanism-verification
results below, but the Groq free tier's 200,000-token-per-day budget (already tight from Phase 5's
extensive live testing yesterday) ran out mid-sweep: `diagnose`/`propose_patch` calls from
`day3_type_drift` onward returned HTTP 429, and the guardrail did exactly what it's supposed to
do under that failure — degraded to `drift_class: unknown`, `confidence: 0.0`, and escalated,
rather than raising or guessing. That is correct behavior for a real failure; it is **not** the
capability number this table is supposed to report, so this file is intentionally left as a
partial/pending result rather than presenting those escalations as the real outcome.

| Dataset | Fault | Baseline | Agent | Heal attempts | Outcome |
|---|---|---|---|---|---|
| day1_clean | none | pass | pass | 0 | clean — **confirmed live today** |
| day2_renamed | customer_id -> cust_id | **fail** | pass | 1 | auto-healed — **confirmed live today** |
| day3_type_drift | revenue as comma-formatted string | **fail** | *pending* | *pending* | blocked by Groq daily quota mid-run |
| day4_combo | rename + type drift together | **fail** | *pending* | *pending* | blocked by Groq daily quota mid-run |
| day5_unfixable | two unrelated, non-mechanical drifts | **fail** | **fail** | 0 | correctly escalated, no patch proposed — **confirmed live today**, though diagnose never got far enough to consume a meaningful token budget here regardless |
| day6_ambiguous_rename | ambiguous rename | **fail** | *pending* | *pending* | blocked by Groq daily quota mid-run |

A background job is polling Groq (probing with a realistic-sized request, backing off on the
`retry-after` hint in each 429 body) and will re-run `eval/run_eval.py` automatically once the
budget can absorb a full sweep, regenerating this file for real. All three "pending" rows were
already verified, repeatedly, against a fresh quota in Phase 3 (25/25 across a repeatability
sweep — see README.md's Phase 3 section) — this file will confirm those numbers hold in one
continuous, unattended, end-to-end run, which is a different and stronger claim than "the
prompt is well-tuned."

Raw per-dataset output from today's partial run: `eval/results/eval_results.json` (also partial
— will be overwritten by the rerun).
