"""Deterministic date specialist: only a fully observed declared format is eligible."""

import pandas as pd

from graph.schemas import DateParseSpecialistOutput
from pipeline.contract import contract_for_state
from pipeline.mapping import DATE_PARSE_REGISTRY
from pipeline.steps import parse_declared_date


async def spec_date_parse(state: dict) -> dict:
    findings = state.get("drift_items") or []
    contract = contract_for_state(state)
    if (not findings or any(f.get("column") != "order_date" or
                            f.get("kind") not in {"null_violation", "dtype_mismatch"}
                            for f in findings) or
            contract.expected_dtypes.get("order_date") != "datetime64[ns]"):
        return {"specialist_output": {}}
    try:
        dates = pd.read_csv(state["source_path"], usecols=["order_date"])["order_date"]
        if dates.empty or dates.isna().any():
            return {"specialist_output": {}}
        for operation in DATE_PARSE_REGISTRY:
            parsed = parse_declared_date(dates, operation)
            if parsed.notna().all():
                return {"specialist_output": DateParseSpecialistOutput.model_validate(
                    {"date_parse": {"order_date": operation}}).model_dump()}
    except (ValueError, KeyError, OSError):
        pass
    return {"specialist_output": {}}
