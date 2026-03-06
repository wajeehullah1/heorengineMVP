"""Pydantic schemas for Markov cost-effectiveness model inputs and outputs."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ── Inputs ────────────────────────────────────────────────────────────

class MarkovInputs(BaseModel):
    """Parameters for a 2-state (Alive → Dead) Markov cost-effectiveness model.

    The model compares a *standard care* arm against a *treatment* arm over
    a configurable time horizon, discounting costs and QALYs at a constant
    annual rate, and derives the Incremental Cost-Effectiveness Ratio (ICER).
    """

    intervention_name: str = Field(
        ..., min_length=1, description="Name of the treatment or intervention"
    )

    # ── Model configuration ──
    time_horizon: int = Field(
        5, gt=0, le=50, description="Simulation length in years"
    )
    cycle_length: float = Field(
        1.0, gt=0, le=1, description="Cycle length as fraction of a year (1 = annual, 0.25 = quarterly)"
    )
    discount_rate: float = Field(
        0.035, ge=0, le=1, description="Annual discount rate for costs and QALYs (0.035 = 3.5%)"
    )

    # ── Standard care arm ──
    prob_death_standard: float = Field(
        ..., description="Annual mortality probability for standard care (0–1)"
    )
    cost_standard_annual: float = Field(
        ..., ge=0, description="Annual cost (£) while alive under standard care"
    )
    utility_standard: float = Field(
        ..., description="Quality-of-life weight for standard care (0–1)"
    )

    # ── Treatment arm ──
    prob_death_treatment: float = Field(
        ..., description="Annual mortality probability for the treatment arm (0–1)"
    )
    cost_treatment_annual: float = Field(
        ..., ge=0, description="Annual cost (£) while alive under treatment"
    )
    cost_treatment_initial: float = Field(
        0, ge=0, description="One-time upfront treatment cost (£)"
    )
    utility_treatment: float = Field(
        ..., description="Quality-of-life weight for the treatment arm (0–1)"
    )

    # ── Validators ──

    @field_validator("prob_death_standard", "prob_death_treatment")
    @classmethod
    def probability_between_0_and_1(cls, v: float, info) -> float:
        if not 0 <= v <= 1:
            raise ValueError(
                f"{info.field_name} must be between 0 and 1, got {v}"
            )
        return v

    @field_validator("utility_standard", "utility_treatment")
    @classmethod
    def utility_between_0_and_1(cls, v: float, info) -> float:
        if not 0 <= v <= 1:
            raise ValueError(
                f"{info.field_name} must be between 0 and 1, got {v}"
            )
        return v

    # ── Helpers ──

    def to_r_params(self) -> dict:
        """Convert to the dict format expected by ``r/markov_model.R``.

        The R script uses ``cost_standard`` / ``cost_treatment`` (without the
        ``_annual`` suffix), so this method handles the mapping.
        """
        return {
            "time_horizon": self.time_horizon,
            "cycle_length": self.cycle_length,
            "discount_rate": self.discount_rate,
            "prob_death_standard": self.prob_death_standard,
            "cost_standard": self.cost_standard_annual,
            "utility_standard": self.utility_standard,
            "prob_death_treatment": self.prob_death_treatment,
            "cost_treatment": self.cost_treatment_annual,
            "cost_treatment_initial": self.cost_treatment_initial,
            "utility_treatment": self.utility_treatment,
        }


# ── Sub-models ────────────────────────────────────────────────────────

class ArmResult(BaseModel):
    """Discounted totals for a single model arm."""

    total_cost: float = Field(..., description="Total discounted cost (£)")
    total_qalys: float = Field(..., description="Total discounted QALYs")


# ── Outputs ───────────────────────────────────────────────────────────

class MarkovResults(BaseModel):
    """Results from a 2-state Markov cost-effectiveness analysis."""

    standard_care: ArmResult
    treatment: ArmResult
    incremental_cost: float = Field(
        ..., description="Treatment cost minus standard care cost (£)"
    )
    incremental_qalys: float = Field(
        ..., description="Treatment QALYs minus standard care QALYs"
    )
    icer: Optional[float] = Field(
        None,
        description="Incremental Cost-Effectiveness Ratio (£/QALY). "
        "None when there is no QALY difference between arms.",
    )
    interpretation: str = Field(
        ..., description="Plain-English assessment against NICE thresholds"
    )
    cost_effective_25k: bool = Field(
        ..., description="True if ICER < £25,000/QALY (or treatment dominates)"
    )
    cost_effective_35k: bool = Field(
        ..., description="True if ICER < £35,000/QALY (or treatment dominates)"
    )

    def get_summary(self) -> str:
        """Return a formatted text summary of the cost-effectiveness results.

        Example output::

            ── Cost-Effectiveness Summary ──────────────────
            Standard care:  £21,217  |  2.97 QALYs
            Treatment:      £50,282  |  3.75 QALYs
            Incremental:    £29,064  |  0.78 QALYs
            ICER:           £37,346/QALY
            Result:         Not cost-effective
            NICE £25k:      ✗  |  NICE £35k: ✗
        """
        std = self.standard_care
        trt = self.treatment

        if self.icer is not None:
            icer_str = f"£{self.icer:,.0f}/QALY"
        else:
            icer_str = "N/A (no QALY difference)"

        nice_25 = "✓" if self.cost_effective_25k else "✗"
        nice_35 = "✓" if self.cost_effective_35k else "✗"

        return (
            "── Cost-Effectiveness Summary ──────────────────\n"
            f"Standard care:  £{std.total_cost:,.0f}  |  {std.total_qalys:.2f} QALYs\n"
            f"Treatment:      £{trt.total_cost:,.0f}  |  {trt.total_qalys:.2f} QALYs\n"
            f"Incremental:    £{self.incremental_cost:,.0f}  |  {self.incremental_qalys:.2f} QALYs\n"
            f"ICER:           {icer_str}\n"
            f"Result:         {self.interpretation}\n"
            f"NICE £25k:      {nice_25}  |  NICE £35k: {nice_35}"
        )

    @classmethod
    def from_r_output(cls, raw: dict) -> MarkovResults:
        """Construct a ``MarkovResults`` from the raw dict returned by R.

        Handles the ``"NA"`` string that R emits when the ICER is undefined.
        """
        inc = raw["incremental"]
        icer_raw = inc["icer"]
        icer = None if icer_raw == "NA" else float(icer_raw)

        # Determine cost-effectiveness flags
        # Dominant (negative ICER with positive QALYs and negative cost) counts
        # as cost-effective at any threshold.
        dominant = inc["cost"] < 0 and inc["qalys"] > 0
        cost_effective_25k = dominant or (icer is not None and icer < 25_000)
        cost_effective_35k = dominant or (icer is not None and icer < 35_000)

        return cls(
            standard_care=ArmResult(**raw["standard_care"]),
            treatment=ArmResult(**raw["treatment"]),
            incremental_cost=inc["cost"],
            incremental_qalys=inc["qalys"],
            icer=icer,
            interpretation=raw["interpretation"],
            cost_effective_25k=cost_effective_25k,
            cost_effective_35k=cost_effective_35k,
        )
