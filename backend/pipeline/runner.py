"""load -> apply_mapping -> transform -> validate. ~120 lines by design — see
ARCHITECTURE.md, "The Pipeline (keep it dumb)". No DAG engine, no retries, no
scheduling.

`run_pipeline` never raises. Every step is wrapped so a broken source file or
a bad cast produces a structured failure the graph can route on (Phase 2+),
not an exception that kills the run.
"""

from dataclasses import dataclass, field

import pandas as pd

from pipeline.contract import Contract, FailureCase, validate
from pipeline.mapping import MappingPatch
from pipeline.steps import apply_mapping, transform


@dataclass
class PipelineResult:
    passed: bool
    failures: list[FailureCase] = field(default_factory=list)
    rows_in: int = 0
    rows_out: int = 0

    def as_strings(self) -> list[str]:
        return [f.summary() for f in self.failures]


def _load(source_path: str) -> pd.DataFrame:
    return pd.read_csv(source_path)


def run_pipeline(
    source_path: str, mapping: MappingPatch, contract: Contract
) -> PipelineResult:
    try:
        df = _load(source_path)
    except Exception as exc:
        return PipelineResult(
            passed=False,
            failures=[
                FailureCase(
                    column="__source__",
                    check="load_error",
                    failure_count=1,
                    example_values=[f"{type(exc).__name__}: {exc}"],
                )
            ],
        )
    rows_in = len(df)

    try:
        df = apply_mapping(df, mapping)
        df = transform(df)
    except Exception as exc:
        return PipelineResult(
            passed=False,
            failures=[
                FailureCase(
                    column="__mapping__",
                    check="transform_error",
                    failure_count=1,
                    example_values=[f"{type(exc).__name__}: {exc}"],
                )
            ],
            rows_in=rows_in,
        )

    result = validate(df, contract)
    return PipelineResult(
        passed=result.passed,
        failures=result.failures,
        rows_in=rows_in,
        rows_out=len(df),
    )
