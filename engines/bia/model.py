"""Budget Impact Analysis — core calculation engine.

Takes validated BIAInputs from the input form and produces a full
BIAResults object with annual net budget impact, cost per patient,
break-even year, cost drivers, and conservative/base/optimistic scenarios.
"""

from __future__ import annotations

from typing import Optional

from .schema import BIAInputs, BIAResults, ScenarioResult, Discounting
from .cost_translator import (
    BAND_RATES,
    NHS_REFERENCE_COSTS,
    calculate_workforce_cost,
    apply_discount,
    get_cost,
)


# ── Constants ──────────────────────────────────────────────────────────

# Complication cost is modelled as a % of total current pathway cost.
# 10% is a standard proxy when no explicit complication tariff is available.
COMPLICATION_COST_FRACTION = 0.10

# Assumed number of staff who need training (used when a specific count
# is not provided).  Conservative default for an acute trust ward.
DEFAULT_TRAINING_HEADCOUNT = 10

# Average hourly rate across all AfC bands — used for valuing time saved
# when the specific role mix of the saved time is unknown.
AVERAGE_HOURLY_RATE = round(
    sum(BAND_RATES.values()) / len(BAND_RATES), 2
)


# ── Helpers ────────────────────────────────────────────────────────────

def _current_pathway_cost(inputs: BIAInputs) -> dict[str, float]:
    """Break the current pathway into named cost components.

    Returns a dict so the caller can both sum the total *and* rank
    individual drivers.
    """
    # Staff time — uses the workforce table from the form
    workforce_cost = calculate_workforce_cost(
        [row.model_dump() for row in inputs.workforce]
    )

    # Outpatient visits: first visit at the higher tariff, the rest at
    # follow-up rate.  If only one visit it counts as a first attendance.
    if inputs.outpatient_visits >= 1:
        outpatient_cost = (
            get_cost("outpatient_first")
            + max(0, inputs.outpatient_visits - 1) * get_cost("outpatient_followup")
        )
    else:
        outpatient_cost = 0.0

    # Bed days (general ward) — admissions * bed-days per admission
    bed_day_cost = (
        inputs.admissions * inputs.bed_days * get_cost("bed_day_general")
    )

    # Tests — costed at the follow-up outpatient rate as a proxy
    test_cost = inputs.tests * get_cost("outpatient_followup")

    # Procedures — costed at one theatre-hour per procedure
    procedure_cost = inputs.procedures * get_cost("theatre_hour")

    # Consumables — directly from input
    consumables_cost = inputs.consumables

    return {
        "Staff time": workforce_cost,
        "Outpatient visits": outpatient_cost,
        "Bed days": bed_day_cost,
        "Tests": test_cost,
        "Procedures": procedure_cost,
        "Consumables": consumables_cost,
    }


def _new_pathway_cost(
    inputs: BIAInputs,
    current_total: float,
    current_components: dict[str, float],
) -> dict[str, float]:
    """Calculate new pathway cost components after intervention.

    The device price is added, then each saving category is subtracted
    from the relevant current-pathway component.
    """
    # Device / intervention price (normalised to per-patient-per-year)
    device_cost = inputs.price  # already per-patient or per-year from form

    # Staff time saved — valued at average hourly rate
    staff_saving = (inputs.staff_time_saved / 60) * AVERAGE_HOURLY_RATE

    # Outpatient / test visit reduction
    visit_saving = (
        current_components["Outpatient visits"] * inputs.visits_reduced / 100
    )

    # Bed-day reduction (length-of-stay reduction in days)
    # Convert days saved into £ using the per-day general ward cost
    bed_day_saving = (
        inputs.admissions
        * inputs.los_reduced
        * get_cost("bed_day_general")
    )

    # Complication reduction — proxy: 10% of current pathway cost
    complication_cost_baseline = current_total * COMPLICATION_COST_FRACTION
    complication_saving = (
        complication_cost_baseline * inputs.complications_reduced / 100
    )

    # Readmission reduction — modelled as a fraction of the bed-day cost
    readmission_saving = (
        current_components["Bed days"] * inputs.readmissions_reduced / 100
    )

    # Follow-up visit reduction
    follow_up_saving = (
        current_components["Outpatient visits"] * inputs.follow_up_reduced / 100
    )

    # Total savings offset against device cost
    total_savings = (
        staff_saving
        + visit_saving
        + bed_day_saving
        + complication_saving
        + readmission_saving
        + follow_up_saving
    )

    return {
        "Device acquisition": device_cost,
        "Staff time saving": -staff_saving,
        "Visit reduction": -visit_saving,
        "Bed-day reduction": -bed_day_saving,
        "Complication reduction": -complication_saving,
        "Readmission reduction": -readmission_saving,
        "Follow-up reduction": -follow_up_saving,
        "Net new pathway": round(device_cost - total_savings, 2),
    }


