"""Pydantic request/response schemas for HEOR Engine workflow endpoints.

Each pair of ``*Request`` / ``*Response`` models covers one top-level workflow
exposed by :class:`~agents.orchestrator.HEOROrchestrator`:

    BIA          — Budget Impact Analysis
    CEA          — Cost-Effectiveness Analysis (Markov)
    Combined     — BIA + CEA in a single call with auto-derived CEA params
    SLR          — Systematic Literature Review abstract screening

Usage::

    from agents.workflow_schema import (
        BIAWorkflowRequest,
        BIAWorkflowResponse,
        CombinedWorkflowRequest,
        CombinedWorkflowResponse,
        SLRWorkflowRequest,
        SLRWorkflowResponse,
    )
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Enums ─────────────────────────────────────────────────────────────────────

class WorkflowStatus(str, Enum):
    """Terminal or intermediate status for any workflow run."""

    COMPLETED = "completed"
    """All steps executed without error; outputs are available."""

    FAILED = "failed"
    """A non-recoverable error halted the workflow; no outputs produced."""

    PARTIAL = "partial"
    """Workflow completed but one or more optional steps were skipped or errored
    (e.g. report generation failed after a successful calculation)."""


class ReportFormat(str, Enum):
    """Output format for generated slide decks."""

    PPTX = "pptx"
    """Microsoft PowerPoint — the default and recommended format."""

    DOCX = "docx"
    """Microsoft Word — useful when presentational slides are not required."""


class ExportFormat(str, Enum):
    """Tabular export format for SLR screening results."""

    CSV = "csv"
    """Comma-separated values — maximum compatibility."""

    EXCEL = "excel"
    """Microsoft Excel (.xlsx) — preserves formatting and multi-sheet layout."""


# ── Shared validators ─────────────────────────────────────────────────────────

# Required top-level keys for each BIA inputs dict (subset — enough to detect
# obviously empty or malformed dicts before handing off to the orchestrator).
_BIA_REQUIRED_KEYS: frozenset[str] = frozenset({
    "setting", "model_year", "forecast_years", "catchment_size",
    "eligible_pct", "uptake_y1", "workforce", "price",
})

# Required keys for Markov / CEA inputs.
_CEA_REQUIRED_KEYS: frozenset[str] = frozenset({
    "intervention_name",
    "prob_death_standard", "cost_standard_annual", "utility_standard",
    "prob_death_treatment", "cost_treatment_annual", "utility_treatment",
})

# Required PICO keys for SLR screening.
_PICO_REQUIRED_KEYS: frozenset[str] = frozenset({
    "population", "intervention", "comparison", "outcomes",
})


# ── BIA Workflow ──────────────────────────────────────────────────────────────

class BIAWorkflowRequest(BaseModel):
    """Request body for a full Budget Impact Analysis workflow.

    Passed directly to
    :meth:`~agents.orchestrator.HEOROrchestrator.run_full_bia_workflow`.
    The ``inputs`` dict must satisfy :class:`~engines.bia.schema.BIAInputs`;
    use :meth:`has_required_fields` to check for the most critical keys before
    submitting.
    """

    inputs: dict[str, Any] = Field(
        ...,
        description=(
            "Raw BIA input parameters.  Must contain at minimum: "
            "``setting``, ``model_year``, ``forecast_years``, ``catchment_size``, "
            "``eligible_pct``, ``uptake_y1``, ``workforce`` (list), and ``price``. "
            "All other BIAInputs fields default to their schema defaults."
        ),
    )
    enrich_with_evidence: bool = Field(
        True,
        description=(
            "When True, the evidence agent pre-fills missing NHS reference costs, "
            "population estimates, and NICE comparators before running the model. "
            "Set to False for exact reproducibility of a previously-validated run."
        ),
    )
    generate_report: bool = Field(
        True,
        description=(
            "When True, a PowerPoint report is generated after the BIA calculation "
            "and its path is returned in ``report_url``. "
            "Set to False for API-only consumers that do not need a file output."
        ),
    )
    report_format: ReportFormat = Field(
        ReportFormat.PPTX,
        description="Format of the generated report.  Defaults to PowerPoint.",
    )
    intervention_name: Optional[str] = Field(
        None,
        description=(
            "Human-readable name used as the report title and submission label, "
            "e.g. 'Acme Remote Monitoring Device v2'. "
            "If omitted the report uses a generic title."
        ),
    )

    # ── Validators ────────────────────────────────────────────────────────

    @field_validator("inputs", mode="before")
    @classmethod
    def inputs_must_not_be_empty(cls, v: dict) -> dict:
        """Reject completely empty input dicts at the boundary."""
        if not isinstance(v, dict) or len(v) == 0:
            raise ValueError(
                "inputs must be a non-empty dict containing BIA parameters"
            )
        return v

    @field_validator("intervention_name", mode="before")
    @classmethod
    def strip_intervention_name(cls, v: Optional[str]) -> Optional[str]:
        """Strip whitespace; return None for blank strings."""
        if v is None:
            return None
        v = v.strip()
        return v if v else None

    # ── Helpers ───────────────────────────────────────────────────────────

    def has_required_fields(self) -> bool:
        """Return True if ``inputs`` contains every critical BIA key.

        This is a lightweight pre-check — Pydantic will validate the values
        fully once the dict is passed to :class:`~engines.bia.schema.BIAInputs`.

        Returns:
            True when all eight required keys are present.
        """
        return _BIA_REQUIRED_KEYS.issubset(self.inputs.keys())

    def missing_fields(self) -> list[str]:
        """Return a sorted list of required BIA keys absent from ``inputs``.

        Returns:
            Empty list when all required keys are present.
        """
        return sorted(_BIA_REQUIRED_KEYS - set(self.inputs.keys()))


class BIAWorkflowResponse(BaseModel):
    """Response payload returned after a full BIA workflow run.

    ``results`` is the serialised :class:`~engines.bia.schema.BIAResults`
    dict.  Individual fields (break-even year, top cost drivers) can be
    extracted via the helper methods below.
    """

    workflow_id: str = Field(
        ...,
        description=(
            "Unique identifier for this workflow run "
            "(format: ``bia_{YYYYMMDD_HHMMSS}_{8-char-uuid}``)."
        ),
    )
    submission_id: str = Field(
        ...,
        description=(
            "Identifier of the saved submission file under "
            "``data/submissions/``.  Typically the same as ``workflow_id``."
        ),
    )
    status: WorkflowStatus = Field(
        ...,
        description="Terminal status of the workflow run.",
    )
    results: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Serialised BIAResults containing ``annual_budget_impact``, "
            "``cost_per_patient``, ``total_treated_patients``, "
            "``break_even_year``, ``top_cost_drivers``, and ``scenarios``."
        ),
    )
    enrichment_applied: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Summary of evidence-agent enrichment applied to the inputs. "
            "Keys reflect which data sources were used "
            "(e.g. ``nhs_reference_costs``, ``ons_population``, ``nice_context``)."
        ),
    )
    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "Clinical-sense warnings from "
            ":func:`~engines.bia.validation.validate_clinical_sense` — advisory "
            "only, they do not indicate a failed run."
        ),
    )
    report_url: Optional[str] = Field(
        None,
        description=(
            "Filesystem path (or relative API URL) of the generated report file. "
            "None when ``generate_report`` was False or report generation failed."
        ),
    )
    execution_time_seconds: float = Field(
        ...,
        ge=0,
        description="Wall-clock time from workflow start to completion (seconds).",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the workflow was initiated.",
    )

    # ── Validators ────────────────────────────────────────────────────────

    @field_validator("workflow_id", "submission_id", mode="before")
    @classmethod
    def require_non_empty_id(cls, v: str) -> str:
        """Reject blank ID strings."""
        if not isinstance(v, str) or not v.strip():
            raise ValueError("ID fields must be non-empty strings")
        return v.strip()

    # ── Helpers ───────────────────────────────────────────────────────────

    def is_complete(self) -> bool:
        """Return True if the workflow completed without error."""
        return self.status == WorkflowStatus.COMPLETED

    def format_execution_time(self) -> str:
        """Return execution time as a human-readable string.

        Examples:
            ``"0.8s"``, ``"23.4s"``, ``"1m 23.4s"``
        """
        t = self.execution_time_seconds
        if t < 60:
            return f"{t:.1f}s"
        minutes = int(t // 60)
        secs = t % 60
        return f"{minutes}m {secs:.1f}s"

    def break_even_year(self) -> Optional[int]:
        """Extract the break-even year from ``results``, or None.

        Returns:
            Integer year (1-indexed) when cumulative savings exceed costs,
            or None when the model never breaks even or results are absent.
        """
        return self.results.get("break_even_year")

    def top_drivers(self) -> list[str]:
        """Extract the ranked top cost drivers from ``results``.

        Returns:
            List of cost-driver labels (e.g. ``['Staff time', 'Bed days']``),
            or empty list if results are absent.
        """
        return self.results.get("top_cost_drivers", [])

    def annual_impacts(self) -> list[float]:
        """Return the base-case annual net budget impact series (£).

        Returns:
            List of floats (one per forecast year), or empty list.
        """
        return self.results.get("annual_budget_impact", [])


# ── CEA Workflow ──────────────────────────────────────────────────────────────

class CEAWorkflowRequest(BaseModel):
    """Request body for a full Cost-Effectiveness Analysis (Markov) workflow.

    Passed to :meth:`~agents.orchestrator.HEOROrchestrator.run_full_cea_workflow`.
    ``inputs`` must satisfy :class:`~engines.markov.schema.MarkovInputs`;
    the seven required keys are: ``intervention_name``,
    ``prob_death_standard``, ``cost_standard_annual``, ``utility_standard``,
    ``prob_death_treatment``, ``cost_treatment_annual``, ``utility_treatment``.

    .. note::
        This workflow requires R to be installed on the host machine.
    """

    inputs: dict[str, Any] = Field(
        ...,
        description=(
            "Raw Markov model parameters.  Required keys: "
            "``intervention_name``, ``prob_death_standard``, "
            "``cost_standard_annual``, ``utility_standard``, "
            "``prob_death_treatment``, ``cost_treatment_annual``, "
            "``utility_treatment``.  Optional keys (with defaults): "
            "``time_horizon`` (5), ``cycle_length`` (1.0), "
            "``discount_rate`` (0.035), ``cost_treatment_initial`` (0)."
        ),
    )
    validate_against_nice: bool = Field(
        True,
        description=(
            "When True, NICE threshold context is fetched for the "
            "intervention and included in the response under "
            "``validation_report``.  Has no effect on the Markov calculation."
        ),
    )
    generate_report: bool = Field(
        True,
        description=(
            "When True, a PowerPoint CEA report is generated and its path "
            "returned in ``report_url``."
        ),
    )
    intervention_name: str = Field(
        ...,
        min_length=1,
        description=(
            "Name of the technology being evaluated.  Used as the report title "
            "and must match the ``intervention_name`` key in ``inputs``."
        ),
    )

    # ── Validators ────────────────────────────────────────────────────────

    @field_validator("inputs", mode="before")
    @classmethod
    def inputs_must_not_be_empty(cls, v: dict) -> dict:
        """Reject completely empty input dicts."""
        if not isinstance(v, dict) or len(v) == 0:
            raise ValueError(
                "inputs must be a non-empty dict containing Markov parameters"
            )
        return v

    @field_validator("intervention_name", mode="before")
    @classmethod
    def strip_intervention_name(cls, v: str) -> str:
        """Strip whitespace and reject blank strings."""
        if not isinstance(v, str):
            raise ValueError("intervention_name must be a string")
        v = v.strip()
        if not v:
            raise ValueError("intervention_name must not be empty")
        return v

    @model_validator(mode="after")
    def sync_intervention_name_to_inputs(self) -> CEAWorkflowRequest:
        """Ensure ``inputs['intervention_name']`` matches ``intervention_name``.

        If ``inputs`` does not contain ``intervention_name``, inject it from
        the top-level field.  If both are present and differ, the top-level
        field wins and ``inputs`` is updated to match.
        """
        self.inputs.setdefault("intervention_name", self.intervention_name)
        if self.inputs["intervention_name"] != self.intervention_name:
            self.inputs["intervention_name"] = self.intervention_name
        return self

    # ── Helpers ───────────────────────────────────────────────────────────

    def has_required_fields(self) -> bool:
        """Return True if ``inputs`` contains every critical Markov key.

        Returns:
            True when all seven required fields are present in ``inputs``.
        """
        return _CEA_REQUIRED_KEYS.issubset(self.inputs.keys())

    def missing_fields(self) -> list[str]:
        """Return required Markov keys absent from ``inputs``.

        Returns:
            Sorted list of missing key names, empty if complete.
        """
        return sorted(_CEA_REQUIRED_KEYS - set(self.inputs.keys()))


class CEAWorkflowResponse(BaseModel):
    """Response payload after a full CEA / Markov workflow run.

    ``results`` is the serialised :class:`~engines.markov.schema.MarkovResults`
    dict.  ICER, interpretation, and NICE threshold flags can be read directly
    from the dict or via the helper methods.
    """

    workflow_id: str = Field(
        ...,
        description=(
            "Unique identifier (format: ``cea_{YYYYMMDD_HHMMSS}_{8-char-uuid}``)."
        ),
    )
    status: WorkflowStatus = Field(
        ...,
        description="Terminal status of the workflow run.",
    )
    results: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Serialised MarkovResults containing ``standard_care``, ``treatment``, "
            "``incremental_cost``, ``incremental_qalys``, ``icer``, "
            "``interpretation``, ``cost_effective_25k``, ``cost_effective_35k``."
        ),
    )
    validation_report: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "NICE context and benchmark comparisons fetched by the evidence agent. "
            "Empty dict when ``validate_against_nice`` was False or the lookup failed."
        ),
    )
    report_url: Optional[str] = Field(
        None,
        description="Path of the generated CEA PowerPoint report, or None.",
    )
    execution_time_seconds: float = Field(
        ...,
        ge=0,
        description="Wall-clock time from workflow start to completion (seconds).",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the workflow was initiated.",
    )

    # ── Validators ────────────────────────────────────────────────────────

    @field_validator("workflow_id", mode="before")
    @classmethod
    def require_non_empty_id(cls, v: str) -> str:
        """Reject blank workflow ID strings."""
        if not isinstance(v, str) or not v.strip():
            raise ValueError("workflow_id must be a non-empty string")
        return v.strip()

    # ── Helpers ───────────────────────────────────────────────────────────

    def is_complete(self) -> bool:
        """Return True if the workflow completed without error."""
        return self.status == WorkflowStatus.COMPLETED

    def icer_formatted(self) -> str:
        """Return the ICER as a formatted string.

        Returns:
            ``"£37,346/QALY"``, ``"Dominant (better outcomes, lower cost)"``,
            ``"N/A (no QALY difference)"``, or ``"—"`` when results are absent.
        """
        if not self.results:
            return "—"
        icer = self.results.get("icer")
        interp = self.results.get("interpretation", "")
        if icer is None:
            # Dominant or equal-outcome arms
            return interp if interp else "N/A (no QALY difference)"
        return f"£{icer:,.0f}/QALY"

    def is_cost_effective(self, threshold: int = 25_000) -> Optional[bool]:
        """Return True if the intervention is cost-effective at *threshold* £/QALY.

        Args:
            threshold: NICE cost-effectiveness threshold in £/QALY.
                Use 25_000 (default) for the standard lower threshold or
                35_000 for the upper end-of-life threshold.

        Returns:
            Boolean flag from ``results``, or None when results are absent.
        """
        if not self.results:
            return None
        if threshold <= 25_000:
            return self.results.get("cost_effective_25k")
        return self.results.get("cost_effective_35k")

    def incremental_summary(self) -> dict[str, Any]:
        """Return incremental cost, QALYs, and ICER in a single dict.

        Returns:
            Dict with keys ``incremental_cost``, ``incremental_qalys``, ``icer``
            (raw float or None), ``icer_formatted`` (string), ``interpretation``.
            All values are None / empty string when results are absent.
        """
        return {
            "incremental_cost": self.results.get("incremental_cost"),
            "incremental_qalys": self.results.get("incremental_qalys"),
            "icer": self.results.get("icer"),
            "icer_formatted": self.icer_formatted(),
            "interpretation": self.results.get("interpretation", ""),
        }


# ── Combined Workflow ─────────────────────────────────────────────────────────

class CombinedWorkflowRequest(BaseModel):
    """Request body for a combined BIA + CEA workflow.

    Passed to
    :meth:`~agents.orchestrator.HEOROrchestrator.run_combined_workflow`.
    The Markov CEA parameters are automatically derived from the BIA results
    — the caller only needs to supply ``mortality_reduction_pct`` and
    ``utility_gain``.
    """

    bia_inputs: dict[str, Any] = Field(
        ...,
        description=(
            "Raw BIA parameters — same structure as "
            ":attr:`BIAWorkflowRequest.inputs`."
        ),
    )
    mortality_reduction_pct: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description=(
            "Absolute reduction in annual mortality probability attributed to the "
            "intervention, expressed as a **percentage** (0–100).  For example, "
            "a value of 3.0 means a 3 percentage-point reduction "
            "(i.e. 0.03 absolute).  "
            "Use :meth:`mortality_reduction_absolute` to convert to the 0–1 "
            "scale expected by the Markov model."
        ),
    )
    utility_gain: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Additive QALY-weight improvement on the 0–1 EQ-5D scale. "
            "For example, 0.10 represents a gain of 0.1 utility points "
            "(approximately equivalent to moving from moderate to mild health state)."
        ),
    )
    intervention_name: str = Field(
        ...,
        min_length=1,
        description=(
            "Human-readable label used in both the BIA and CEA report titles."
        ),
    )
    generate_combined_report: bool = Field(
        True,
        description=(
            "When True, a combined report deck (BIA + CEA slides) is generated "
            "after both analyses complete.  Individual sub-reports are always "
            "generated regardless of this flag."
        ),
    )

    # ── Validators ────────────────────────────────────────────────────────

    @field_validator("bia_inputs", mode="before")
    @classmethod
    def bia_inputs_must_not_be_empty(cls, v: dict) -> dict:
        """Reject completely empty BIA input dicts."""
        if not isinstance(v, dict) or len(v) == 0:
            raise ValueError("bia_inputs must be a non-empty dict")
        return v

    @field_validator("intervention_name", mode="before")
    @classmethod
    def strip_intervention_name(cls, v: str) -> str:
        """Strip whitespace and reject blank strings."""
        if not isinstance(v, str):
            raise ValueError("intervention_name must be a string")
        v = v.strip()
        if not v:
            raise ValueError("intervention_name must not be empty")
        return v

    # ── Helpers ───────────────────────────────────────────────────────────

    def mortality_reduction_absolute(self) -> float:
        """Convert ``mortality_reduction_pct`` to the 0–1 scale for the Markov model.

        Returns:
            Float in [0, 1], e.g. ``3.0 → 0.03``.
        """
        return round(self.mortality_reduction_pct / 100.0, 6)

    def has_required_bia_fields(self) -> bool:
        """Return True if ``bia_inputs`` contains every critical BIA key.

        Returns:
            True when all required BIA keys are present.
        """
        return _BIA_REQUIRED_KEYS.issubset(self.bia_inputs.keys())


class CombinedWorkflowResponse(BaseModel):
    """Response payload after a combined BIA + CEA workflow run.

    ``executive_summary`` is auto-generated from the BIA and CEA results
    when not supplied explicitly; callers can override it by passing their
    own text.
    """

    workflow_id: str = Field(
        ...,
        description=(
            "Top-level combined workflow ID "
            "(format: ``combined_{YYYYMMDD_HHMMSS}_{8-char-uuid}``)."
        ),
    )
    status: WorkflowStatus = Field(
        ...,
        description="Terminal status of the combined workflow.",
    )
    bia_results: dict[str, Any] = Field(
        default_factory=dict,
        description="Serialised BIAResults from the BIA sub-workflow.",
    )
    cea_results: dict[str, Any] = Field(
        default_factory=dict,
        description="Serialised MarkovResults from the CEA sub-workflow.",
    )
    combined_report_url: Optional[str] = Field(
        None,
        description=(
            "Path of a merged BIA+CEA slide deck, or None when "
            "``generate_combined_report`` was False."
        ),
    )
    executive_summary: str = Field(
        default="",
        description=(
            "Auto-generated plain-English summary of both analyses, suitable "
            "for inclusion in an NHS board paper or NICE submission cover note. "
            "Override by supplying a non-empty string at construction time."
        ),
    )
    execution_time_seconds: float = Field(
        ...,
        ge=0,
        description="Total wall-clock time for both sub-workflows (seconds).",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the workflow was initiated.",
    )

    # ── Validators ────────────────────────────────────────────────────────

    @field_validator("workflow_id", mode="before")
    @classmethod
    def require_non_empty_id(cls, v: str) -> str:
        """Reject blank workflow ID strings."""
        if not isinstance(v, str) or not v.strip():
            raise ValueError("workflow_id must be a non-empty string")
        return v.strip()

    @model_validator(mode="after")
    def auto_generate_executive_summary(self) -> CombinedWorkflowResponse:
        """Populate ``executive_summary`` from BIA and CEA results if empty.

        The generated summary is a concise paragraph suitable for an NHS board
        paper.  It is only computed when ``executive_summary`` is blank, so
        callers can supply their own text by pre-populating the field.
        """
        if self.executive_summary:
            return self  # caller provided their own summary

        bia = self.bia_results
        cea = self.cea_results

        if not bia and not cea:
            return self  # nothing to summarise

        parts: list[str] = []

        # ── BIA sentence ──────────────────────────────────────────────────
        if bia:
            impacts: list[float] = bia.get("annual_budget_impact", [])
            bey: Optional[int] = bia.get("break_even_year")
            drivers: list[str] = bia.get("top_cost_drivers", [])

            if impacts:
                cumulative = sum(impacts)
                n_years = len(impacts)
                sign = "saving" if cumulative < 0 else "investment"
                parts.append(
                    f"The {n_years}-year cumulative budget {sign} is "
                    f"£{abs(cumulative):,.0f}."
                )
            if bey is not None:
                parts.append(f"Break-even is projected in Year {bey}.")
            elif impacts and sum(impacts) > 0:
                parts.append("The model does not project a break-even within the forecast period.")
            if drivers:
                parts.append(f"Primary cost drivers: {', '.join(drivers[:3])}.")

        # ── CEA sentence ──────────────────────────────────────────────────
        if cea:
            icer: Optional[float] = cea.get("icer")
            interp: str = cea.get("interpretation", "")
            ce_25k: Optional[bool] = cea.get("cost_effective_25k")
            inc_cost: Optional[float] = cea.get("incremental_cost")
            inc_qalys: Optional[float] = cea.get("incremental_qalys")

            if icer is not None:
                parts.append(
                    f"Cost-effectiveness analysis yields an ICER of "
                    f"£{icer:,.0f}/QALY ({interp.lower()})."
                )
            elif inc_cost is not None and inc_qalys is not None:
                if inc_cost < 0 and inc_qalys > 0:
                    parts.append("The intervention dominates standard care (lower cost and better outcomes).")
                else:
                    parts.append(f"Incremental outcomes: £{inc_cost:,.0f} cost, {inc_qalys:.2f} QALYs.")

            if ce_25k is not None:
                threshold_str = "below the £25,000/QALY" if ce_25k else "above the £25,000/QALY"
                parts.append(f"The ICER is {threshold_str} NICE threshold.")

        self.executive_summary = " ".join(parts)
        return self

    # ── Helpers ───────────────────────────────────────────────────────────

    def is_complete(self) -> bool:
        """Return True if the combined workflow completed without error."""
        return self.status == WorkflowStatus.COMPLETED

    def both_analyses_available(self) -> bool:
        """Return True if both BIA and CEA results are populated.

        A combined report is only meaningful when both sets of results are
        available.  Use this guard before rendering combined slides.
        """
        return bool(self.bia_results) and bool(self.cea_results)

    def bia_break_even(self) -> Optional[int]:
        """Extract break-even year from BIA results, or None."""
        return self.bia_results.get("break_even_year")

    def cea_icer(self) -> Optional[float]:
        """Extract raw ICER float from CEA results, or None."""
        return self.cea_results.get("icer")


# ── SLR Workflow ──────────────────────────────────────────────────────────────

class SLRWorkflowRequest(BaseModel):
    """Request body for an AI abstract screening workflow.

    Passed to :meth:`~agents.orchestrator.HEOROrchestrator.run_slr_workflow`.
    ``pico_criteria`` must contain the four required PICO keys; ``abstracts``
    must be a non-empty list of dicts that can be parsed into
    :class:`~engines.slr.schema.Abstract` objects.
    """

    pico_criteria: dict[str, Any] = Field(
        ...,
        description=(
            "PICO eligibility framework.  Required keys: "
            "``population``, ``intervention``, ``comparison``, ``outcomes`` (list). "
            "Optional keys: ``study_types`` (list, default ['RCT']), "
            "``exclusion_criteria`` (list)."
        ),
    )
    abstracts: list[dict[str, Any]] = Field(
        ...,
        min_length=1,
        description=(
            "List of abstract dicts to screen.  Each dict must contain: "
            "``pmid`` (str), ``title`` (str), ``abstract`` (str), "
            "``authors`` (list[str]), ``journal`` (str), ``year`` (int). "
            "Optional: ``doi`` (str), ``keywords`` (list[str])."
        ),
    )
    batch_name: Optional[str] = Field(
        None,
        description=(
            "Human-readable label for this screening run, used in file names "
            "and audit logs.  If omitted, a timestamp-based name is generated."
        ),
    )
    export_format: ExportFormat = Field(
        ExportFormat.CSV,
        description=(
            "Tabular export format for the screening results.  "
            "CSV is the default; Excel adds multi-sheet formatting."
        ),
    )

    # ── Validators ────────────────────────────────────────────────────────

    @field_validator("pico_criteria", mode="before")
    @classmethod
    def validate_pico_keys(cls, v: dict) -> dict:
        """Reject PICO dicts missing any of the four required keys."""
        if not isinstance(v, dict) or len(v) == 0:
            raise ValueError("pico_criteria must be a non-empty dict")
        missing = _PICO_REQUIRED_KEYS - set(v.keys())
        if missing:
            raise ValueError(
                f"pico_criteria is missing required keys: {sorted(missing)}. "
                f"Required: {sorted(_PICO_REQUIRED_KEYS)}"
            )
        return v

    @field_validator("pico_criteria", mode="before")
    @classmethod
    def validate_outcomes_list(cls, v: dict) -> dict:
        """Ensure ``outcomes`` is a non-empty list."""
        # Only run if the dict is already valid (missing-keys check runs first
        # in Pydantic v2 field validator ordering; if 'outcomes' is absent the
        # previous validator will already have raised).
        if isinstance(v, dict) and "outcomes" in v:
            outcomes = v["outcomes"]
            if not isinstance(outcomes, list) or len(outcomes) == 0:
                raise ValueError("pico_criteria['outcomes'] must be a non-empty list")
        return v

    @field_validator("batch_name", mode="before")
    @classmethod
    def strip_batch_name(cls, v: Optional[str]) -> Optional[str]:
        """Strip whitespace; return None for blank batch names."""
        if v is None:
            return None
        v = v.strip()
        return v if v else None

    # ── Helpers ───────────────────────────────────────────────────────────

    def abstract_count(self) -> int:
        """Return the number of abstracts submitted for screening."""
        return len(self.abstracts)

    def has_exclusion_criteria(self) -> bool:
        """Return True if the PICO dict includes explicit exclusion criteria."""
        ec = self.pico_criteria.get("exclusion_criteria")
        return bool(ec and len(ec) > 0)

    def effective_batch_name(self) -> str:
        """Return ``batch_name`` if set, otherwise a timestamp-based label.

        Returns:
            A non-empty string always — safe to use in file names.
        """
        if self.batch_name:
            return self.batch_name
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        population_slug = (
            self.pico_criteria.get("population", "slr")[:20]
            .lower()
            .replace(" ", "-")
        )
        return f"{population_slug}_{ts}"


class SLRWorkflowResponse(BaseModel):
    """Response payload after an SLR abstract screening workflow run.

    ``decisions`` is a list of serialised
    :class:`~engines.slr.schema.ScreeningDecision` dicts.
    Aggregate counts are pre-computed in ``screening_summary``; per-decision
    data can be grouped by outcome using :meth:`decisions_by_outcome`.
    """

    workflow_id: str = Field(
        ...,
        description=(
            "Unique identifier for this SLR workflow "
            "(format: ``slr_{YYYYMMDD_HHMMSS}_{8-char-uuid}``)."
        ),
    )
    batch_id: str = Field(
        ...,
        description=(
            "UUID of the :class:`~engines.slr.schema.ScreeningBatch` "
            "created and saved by this run."
        ),
    )
    status: WorkflowStatus = Field(
        ...,
        description="Terminal status of the workflow run.",
    )
    screening_summary: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Aggregate counts from the screening run.  Keys: "
            "``total``, ``included``, ``excluded``, ``uncertain``, "
            "``inclusion_rate`` (float 0–1), ``mean_pico_score`` (float 0–4)."
        ),
    )
    decisions: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Serialised list of ScreeningDecision dicts, one per abstract. "
            "Each dict contains: ``pmid``, ``decision``, ``confidence``, "
            "``reasoning``, ``pico_match``, ``exclusion_reasons``, "
            "``reviewer``, ``timestamp``."
        ),
    )
    export_url: Optional[str] = Field(
        None,
        description=(
            "Filesystem path of the exported results file (CSV or Excel), "
            "or None if export failed."
        ),
    )
    execution_time_seconds: float = Field(
        ...,
        ge=0,
        description="Wall-clock time from workflow start to completion (seconds).",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the workflow was initiated.",
    )

    # ── Validators ────────────────────────────────────────────────────────

    @field_validator("workflow_id", "batch_id", mode="before")
    @classmethod
    def require_non_empty_id(cls, v: str) -> str:
        """Reject blank ID strings."""
        if not isinstance(v, str) or not v.strip():
            raise ValueError("ID fields must be non-empty strings")
        return v.strip()

    @model_validator(mode="after")
    def populate_screening_summary(self) -> SLRWorkflowResponse:
        """Compute ``screening_summary`` from ``decisions`` if not pre-populated.

        The orchestrator normally populates ``screening_summary`` directly from
        the batch; this fallback ensures the field is always consistent with
        ``decisions``.
        """
        if self.screening_summary or not self.decisions:
            return self

        total = len(self.decisions)
        included = sum(1 for d in self.decisions if d.get("decision") == "include")
        excluded = sum(1 for d in self.decisions if d.get("decision") == "exclude")
        uncertain = sum(1 for d in self.decisions if d.get("decision") == "uncertain")

        self.screening_summary = {
            "total": total,
            "included": included,
            "excluded": excluded,
            "uncertain": uncertain,
            "inclusion_rate": round(included / total, 4) if total else 0.0,
        }
        return self

    # ── Helpers ───────────────────────────────────────────────────────────

    def is_complete(self) -> bool:
        """Return True if the workflow completed without error."""
        return self.status == WorkflowStatus.COMPLETED

    def inclusion_rate(self) -> float:
        """Return the proportion of abstracts screened as INCLUDE (0–1).

        Returns:
            Float from 0.0 to 1.0, or 0.0 when no decisions are present.
        """
        return float(self.screening_summary.get("inclusion_rate", 0.0))

    def has_uncertain_decisions(self) -> bool:
        """Return True if any abstracts were screened as UNCERTAIN.

        Uncertain decisions require human review (full-text retrieval) before
        the screening stage is complete.
        """
        return int(self.screening_summary.get("uncertain", 0)) > 0

    def decisions_by_outcome(self) -> dict[str, list[dict[str, Any]]]:
        """Return decisions grouped by screening outcome.

        Returns:
            Dict with keys ``"include"``, ``"exclude"``, ``"uncertain"``,
            each mapping to a list of decision dicts.

        Example::

            resp.decisions_by_outcome()["include"]  # → [{"pmid": "...", ...}, ...]
        """
        groups: dict[str, list[dict[str, Any]]] = {
            "include": [],
            "exclude": [],
            "uncertain": [],
        }
        for d in self.decisions:
            outcome = d.get("decision", "uncertain")
            if outcome in groups:
                groups[outcome].append(d)
            else:
                groups.setdefault(outcome, []).append(d)
        return groups

    def format_execution_time(self) -> str:
        """Return execution time as a human-readable string.

        Examples:
            ``"45.2s"``, ``"2m 3.1s"``
        """
        t = self.execution_time_seconds
        if t < 60:
            return f"{t:.1f}s"
        minutes = int(t // 60)
        secs = t % 60
        return f"{minutes}m {secs:.1f}s"
