"""Descriptive component metrics. Validation and approval never imply correctness."""
from math import sqrt


def wilson_interval(successes: int, total: int, z: float = 1.96) -> list[float] | None:
    if total == 0:
        return None
    p = successes / total
    z2 = z * z
    denom = 1 + z2 / total
    center = (p + z2 / (2 * total)) / denom
    margin = z * sqrt((p * (1 - p) + z2 / (4 * total)) / total) / denom
    return [round(max(0.0, center - margin), 3), round(min(1.0, center + margin), 3)]


def aggregate_component_events(rows: list[dict]) -> dict:
    proposals = [r for r in rows if r["event_type"] == "proposal"]
    decisions = [r for r in rows if r["event_type"] == "decision"]
    outcomes = [r for r in rows if r["event_type"] == "outcome" and r.get("validation_passed") is not None]
    rejected = [r for r in rows if r["event_type"] == "outcome" and r.get("validation_passed") is None]
    labeled = [r for r in proposals if r.get("labeled_correct") is not None]
    correct = sum(r["labeled_correct"] is True for r in labeled)
    approved = sum(r.get("decision") == "human_approved" for r in decisions)
    reviewed = sum(r.get("decision") in {"human_approved", "human_rejected"} for r in decisions)
    return {
        "proposals": len(proposals),
        "decisions": len(decisions),
        "auto_applied": sum(r.get("decision") == "auto_applied" for r in decisions),
        "human_reviewed": reviewed,
        "human_approved": approved,
        "human_approval_rate": round(approved / reviewed, 3) if reviewed else None,
        "human_approval_denominator": reviewed,
        "validation_passed": sum(r.get("validation_passed") is True for r in outcomes),
        "validation_denominator": len(outcomes),
        "rejected_without_validation": len(rejected),
        "labeled_correct": correct,
        "labeled_denominator": len(labeled),
        "labeled_correctness_rate": round(correct / len(labeled), 3) if labeled else None,
        "labeled_correctness_ci95": wilson_interval(correct, len(labeled)),
        "adaptive_threshold_enabled": False,
        "confidence_threshold": 0.75,
    }
