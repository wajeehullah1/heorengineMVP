"""Translate NHS workforce bands and reference costs into £ values."""

from __future__ import annotations

# ── AfC Band Hourly Rates (£/hour, 2024-25 PSSRU) ─────────────────────

BAND_RATES: dict[str, float] = {
    "Band 2": 12.45,
    "Band 3": 14.28,
    "Band 4": 16.83,
    "Band 5 (Staff Nurse)": 21.37,
    "Band 6 (Senior Nurse/AHP)": 26.54,
    "Band 7 (Advanced Practitioner)": 32.11,
    "Band 8a (Consultant Nurse/Manager)": 40.22,
    "Registrar": 38.50,
    "Consultant": 72.00,
    "Admin/Clerical": 11.90,
}

# ── NHS Reference Costs (£, national average) ─────────────────────────

NHS_REFERENCE_COSTS: dict[str, float] = {
    "bed_day_general": 400.00,
    "bed_day_icu": 1800.00,
    "outpatient_first": 120.00,
    "outpatient_followup": 85.00,
    "emergency_dept": 180.00,
    "theatre_hour": 1200.00,
}


def calculate_workforce_cost(workforce: list[dict]) -> float:
    """Return total workforce cost per patient from a list of role rows.

    Each item in *workforce* must have the keys ``role`` (str matching a
    BAND_RATES key), ``minutes`` (numeric) and ``frequency`` (str, currently
    unused — included for forward-compatibility with per-visit costing).

    Args:
        workforce: List of dicts, e.g.
            [{"role": "Band 5 (Staff Nurse)", "minutes": 30, "frequency": "per patient"}]

    Returns:
        Total £ cost per patient across all roles.

    Raises:
        KeyError: If a role is not found in BAND_RATES.
    """
    total = 0.0
    for row in workforce:
        role = row["role"]
        if role not in BAND_RATES:
            raise KeyError(f"Unknown role '{role}'. Valid roles: {list(BAND_RATES)}")
        hourly_rate = BAND_RATES[role]
        minutes = float(row["minutes"])
        total += hourly_rate * minutes / 60
    return round(total, 2)


def get_cost(cost_type: str) -> float:
    """Look up a unit cost from NHS_REFERENCE_COSTS.

    Args:
        cost_type: Key into NHS_REFERENCE_COSTS, e.g. ``"bed_day_general"``.

    Returns:
        The reference cost in £.

    Raises:
        KeyError: If *cost_type* is not a recognised key.
    """
    if cost_type not in NHS_REFERENCE_COSTS:
        raise KeyError(
            f"Unknown cost type '{cost_type}'. "
            f"Valid types: {list(NHS_REFERENCE_COSTS)}"
        )
    return NHS_REFERENCE_COSTS[cost_type]


def apply_discount(cost: float, year: int, rate: float = 0.035) -> float:
    """Apply NICE-standard exponential discounting to a future cost.

    Year 0 (or 1) costs are not discounted.  Year 2 onward are discounted
    at ``1 / (1 + rate) ** (year - 1)``.

    Args:
        cost:  Undiscounted cost in £.
        year:  Forecast year (1-indexed; year 1 = no discount).
        rate:  Annual discount rate (default 3.5 % per NICE reference case).

    Returns:
        Present-value cost in £, rounded to 2 dp.
    """
    if year <= 1:
        return round(cost, 2)
    return round(cost / (1 + rate) ** (year - 1), 2)
