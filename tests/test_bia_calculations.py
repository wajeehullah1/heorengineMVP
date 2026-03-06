"""Tests for the BIA calculation engine (engines/bia/model.py).

Covers: realistic end-to-end calculation, edge cases, scenario generation,
cost-driver ranking, and break-even logic.
"""

import pytest

from engines.bia.schema import BIAInputs
from engines.bia.model import (
    AVERAGE_HOURLY_RATE,
    COMPLICATION_COST_FRACTION,
    DEFAULT_TRAINING_HEADCOUNT,
    calculate_budget_impact,
    calculate_scenarios,
    create_scenario_variant,
    _current_pathway_cost,
    _new_pathway_cost,
)
from engines.bia.cost_translator import BAND_RATES, get_cost


# ── Helpers ────────────────────────────────────────────────────────────

def _make_inputs(**overrides) -> BIAInputs:
    """Return a minimal valid BIAInputs with sensible defaults.

    Any keyword argument overrides the corresponding field.
    """
    defaults = dict(
        setting="Acute NHS Trust",
        model_year=2026,
        forecast_years=3,
        funding_source="Trust operational budget",
        catchment_size=1000,
        eligible_pct=10.0,  # 100 eligible patients
        uptake_y1=30,
        uptake_y2=50,
        uptake_y3=70,
        workforce=[
            {"role": "Band 5 (Staff Nurse)", "minutes": 20, "frequency": "per patient"},
        ],
        outpatient_visits=2,
        tests=1,
        admissions=1,
        bed_days=2,
        price=500.0,
        staff_time_saved=15.0,
        follow_up_reduced=20.0,
    )
    defaults.update(overrides)
    return BIAInputs(**defaults)


# ====================================================================
# 1. Realistic end-to-end calculation
# ====================================================================

