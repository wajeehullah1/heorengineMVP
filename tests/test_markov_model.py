"""Markov cost-effectiveness model tests — end-to-end via the Python→R bridge.

Run with:  pytest tests/test_markov_model.py -v -s
"""

import pytest

from engines.markov.runner import (
    calculate_icer,
    check_r_installed,
    run_markov_model,
    validate_markov_params,
)


# ── Skip the entire module if R is not installed ──────────────────────
def pytest_configure():
    if not check_r_installed():
        pytest.skip(
            "R not installed — skipping Markov model tests.\n"
            "Install R from https://cloud.r-project.org/",
            allow_module_level=True,
        )


# ── Helpers ───────────────────────────────────────────────────────────

def _print_results(results: dict) -> None:
    """Pretty-print Markov model output."""
    std = results["standard_care"]
    trt = results["treatment"]
    inc = results["incremental"]
    prm = results["parameters"]

    print(f"\n  Time horizon: {prm['time_horizon']} years  "
          f"({prm['n_cycles']} cycles, length {prm['cycle_length']})")
    print(f"  Discount rate: {prm['discount_rate']}")
    print(f"  ── Standard care ──")
    print(f"     Total cost:  £{std['total_cost']:,.2f}")
    print(f"     Total QALYs: {std['total_qalys']:.4f}")
    print(f"  ── Treatment ──")
    print(f"     Total cost:  £{trt['total_cost']:,.2f}")
    print(f"     Total QALYs: {trt['total_qalys']:.4f}")
    print(f"  ── Incremental ──")
    print(f"     Cost:  £{inc['cost']:,.2f}")
    print(f"     QALYs: {inc['qalys']:.4f}")
    icer_display = inc['icer'] if isinstance(inc['icer'], str) else f"£{inc['icer']:,.2f}"
    print(f"     ICER:  {icer_display}")
    print(f"  Interpretation: {results['interpretation']}")


# ====================================================================
# 1. Cost-effective scenario
# ====================================================================

class TestCostEffective:
    """Treatment cuts mortality in half with modest cost increase."""

    PARAMS = {
        "time_horizon": 5,
        "prob_death_standard": 0.10,
        "cost_standard": 5000,
        "utility_standard": 0.7,
        "prob_death_treatment": 0.05,
        "cost_treatment": 6000,
        "cost_treatment_initial": 10000,
        "utility_treatment": 0.85,
    }

    @pytest.fixture()
    def results(self):
        return run_markov_model(self.PARAMS)

    def test_treatment_costs_more(self, results):
        """Treatment arm should have higher total cost (upfront + ongoing)."""
        assert results["treatment"]["total_cost"] > results["standard_care"]["total_cost"]
        _print_results(results)

    def test_treatment_gains_qalys(self, results):
        """Treatment arm should produce more QALYs (lower mortality + higher utility)."""
        assert results["treatment"]["total_qalys"] > results["standard_care"]["total_qalys"]

    def test_icer_is_positive(self, results):
        """ICER should be positive (more costly, more effective)."""
        assert isinstance(results["incremental"]["icer"], (int, float))
        assert results["incremental"]["icer"] > 0

    def test_icer_below_nice_threshold(self, results):
        """ICER should fall below £35,000/QALY."""
        assert results["incremental"]["icer"] < 35000


# ====================================================================
# 2. NOT cost-effective scenario
# ====================================================================

class TestNotCostEffective:
    """Very expensive treatment with minimal QALY gain."""

    PARAMS = {
        "time_horizon": 5,
        "prob_death_standard": 0.05,
        "cost_standard": 5000,
        "utility_standard": 0.70,
        "prob_death_treatment": 0.04,
        "cost_treatment": 12000,
        "cost_treatment_initial": 50000,
        "utility_treatment": 0.75,
    }

    @pytest.fixture()
    def results(self):
        return run_markov_model(self.PARAMS)

    def test_icer_above_threshold(self, results):
        """ICER should exceed £35,000/QALY given the high upfront cost."""
        assert isinstance(results["incremental"]["icer"], (int, float))
        assert results["incremental"]["icer"] > 35000
        _print_results(results)

    def test_interpretation_not_cost_effective(self, results):
        """R should label this 'Not cost-effective'."""
        assert results["interpretation"] == "Not cost-effective"


