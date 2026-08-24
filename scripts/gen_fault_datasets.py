"""Generates the six fault datasets in data/sources/.

Hand-authored in the sense that matters: this script is small enough to read
top to bottom, so exactly what each row contains and exactly what fault each
file injects is known, not discovered after the fact by hunting through a csv.
See ARCHITECTURE.md, "Fault Datasets — author these by hand".

Six, not five: day6_ambiguous_rename was added after running real diagnose
calibration against day5 and finding that ARCHITECTURE.md's original fault
table and demo script assumed day5 reaches human_approval, while its own
routing diagram sends an "unknown" classification straight to escalate,
bypassing human_approval entirely. The routing diagram is correct and stays
as-is; day5 correctly escalates directly (multiple unrelated, non-mechanical
drifts is what "unknown" means). human_approval needed a fault where diagnose
is confident about *which class* of fix applies but not confident in the
*specific* patch it would propose — day6 is that case. See prompts.py's
DIAGNOSE_SYSTEM for how the prompt now treats those as two separate axes
rather than one conflated judgement.

All six files share the same 50-row base data; each fault dataset then
applies exactly one (or two, for day4) mechanical mutation to it. Run with:

    python scripts/gen_fault_datasets.py
"""

import csv
import datetime
from pathlib import Path

ROWS = 50
BASE_DATE = datetime.date(2024, 1, 1)
REGIONS = ["North", "South", "East", "West"]
OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "sources"

HEADER = ["order_id", "customer_id", "region", "order_date", "revenue"]


def base_rows() -> list[dict]:
    """50 clean rows. Revenue climbs past 1000 partway through, so day3's
    comma-formatting fault is exercised on both small and thousand-plus
    values, matching ARCHITECTURE.md's "1,240.00" example.
    """
    rows = []
    for i in range(ROWS):
        rows.append(
            {
                "order_id": i + 1,
                "customer_id": 1000 + i,
                "region": REGIONS[i % len(REGIONS)],
                "order_date": (BASE_DATE + datetime.timedelta(days=i)).isoformat(),
                "revenue": round(50 + i * 45.37, 2),
            }
        )
    return rows


def write_csv(path: Path, header: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {path.relative_to(OUT_DIR.parents[1])} ({len(rows)} rows)")


def comma_format(value: float) -> str:
    """1240.0 -> '1,240.00' — the exact string format ARCHITECTURE.md names."""
    return f"{value:,.2f}"


def main() -> None:
    rows = base_rows()

    # day1_clean: control. No fault. Must pass first run, no healing triggered.
    write_csv(OUT_DIR / "day1_clean.csv", HEADER, rows)

    # day2_renamed: customer_id -> cust_id. Rename specialist, high confidence.
    renamed_header = ["order_id", "cust_id", "region", "order_date", "revenue"]
    renamed_rows = [
        {**r, "cust_id": r["customer_id"]} for r in rows
    ]
    for r in renamed_rows:
        del r["customer_id"]
    write_csv(OUT_DIR / "day2_renamed.csv", renamed_header, renamed_rows)

    # day3_type_drift: revenue arrives as a comma-formatted string. Type
    # specialist, high confidence.
    type_drift_rows = [{**r, "revenue": comma_format(r["revenue"])} for r in rows]
    write_csv(OUT_DIR / "day3_type_drift.csv", HEADER, type_drift_rows)

    # day4_combo: both faults at once. Proves the heal cycle needs two passes.
    combo_rows = [
        {**r, "cust_id": r["customer_id"], "revenue": comma_format(r["revenue"])}
        for r in rows
    ]
    for r in combo_rows:
        del r["customer_id"]
    write_csv(OUT_DIR / "day4_combo.csv", renamed_header, combo_rows)

    # day5_unfixable: region values abbreviated to single letters (violates
    # the contract's allowed list) AND a new, unrelated `currency` column
    # appears. Two unrelated drifts at once -> ambiguous -> low confidence.
    unfixable_header = HEADER + ["currency"]
    unfixable_rows = [
        {**r, "region": r["region"][0], "currency": "USD"} for r in rows
    ]
    write_csv(OUT_DIR / "day5_unfixable.csv", unfixable_header, unfixable_rows)

    # day6_ambiguous_rename: customer_id -> acct_ref. Same shape as day2 (one
    # missing column, one unexpected column, matching dtype and values) but
    # "acct_ref" is not a lexical variant of "customer_id" the way "cust_id"
    # is — a rename is still the right CLASS of fix (the pattern is
    # unambiguous), but the SPECIFIC mapping is not obvious enough to trust
    # unattended. Rename specialist, but LOW confidence -> human_approval.
    ambiguous_header = ["order_id", "acct_ref", "region", "order_date", "revenue"]
    ambiguous_rows = [{**r, "acct_ref": r["customer_id"]} for r in rows]
    for r in ambiguous_rows:
        del r["customer_id"]
    write_csv(OUT_DIR / "day6_ambiguous_rename.csv", ambiguous_header, ambiguous_rows)


if __name__ == "__main__":
    print(f"generating fault datasets into {OUT_DIR} ...")
    main()
