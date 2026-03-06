"""End-to-end workflow tests for HEOR Engine.

Tests all four workflow types (BIA, CEA, Combined, SLR) plus the quick-estimate
endpoint, error handling, and workflow retrieval — at both the orchestrator layer
and the FastAPI HTTP layer (via TestClient).

Markers
-------
unit        No external API calls or R dependency; safe to run locally.
integration Requires ANTHROPIC_API_KEY (SLR) or Rscript on PATH (CEA).

Run
---
    pytest tests/test_workflows.py -v
    pytest tests/test_workflows.py -v -m unit
    pytest tests/test_workflows.py -v -m integration
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ── HEOR imports ──────────────────────────────────────────────────────────────
from agents.orchestrator import HEOROrchestrator, WorkflowError
from agents.workflow_schema import (
    BIAWorkflowRequest,
    BIAWorkflowResponse,
    CEAWorkflowRequest,
    CEAWorkflowResponse,
    CombinedWorkflowRequest,
    CombinedWorkflowResponse,
    ExportFormat,
    ReportFormat,
    SLRWorkflowRequest,
    SLRWorkflowResponse,
    WorkflowStatus,
)
from engines.bia.schema import BIAResults, ScenarioResult
from engines.markov.schema import ArmResult, MarkovResults
from engines.slr.schema import (
    Abstract,
    Confidence,
    Decision,
    PICOCriteria,
    PICOMatchItem,
    ScreeningBatch,
    ScreeningDecision,
)

# Import the FastAPI app module so we can patch its singleton orchestrator
import app.main as main_module

_client = TestClient(main_module.app)


# ══════════════════════════════════════════════════════════════════════════════
# Shared raw input dicts
# ══════════════════════════════════════════════════════════════════════════════

MINIMAL_BIA_INPUTS: dict[str, Any] = {
    "setting": "Acute NHS Trust",
    "model_year": 2026,
    "forecast_years": 3,
    "funding_source": "Trust operational budget",
    "catchment_size": 50_000,
    "eligible_pct": 10.0,
    "uptake_y1": 20.0,
    "uptake_y2": 40.0,
    "uptake_y3": 60.0,
    "workforce": [
        {"role": "Band 5 (Staff Nurse)", "minutes": 30, "frequency": "per patient"},
    ],
    "pricing_model": "per-patient",
    "price": 500.0,
}

FULL_BIA_INPUTS: dict[str, Any] = {
    **MINIMAL_BIA_INPUTS,
    "outpatient_visits": 2,
    "tests": 2,
    "admissions": 1,
    "bed_days": 3,
    "staff_time_saved": 15.0,
    "visits_reduced": 20.0,
    "complications_reduced": 10.0,
    "discounting": "off",
}

MINIMAL_CEA_INPUTS: dict[str, Any] = {
    "intervention_name": "RemoteMonitor Pro",
    "prob_death_standard": 0.05,
    "cost_standard_annual": 4_000.0,
    "utility_standard": 0.75,
    "prob_death_treatment": 0.03,
    "cost_treatment_annual": 5_500.0,
    "utility_treatment": 0.82,
    "time_horizon": 5,
    "discount_rate": 0.035,
}

PICO_DICT: dict[str, Any] = {
    "population": "Adults with type 2 diabetes",
    "intervention": "Remote continuous glucose monitoring (CGM)",
    "comparison": "Standard care or self-monitoring of blood glucose",
    "outcomes": ["HbA1c reduction", "Time in range", "Quality of life"],
    "study_types": ["RCT", "Cohort study"],
    "exclusion_criteria": ["Paediatric populations", "Type 1 diabetes only"],
}

SAMPLE_ABSTRACTS: list[dict[str, Any]] = [
    {
        "pmid": "35421876",
        "title": "Continuous glucose monitoring versus SMBG in adults with T2DM: an RCT",
        "abstract": (
            "Background: rtCGM improves glycaemic outcomes in adults with type 2 diabetes. "
            "Methods: 24-week RCT, 312 participants. "
            "Results: HbA1c reduced by 6.7 mmol/mol; time in range increased 18.7 pp. "
            "Conclusions: rtCGM significantly improves outcomes vs SMBG."
        ),
        "authors": ["Smith JA", "Patel RK"],
        "journal": "Lancet Diabetes & Endocrinology",
        "year": 2022,
    },
    {
        "pmid": "36445678",
        "title": "Flash glucose monitoring in children with T1DM: a crossover trial",
        "abstract": (
            "Objective: Compare FGM with fingerstick in paediatric T1DM. "
            "Participants: 94 children aged 6-17. "
            "Results: TIR +4.9 pp with FGM. "
            "Conclusions: FGM improves TIR in children with T1DM."
        ),
        "authors": ["Brown CE", "Thompson AF"],
        "journal": "Pediatric Diabetes",
        "year": 2022,
    },
]


# ══════════════════════════════════════════════════════════════════════════════
# Mock object factories
# ══════════════════════════════════════════════════════════════════════════════

def _make_bia_results(forecast_years: int = 3) -> BIAResults:
    """Return a realistic BIAResults object for use in mocks."""
    impacts = [10_000.0, 7_500.0, 4_000.0][:forecast_years]
    cpps = [500.0, 480.0, 450.0][:forecast_years]
    treated = [100, 200, 300][:forecast_years]
    sr = ScenarioResult(
        annual_budget_impact=impacts,
        cost_per_patient=cpps,
        total_treated_patients=treated,
    )
    return BIAResults(
        annual_budget_impact=impacts,
        cost_per_patient=cpps,
        total_treated_patients=treated,
        break_even_year=3,
        top_cost_drivers=["Workforce", "Device cost", "Outpatient visits"],
        scenarios={"conservative": sr, "base": sr, "optimistic": sr},
    )


def _make_markov_results(icer: float = 14_994.0) -> MarkovResults:
    """Return a realistic MarkovResults object for use in mocks."""
    return MarkovResults(
        standard_care=ArmResult(total_cost=21_217.0, total_qalys=2.97),
        treatment=ArmResult(total_cost=28_564.0, total_qalys=3.46),
        incremental_cost=7_347.0,
        incremental_qalys=0.49,
        icer=icer,
        interpretation="Cost-effective at NICE £25,000/QALY threshold",
        cost_effective_25k=True,
        cost_effective_35k=True,
    )


def _make_screening_decision(pmid: str, decision: str = "include") -> ScreeningDecision:
    """Return a ScreeningDecision for use in SLR mocks."""
    return ScreeningDecision(
        pmid=pmid,
        decision=Decision(decision),
        confidence=Confidence.HIGH,
        reasoning="Abstract clearly meets all PICO criteria for the review.",
        pico_match={
            "population": PICOMatchItem(matched=True, note="Adults with T2DM stated"),
            "intervention": PICOMatchItem(matched=True, note="CGM described"),
            "comparison": PICOMatchItem(matched=True, note="SMBG comparator present"),
            "outcome": PICOMatchItem(matched=True, note="HbA1c reduction reported"),
        },
        exclusion_reasons=[],
    )


def _make_enrichment_result(inputs: dict) -> dict:
    """Simulate the enrich_bia_inputs() wrapper dict with no actual changes."""
    return {
        "inputs": dict(inputs),
        "suggested_values": {},
        "warnings": [],
        "comparators": [],
        "reference_costs": {},
        "population_context": {},
        "metadata": {"source": "mock"},
    }


def _make_orch_bia_result(
    wf_id: str = "bia_20260226_120000_abcd1234",
    report_path: str | None = "/tmp/test_report.pptx",
) -> dict:
    """Return the dict that run_full_bia_workflow would produce."""
    results = _make_bia_results()
    return {
        "workflow_id": wf_id,
        "status": "completed",
        "bia_results": results.model_dump(),
        "scenarios": {k: v.model_dump() for k, v in results.scenarios.items()},
        "warnings": ["Uptake ramp is aggressive — verify with clinical leads."],
        "suggestions": [],
        "confidence": "High",
        "validation": {},
        "enrichment_meta": {},
        "report_path": report_path,
    }


def _make_orch_cea_result(
    wf_id: str = "cea_20260226_120000_ef567890",
    report_path: str | None = "/tmp/test_cea_report.pptx",
) -> dict:
    """Return the dict that run_full_cea_workflow would produce."""
    return {
        "workflow_id": wf_id,
        "status": "completed",
        "cea_results": _make_markov_results().model_dump(),
        "nice_context": {"threshold_25k": 25_000, "threshold_35k": 35_000},
        "report_path": report_path,
    }


def _make_orch_slr_result(
    wf_id: str = "slr_20260226_120000_aabb1234",
    batch_id: str = "batch-uuid-test-1234",
) -> dict:
    """Return the dict that run_slr_workflow would produce."""
    return {
        "workflow_id": wf_id,
        "status": "completed",
        "batch_id": batch_id,
        "total": 2,
        "included": 1,
        "excluded": 1,
        "uncertain": 0,
        "batch_path": None,
        "export_path": None,
    }


def _make_pico_criteria(pico: dict) -> PICOCriteria:
    """Build a PICOCriteria from the required keys of a PICO dict."""
    return PICOCriteria(
        population=pico["population"],
        intervention=pico["intervention"],
        comparison=pico["comparison"],
        outcomes=pico["outcomes"],
        study_types=pico.get("study_types", ["RCT"]),
        exclusion_criteria=pico.get("exclusion_criteria", []),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Pytest fixtures
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def bia_inputs() -> dict[str, Any]:
    return dict(MINIMAL_BIA_INPUTS)


@pytest.fixture()
def full_bia_inputs() -> dict[str, Any]:
    return dict(FULL_BIA_INPUTS)


@pytest.fixture()
def cea_inputs() -> dict[str, Any]:
    return dict(MINIMAL_CEA_INPUTS)


@pytest.fixture()
def pico_criteria() -> dict[str, Any]:
    return dict(PICO_DICT)


@pytest.fixture()
def sample_abstracts() -> list[dict[str, Any]]:
    return [dict(a) for a in SAMPLE_ABSTRACTS]


@pytest.fixture()
def orch_no_r() -> HEOROrchestrator:
    """Orchestrator with R forced off."""
    return HEOROrchestrator(config={"r_available": False})


@pytest.fixture()
def orch_with_r() -> HEOROrchestrator:
    """Orchestrator with R forced on (R may not actually be present)."""
    return HEOROrchestrator(config={"r_available": True})


# ── Common BIA patch set ───────────────────────────────────────────────────────

_BIA_ENGINE_PATCHES = {
    "agents.orchestrator.enrich_bia_inputs": None,        # replaced per-test
    "agents.orchestrator.calculate_budget_impact": None,  # replaced per-test
    "agents.orchestrator.calculate_scenarios": None,
    "agents.orchestrator.validate_clinical_sense": None,
    "agents.orchestrator.suggest_missing_inputs": None,
    "agents.orchestrator.estimate_confidence": None,
    "agents.orchestrator.validate_against_references": None,
    "agents.orchestrator.generate_bia_report": None,
}


# ══════════════════════════════════════════════════════════════════════════════
# 1. BIA workflow — orchestrator layer
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestBIAWorkflowOrchestrator:
    """Tests for run_full_bia_workflow with mocked engine dependencies."""

    def _run(
        self,
        inputs: dict,
        orch: HEOROrchestrator,
        *,
        bia_results: BIAResults | None = None,
        report_path: Path | None = Path("/tmp/rpt.pptx"),
        enrich_side_effect=None,
    ) -> dict:
        """Run workflow with standard mocks."""
        bia_results = bia_results or _make_bia_results()
        enrich_fn = enrich_side_effect or _make_enrichment_result

        with (
            patch("agents.orchestrator.enrich_bia_inputs", side_effect=enrich_fn),
            patch("agents.orchestrator.calculate_budget_impact", return_value=bia_results),
            patch("agents.orchestrator.calculate_scenarios", return_value=bia_results.scenarios),
            patch("agents.orchestrator.validate_clinical_sense", return_value=[]),
            patch("agents.orchestrator.suggest_missing_inputs", return_value=[]),
            patch("agents.orchestrator.estimate_confidence", return_value="High"),
            patch("agents.orchestrator.validate_against_references", return_value={}),
            patch("agents.orchestrator.generate_bia_report", return_value=report_path),
        ):
            return orch.run_full_bia_workflow(inputs)

    # ── Return structure ───────────────────────────────────────────────

    def test_returns_expected_keys(self, bia_inputs, orch_no_r):
        result = self._run(bia_inputs, orch_no_r)
        for key in ("workflow_id", "status", "bia_results", "scenarios", "warnings", "confidence"):
            assert key in result, f"Missing key: {key!r}"

    def test_status_is_completed(self, bia_inputs, orch_no_r):
        assert self._run(bia_inputs, orch_no_r)["status"] == "completed"

    def test_workflow_id_format(self, bia_inputs, orch_no_r):
        wf_id = self._run(bia_inputs, orch_no_r)["workflow_id"]
        assert re.match(r"^bia_\d{8}_\d{6}_[a-f0-9]{8}$", wf_id), (
            f"Unexpected workflow ID format: {wf_id!r}"
        )

    def test_bia_results_have_3_year_series(self, bia_inputs, orch_no_r):
        bia = self._run(bia_inputs, orch_no_r)["bia_results"]
        assert len(bia["annual_budget_impact"]) == 3
        assert len(bia["total_treated_patients"]) == 3
        assert len(bia["cost_per_patient"]) == 3

    def test_scenarios_contain_three_variants(self, bia_inputs, orch_no_r):
        scenarios = self._run(bia_inputs, orch_no_r)["scenarios"]
        assert set(scenarios.keys()) >= {"conservative", "base", "optimistic"}

    def test_break_even_year_in_results(self, bia_inputs, orch_no_r):
        assert self._run(bia_inputs, orch_no_r)["bia_results"]["break_even_year"] == 3

    def test_top_cost_drivers_non_empty(self, bia_inputs, orch_no_r):
        drivers = self._run(bia_inputs, orch_no_r)["bia_results"]["top_cost_drivers"]
        assert isinstance(drivers, list) and len(drivers) > 0

    def test_report_path_returned(self, bia_inputs, orch_no_r):
        result = self._run(bia_inputs, orch_no_r, report_path=Path("/tmp/rpt.pptx"))
        assert result["report_path"] is not None
        assert result["report_path"].endswith(".pptx")

    # ── Enrichment behaviour ───────────────────────────────────────────

    def test_enrichment_suggested_values_merged_into_bia_inputs(self, bia_inputs, orch_no_r):
        """suggested_values from enrich_bia_inputs must be applied before BIA runs."""
        ENRICHED_PRICE = 999.0
        captured: list = []

        def enrichment_with_suggestion(inp):
            return {**_make_enrichment_result(inp), "suggested_values": {"price": ENRICHED_PRICE}}

        def capture_inputs(bia_inputs_obj):
            captured.append(bia_inputs_obj)
            return _make_bia_results()

        with (
            patch("agents.orchestrator.enrich_bia_inputs", side_effect=enrichment_with_suggestion),
            patch("agents.orchestrator.calculate_budget_impact", side_effect=capture_inputs),
            patch("agents.orchestrator.calculate_scenarios", return_value={}),
            patch("agents.orchestrator.validate_clinical_sense", return_value=[]),
            patch("agents.orchestrator.suggest_missing_inputs", return_value=[]),
            patch("agents.orchestrator.estimate_confidence", return_value="High"),
            patch("agents.orchestrator.validate_against_references", return_value={}),
            patch("agents.orchestrator.generate_bia_report", return_value=None),
        ):
            orch_no_r.run_full_bia_workflow(bia_inputs)

        assert captured, "calculate_budget_impact was never called"
        assert captured[0].price == ENRICHED_PRICE

    def test_enrich_failure_falls_back_to_raw_inputs(self, bia_inputs, orch_no_r):
        """If enrichment raises, the workflow continues with the original inputs."""
        result = self._run(bia_inputs, orch_no_r, enrich_side_effect=ConnectionError("timeout"))
        assert result["status"] == "completed"

    # ── Persistence ────────────────────────────────────────────────────

    def test_workflow_state_persisted_to_disk(self, bia_inputs, orch_no_r, tmp_path):
        orch_no_r._workflows_dir = tmp_path
        result = self._run(bia_inputs, orch_no_r)
        wf_file = tmp_path / f"{result['workflow_id']}.json"
        assert wf_file.exists()
        state = json.loads(wf_file.read_text())
        assert state["workflow_type"] == "bia"
        assert "steps" in state

    def test_workflow_state_has_named_steps(self, bia_inputs, orch_no_r, tmp_path):
        orch_no_r._workflows_dir = tmp_path
        result = self._run(bia_inputs, orch_no_r)
        state = orch_no_r.get_workflow_status(result["workflow_id"])
        step_names = [s["step"] for s in state["steps"]]
        assert "validate_inputs" in step_names
        assert "calculate_bia" in step_names
        assert "generate_report" in step_names

    # ── Soft failure: report generation ───────────────────────────────

    def test_report_generation_failure_is_soft(self, bia_inputs, orch_no_r):
        """A report crash should not abort the workflow."""
        result = self._run(bia_inputs, orch_no_r, report_path=None,
                           enrich_side_effect=_make_enrichment_result)
        # Override report mock: simulate exception inside _run helper
        with (
            patch("agents.orchestrator.enrich_bia_inputs", side_effect=_make_enrichment_result),
            patch("agents.orchestrator.calculate_budget_impact", return_value=_make_bia_results()),
            patch("agents.orchestrator.calculate_scenarios", return_value={}),
            patch("agents.orchestrator.validate_clinical_sense", return_value=[]),
            patch("agents.orchestrator.suggest_missing_inputs", return_value=[]),
            patch("agents.orchestrator.estimate_confidence", return_value="High"),
            patch("agents.orchestrator.validate_against_references", return_value={}),
            patch("agents.orchestrator.generate_bia_report", side_effect=RuntimeError("pptx crashed")),
        ):
            result = orch_no_r.run_full_bia_workflow(bia_inputs)
        assert result["status"] == "completed"
        assert result["report_path"] is None

    # ── Error handling ─────────────────────────────────────────────────

    def test_missing_required_field_raises_workflow_error(self, orch_no_r):
        bad = {k: v for k, v in MINIMAL_BIA_INPUTS.items() if k != "price"}
        with pytest.raises(WorkflowError) as exc_info:
            orch_no_r.run_full_bia_workflow(bad)
        assert exc_info.value.step == "validate_inputs"

    def test_workflow_error_has_bia_workflow_id(self, orch_no_r):
        bad = {k: v for k, v in MINIMAL_BIA_INPUTS.items() if k != "catchment_size"}
        with pytest.raises(WorkflowError) as exc_info:
            orch_no_r.run_full_bia_workflow(bad)
        assert exc_info.value.workflow_id.startswith("bia_")

    def test_multiple_missing_fields_all_reported(self, orch_no_r):
        bad = {"setting": "Acute NHS Trust"}  # missing almost everything
        with pytest.raises(WorkflowError) as exc_info:
            orch_no_r.run_full_bia_workflow(bad)
        error_msg = str(exc_info.value)
        # At least one of the many missing fields should appear
        assert "Missing required field" in error_msg


# ══════════════════════════════════════════════════════════════════════════════
# 2. CEA workflow — orchestrator layer
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestCEAWorkflowOrchestrator:
    """Tests for run_full_cea_workflow with mocked R engine."""

    def _run(
        self,
        inputs: dict,
        orch: HEOROrchestrator,
        markov_result: MarkovResults | None = None,
        nice_ctx: dict | None = None,
    ) -> dict:
        markov_result = markov_result or _make_markov_results()
        nice_ctx = nice_ctx or {}
        with (
            patch("agents.orchestrator.run_markov_with_validation", return_value=markov_result),
            patch("agents.orchestrator.get_nice_threshold_context", return_value=nice_ctx),
            patch("agents.orchestrator.generate_cea_report", return_value=Path("/tmp/cea.pptx")),
        ):
            return orch.run_full_cea_workflow(inputs)

    def test_r_not_installed_raises_at_check_r_step(self, cea_inputs, orch_no_r):
        with pytest.raises(WorkflowError) as exc_info:
            orch_no_r.run_full_cea_workflow(cea_inputs)
        assert exc_info.value.step == "check_r"

    def test_r_not_installed_error_has_cea_workflow_id(self, cea_inputs, orch_no_r):
        with pytest.raises(WorkflowError) as exc_info:
            orch_no_r.run_full_cea_workflow(cea_inputs)
        assert exc_info.value.workflow_id.startswith("cea_")

    def test_workflow_id_has_cea_prefix(self, cea_inputs, orch_with_r):
        assert self._run(cea_inputs, orch_with_r)["workflow_id"].startswith("cea_")

    def test_status_is_completed(self, cea_inputs, orch_with_r):
        assert self._run(cea_inputs, orch_with_r)["status"] == "completed"

    def test_icer_in_cea_results(self, cea_inputs, orch_with_r):
        result = self._run(cea_inputs, orch_with_r, markov_result=_make_markov_results(icer=22_500.0))
        assert result["cea_results"]["icer"] == pytest.approx(22_500.0)

    def test_interpretation_present(self, cea_inputs, orch_with_r):
        result = self._run(cea_inputs, orch_with_r)
        assert result["cea_results"]["interpretation"] != ""

    def test_cost_effective_flags_present(self, cea_inputs, orch_with_r):
        cea = self._run(cea_inputs, orch_with_r)["cea_results"]
        assert "cost_effective_25k" in cea
        assert "cost_effective_35k" in cea

    def test_nice_context_returned(self, cea_inputs, orch_with_r):
        nice_ctx = {"standard_threshold": 25_000}
        result = self._run(cea_inputs, orch_with_r, nice_ctx=nice_ctx)
        assert result["nice_context"] == nice_ctx

    def test_missing_required_field_raises(self, orch_with_r):
        bad = {k: v for k, v in MINIMAL_CEA_INPUTS.items() if k != "prob_death_standard"}
        with pytest.raises(WorkflowError) as exc_info:
            orch_with_r.run_full_cea_workflow(bad)
        assert exc_info.value.step == "validate_inputs"

    def test_report_path_in_result(self, cea_inputs, orch_with_r):
        result = self._run(cea_inputs, orch_with_r)
        assert result["report_path"] is not None


# ══════════════════════════════════════════════════════════════════════════════
# 3. Combined BIA + CEA workflow — orchestrator layer
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestCombinedWorkflowOrchestrator:
    """Tests for run_combined_workflow with mocked sub-engines."""

    def _run(
        self,
        bia_inputs: dict,
        orch: HEOROrchestrator,
        mortality_reduction: float = 0.03,
        utility_gain: float = 0.08,
    ) -> dict:
        bia_results = _make_bia_results()
        with (
            patch("agents.orchestrator.enrich_bia_inputs", side_effect=_make_enrichment_result),
            patch("agents.orchestrator.calculate_budget_impact", return_value=bia_results),
            patch("agents.orchestrator.calculate_scenarios", return_value=bia_results.scenarios),
            patch("agents.orchestrator.validate_clinical_sense", return_value=[]),
            patch("agents.orchestrator.suggest_missing_inputs", return_value=[]),
            patch("agents.orchestrator.estimate_confidence", return_value="High"),
            patch("agents.orchestrator.validate_against_references", return_value={}),
            patch("agents.orchestrator.generate_bia_report", return_value=Path("/tmp/bia.pptx")),
            patch("agents.orchestrator.run_markov_with_validation", return_value=_make_markov_results()),
            patch("agents.orchestrator.get_nice_threshold_context", return_value={}),
            patch("agents.orchestrator.generate_cea_report", return_value=Path("/tmp/cea.pptx")),
        ):
            return orch.run_combined_workflow(
                bia_inputs=bia_inputs,
                mortality_reduction=mortality_reduction,
                utility_gain=utility_gain,
            )

    def test_workflow_id_has_combined_prefix(self, bia_inputs, orch_with_r):
        assert self._run(bia_inputs, orch_with_r)["workflow_id"].startswith("combined_")

    def test_status_is_completed(self, bia_inputs, orch_with_r):
        assert self._run(bia_inputs, orch_with_r)["status"] == "completed"

    def test_both_results_present_and_non_empty(self, bia_inputs, orch_with_r):
        result = self._run(bia_inputs, orch_with_r)
        assert result["bia_results"]
        assert result["cea_results"]

    def test_cea_sub_workflow_id_present(self, bia_inputs, orch_with_r):
        result = self._run(bia_inputs, orch_with_r)
        assert result.get("cea_workflow_id", "").startswith("cea_")

    def test_markov_cost_standard_derived_from_bia_cpp(self, bia_inputs, orch_with_r):
        """cost_standard_annual passed to Markov = Year-1 cost_per_patient from BIA."""
        bia_results = _make_bia_results()
        expected_cost_standard = bia_results.cost_per_patient[0]
        captured: list[dict] = []

        def capture(inputs: dict):
            captured.append(dict(inputs))
            return _make_markov_results()

        with (
            patch("agents.orchestrator.enrich_bia_inputs", side_effect=_make_enrichment_result),
            patch("agents.orchestrator.calculate_budget_impact", return_value=bia_results),
            patch("agents.orchestrator.calculate_scenarios", return_value=bia_results.scenarios),
            patch("agents.orchestrator.validate_clinical_sense", return_value=[]),
            patch("agents.orchestrator.suggest_missing_inputs", return_value=[]),
            patch("agents.orchestrator.estimate_confidence", return_value="High"),
            patch("agents.orchestrator.validate_against_references", return_value={}),
            patch("agents.orchestrator.generate_bia_report", return_value=None),
            patch("agents.orchestrator.run_markov_with_validation", side_effect=capture),
            patch("agents.orchestrator.get_nice_threshold_context", return_value={}),
            patch("agents.orchestrator.generate_cea_report", return_value=None),
        ):
            orch_with_r.run_combined_workflow(bia_inputs, 0.03, 0.08)

        assert captured, "run_markov_with_validation was not called"
        assert captured[0]["cost_standard_annual"] == pytest.approx(expected_cost_standard)

    def test_mortality_reduction_applied_to_markov_params(self, bia_inputs, orch_with_r):
        """prob_death_treatment = baseline_mortality − mortality_reduction."""
        baseline = orch_with_r._config.get("baseline_mortality", 0.05)
        reduction = 0.02
        captured: list[dict] = []

        def capture(inputs: dict):
            captured.append(dict(inputs))
            return _make_markov_results()

        with (
            patch("agents.orchestrator.enrich_bia_inputs", side_effect=_make_enrichment_result),
            patch("agents.orchestrator.calculate_budget_impact", return_value=_make_bia_results()),
            patch("agents.orchestrator.calculate_scenarios", return_value={}),
            patch("agents.orchestrator.validate_clinical_sense", return_value=[]),
            patch("agents.orchestrator.suggest_missing_inputs", return_value=[]),
            patch("agents.orchestrator.estimate_confidence", return_value="High"),
            patch("agents.orchestrator.validate_against_references", return_value={}),
            patch("agents.orchestrator.generate_bia_report", return_value=None),
            patch("agents.orchestrator.run_markov_with_validation", side_effect=capture),
            patch("agents.orchestrator.get_nice_threshold_context", return_value={}),
            patch("agents.orchestrator.generate_cea_report", return_value=None),
        ):
            orch_with_r.run_combined_workflow(bia_inputs, reduction, 0.08)

        assert captured
        assert captured[0]["prob_death_treatment"] == pytest.approx(baseline - reduction)

    def test_cea_failure_when_r_unavailable_raises(self, bia_inputs, orch_no_r):
        """Combined workflow raises WorkflowError when CEA sub-workflow hits check_r."""
        with (
            patch("agents.orchestrator.enrich_bia_inputs", side_effect=_make_enrichment_result),
            patch("agents.orchestrator.calculate_budget_impact", return_value=_make_bia_results()),
            patch("agents.orchestrator.calculate_scenarios", return_value={}),
            patch("agents.orchestrator.validate_clinical_sense", return_value=[]),
            patch("agents.orchestrator.suggest_missing_inputs", return_value=[]),
            patch("agents.orchestrator.estimate_confidence", return_value="High"),
            patch("agents.orchestrator.validate_against_references", return_value={}),
            patch("agents.orchestrator.generate_bia_report", return_value=None),
        ):
            with pytest.raises(WorkflowError) as exc_info:
                orch_no_r.run_combined_workflow(bia_inputs, 0.03, 0.08)

        assert "run_cea" in str(exc_info.value) or "check_r" in str(exc_info.value)


# ══════════════════════════════════════════════════════════════════════════════
# 4. SLR workflow — orchestrator layer
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestSLRWorkflowOrchestrator:
    """Tests for run_slr_workflow with mocked Claude API."""

    def _run(
        self,
        pico: dict,
        abstracts: list[dict],
        orch: HEOROrchestrator,
        decisions: list[ScreeningDecision] | None = None,
    ) -> dict:
        if decisions is None:
            decisions = [
                _make_screening_decision("35421876", "include"),
                _make_screening_decision("36445678", "exclude"),
            ]
        mock_batch = ScreeningBatch(
            batch_id="test-batch-uuid-1234",
            pico_criteria=_make_pico_criteria(pico),
            abstracts=[Abstract(**a) for a in abstracts],
        )
        with (
            patch("agents.orchestrator.create_screening_batch", return_value=mock_batch),
            patch("agents.orchestrator.screen_abstracts", return_value=decisions),
            patch("agents.orchestrator.save_batch", return_value=Path("/tmp/batch.json")),
            patch("agents.orchestrator.export_screening_results", return_value="/tmp/export.csv"),
        ):
            return orch.run_slr_workflow(pico, abstracts)

    def test_workflow_id_has_slr_prefix(self, pico_criteria, sample_abstracts, orch_no_r):
        assert self._run(pico_criteria, sample_abstracts, orch_no_r)["workflow_id"].startswith("slr_")

    def test_status_is_completed(self, pico_criteria, sample_abstracts, orch_no_r):
        assert self._run(pico_criteria, sample_abstracts, orch_no_r)["status"] == "completed"

    def test_batch_id_in_result(self, pico_criteria, sample_abstracts, orch_no_r):
        assert self._run(pico_criteria, sample_abstracts, orch_no_r)["batch_id"] == "test-batch-uuid-1234"

    def test_decision_counts_are_correct(self, pico_criteria, sample_abstracts, orch_no_r):
        result = self._run(pico_criteria, sample_abstracts, orch_no_r)
        assert result["total"] == 2
        assert result["included"] == 1
        assert result["excluded"] == 1
        assert result["uncertain"] == 0

    def test_uncertain_decisions_counted_separately(self, pico_criteria, orch_no_r):
        three_abstracts = [
            dict(SAMPLE_ABSTRACTS[0], pmid="111"),
            dict(SAMPLE_ABSTRACTS[1], pmid="222"),
            {
                "pmid": "333", "title": "Irrelevant study", "abstract": "Animal study on mice.",
                "authors": ["Doe J"], "journal": "JAMA", "year": 2021,
            },
        ]
        decisions = [
            _make_screening_decision("111", "include"),
            _make_screening_decision("222", "uncertain"),
            _make_screening_decision("333", "exclude"),
        ]
        result = self._run(pico_criteria, three_abstracts, orch_no_r, decisions=decisions)
        assert result["total"] == 3
        assert result["included"] == 1
        assert result["uncertain"] == 1
        assert result["excluded"] == 1

    def test_export_path_returned(self, pico_criteria, sample_abstracts, orch_no_r):
        assert self._run(pico_criteria, sample_abstracts, orch_no_r)["export_path"] == "/tmp/export.csv"

    def test_batch_path_returned(self, pico_criteria, sample_abstracts, orch_no_r):
        assert self._run(pico_criteria, sample_abstracts, orch_no_r)["batch_path"] == "/tmp/batch.json"

    def test_missing_pico_population_raises(self, sample_abstracts, orch_no_r):
        bad_pico = {k: v for k, v in PICO_DICT.items() if k != "population"}
        with pytest.raises(WorkflowError) as exc_info:
            orch_no_r.run_slr_workflow(bad_pico, sample_abstracts)
        assert exc_info.value.step == "validate_pico"

    def test_empty_outcomes_list_raises(self, sample_abstracts, orch_no_r):
        bad_pico = {**PICO_DICT, "outcomes": []}
        with pytest.raises(WorkflowError):
            orch_no_r.run_slr_workflow(bad_pico, sample_abstracts)

    def test_screen_abstracts_called_with_correct_batch_size(
        self, pico_criteria, sample_abstracts, orch_no_r
    ):
        mock_screen = MagicMock(
            return_value=[_make_screening_decision("35421876", "include")]
        )
        mock_batch = ScreeningBatch(
            batch_id="batch-size-test",
            pico_criteria=_make_pico_criteria(pico_criteria),
            abstracts=[Abstract(**sample_abstracts[0])],
        )
        with (
            patch("agents.orchestrator.create_screening_batch", return_value=mock_batch),
            patch("agents.orchestrator.screen_abstracts", mock_screen),
            patch("agents.orchestrator.save_batch", return_value=Path("/tmp/b.json")),
            patch("agents.orchestrator.export_screening_results", return_value="/tmp/e.csv"),
        ):
            orch_no_r.run_slr_workflow(pico_criteria, [sample_abstracts[0]], batch_size=5)

        assert mock_screen.called
        assert mock_screen.call_args.kwargs.get("batch_size") == 5

    def test_missing_api_key_raises_workflow_error(self, pico_criteria, sample_abstracts, orch_no_r):
        mock_batch = ScreeningBatch(
            batch_id="no-key-test",
            pico_criteria=_make_pico_criteria(pico_criteria),
            abstracts=[Abstract(**a) for a in sample_abstracts],
        )
        with (
            patch("agents.orchestrator.create_screening_batch", return_value=mock_batch),
            patch(
                "agents.orchestrator.screen_abstracts",
                side_effect=EnvironmentError("ANTHROPIC_API_KEY not set"),
            ),
        ):
            with pytest.raises(WorkflowError) as exc_info:
                orch_no_r.run_slr_workflow(pico_criteria, sample_abstracts)
        assert exc_info.value.step == "screen_abstracts"


# ══════════════════════════════════════════════════════════════════════════════
# 5. Workflow retrieval — orchestrator + HTTP
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestWorkflowRetrieval:
    """Tests for get_workflow_status, GET /api/workflows/{id}, and GET /api/workflows."""

    def _run_bia(self, orch: HEOROrchestrator, inputs: dict | None = None) -> str:
        """Run a mocked BIA workflow and return its ID."""
        inputs = inputs or dict(MINIMAL_BIA_INPUTS)
        with (
            patch("agents.orchestrator.enrich_bia_inputs", side_effect=_make_enrichment_result),
            patch("agents.orchestrator.calculate_budget_impact", return_value=_make_bia_results()),
            patch("agents.orchestrator.calculate_scenarios", return_value={}),
            patch("agents.orchestrator.validate_clinical_sense", return_value=[]),
            patch("agents.orchestrator.suggest_missing_inputs", return_value=[]),
            patch("agents.orchestrator.estimate_confidence", return_value="High"),
            patch("agents.orchestrator.validate_against_references", return_value={}),
            patch("agents.orchestrator.generate_bia_report", return_value=None),
        ):
            return orch.run_full_bia_workflow(inputs)["workflow_id"]

    def test_get_status_returns_workflow_type(self, bia_inputs, orch_no_r, tmp_path):
        orch_no_r._workflows_dir = tmp_path
        wf_id = self._run_bia(orch_no_r, bia_inputs)
        state = orch_no_r.get_workflow_status(wf_id)
        assert state["workflow_type"] == "bia"

    def test_get_status_has_steps(self, bia_inputs, orch_no_r, tmp_path):
        orch_no_r._workflows_dir = tmp_path
        wf_id = self._run_bia(orch_no_r, bia_inputs)
        state = orch_no_r.get_workflow_status(wf_id)
        assert len(state["steps"]) > 0
        step_names = {s["step"] for s in state["steps"]}
        assert "validate_inputs" in step_names
        assert "calculate_bia" in step_names

    def test_get_status_loaded_from_disk_after_memory_cleared(self, orch_no_r, tmp_path):
        orch_no_r._workflows_dir = tmp_path
        fake_id = "bia_20260226_000000_deadbeef"
        fake_state = {
            "workflow_id": fake_id, "workflow_type": "bia", "status": "completed",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "inputs_summary": {}, "steps": [],
        }
        (tmp_path / f"{fake_id}.json").write_text(json.dumps(fake_state), encoding="utf-8")
        state = orch_no_r.get_workflow_status(fake_id)
        assert state["status"] == "completed"

    def test_nonexistent_workflow_returns_error_key(self, orch_no_r, tmp_path):
        orch_no_r._workflows_dir = tmp_path
        state = orch_no_r.get_workflow_status("does_not_exist_xyz")
        assert "error" in state

    def test_http_get_workflow_returns_200(self):
        mock_state = {
            "workflow_id": "bia_20260226_120000_abcd1234", "workflow_type": "bia",
            "status": "completed", "created_at": "2026-02-26T12:00:00+00:00",
            "updated_at": "2026-02-26T12:00:05+00:00", "inputs_summary": {}, "steps": [],
        }
        with patch.object(main_module._orchestrator, "get_workflow_status", return_value=mock_state):
            resp = _client.get("/api/workflows/bia_20260226_120000_abcd1234")
        assert resp.status_code == 200
        assert resp.json()["workflow_id"] == "bia_20260226_120000_abcd1234"

    def test_http_get_nonexistent_workflow_returns_404(self):
        with patch.object(main_module._orchestrator, "get_workflow_status",
                          return_value={"error": "not found"}):
            resp = _client.get("/api/workflows/does_not_exist")
        assert resp.status_code == 404

    def test_http_list_workflows_response_keys(self):
        resp = _client.get("/api/workflows")
        assert resp.status_code == 200
        data = resp.json()
        for key in ("workflows", "total", "page", "page_size", "total_pages"):
            assert key in data

    def test_http_list_workflows_filter_by_type(self, tmp_path, monkeypatch):
        for wf_type, wf_id in [
            ("bia", "bia_20260226_120000_aaaaaaaa"),
            ("cea", "cea_20260226_120000_bbbbbbbb"),
        ]:
            state = {
                "workflow_id": wf_id, "workflow_type": wf_type, "status": "completed",
                "created_at": "2026-02-26T12:00:00+00:00",
                "updated_at": "2026-02-26T12:00:01+00:00",
                "inputs_summary": {}, "steps": [],
            }
            (tmp_path / f"{wf_id}.json").write_text(json.dumps(state), encoding="utf-8")

        monkeypatch.setattr(main_module, "_WORKFLOWS_DIR", tmp_path)
        resp = _client.get("/api/workflows?type=bia")
        assert resp.status_code == 200
        assert all(w["workflow_type"] == "bia" for w in resp.json()["workflows"])

    def test_http_list_workflows_filter_by_status(self, tmp_path, monkeypatch):
        for i, status in enumerate(["completed", "failed", "completed"]):
            wf_id = f"bia_20260226_12000{i}_{'a' * 8}"
            state = {
                "workflow_id": wf_id, "workflow_type": "bia", "status": status,
                "created_at": "2026-02-26T12:00:00+00:00",
                "updated_at": "2026-02-26T12:00:01+00:00",
                "inputs_summary": {}, "steps": [],
            }
            (tmp_path / f"{wf_id}.json").write_text(json.dumps(state), encoding="utf-8")

        monkeypatch.setattr(main_module, "_WORKFLOWS_DIR", tmp_path)
        resp = _client.get("/api/workflows?status=failed")
        assert resp.status_code == 200
        workflows = resp.json()["workflows"]
        assert len(workflows) == 1
        assert workflows[0]["status"] == "failed"

    def test_http_list_workflows_pagination(self, tmp_path, monkeypatch):
        for i in range(5):
            wf_id = f"bia_2026022{i}_120000_{'a' * 8}"
            state = {
                "workflow_id": wf_id, "workflow_type": "bia", "status": "completed",
                "created_at": f"2026-02-2{i}T12:00:00+00:00",
                "updated_at": f"2026-02-2{i}T12:00:01+00:00",
                "inputs_summary": {}, "steps": [],
            }
            (tmp_path / f"{wf_id}.json").write_text(json.dumps(state), encoding="utf-8")

        monkeypatch.setattr(main_module, "_WORKFLOWS_DIR", tmp_path)
        resp = _client.get("/api/workflows?page=1&page_size=2")
        data = resp.json()
        assert data["page"] == 1
        assert data["page_size"] == 2
        assert len(data["workflows"]) == 2
        assert data["total"] == 5
        assert data["has_next"] is True


# ══════════════════════════════════════════════════════════════════════════════
# 6. HTTP — POST /api/workflows/bia
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestBIAWorkflowHTTP:
    """Tests for the BIA workflow HTTP endpoint."""

    def test_returns_200(self):
        with patch.object(main_module._orchestrator, "run_full_bia_workflow",
                          return_value=_make_orch_bia_result()):
            resp = _client.post("/api/workflows/bia", json={"inputs": MINIMAL_BIA_INPUTS})
        assert resp.status_code == 200

    def test_response_has_workflow_id(self):
        with patch.object(main_module._orchestrator, "run_full_bia_workflow",
                          return_value=_make_orch_bia_result(wf_id="bia_20260226_130000_deadbeef")):
            resp = _client.post("/api/workflows/bia", json={"inputs": MINIMAL_BIA_INPUTS})
        assert resp.json()["workflow_id"] == "bia_20260226_130000_deadbeef"

    def test_response_schema_keys_present(self):
        with patch.object(main_module._orchestrator, "run_full_bia_workflow",
                          return_value=_make_orch_bia_result()):
            resp = _client.post("/api/workflows/bia", json={"inputs": MINIMAL_BIA_INPUTS})
        data = resp.json()
        for key in ("workflow_id", "submission_id", "status", "results",
                    "enrichment_applied", "warnings", "execution_time_seconds"):
            assert key in data, f"Missing key: {key!r}"

    def test_execution_time_header_present(self):
        with patch.object(main_module._orchestrator, "run_full_bia_workflow",
                          return_value=_make_orch_bia_result()):
            resp = _client.post("/api/workflows/bia", json={"inputs": MINIMAL_BIA_INPUTS})
        assert "x-execution-time-ms" in resp.headers

    def test_status_partial_when_report_requested_but_absent(self):
        with patch.object(main_module._orchestrator, "run_full_bia_workflow",
                          return_value=_make_orch_bia_result(report_path=None)):
            resp = _client.post("/api/workflows/bia",
                                json={"inputs": MINIMAL_BIA_INPUTS, "generate_report": True})
        assert resp.status_code == 200
        assert resp.json()["status"] == WorkflowStatus.PARTIAL.value

    def test_status_completed_when_report_present(self):
        with patch.object(main_module._orchestrator, "run_full_bia_workflow",
                          return_value=_make_orch_bia_result(report_path="/tmp/rpt.pptx")):
            resp = _client.post("/api/workflows/bia", json={"inputs": MINIMAL_BIA_INPUTS})
        assert resp.json()["status"] == WorkflowStatus.COMPLETED.value

    def test_report_url_format(self):
        with patch.object(main_module._orchestrator, "run_full_bia_workflow",
                          return_value=_make_orch_bia_result(wf_id="bia_20260226_130000_cafebabe")):
            resp = _client.post("/api/workflows/bia", json={"inputs": MINIMAL_BIA_INPUTS})
        assert resp.json()["report_url"] == "/api/workflows/bia_20260226_130000_cafebabe/report"

    def test_empty_inputs_returns_422(self):
        resp = _client.post("/api/workflows/bia", json={"inputs": {}})
        assert resp.status_code == 422

    def test_validate_inputs_step_failure_returns_422(self):
        exc = WorkflowError("Missing: 'price'", workflow_id="bia_t", step="validate_inputs")
        with patch.object(main_module._orchestrator, "run_full_bia_workflow", side_effect=exc):
            resp = _client.post("/api/workflows/bia",
                                json={"inputs": {k: v for k, v in MINIMAL_BIA_INPUTS.items()
                                                 if k != "price"}})
        assert resp.status_code == 422

    def test_parse_inputs_step_failure_returns_422(self):
        exc = WorkflowError("Pydantic error", workflow_id="bia_t", step="parse_inputs")
        with patch.object(main_module._orchestrator, "run_full_bia_workflow", side_effect=exc):
            resp = _client.post("/api/workflows/bia", json={"inputs": MINIMAL_BIA_INPUTS})
        assert resp.status_code == 422

    def test_calculate_bia_step_failure_returns_500(self):
        exc = WorkflowError("calc failed", workflow_id="bia_t", step="calculate_bia")
        with patch.object(main_module._orchestrator, "run_full_bia_workflow", side_effect=exc):
            resp = _client.post("/api/workflows/bia", json={"inputs": MINIMAL_BIA_INPUTS})
        assert resp.status_code == 500
        assert resp.json()["detail"]["step"] == "calculate_bia"

    def test_unexpected_exception_returns_500(self):
        with patch.object(main_module._orchestrator, "run_full_bia_workflow",
                          side_effect=RuntimeError("unexpected")):
            resp = _client.post("/api/workflows/bia", json={"inputs": MINIMAL_BIA_INPUTS})
        assert resp.status_code == 500

    def test_enrichment_applied_key_present(self):
        with patch.object(main_module._orchestrator, "run_full_bia_workflow",
                          return_value=_make_orch_bia_result()):
            resp = _client.post("/api/workflows/bia", json={"inputs": MINIMAL_BIA_INPUTS})
        assert "confidence_rating" in resp.json()["enrichment_applied"]


# ══════════════════════════════════════════════════════════════════════════════
# 7. HTTP — POST /api/workflows/cea
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestCEAWorkflowHTTP:
    """Tests for the CEA workflow HTTP endpoint."""

    _BASE_BODY = {"inputs": MINIMAL_CEA_INPUTS, "intervention_name": "RemoteMonitor Pro"}

    def test_returns_200(self):
        with patch.object(main_module._orchestrator, "run_full_cea_workflow",
                          return_value=_make_orch_cea_result()):
            resp = _client.post("/api/workflows/cea", json=self._BASE_BODY)
        assert resp.status_code == 200

    def test_response_schema_keys(self):
        with patch.object(main_module._orchestrator, "run_full_cea_workflow",
                          return_value=_make_orch_cea_result()):
            resp = _client.post("/api/workflows/cea", json=self._BASE_BODY)
        data = resp.json()
        for key in ("workflow_id", "status", "results", "validation_report", "execution_time_seconds"):
            assert key in data

    def test_r_not_installed_returns_503(self):
        exc = WorkflowError("R not installed", workflow_id="cea_t", step="check_r")
        with patch.object(main_module._orchestrator, "run_full_cea_workflow", side_effect=exc):
            resp = _client.post("/api/workflows/cea", json=self._BASE_BODY)
        assert resp.status_code == 503

    def test_missing_required_field_returns_422(self):
        exc = WorkflowError("Missing field", workflow_id="cea_t", step="validate_inputs")
        with patch.object(main_module._orchestrator, "run_full_cea_workflow", side_effect=exc):
            resp = _client.post("/api/workflows/cea", json=self._BASE_BODY)
        assert resp.status_code == 422

    def test_icer_in_results(self):
        with patch.object(main_module._orchestrator, "run_full_cea_workflow",
                          return_value=_make_orch_cea_result()):
            resp = _client.post("/api/workflows/cea", json=self._BASE_BODY)
        assert isinstance(resp.json()["results"].get("icer"), (int, float))

    def test_empty_intervention_name_returns_422(self):
        resp = _client.post("/api/workflows/cea",
                            json={**self._BASE_BODY, "intervention_name": ""})
        assert resp.status_code == 422

    def test_empty_inputs_returns_422(self):
        resp = _client.post("/api/workflows/cea",
                            json={"inputs": {}, "intervention_name": "Test"})
        assert resp.status_code == 422

    def test_execution_time_header_present(self):
        with patch.object(main_module._orchestrator, "run_full_cea_workflow",
                          return_value=_make_orch_cea_result()):
            resp = _client.post("/api/workflows/cea", json=self._BASE_BODY)
        assert "x-execution-time-ms" in resp.headers


# ══════════════════════════════════════════════════════════════════════════════
# 8. HTTP — POST /api/workflows/combined
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestCombinedWorkflowHTTP:
    """Tests for the combined BIA + CEA workflow HTTP endpoint."""

    _BASE_BODY = {
        "bia_inputs": MINIMAL_BIA_INPUTS,
        "mortality_reduction_pct": 3.0,
        "utility_gain": 0.08,
        "intervention_name": "RemoteMonitor Pro",
    }

    def _mock_combined_result(self) -> dict:
        bia_res = _make_bia_results()
        return {
            "workflow_id": "combined_20260226_120000_abcdef01",
            "status": "completed",
            "bia_results": bia_res.model_dump(),
            "scenarios": {k: v.model_dump() for k, v in bia_res.scenarios.items()},
            "cea_results": _make_markov_results().model_dump(),
            "nice_context": {},
            "cea_workflow_id": "cea_20260226_120001_beef1234",
            "report_path": "/tmp/bia.pptx",
            "cea_report_path": "/tmp/cea.pptx",
            "combined_report_path": None,
            "warnings": [],
            "suggestions": [],
            "confidence": "High",
            "validation": {},
            "enrichment_meta": {},
        }

    def test_returns_200(self):
        with patch.object(main_module._orchestrator, "run_combined_workflow",
                          return_value=self._mock_combined_result()):
            resp = _client.post("/api/workflows/combined", json=self._BASE_BODY)
        assert resp.status_code == 200

    def test_both_results_present(self):
        with patch.object(main_module._orchestrator, "run_combined_workflow",
                          return_value=self._mock_combined_result()):
            resp = _client.post("/api/workflows/combined", json=self._BASE_BODY)
        data = resp.json()
        assert data["bia_results"]
        assert data["cea_results"]

    def test_executive_summary_auto_generated(self):
        with patch.object(main_module._orchestrator, "run_combined_workflow",
                          return_value=self._mock_combined_result()):
            resp = _client.post("/api/workflows/combined", json=self._BASE_BODY)
        summary = resp.json().get("executive_summary", "")
        assert isinstance(summary, str) and len(summary) > 0

    def test_cea_failure_returns_206_with_partial_status(self):
        exc = WorkflowError("CEA sub-workflow failed", workflow_id="combined_t", step="run_cea")
        bia_res = _make_bia_results()
        saved = {"workflow_id": "combined_t", "bia_results": bia_res.model_dump(), "scenarios": {}}
        with (
            patch.object(main_module._orchestrator, "run_combined_workflow", side_effect=exc),
            patch.object(main_module._orchestrator, "get_workflow_status", return_value=saved),
        ):
            resp = _client.post("/api/workflows/combined", json=self._BASE_BODY)
        assert resp.status_code == 206
        assert resp.json()["status"] == WorkflowStatus.PARTIAL.value

    def test_bia_validation_failure_returns_422(self):
        exc = WorkflowError("Bad BIA inputs", workflow_id="combined_t", step="validate_inputs")
        with patch.object(main_module._orchestrator, "run_combined_workflow", side_effect=exc):
            resp = _client.post("/api/workflows/combined", json=self._BASE_BODY)
        assert resp.status_code == 422

    def test_empty_bia_inputs_returns_422(self):
        resp = _client.post("/api/workflows/combined",
                            json={**self._BASE_BODY, "bia_inputs": {}})
        assert resp.status_code == 422

    def test_mortality_over_100_returns_422(self):
        resp = _client.post("/api/workflows/combined",
                            json={**self._BASE_BODY, "mortality_reduction_pct": 101.0})
        assert resp.status_code == 422

    def test_utility_over_1_returns_422(self):
        resp = _client.post("/api/workflows/combined",
                            json={**self._BASE_BODY, "utility_gain": 1.5})
        assert resp.status_code == 422

    def test_mortality_pct_converted_to_absolute(self):
        """The endpoint converts mortality_reduction_pct / 100 before calling orchestrator."""
        captured: list[dict] = []

        def capture(**kwargs):
            captured.append(kwargs)
            raise WorkflowError("stop", workflow_id="t", step="validate_inputs")

        with patch.object(main_module._orchestrator, "run_combined_workflow",
                          side_effect=capture):
            _client.post("/api/workflows/combined",
                         json={**self._BASE_BODY, "mortality_reduction_pct": 5.0})

        if captured:
            assert captured[0].get("mortality_reduction") == pytest.approx(0.05)

    def test_execution_time_header_present(self):
        with patch.object(main_module._orchestrator, "run_combined_workflow",
                          return_value=self._mock_combined_result()):
            resp = _client.post("/api/workflows/combined", json=self._BASE_BODY)
        assert "x-execution-time-ms" in resp.headers


# ══════════════════════════════════════════════════════════════════════════════
# 9. HTTP — POST /api/workflows/slr
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestSLRWorkflowHTTP:
    """Tests for the SLR workflow HTTP endpoint."""

    _BASE_BODY = {
        "pico_criteria": PICO_DICT,
        "abstracts": SAMPLE_ABSTRACTS,
        "batch_name": "unit-test-batch",
        "export_format": "csv",
    }

    def test_returns_200(self):
        with patch.object(main_module._orchestrator, "run_slr_workflow",
                          return_value=_make_orch_slr_result()):
            resp = _client.post("/api/workflows/slr", json=self._BASE_BODY)
        assert resp.status_code == 200

    def test_response_schema_keys(self):
        with patch.object(main_module._orchestrator, "run_slr_workflow",
                          return_value=_make_orch_slr_result()):
            resp = _client.post("/api/workflows/slr", json=self._BASE_BODY)
        data = resp.json()
        for key in ("workflow_id", "batch_id", "status", "screening_summary",
                    "decisions", "execution_time_seconds"):
            assert key in data

    def test_screening_summary_counts(self):
        with patch.object(main_module._orchestrator, "run_slr_workflow",
                          return_value=_make_orch_slr_result()):
            resp = _client.post("/api/workflows/slr", json=self._BASE_BODY)
        s = resp.json()["screening_summary"]
        assert s["total"] == 2
        assert s["included"] == 1
        assert s["excluded"] == 1
        assert s["uncertain"] == 0
        assert s["inclusion_rate"] == pytest.approx(0.5)

    def test_status_is_completed(self):
        with patch.object(main_module._orchestrator, "run_slr_workflow",
                          return_value=_make_orch_slr_result()):
            resp = _client.post("/api/workflows/slr", json=self._BASE_BODY)
        assert resp.json()["status"] == "completed"

    def test_missing_pico_population_returns_422(self):
        bad_pico = {k: v for k, v in PICO_DICT.items() if k != "population"}
        resp = _client.post("/api/workflows/slr",
                            json={**self._BASE_BODY, "pico_criteria": bad_pico})
        assert resp.status_code == 422

    def test_empty_abstracts_returns_422(self):
        resp = _client.post("/api/workflows/slr",
                            json={**self._BASE_BODY, "abstracts": []})
        assert resp.status_code == 422

    def test_empty_outcomes_list_returns_422(self):
        bad_pico = {**PICO_DICT, "outcomes": []}
        resp = _client.post("/api/workflows/slr",
                            json={**self._BASE_BODY, "pico_criteria": bad_pico})
        assert resp.status_code == 422

    def test_api_key_missing_returns_503(self):
        exc = WorkflowError("ANTHROPIC_API_KEY not set", workflow_id="slr_t",
                            step="screen_abstracts")
        with patch.object(main_module._orchestrator, "run_slr_workflow", side_effect=exc):
            resp = _client.post("/api/workflows/slr", json=self._BASE_BODY)
        assert resp.status_code == 503

    def test_pico_validation_failure_returns_422(self):
        exc = WorkflowError("PICO bad", workflow_id="slr_t", step="validate_pico")
        with patch.object(main_module._orchestrator, "run_slr_workflow", side_effect=exc):
            resp = _client.post("/api/workflows/slr", json=self._BASE_BODY)
        assert resp.status_code == 422


# ══════════════════════════════════════════════════════════════════════════════
# 10. HTTP — POST /api/workflows/quick-estimate
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestQuickEstimateHTTP:
    """Tests for the quick-estimate endpoint (runs real BIA engine, no mocks needed)."""

    def _body(self, condition: str = "diabetes", **overrides) -> dict:
        base = {
            "condition": condition,
            "intervention_name": "GlycoTrack Remote CGM",
            "catchment_population": 250_000,
            "device_cost_per_patient": 600.0,
            "expected_los_reduction_days": 0.0,
            "expected_visit_reduction_pct": 10.0,
        }
        base.update(overrides)
        return base

    def test_returns_200(self):
        assert _client.post("/api/workflows/quick-estimate", json=self._body()).status_code == 200

    def test_response_schema_keys(self):
        resp = _client.post("/api/workflows/quick-estimate", json=self._body())
        data = resp.json()
        for key in ("intervention_name", "estimate", "defaults_applied",
                    "next_steps", "caveats", "execution_time_seconds", "disclaimer"):
            assert key in data, f"Missing key: {key!r}"

    def test_annual_impacts_are_3_years(self):
        resp = _client.post("/api/workflows/quick-estimate", json=self._body())
        impacts = resp.json()["estimate"]["annual_impacts"]
        assert len(impacts) == 3
        for item in impacts:
            assert "year" in item
            assert "net_budget_impact_gbp" in item

    def test_diabetes_defaults_applied(self):
        resp = _client.post("/api/workflows/quick-estimate", json=self._body("diabetes"))
        assert resp.json()["defaults_applied"]["eligible_pct"] == pytest.approx(7.0)

    def test_cardiovascular_defaults_applied(self):
        resp = _client.post("/api/workflows/quick-estimate", json=self._body("cardiovascular"))
        assert resp.json()["defaults_applied"]["eligible_pct"] == pytest.approx(4.0)

    def test_cancer_defaults_applied(self):
        resp = _client.post("/api/workflows/quick-estimate", json=self._body("cancer"))
        assert resp.json()["defaults_applied"]["eligible_pct"] == pytest.approx(1.0)

    def test_respiratory_defaults_applied(self):
        resp = _client.post("/api/workflows/quick-estimate", json=self._body("respiratory"))
        assert resp.json()["defaults_applied"]["eligible_pct"] == pytest.approx(10.0)

    def test_unknown_condition_uses_generic_defaults(self):
        resp = _client.post("/api/workflows/quick-estimate",
                            json=self._body("ophthalmology"))
        assert resp.status_code == 200
        assert resp.json()["defaults_applied"]["eligible_pct"] == pytest.approx(5.0)

    def test_eligible_patients_calculated_correctly(self):
        # diabetes default: 7% eligible; 100k catchment → 7000 eligible
        resp = _client.post("/api/workflows/quick-estimate",
                            json=self._body(catchment_population=100_000))
        assert resp.json()["estimate"]["eligible_patients"] == 7_000

    def test_next_steps_point_to_full_workflows(self):
        resp = _client.post("/api/workflows/quick-estimate", json=self._body())
        next_steps = resp.json()["next_steps"]
        assert "full_bia" in next_steps
        assert "cea" in next_steps
        assert "combined" in next_steps

    def test_interpretation_mentions_investment_or_saving(self):
        resp = _client.post("/api/workflows/quick-estimate", json=self._body())
        interp = resp.json().get("interpretation", "").lower()
        assert "investment" in interp or "saving" in interp

    def test_zero_catchment_returns_422(self):
        resp = _client.post("/api/workflows/quick-estimate",
                            json=self._body(catchment_population=0))
        assert resp.status_code == 422

    def test_negative_device_cost_returns_422(self):
        resp = _client.post("/api/workflows/quick-estimate",
                            json=self._body(device_cost_per_patient=-100))
        assert resp.status_code == 422

    def test_visit_reduction_over_100_returns_422(self):
        resp = _client.post("/api/workflows/quick-estimate",
                            json=self._body(expected_visit_reduction_pct=150))
        assert resp.status_code == 422

    def test_treated_patients_grow_with_uptake(self):
        resp = _client.post("/api/workflows/quick-estimate", json=self._body())
        patients = resp.json()["estimate"]["treated_patients_by_year"]
        assert len(patients) == 3
        assert patients[0] <= patients[1] <= patients[2]


# ══════════════════════════════════════════════════════════════════════════════
# 11. Error handling — edge cases and WorkflowError behaviour
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestErrorHandling:
    """Tests for error propagation, HTTP status codes, and WorkflowError."""

    def test_workflow_error_str_includes_step_and_id(self):
        exc = WorkflowError("calc failed", workflow_id="bia_test_wf", step="calculate_bia")
        s = str(exc)
        assert "calculate_bia" in s
        assert "bia_test_wf" in s

    def test_workflow_error_attributes_accessible(self):
        exc = WorkflowError("bad input", workflow_id="slr_xyz", step="validate_pico")
        assert exc.workflow_id == "slr_xyz"
        assert exc.step == "validate_pico"

    def test_workflow_error_is_runtime_error_subclass(self):
        assert isinstance(WorkflowError("test", workflow_id="x", step="y"), RuntimeError)

    def test_bia_empty_inputs_rejected_before_orchestrator(self):
        resp = _client.post("/api/workflows/bia", json={"inputs": {}})
        assert resp.status_code == 422

    def test_cea_empty_inputs_rejected(self):
        resp = _client.post("/api/workflows/cea",
                            json={"inputs": {}, "intervention_name": "Test"})
        assert resp.status_code == 422

    def test_slr_empty_pico_rejected(self):
        resp = _client.post("/api/workflows/slr",
                            json={"pico_criteria": {}, "abstracts": SAMPLE_ABSTRACTS})
        assert resp.status_code == 422

    def test_slr_missing_comparison_rejected(self):
        bad_pico = {k: v for k, v in PICO_DICT.items() if k != "comparison"}
        resp = _client.post("/api/workflows/slr",
                            json={"pico_criteria": bad_pico, "abstracts": SAMPLE_ABSTRACTS})
        assert resp.status_code == 422

    def test_combined_mortality_over_100_rejected(self):
        resp = _client.post("/api/workflows/combined", json={
            "bia_inputs": MINIMAL_BIA_INPUTS, "mortality_reduction_pct": 101.0,
            "utility_gain": 0.1, "intervention_name": "Test",
        })
        assert resp.status_code == 422

    def test_combined_utility_over_1_rejected(self):
        resp = _client.post("/api/workflows/combined", json={
            "bia_inputs": MINIMAL_BIA_INPUTS, "mortality_reduction_pct": 3.0,
            "utility_gain": 1.5, "intervention_name": "Test",
        })
        assert resp.status_code == 422

    def test_unexpected_runtime_error_returns_500(self):
        with patch.object(main_module._orchestrator, "run_full_bia_workflow",
                          side_effect=RuntimeError("unexpected")):
            resp = _client.post("/api/workflows/bia", json={"inputs": MINIMAL_BIA_INPUTS})
        assert resp.status_code == 500

    def test_fatal_step_error_returns_step_in_detail(self):
        exc = WorkflowError("numpy overflow", workflow_id="bia_x", step="calculate_bia")
        with patch.object(main_module._orchestrator, "run_full_bia_workflow", side_effect=exc):
            resp = _client.post("/api/workflows/bia", json={"inputs": MINIMAL_BIA_INPUTS})
        assert resp.status_code == 500
        assert resp.json()["detail"]["step"] == "calculate_bia"

    def test_validate_workflow_inputs_unknown_type_returns_errors(self, orch_no_r):
        valid, errors = orch_no_r.validate_workflow_inputs({}, "unknown_type")
        assert valid is False
        assert any("unknown_type" in e.lower() or "Unknown workflow_type" in e for e in errors)

    def test_validate_workflow_inputs_missing_fields_listed(self, orch_no_r):
        valid, errors = orch_no_r.validate_workflow_inputs({"setting": "ICB"}, "bia")
        assert valid is False
        missing_in_errors = " ".join(errors)
        assert "price" in missing_in_errors

    def test_log_workflow_step_unknown_id_does_not_crash(self, orch_no_r):
        # Should log a warning but not raise
        orch_no_r.log_workflow_step("nonexistent_wf_id", "test_step", "completed")


# ══════════════════════════════════════════════════════════════════════════════
# 12. Pydantic schema helpers — pure unit tests
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestWorkflowSchemaHelpers:
    """Unit tests for request/response model validators and helper methods."""

    # ── BIAWorkflowRequest ─────────────────────────────────────────────

    def test_bia_req_has_required_fields_true(self):
        assert BIAWorkflowRequest(inputs=FULL_BIA_INPUTS).has_required_fields() is True

    def test_bia_req_has_required_fields_false_when_price_missing(self):
        bad = {k: v for k, v in FULL_BIA_INPUTS.items() if k != "price"}
        assert BIAWorkflowRequest(inputs=bad).has_required_fields() is False

    def test_bia_req_missing_fields_list(self):
        bad = {k: v for k, v in FULL_BIA_INPUTS.items() if k not in ("price", "setting")}
        missing = BIAWorkflowRequest(inputs=bad).missing_fields()
        assert "price" in missing and "setting" in missing

    def test_bia_req_empty_inputs_raises(self):
        with pytest.raises(Exception):
            BIAWorkflowRequest(inputs={})

    def test_bia_req_intervention_name_stripped(self):
        req = BIAWorkflowRequest(inputs=MINIMAL_BIA_INPUTS, intervention_name="  My Device  ")
        assert req.intervention_name == "My Device"

    def test_bia_req_blank_intervention_name_becomes_none(self):
        req = BIAWorkflowRequest(inputs=MINIMAL_BIA_INPUTS, intervention_name="   ")
        assert req.intervention_name is None

    # ── BIAWorkflowResponse ────────────────────────────────────────────

    def _bia_resp(self, **kwargs) -> BIAWorkflowResponse:
        defaults = dict(workflow_id="bia_t", submission_id="bia_t",
                        status=WorkflowStatus.COMPLETED, results={}, execution_time_seconds=1.0)
        defaults.update(kwargs)
        return BIAWorkflowResponse(**defaults)

    def test_bia_resp_break_even_year(self):
        assert self._bia_resp(results={"break_even_year": 2}).break_even_year() == 2

    def test_bia_resp_break_even_year_none_when_absent(self):
        assert self._bia_resp().break_even_year() is None

    def test_bia_resp_top_drivers(self):
        drivers = self._bia_resp(
            results={"top_cost_drivers": ["Workforce", "Device cost"]}
        ).top_drivers()
        assert drivers == ["Workforce", "Device cost"]

    def test_bia_resp_annual_impacts(self):
        impacts = self._bia_resp(
            results={"annual_budget_impact": [10_000.0, 7_500.0, 4_000.0]}
        ).annual_impacts()
        assert impacts == [10_000.0, 7_500.0, 4_000.0]

    def test_bia_resp_format_time_seconds(self):
        assert self._bia_resp(execution_time_seconds=45.2).format_execution_time() == "45.2s"

    def test_bia_resp_format_time_minutes(self):
        assert self._bia_resp(execution_time_seconds=83.4).format_execution_time() == "1m 23.4s"

    def test_bia_resp_is_complete_true(self):
        assert self._bia_resp(status=WorkflowStatus.COMPLETED).is_complete() is True

    def test_bia_resp_is_complete_false_for_partial(self):
        assert self._bia_resp(status=WorkflowStatus.PARTIAL).is_complete() is False

    # ── CEAWorkflowRequest ─────────────────────────────────────────────

    def test_cea_req_syncs_intervention_name_into_inputs(self):
        req = CEAWorkflowRequest(inputs=MINIMAL_CEA_INPUTS, intervention_name="Override")
        assert req.inputs["intervention_name"] == "Override"

    def test_cea_req_has_required_fields_true(self):
        assert CEAWorkflowRequest(inputs=MINIMAL_CEA_INPUTS, intervention_name="Test").has_required_fields()

    def test_cea_req_missing_fields_listed(self):
        bad = {k: v for k, v in MINIMAL_CEA_INPUTS.items() if k != "cost_standard_annual"}
        missing = CEAWorkflowRequest(inputs=bad, intervention_name="Test").missing_fields()
        assert "cost_standard_annual" in missing

    # ── CEAWorkflowResponse ────────────────────────────────────────────

    def _cea_resp(self, results: dict, **kwargs) -> CEAWorkflowResponse:
        defaults = dict(workflow_id="cea_t", status=WorkflowStatus.COMPLETED,
                        results=results, execution_time_seconds=2.0)
        defaults.update(kwargs)
        return CEAWorkflowResponse(**defaults)

    def test_cea_resp_icer_formatted_gbp(self):
        assert self._cea_resp({"icer": 22_500.0}).icer_formatted() == "£22,500/QALY"

    def test_cea_resp_icer_formatted_no_results(self):
        assert self._cea_resp({}).icer_formatted() == "—"

    def test_cea_resp_cost_effective_25k(self):
        resp = self._cea_resp({"icer": 14_994.0, "cost_effective_25k": True, "cost_effective_35k": True})
        assert resp.is_cost_effective(threshold=25_000) is True

    def test_cea_resp_not_cost_effective_25k(self):
        resp = self._cea_resp({"icer": 30_000.0, "cost_effective_25k": False, "cost_effective_35k": True})
        assert resp.is_cost_effective(threshold=25_000) is False
        assert resp.is_cost_effective(threshold=35_000) is True

    def test_cea_resp_incremental_summary_has_all_keys(self):
        resp = self._cea_resp({
            "incremental_cost": 7_347.0, "incremental_qalys": 0.49,
            "icer": 14_994.0, "interpretation": "Cost-effective",
        })
        summary = resp.incremental_summary()
        for key in ("incremental_cost", "incremental_qalys", "icer", "icer_formatted", "interpretation"):
            assert key in summary

    # ── CombinedWorkflowRequest ────────────────────────────────────────

    def _comb_req(self, **kwargs) -> CombinedWorkflowRequest:
        defaults = dict(bia_inputs=MINIMAL_BIA_INPUTS, mortality_reduction_pct=3.0,
                        utility_gain=0.08, intervention_name="Test")
        defaults.update(kwargs)
        return CombinedWorkflowRequest(**defaults)

    def test_combined_req_mortality_absolute_conversion(self):
        assert self._comb_req(mortality_reduction_pct=3.0).mortality_reduction_absolute() == pytest.approx(0.03)

    def test_combined_req_mortality_zero(self):
        assert self._comb_req(mortality_reduction_pct=0.0).mortality_reduction_absolute() == 0.0

    def test_combined_req_mortality_over_100_rejected(self):
        with pytest.raises(Exception):
            self._comb_req(mortality_reduction_pct=101.0)

    def test_combined_req_utility_over_1_rejected(self):
        with pytest.raises(Exception):
            self._comb_req(utility_gain=1.1)

    def test_combined_req_has_required_bia_fields(self):
        assert self._comb_req(bia_inputs=FULL_BIA_INPUTS).has_required_bia_fields() is True

    # ── CombinedWorkflowResponse ───────────────────────────────────────

    def _comb_resp(self, **kwargs) -> CombinedWorkflowResponse:
        defaults = dict(workflow_id="combined_t", status=WorkflowStatus.COMPLETED,
                        bia_results={}, cea_results={}, execution_time_seconds=5.0)
        defaults.update(kwargs)
        return CombinedWorkflowResponse(**defaults)

    def test_combined_resp_executive_summary_auto_generated(self):
        resp = self._comb_resp(
            bia_results=_make_bia_results().model_dump(),
            cea_results=_make_markov_results().model_dump(),
        )
        assert len(resp.executive_summary) > 0

    def test_combined_resp_executive_summary_mentions_icer(self):
        resp = self._comb_resp(
            bia_results=_make_bia_results().model_dump(),
            cea_results=_make_markov_results(icer=22_500.0).model_dump(),
        )
        assert "22,500" in resp.executive_summary

    def test_combined_resp_both_analyses_available_true(self):
        resp = self._comb_resp(
            bia_results={"annual_budget_impact": [1000.0]},
            cea_results={"icer": 20_000.0},
        )
        assert resp.both_analyses_available() is True

    def test_combined_resp_both_analyses_available_false_when_cea_empty(self):
        resp = self._comb_resp(
            status=WorkflowStatus.PARTIAL,
            bia_results={"annual_budget_impact": [1000.0]},
            cea_results={},
        )
        assert resp.both_analyses_available() is False

    def test_combined_resp_bia_break_even(self):
        assert self._comb_resp(bia_results={"break_even_year": 2}).bia_break_even() == 2

    def test_combined_resp_cea_icer(self):
        assert self._comb_resp(cea_results={"icer": 18_500.0}).cea_icer() == pytest.approx(18_500.0)

    # ── SLRWorkflowRequest ─────────────────────────────────────────────

    def _slr_req(self, **kwargs) -> SLRWorkflowRequest:
        defaults = dict(pico_criteria=PICO_DICT, abstracts=SAMPLE_ABSTRACTS)
        defaults.update(kwargs)
        return SLRWorkflowRequest(**defaults)

    def test_slr_req_abstract_count(self):
        assert self._slr_req().abstract_count() == 2

    def test_slr_req_has_exclusion_criteria_true(self):
        assert self._slr_req().has_exclusion_criteria() is True

    def test_slr_req_has_exclusion_criteria_false(self):
        pico_no_exc = {k: v for k, v in PICO_DICT.items() if k != "exclusion_criteria"}
        assert self._slr_req(pico_criteria=pico_no_exc).has_exclusion_criteria() is False

    def test_slr_req_effective_batch_name_uses_provided(self):
        assert self._slr_req(batch_name="my-batch").effective_batch_name() == "my-batch"

    def test_slr_req_effective_batch_name_generates_when_absent(self):
        name = self._slr_req().effective_batch_name()
        assert isinstance(name, str) and len(name) > 0

    def test_slr_req_strip_batch_name(self):
        assert self._slr_req(batch_name="  padded  ").batch_name == "padded"

    def test_slr_req_blank_batch_name_becomes_none(self):
        assert self._slr_req(batch_name="   ").batch_name is None

    def test_slr_req_empty_outcomes_rejected(self):
        with pytest.raises(Exception):
            self._slr_req(pico_criteria={**PICO_DICT, "outcomes": []})

    # ── SLRWorkflowResponse ────────────────────────────────────────────

    def _slr_resp(self, **kwargs) -> SLRWorkflowResponse:
        defaults = dict(workflow_id="slr_t", batch_id="batch_t",
                        status=WorkflowStatus.COMPLETED, execution_time_seconds=3.0)
        defaults.update(kwargs)
        return SLRWorkflowResponse(**defaults)

    def test_slr_resp_inclusion_rate(self):
        resp = self._slr_resp(screening_summary={
            "total": 4, "included": 2, "excluded": 2, "uncertain": 0, "inclusion_rate": 0.5
        })
        assert resp.inclusion_rate() == pytest.approx(0.5)

    def test_slr_resp_has_uncertain_true(self):
        assert self._slr_resp(screening_summary={"uncertain": 1}).has_uncertain_decisions() is True

    def test_slr_resp_has_uncertain_false(self):
        assert self._slr_resp(screening_summary={"uncertain": 0}).has_uncertain_decisions() is False

    def test_slr_resp_decisions_by_outcome_grouping(self):
        decisions = [
            {"pmid": "111", "decision": "include"},
            {"pmid": "222", "decision": "exclude"},
            {"pmid": "333", "decision": "uncertain"},
        ]
        grouped = self._slr_resp(
            decisions=decisions,
            screening_summary={"total": 3, "included": 1, "excluded": 1, "uncertain": 1},
        ).decisions_by_outcome()
        assert grouped["include"][0]["pmid"] == "111"
        assert grouped["exclude"][0]["pmid"] == "222"
        assert grouped["uncertain"][0]["pmid"] == "333"

    def test_slr_resp_screening_summary_backcomputed_from_decisions(self):
        """model_validator populates screening_summary from decisions when absent."""
        decisions = [
            {"pmid": "111", "decision": "include"},
            {"pmid": "222", "decision": "exclude"},
            {"pmid": "333", "decision": "exclude"},
        ]
        resp = self._slr_resp(decisions=decisions)
        assert resp.screening_summary["total"] == 3
        assert resp.screening_summary["included"] == 1
        assert resp.screening_summary["excluded"] == 2
        assert resp.screening_summary["inclusion_rate"] == pytest.approx(1 / 3, rel=1e-3)

    def test_slr_resp_format_execution_time_minutes(self):
        assert self._slr_resp(execution_time_seconds=125.6).format_execution_time() == "2m 5.6s"

    # ── Enum round-trips ───────────────────────────────────────────────

    def test_workflow_status_values(self):
        assert WorkflowStatus.COMPLETED.value == "completed"
        assert WorkflowStatus.FAILED.value == "failed"
        assert WorkflowStatus.PARTIAL.value == "partial"

    def test_report_format_round_trip(self):
        assert ReportFormat("pptx") == ReportFormat.PPTX
        assert ReportFormat("docx") == ReportFormat.DOCX

    def test_export_format_round_trip(self):
        assert ExportFormat("csv") == ExportFormat.CSV
        assert ExportFormat("excel") == ExportFormat.EXCEL


# ══════════════════════════════════════════════════════════════════════════════
# 13. Integration tests — require real external services
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestBIAWorkflowIntegration:
    """End-to-end BIA — uses real engine, no external APIs required."""

    def test_bia_workflow_end_to_end(self):
        orch = HEOROrchestrator(config={"r_available": False})
        with patch("agents.orchestrator.generate_bia_report", return_value=None):
            result = orch.run_full_bia_workflow(dict(FULL_BIA_INPUTS))
        assert result["status"] == "completed"
        assert result["workflow_id"].startswith("bia_")
        assert len(result["bia_results"]["annual_budget_impact"]) == 3

    def test_bia_via_http_end_to_end(self):
        with patch("agents.orchestrator.generate_bia_report", return_value=None):
            resp = _client.post("/api/workflows/bia", json={
                "inputs": FULL_BIA_INPUTS,
                "generate_report": False,
                "enrich_with_evidence": False,
            })
        assert resp.status_code in (200, 206)
        assert resp.json()["results"]["annual_budget_impact"]

    def test_bia_results_numerically_reasonable(self):
        orch = HEOROrchestrator(config={"r_available": False})
        inputs = {**FULL_BIA_INPUTS, "catchment_size": 100_000, "eligible_pct": 5.0,
                  "uptake_y1": 10.0, "uptake_y2": 20.0, "uptake_y3": 30.0, "price": 1_000.0}
        with patch("agents.orchestrator.generate_bia_report", return_value=None):
            result = orch.run_full_bia_workflow(inputs)
        patients = result["bia_results"]["total_treated_patients"]
        # Treated patients should grow with uptake
        assert patients[0] <= patients[1] <= patients[2]
        # Budget impacts should be finite
        for impact in result["bia_results"]["annual_budget_impact"]:
            assert abs(impact) < 1e9

    def test_workflow_retrievable_by_id_after_run(self):
        orch = HEOROrchestrator(config={"r_available": False})
        with patch("agents.orchestrator.generate_bia_report", return_value=None):
            result = orch.run_full_bia_workflow(dict(FULL_BIA_INPUTS))
        state = orch.get_workflow_status(result["workflow_id"])
        assert state["workflow_id"] == result["workflow_id"]
        assert state["workflow_type"] == "bia"
        assert len(state["steps"]) > 0

    def test_bia_scenarios_vary_from_base(self):
        orch = HEOROrchestrator(config={"r_available": False})
        with patch("agents.orchestrator.generate_bia_report", return_value=None):
            result = orch.run_full_bia_workflow(dict(FULL_BIA_INPUTS))
        scenarios = result["scenarios"]
        assert set(scenarios.keys()) >= {"conservative", "base", "optimistic"}


@pytest.mark.integration
class TestSLRWorkflowIntegration:
    """SLR integration — requires ANTHROPIC_API_KEY."""

    @pytest.fixture(autouse=True)
    def require_api_key(self):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            pytest.skip("ANTHROPIC_API_KEY not set")

    def test_screen_two_abstracts_returns_decisions(self):
        orch = HEOROrchestrator()
        result = orch.run_slr_workflow(pico=PICO_DICT, abstracts=SAMPLE_ABSTRACTS)
        assert result["status"] == "completed"
        assert result["total"] == 2
        assert result["included"] + result["excluded"] + result["uncertain"] == 2

    def test_decisions_have_reasoning(self):
        orch = HEOROrchestrator()
        result = orch.run_slr_workflow(pico=PICO_DICT, abstracts=[SAMPLE_ABSTRACTS[0]])
        batch_path = result.get("batch_path")
        if batch_path and Path(batch_path).exists():
            raw = json.loads(Path(batch_path).read_text(encoding="utf-8"))
            decisions = raw.get("decisions", [])
            assert len(decisions) == 1
            assert decisions[0].get("reasoning")

    def test_paediatric_abstract_excluded(self):
        """Second abstract (paediatric T1DM) should not be included for an adult T2DM PICO."""
        orch = HEOROrchestrator()
        result = orch.run_slr_workflow(pico=PICO_DICT, abstracts=[SAMPLE_ABSTRACTS[1]])
        # Paediatric T1DM should be excluded or uncertain — never included
        assert result["included"] == 0


@pytest.mark.integration
class TestCEAWorkflowIntegration:
    """CEA integration — requires Rscript on PATH."""

    @pytest.fixture(autouse=True)
    def require_r(self):
        from engines.markov.runner import check_r_installed
        if not check_r_installed():
            pytest.skip("Rscript not on PATH")

    def test_cea_workflow_end_to_end(self):
        orch = HEOROrchestrator()
        with patch("agents.orchestrator.generate_cea_report", return_value=None):
            result = orch.run_full_cea_workflow(dict(MINIMAL_CEA_INPUTS))
        assert result["status"] == "completed"
        cea = result["cea_results"]
        assert "interpretation" in cea
        assert "cost_effective_25k" in cea

    def test_cea_icer_finite_and_positive(self):
        orch = HEOROrchestrator()
        with patch("agents.orchestrator.generate_cea_report", return_value=None):
            result = orch.run_full_cea_workflow(dict(MINIMAL_CEA_INPUTS))
        icer = result["cea_results"].get("icer")
        if icer is not None:
            assert 0 < icer < 500_000  # NHS-plausible range
