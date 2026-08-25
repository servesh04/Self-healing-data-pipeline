"""pipeline.runner.run_pipeline — the deterministic load -> apply_mapping ->
transform -> validate pipeline, exercised against all 6 real fault datasets
in data/sources/. Pandera is the oracle (ARCHITECTURE.md, "the pipeline
(keep it dumb)"): `result.passed` is exactly what route_after_run and
route_after_rerun key off of, so these tests are the same ground truth
those routers trust, run against the real files rather than synthetic
stand-ins.

Every case pairs a real dataset with either the empty seed mapping (proving
the drift is real and actually fails validation) or the mapping a correct
heal would produce (proving that specific mapping is what actually fixes
it — not just that some patch was applied).
"""

import pytest
from pipeline.mapping import MappingPatch
from pipeline.runner import run_pipeline
from pydantic import ValidationError

pytestmark = pytest.mark.integration

CASES = [
    pytest.param("day1_clean", MappingPatch(), True, id="day1-clean-no-mapping-needed"),
    pytest.param("day2_renamed", MappingPatch(), False, id="day2-rename-drift-fails-unpatched"),
    pytest.param(
        "day2_renamed",
        MappingPatch(renames={"cust_id": "customer_id"}),
        True,
        id="day2-rename-drift-heals-with-the-correct-rename",
    ),
    pytest.param("day3_type_drift", MappingPatch(), False, id="day3-type-drift-fails-unpatched"),
    pytest.param(
        "day3_type_drift",
        MappingPatch(casts={"revenue": "strip_commas_to_float"}),
        True,
        id="day3-type-drift-heals-with-the-correct-cast",
    ),
    pytest.param("day4_combo", MappingPatch(), False, id="day4-combo-fails-unpatched"),
    pytest.param(
        "day4_combo",
        MappingPatch(renames={"cust_id": "customer_id"}),
        False,
        id="day4-combo-rename-alone-is-not-enough-type-drift-remains",
    ),
    pytest.param(
        "day4_combo",
        MappingPatch(renames={"cust_id": "customer_id"}, casts={"revenue": "strip_commas_to_float"}),
        True,
        id="day4-combo-heals-once-both-patches-are-merged",
    ),
    pytest.param("day5_unfixable", MappingPatch(), False, id="day5-unfixable-fails-unpatched"),
    pytest.param(
        "day5_unfixable",
        MappingPatch(drops=["currency"]),
        False,
        id="day5-unfixable-still-fails-no-registered-mapping-op-can-fix-it",
    ),
    pytest.param("day6_ambiguous_rename", MappingPatch(), False, id="day6-ambiguous-rename-fails-unpatched"),
    pytest.param(
        "day6_ambiguous_rename",
        MappingPatch(renames={"acct_ref": "customer_id"}),
        True,
        id="day6-ambiguous-rename-heals-with-the-correct-rename",
    ),
]


@pytest.mark.parametrize("dataset, mapping, expected_passed", CASES)
def test_run_pipeline_against_every_fault_dataset(fault_datasets, contract, dataset, mapping, expected_passed):
    result = run_pipeline(str(fault_datasets[dataset]), mapping, contract)

    assert result.passed is expected_passed
    assert result.rows_in == 50


