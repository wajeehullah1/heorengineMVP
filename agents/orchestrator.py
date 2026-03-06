"""HEOR Engine orchestrator — coordinates all analysis workflows.

This module provides :class:`HEOROrchestrator`, a high-level class that
chains together the BIA engine, Markov CEA engine, SLR screener, and
evidence agent into end-to-end workflows.

Typical usage::

    from agents.orchestrator import HEOROrchestrator

    orch = HEOROrchestrator()

    # Run a full BIA
    result = orch.run_full_bia_workflow(bia_inputs_dict)
    print(result["report_path"])

    # Run combined BIA + CEA
    result = orch.run_combined_workflow(
        bia_inputs=bia_inputs_dict,
        mortality_reduction=0.03,
        utility_gain=0.10,
    )

    # Screen a batch of abstracts
    result = orch.run_slr_workflow(pico_dict, abstracts_list)
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from engines.bia.model import calculate_budget_impact, calculate_scenarios
from engines.bia.schema import BIAInputs, BIAResults
from engines.bia.validation import (
    estimate_confidence,
    suggest_missing_inputs,
    validate_clinical_sense,
)
from engines.markov.runner import (
    check_r_installed,
    run_markov_with_validation,
)
from engines.markov.schema import MarkovInputs, MarkovResults
from engines.reports.pptx_builder import generate_bia_report, generate_cea_report
from engines.slr.schema import Abstract, PICOCriteria, ScreeningBatch
from engines.slr.screener import (
    create_screening_batch,
    export_screening_results,
    save_batch,
    screen_abstracts,
)
from agents.evidence_agent import (
    enrich_bia_inputs,
    get_nice_comparators,
    get_nice_threshold_context,
    validate_against_references,
)

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SUBMISSIONS_DIR = _REPO_ROOT / "data" / "submissions"
_WORKFLOWS_DIR = _REPO_ROOT / "data" / "workflows"

# Ensure directories exist at import time
_SUBMISSIONS_DIR.mkdir(parents=True, exist_ok=True)
_WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)


# ── Exceptions ────────────────────────────────────────────────────────────────

class WorkflowError(RuntimeError):
    """Raised when a workflow step fails unrecoverably.

    Attributes:
        workflow_id: Identifier of the workflow that failed.
        step: Name of the step that raised the error.
    """

    def __init__(self, message: str, workflow_id: str = "", step: str = "") -> None:
        super().__init__(message)
        self.workflow_id = workflow_id
        self.step = step

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.workflow_id:
            parts.append(f"workflow_id={self.workflow_id}")
        if self.step:
            parts.append(f"step={self.step}")
        return " | ".join(parts)


# ── Orchestrator ──────────────────────────────────────────────────────────────

class HEOROrchestrator:
    """Coordinates all HEOR Engine components into unified end-to-end workflows.

    Each workflow method follows the same pattern:

    1. Generate a unique workflow ID and initialise on-disk state.
    2. Validate inputs (raises :class:`WorkflowError` on hard failures).
    3. Enrich / prepare data using the evidence agent or pre-processing steps.
    4. Run the core calculation engine(s).
    5. Persist intermediate and final results.
    6. Generate a PowerPoint report.
    7. Return a structured summary dict.

    Args:
        config: Optional configuration overrides.  Recognised keys:

            ``log_level`` (str) — ``"DEBUG"``, ``"INFO"`` (default), etc.

            ``r_available`` (bool) — override the auto-detected R availability
            flag (useful in testing).
    """

    # Fields required for each workflow type, used by validate_workflow_inputs.
    _REQUIRED_FIELDS: dict[str, list[str]] = {
        "bia": [
            "setting", "model_year", "forecast_years", "funding_source",
            "catchment_size", "eligible_pct", "uptake_y1", "uptake_y2",
            "uptake_y3", "workforce", "pricing_model", "price",
        ],
        "cea": [
            "intervention_name",
            "prob_death_standard", "cost_standard_annual", "utility_standard",
            "prob_death_treatment", "cost_treatment_annual", "utility_treatment",
        ],
        "slr": ["population", "intervention", "comparison", "outcomes"],
        "combined": [
            "setting", "model_year", "forecast_years", "funding_source",
            "catchment_size", "eligible_pct", "uptake_y1", "uptake_y2",
            "uptake_y3", "workforce", "pricing_model", "price",
        ],
    }

    def __init__(self, config: Optional[dict] = None) -> None:
        cfg = config or {}

        # Configure logging
        log_level = cfg.get("log_level", "INFO").upper()
        logging.basicConfig(
            level=getattr(logging, log_level, logging.INFO),
            format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        )

        self._config = cfg
        self._log = logger
        self._workflows_dir = _WORKFLOWS_DIR

        # In-memory store for workflow step logs (keyed by workflow_id)
        self._workflow_log: dict[str, dict[str, Any]] = {}

        # Read API key lazily at call time; store None here
        self._api_key: Optional[str] = None

        # Detect R availability once at init (can be overridden via config)
        if "r_available" in cfg:
            self._r_available: bool = bool(cfg["r_available"])
        else:
            self._r_available = check_r_installed()

        self._log.info(
            "HEOROrchestrator initialised | R=%s", self._r_available
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _new_workflow_id(self, prefix: str) -> str:
        """Return a sortable, unique workflow identifier.

        Format: ``{prefix}_{YYYYMMDD_HHMMSS}_{8-char-uuid}``
        """
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        short_uuid = uuid.uuid4().hex[:8]
        return f"{prefix}_{ts}_{short_uuid}"

    def _init_workflow_state(
        self,
        workflow_id: str,
        workflow_type: str,
        inputs_summary: dict,
    ) -> None:
        """Write the initial workflow state JSON to disk.

        Args:
            workflow_id: Unique identifier for this workflow run.
            workflow_type: One of ``"bia"``, ``"cea"``, ``"combined"``, ``"slr"``.
            inputs_summary: Lightweight summary of the inputs (not the full
                validated object) to keep the state file human-readable.
        """
        state = {
            "workflow_id": workflow_id,
            "workflow_type": workflow_type,
            "status": "started",
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
            "updated_at": datetime.now(tz=timezone.utc).isoformat(),
            "inputs_summary": inputs_summary,
            "steps": [],
        }
        self._workflow_log[workflow_id] = state
        self._persist_workflow_state(workflow_id)

    def _persist_workflow_state(self, workflow_id: str) -> None:
        """Write the current in-memory state for *workflow_id* to disk.

        I/O errors are logged but not re-raised so they never abort a workflow.
        """
        try:
            path = self._workflows_dir / f"{workflow_id}.json"
            path.write_text(
                json.dumps(self._workflow_log[workflow_id], indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            self._log.warning(
                "Could not persist workflow state for %s: %s", workflow_id, exc
            )

    def _derive_markov_params(
        self,
        bia_inputs: BIAInputs,
        bia_results: BIAResults,
        mortality_reduction: float,
        utility_gain: float,
        intervention_name: str = "",
    ) -> dict:
        """Derive Markov CEA parameters from BIA inputs and results.

        Estimation logic
        ----------------
        ``cost_standard_annual``
            Mean current pathway cost per patient (Year 1).

        ``cost_treatment_annual``
            Baseline standard cost + intervention price − per-patient savings
            (taken from Year 1 cost-per-patient in the BIA).

        ``prob_death_treatment``
            Standard mortality probability reduced by *mortality_reduction*.
            Standard mortality is estimated from the BIA setting and a
            conservative 5 % annual placeholder (override via config).

        ``utility_treatment``
            Standard utility + *utility_gain*, clamped to 1.0.

        Args:
            bia_inputs:          Validated BIA inputs.
            bia_results:         Results from the BIA calculation.
            mortality_reduction: Absolute reduction in annual mortality
                                 probability (e.g. 0.03 = 3 percentage points).
            utility_gain:        Additive QALY-weight improvement (e.g. 0.10).
            intervention_name:   Label for the treatment arm.

        Returns:
            Dict suitable for :func:`~engines.markov.runner.run_markov_with_validation`.
        """
        # Year 1 values from base-case BIA
        y1_cpp_standard = bia_results.cost_per_patient[0] if bia_results.cost_per_patient else 0.0
        y1_cpp_treatment = bia_results.scenarios["base"].cost_per_patient[0] if bia_results.scenarios else y1_cpp_standard

        # Conservative placeholder mortality — override via config
        prob_death_std = self._config.get("baseline_mortality", 0.05)
        prob_death_trt = max(0.0, prob_death_std - mortality_reduction)

        # Conservative utility placeholders — override via config
        utility_std = self._config.get("baseline_utility", 0.75)
        utility_trt = min(1.0, utility_std + utility_gain)

        name = intervention_name or getattr(bia_inputs, "intervention_name", "Intervention")

        return {
            "intervention_name": name,
            "prob_death_standard": prob_death_std,
            "cost_standard_annual": round(y1_cpp_standard, 2),
            "utility_standard": utility_std,
            "prob_death_treatment": prob_death_trt,
            "cost_treatment_annual": round(y1_cpp_treatment, 2),
            "utility_treatment": utility_trt,
            "time_horizon": self._config.get("time_horizon", 5),
            "discount_rate": self._config.get("discount_rate", 0.035),
        }

    # ── Public helpers ────────────────────────────────────────────────────────

    def validate_workflow_inputs(
        self,
        inputs: dict,
        workflow_type: str,
    ) -> tuple[bool, list[str]]:
        """Check that *inputs* contains all required fields for *workflow_type*.

        This is a lightweight structural check — it does **not** run Pydantic
        validation or clinical-sense checks.  Those happen inside each workflow
        method.

        Args:
            inputs:        Raw input dict from the caller.
            workflow_type: One of ``"bia"``, ``"cea"``, ``"combined"``, ``"slr"``.

        Returns:
            A ``(valid, errors)`` tuple.  *valid* is ``True`` when *errors* is
            empty.  *errors* is a list of human-readable problem descriptions.
        """
        required = self._REQUIRED_FIELDS.get(workflow_type, [])
        if not required:
            return False, [f"Unknown workflow_type '{workflow_type}'"]

        errors = [
            f"Missing required field: '{field}'"
            for field in required
            if field not in inputs or inputs[field] is None
        ]
        return len(errors) == 0, errors

    def log_workflow_step(
        self,
        workflow_id: str,
        step: str,
        status: str,
        details: Optional[dict] = None,
    ) -> None:
        """Append a step record to the workflow audit log and persist to disk.

        Args:
            workflow_id: The running workflow to update.
            step:        Short step name, e.g. ``"enrich_inputs"``.
            status:      One of ``"started"``, ``"completed"``, ``"skipped"``,
                         ``"failed"``.
            details:     Optional dict of extra information to store.
        """
        if workflow_id not in self._workflow_log:
            self._log.warning(
                "log_workflow_step called for unknown workflow_id '%s'",
                workflow_id,
            )
            return

        entry = {
            "step": step,
            "status": status,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "details": details or {},
        }
        self._workflow_log[workflow_id]["steps"].append(entry)
        self._workflow_log[workflow_id]["updated_at"] = entry["timestamp"]

        # Reflect terminal status at top level
        if status == "failed":
            self._workflow_log[workflow_id]["status"] = "failed"
        elif status == "completed" and step == "generate_report":
            self._workflow_log[workflow_id]["status"] = "completed"

        self._persist_workflow_state(workflow_id)
        self._log.debug("Workflow %s | step=%s status=%s", workflow_id, step, status)

    def get_workflow_status(self, workflow_id: str) -> dict:
        """Return the current status dict for a workflow.

        Falls back to reading from disk if the workflow is not in the
        in-memory log (e.g. after a server restart).

        Args:
            workflow_id: The workflow identifier.

        Returns:
            The workflow state dict, or ``{"error": "not found"}`` if the
            workflow does not exist in memory or on disk.
        """
        if workflow_id in self._workflow_log:
            return dict(self._workflow_log[workflow_id])

        # Try loading from disk
        path = self._workflows_dir / f"{workflow_id}.json"
        if path.exists():
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
                self._workflow_log[workflow_id] = state
                return dict(state)
            except (OSError, json.JSONDecodeError) as exc:
                self._log.error("Could not load workflow state from %s: %s", path, exc)

        return {"error": f"Workflow '{workflow_id}' not found"}

    # ── BIA workflow ──────────────────────────────────────────────────────────

    def run_full_bia_workflow(self, inputs: dict) -> dict:
        """Run a complete Budget Impact Analysis workflow.

        Steps
        -----
        1. Validate required fields.
        2. Enrich inputs via the evidence agent (NHS costs, population data).
        3. Validate enriched inputs with Pydantic + clinical-sense checks.
        4. Calculate base-case BIA and scenarios.
        5. Save enriched inputs to ``data/submissions/``.
        6. Validate results against NHS reference benchmarks.
        7. Generate PowerPoint report.
        8. Return summary dict.

        Args:
            inputs: Raw BIA input dict.

        Returns:
            Dict with keys: ``workflow_id``, ``bia_results``, ``scenarios``,
            ``warnings``, ``suggestions``, ``confidence``, ``validation``,
            ``report_path``, ``status``.

        Raises:
            WorkflowError: If a non-recoverable step fails (e.g. Pydantic
                validation of the enriched inputs).
        """
        wf_id = self._new_workflow_id("bia")
        self._init_workflow_state(wf_id, "bia", {
            "setting": inputs.get("setting"),
            "forecast_years": inputs.get("forecast_years"),
            "price": inputs.get("price"),
        })

        # ── Step 1: Validate required fields ─────────────────────────────
        self.log_workflow_step(wf_id, "validate_inputs", "started")
        valid, errors = self.validate_workflow_inputs(inputs, "bia")
        if not valid:
            self.log_workflow_step(wf_id, "validate_inputs", "failed", {"errors": errors})
            raise WorkflowError(
                f"Input validation failed: {'; '.join(errors)}",
                workflow_id=wf_id,
                step="validate_inputs",
            )
        self.log_workflow_step(wf_id, "validate_inputs", "completed")

        # ── Step 2: Enrich inputs ─────────────────────────────────────────
        # enrich_bia_inputs returns {"inputs": {...}, "suggested_values": {...}, ...}
        # We merge suggested_values onto a copy of the original inputs.
        self.log_workflow_step(wf_id, "enrich_inputs", "started")
        enrichment_meta: dict = {}
        try:
            enrichment_result = enrich_bia_inputs(inputs)
            enriched = dict(inputs)
            enriched.update(enrichment_result.get("suggested_values", {}))
            enrichment_meta = {
                k: enrichment_result[k]
                for k in ("warnings", "comparators", "reference_costs",
                          "population_context", "metadata")
                if k in enrichment_result
            }
        except Exception as exc:
            self._log.warning("Evidence enrichment failed (%s) — using raw inputs", exc)
            enriched = dict(inputs)
        self.log_workflow_step(wf_id, "enrich_inputs", "completed",
                               {"enriched_keys": list(enriched.keys())})

        # ── Step 3: Parse + validate with Pydantic ────────────────────────
        self.log_workflow_step(wf_id, "parse_inputs", "started")
        try:
            bia_inputs = BIAInputs(**enriched)
        except Exception as exc:
            self.log_workflow_step(wf_id, "parse_inputs", "failed", {"error": str(exc)})
            raise WorkflowError(str(exc), workflow_id=wf_id, step="parse_inputs") from exc

        warnings = validate_clinical_sense(bia_inputs)
        suggestions = suggest_missing_inputs(bia_inputs)
        confidence = estimate_confidence(bia_inputs)
        self.log_workflow_step(wf_id, "parse_inputs", "completed", {
            "warnings": len(warnings),
            "suggestions": len(suggestions),
            "confidence": confidence,
        })

        # ── Step 4: Calculate BIA ─────────────────────────────────────────
        self.log_workflow_step(wf_id, "calculate_bia", "started")
        try:
            bia_results = calculate_budget_impact(bia_inputs)
            scenarios = calculate_scenarios(bia_inputs)
        except Exception as exc:
            self.log_workflow_step(wf_id, "calculate_bia", "failed", {"error": str(exc)})
            raise WorkflowError(str(exc), workflow_id=wf_id, step="calculate_bia") from exc
        self.log_workflow_step(wf_id, "calculate_bia", "completed", {
            "break_even_year": bia_results.break_even_year,
        })

        # ── Step 5: Save submission ───────────────────────────────────────
        self.log_workflow_step(wf_id, "save_submission", "started")
        submission_path: Optional[Path] = None
        try:
            submission_path = _SUBMISSIONS_DIR / f"{wf_id}.json"
            submission_path.write_text(
                json.dumps({
                    "workflow_id": wf_id,
                    "inputs": bia_inputs.model_dump(),
                    "results": bia_results.model_dump(),
                }, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            self._log.warning("Could not save submission: %s", exc)
        self.log_workflow_step(wf_id, "save_submission", "completed",
                               {"path": str(submission_path)})

        # ── Step 6: Validate results against references ───────────────────
        self.log_workflow_step(wf_id, "nice_validate", "started")
        validation: dict = {}
        try:
            validation = validate_against_references(
                bia_inputs.model_dump(), bia_results.model_dump()
            )
        except Exception as exc:
            self._log.warning("Reference validation failed (%s) — skipping", exc)
        self.log_workflow_step(wf_id, "nice_validate", "completed",
                               {"flags": len(validation)})

        # ── Step 7: Generate report ───────────────────────────────────────
        self.log_workflow_step(wf_id, "generate_report", "started")
        report_path: Optional[str] = None
        try:
            report_results = {
                "submission_id": wf_id,
                "validation": {
                    "warnings": warnings,
                    "suggestions": suggestions,
                    "confidence": confidence,
                    "validation_flags": validation,
                },
                "summary": {
                    "eligible_patients": bia_inputs.eligible_patients,
                    "treated_patients": bia_inputs.treated_patients_by_year,
                },
                "base":         scenarios["base"].model_dump(),
                "conservative": scenarios["conservative"].model_dump(),
                "optimistic":   scenarios["optimistic"].model_dump(),
            }
            path = generate_bia_report(bia_inputs, report_results, wf_id)
            report_path = str(path)
        except Exception as exc:
            self._log.warning("Report generation failed (%s)", exc)
        self.log_workflow_step(wf_id, "generate_report", "completed",
                               {"report_path": report_path})

        return {
            "workflow_id": wf_id,
            "status": "completed",
            "bia_results": bia_results.model_dump(),
            "scenarios": {k: v.model_dump() for k, v in scenarios.items()},
            "warnings": warnings,
            "suggestions": suggestions,
            "confidence": confidence,
            "validation": validation,
            "enrichment_meta": enrichment_meta,
            "report_path": report_path,
        }

    # ── CEA workflow ──────────────────────────────────────────────────────────

    def run_full_cea_workflow(self, inputs: dict) -> dict:
        """Run a complete Cost-Effectiveness Analysis workflow via Markov model.

        Steps
        -----
        1. Validate required fields.
        2. Fetch NICE threshold context for condition / intervention.
        3. Check R is installed; fail fast if not.
        4. Run Markov model.
        5. Generate CEA PowerPoint report.
        6. Return summary dict.

        Args:
            inputs: Raw Markov input dict — must satisfy
                :class:`~engines.markov.schema.MarkovInputs`.

        Returns:
            Dict with keys: ``workflow_id``, ``cea_results``,
            ``nice_context``, ``report_path``, ``status``.

        Raises:
            WorkflowError: If R is not installed or Markov validation fails.
        """
        wf_id = self._new_workflow_id("cea")
        self._init_workflow_state(wf_id, "cea", {
            "intervention_name": inputs.get("intervention_name"),
            "time_horizon": inputs.get("time_horizon", 5),
        })

        # ── Step 1: Validate required fields ─────────────────────────────
        self.log_workflow_step(wf_id, "validate_inputs", "started")
        valid, errors = self.validate_workflow_inputs(inputs, "cea")
        if not valid:
            self.log_workflow_step(wf_id, "validate_inputs", "failed", {"errors": errors})
            raise WorkflowError(
                f"Input validation failed: {'; '.join(errors)}",
                workflow_id=wf_id,
                step="validate_inputs",
            )
        self.log_workflow_step(wf_id, "validate_inputs", "completed")

        # ── Step 2: NICE threshold context ────────────────────────────────
        self.log_workflow_step(wf_id, "nice_context", "started")
        nice_context: dict = {}
        try:
            condition = inputs.get("condition", inputs.get("intervention_name", ""))
            nice_context = get_nice_threshold_context(condition)
        except Exception as exc:
            self._log.warning("NICE context lookup failed (%s) — continuing", exc)
        self.log_workflow_step(wf_id, "nice_context", "completed",
                               {"context_keys": list(nice_context.keys())})

        # ── Step 3: Check R ───────────────────────────────────────────────
        self.log_workflow_step(wf_id, "check_r", "started")
        if not self._r_available:
            self.log_workflow_step(wf_id, "check_r", "failed",
                                   {"error": "Rscript not found on PATH"})
            raise WorkflowError(
                "R is not installed. Install R from https://cran.r-project.org/ "
                "and ensure 'Rscript' is on your PATH.",
                workflow_id=wf_id,
                step="check_r",
            )
        self.log_workflow_step(wf_id, "check_r", "completed")

        # ── Step 4: Run Markov model ──────────────────────────────────────
        self.log_workflow_step(wf_id, "run_markov", "started")
        try:
            cea_results = run_markov_with_validation(inputs)
        except (ValueError, Exception) as exc:
            self.log_workflow_step(wf_id, "run_markov", "failed", {"error": str(exc)})
            raise WorkflowError(str(exc), workflow_id=wf_id, step="run_markov") from exc
        self.log_workflow_step(wf_id, "run_markov", "completed", {
            "icer": cea_results.icer,
            "interpretation": cea_results.interpretation,
        })

        # ── Step 5: Generate CEA report ───────────────────────────────────
        self.log_workflow_step(wf_id, "generate_report", "started")
        report_path: Optional[str] = None
        try:
            markov_inputs = MarkovInputs(**inputs)
            path = generate_cea_report(markov_inputs, cea_results)
            report_path = str(path)
        except Exception as exc:
            self._log.warning("CEA report generation failed (%s)", exc)
        self.log_workflow_step(wf_id, "generate_report", "completed",
                               {"report_path": report_path})

        return {
            "workflow_id": wf_id,
            "status": "completed",
            "cea_results": cea_results.model_dump(),
            "nice_context": nice_context,
            "report_path": report_path,
        }

    # ── Combined BIA + CEA workflow ───────────────────────────────────────────

    def run_combined_workflow(
        self,
        bia_inputs: dict,
        mortality_reduction: float,
        utility_gain: float,
    ) -> dict:
        """Run BIA then derive Markov CEA parameters and run a combined analysis.

        The Markov CEA parameters are automatically derived from the BIA
        results using :meth:`_derive_markov_params`.  This avoids the need
        for the caller to supply overlapping inputs for both models.

        Args:
            bia_inputs:          Raw BIA input dict.
            mortality_reduction: Absolute reduction in annual mortality
                                 probability attributed to the intervention.
            utility_gain:        Additive QALY-weight improvement.

        Returns:
            Dict with all keys from :meth:`run_full_bia_workflow` plus
            ``cea_workflow_id``, ``cea_results``, ``nice_context``,
            ``cea_report_path``, and ``combined_report_path``.

        Raises:
            WorkflowError: If either the BIA or CEA sub-workflow fails.
        """
        wf_id = self._new_workflow_id("combined")
        self._init_workflow_state(wf_id, "combined", {
            "bia_setting": bia_inputs.get("setting"),
            "mortality_reduction": mortality_reduction,
            "utility_gain": utility_gain,
        })

        # ── Run BIA sub-workflow ──────────────────────────────────────────
        self.log_workflow_step(wf_id, "run_bia", "started")
        try:
            bia_output = self.run_full_bia_workflow(bia_inputs)
        except WorkflowError as exc:
            self.log_workflow_step(wf_id, "run_bia", "failed", {"error": str(exc)})
            raise WorkflowError(
                f"BIA sub-workflow failed: {exc}",
                workflow_id=wf_id,
                step="run_bia",
            ) from exc
        self.log_workflow_step(wf_id, "run_bia", "completed",
                               {"bia_workflow_id": bia_output["workflow_id"]})

        # ── Derive Markov parameters from BIA results ─────────────────────
        self.log_workflow_step(wf_id, "derive_markov_params", "started")
        try:
            enr_result = enrich_bia_inputs(bia_inputs)
            enriched_bia = dict(bia_inputs)
            enriched_bia.update(enr_result.get("suggested_values", {}))
            parsed_bia = BIAInputs(**enriched_bia)
            parsed_results = BIAResults(**bia_output["bia_results"])
            markov_params = self._derive_markov_params(
                parsed_bia, parsed_results, mortality_reduction, utility_gain
            )
        except Exception as exc:
            self.log_workflow_step(wf_id, "derive_markov_params", "failed",
                                   {"error": str(exc)})
            raise WorkflowError(str(exc), workflow_id=wf_id,
                                step="derive_markov_params") from exc
        self.log_workflow_step(wf_id, "derive_markov_params", "completed",
                               {"markov_params_keys": list(markov_params.keys())})

        # ── Run CEA sub-workflow ──────────────────────────────────────────
        self.log_workflow_step(wf_id, "run_cea", "started")
        try:
            cea_output = self.run_full_cea_workflow(markov_params)
        except WorkflowError as exc:
            self.log_workflow_step(wf_id, "run_cea", "failed", {"error": str(exc)})
            raise WorkflowError(
                f"CEA sub-workflow failed: {exc}",
                workflow_id=wf_id,
                step="run_cea",
            ) from exc
        self.log_workflow_step(wf_id, "run_cea", "completed",
                               {"cea_workflow_id": cea_output["workflow_id"]})

        self.log_workflow_step(wf_id, "generate_report", "completed", {
            "bia_report_path": bia_output.get("report_path"),
            "cea_report_path": cea_output.get("report_path"),
        })
        self._workflow_log[wf_id]["status"] = "completed"
        self._persist_workflow_state(wf_id)

        return {
            **bia_output,
            "workflow_id": wf_id,
            "status": "completed",
            "cea_workflow_id": cea_output["workflow_id"],
            "cea_results": cea_output["cea_results"],
            "nice_context": cea_output.get("nice_context", {}),
            "cea_report_path": cea_output.get("report_path"),
            "combined_report_path": None,  # placeholder for a merged deck
        }

    # ── SLR workflow ──────────────────────────────────────────────────────────

    def run_slr_workflow(
        self,
        pico: dict,
        abstracts: list[dict],
        batch_size: int = 10,
    ) -> dict:
        """Run an AI-powered abstract screening workflow.

        Steps
        -----
        1. Validate PICO fields.
        2. Parse PICO dict into :class:`~engines.slr.schema.PICOCriteria`.
        3. Parse abstracts into :class:`~engines.slr.schema.Abstract` objects.
        4. Create a :class:`~engines.slr.schema.ScreeningBatch`.
        5. Screen abstracts against the PICO via Claude.
        6. Populate batch with decisions and save to disk.
        7. Export results to CSV.
        8. Return summary dict.

        Args:
            pico:       PICO criteria dict — must contain ``population``,
                        ``intervention``, ``comparison``, and ``outcomes``.
            abstracts:  List of abstract dicts (see
                        :class:`~engines.slr.schema.Abstract`).
            batch_size: Number of abstracts per Claude API call (default 10).

        Returns:
            Dict with keys: ``workflow_id``, ``batch_id``, ``total``,
            ``included``, ``excluded``, ``uncertain``, ``batch_path``,
            ``export_path``, ``status``.

        Raises:
            WorkflowError: If PICO validation fails or the Anthropic API key
                is not set.
        """
        wf_id = self._new_workflow_id("slr")
        self._init_workflow_state(wf_id, "slr", {
            "population": pico.get("population"),
            "intervention": pico.get("intervention"),
            "n_abstracts": len(abstracts),
        })

        # ── Step 1: Validate PICO ─────────────────────────────────────────
        self.log_workflow_step(wf_id, "validate_pico", "started")
        valid, errors = self.validate_workflow_inputs(pico, "slr")
        if not valid:
            self.log_workflow_step(wf_id, "validate_pico", "failed", {"errors": errors})
            raise WorkflowError(
                f"PICO validation failed: {'; '.join(errors)}",
                workflow_id=wf_id,
                step="validate_pico",
            )
        self.log_workflow_step(wf_id, "validate_pico", "completed")

        # ── Step 2: Parse PICO ────────────────────────────────────────────
        self.log_workflow_step(wf_id, "parse_pico", "started")
        try:
            pico_criteria = PICOCriteria(
                population=pico["population"],
                intervention=pico["intervention"],
                comparison=pico["comparison"],
                outcomes=pico["outcomes"],
                study_types=pico.get("study_types", ["RCT"]),
                exclusion_criteria=pico.get("exclusion_criteria", []),
            )
        except Exception as exc:
            self.log_workflow_step(wf_id, "parse_pico", "failed", {"error": str(exc)})
            raise WorkflowError(str(exc), workflow_id=wf_id, step="parse_pico") from exc
        self.log_workflow_step(wf_id, "parse_pico", "completed")

        # ── Step 3: Parse abstracts ───────────────────────────────────────
        self.log_workflow_step(wf_id, "parse_abstracts", "started")
        try:
            abstract_objs = [Abstract(**a) for a in abstracts]
        except Exception as exc:
            self.log_workflow_step(wf_id, "parse_abstracts", "failed",
                                   {"error": str(exc)})
            raise WorkflowError(str(exc), workflow_id=wf_id,
                                step="parse_abstracts") from exc
        self.log_workflow_step(wf_id, "parse_abstracts", "completed",
                               {"count": len(abstract_objs)})

        # ── Step 4: Create batch ──────────────────────────────────────────
        self.log_workflow_step(wf_id, "create_batch", "started")
        batch = create_screening_batch(abstract_objs, pico_criteria)
        self.log_workflow_step(wf_id, "create_batch", "completed",
                               {"batch_id": batch.batch_id})

        # ── Step 5: Screen abstracts ──────────────────────────────────────
        self.log_workflow_step(wf_id, "screen_abstracts", "started")
        try:
            decisions = screen_abstracts(abstract_objs, pico_criteria, batch_size=batch_size)
        except EnvironmentError as exc:
            self.log_workflow_step(wf_id, "screen_abstracts", "failed",
                                   {"error": str(exc)})
            raise WorkflowError(str(exc), workflow_id=wf_id,
                                step="screen_abstracts") from exc
        except Exception as exc:
            self.log_workflow_step(wf_id, "screen_abstracts", "failed",
                                   {"error": str(exc)})
            raise WorkflowError(str(exc), workflow_id=wf_id,
                                step="screen_abstracts") from exc

        for decision in decisions:
            batch.add_decision(decision)

        self.log_workflow_step(wf_id, "screen_abstracts", "completed", {
            "total": len(decisions),
            "included": sum(1 for d in decisions if d.decision.value == "include"),
            "excluded": sum(1 for d in decisions if d.decision.value == "exclude"),
            "uncertain": sum(1 for d in decisions if d.decision.value == "uncertain"),
        })

        # ── Step 6: Save batch ────────────────────────────────────────────
        self.log_workflow_step(wf_id, "save_batch", "started")
        batch_path: Optional[str] = None
        try:
            bp = save_batch(batch)
            batch_path = str(bp)
        except Exception as exc:
            self._log.warning("Could not save batch: %s", exc)
        self.log_workflow_step(wf_id, "save_batch", "completed",
                               {"batch_path": batch_path})

        # ── Step 7: Export CSV ────────────────────────────────────────────
        self.log_workflow_step(wf_id, "export_csv", "started")
        export_path: Optional[str] = None
        try:
            ep = export_screening_results(batch, format="csv")
            export_path = str(ep)
        except Exception as exc:
            self._log.warning("Could not export CSV: %s", exc)
        self.log_workflow_step(wf_id, "export_csv", "completed",
                               {"export_path": export_path})

        self._workflow_log[wf_id]["status"] = "completed"
        self._persist_workflow_state(wf_id)

        included = [d for d in decisions if d.decision.value == "include"]
        excluded = [d for d in decisions if d.decision.value == "exclude"]
        uncertain = [d for d in decisions if d.decision.value == "uncertain"]

        return {
            "workflow_id": wf_id,
            "status": "completed",
            "batch_id": batch.batch_id,
            "total": len(decisions),
            "included": len(included),
            "excluded": len(excluded),
            "uncertain": len(uncertain),
            "batch_path": batch_path,
            "export_path": export_path,
        }

    # ── Evidence enrichment workflow ──────────────────────────────────────────

    def run_evidence_enrichment(self, partial_inputs: dict) -> dict:
        """Enrich a partial BIA input dict with NHS reference data and NICE guidance.

        This is a lighter-weight workflow than :meth:`run_full_bia_workflow`.
        It enriches the inputs and returns NICE context and suggested
        comparators, but does **not** run the BIA calculation or generate a
        report.

        Args:
            partial_inputs: Partial BIA input dict — only ``setting`` and
                ``price`` are truly required; all other fields will be filled
                in with sensible defaults by the evidence agent.

        Returns:
            Dict with keys: ``workflow_id``, ``enriched_inputs``,
            ``nice_context``, ``comparators``, ``status``.
        """
        wf_id = self._new_workflow_id("enrich")
        self._init_workflow_state(wf_id, "enrich", {
            "setting": partial_inputs.get("setting"),
            "n_input_keys": len(partial_inputs),
        })

        # ── Enrich inputs ─────────────────────────────────────────────────
        self.log_workflow_step(wf_id, "enrich_inputs", "started")
        enriched: dict = dict(partial_inputs)
        try:
            enriched = enrich_bia_inputs(partial_inputs)
        except Exception as exc:
            self._log.warning("Enrichment failed (%s) — returning raw inputs", exc)
        self.log_workflow_step(wf_id, "enrich_inputs", "completed",
                               {"enriched_keys": len(enriched)})

        # ── NICE context ──────────────────────────────────────────────────
        self.log_workflow_step(wf_id, "nice_context", "started")
        nice_context: dict = {}
        try:
            condition = partial_inputs.get("condition", "")
            nice_context = get_nice_threshold_context(condition)
        except Exception as exc:
            self._log.warning("NICE context lookup failed (%s)", exc)
        self.log_workflow_step(wf_id, "nice_context", "completed")

        # ── Comparators ───────────────────────────────────────────────────
        self.log_workflow_step(wf_id, "get_comparators", "started")
        comparators: list[dict] = []
        try:
            condition = partial_inputs.get("condition", "")
            intervention_type = partial_inputs.get("intervention_type", "digital")
            comparators = get_nice_comparators(condition, intervention_type)
        except Exception as exc:
            self._log.warning("Comparator lookup failed (%s)", exc)
        self.log_workflow_step(wf_id, "get_comparators", "completed",
                               {"n_comparators": len(comparators)})

        self._workflow_log[wf_id]["status"] = "completed"
        self._persist_workflow_state(wf_id)

        return {
            "workflow_id": wf_id,
            "status": "completed",
            "enriched_inputs": enriched,
            "nice_context": nice_context,
            "comparators": comparators,
        }
