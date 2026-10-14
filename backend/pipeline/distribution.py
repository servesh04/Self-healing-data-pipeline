"""Alert-only robust distribution checks against reviewed, versioned baselines.

The median shift is measured in baseline IQR units. Baselines are curated, not
learned from the batch under test; a shifted batch can never redefine normal.
"""

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json

import pandas as pd
import yaml

from pipeline.mapping import MappingPatch
from pipeline.steps import apply_mapping, transform

BASELINES = Path(__file__).resolve().parents[2] / "config" / "distribution_baselines.yaml"


@dataclass(frozen=True)
class DistributionResult:
    baseline_id: str | None
    baseline_version: str | None
    baseline_digest: str | None
    alerts: list[dict]
    skipped: list[dict]
    profile: dict


def load_baseline(contract_name: str, family: str | None, path: Path = BASELINES) -> dict | None:
    if not family:
        return None
    baseline_id = f"{contract_name}/{family}"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    spec = (raw.get("baselines") or {}).get(baseline_id)
    if spec is None:
        return None
    if not isinstance(spec, dict) or not isinstance(spec.get("columns"), dict):
        raise ValueError(f"invalid distribution baseline: {baseline_id}")
    if int(spec.get("minimum_rows", 0)) < 2 or not 0 <= float(spec.get("max_missing_fraction", -1)) <= 1:
        raise ValueError("invalid distribution baseline sample or missingness rule")
    if float(spec.get("median_shift_iqr_limit", 0)) <= 0:
        raise ValueError("invalid distribution shift threshold")
    for column, reference in spec["columns"].items():
        if (int(reference.get("count", 0)) < int(spec["minimum_rows"])
                or int(reference.get("missing_count", -1)) < 0
                or not float(reference["q1"]) < float(reference["q3"])):
            raise ValueError(f"invalid distribution reference: {column}")
    digest = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
    return {"baseline_id": baseline_id, "spec": spec, "digest": digest}


def check_distribution(source_path: str, mapping: MappingPatch, contract_name: str,
                       family: str | None, baseline_path: Path = BASELINES,
                       pinned_baseline: dict | None = None) -> DistributionResult:
    if not family:
        return DistributionResult(None, None, None, [], [{"reason": "no source family"}], {})
    loaded = pinned_baseline if pinned_baseline is not None else load_baseline(contract_name, family, baseline_path)
    if loaded is None:
        return DistributionResult(None, None, None, [], [{"reason": "no reviewed baseline"}], {})
    baseline_id, spec, digest = loaded["baseline_id"], loaded["spec"], loaded["digest"]
    if baseline_id != f"{contract_name}/{family}" or hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest() != digest:
        raise ValueError("pinned distribution baseline mismatch")
    df = transform(apply_mapping(pd.read_csv(source_path), mapping))
    minimum = int(spec["minimum_rows"])
    missing_limit = float(spec["max_missing_fraction"])
    shift_limit = float(spec["median_shift_iqr_limit"])
    alerts: list[dict] = []
    skipped: list[dict] = []
    profile: dict = {"row_count": len(df), "columns": {}}
    for column, reference in spec["columns"].items():
        if column not in df:
            skipped.append({"column": column, "reason": "column absent"})
            continue
        values = pd.to_numeric(df[column], errors="coerce")
        valid = values.dropna()
        missing = len(df) - len(valid)
        entry = {"count": int(len(valid)), "missing_count": int(missing),
                 "missing_fraction": missing / len(df) if len(df) else 1.0}
        if len(valid):
            entry.update({"q1": float(valid.quantile(.25)), "median": float(valid.median()),
                          "q3": float(valid.quantile(.75))})
        profile["columns"][column] = entry
        if len(df) < minimum or len(valid) < minimum:
            skipped.append({"column": column, "reason": "insufficient sample",
                            "rows": len(df), "valid": len(valid), "minimum": minimum})
            continue
        if entry["missing_fraction"] > missing_limit:
            skipped.append({"column": column, "reason": "excess missingness",
                            "missing_fraction": entry["missing_fraction"]})
            continue
        spread = float(reference["q3"]) - float(reference["q1"])
        if spread <= 0:
            skipped.append({"column": column, "reason": "zero baseline IQR"})
            continue
        distance = abs(entry["median"] - float(reference["median"])) / spread
        if distance > shift_limit:
            alerts.append({"column": column, "check": "median_shift_iqr",
                           "baseline_median": float(reference["median"]),
                           "observed_median": entry["median"], "distance_iqr": distance,
                           "threshold_iqr": shift_limit})
    return DistributionResult(baseline_id, str(spec["version"]), digest, alerts, skipped, profile)