class TestRealisticScenario:
    """Device at £500/patient, saves 15 min Band 5 time, 20% fewer
    follow-ups.  100 eligible patients, 30/50/70% uptake."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.inputs = _make_inputs()
        self.result = calculate_budget_impact(
            self.inputs, _include_scenarios=False
        )

    def test_eligible_cohort(self):
        assert self.inputs.eligible_patients == 100, (
            f"Expected 100 eligible (1000 * 10%), got {self.inputs.eligible_patients}"
        )

    def test_treated_patients(self):
        assert self.result.total_treated_patients == [30, 50, 70], (
            f"Expected [30, 50, 70], got {self.result.total_treated_patients}"
        )

    def test_net_impact_matches_manual(self):
        """Step-by-step manual calculation to verify the engine.

        The engine rounds workforce cost to 2 dp via
        calculate_workforce_cost() before summing into the current
        pathway total, so we replicate that here.
        """
        # Current pathway cost per patient
        # (round workforce cost to 2 dp, matching the engine)
        workforce_cost = round(BAND_RATES["Band 5 (Staff Nurse)"] * 20 / 60, 2)
        outpatient_cost = get_cost("outpatient_first") + 1 * get_cost("outpatient_followup")  # 120 + 85
        bed_day_cost = 1 * 2 * get_cost("bed_day_general")  # 800
        test_cost = 1 * get_cost("outpatient_followup")  # 85
        procedure_cost = 0.0
        consumables_cost = 0.0
        current_cpp = round(
            workforce_cost + outpatient_cost + bed_day_cost
            + test_cost + procedure_cost + consumables_cost,
            2,
        )

        # New pathway cost per patient
        device = 500.0
        staff_saving = (15 / 60) * AVERAGE_HOURLY_RATE
        visit_saving = outpatient_cost * 0.0  # visits_reduced defaults to 0
        bed_day_saving = 0.0  # los_reduced defaults to 0
        complication_saving = current_cpp * COMPLICATION_COST_FRACTION * 0.0  # complications_reduced = 0
        readmission_saving = bed_day_cost * 0.0  # readmissions_reduced = 0
        follow_up_saving = outpatient_cost * 20 / 100  # follow_up_reduced = 20%

        total_savings = (
            staff_saving + visit_saving + bed_day_saving
            + complication_saving + readmission_saving + follow_up_saving
        )
        new_cpp = round(device - total_savings, 2)

        # No one-off costs (setup_cost=0, needs_training=False)
        one_off = 0.0

        # Year 1: 30 patients
        expected_y1 = round((new_cpp - current_cpp) * 30 + one_off, 2)
        # Year 2: 50 patients
        expected_y2 = round((new_cpp - current_cpp) * 50, 2)
        # Year 3: 70 patients
        expected_y3 = round((new_cpp - current_cpp) * 70, 2)

        actual = self.result.annual_budget_impact
        assert actual[0] == pytest.approx(expected_y1, abs=0.01), (
            f"Year 1 mismatch: expected £{expected_y1:.2f}, got £{actual[0]:.2f}"
        )
        assert actual[1] == pytest.approx(expected_y2, abs=0.01), (
            f"Year 2 mismatch: expected £{expected_y2:.2f}, got £{actual[1]:.2f}"
        )
        assert actual[2] == pytest.approx(expected_y3, abs=0.01), (
            f"Year 3 mismatch: expected £{expected_y3:.2f}, got £{actual[2]:.2f}"
        )

    def test_has_three_years_of_output(self):
        assert len(self.result.annual_budget_impact) == 3, (
            "Expected 3 years of budget impact"
        )
        assert len(self.result.cost_per_patient) == 3, (
            "Expected 3 years of cost-per-patient"
        )

    def test_top_cost_drivers_not_empty(self):
        assert len(self.result.top_cost_drivers) == 3, (
            f"Expected 3 top cost drivers, got {len(self.result.top_cost_drivers)}"
        )


# ====================================================================
# 2. Edge cases
# ====================================================================

class TestEdgeCases:

    def test_zero_uptake_gives_zero_impact(self):
        """If nobody adopts the device, budget impact should be zero."""
        inputs = _make_inputs(uptake_y1=0, uptake_y2=0, uptake_y3=0)
        result = calculate_budget_impact(inputs, _include_scenarios=False)

        for yr, impact in enumerate(result.annual_budget_impact, 1):
            assert impact == 0.0, (
                f"Year {yr} impact should be £0 with zero uptake, got £{impact:.2f}"
            )

    def test_expensive_device_negative_savings(self):
        """A very expensive device with no savings should produce positive
        (i.e. extra cost) budget impact each year."""
        inputs = _make_inputs(
            price=50000.0,
            staff_time_saved=0,
            follow_up_reduced=0,
            visits_reduced=0,
            complications_reduced=0,
            readmissions_reduced=0,
            los_reduced=0,
        )
        result = calculate_budget_impact(inputs, _include_scenarios=False)

        for yr, impact in enumerate(result.annual_budget_impact, 1):
            assert impact > 0, (
                f"Year {yr}: expensive device with no savings should increase "
                f"budget (positive impact), got £{impact:.2f}"
            )

    def test_full_uptake_from_year_one(self):
        """100% uptake in all years means treated = eligible every year."""
        inputs = _make_inputs(uptake_y1=100, uptake_y2=100, uptake_y3=100)
        result = calculate_budget_impact(inputs, _include_scenarios=False)

        eligible = inputs.eligible_patients
        assert result.total_treated_patients == [eligible, eligible, eligible], (
            f"With 100% uptake, treated should equal eligible ({eligible}) "
            f"every year, got {result.total_treated_patients}"
        )

    def test_no_savings_only_device_cost(self):
        """When there are zero savings, the new pathway cost equals the
        device price and the net impact equals (device - current) * n."""
        inputs = _make_inputs(
            price=200.0,
            staff_time_saved=0,
            follow_up_reduced=0,
            visits_reduced=0,
            complications_reduced=0,
            readmissions_reduced=0,
            los_reduced=0,
        )
        result = calculate_budget_impact(inputs, _include_scenarios=False)

        # New pathway cost = device price (no savings subtracted)
        for cpp in result.cost_per_patient:
            assert cpp == pytest.approx(200.0, abs=0.01), (
                f"With zero savings, cost/patient should equal device price "
                f"(£200), got £{cpp:.2f}"
            )


# ====================================================================
# 3. Scenario generation
# ====================================================================

class TestScenarioGeneration:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.inputs = _make_inputs()
        self.scenarios = calculate_scenarios(self.inputs)

    def test_all_three_scenarios_present(self):
        assert set(self.scenarios.keys()) == {"conservative", "base", "optimistic"}, (
            f"Expected three scenario keys, got {set(self.scenarios.keys())}"
        )

    def test_conservative_lower_uptake(self):
        """Conservative scenario should treat fewer patients than base."""
        cons = self.scenarios["conservative"].total_treated_patients
        base = self.scenarios["base"].total_treated_patients

        for yr in range(3):
            assert cons[yr] <= base[yr], (
                f"Year {yr+1}: conservative treated ({cons[yr]}) should be "
                f"<= base ({base[yr]})"
            )

    def test_optimistic_higher_uptake(self):
        """Optimistic scenario should treat more patients than base."""
        opt = self.scenarios["optimistic"].total_treated_patients
        base = self.scenarios["base"].total_treated_patients

        for yr in range(3):
            assert opt[yr] >= base[yr], (
                f"Year {yr+1}: optimistic treated ({opt[yr]}) should be "
                f">= base ({base[yr]})"
            )

    def test_optimistic_has_larger_savings(self):
        """Optimistic net impact should be more negative (= bigger savings)
        than conservative, when the device already saves money."""
        cons = self.scenarios["conservative"].annual_budget_impact
        opt = self.scenarios["optimistic"].annual_budget_impact

        for yr in range(3):
            assert opt[yr] <= cons[yr], (
                f"Year {yr+1}: optimistic impact (£{opt[yr]:,.2f}) should be "
                f"<= conservative (£{cons[yr]:,.2f})"
            )

    def test_all_scenarios_same_eligible_cohort(self):
        """Eligible cohort is derived from catchment and eligible_pct,
        which are NOT modified by scenario variants."""
        cons_variant = create_scenario_variant(
            self.inputs, uptake_mult=0.8, price_mult=1.15, savings_mult=0.7
        )
        opt_variant = create_scenario_variant(
            self.inputs, uptake_mult=1.2, price_mult=0.9, savings_mult=1.2
        )

        assert cons_variant.eligible_patients == self.inputs.eligible_patients, (
            "Conservative eligible cohort should match base"
        )
        assert opt_variant.eligible_patients == self.inputs.eligible_patients, (
            "Optimistic eligible cohort should match base"
        )

    def test_variant_uptake_clamped_to_100(self):
        """Scaling uptake of 90% by 1.2x should clamp at 100, not 108."""
        inputs = _make_inputs(uptake_y1=90)
        variant = create_scenario_variant(
            inputs, uptake_mult=1.2, price_mult=1.0, savings_mult=1.0
        )
        assert variant.uptake_y1 == 100.0, (
            f"Uptake should be clamped to 100%, got {variant.uptake_y1}%"
        )


# ====================================================================
# 4. Cost driver identification
# ====================================================================

class TestCostDrivers:

    def test_device_price_dominates(self):
        """When device is very expensive and other costs are minimal,
        'Device acquisition' should be the top driver."""
        inputs = _make_inputs(
            price=50000.0,
            workforce=[
                {"role": "Admin/Clerical", "minutes": 5, "frequency": "per patient"},
            ],
            outpatient_visits=0,
            admissions=0,
            bed_days=0,
            tests=0,
        )
        result = calculate_budget_impact(inputs, _include_scenarios=False)

        assert result.top_cost_drivers[0] == "Device acquisition", (
            f"Expected 'Device acquisition' as top driver when device is "
            f"£50,000, got '{result.top_cost_drivers[0]}'"
        )

    def test_bed_days_dominate(self):
        """When bed-day costs are very high, 'Bed days' should rank first."""
        inputs = _make_inputs(
            price=10.0,
            workforce=[
                {"role": "Admin/Clerical", "minutes": 5, "frequency": "per patient"},
            ],
            outpatient_visits=0,
            admissions=5,
            bed_days=10,  # 5 * 10 * £400 = £20,000
            tests=0,
        )
        result = calculate_budget_impact(inputs, _include_scenarios=False)

        assert result.top_cost_drivers[0] == "Bed days", (
            f"Expected 'Bed days' as top driver with 5 admissions * 10 days, "
            f"got '{result.top_cost_drivers[0]}'"
        )

    def test_top_three_returned(self):
        """Should always return exactly 3 cost drivers."""
        inputs = _make_inputs()
        result = calculate_budget_impact(inputs, _include_scenarios=False)

        assert len(result.top_cost_drivers) == 3, (
            f"Expected exactly 3 top cost drivers, got {len(result.top_cost_drivers)}"
        )

    def test_drivers_are_strings(self):
        inputs = _make_inputs()
        result = calculate_budget_impact(inputs, _include_scenarios=False)

        for driver in result.top_cost_drivers:
            assert isinstance(driver, str), (
                f"Cost driver should be a string, got {type(driver)}"
            )


# ====================================================================
# 5. Break-even calculation
# ====================================================================

class TestBreakEven:

    def test_immediate_break_even(self):
        """A cheap device with large savings should break even in year 1."""
        inputs = _make_inputs(
            price=10.0,
            staff_time_saved=60,      # saves 1 full hour
            follow_up_reduced=50.0,
            visits_reduced=50.0,
            los_reduced=1.0,
            uptake_y1=50,
        )
        result = calculate_budget_impact(inputs, _include_scenarios=False)

        assert result.break_even_year == 1, (
            f"Cheap device with big savings should break even in year 1, "
            f"got year {result.break_even_year}. "
            f"Impacts: {result.annual_budget_impact}"
        )

    def test_never_breaks_even(self):
        """A very expensive device with no savings should never break even."""
        inputs = _make_inputs(
            price=100000.0,
            staff_time_saved=0,
            follow_up_reduced=0,
            visits_reduced=0,
            complications_reduced=0,
            readmissions_reduced=0,
            los_reduced=0,
        )
        result = calculate_budget_impact(inputs, _include_scenarios=False)

        assert result.break_even_year is None, (
            f"Expensive device with zero savings should never break even, "
            f"got year {result.break_even_year}. "
            f"Impacts: {result.annual_budget_impact}"
        )

    def test_break_even_year_2(self):
        """Engineer a scenario where year 1 is net-positive cost (because
        of a large setup cost) but cumulative flips negative in year 2.

        Strategy: use a small eligible population (10 patients) with a
        device that saves modestly per patient, but add a very large
        one-off setup cost that dominates year 1.
        """
        inputs = _make_inputs(
            catchment_size=100,
            eligible_pct=10.0,         # 10 eligible patients
            uptake_y1=50,              # 5 treated in year 1
            uptake_y2=80,              # 8 treated in year 2
            uptake_y3=80,              # 8 treated in year 3
            price=100.0,              # cheap device
            setup_cost=6000.0,        # big one-off, dwarfs 5 patients' savings
            staff_time_saved=15,
            follow_up_reduced=20.0,
            visits_reduced=0,
            complications_reduced=0,
            readmissions_reduced=0,
            los_reduced=0,
        )
        result = calculate_budget_impact(inputs, _include_scenarios=False)

        # Year 1 should be positive (£6k setup > small per-patient savings)
        assert result.annual_budget_impact[0] > 0, (
            f"Year 1 should be net cost due to £6k setup with only 5 patients, "
            f"got £{result.annual_budget_impact[0]:,.2f}"
        )
        # Should eventually break even (year 2 or 3)
        assert result.break_even_year is not None, (
            f"Should eventually break even. Impacts: {result.annual_budget_impact}"
        )
        assert result.break_even_year >= 2, (
            f"Should not break even in year 1 due to setup cost, "
            f"got year {result.break_even_year}"
        )