# ====================================================================
# 3. Edge cases
# ====================================================================

class TestEdgeCases:

    def test_identical_arms(self):
        """When both arms are identical the ICER is undefined."""
        results = run_markov_model({
            "time_horizon": 5,
            "prob_death_standard": 0.05,
            "cost_standard": 5000,
            "utility_standard": 0.7,
            "prob_death_treatment": 0.05,
            "cost_treatment": 5000,
            "utility_treatment": 0.7,
        })
        # R returns "NA" as a string when QALYs are equal
        assert results["incremental"]["icer"] == "NA"
        assert results["incremental"]["qalys"] == 0.0
        _print_results(results)

    def test_treatment_dominates(self):
        """Treatment is cheaper AND more effective — ICER should be negative."""
        results = run_markov_model({
            "time_horizon": 5,
            "prob_death_standard": 0.10,
            "cost_standard": 8000,
            "utility_standard": 0.6,
            "prob_death_treatment": 0.03,
            "cost_treatment": 4000,
            "utility_treatment": 0.85,
        })
        assert results["incremental"]["cost"] < 0, "Treatment should be cheaper"
        assert results["incremental"]["qalys"] > 0, "Treatment should gain QALYs"
        assert isinstance(results["incremental"]["icer"], (int, float))
        assert results["incremental"]["icer"] < 0, "ICER should be negative (dominant)"
        assert results["interpretation"] == "Dominant (less costly, more effective)"
        _print_results(results)

    def test_treatment_dominated(self):
        """Treatment is more expensive AND less effective — dominated."""
        results = run_markov_model({
            "time_horizon": 5,
            "prob_death_standard": 0.03,
            "cost_standard": 4000,
            "utility_standard": 0.85,
            "prob_death_treatment": 0.10,
            "cost_treatment": 8000,
            "utility_treatment": 0.6,
        })
        assert results["incremental"]["cost"] > 0, "Treatment should cost more"
        assert results["incremental"]["qalys"] < 0, "Treatment should lose QALYs"
        assert results["interpretation"] == "Dominated (more costly, less effective)"
        _print_results(results)


# ====================================================================
# 4. Python-side calculate_icer
# ====================================================================

class TestCalculateIcer:

    def test_basic_icer(self):
        """Pure-Python ICER should match manual calculation."""
        icer = calculate_icer(
            standard_cost=20000, standard_qalys=3.0,
            treatment_cost=35000, treatment_qalys=4.0,
        )
        assert icer == pytest.approx(15000.0)
        print(f"\n  ICER = £{icer:,.2f}/QALY")

    def test_zero_qaly_difference_raises(self):
        """ZeroDivisionError when QALYs are identical."""
        with pytest.raises(ZeroDivisionError):
            calculate_icer(10000, 2.0, 20000, 2.0)
        print("\n  Correctly raised ZeroDivisionError for zero QALY diff")


# ====================================================================
# 5. Input validation
# ====================================================================

class TestValidation:

    def test_missing_required_param(self):
        with pytest.raises(ValueError, match="Missing required parameters"):
            run_markov_model({"prob_death_standard": 0.05})
        print("\n  Correctly rejected missing parameters")

    def test_probability_out_of_range(self):
        with pytest.raises(ValueError, match="must be between 0 and 1"):
            run_markov_model({
                "prob_death_standard": 1.5,
                "cost_standard": 5000, "utility_standard": 0.7,
                "prob_death_treatment": 0.03,
                "cost_treatment": 8000, "utility_treatment": 0.85,
            })
        print("\n  Correctly rejected probability > 1")

    def test_negative_cost(self):
        with pytest.raises(ValueError, match="must be non-negative"):
            run_markov_model({
                "prob_death_standard": 0.05,
                "cost_standard": -500, "utility_standard": 0.7,
                "prob_death_treatment": 0.03,
                "cost_treatment": 8000, "utility_treatment": 0.85,
            })
        print("\n  Correctly rejected negative cost")