class TestFailureSignalCharacteristics:
    """The SPECIFIC shape of a failure, not just pass/fail -- this is what
    diff_contract.py (and, upstream of it, diagnose) actually reads to
    classify drift. A pass/fail boolean alone wouldn't catch a change to
    which checks fire or how many.
    """

    def test_day4_combo_surfaces_both_independent_drift_signals_at_once(self, fault_datasets, contract):
        """diff_contract.py's docstring states this exact fact and relies
        on it narrowing correctly between heal attempts: an empty mapping
        against day4_combo produces 4 failure signals -- 2 for the rename
        (missing customer_id, unexpected cust_id) and 2 for the type drift
        (the dtype mismatch itself, plus the cascading TypeError from the
        `>= 0` check running on a still-string value). Both drift classes
        must be visible simultaneously, not just the first one Pandera
        happens to report.
        """
        result = run_pipeline(str(fault_datasets["day4_combo"]), MappingPatch(), contract)
        checks = {f.check for f in result.failures}

        assert "column_in_schema" in checks  # cust_id, unexpected
        assert "column_in_dataframe" in checks  # customer_id, missing
        assert any(c.startswith("dtype(") for c in checks)  # revenue as str
        assert len(result.failures) == 4

    def test_day3_type_drift_cascading_typeerror_is_not_filtered_here(self, fault_datasets, contract):
        """run_pipeline (and pipeline.contract.validate underneath it) is
        the raw oracle -- it reports every failure Pandera actually raises,
        including the second, uninformative failure a dtype mismatch
        cascades into (a numeric check comparing against a still-string
        value). Filtering that cascade artifact out is diff_contract's
        job, deliberately done one layer up (see diff_contract.py's
        _is_dtype_cascade_artifact) -- run_pipeline itself must keep
        reporting it raw, or diff_contract would have nothing to filter.
        """
        result = run_pipeline(str(fault_datasets["day3_type_drift"]), MappingPatch(), contract)
        cascade_artifacts = [f for f in result.failures if "TypeError(" in " ".join(f.example_values)]

        assert len(cascade_artifacts) == 1
        assert cascade_artifacts[0].check == "greater_than_or_equal_to(0)"

    def test_day5_unfixable_region_violation_survives_dropping_the_extra_column(
        self, fault_datasets, contract
    ):
        """day5 has TWO problems (an undeclared `currency` column, and
        region values abbreviated to single letters), but only one of them
        is expressible as a mapping op at all: dropping `currency` is
        exactly what a nullability/structural specialist could propose,
        and it genuinely removes that failure -- but the abbreviated
        region values still violate the contract's `allowed` check
        afterwards, on all 50 rows, because no registered cast or
        null_policy op can expand "N" into "North". This is what makes
        day5 "unfixable" a fact about the available mapping ops, not a
        story about diagnose or a specialist trying insufficiently hard.
        """
        result = run_pipeline(
            str(fault_datasets["day5_unfixable"]), MappingPatch(drops=["currency"]), contract
        )

        assert result.passed is False
        assert len(result.failures) == 1
        assert result.failures[0].column == "region"
        assert result.failures[0].failure_count == 50

    def test_as_strings_produces_one_readable_line_per_failure(self, fault_datasets, contract):
        """as_strings() is what ends up in HealState.assertion_failures and
        gets shown directly in the dashboard's AssertionBars component --
        it must stay one string per FailureCase, in the same order.
        """
        result = run_pipeline(str(fault_datasets["day2_renamed"]), MappingPatch(), contract)
        strings = result.as_strings()

        assert len(strings) == len(result.failures)
        assert all(isinstance(s, str) and s for s in strings)


class TestMappingPatchValidation:
    """pipeline.mapping.MappingPatch is the ONLY thing standing between an
    agent-proposed patch and Postgres (apply_patch persists through this
    model, no exceptions) -- these are what "the agent proposes config,
    never code" actually resolves to at runtime: a cast or null_policy
    value is always the NAME of a registered function, checked against
    the same registry apply_mapping resolves against, never an arbitrary
    string.
    """

    def test_rejects_a_cast_op_that_is_not_in_the_registry(self):
        with pytest.raises(ValidationError):
            MappingPatch(casts={"revenue": "eval_arbitrary_python"})

    def test_rejects_a_null_policy_op_that_is_not_in_the_registry(self):
        with pytest.raises(ValidationError):
            MappingPatch(null_policy={"region": "not_a_real_op"})

    def test_rejects_a_key_outside_the_four_declared_sections(self):
        """extra="forbid" is the actual enforcement of "a specialist must
        not emit keys outside its section" at the MappingPatch level --
        this is the fallback check once a patch is already merged; the
        per-specialist schemas (graph/schemas.py) catch the same class of
        leak one node earlier, tested separately in test_graph_structure.py.
        """
        with pytest.raises(ValidationError):
            MappingPatch.model_validate({"renames": {}, "made_up_section": {"x": "y"}})
