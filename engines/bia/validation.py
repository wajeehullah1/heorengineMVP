"""Clinical-sense validation, missing-input suggestions, and confidence scoring.

These functions do NOT reject inputs — they return advisory warnings and
suggestions so the front-end can prompt the user before running the BIA.
"""

from __future__ import annotations

from .schema import BIAInputs


def validate_clinical_sense(inputs: BIAInputs) -> list[str]:
    """Return warnings for input values that look clinically unusual.

    None of these are hard errors.  They flag values that may indicate
    a data-entry mistake or an incorrect unit so the user can double-check
    before running the model.

    Checks
    ------
    - Uptake decreasing over time (Y2 < Y1 or Y3 < Y2).
    - Any savings percentage > 80 % (suspiciously optimistic).
    - Device price < £10 or > £100,000 (possible unit error).
    - Eligible cohort > catchment size (mathematically impossible when
      eligible_pct > 100, but also flagged if the rounded count exceeds
      the catchment due to rounding).
    - Staff time saved exceeds total current pathway time.
    """
    warnings: list[str] = []

    # ── Uptake trajectory ──────────────────────────────────────────────
    if inputs.uptake_y2 < inputs.uptake_y1:
        warnings.append(
            f"Uptake drops from {inputs.uptake_y1}% (Y1) to "
            f"{inputs.uptake_y2}% (Y2) — is this intentional?"
        )
    if inputs.uptake_y3 < inputs.uptake_y2:
        warnings.append(
            f"Uptake drops from {inputs.uptake_y2}% (Y2) to "
            f"{inputs.uptake_y3}% (Y3) — is this intentional?"
        )

    # ── Savings percentages ────────────────────────────────────────────
    savings_fields = {
        "Visits reduced": inputs.visits_reduced,
        "Complications reduced": inputs.complications_reduced,
        "Readmissions reduced": inputs.readmissions_reduced,
        "Follow-up visits reduced": inputs.follow_up_reduced,
    }
    for label, value in savings_fields.items():
        if value > 80:
            warnings.append(
                f"{label} is {value}% — reductions above 80% are unusual. "
                f"Please verify this is evidence-based."
            )

    # ── Device price ───────────────────────────────────────────────────
    if inputs.price < 10:
        warnings.append(
            f"Device price is £{inputs.price:.2f} — this seems very low. "
            f"Check the price unit ({inputs.price_unit.value})."
        )
    if inputs.price > 100_000:
        warnings.append(
            f"Device price is £{inputs.price:,.2f} — this seems very high. "
            f"Check the price unit ({inputs.price_unit.value})."
        )

    # ── Eligible cohort vs catchment ───────────────────────────────────
    if inputs.eligible_patients > inputs.catchment_size:
        warnings.append(
            f"Eligible cohort ({inputs.eligible_patients:,}) exceeds "
            f"catchment size ({inputs.catchment_size:,}) — "
            f"check eligible percentage ({inputs.eligible_pct}%)."
        )

    # ── Staff time saved vs current pathway time ───────────────────────
    total_current_minutes = sum(row.minutes for row in inputs.workforce)
    if inputs.staff_time_saved > total_current_minutes:
        warnings.append(
            f"Staff time saved ({inputs.staff_time_saved} mins) exceeds "
            f"total current pathway time ({total_current_minutes} mins) — "
            f"you cannot save more time than the pathway currently uses."
        )

    return warnings


def suggest_missing_inputs(inputs: BIAInputs) -> list[str]:
    """Return suggestions for optional fields that would improve accuracy.

    Each suggestion is a plain-English string the front-end can display
    as a prompt.  Only fields that are currently at their default (zero /
    empty) are flagged.
    """
    suggestions: list[str] = []

    if inputs.outpatient_visits == 0:
        suggestions.append(
            "Consider adding outpatient visit data for a more accurate "
            "current pathway cost."
        )

    if inputs.admissions == 0 and inputs.bed_days == 0:
        suggestions.append(
            "Consider adding admission and bed-day data if the device "
            "affects inpatient stays."
        )

    if inputs.tests == 0:
        suggestions.append(
            "Consider adding test/investigation data if the device "
            "replaces or reduces diagnostic tests."
        )

    if inputs.consumables == 0:
        suggestions.append(
            "Consider adding consumables cost if the current pathway "
            "uses disposable supplies."
        )

    if inputs.procedures == 0 and inputs.admissions > 0:
        suggestions.append(
            "You have admissions but no procedures — add procedure data "
            "if relevant (e.g. surgical interventions)."
        )

    if inputs.comparator_names is None or inputs.comparator_names == "":
        suggestions.append(
            "Consider naming the current comparator(s) for clearer "
            "reporting (e.g. 'Paper-based triage, existing EPR')."
        )

    if inputs.prevalence is None or inputs.prevalence == "":
        suggestions.append(
            "Adding prevalence/incidence notes strengthens the "
            "epidemiological rationale in the final report."
        )

    return suggestions


def estimate_confidence(inputs: BIAInputs) -> str:
    """Rate overall data completeness as High, Medium, or Low.

    Scoring
    -------
    Each populated input area earns points:

    - Workforce data present (always true — required field):  +1
    - Outpatient visits > 0:                                  +1
    - Admissions + bed days > 0:                              +1
    - Tests > 0:                                              +1
    - Procedures > 0:                                         +1
    - Consumables > 0:                                        +1
    - At least one savings field > 0:                         +1
    - Prevalence notes provided:                              +1

    High   = 6+ points  (detailed current pathway + savings data)
    Medium = 3–5 points (some resource data present)
    Low    = 0–2 points (only device price, minimal pathway detail)
    """
    score = 0

    # Workforce (always at least 1 row — required by schema)
    score += 1

    if inputs.outpatient_visits > 0:
        score += 1

    if inputs.admissions > 0 and inputs.bed_days > 0:
        score += 1

    if inputs.tests > 0:
        score += 1

    if inputs.procedures > 0:
        score += 1

    if inputs.consumables > 0:
        score += 1

    has_savings = any([
        inputs.staff_time_saved > 0,
        inputs.visits_reduced > 0,
        inputs.complications_reduced > 0,
        inputs.readmissions_reduced > 0,
        inputs.los_reduced > 0,
        inputs.follow_up_reduced > 0,
    ])
    if has_savings:
        score += 1

    if inputs.prevalence:
        score += 1

    if score >= 6:
        return "High"
    elif score >= 3:
        return "Medium"
    else:
        return "Low"