def _one_off_costs(inputs: BIAInputs) -> float:
    """Return total year-1-only costs (setup + training)."""
    total = inputs.setup_cost

    if inputs.needs_training and inputs.training_hours:
        # Value training time at the average hourly rate
        total += (
            inputs.training_hours
            * AVERAGE_HOURLY_RATE
            * DEFAULT_TRAINING_HEADCOUNT
        )

    return round(total, 2)


def _rank_cost_drivers(
    current_components: dict[str, float],
    new_components: dict[str, float],
) -> list[str]:
    """Return top 3 cost categories ranked by absolute impact.

    Impact = difference between what the category costs on the current
    pathway vs what it contributes (cost or saving) on the new pathway.
    """
    drivers: dict[str, float] = {}

    # Current pathway components are costs being displaced
    for label, value in current_components.items():
        drivers[label] = abs(value)

    # Device acquisition is a new cost
    drivers["Device acquisition"] = abs(new_components["Device acquisition"])

    # Sort descending by magnitude and take top 3
    ranked = sorted(drivers, key=lambda k: drivers[k], reverse=True)
    return ranked[:3]


def _build_scenario(
    inputs: BIAInputs,
    treated: list[int],
    current_cpp: float,
    new_cpp: float,
    one_off: float,
    discount: bool,
    factor: float,
) -> ScenarioResult:
    """Build a single scenario result, applying a scaling factor.

    factor < 1 → optimistic (lower new-pathway cost)
    factor = 1 → base case
    factor > 1 → conservative (higher new-pathway cost)
    """
    adjusted_new_cpp = new_cpp * factor
    impacts: list[float] = []
    cpps: list[float] = []

    for yr_idx, n_treated in enumerate(treated):
        year = yr_idx + 1

        # Net impact = new cost minus current cost, scaled by treated pop
        net = (adjusted_new_cpp - current_cpp) * n_treated

        # Add one-off costs in year 1
        if year == 1:
            net += one_off

        # Apply discounting if switched on
        if discount and year > 1:
            net = apply_discount(net, year)

        impacts.append(round(net, 2))
        cpps.append(round(adjusted_new_cpp, 2))

    return ScenarioResult(
        annual_budget_impact=impacts,
        cost_per_patient=cpps,
        total_treated_patients=treated,
    )


# ── Main entry point ──────────────────────────────────────────────────

def calculate_budget_impact(
    inputs: BIAInputs,
    *,
    _include_scenarios: bool = True,
) -> BIAResults:
    """Run the full Budget Impact Analysis and return structured results.

    Steps
    -----
    1. Derive eligible cohort and treated patients per year from
       catchment size, eligible %, and uptake trajectory.
    2. Cost the current care pathway (workforce + resources).
    3. Cost the new pathway (device price minus savings).
    4. Calculate net budget impact per year (with one-off costs in Y1).
    5. Find the break-even year (cumulative impact <= 0).
    6. Rank top cost drivers by absolute magnitude.
    7. Package base, conservative, and optimistic scenarios (unless
       called internally by calculate_scenarios to avoid recursion).

    Args:
        inputs: Validated BIAInputs from the form / API.
        _include_scenarios: Internal flag. When ``False`` the scenarios
            dict contains only the base case (prevents recursion when
            called from :func:`calculate_scenarios`).

    Returns:
        BIAResults with annual impacts, cost per patient, break-even,
        top drivers, and three scenarios.
    """

    # ── 1. Population ──────────────────────────────────────────────────
    treated = inputs.treated_patients_by_year  # [y1, y2, y3]

    # ── 2. Current pathway cost per patient ────────────────────────────
    current_components = _current_pathway_cost(inputs)
    current_cpp = round(sum(current_components.values()), 2)

    # ── 3. New pathway cost per patient ────────────────────────────────
    new_components = _new_pathway_cost(inputs, current_cpp, current_components)
    new_cpp = new_components["Net new pathway"]

    # ── 4. One-off costs (year 1 only) ─────────────────────────────────
    one_off = _one_off_costs(inputs)

    # ── 5. Should we discount? ─────────────────────────────────────────
    discount = inputs.discounting == Discounting.ON

    # ── 6. Base-case annual net budget impact ──────────────────────────
    base = _build_scenario(
        inputs, treated, current_cpp, new_cpp, one_off, discount, factor=1.0
    )

    # ── 7. Break-even year ─────────────────────────────────────────────
    # Negative cumulative impact means savings exceed costs
    cumulative = 0.0
    break_even: Optional[int] = None
    for yr, impact in enumerate(base.annual_budget_impact, start=1):
        cumulative += impact
        if cumulative <= 0 and break_even is None:
            break_even = yr

    # ── 8. Top cost drivers ────────────────────────────────────────────
    top_drivers = _rank_cost_drivers(current_components, new_components)

    # ── 9. Scenario analysis ───────────────────────────────────────────
    if _include_scenarios:
        scenarios = calculate_scenarios(inputs)
        scenario_results = {
            name: ScenarioResult(
                annual_budget_impact=result.annual_budget_impact,
                cost_per_patient=result.cost_per_patient,
                total_treated_patients=result.total_treated_patients,
            )
            for name, result in scenarios.items()
        }
    else:
        # Inner call from calculate_scenarios — only include base case
        scenario_results = {"base": base}

    return BIAResults(
        annual_budget_impact=base.annual_budget_impact,
        cost_per_patient=base.cost_per_patient,
        total_treated_patients=treated,
        break_even_year=break_even,
        top_cost_drivers=top_drivers,
        scenarios=scenario_results,
    )


