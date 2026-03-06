"""Pydantic schemas for Budget Impact Analysis inputs and outputs."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ── Enums ──────────────────────────────────────────────────────────────

class NHSSetting(str, Enum):
    ACUTE = "Acute NHS Trust"
    ICB = "ICB"
    PRIMARY_CARE = "Primary Care Network"


class CatchmentType(str, Enum):
    POPULATION = "population"
    BEDS = "beds"


class FundingSource(str, Enum):
    TRUST_OPERATIONAL = "Trust operational budget"
    ICB_COMMISSIONING = "ICB commissioning"
    TRANSFORMATION = "Transformation / innovation funding"
    CAPITAL = "Capital budget"
    INDUSTRY_PILOT = "Industry-funded pilot"
    RESEARCH_GRANT = "Research / grant"
    UNSURE = "Unsure"


class PricingModel(str, Enum):
    PER_PATIENT = "per-patient"
    PER_USE = "per-use"
    SUBSCRIPTION = "subscription"
    CAPITAL_CONSUMABLES = "capital + consumables"


class PriceUnit(str, Enum):
    PER_YEAR = "per year"
    PER_PATIENT = "per patient"
    PER_USE = "per use"


class ComparatorType(str, Enum):
    NONE = "none"
    DIGITAL = "digital"
    DIAGNOSTIC = "diagnostic"
    DEVICE = "device"


class Discounting(str, Enum):
    OFF = "off"
    ON = "on"


# ── Sub-models ─────────────────────────────────────────────────────────

class WorkforceRow(BaseModel):
    """Single row in the workforce table."""

    role: str = Field(
        ...,
        description="NHS AfC band or role, e.g. 'Band 5 (Staff Nurse)'",
    )
    minutes: float = Field(..., ge=0, description="Minutes per patient")
    frequency: str = Field(
        "per patient",
        description="One of: per patient, per visit, per admission, per year",
    )


class ScenarioResult(BaseModel):
    """Budget impact results for a single scenario."""

    annual_budget_impact: list[float] = Field(
        ..., description="Net budget impact (£) for each forecast year"
    )
    cost_per_patient: list[float] = Field(
        ..., description="Cost per treated patient (£) for each forecast year"
    )
    total_treated_patients: list[int] = Field(
        ..., description="Number of treated patients for each forecast year"
    )


# ── Inputs ─────────────────────────────────────────────────────────────

class BIAInputs(BaseModel):
    """All inputs collected by the HEOR Input Engine form."""

    # ── Section 1: Setting & Scope ──
    setting: NHSSetting
    model_year: int = Field(
        ..., ge=2024, le=2030, description="Financial year the model starts"
    )
    forecast_years: int = Field(
        ..., ge=1, le=10, description="Number of years to forecast"
    )
    funding_source: FundingSource

    # ── Section 2: Target Population ──
    catchment_type: CatchmentType = CatchmentType.POPULATION
    catchment_size: int = Field(..., gt=0, description="Population or bed count")
    eligible_pct: float = Field(
        ..., gt=0, le=100, description="Percentage of catchment eligible"
    )
    uptake_y1: float = Field(..., ge=0, le=100, description="Year 1 uptake %")
    uptake_y2: float = Field(..., ge=0, le=100, description="Year 2 uptake %")
    uptake_y3: float = Field(..., ge=0, le=100, description="Year 3 uptake %")
    prevalence: Optional[str] = Field(
        None, description="Free-text prevalence / incidence notes"
    )

    # ── Section 3: Current Pathway ──
    workforce: list[WorkforceRow] = Field(
        ..., min_length=1, description="At least one workforce row required"
    )
    outpatient_visits: int = Field(0, ge=0, description="Visits per patient per year")
    tests: int = Field(0, ge=0, description="Tests per patient per year")
    admissions: int = Field(0, ge=0, description="Admissions per patient per year")
    bed_days: int = Field(0, ge=0, description="Bed days per admission")
    procedures: int = Field(0, ge=0, description="Procedures per patient per year")
    consumables: float = Field(0, ge=0, description="Consumables cost £ per patient")

    # ── Section 4: Intervention & Pricing ──
    pricing_model: PricingModel = PricingModel.PER_PATIENT
    price: float = Field(..., ge=0, description="Intervention price (£)")
    price_unit: PriceUnit = PriceUnit.PER_YEAR
    needs_training: bool = False
    training_roles: Optional[str] = Field(
        None, description="Roles requiring training, e.g. 'Band 5 nurses, registrars'"
    )
    training_hours: Optional[float] = Field(
        None, ge=0, description="Training hours per person"
    )
    setup_cost: float = Field(0, ge=0, description="One-off setup cost (£)")

    # ── Resource changes vs current pathway ──
    staff_time_saved: float = Field(0, ge=0, description="Minutes saved per patient")
    visits_reduced: float = Field(
        0, ge=0, le=100, description="% reduction in visits/tests"
    )
    complications_reduced: float = Field(
        0, ge=0, le=100, description="% reduction in complications"
    )
    readmissions_reduced: float = Field(
        0, ge=0, le=100, description="% reduction in readmissions"
    )
    los_reduced: float = Field(
        0, ge=0, description="Reduction in length of stay (days)"
    )
    follow_up_reduced: float = Field(
        0, ge=0, le=100, description="% reduction in follow-up visits"
    )

    # ── Comparator & Discounting ──
    comparator: ComparatorType = ComparatorType.NONE
    comparator_names: Optional[str] = Field(
        None, description="Named alternatives, e.g. 'Paper-based triage, existing EPR'"
    )
    discounting: Discounting = Discounting.OFF

    # ── Computed helpers ──

    @property
    def eligible_patients(self) -> int:
        return round(self.catchment_size * self.eligible_pct / 100)

    @property
    def treated_patients_by_year(self) -> list[int]:
        n = self.eligible_patients
        return [
            round(n * self.uptake_y1 / 100),
            round(n * self.uptake_y2 / 100),
            round(n * self.uptake_y3 / 100),
        ]

    @field_validator("training_roles", "training_hours")
    @classmethod
    def training_fields_required_when_needed(cls, v, info):
        if info.data.get("needs_training") and v is None:
            raise ValueError("Required when needs_training is True")
        return v


# ── Outputs ────────────────────────────────────────────────────────────

class BIAResults(BaseModel):
    """Outputs produced by the BIA engine."""

    annual_budget_impact: list[float] = Field(
        ..., description="Net budget impact (£) for each forecast year"
    )
    cost_per_patient: list[float] = Field(
        ..., description="Cost per treated patient (£) for each forecast year"
    )
    total_treated_patients: list[int] = Field(
        ..., description="Treated patients for each forecast year"
    )
    break_even_year: Optional[int] = Field(
        None,
        description="Year in which cumulative savings exceed costs (null if never)",
    )
    top_cost_drivers: list[str] = Field(
        ..., description="Ranked list of the largest cost components"
    )
    scenarios: dict[str, ScenarioResult] = Field(
        ...,
        description="Results keyed by scenario name: conservative, base, optimistic",
    )
