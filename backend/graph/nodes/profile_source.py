"""Real node — Phase 3, deterministic. Reads the RAW source CSV directly (no
mapping applied) and records column names, inferred dtypes, null counts, and
sample values — giving diagnose real facts rather than letting it hallucinate
column names (ARCHITECTURE.md's node spec).

Runs once per run, not on the loop-back path (see routing.route_after_rerun:
a failed rerun loops to diff_contract, not here). diff_contract re-derives
fresh drift signal on every iteration from the latest pipeline run's actual
failures instead — see diff_contract.py's docstring for why that, not a
re-profile of the static raw file, is what makes the signal narrow correctly
between heal attempts.
"""

import pandas as pd


def profile_source(state: dict) -> dict:
    try:
        df = pd.read_csv(state["source_path"])
    except Exception as exc:
        return {
            "actual_schema": {},
            "source_profile": {"__error__": {"message": f"{type(exc).__name__}: {exc}"}},
        }

    schema: dict[str, str] = {}
    profile: dict[str, dict] = {}
    for col in df.columns:
        dtype = str(df[col].dtype)
        schema[col] = dtype
        profile[col] = {
            "dtype": dtype,
            "null_count": int(df[col].isna().sum()),
            "samples": df[col].dropna().astype(str).head(3).tolist(),
        }

    return {"actual_schema": schema, "source_profile": profile}
