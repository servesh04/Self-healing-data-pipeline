"""Held-out A/B proposal comparison; never enables example prompting.

Input JSON: {"cases": [{"case_id": "...", "specialist": "spec_rename",
"state": {<checkpoint-like specialist state>}, "expected_patch": {"renames": {...}}}]}
Every case must use a distinct held-out run_id not used as a retrieved review.
This script only calls specialists and reads labeled examples; it never writes
a mapping, checkpoint, label, or threshold.
"""

import argparse
import asyncio
import json
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import db  # noqa: E402
from config import get_settings  # noqa: E402
from graph.nodes.spec_rename import spec_rename  # noqa: E402
from graph.nodes.spec_type import spec_type  # noqa: E402
from graph.nodes.spec_nullability import spec_nullability  # noqa: E402
from graph.nodes.spec_value import spec_value  # noqa: E402

SPECIALISTS = {"spec_rename": spec_rename, "spec_type": spec_type,
               "spec_nullability": spec_nullability, "spec_value": spec_value}


async def compare(cases: list[dict], pipeline_env: str, seed: int = 42) -> dict:
    if not cases or len({case["case_id"] for case in cases}) != len(cases):
        raise ValueError("cases need unique IDs")
    if len({case["state"]["run_id"] for case in cases}) != len(cases):
        raise ValueError("held-out run IDs must be unique")
    rng = random.Random(seed)
    rows = []
    for case in cases:
        specialist = case["specialist"]
        if specialist not in SPECIALISTS or case["state"].get("pipeline_env") != pipeline_env:
            raise ValueError("unknown specialist or mismatched pipeline environment")
        arms = [False, True]
        rng.shuffle(arms)
        outputs = []
        for with_examples in arms:
            os.environ["ENABLE_APPROVAL_EXAMPLES"] = "1" if with_examples else "0"
            patch = (await SPECIALISTS[specialist](case["state"])).get("specialist_output") or {}
            outputs.append({"arm": "A" if len(outputs) == 0 else "B",
                            "with_examples": with_examples,
                            "patch": patch,
                            "exact_match": patch == case["expected_patch"]})
        rows.append({"case_id": case["case_id"], "specialist": specialist,
                     "arms": outputs})
    return {"seed": seed, "pipeline_env": pipeline_env, "cases": rows,
            "without_examples": {"correct": sum(o["exact_match"] for row in rows for o in row["arms"] if not o["with_examples"]), "total": len(rows)},
            "with_examples": {"correct": sum(o["exact_match"] for row in rows for o in row["arms"] if o["with_examples"]), "total": len(rows)}}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pipeline-env", required=True)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    await db.open_pool(get_settings().database_url)
    try:
        result = await compare(cases, args.pipeline_env, args.seed)
    finally:
        os.environ.pop("ENABLE_APPROVAL_EXAMPLES", None)
        await db.close_pool()
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
