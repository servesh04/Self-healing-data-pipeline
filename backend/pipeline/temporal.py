"""Alert-only date-range monitoring against a pinned rule and UTC run clock."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json

import pandas as pd
import yaml

from pipeline.mapping import MappingPatch
from pipeline.steps import apply_mapping, transform

RULES = Path(__file__).resolve().parents[2] / "config" / "temporal_rules.yaml"


@dataclass(frozen=True)
class TemporalResult:
    alerts: list[dict]
    skipped: list[dict]
    profile: dict
    rule_digest: str | None


def _digest(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


def load_temporal_rule(contract_name: str, family: str | None, path: Path = RULES) -> dict | None:
    if not family:
        return None
    key = f"{contract_name}/{family}"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    spec = (raw.get("rules") or {}).get(key)
    if spec is None:
        return None
    if (spec.get("timezone") != "UTC" or not spec.get("version") or
            spec.get("column") != "order_date" or
            int(spec.get("minimum_valid_rows", 0)) < 1 or
            int(spec.get("max_future_skew_days", -1)) < 0 or
            int(spec.get("max_freshness_lag_days", -1)) < 0 or
            int(spec.get("max_history_days", -1)) < 0):
        raise ValueError("invalid temporal rule")
    return {"rule_id": key, "spec": spec, "digest": _digest(spec)}


def check_temporal(source_path: str, mapping: MappingPatch, contract_name: str,
                   family: str | None, run_clock_utc: str,
                   pinned_rule: dict | None = None, rule_path: Path = RULES) -> TemporalResult:
    rule = pinned_rule if pinned_rule is not None else load_temporal_rule(contract_name, family, rule_path)
    if rule is None:
        return TemporalResult([], [{"reason": "no temporal rule"}], {}, None)
    if rule["rule_id"] != f"{contract_name}/{family}" or _digest(rule["spec"]) != rule["digest"]:
        raise ValueError("pinned temporal rule mismatch")
    spec = rule["spec"]
    clock = datetime.fromisoformat(run_clock_utc.replace("Z", "+00:00"))
    if clock.tzinfo is None or clock.utcoffset().total_seconds() != 0:
        raise ValueError("run clock must be UTC")
    frame = transform(apply_mapping(pd.read_csv(source_path), mapping))
    dates = pd.to_datetime(frame[spec["column"]], errors="coerce")
    valid = dates.dropna()
    profile = {"row_count": len(frame), "parsed_count": len(valid),
               "invalid_count": int(dates.isna().sum()), "minimum": None, "maximum": None,
               "rule_id": rule["rule_id"], "rule_version": spec["version"],
               "timezone": spec["timezone"], "run_clock_utc": run_clock_utc}
    if len(valid):
        profile["minimum"] = valid.min().isoformat()
        profile["maximum"] = valid.max().isoformat()
    minimum = int(spec["minimum_valid_rows"])
    if len(valid) < minimum:
        return TemporalResult([], [{"reason": "insufficient sample", "valid": len(valid),
                                     "minimum": minimum}], profile, rule["digest"])
    clock_day = pd.Timestamp(clock).tz_localize(None).normalize()
    future = valid > clock_day + pd.Timedelta(days=int(spec["max_future_skew_days"]))
    old = valid < clock_day - pd.Timedelta(days=int(spec["max_history_days"]))
    recent = valid.max() < clock_day - pd.Timedelta(days=int(spec["max_freshness_lag_days"]))
    alerts = []
    for check, count in (("future_date", int(future.sum())), ("outside_history_window", int(old.sum())),
                         ("stale_batch", int(bool(recent)))):
        if count:
            alerts.append({"column": spec["column"], "check": check, "count": count,
                           "rule_id": rule["rule_id"], "rule_version": spec["version"]})
    return TemporalResult(alerts, [], profile, rule["digest"])