# ── Scenario analysis ─────────────────────────────────────────────────

def create_scenario_variant(
    inputs: BIAInputs,
    uptake_mult: float,
    price_mult: float,
    savings_mult: float,
) -> BIAInputs:
    """Return a copy of *inputs* with uptake, price, and savings scaled.

    This avoids code duplication when building conservative / optimistic
    variants.  All three multipliers are applied independently:

    - **uptake_mult**: scales uptake_y1 / y2 / y3 (clamped to 0–100 %).
    - **price_mult**: scales the device price.
    - **savings_mult**: scales all resource-saving fields (staff_time_saved,
      visits_reduced, complications_reduced, readmissions_reduced,
      los_reduced, follow_up_reduced).  Percentage fields are clamped
      to 0–100 %.

    Args:
        inputs:       Original validated BIAInputs.
        uptake_mult:  Multiplier for uptake (e.g. 0.8 = 20 % lower).
        price_mult:   Multiplier for device price (e.g. 1.15 = 15 % higher).
        savings_mult: Multiplier for savings (e.g. 0.7 = 30 % lower).

    Returns:
        A new BIAInputs instance with the adjusted values.
    """
    data = inputs.model_dump()

    # Scale uptake (clamp to 0–100)
    for field in ("uptake_y1", "uptake_y2", "uptake_y3"):
        data[field] = min(100.0, max(0.0, data[field] * uptake_mult))

    # Scale device price
    data["price"] = data["price"] * price_mult

    # Scale all savings fields
    for field in ("staff_time_saved", "los_reduced"):
        data[field] = max(0.0, data[field] * savings_mult)

    for field in (
        "visits_reduced",
        "complications_reduced",
        "readmissions_reduced",
        "follow_up_reduced",
    ):
        data[field] = min(100.0, max(0.0, data[field] * savings_mult))

    return BIAInputs(**data)


def calculate_scenarios(inputs: BIAInputs) -> dict[str, BIAResults]:
    """Run the BIA under three scenarios and return all three result sets.

    Scenarios
    ---------
    **Conservative** — assumes adoption is slower, the device is more
    expensive, and realised savings are lower than expected:

    - Uptake reduced by 20 % (× 0.8)
    - Device price increased by 15 % (× 1.15)
    - All savings reduced by 30 % (× 0.7)

    **Base** — uses the client-supplied inputs as-is.  This is the
    reference case against which the other two are compared.

    **Optimistic** — assumes faster adoption, a negotiated price
    discount, and better-than-expected efficiency gains:

    - Uptake increased by 20 % (× 1.2, clamped to 100 %)
    - Device price reduced by 10 % (× 0.9)
    - All savings increased by 20 % (× 1.2, percentages clamped to 100 %)

    Args:
        inputs: Validated BIAInputs from the form / API.

    Returns:
        Dict with keys ``"conservative"``, ``"base"``, ``"optimistic"``,
        each mapping to a full BIAResults object.
    """
    conservative_inputs = create_scenario_variant(
        inputs,
        uptake_mult=0.8,
        price_mult=1.15,
        savings_mult=0.7,
    )

    optimistic_inputs = create_scenario_variant(
        inputs,
        uptake_mult=1.2,
        price_mult=0.9,
        savings_mult=1.2,
    )

    return {
        "conservative": calculate_budget_impact(
            conservative_inputs, _include_scenarios=False
        ),
        "base": calculate_budget_impact(inputs, _include_scenarios=False),
        "optimistic": calculate_budget_impact(
            optimistic_inputs, _include_scenarios=False
        ),
    }
