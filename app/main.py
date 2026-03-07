"""HEOR Engine – FastAPI backend."""

import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from typing import Any, Dict, List, Optional

import asyncio
import threading
from collections import defaultdict

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engines.bia.schema import BIAInputs, BIAResults
from engines.bia.cost_translator import calculate_workforce_cost
from engines.bia.model import calculate_budget_impact, calculate_scenarios
from engines.bia.validation import validate_clinical_sense, suggest_missing_inputs, estimate_confidence
from engines.reports.pptx_builder import (
    add_cea_slides_to_bia_report,
    generate_bia_report,
    generate_cea_report,
)
from engines.markov.schema import MarkovInputs, MarkovResults
from engines.markov.runner import (
    RScriptError,
    check_r_installed,
    run_markov_model,
    run_markov_with_validation,
)
from agents.evidence_agent import (
    enrich_bia_inputs,
    fetch_nhs_reference_costs,
    fetch_ons_population_data,
    get_cost_by_category,
    get_nice_comparators,
    get_nice_threshold_context,
    get_population_by_region,
    search_nice_guidance,
    search_reference_costs,
    validate_against_references,
)
from engines.slr.schema import (
    Abstract,
    PICOCriteria,
    ScreeningBatch,
    ScreeningDecision,
)
from engines.slr.screener import (
    ANTHROPIC_API_KEY as _SLR_API_KEY,
    _BATCHES_DIR as _SLR_BATCHES_DIR,
    _EXPORTS_DIR as _SLR_EXPORTS_DIR,
    create_screening_batch,
    export_screening_results,
    load_batch,
    save_batch,
    screen_abstracts,
)
from agents.auto_populate import AutoPopulator
from agents.orchestrator import HEOROrchestrator, WorkflowError
from agents.workflow_schema import (
    BIAWorkflowRequest,
    BIAWorkflowResponse,
    CEAWorkflowRequest,
    CEAWorkflowResponse,
    CombinedWorkflowRequest,
    CombinedWorkflowResponse,
    SLRWorkflowRequest,
    SLRWorkflowResponse,
    WorkflowStatus,
)

import os
from pathlib import Path

# Writable directory in serverless environments
BASE_STORAGE = Path(os.getenv("HEOR_STORAGE_DIR", "/tmp/heor"))

SUBMISSIONS_DIR = BASE_STORAGE / "submissions"
REPORTS_DIR = BASE_STORAGE / "reports"
_WORKFLOWS_DIR = BASE_STORAGE / "workflows"

# Ensure directories exist
for d in [SUBMISSIONS_DIR, REPORTS_DIR, _WORKFLOWS_DIR]:
    d.mkdir(parents=True, exist_ok=True)
# Shared orchestrator — initialised once at import time.
_orchestrator = HEOROrchestrator()

@app.get("/")
def root():
    return {"message": "HEOR Engine API running"}

app = FastAPI(
    title="HEOR Engine API",
    version="0.2.0",
    description=(
        "Health Economics & Outcomes Research Engine: Budget Impact Analysis, "
        "Cost-Effectiveness Analysis (Markov) and AI abstract screening (SLR)."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_execution_time_header(request: Request, call_next: Any) -> Response:
    """Attach ``X-Execution-Time-Ms`` to every response for client-side timing.

    The header value is the wall-clock time in milliseconds from the first
    byte of the request to the last byte of the response body, rounded to
    one decimal place.
    """
    t0 = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    response.headers["X-Execution-Time-Ms"] = str(elapsed_ms)
    return response


# ── Endpoints ──────────────────────────────────────────────────────────

@app.post("/api/health")
def health_check():
    return {"status": "ok"}


@app.post("/api/inputs")
def save_inputs(inputs: BIAInputs):
    try:
        workforce_cost = calculate_workforce_cost(
            [row.model_dump() for row in inputs.workforce]
        )

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        payload = json.loads(inputs.model_dump_json())
        payload["workforce_cost_per_patient"] = workforce_cost

        filepath = SUBMISSIONS_DIR / f"{ts}.json"
        filepath.write_text(json.dumps(payload, indent=2))

        return {
            "id": ts,
            "status": "saved",
            "workforce_cost_per_patient": workforce_cost,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save inputs: {e}")


@app.post("/api/calculate-bia")
def run_bia(submission_id: str):
    # 1. Check the submission file exists
    filepath = SUBMISSIONS_DIR / f"{submission_id}.json"
    if not filepath.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Submission '{submission_id}' not found",
        )

    # 2. Load and validate the saved inputs
    try:
        raw = json.loads(filepath.read_text())
        inputs = BIAInputs(**raw)
    except ValidationError as e:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Saved submission data failed validation",
                "errors": e.errors(),
            },
        )

    # 3. Clinical-sense validation
    warnings = validate_clinical_sense(inputs)
    suggestions = suggest_missing_inputs(inputs)
    confidence = estimate_confidence(inputs)

    # 4. Run the full scenario analysis
    try:
        scenarios = calculate_scenarios(inputs)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"BIA calculation failed: {e}",
        )

    # 5. Return structured results for all three scenarios
    base = scenarios["base"]
    return {
        "submission_id": submission_id,
        "validation": {
            "warnings": warnings,
            "suggestions": suggestions,
            "confidence": confidence,
        },
        "summary": {
            "eligible_patients": inputs.eligible_patients,
            "treated_patients": inputs.treated_patients_by_year,
            "current_pathway_cost_per_patient": round(
                sum(
                    calculate_workforce_cost(
                        [r.model_dump() for r in inputs.workforce]
                    )
                    for _ in [None]
                ),
                2,
            ),
        },
        "base": base.model_dump(),
        "conservative": scenarios["conservative"].model_dump(),
        "optimistic": scenarios["optimistic"].model_dump(),
    }


@app.get("/api/submissions")
def list_submissions():
    files = sorted(SUBMISSIONS_DIR.glob("*.json"), reverse=True)
    submissions = []
    for f in files:
        submission_id = f.stem
        # Parse timestamp from the ID format: YYYYMMDD_HHMMSS_ffffff
        parts = submission_id.split("_")
        if len(parts) >= 2:
            try:
                ts = datetime.strptime(
                    f"{parts[0]}_{parts[1]}", "%Y%m%d_%H%M%S"
                )
                created_at = ts.strftime("%Y-%m-%d %H:%M:%S UTC")
            except ValueError:
                created_at = "unknown"
        else:
            created_at = "unknown"

        submissions.append({
            "id": submission_id,
            "created_at": created_at,
        })

    return {"count": len(submissions), "submissions": submissions}


# ── Report generation ─────────────────────────────────────────────────

class GenerateReportRequest(BaseModel):
    submission_id: str
    intervention_name: Optional[str] = "Medical Device"


@app.post("/api/generate-report")
def create_report(body: GenerateReportRequest):
    # 1. Load saved inputs
    filepath = SUBMISSIONS_DIR / f"{body.submission_id}.json"
    if not filepath.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Submission '{body.submission_id}' not found",
        )

    try:
        raw = json.loads(filepath.read_text())
        inputs = BIAInputs(**raw)
    except ValidationError as e:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Saved submission data failed validation",
                "errors": e.errors(),
            },
        )

    # 2. Run scenario analysis
    try:
        scenarios = calculate_scenarios(inputs)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"BIA calculation failed: {e}",
        )

    # 3. Build the full results dict expected by generate_bia_report
    results = {
        "submission_id": body.submission_id,
        "validation": {
            "warnings": validate_clinical_sense(inputs),
            "suggestions": suggest_missing_inputs(inputs),
            "confidence": estimate_confidence(inputs),
        },
        "summary": {
            "eligible_patients": inputs.eligible_patients,
            "treated_patients": inputs.treated_patients_by_year,
        },
        "base": scenarios["base"].model_dump(),
        "conservative": scenarios["conservative"].model_dump(),
        "optimistic": scenarios["optimistic"].model_dump(),
    }

    # 4. Generate the PPTX report
    try:
        report_path = generate_bia_report(inputs, results, body.submission_id)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Report generation failed: {e}",
        )

    return {
        "filepath": report_path,
        "download_url": f"/api/download-report/{body.submission_id}",
        "message": "Report generated successfully",
    }


@app.get("/api/download-report/{submission_id}")
def download_report(submission_id: str):
    report_path = REPORTS_DIR / f"{submission_id}.pptx"
    if not report_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Report for submission '{submission_id}' not found. "
            f"Generate it first via POST /api/generate-report.",
        )

    return FileResponse(
        path=str(report_path),
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=f"HEOR_BIA_Report_{submission_id}.pptx",
        headers={
            "Content-Disposition": f'attachment; filename="HEOR_BIA_Report_{submission_id}.pptx"',
        },
    )


# ── Markov / ICER endpoints ──────────────────────────────────────────

class ICERFromBIARequest(BaseModel):
    """Request body for deriving Markov/ICER inputs from an existing BIA submission."""

    submission_id: str = Field(
        ..., description="ID of an existing BIA submission"
    )
    mortality_reduction: float = Field(
        ..., gt=0, le=100,
        description="Percentage reduction in annual mortality due to treatment (e.g. 50 = halves mortality)",
    )
    utility_gain: float = Field(
        ..., gt=0, le=1,
        description="Absolute improvement in utility/QoL weight (e.g. 0.10 adds 0.10 to baseline)",
    )
    base_mortality: float = Field(
        0.08, ge=0, le=1,
        description="Assumed annual mortality under standard care (default 0.08 = 8%)",
    )
    base_utility: float = Field(
        0.70, ge=0, le=1,
        description="Assumed utility under standard care (default 0.70)",
    )
    time_horizon: int = Field(
        5, gt=0, le=50,
        description="Markov model time horizon in years (default 5)",
    )


# Condition-specific defaults for the Markov model
_CONDITION_DEFAULTS = {
    "cancer": {
        "label": "Cancer",
        "description": "Oncology — solid tumours, haematological malignancies",
        "prob_death_standard": 0.15,
        "utility_standard": 0.60,
        "typical_annual_cost": 12000,
    },
    "cardiovascular": {
        "label": "Cardiovascular",
        "description": "Heart failure, coronary artery disease, stroke",
        "prob_death_standard": 0.08,
        "utility_standard": 0.70,
        "typical_annual_cost": 6000,
    },
    "diabetes": {
        "label": "Diabetes",
        "description": "Type 1 & Type 2 diabetes mellitus",
        "prob_death_standard": 0.05,
        "utility_standard": 0.75,
        "typical_annual_cost": 4000,
    },
    "respiratory": {
        "label": "Respiratory",
        "description": "COPD, asthma, pulmonary fibrosis",
        "prob_death_standard": 0.10,
        "utility_standard": 0.65,
        "typical_annual_cost": 5000,
    },
}


@app.post(
    "/api/calculate-icer",
    summary="Run Markov cost-effectiveness analysis",
    response_description="Full Markov results including ICER and NICE threshold assessment",
)
def calculate_icer_endpoint(inputs: MarkovInputs):
    """Run a 2-state Markov model and return cost-effectiveness results.

    Accepts full Markov parameters (both arms) and returns discounted
    costs, QALYs, ICER, and a plain-English interpretation against
    NICE willingness-to-pay thresholds.

    **Example request body:**
    ```json
    {
        "intervention_name": "New Cancer Drug",
        "time_horizon": 5,
        "prob_death_standard": 0.10,
        "cost_standard_annual": 8000,
        "utility_standard": 0.60,
        "prob_death_treatment": 0.05,
        "cost_treatment_annual": 15000,
        "cost_treatment_initial": 30000,
        "utility_treatment": 0.75
    }
    ```
    """
    if not check_r_installed():
        raise HTTPException(
            status_code=503,
            detail="R is not installed on the server. "
            "Install R from https://cran.r-project.org/ to enable Markov models.",
        )

    try:
        results = run_markov_model(inputs)
    except RScriptError as e:
        raise HTTPException(
            status_code=500,
            detail=f"R script failed: {e.stderr.strip()}",
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return results.model_dump()


@app.post(
    "/api/generate-cea-report",
    summary="Generate a standalone CEA PowerPoint report",
    response_description="Filepath and download URL for the CEA report",
)
def generate_cea_report_endpoint(inputs: MarkovInputs):
    """Run the Markov model and generate a 6-slide CEA slide deck.

    Returns the ICER results alongside a downloadable PPTX report.
    """
    if not check_r_installed():
        raise HTTPException(
            status_code=503,
            detail="R is not installed on the server. "
            "Install R from https://cran.r-project.org/ to enable Markov models.",
        )

    try:
        results = run_markov_model(inputs)
    except RScriptError as e:
        raise HTTPException(
            status_code=500, detail=f"R script failed: {e.stderr.strip()}",
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Generate a submission-style ID for the report filename
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")

    try:
        report_path = generate_cea_report(inputs, results, ts)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Report generation failed: {e}",
        )

    return {
        "filepath": report_path,
        "download_url": f"/api/download-cea-report/{ts}",
        "message": "CEA report generated successfully",
        "results": results.model_dump(),
    }


@app.get("/api/download-cea-report/{report_id}")
def download_cea_report(report_id: str):
    """Download a previously generated CEA report."""
    report_path = REPORTS_DIR / f"CEA_{report_id}.pptx"
    if not report_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"CEA report '{report_id}' not found. "
            f"Generate it first via POST /api/generate-cea-report.",
        )

    return FileResponse(
        path=str(report_path),
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=f"HEOR_CEA_Report_{report_id}.pptx",
        headers={
            "Content-Disposition": f'attachment; filename="HEOR_CEA_Report_{report_id}.pptx"',
        },
    )


@app.post(
    "/api/calculate-icer-from-bia",
    summary="Derive Markov/ICER analysis from a BIA submission",
    response_description="Combined BIA summary and Markov cost-effectiveness results",
)
def calculate_icer_from_bia(body: ICERFromBIARequest):
    """Bridge a BIA submission into a Markov cost-effectiveness analysis.

    Loads the saved BIA inputs, extracts cost-per-patient as the treatment
    cost, applies the provided mortality reduction and utility gain, and
    runs a 2-state Markov model.

    **Example request body:**
    ```json
    {
        "submission_id": "20260223_141500_000000",
        "mortality_reduction": 50,
        "utility_gain": 0.15,
        "base_mortality": 0.08,
        "base_utility": 0.70,
        "time_horizon": 5
    }
    ```
    """
    # 1. Load BIA submission
    filepath = SUBMISSIONS_DIR / f"{body.submission_id}.json"
    if not filepath.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Submission '{body.submission_id}' not found",
        )

    try:
        raw = json.loads(filepath.read_text())
        bia_inputs = BIAInputs(**raw)
    except ValidationError as e:
        raise HTTPException(
            status_code=422,
            detail={"message": "Saved submission data failed validation", "errors": e.errors()},
        )

    # 2. Run BIA scenarios for the summary
    try:
        scenarios = calculate_scenarios(bia_inputs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"BIA calculation failed: {e}")

    base_bia = scenarios["base"].model_dump()

    # 3. Derive Markov parameters from BIA data
    # Treatment annual cost = BIA cost per patient (year 1)
    cost_treatment_annual = base_bia["cost_per_patient"][0] if base_bia["cost_per_patient"] else 0
    # Standard care annual cost = workforce + resource costs (no intervention)
    cost_standard_annual = calculate_workforce_cost(
        [r.model_dump() for r in bia_inputs.workforce]
    )

    # Apply mortality reduction
    prob_death_treatment = body.base_mortality * (1 - body.mortality_reduction / 100)
    # Apply utility gain (capped at 1.0)
    utility_treatment = min(body.base_utility + body.utility_gain, 1.0)

    # Initial cost = setup cost + device price (if applicable)
    cost_treatment_initial = bia_inputs.setup_cost + bia_inputs.price

    markov_inputs = MarkovInputs(
        intervention_name=f"BIA Submission {body.submission_id}",
        time_horizon=body.time_horizon,
        prob_death_standard=body.base_mortality,
        cost_standard_annual=cost_standard_annual,
        utility_standard=body.base_utility,
        prob_death_treatment=prob_death_treatment,
        cost_treatment_annual=cost_treatment_annual,
        cost_treatment_initial=cost_treatment_initial,
        utility_treatment=utility_treatment,
    )

    # 4. Check R and run Markov model
    if not check_r_installed():
        raise HTTPException(
            status_code=503,
            detail="R is not installed on the server. "
            "Install R from https://cran.r-project.org/ to enable Markov models.",
        )

    try:
        markov_results = run_markov_model(markov_inputs)
    except RScriptError as e:
        raise HTTPException(status_code=500, detail=f"R script failed: {e.stderr.strip()}")
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # 5. Return combined results
    return {
        "submission_id": body.submission_id,
        "bia_summary": {
            "eligible_patients": bia_inputs.eligible_patients,
            "treated_patients": bia_inputs.treated_patients_by_year,
            "annual_budget_impact": base_bia["annual_budget_impact"],
            "cost_per_patient": base_bia["cost_per_patient"],
        },
        "markov_inputs": {
            "intervention_name": markov_inputs.intervention_name,
            "time_horizon": markov_inputs.time_horizon,
            "prob_death_standard": markov_inputs.prob_death_standard,
            "cost_standard_annual": markov_inputs.cost_standard_annual,
            "utility_standard": markov_inputs.utility_standard,
            "prob_death_treatment": markov_inputs.prob_death_treatment,
            "cost_treatment_annual": markov_inputs.cost_treatment_annual,
            "cost_treatment_initial": markov_inputs.cost_treatment_initial,
            "utility_treatment": markov_inputs.utility_treatment,
        },
        "markov_results": markov_results.model_dump(),
    }


@app.get(
    "/api/markov-defaults",
    summary="Default Markov parameters by condition type",
    response_description="Condition-specific mortality, utility, and cost defaults",
)
def get_markov_defaults():
    """Return typical default values for different clinical condition types.

    These values are population-level averages from published literature and
    NICE technology appraisals.  They are intended as starting points — users
    should adjust to their specific population and intervention.

    **Conditions covered:** Cancer, Cardiovascular, Diabetes, Respiratory.
    """
    return {
        "conditions": _CONDITION_DEFAULTS,
        "model_defaults": {
            "time_horizon": 5,
            "cycle_length": 1.0,
            "discount_rate": 0.035,
        },
        "nice_thresholds": {
            "standard": 25000,
            "extended": 35000,
            "end_of_life": 50000,
            "description": "NICE willingness-to-pay thresholds (£/QALY)",
        },
    }


# ── Combined BIA + CEA report ────────────────────────────────────────

class CombinedReportRequest(BaseModel):
    """Request body for generating a combined BIA + CEA PowerPoint report."""

    bia_submission_id: str = Field(
        ..., description="ID of an existing BIA submission"
    )
    markov_params: dict = Field(
        ...,
        description="Markov model parameters matching MarkovInputs schema "
        "(intervention_name, prob_death_standard, cost_standard_annual, "
        "utility_standard, prob_death_treatment, cost_treatment_annual, "
        "utility_treatment, etc.)",
    )
    intervention_name: Optional[str] = Field(
        None,
        description="Override intervention name for the report title "
        "(defaults to markov_params.intervention_name)",
    )


@app.post(
    "/api/generate-combined-report",
    summary="Generate combined BIA + CEA PowerPoint report",
    response_description="Filepath and download URL for the combined report",
)
def generate_combined_report(body: CombinedReportRequest):
    """Generate a single PPTX containing both BIA and CEA slides.

    Loads the BIA submission, runs scenario analysis, validates the Markov
    parameters, runs the cost-effectiveness model via R, and produces a
    combined slide deck with budget impact slides followed by
    cost-effectiveness slides and a final summary.

    **Example request body:**
    ```json
    {
        "bia_submission_id": "20260224_120000_000000",
        "markov_params": {
            "intervention_name": "AI Wound Camera",
            "time_horizon": 5,
            "prob_death_standard": 0.08,
            "cost_standard_annual": 12000,
            "utility_standard": 0.65,
            "prob_death_treatment": 0.04,
            "cost_treatment_annual": 8000,
            "cost_treatment_initial": 25000,
            "utility_treatment": 0.80
        }
    }
    ```
    """
    # ── 1. Load and validate BIA submission ──────────────────────────
    filepath = SUBMISSIONS_DIR / f"{body.bia_submission_id}.json"
    if not filepath.exists():
        raise HTTPException(
            status_code=404,
            detail=f"BIA submission '{body.bia_submission_id}' not found",
        )

    try:
        raw = json.loads(filepath.read_text())
        bia_inputs = BIAInputs(**raw)
    except ValidationError as e:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "BIA submission data failed validation",
                "errors": e.errors(),
            },
        )

    # ── 2. Run BIA scenario analysis ─────────────────────────────────
    try:
        scenarios = calculate_scenarios(bia_inputs)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"BIA calculation failed: {e}",
        )

    bia_results = {
        "validation": {
            "warnings": validate_clinical_sense(bia_inputs),
            "suggestions": suggest_missing_inputs(bia_inputs),
            "confidence": estimate_confidence(bia_inputs),
        },
        "summary": {
            "eligible_patients": bia_inputs.eligible_patients,
            "treated_patients": bia_inputs.treated_patients_by_year,
        },
        "base": scenarios["base"].model_dump(),
        "conservative": scenarios["conservative"].model_dump(),
        "optimistic": scenarios["optimistic"].model_dump(),
    }

    # ── 3. Validate Markov parameters ────────────────────────────────
    markov_dict = dict(body.markov_params)
    if body.intervention_name:
        markov_dict["intervention_name"] = body.intervention_name

    try:
        markov_inputs = MarkovInputs(**markov_dict)
    except ValidationError as e:
        errors = "; ".join(
            f"{err['loc'][-1]}: {err['msg']}" for err in e.errors()
        )
        raise HTTPException(
            status_code=422,
            detail=f"Invalid Markov parameters — {errors}",
        )

    # ── 4. Run Markov model ──────────────────────────────────────────
    if not check_r_installed():
        raise HTTPException(
            status_code=503,
            detail="R is not installed on the server. "
            "Install R from https://cran.r-project.org/ to enable Markov models.",
        )

    try:
        markov_results = run_markov_model(markov_inputs)
    except RScriptError as e:
        raise HTTPException(
            status_code=500, detail=f"R script failed: {e.stderr.strip()}",
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # ── 5. Generate combined report ──────────────────────────────────
    try:
        report_path = add_cea_slides_to_bia_report(
            bia_inputs, bia_results, markov_inputs, markov_results,
            body.bia_submission_id,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Report generation failed: {e}",
        )

    # ── 6. Build summary for the response ────────────────────────────
    base_bia = bia_results["base"]
    three_year_total = sum(base_bia["annual_budget_impact"])

    icer = markov_results.icer
    icer_display = f"£{icer:,.0f}/QALY" if icer is not None else "N/A"

    if markov_results.cost_effective_25k and three_year_total < 0:
        recommendation = "Strong case for adoption — cost-saving BIA and cost-effective CEA"
    elif markov_results.cost_effective_35k and three_year_total < 0:
        recommendation = "Good case for adoption — cost-saving BIA, within extended NICE threshold"
    elif markov_results.cost_effective_25k:
        recommendation = "Cost-effective at standard NICE threshold — review budget impact trajectory"
    elif markov_results.cost_effective_35k:
        recommendation = "Potentially cost-effective — may require commissioner case"
    else:
        recommendation = "Above NICE threshold — consider price negotiation or restricted population"

    return {
        "filepath": report_path,
        "download_url": f"/api/download-combined-report/{body.bia_submission_id}",
        "message": "Combined BIA + CEA report generated successfully",
        "summary": {
            "budget_impact_3yr": f"£{three_year_total:,.0f}",
            "icer": icer_display,
            "cost_effective_25k": markov_results.cost_effective_25k,
            "cost_effective_35k": markov_results.cost_effective_35k,
            "interpretation": markov_results.interpretation,
            "recommendation": recommendation,
        },
    }


@app.get("/api/download-combined-report/{submission_id}")
def download_combined_report(submission_id: str):
    """Download a previously generated combined BIA + CEA report."""
    report_path = REPORTS_DIR / f"BIA_CEA_{submission_id}.pptx"
    if not report_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Combined report for '{submission_id}' not found. "
            f"Generate it first via POST /api/generate-combined-report.",
        )

    return FileResponse(
        path=str(report_path),
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=f"HEOR_BIA_CEA_Report_{submission_id}.pptx",
        headers={
            "Content-Disposition": f'attachment; filename="HEOR_BIA_CEA_Report_{submission_id}.pptx"',
        },
    )


# ── Evidence / Reference data endpoints ──────────────────────────────

# ── Pydantic models ───────────────────────────────────────────────────

class EnrichInputsRequest(BaseModel):
    """Partial BIA inputs to be enriched with reference data.

    All fields are optional. Provide as many or as few as are known —
    the enrichment function will fill gaps from NHS, ONS, and NICE
    reference data and flag anything that looks implausible.
    """

    condition: Optional[str] = Field(
        None,
        description="Clinical condition (e.g. 'Heart Failure', 'Diabetes')",
        examples=["Heart Failure"],
    )
    intervention_type: Optional[str] = Field(
        None,
        description="Type of intervention (e.g. 'remote_monitoring', 'digital', 'ai')",
        examples=["remote_monitoring"],
    )
    catchment_size: Optional[int] = Field(
        None,
        gt=0,
        description="Total catchment population. Estimated from bed count or region if omitted.",
        examples=[250000],
    )
    bed_count: Optional[int] = Field(
        None,
        gt=0,
        description="Number of staffed beds — used to estimate catchment when catchment_size is absent.",
        examples=[300],
    )
    eligible_pct: Optional[float] = Field(
        None,
        gt=0,
        le=1,
        description="Fraction of catchment eligible for the intervention. "
                    "Suggested from ONS prevalence if omitted.",
        examples=[0.063],
    )
    region: Optional[str] = Field(
        None,
        description="NHS England region name (e.g. 'North West', 'London'). "
                    "Used to estimate catchment when other signals are absent.",
        examples=["North West"],
    )
    costs: Optional[Dict[str, float]] = Field(
        None,
        description="User-supplied unit costs keyed by pathway label "
                    "(e.g. {'outpatient_visit': 80, 'device_cost': 500}). "
                    "Compared against NHS reference costs; discrepancies are flagged.",
        examples=[{"outpatient_visit": 80, "device_cost": 500}],
    )

    model_config = {"extra": "allow"}


class ValidateRequest(BaseModel):
    """BIA inputs and modelled results to validate against NICE precedents."""

    inputs: Dict[str, Any] = Field(
        ...,
        description="BIA input parameters (same structure as EnrichInputsRequest).",
    )
    results: Dict[str, Any] = Field(
        ...,
        description="BIA/CEA model outputs. Recognised keys: "
                    "icer (£/QALY), net_savings (£), intervention_cost (£), "
                    "year1_uptake (fraction), annual_savings (list of £).",
        examples=[{
            "icer": 18500,
            "net_savings": 200000,
            "intervention_cost": 100000,
            "year1_uptake": 0.12,
            "annual_savings": [60000, 70000, 70000],
        }],
    )


# ── GET /api/evidence/reference-costs ────────────────────────────────

@app.get(
    "/api/evidence/reference-costs",
    summary="NHS reference costs",
    response_description="NHS National Cost Collection 2024/25 unit costs with optional filtering",
    tags=["Evidence"],
)
def get_reference_costs(
    search: Optional[str] = Query(
        None,
        description="Free-text keyword filter (e.g. 'bed day', 'outpatient', 'mri'). "
                    "Case-insensitive; underscores and spaces are equivalent.",
        examples=["bed day"],
    ),
    category: Optional[str] = Query(
        None,
        description="Filter by cost category. One of: inpatient, outpatient, emergency, "
                    "diagnostics, procedures, ambulance, community.",
        examples=["inpatient"],
    ),
):
    """Return NHS National Cost Collection 2024/25 unit costs (GBP).

    Results can be narrowed by keyword (`search`) and/or category.
    When both are provided the category filter is applied first, then
    the keyword search is run over the filtered set.

    **Example responses:**

    - `GET /api/evidence/reference-costs` → all 37 cost items
    - `GET /api/evidence/reference-costs?category=inpatient` → 7 bed-day costs
    - `GET /api/evidence/reference-costs?search=mri` → MRI scan cost
    - `GET /api/evidence/reference-costs?category=diagnostics&search=ct` → CT costs only
    """
    try:
        full_payload = fetch_nhs_reference_costs()
        all_costs: Dict[str, float] = full_payload["costs"]
        metadata: dict = full_payload["metadata"]

        # Apply category filter
        if category:
            filtered = get_cost_by_category(category)
            if not filtered:
                raise HTTPException(
                    status_code=404,
                    detail=f"No costs found for category '{category}'. "
                           "Valid categories: inpatient, outpatient, emergency, "
                           "diagnostics, procedures, ambulance, community.",
                )
            working_costs = filtered
        else:
            working_costs = all_costs

        # Apply keyword search over the working set
        if search:
            needle = search.strip().lower().replace("_", " ")
            working_costs = {
                k: v for k, v in working_costs.items()
                if needle in k.replace("_", " ")
            }
            if not working_costs:
                raise HTTPException(
                    status_code=404,
                    detail=f"No costs matched search term '{search}'"
                           + (f" in category '{category}'" if category else "") + ".",
                )

        return {
            "costs": working_costs,
            "count": len(working_costs),
            "filters_applied": {
                "search": search,
                "category": category,
            },
            "metadata": metadata,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve reference costs: {e}")


# ── GET /api/evidence/population ─────────────────────────────────────

@app.get(
    "/api/evidence/population",
    summary="ONS population data",
    response_description="ONS Mid-Year Population Estimates 2024 with optional region and prevalence filtering",
    tags=["Evidence"],
)
def get_population_data(
    region: Optional[str] = Query(
        None,
        description="NHS England region name (e.g. 'London', 'North West', 'Yorkshire'). "
                    "Returns that region's population alongside the full dataset.",
        examples=["London"],
    ),
    condition: Optional[str] = Query(
        None,
        description="Clinical condition for prevalence lookup "
                    "(e.g. 'diabetes', 'hypertension', 'heart_disease'). "
                    "Returns the national prevalence rate and estimated patient count.",
        examples=["diabetes"],
    ),
):
    """Return ONS Mid-Year Population Estimates 2024 for the UK.

    The full dataset includes UK-wide totals, nine England regions,
    eighteen ONS age bands, and nine disease prevalence rates from
    NHS Digital.

    Optional filters:

    - `region` — adds a `region_detail` block with that region's population
    - `condition` — adds a `prevalence_detail` block with the national
      prevalence rate and a per-region estimated patient count

    **Example responses:**

    - `GET /api/evidence/population` → full dataset
    - `GET /api/evidence/population?region=london` → full dataset + London detail
    - `GET /api/evidence/population?condition=diabetes` → full dataset + diabetes prevalence
    - `GET /api/evidence/population?region=north+west&condition=hypertension` → combined
    """
    try:
        payload = fetch_ons_population_data()
        pop_data: dict = payload["population"]
        metadata: dict = payload["metadata"]

        response: Dict[str, Any] = {
            "population": pop_data,
            "metadata": metadata,
        }

        # Region detail
        if region:
            region_pop = get_population_by_region(region)
            if region_pop == 0:
                known = sorted(pop_data["england_regions"].keys())
                raise HTTPException(
                    status_code=404,
                    detail=f"Region '{region}' not recognised. "
                           f"Known regions: {', '.join(known)}.",
                )
            response["region_detail"] = {
                "region": region,
                "population": region_pop,
            }

        # Prevalence / condition detail
        if condition:
            cond_key = condition.strip().lower().replace(" ", "_").replace("-", "_")
            prevalence_map: Dict[str, float] = pop_data["prevalence_estimates"]

            # Substring match so 'heart disease' finds 'heart_disease'
            matched_key = next(
                (k for k in prevalence_map if cond_key in k or k in cond_key), None
            )
            if matched_key is None:
                known_conds = sorted(prevalence_map.keys())
                raise HTTPException(
                    status_code=404,
                    detail=f"No prevalence data for condition '{condition}'. "
                           f"Known conditions: {', '.join(known_conds)}.",
                )

            prevalence = prevalence_map[matched_key]
            uk_total: int = pop_data["uk_total"]["total"]
            england_total: int = pop_data["uk_total"]["england"]

            per_region: Dict[str, int] = {
                reg: round(pop * prevalence)
                for reg, pop in pop_data["england_regions"].items()
            }

            prevalence_detail: Dict[str, Any] = {
                "condition": matched_key,
                "prevalence_rate": prevalence,
                "prevalence_pct": f"{prevalence:.1%}",
                "estimated_uk_patients": round(uk_total * prevalence),
                "estimated_england_patients": round(england_total * prevalence),
                "estimated_patients_by_region": per_region,
            }

            # If a region was also requested, add its specific estimate
            if region and "region_detail" in response:
                region_pop = response["region_detail"]["population"]
                prevalence_detail["estimated_patients_in_region"] = round(
                    region_pop * prevalence
                )

            response["prevalence_detail"] = prevalence_detail

        return response

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve population data: {e}")


# ── GET /api/evidence/nice-guidance ──────────────────────────────────

# Map URL-friendly type values to the short aliases accepted by search_nice_guidance()
_NICE_TYPE_ALIASES: Dict[str, str] = {
    "technology-appraisal": "ta",
    "ta": "ta",
    "nice-guideline": "ng",
    "ng": "ng",
    "medtech-innovation-briefing": "mib",
    "mib": "mib",
    "diagnostics-guidance": "dg",
    "dg": "dg",
    "highly-specialised-technology": "hst",
    "hst": "hst",
}


@app.get(
    "/api/evidence/nice-guidance",
    summary="Search NICE guidance",
    response_description="List of matching NICE guidance records sorted by date (most recent first)",
    tags=["Evidence"],
)
def get_nice_guidance(
    search: Optional[str] = Query(
        None,
        description="Free-text keyword (e.g. 'diabetes', 'remote monitoring', 'AI'). "
                    "Matches against title, condition, ID, intervention types, and recommendations.",
        examples=["remote monitoring"],
    ),
    type: Optional[str] = Query(
        None,
        alias="type",
        description="Filter by document type. Accepted values: "
                    "ta, technology-appraisal, ng, nice-guideline, "
                    "mib, medtech-innovation-briefing, dg, diagnostics-guidance, "
                    "hst, highly-specialised-technology.",
        examples=["ta"],
    ),
    condition: Optional[str] = Query(
        None,
        description="Clinical condition shortcut — equivalent to searching by condition name. "
                    "Combined with `search` using AND logic.",
        examples=["Heart Failure"],
    ),
    include_threshold: bool = Query(
        False,
        description="When true, includes NICE WTP threshold context for the searched condition.",
    ),
):
    """Search the curated NICE guidance database.

    Returns guidance records sorted by date (most recent first). All
    query parameters are optional; omitting all returns the full database.

    **Document types accepted:**

    | Short alias | Full name |
    |---|---|
    | `ta` | Technology Appraisal |
    | `ng` | NICE Guideline |
    | `mib` | Medtech Innovation Briefing |
    | `dg` | Diagnostics Guidance |
    | `hst` | Highly Specialised Technology |

    **Example queries:**

    - `GET /api/evidence/nice-guidance?search=diabetes`
    - `GET /api/evidence/nice-guidance?type=ta&search=ai`
    - `GET /api/evidence/nice-guidance?condition=heart+failure&include_threshold=true`
    """
    try:
        # Resolve type alias
        type_arg = "all"
        if type:
            normalised_type = type.strip().lower()
            type_arg = _NICE_TYPE_ALIASES.get(normalised_type, normalised_type)

        # Build search term — combine search + condition
        parts = [p for p in [search, condition] if p and p.strip()]
        search_term = " ".join(parts)

        results = search_nice_guidance(search_term, guidance_type=type_arg)

        response: Dict[str, Any] = {
            "count": len(results),
            "guidance": results,
            "filters_applied": {
                "search": search,
                "condition": condition,
                "type": type,
            },
        }

        if include_threshold and (condition or search):
            ctx_term = condition or search or ""
            response["threshold_context"] = get_nice_threshold_context(ctx_term)

        return response

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to search NICE guidance: {e}")


# ── POST /api/evidence/enrich-inputs ─────────────────────────────────

@app.post(
    "/api/evidence/enrich-inputs",
    summary="Enrich BIA inputs with reference data",
    response_description="Original inputs plus suggested values, warnings, comparators, "
                         "relevant NHS costs, and ONS population context",
    tags=["Evidence"],
)
def enrich_inputs(body: EnrichInputsRequest):
    """Enrich partial BIA inputs with NHS, ONS, and NICE reference data.

    Call this endpoint before submitting a BIA to get:

    - **suggested_values** — recommended parameter values where inputs are
      missing (catchment size from beds or region, eligible % from ONS
      prevalence, etc.)
    - **warnings** — data-quality flags (costs diverging >50% from NHS
      reference, eligible % far above national prevalence, etc.)
    - **comparators** — NICE-approved interventions for the same condition
      and intervention type, with comparator lists and ICERs
    - **reference_costs** — the subset of NHS costs relevant to the care
      pathway (cardiac, respiratory, oncology, etc.)
    - **population_context** — catchment size, prevalence rate, and
      estimated eligible patient count from ONS 2024

    All input fields are optional — the function degrades gracefully when
    only partial information is available.

    **Example request:**
    ```json
    {
        "condition": "Heart Failure",
        "intervention_type": "remote_monitoring",
        "bed_count": 300,
        "costs": {"outpatient_visit": 80, "device_cost": 500}
    }
    ```
    """
    try:
        raw_inputs = body.model_dump(exclude_none=True)
        enriched = enrich_bia_inputs(raw_inputs)
        return enriched
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Enrichment failed: {e}")


# ── POST /api/evidence/validate ───────────────────────────────────────

@app.post(
    "/api/evidence/validate",
    summary="Validate BIA/CEA results against NICE precedents",
    response_description="Validation report with overall status, ICER assessment, "
                         "savings plausibility, uptake trajectory, and red flags",
    tags=["Evidence"],
)
def validate_results(body: ValidateRequest):
    """Validate BIA inputs and modelled results against NICE reference data.

    Checks four areas and returns a structured report:

    1. **ICER plausibility** — compares the submitted ICER to the applicable
       NICE WTP threshold band (standard / end-of-life / highly specialised)
       and to the precedent range from similar approved technologies.

    2. **Cost-savings plausibility** — flags if net savings exceed 5× the
       intervention cost (typically a sign of over-optimistic resource
       utilisation assumptions) or if the model shows unexpected net costs.

    3. **Uptake trajectory** — warns if year-1 uptake exceeds 30% (unusual
       for novel medtech rollouts) or is below 2%.

    4. **Red flags** — any of the above that represent critical concerns;
       their presence sets `overall_status` to `"fail"`.

    **`overall_status`** values:
    - `"pass"` — no warnings or red flags
    - `"warning"` — non-critical concerns worth reviewing
    - `"fail"` — one or more red flags requiring investigation

    **Example request:**
    ```json
    {
        "inputs": {
            "condition": "Heart Failure",
            "intervention_type": "remote_monitoring"
        },
        "results": {
            "icer": 18500,
            "net_savings": 200000,
            "intervention_cost": 100000,
            "year1_uptake": 0.12,
            "annual_savings": [60000, 70000, 70000]
        }
    }
    ```
    """
    try:
        report = validate_against_references(body.inputs, body.results)
        return report
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Validation failed: {e}")


# ── POST /api/suggest-defaults ────────────────────────────────────────

# Uptake trajectories keyed by rollout archetype.
# Values are (y1%, y2%, y3%) tuples.
_UPTAKE_PROFILES: Dict[str, Dict[str, Any]] = {
    "conservative": {
        "uptake_y1": 15,
        "uptake_y2": 35,
        "uptake_y3": 55,
        "rationale": "Conservative ramp-up: limited early adopters, slow commissioner buy-in",
    },
    "moderate": {
        "uptake_y1": 25,
        "uptake_y2": 50,
        "uptake_y3": 75,
        "rationale": "Typical digital health rollout: steady growth following initial pilot",
    },
    "optimistic": {
        "uptake_y1": 40,
        "uptake_y2": 65,
        "uptake_y3": 85,
        "rationale": "Rapid adoption: strong clinical champion, pre-existing infrastructure",
    },
}

# Per-region diabetes prevalence adjustments (fraction, relative to national 6.8 %).
# Used to populate the regional variation warning.
_DIABETES_REGIONAL_PREVALENCE: Dict[str, float] = {
    "london":            0.072,
    "north_east":        0.061,
    "north_west":        0.071,
    "yorkshire_humber":  0.070,
    "east_midlands":     0.069,
    "west_midlands":     0.078,
    "east_england":      0.064,
    "south_east":        0.063,
    "south_west":        0.059,
}

# Conditions that have documented regional variation worth surfacing.
_REGIONAL_VARIATION_CONDITIONS = {"diabetes", "hypertension", "copd", "heart_disease"}

# Typical total pathway costs (£/patient/year) by broad condition group —
# used when we cannot derive a cost from reference data alone.
_TYPICAL_PATHWAY_COSTS: Dict[str, int] = {
    "diabetes":      2_500,
    "hypertension":  1_800,
    "heart failure": 4_200,
    "heart_disease": 4_200,
    "copd":          3_800,
    "asthma":        2_200,
    "stroke":        8_500,
    "cancer":       12_000,
    "dementia":      6_500,
    "mental_health": 3_200,
    "obesity":       1_500,
    "respiratory":   3_500,
}

# Workforce time benchmarks (minutes per patient per year) drawn from
# NICE technology appraisal resource-use assumptions for digital interventions.
_WORKFORCE_BENCHMARKS: Dict[str, Dict[str, Any]] = {
    "digital": {
        "setup_minutes":   30,
        "followup_minutes": 15,
        "role":            "Band 5 (Staff Nurse)",
        "source":          "NICE NG28 / MIB234 resource-use assumptions",
    },
    "remote_monitoring": {
        "setup_minutes":   45,
        "followup_minutes": 20,
        "role":            "Band 6 (Senior Nurse)",
        "source":          "NICE NG185 / MIB234 resource-use assumptions",
    },
    "diagnostic": {
        "setup_minutes":   20,
        "followup_minutes": 10,
        "role":            "Band 5 (Staff Nurse)",
        "source":          "NICE DG56 / DG61 resource-use assumptions",
    },
    "ai": {
        "setup_minutes":   15,
        "followup_minutes":  5,
        "role":            "Admin/Clerical",
        "source":          "NICE TA123 resource-use assumptions",
    },
    "pharmaceutical": {
        "setup_minutes":   30,
        "followup_minutes": 20,
        "role":            "Consultant",
        "source":          "NICE TA894 / TA878 resource-use assumptions",
    },
}


class SuggestDefaultsRequest(BaseModel):
    """Minimal inputs needed to generate a BIA defaults cheat sheet."""

    condition: str = Field(
        ...,
        description="Clinical condition (e.g. 'diabetes', 'heart failure', 'stroke')",
        examples=["diabetes"],
    )
    intervention_type: str = Field(
        ...,
        description="Type of intervention (e.g. 'digital', 'remote_monitoring', "
                    "'diagnostic', 'ai', 'pharmaceutical')",
        examples=["digital"],
    )
    setting: str = Field(
        "Acute NHS Trust",
        description="Care setting (e.g. 'Acute NHS Trust', 'Primary Care', 'Community')",
        examples=["Acute NHS Trust"],
    )


@app.post(
    "/api/suggest-defaults",
    summary="Suggest BIA defaults for a condition and intervention type",
    response_description="Condition-specific default parameters, reference costs, "
                         "relevant NICE guidance, and regional variation warnings",
    tags=["Evidence"],
)
def suggest_defaults(body: SuggestDefaultsRequest):
    """Return a 'cheat sheet' of sensible BIA starting values.

    Combines ONS prevalence data, NHS reference costs, NICE guidance, and
    published workforce benchmarks to suggest realistic defaults when a user
    begins filling in the BIA form.

    **Returned suggestions:**

    - `eligible_pct` — ONS national prevalence rate for the condition
    - `eligible_pct_source` — provenance string
    - `uptake_y1/y2/y3` — moderate rollout trajectory (25 / 50 / 75 %)
    - `uptake_rationale` — plain-English explanation
    - `typical_pathway_cost` — rough annual cost per patient in current pathway
    - `workforce_suggestion` — role, setup minutes, and follow-up minutes from
      NICE appraisal resource-use assumptions
    - `relevant_nice_guidance` — list of NICE IDs matching the condition
    - `comparator_tools` — approved comparators with ICERs where available

    **Warnings** flag regional variation, missing NICE precedents, and any
    assumption that may not apply to the user's specific setting.

    **Example request:**
    ```json
    {
        "condition": "diabetes",
        "intervention_type": "digital",
        "setting": "Acute NHS Trust"
    }
    ```
    """
    try:
        condition = body.condition.strip()
        itype     = body.intervention_type.strip().lower().replace(" ", "_").replace("-", "_")
        cond_lower = condition.lower()

        warnings: list[str] = []
        suggestions: Dict[str, Any] = {}

        # ------------------------------------------------------------------
        # 1. Eligible % from ONS prevalence
        # ------------------------------------------------------------------
        ons = fetch_ons_population_data()["population"]
        prev_map: Dict[str, float] = ons["prevalence_estimates"]

        # Substring match (handles "heart failure" → "heart_disease" etc.)
        cond_key = cond_lower.replace(" ", "_")
        prevalence: Optional[float] = None
        matched_cond: Optional[str] = None
        for k, v in prev_map.items():
            if k in cond_key or cond_key in k:
                prevalence = v
                matched_cond = k
                break

        if prevalence is not None:
            suggestions["eligible_pct"]        = round(prevalence * 100, 2)  # as %
            suggestions["eligible_pct_source"] = f"ONS {matched_cond} prevalence (national average)"
        else:
            suggestions["eligible_pct"]        = None
            suggestions["eligible_pct_source"] = "Not available — no ONS prevalence data for this condition"
            warnings.append(
                f"No ONS prevalence data found for '{condition}'. "
                "Set eligible_pct manually based on local population data."
            )

        # ------------------------------------------------------------------
        # 2. Uptake trajectory — default to moderate profile
        # ------------------------------------------------------------------
        profile = _UPTAKE_PROFILES["moderate"]
        suggestions["uptake_y1"]         = profile["uptake_y1"]
        suggestions["uptake_y2"]         = profile["uptake_y2"]
        suggestions["uptake_y3"]         = profile["uptake_y3"]
        suggestions["uptake_rationale"]  = profile["rationale"]
        suggestions["all_uptake_profiles"] = {
            name: {k: v for k, v in p.items() if k != "rationale"}
            for name, p in _UPTAKE_PROFILES.items()
        }

        # ------------------------------------------------------------------
        # 3. Typical pathway cost
        # ------------------------------------------------------------------
        pathway_cost: Optional[int] = None
        for key, cost in _TYPICAL_PATHWAY_COSTS.items():
            if key in cond_lower or cond_lower in key:
                pathway_cost = cost
                break
        suggestions["typical_pathway_cost"] = pathway_cost
        suggestions["typical_pathway_cost_source"] = (
            "NHS Reference Cost Collection 2024/25 — indicative composite"
            if pathway_cost else "Not available for this condition"
        )
        if pathway_cost is None:
            warnings.append(
                f"No typical pathway cost benchmark found for '{condition}'. "
                "Use NHS Reference Costs to build a bottom-up estimate."
            )

        # ------------------------------------------------------------------
        # 4. Workforce benchmarks from NICE appraisals
        # ------------------------------------------------------------------
        workforce = _WORKFORCE_BENCHMARKS.get(itype)
        if workforce:
            suggestions["workforce_suggestion"] = workforce
        else:
            # Closest known type
            known = list(_WORKFORCE_BENCHMARKS.keys())
            suggestions["workforce_suggestion"] = None
            warnings.append(
                f"No workforce benchmark found for intervention type '{body.intervention_type}'. "
                f"Known types: {', '.join(known)}. "
                "Use 15–30 min Band 5 nurse time per patient as a default."
            )

        # ------------------------------------------------------------------
        # 5. Relevant NICE guidance
        # ------------------------------------------------------------------
        nice_results = search_nice_guidance(condition)
        nice_ids = [r["id"] for r in nice_results]
        suggestions["relevant_nice_guidance"] = nice_ids

        if not nice_ids:
            warnings.append(
                f"No NICE guidance found for '{condition}'. "
                "Consider searching the NICE website directly for relevant appraisals."
            )

        # ------------------------------------------------------------------
        # 6. Comparator tools with ICERs
        # ------------------------------------------------------------------
        comparators = get_nice_comparators(condition, itype)
        comparator_labels: list[str] = []
        for comp in comparators:
            label = comp["title"]
            if comp.get("icer"):
                label += f" (ICER £{comp['icer']:,}/QALY)"
            comparator_labels.append(f"{comp['id']}: {label}")
        suggestions["comparator_tools"] = comparator_labels

        if not comparator_labels:
            # Broaden: show any NICE guidance for the condition as context
            broader = [
                f"{r['id']}: {r['title']}"
                for r in nice_results
                if r.get("icer")
            ]
            suggestions["comparator_tools"] = broader
            if not broader:
                warnings.append(
                    f"No NICE-approved comparators with ICERs found for "
                    f"'{condition}' + '{body.intervention_type}'. "
                    "Use clinical judgement to select comparators."
                )

        # ------------------------------------------------------------------
        # 7. Regional variation warning (selected conditions)
        # ------------------------------------------------------------------
        if matched_cond in _REGIONAL_VARIATION_CONDITIONS:
            if matched_cond == "diabetes":
                region_rates = {
                    r.replace("_", " ").title(): f"{v:.1%}"
                    for r, v in _DIABETES_REGIONAL_PREVALENCE.items()
                }
                rate_pairs = ", ".join(
                    f"{r} {v}" for r, v in sorted(region_rates.items())
                )
                warnings.append(
                    f"{condition.title()} prevalence varies by region — {rate_pairs}. "
                    "Adjust eligible_pct if your catchment is not nationally representative."
                )
            else:
                warnings.append(
                    f"{condition.title()} prevalence shows regional variation. "
                    "Consider adjusting eligible_pct for your specific catchment area."
                )

        # ------------------------------------------------------------------
        # 8. Setting-specific note
        # ------------------------------------------------------------------
        setting_lower = body.setting.lower()
        if "primary" in setting_lower or "gp" in setting_lower:
            warnings.append(
                "Primary care setting detected. NHS reference costs are predominantly "
                "based on secondary care tariffs — adjust pathway costs accordingly."
            )
        elif "community" in setting_lower:
            warnings.append(
                "Community setting detected. Consider using PSSRU unit costs "
                "for community nursing and therapy staff instead of AFC band rates."
            )

        return {
            "condition": condition,
            "intervention_type": body.intervention_type,
            "setting": body.setting,
            "suggestions": suggestions,
            "warnings": warnings,
            "metadata": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "sources": [
                    "ONS Mid-Year Population Estimates 2024",
                    "NHS National Cost Collection 2024/25",
                    "NICE Guidance Database (curated MVP)",
                ],
            },
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"suggest-defaults failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# SLR (Systematic Literature Review) screening endpoints
# ══════════════════════════════════════════════════════════════════════════════

# ── Pydantic request/response models ──────────────────────────────────────────

class SLRScreenRequest(BaseModel):
    """Request body for POST /api/slr/screen."""

    pico: PICOCriteria = Field(
        ...,
        description="PICO eligibility criteria for this screening run.",
        openapi_examples={
            "diabetes_cgm": {
                "summary": "T2DM remote glucose monitoring review",
                "value": {
                    "population": "Adults with type 2 diabetes",
                    "intervention": "Remote glucose monitoring or CGM",
                    "comparison": "Standard care or SMBG",
                    "outcomes": ["HbA1c reduction", "Time in range", "Quality of life"],
                    "study_types": ["RCT", "Cohort study", "Economic evaluation"],
                    "exclusion_criteria": ["Pediatric populations", "Type 1 diabetes only"],
                },
            }
        },
    )
    abstracts: list[Abstract] = Field(
        ...,
        min_length=1,
        description="One or more PubMed abstracts to screen.",
    )
    batch_size: int = Field(
        default=10,
        ge=1,
        le=25,
        description=(
            "Number of abstracts per Claude API call. "
            "Smaller values reduce truncation risk for long abstracts; "
            "larger values reduce API call count for big corpora."
        ),
    )


class SLRBatchSummary(BaseModel):
    """Lightweight batch metadata returned by GET /api/slr/batches."""

    batch_id: str
    created_at: datetime
    abstract_count: int
    decision_count: int
    included: int
    excluded: int
    uncertain: int
    inclusion_rate: float
    mean_pico_score: float
    population: str
    intervention: str


class SLRScreenResponse(BaseModel):
    """Response for POST /api/slr/screen."""

    batch_id: str
    summary: dict
    decisions: list[dict]
    batch_json_path: str


class SLRExportRequest(BaseModel):
    """Request body for POST /api/slr/export/{batch_id}."""

    format: str = Field(
        default="csv",
        description="Export format: 'csv' or 'excel'.",
        pattern="^(csv|excel|xlsx)$",
    )


# ── Sample PICO library ────────────────────────────────────────────────────────

_SAMPLE_PICO: dict[str, dict] = {
    "diabetes_remote_monitoring": {
        "label": "Diabetes remote monitoring",
        "description": (
            "RCTs and economic evaluations assessing CGM or remote glucose monitoring "
            "vs. standard care in adults with type 2 diabetes."
        ),
        "pico": {
            "population": "Adults (≥18 years) with type 2 diabetes mellitus",
            "intervention": (
                "Continuous glucose monitoring (CGM) or remote glucose monitoring "
                "with clinician-facing dashboard or alert system"
            ),
            "comparison": "Standard care or self-monitoring of blood glucose (SMBG)",
            "outcomes": [
                "HbA1c reduction",
                "Time in range (TIR)",
                "Hypoglycaemia episodes",
                "Quality of life (EQ-5D or DTSQ)",
                "Cost-effectiveness (ICER per QALY)",
                "Hospitalization rates",
            ],
            "study_types": ["RCT", "Cohort study", "Economic evaluation", "Cost-utility analysis"],
            "exclusion_criteria": [
                "Paediatric populations (age < 18 years)",
                "Type 1 diabetes only studies",
                "Animal or in vitro studies",
                "Conference abstracts without full methods",
                "Follow-up < 3 months",
            ],
        },
    },
    "ai_diagnostic_tools": {
        "label": "AI diagnostic tools in secondary care",
        "description": (
            "Evidence on AI-assisted diagnostic decision support tools vs. standard "
            "clinical review in adult secondary-care settings."
        ),
        "pico": {
            "population": (
                "Adult patients (≥18 years) referred to secondary care for "
                "diagnostic workup across any condition"
            ),
            "intervention": (
                "AI-powered or machine-learning diagnostic decision support system "
                "(image recognition, predictive scoring, NLP-based triage)"
            ),
            "comparison": "Standard clinical assessment without AI assistance",
            "outcomes": [
                "Diagnostic accuracy (sensitivity, specificity, AUC)",
                "Time to diagnosis",
                "Clinical outcomes (mortality, morbidity)",
                "Cost per correct diagnosis",
                "Clinician acceptance and workflow integration",
            ],
            "study_types": [
                "RCT",
                "Diagnostic accuracy study",
                "Cohort study",
                "Economic evaluation",
                "Systematic review",
            ],
            "exclusion_criteria": [
                "Paediatric-only studies",
                "In vitro or animal studies",
                "Single-centre feasibility studies without comparative arm",
                "Editorials, letters, or conference abstracts",
            ],
        },
    },
    "preventive_interventions": {
        "label": "Preventive interventions in primary care",
        "description": (
            "Effectiveness and cost-effectiveness of preventive care programmes "
            "targeting high-risk adults in NHS primary care."
        ),
        "pico": {
            "population": (
                "Adults (≥18 years) at high risk of developing a chronic condition "
                "(e.g. pre-diabetes, stage-1 hypertension) in a primary care setting"
            ),
            "intervention": (
                "Structured preventive programme: lifestyle modification, "
                "pharmacological prevention, or digital behaviour-change intervention"
            ),
            "comparison": "Usual care, watchful waiting, or no active intervention",
            "outcomes": [
                "Incident disease rate",
                "Biomarker improvement (e.g. HbA1c, blood pressure, lipid profile)",
                "Quality-adjusted life years (QALYs)",
                "Cost per QALY gained",
                "Programme adherence and dropout rates",
            ],
            "study_types": [
                "RCT",
                "Cluster RCT",
                "Cohort study",
                "Economic evaluation",
                "Cost-effectiveness analysis",
            ],
            "exclusion_criteria": [
                "Secondary prevention studies (existing disease diagnosis)",
                "Inpatient or secondary care setting",
                "Follow-up < 6 months",
                "Non-English language without translation",
            ],
        },
    },
    "economic_evaluations": {
        "label": "Health technology economic evaluations",
        "description": (
            "Cost-effectiveness and cost-utility analyses for health technologies "
            "submitted for NICE appraisal or NHS commissioning decisions."
        ),
        "pico": {
            "population": "Adult NHS patients eligible for the technology under appraisal",
            "intervention": (
                "Any health technology (drug, device, diagnostic, or digital health tool) "
                "with a published economic evaluation"
            ),
            "comparison": "Current standard of care, best supportive care, or placebo",
            "outcomes": [
                "Incremental cost-effectiveness ratio (ICER)",
                "Cost per QALY gained",
                "Life-years gained",
                "Budget impact (NHS perspective)",
                "Sensitivity analysis results",
            ],
            "study_types": [
                "Cost-effectiveness analysis",
                "Cost-utility analysis",
                "Budget impact analysis",
                "Decision analytic model",
                "Systematic review of economic evaluations",
            ],
            "exclusion_criteria": [
                "Non-UK or non-NHS perspective without transferability analysis",
                "Studies without incremental analysis",
                "Cost-minimisation analyses only",
                "Non-English language",
            ],
        },
    },
}


# ── Helper: load all batch metadata ───────────────────────────────────────────

def _list_all_batches() -> list[SLRBatchSummary]:
    """Scan the batches directory and return summary metadata for every batch."""
    _SLR_BATCHES_DIR.mkdir(parents=True, exist_ok=True)
    summaries: list[SLRBatchSummary] = []

    for json_file in sorted(_SLR_BATCHES_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            batch = ScreeningBatch.model_validate(data)
            s = batch.summary
            summaries.append(
                SLRBatchSummary(
                    batch_id=batch.batch_id,
                    created_at=batch.created_at,
                    abstract_count=len(batch.abstracts),
                    decision_count=len(batch.decisions),
                    included=s.get("included", 0),
                    excluded=s.get("excluded", 0),
                    uncertain=s.get("uncertain", 0),
                    inclusion_rate=s.get("inclusion_rate", 0.0),
                    mean_pico_score=s.get("mean_pico_score", 0.0),
                    population=batch.pico_criteria.population[:120],
                    intervention=batch.pico_criteria.intervention[:120],
                )
            )
        except Exception:
            # Skip corrupt or unreadable batch files
            continue

    return summaries


# ── POST /api/slr/screen ───────────────────────────────────────────────────────

@app.post(
    "/api/slr/screen",
    summary="Screen abstracts against PICO criteria",
    response_description="Completed ScreeningBatch with all decisions",
    tags=["SLR Screening"],
    responses={
        200: {"description": "Screening complete — batch with all decisions returned."},
        400: {"description": "No abstracts provided or empty PICO fields."},
        503: {
            "description": (
                "ANTHROPIC_API_KEY not set or Anthropic SDK not installed. "
                "Set the environment variable and restart the server."
            )
        },
        422: {"description": "Request body failed Pydantic validation."},
        500: {"description": "Unexpected screening error."},
    },
)
def slr_screen(body: SLRScreenRequest) -> SLRScreenResponse:
    """Screen a list of PubMed abstracts against PICO eligibility criteria.

    Splits the abstracts into batches, sends each batch to Claude as a single
    API call, parses the structured response, and returns a complete
    ``ScreeningBatch`` with one ``ScreeningDecision`` per abstract.

    The batch is also saved to ``data/slr/batches/{batch_id}.json`` so it can
    be retrieved later via **GET /api/slr/batch/{batch_id}**.

    **Typical payload size**: 20 abstracts → 2 API calls (batch_size=10),
    ~15–30 seconds depending on abstract length.
    """
    if not _SLR_API_KEY:
        raise HTTPException(
            status_code=503,
            detail=(
                "ANTHROPIC_API_KEY is not set on the server. "
                "Set the environment variable and restart the API."
            ),
        )

    # Create the batch record (persists empty shell to disk)
    try:
        batch = create_screening_batch(body.abstracts, body.pico)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create screening batch: {exc}",
        )

    # Run AI screening
    try:
        decisions = screen_abstracts(
            body.abstracts,
            body.pico,
            batch_size=body.batch_size,
        )
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except EnvironmentError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Screening failed: {exc}",
        )

    # Populate batch with decisions and persist updated state
    for dec in decisions:
        batch.add_decision(dec)

    try:
        batch_path = save_batch(batch)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save batch after screening: {exc}",
        )

    return SLRScreenResponse(
        batch_id=batch.batch_id,
        summary=batch.summary,
        decisions=[
            json.loads(dec.model_dump_json())
            for dec in batch.decisions
        ],
        batch_json_path=str(batch_path),
    )


# ── GET /api/slr/batches ───────────────────────────────────────────────────────

@app.get(
    "/api/slr/batches",
    summary="List all screening batches",
    response_description="List of batch summaries sorted by date (newest first)",
    tags=["SLR Screening"],
    responses={
        200: {"description": "List of all saved screening batches (may be empty)."},
        500: {"description": "Failed to read batch directory."},
    },
)
def slr_list_batches(
    limit: int = Query(
        default=50,
        ge=1,
        le=500,
        description="Maximum number of batches to return.",
    ),
) -> dict:
    """Return summary metadata for all saved screening batches.

    Batches are sorted newest-first based on their ``created_at`` timestamp.
    Each entry includes abstract/decision counts and top-level summary statistics
    so the caller can pick a batch to inspect without loading full decision data.
    """
    try:
        summaries = _list_all_batches()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list batches: {exc}",
        )

    paged = summaries[:limit]
    return {
        "total": len(summaries),
        "returned": len(paged),
        "batches": [s.model_dump() for s in paged],
    }


# ── GET /api/slr/batch/{batch_id} ─────────────────────────────────────────────

@app.get(
    "/api/slr/batch/{batch_id}",
    summary="Get full details for a specific screening batch",
    response_description="ScreeningBatch with all decisions and reasoning",
    tags=["SLR Screening"],
    responses={
        200: {"description": "Full batch data including all decisions."},
        404: {"description": "No batch found with the given batch_id."},
        422: {"description": "Saved batch JSON failed schema validation."},
        500: {"description": "Unexpected error loading the batch."},
    },
)
def slr_get_batch(batch_id: str) -> dict:
    """Return complete details for a previously saved screening batch.

    Includes:
    - PICO criteria used for the run
    - All abstracts that were submitted
    - Every ``ScreeningDecision`` with full ``pico_match``, ``reasoning``,
      and ``exclusion_reasons``
    - Aggregate ``summary`` statistics
    """
    try:
        batch = load_batch(batch_id)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Batch '{batch_id}' not found. "
                   "Use GET /api/slr/batches to list available batch IDs.",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load batch '{batch_id}': {exc}",
        )

    return json.loads(batch.model_dump_json())


# ── POST /api/slr/export/{batch_id} ───────────────────────────────────────────

@app.post(
    "/api/slr/export/{batch_id}",
    summary="Export a batch to CSV or Excel",
    response_description="File download of the exported screening results",
    tags=["SLR Screening"],
    responses={
        200: {"description": "File download — CSV or Excel attachment."},
        400: {"description": "Invalid export format requested."},
        404: {"description": "No batch found with the given batch_id."},
        422: {"description": "Batch has no decisions to export."},
        500: {"description": "Export failed."},
    },
)
def slr_export_batch(batch_id: str, body: SLRExportRequest) -> FileResponse:
    """Export a saved screening batch to a downloadable file.

    Supported formats:
    - **csv** — UTF-8 CSV with columns: PMID, Title, Authors, Journal, Year,
      Decision, Confidence, PICO_Score, Reasoning, Exclusion_Reasons, Reviewer,
      Timestamp.
    - **excel** / **xlsx** — Same columns in an XLSX workbook with colour-coded
      Decision cells (green / red / amber) and auto-width columns.

    The file is written to ``data/slr/exports/`` and served as an attachment.
    Subsequent calls overwrite the previous export for the same batch.
    """
    # Load batch
    try:
        batch = load_batch(batch_id)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Batch '{batch_id}' not found.",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load batch '{batch_id}': {exc}",
        )

    if not batch.decisions:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Batch '{batch_id}' has no decisions to export. "
                "Run POST /api/slr/screen first."
            ),
        )

    # Validate format
    fmt = body.format.strip().lower()
    if fmt not in ("csv", "excel", "xlsx"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format '{body.format}'. Use 'csv', 'excel', or 'xlsx'.",
        )

    # Export
    try:
        filepath = export_screening_results(batch, format=fmt)
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Excel export requires openpyxl: {exc}",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Export failed: {exc}",
        )

    media_type = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if fmt in ("excel", "xlsx")
        else "text/csv"
    )
    file_ext   = "xlsx" if fmt in ("excel", "xlsx") else "csv"
    filename   = f"slr_{batch_id}.{file_ext}"

    return FileResponse(
        path=filepath,
        media_type=media_type,
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── GET /api/slr/sample-pico ───────────────────────────────────────────────────

@app.get(
    "/api/slr/sample-pico",
    summary="Get example PICO criteria for common review types",
    response_description="Dict of ready-to-use PICO templates keyed by review type",
    tags=["SLR Screening"],
    responses={
        200: {"description": "All sample PICO templates returned successfully."},
    },
)
def slr_sample_pico(
    key: Optional[str] = Query(
        default=None,
        description=(
            "Return only the template for this key. "
            "Available: diabetes_remote_monitoring, ai_diagnostic_tools, "
            "preventive_interventions, economic_evaluations. "
            "Omit to return all templates."
        ),
    ),
) -> dict:
    """Return one or all pre-built PICO criteria templates.

    These templates are designed to help users get started quickly by providing
    realistic, field-complete PICO definitions for common health-technology
    review scenarios.  Each template can be passed directly as the ``pico``
    field of a **POST /api/slr/screen** request body.

    Available review types:

    | Key | Description |
    |-----|-------------|
    | ``diabetes_remote_monitoring`` | CGM/remote monitoring vs SMBG in T2DM |
    | ``ai_diagnostic_tools`` | AI decision-support in secondary care |
    | ``preventive_interventions`` | Preventive care programmes in primary care |
    | ``economic_evaluations`` | NICE-style cost-effectiveness analyses |
    """
    if key is not None:
        if key not in _SAMPLE_PICO:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Sample PICO key '{key}' not found. "
                    f"Available keys: {sorted(_SAMPLE_PICO.keys())}"
                ),
            )
        template = _SAMPLE_PICO[key]
        return {
            "key": key,
            "label": template["label"],
            "description": template["description"],
            "pico": template["pico"],
            "usage_hint": (
                "Pass the 'pico' object as the 'pico' field of "
                "POST /api/slr/screen to begin screening."
            ),
        }

    return {
        "count": len(_SAMPLE_PICO),
        "templates": {
            k: {
                "key": k,
                "label": v["label"],
                "description": v["description"],
                "pico": v["pico"],
            }
            for k, v in _SAMPLE_PICO.items()
        },
        "usage_hint": (
            "Pass any 'pico' object as the 'pico' field of "
            "POST /api/slr/screen to begin screening, or use "
            "GET /api/slr/sample-pico?key=<key> to fetch a single template."
        ),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Workflow orchestration endpoints
# ══════════════════════════════════════════════════════════════════════════════

# ── Condition-specific pathway defaults for quick estimation ──────────────────

_QUICK_ESTIMATE_DEFAULTS: dict[str, dict] = {
    "diabetes": {
        "eligible_pct": 7.0,
        "outpatient_visits": 2,
        "admissions": 0,
        "bed_days": 0,
        "tests": 4,
        "staff_minutes": 30,
        "staff_role": "Band 5 (Staff Nurse)",
    },
    "cardiovascular": {
        "eligible_pct": 4.0,
        "outpatient_visits": 3,
        "admissions": 1,
        "bed_days": 4,
        "tests": 3,
        "staff_minutes": 45,
        "staff_role": "Band 6 (Senior Nurse/AHP)",
    },
    "cancer": {
        "eligible_pct": 1.0,
        "outpatient_visits": 8,
        "admissions": 2,
        "bed_days": 5,
        "tests": 6,
        "staff_minutes": 60,
        "staff_role": "Band 7 (Advanced Practitioner)",
    },
    "respiratory": {
        "eligible_pct": 10.0,
        "outpatient_visits": 2,
        "admissions": 1,
        "bed_days": 3,
        "tests": 2,
        "staff_minutes": 20,
        "staff_role": "Band 5 (Staff Nurse)",
    },
    "default": {
        "eligible_pct": 5.0,
        "outpatient_visits": 2,
        "admissions": 0,
        "bed_days": 0,
        "tests": 2,
        "staff_minutes": 30,
        "staff_role": "Band 5 (Staff Nurse)",
    },
}


class QuickEstimateRequest(BaseModel):
    """Minimal inputs for a back-of-envelope Budget Impact Analysis."""

    condition: str = Field(
        "diabetes",
        description=(
            "Clinical condition or therapeutic area. Recognised values: "
            "``diabetes``, ``cardiovascular``, ``cancer``, ``respiratory``. "
            "Any other value uses generic NHS defaults."
        ),
    )
    intervention_name: str = Field(
        ...,
        min_length=1,
        description="Short name for the technology being evaluated.",
    )
    catchment_population: int = Field(
        ...,
        gt=0,
        description="Total catchment population (NHS ICB or trust level).",
    )
    device_cost_per_patient: float = Field(
        ...,
        ge=0,
        description="Annual per-patient cost of the intervention (£).",
    )
    expected_los_reduction_days: float = Field(
        0.0,
        ge=0,
        description=(
            "Expected reduction in hospital length of stay per admission (days). "
            "Set to 0 if the device does not affect inpatient stays."
        ),
    )
    expected_visit_reduction_pct: float = Field(
        0.0,
        ge=0,
        le=100,
        description="Expected percentage reduction in outpatient visits (0–100).",
    )


# ── POST /api/workflows/bia ───────────────────────────────────────────────────

_BIA_EXAMPLE = {
    "inputs": {
        "setting": "ICB",
        "model_year": 2025,
        "forecast_years": 3,
        "funding_source": "ICB commissioning",
        "catchment_size": 500000,
        "eligible_pct": 7.0,
        "uptake_y1": 15,
        "uptake_y2": 30,
        "uptake_y3": 45,
        "workforce": [
            {"role": "Band 5 (Staff Nurse)", "minutes": 30, "frequency": "per patient"},
            {"role": "Band 7 (Advanced Practitioner)", "minutes": 15, "frequency": "per patient"},
        ],
        "pricing_model": "per-patient",
        "price": 750,
        "price_unit": "per year",
        "outpatient_visits": 2,
        "staff_time_saved": 20,
        "visits_reduced": 20,
        "discounting": "off",
    },
    "enrich_with_evidence": True,
    "generate_report": True,
    "report_format": "pptx",
    "intervention_name": "GlycoTrack Remote CGM System",
}


@app.post(
    "/api/workflows/bia",
    summary="Run full BIA workflow",
    response_description=(
        "Budget Impact Analysis results with scenarios, validation, "
        "and optional PowerPoint report link"
    ),
    tags=["Workflows"],
    responses={
        200: {"description": "BIA completed (all steps succeeded)."},
        206: {"description": "BIA completed partially — calculation succeeded but an optional step failed."},
        422: {"description": "Input validation failed — fix the reported field errors and resubmit."},
        500: {"description": "BIA calculation failed — check server logs."},
    },
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "cgm_icb": {
                            "summary": "ICB — remote CGM system for T2DM",
                            "value": _BIA_EXAMPLE,
                        }
                    }
                }
            }
        }
    },
)
def workflow_bia(body: BIAWorkflowRequest) -> dict:
    """Run a full Budget Impact Analysis workflow in a single API call.

    **Five-step pipeline:**

    1. **Enrich inputs** (optional) — pre-fills missing NHS reference costs,
       ONS population estimates, and NICE comparators via the evidence agent.
    2. **Validate** — Pydantic validation + clinical-sense checks.
    3. **Calculate BIA** — base case + conservative / optimistic scenarios.
    4. **Save submission** — persists inputs and results to ``data/submissions/``.
    5. **Generate report** (optional) — branded PowerPoint slide deck.

    **Partial completion:** if optional steps (enrichment or report generation)
    fail, the endpoint still returns calculation results with ``status: "partial"``
    rather than raising an error.  Fatal steps (input validation, BIA
    calculation) raise ``422`` or ``500`` respectively.

    **Report download:** if a report is generated, use
    ``GET /api/workflows/{workflow_id}/report`` to download it.
    """
    t0 = time.perf_counter()

    # Merge optional intervention_name into the inputs dict
    effective_inputs = dict(body.inputs)
    if body.intervention_name:
        effective_inputs.setdefault("intervention_name", body.intervention_name)

    try:
        result = _orchestrator.run_full_bia_workflow(effective_inputs)
    except WorkflowError as e:
        elapsed = round(time.perf_counter() - t0, 3)
        if e.step in ("validate_inputs", "parse_inputs"):
            raise HTTPException(
                status_code=422,
                detail={
                    "message": str(e),
                    "step": e.step,
                    "workflow_id": e.workflow_id,
                    "execution_time_seconds": elapsed,
                },
            )
        raise HTTPException(
            status_code=500,
            detail={
                "message": str(e),
                "step": e.step,
                "workflow_id": e.workflow_id,
                "execution_time_seconds": elapsed,
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"BIA workflow error: {e}")

    elapsed = round(time.perf_counter() - t0, 3)
    wf_id: str = result["workflow_id"]

    # Merge BIA results dict with scenarios so helpers (break_even, top_drivers) work
    results_dict = {**result.get("bia_results", {})}
    results_dict["scenarios"] = result.get("scenarios", {})

    # PARTIAL if report was requested but the path came back empty
    report_path: Optional[str] = result.get("report_path")
    effective_status = (
        WorkflowStatus.PARTIAL
        if body.generate_report and not report_path
        else WorkflowStatus.COMPLETED
    )
    report_url = f"/api/workflows/{wf_id}/report" if report_path else None

    return BIAWorkflowResponse(
        workflow_id=wf_id,
        submission_id=wf_id,
        status=effective_status,
        results=results_dict,
        enrichment_applied={
            "evidence_enrichment_requested": body.enrich_with_evidence,
            "confidence_rating": result.get("confidence", "Unknown"),
            "validation_flags": result.get("validation", {}),
            "suggestions": result.get("suggestions", []),
        },
        warnings=result.get("warnings", []),
        report_url=report_url,
        execution_time_seconds=elapsed,
    ).model_dump()


# ── POST /api/workflows/cea ───────────────────────────────────────────────────

_CEA_EXAMPLE = {
    "inputs": {
        "prob_death_standard": 0.05,
        "cost_standard_annual": 4000,
        "utility_standard": 0.75,
        "prob_death_treatment": 0.03,
        "cost_treatment_annual": 5500,
        "cost_treatment_initial": 0,
        "utility_treatment": 0.82,
        "time_horizon": 5,
        "discount_rate": 0.035,
    },
    "validate_against_nice": True,
    "generate_report": True,
    "intervention_name": "GlycoTrack Remote CGM System",
}


@app.post(
    "/api/workflows/cea",
    summary="Run full CEA workflow (Markov model)",
    response_description=(
        "Cost-effectiveness analysis with ICER, NICE threshold flags, "
        "and optional PowerPoint report link"
    ),
    tags=["Workflows"],
    responses={
        200: {"description": "CEA completed successfully."},
        422: {"description": "Input validation failed."},
        503: {"description": "R is not installed — install from https://cran.r-project.org/"},
        500: {"description": "Markov model failed."},
    },
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "cgm_cea": {
                            "summary": "CEA — remote CGM system for T2DM",
                            "value": _CEA_EXAMPLE,
                        }
                    }
                }
            }
        }
    },
)
def workflow_cea(body: CEAWorkflowRequest) -> dict:
    """Run a full Cost-Effectiveness Analysis via a 2-state Markov model.

    **Four-step pipeline:**

    1. **NICE context** — fetch relevant NICE threshold context and comparators
       for the condition (can be skipped with ``validate_against_nice: false``).
    2. **R check** — verify ``Rscript`` is available; returns ``503`` if not.
    3. **Markov model** — run ``r/markov_model.R``, derive discounted costs,
       QALYs, ICER, and plain-English interpretation.
    4. **CEA report** (optional) — 6-slide PowerPoint deck.

    **NICE thresholds:** the response includes ``cost_effective_25k`` and
    ``cost_effective_35k`` boolean flags alongside the raw ICER.

    .. note::
        Requires ``Rscript`` to be on the server PATH.
    """
    t0 = time.perf_counter()

    try:
        result = _orchestrator.run_full_cea_workflow(body.inputs)
    except WorkflowError as e:
        elapsed = round(time.perf_counter() - t0, 3)
        if e.step == "validate_inputs":
            raise HTTPException(
                status_code=422,
                detail={"message": str(e), "step": e.step, "execution_time_seconds": elapsed},
            )
        if e.step == "check_r":
            raise HTTPException(
                status_code=503,
                detail={"message": str(e), "step": e.step, "execution_time_seconds": elapsed},
            )
        raise HTTPException(
            status_code=500,
            detail={"message": str(e), "step": e.step, "execution_time_seconds": elapsed},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"CEA workflow error: {e}")

    elapsed = round(time.perf_counter() - t0, 3)
    wf_id: str = result["workflow_id"]
    report_path: Optional[str] = result.get("report_path")
    report_url = f"/api/workflows/{wf_id}/report" if report_path else None

    return CEAWorkflowResponse(
        workflow_id=wf_id,
        status=WorkflowStatus.COMPLETED,
        results=result.get("cea_results", {}),
        validation_report=result.get("nice_context", {}),
        report_url=report_url,
        execution_time_seconds=elapsed,
    ).model_dump()


# ── POST /api/workflows/combined ─────────────────────────────────────────────

_COMBINED_EXAMPLE = {
    "bia_inputs": {
        "setting": "ICB",
        "model_year": 2025,
        "forecast_years": 3,
        "funding_source": "ICB commissioning",
        "catchment_size": 500000,
        "eligible_pct": 7.0,
        "uptake_y1": 15,
        "uptake_y2": 30,
        "uptake_y3": 45,
        "workforce": [
            {"role": "Band 5 (Staff Nurse)", "minutes": 30, "frequency": "per patient"},
        ],
        "pricing_model": "per-patient",
        "price": 750,
        "price_unit": "per year",
        "outpatient_visits": 2,
        "staff_time_saved": 20,
        "visits_reduced": 20,
    },
    "mortality_reduction_pct": 3.0,
    "utility_gain": 0.08,
    "intervention_name": "GlycoTrack Remote CGM System",
    "generate_combined_report": True,
}


@app.post(
    "/api/workflows/combined",
    summary="Run full HEOR package (BIA + CEA)",
    response_description=(
        "Combined budget impact and cost-effectiveness analysis with "
        "auto-generated executive summary"
    ),
    tags=["Workflows"],
    responses={
        200: {"description": "Combined workflow completed — both BIA and CEA succeeded."},
        206: {
            "description": (
                "BIA succeeded but CEA failed (R unavailable or Markov error). "
                "BIA results are still returned with status: partial."
            )
        },
        422: {"description": "BIA input validation failed."},
        500: {"description": "BIA calculation failed."},
    },
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "full_heor": {
                            "summary": "Full HEOR package — remote CGM for T2DM",
                            "value": _COMBINED_EXAMPLE,
                        }
                    }
                }
            }
        }
    },
)
def workflow_combined(body: CombinedWorkflowRequest) -> dict:
    """Run the full HEOR package: BIA then CEA with auto-derived Markov parameters.

    **This is the flagship endpoint** — it chains both analyses in a single
    call, automatically derives Markov CEA inputs from the BIA results, and
    writes a plain-English executive summary.

    **Markov parameter derivation:**

    - ``cost_standard_annual`` = Year 1 current pathway cost per patient
    - ``cost_treatment_annual`` = Year 1 new pathway cost per patient (from BIA)
    - ``prob_death_treatment`` = baseline mortality − (``mortality_reduction_pct`` / 100)
    - ``utility_treatment`` = baseline utility + ``utility_gain``

    The caller only needs to supply ``mortality_reduction_pct`` and
    ``utility_gain`` — all costs flow automatically from the BIA.

    **Partial completion:** if R is unavailable or the Markov step fails,
    BIA results are still returned with ``status: "partial"``.  No data is
    lost — the BIA sub-workflow has already saved its submission.

    .. note::
        Requires ``Rscript`` for the CEA sub-workflow.
    """
    t0 = time.perf_counter()

    try:
        result = _orchestrator.run_combined_workflow(
            bia_inputs=body.bia_inputs,
            mortality_reduction=body.mortality_reduction_absolute(),
            utility_gain=body.utility_gain,
        )
    except WorkflowError as e:
        elapsed = round(time.perf_counter() - t0, 3)

        # Fatal BIA validation errors → 422
        if e.step in ("validate_inputs", "parse_inputs", "derive_markov_params"):
            raise HTTPException(
                status_code=422,
                detail={"message": str(e), "step": e.step, "execution_time_seconds": elapsed},
            )

        # CEA step failed (R missing or Markov error) — try to return partial BIA result
        if e.step in ("check_r", "run_cea") or "CEA sub-workflow" in str(e):
            wf_status = _orchestrator.get_workflow_status(e.workflow_id)
            bia_res = wf_status.get("bia_results", {})
            return JSONResponse(
                status_code=206,
                content=CombinedWorkflowResponse(
                    workflow_id=e.workflow_id,
                    status=WorkflowStatus.PARTIAL,
                    bia_results={**bia_res, "scenarios": wf_status.get("scenarios", {})},
                    cea_results={},
                    execution_time_seconds=elapsed,
                ).model_dump(mode="json"),
            )

        # BIA fatal error → 500
        raise HTTPException(
            status_code=500,
            detail={"message": str(e), "step": e.step, "execution_time_seconds": elapsed},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Combined workflow error: {e}")

    elapsed = round(time.perf_counter() - t0, 3)
    wf_id: str = result["workflow_id"]

    # Build report URLs from sub-workflow IDs stored in the result
    bia_report_path: Optional[str] = result.get("report_path")
    cea_report_path: Optional[str] = result.get("cea_report_path")
    bia_wf_id = result.get("workflow_id", wf_id)
    cea_wf_id = result.get("cea_workflow_id", "")

    combined_report_url = None
    if bia_report_path:
        combined_report_url = f"/api/workflows/{bia_wf_id}/report"

    return CombinedWorkflowResponse(
        workflow_id=wf_id,
        status=WorkflowStatus.COMPLETED,
        bia_results={
            **result.get("bia_results", {}),
            "scenarios": result.get("scenarios", {}),
        },
        cea_results=result.get("cea_results", {}),
        combined_report_url=combined_report_url,
        execution_time_seconds=elapsed,
    ).model_dump()


# ── POST /api/workflows/slr ───────────────────────────────────────────────────

_SLR_EXAMPLE = {
    "pico_criteria": {
        "population": "Adults with type 2 diabetes",
        "intervention": "Remote continuous glucose monitoring (CGM)",
        "comparison": "Standard care or self-monitoring of blood glucose",
        "outcomes": ["HbA1c reduction", "Time in range", "Quality of life"],
        "study_types": ["RCT", "Cohort study", "Economic evaluation"],
        "exclusion_criteria": ["Paediatric populations", "Type 1 diabetes only"],
    },
    "abstracts": [
        {
            "pmid": "35421876",
            "title": "Continuous glucose monitoring versus SMBG in adults with T2DM: an RCT",
            "abstract": "Background: rtCGM may improve glycaemic outcomes...",
            "authors": ["Smith JA", "Patel RK"],
            "journal": "Lancet Diabetes & Endocrinology",
            "year": 2022,
        }
    ],
    "batch_name": "diabetes-cgm-q1-2026",
    "export_format": "csv",
}


@app.post(
    "/api/workflows/slr",
    summary="Run SLR abstract screening workflow",
    response_description=(
        "AI screening decisions with per-abstract PICO match grids "
        "and export download link"
    ),
    tags=["Workflows"],
    responses={
        200: {"description": "Screening completed — all abstracts processed."},
        422: {"description": "PICO or abstract validation failed."},
        503: {"description": "Anthropic API key not configured on the server."},
        500: {"description": "Screening failed."},
    },
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "diabetes_cgm": {
                            "summary": "T2DM remote CGM review",
                            "value": _SLR_EXAMPLE,
                        }
                    }
                }
            }
        }
    },
)
def workflow_slr(body: SLRWorkflowRequest) -> dict:
    """Run an AI-powered abstract screening workflow against PICO criteria.

    **Five-step pipeline:**

    1. **Validate** PICO criteria (4 required keys) and abstract list.
    2. **Create batch** — assign a UUID, persist batch metadata.
    3. **Screen** — call Claude API in batches; each abstract receives a
       verdict (``include`` / ``exclude`` / ``uncertain``), confidence level,
       reasoning, and a per-PICO-component match grid.
    4. **Save** batch JSON to ``data/slr/batches/``.
    5. **Export** CSV / Excel to ``data/slr/exports/``.

    **Response decisions list:** contains full per-abstract screening data
    loaded directly from the saved batch JSON.  Use
    ``GET /api/workflows/{workflow_id}/export`` to download the tabular file.

    .. note::
        Requires ``ANTHROPIC_API_KEY`` to be set in the server environment.
    """
    t0 = time.perf_counter()

    try:
        result = _orchestrator.run_slr_workflow(
            pico=body.pico_criteria,
            abstracts=body.abstracts,
        )
    except WorkflowError as e:
        elapsed = round(time.perf_counter() - t0, 3)
        if e.step in ("validate_pico", "parse_pico", "parse_abstracts"):
            raise HTTPException(
                status_code=422,
                detail={"message": str(e), "step": e.step, "execution_time_seconds": elapsed},
            )
        if e.step == "screen_abstracts" and ("API key" in str(e) or "ANTHROPIC_API_KEY" in str(e)):
            raise HTTPException(
                status_code=503,
                detail={"message": str(e), "step": e.step, "execution_time_seconds": elapsed},
            )
        raise HTTPException(
            status_code=500,
            detail={"message": str(e), "step": e.step, "execution_time_seconds": elapsed},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SLR workflow error: {e}")

    elapsed = round(time.perf_counter() - t0, 3)
    wf_id: str = result["workflow_id"]

    # Load full per-abstract decisions from the saved batch JSON
    decisions_list: list[dict] = []
    batch_path = result.get("batch_path")
    if batch_path:
        batch_file = Path(batch_path)
        if batch_file.exists():
            try:
                raw_batch = json.loads(batch_file.read_text(encoding="utf-8"))
                decisions_list = raw_batch.get("decisions", [])
            except Exception:
                pass  # summary counts are still accurate; decisions gracefully empty

    export_url = f"/api/workflows/{wf_id}/export" if result.get("export_path") else None
    total = result["total"]

    return SLRWorkflowResponse(
        workflow_id=wf_id,
        batch_id=result["batch_id"],
        status=WorkflowStatus.COMPLETED,
        screening_summary={
            "total": total,
            "included": result["included"],
            "excluded": result["excluded"],
            "uncertain": result["uncertain"],
            "inclusion_rate": round(result["included"] / total, 4) if total else 0.0,
        },
        decisions=decisions_list,
        export_url=export_url,
        execution_time_seconds=elapsed,
    ).model_dump()


# ── GET /api/workflows/{workflow_id} ──────────────────────────────────────────

@app.get(
    "/api/workflows/{workflow_id}",
    summary="Get status and results of any workflow",
    response_description="Full workflow state including step-by-step audit log",
    tags=["Workflows"],
    responses={
        200: {"description": "Workflow state returned."},
        404: {"description": "Workflow not found in memory or on disk."},
    },
)
def get_workflow(workflow_id: str) -> dict:
    """Return the current status and full audit log for any workflow.

    Works for all workflow types: ``bia``, ``cea``, ``combined``, ``slr``,
    and ``enrich``.

    The ``steps`` array provides a complete audit trail — each entry
    records the step name, status, UTC timestamp, and per-step metadata
    (e.g. break-even year after BIA, ICER after CEA).

    If the workflow is not in the server's in-memory cache (e.g. after a
    restart), the state is reloaded transparently from
    ``data/workflows/{workflow_id}.json``.
    """
    state = _orchestrator.get_workflow_status(workflow_id)
    if "error" in state:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Workflow '{workflow_id}' not found. "
                "Verify the workflow_id or check whether the server has been "
                "restarted without persisted state."
            ),
        )
    return state


# ── GET /api/workflows/{workflow_id}/report ───────────────────────────────────

@app.get(
    "/api/workflows/{workflow_id}/report",
    summary="Download a workflow's generated report",
    tags=["Workflows"],
    response_class=FileResponse,
    responses={
        200: {"description": "Report file served as attachment."},
        404: {"description": "Workflow not found or report was not generated."},
    },
)
def download_workflow_report(workflow_id: str):
    """Download the PowerPoint (or Word) report generated by any workflow.

    The report path is resolved from the ``generate_report`` step in the
    workflow audit log stored at ``data/workflows/{workflow_id}.json``.

    Returns a ``404`` if:
    - The workflow does not exist.
    - The workflow ran with ``generate_report: false``.
    - Report generation failed (step recorded as failed in the audit log).
    - The file has been deleted from disk.
    """
    state = _orchestrator.get_workflow_status(workflow_id)
    if "error" in state:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found.")

    # Walk steps in reverse to find the most recent completed generate_report entry
    report_path: Optional[str] = None
    for step in reversed(state.get("steps", [])):
        if step.get("step") == "generate_report" and step.get("status") == "completed":
            report_path = step.get("details", {}).get("report_path")
            break

    if not report_path or not Path(report_path).exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"No report found for workflow '{workflow_id}'. "
                "Ensure the workflow completed with generate_report=true and "
                "that the report file has not been deleted."
            ),
        )

    suffix = Path(report_path).suffix.lower()
    _mime = {
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    media_type = _mime.get(suffix, "application/octet-stream")
    filename = f"HEOR_Report_{workflow_id}{suffix}"

    return FileResponse(
        path=report_path,
        media_type=media_type,
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── GET /api/workflows/{workflow_id}/export ───────────────────────────────────

@app.get(
    "/api/workflows/{workflow_id}/export",
    summary="Download a workflow's SLR export file",
    tags=["Workflows"],
    response_class=FileResponse,
    responses={
        200: {"description": "Export file served as attachment."},
        404: {"description": "Workflow not found or export file not available."},
    },
)
def download_workflow_export(workflow_id: str):
    """Download the CSV or Excel export generated by an SLR workflow.

    The export path is resolved from the ``export_csv`` step in the
    workflow audit log.
    """
    state = _orchestrator.get_workflow_status(workflow_id)
    if "error" in state:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found.")

    export_path: Optional[str] = None
    for step in reversed(state.get("steps", [])):
        if step.get("step") == "export_csv" and step.get("status") == "completed":
            export_path = step.get("details", {}).get("export_path")
            break

    if not export_path or not Path(export_path).exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"No export file found for workflow '{workflow_id}'. "
                "Ensure this is an SLR workflow and that screening completed successfully."
            ),
        )

    suffix = Path(export_path).suffix.lower()
    _mime = {
        ".csv": "text/csv",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    media_type = _mime.get(suffix, "text/csv")
    filename = f"SLR_Screening_{workflow_id}{suffix}"

    return FileResponse(
        path=export_path,
        media_type=media_type,
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── GET /api/workflows ────────────────────────────────────────────────────────

@app.get(
    "/api/workflows",
    summary="List all saved workflows",
    response_description="Paginated workflow summaries with optional type/status/date filters",
    tags=["Workflows"],
    responses={
        200: {"description": "Workflow list returned."},
        422: {"description": "Invalid date format in from_date or to_date."},
    },
)
def list_workflows(
    type: Optional[str] = Query(
        default=None,
        description=(
            "Filter by workflow type. "
            "Values: ``bia``, ``cea``, ``combined``, ``slr``, ``enrich``."
        ),
    ),
    status: Optional[str] = Query(
        default=None,
        description=(
            "Filter by terminal status. "
            "Values: ``completed``, ``failed``, ``partial``, ``started``."
        ),
    ),
    from_date: Optional[str] = Query(
        default=None,
        description=(
            "ISO 8601 date or datetime — include only workflows created "
            "**on or after** this timestamp. Example: ``2026-01-01``."
        ),
    ),
    to_date: Optional[str] = Query(
        default=None,
        description=(
            "ISO 8601 date or datetime — include only workflows created "
            "**on or before** this timestamp. Example: ``2026-12-31``."
        ),
    ),
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)."),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page (1–100)."),
) -> dict:
    """List all saved workflows, newest first, with optional filtering and pagination.

    Workflows are loaded from ``data/workflows/*.json``.

    **Filter examples:**

    - ``?type=bia`` — only BIA workflows
    - ``?status=completed&type=slr`` — completed SLR runs
    - ``?from_date=2026-02-01&to_date=2026-02-28`` — all runs in February
    - ``?type=combined&status=completed&page=2&page_size=10`` — paginated

    Each item in ``workflows`` contains: ``workflow_id``, ``workflow_type``,
    ``status``, ``created_at``, ``updated_at``, ``inputs_summary``
    (lightweight, no raw data), and ``step_count``.
    """
    # Parse optional date bounds
    from_dt: Optional[datetime] = None
    to_dt: Optional[datetime] = None
    try:
        if from_date:
            from_dt = datetime.fromisoformat(from_date.replace("Z", "+00:00"))
            if from_dt.tzinfo is None:
                from_dt = from_dt.replace(tzinfo=timezone.utc)
        if to_date:
            to_dt = datetime.fromisoformat(to_date.replace("Z", "+00:00"))
            if to_dt.tzinfo is None:
                to_dt = to_dt.replace(tzinfo=timezone.utc)
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid date format: {e}. "
                "Use ISO 8601 — e.g. '2026-01-15' or '2026-01-15T12:00:00Z'."
            ),
        )

    workflows: list[dict] = []
    for wf_file in sorted(_WORKFLOWS_DIR.glob("*.json"), reverse=True):
        try:
            state = json.loads(wf_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        # Type filter
        if type is not None and state.get("workflow_type") != type:
            continue

        # Status filter
        if status is not None and state.get("status") != status:
            continue

        # Date-range filters (parse created_at once)
        created_str: Optional[str] = state.get("created_at")
        if (from_dt or to_dt) and created_str:
            try:
                created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)
                if from_dt and created_dt < from_dt:
                    continue
                if to_dt and created_dt > to_dt:
                    continue
            except ValueError:
                pass

        workflows.append({
            "workflow_id": state.get("workflow_id"),
            "workflow_type": state.get("workflow_type"),
            "status": state.get("status"),
            "created_at": state.get("created_at"),
            "updated_at": state.get("updated_at"),
            "inputs_summary": state.get("inputs_summary", {}),
            "step_count": len(state.get("steps", [])),
        })

    total = len(workflows)
    start = (page - 1) * page_size
    page_items = workflows[start: start + page_size]

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "has_next": (start + page_size) < total,
        "has_prev": page > 1,
        "filters": {
            "type": type,
            "status": status,
            "from_date": from_date,
            "to_date": to_date,
        },
        "workflows": page_items,
    }


# ── POST /api/workflows/quick-estimate ───────────────────────────────────────

@app.post(
    "/api/workflows/quick-estimate",
    summary="Back-of-envelope BIA quick estimate",
    response_description=(
        "3-year net budget impact, break-even year, and actionable next steps "
        "using NHS evidence-based pathway defaults"
    ),
    tags=["Workflows"],
    responses={
        200: {"description": "Quick estimate generated."},
        422: {"description": "Input validation failed."},
    },
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "remote_monitoring": {
                            "summary": "Remote monitoring — diabetes (500k ICB)",
                            "value": {
                                "condition": "diabetes",
                                "intervention_name": "Remote monitoring device",
                                "catchment_population": 500000,
                                "device_cost_per_patient": 750,
                                "expected_los_reduction_days": 0,
                                "expected_visit_reduction_pct": 20,
                            },
                        },
                        "ai_triage": {
                            "summary": "AI ECG triage — cardiovascular (300k ICB)",
                            "value": {
                                "condition": "cardiovascular",
                                "intervention_name": "AI ECG triage tool",
                                "catchment_population": 300000,
                                "device_cost_per_patient": 450,
                                "expected_los_reduction_days": 1.5,
                                "expected_visit_reduction_pct": 15,
                            },
                        },
                    }
                }
            }
        }
    },
)
def quick_estimate(body: QuickEstimateRequest) -> dict:
    """Generate a rapid back-of-envelope Budget Impact Analysis.

    Designed for early-stage market access conversations where full
    pathway data is not yet available.  Builds a valid ``BIAInputs`` object
    from condition-specific NHS defaults, runs the standard BIA engine, and
    returns a 3-year summary.

    **Condition defaults applied:**

    | Condition | Eligible % | OPD visits | Admissions | Staff |
    |---|---|---|---|---|
    | ``diabetes`` | 7 % | 2/yr | 0 | Band 5, 30 min |
    | ``cardiovascular`` | 4 % | 3/yr | 1 (4 days) | Band 6, 45 min |
    | ``cancer`` | 1 % | 8/yr | 2 (5 days) | Band 7, 60 min |
    | ``respiratory`` | 10 % | 2/yr | 1 (3 days) | Band 5, 20 min |

    The response includes a ``next_steps`` map pointing to the full workflow
    endpoints for a NICE-ready analysis.

    .. warning::
        This estimate uses national NHS defaults and is **not** suitable for
        NICE submissions, ICB business cases, or HTA dossiers.
    """
    t0 = time.perf_counter()

    condition = body.condition.strip().lower()
    defaults = _QUICK_ESTIMATE_DEFAULTS.get(condition, _QUICK_ESTIMATE_DEFAULTS["default"])

    # Build a complete, valid BIAInputs dict using condition defaults
    model_year = datetime.now(timezone.utc).year
    bia_dict: dict = {
        "setting": "ICB",
        "model_year": max(2024, min(2030, model_year)),
        "forecast_years": 3,
        "funding_source": "ICB commissioning",
        "catchment_type": "population",
        "catchment_size": body.catchment_population,
        "eligible_pct": defaults["eligible_pct"],
        "uptake_y1": 15.0,
        "uptake_y2": 30.0,
        "uptake_y3": 45.0,
        "workforce": [
            {
                "role": defaults["staff_role"],
                "minutes": float(defaults["staff_minutes"]),
                "frequency": "per patient",
            }
        ],
        "outpatient_visits": defaults["outpatient_visits"],
        "admissions": defaults["admissions"],
        "bed_days": defaults["bed_days"],
        "tests": defaults["tests"],
        "consumables": 0.0,
        "procedures": 0,
        "pricing_model": "per-patient",
        "price": body.device_cost_per_patient,
        "price_unit": "per year",
        "needs_training": False,
        "setup_cost": 0.0,
        "staff_time_saved": 0.0,
        "visits_reduced": body.expected_visit_reduction_pct,
        "los_reduced": body.expected_los_reduction_days,
        "complications_reduced": 0.0,
        "readmissions_reduced": 0.0,
        "follow_up_reduced": 0.0,
        "comparator": "none",
        "discounting": "off",
    }

    try:
        bia_inputs = BIAInputs(**bia_dict)
        results = calculate_budget_impact(bia_inputs)
    except Exception as e:
        raise HTTPException(
            status_code=422,
            detail=f"Could not build BIA inputs from quick-estimate parameters: {e}",
        )

    elapsed = round(time.perf_counter() - t0, 3)

    impacts = results.annual_budget_impact
    cumulative = round(sum(impacts), 0)
    direction = "saving" if cumulative < 0 else "investment"
    eligible = bia_inputs.eligible_patients
    condition_label = _CONDITION_DEFAULTS.get(condition, {}).get("label", condition.title())

    return {
        "intervention_name": body.intervention_name,
        "condition": condition_label,
        "catchment_population": body.catchment_population,
        "defaults_applied": {
            "eligible_pct": defaults["eligible_pct"],
            "outpatient_visits_per_year": defaults["outpatient_visits"],
            "staff_role": defaults["staff_role"],
            "staff_minutes_per_patient": defaults["staff_minutes"],
            "uptake_trajectory": "15 % → 30 % → 45 %",
        },
        "estimate": {
            "eligible_patients": eligible,
            "treated_patients_by_year": results.total_treated_patients,
            "annual_impacts": [
                {"year": f"Year {i + 1}", "net_budget_impact_gbp": round(v, 0)}
                for i, v in enumerate(impacts)
            ],
            "cumulative_3yr_net_impact_gbp": cumulative,
            "break_even_year": results.break_even_year,
            "top_cost_drivers": results.top_cost_drivers,
        },
        "interpretation": (
            f"Under NHS default assumptions, this intervention is projected to require "
            f"a {direction} of £{abs(cumulative):,.0f} over 3 years across an estimated "
            f"{eligible:,} eligible patients."
            + (
                f" Break-even is projected in Year {results.break_even_year}."
                if results.break_even_year
                else " The model does not project break-even within 3 years."
            )
        ),
        "caveats": [
            "All pathway costs use national NHS reference costs — local tariffs may differ.",
            (
                f"Eligible population estimated at {defaults['eligible_pct']}% of "
                f"catchment ({condition_label} NHS prevalence proxy)."
            ),
            "Staff time savings are not included (set to zero in this quick estimate).",
            "No training or setup costs are included.",
            "Uptake trajectory (15 % → 30 % → 45 %) is a generic assumption.",
        ],
        "next_steps": {
            "full_bia": "POST /api/workflows/bia — full BIA with your actual pathway data.",
            "cea": "POST /api/workflows/cea — add Markov cost-effectiveness (ICER/QALY) analysis.",
            "combined": "POST /api/workflows/combined — full HEOR package (BIA + CEA) in one call.",
            "evidence": "POST /api/workflows/slr — screen published evidence for your PICO criteria.",
        },
        "execution_time_seconds": elapsed,
        "disclaimer": (
            "This quick estimate uses NHS-wide default assumptions. "
            "It is intended for indicative purposes only and is not suitable for "
            "NICE submissions, ICB business cases, or HTA dossiers without "
            "full pathway costing."
        ),
    }


# ── Auto-populate: shared task store + rate limiter ──────────────────────────
#
# Tasks are keyed by UUID task_id. Each entry holds:
#   status  : "queued" | "searching" | "extracting" | "populating" | "complete" | "failed"
#   step    : human-readable current step description
#   result  : final result dict (populated on completion)
#   error   : error message (populated on failure)
#   created : ISO-8601 timestamp
#   elapsed : seconds taken (populated on completion)

_AUTO_POPULATE_TASKS: dict[str, dict] = {}
_AUTO_POPULATE_LOCK = threading.Lock()

# Rate limiting: max 5 auto-populate requests per IP per 60-second window
_RATE_LIMIT_WINDOW = 60        # seconds
_RATE_LIMIT_MAX = 5            # requests per window
_RATE_LIMIT_STORE: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT_LOCK = threading.Lock()

# Shared AutoPopulator instance (lazy-init avoids import-time side-effects)
_auto_populator: Optional[AutoPopulator] = None
_auto_populator_lock = threading.Lock()


def _get_auto_populator() -> AutoPopulator:
    global _auto_populator
    if _auto_populator is None:
        with _auto_populator_lock:
            if _auto_populator is None:
                _auto_populator = AutoPopulator()
    return _auto_populator


def _check_rate_limit(client_ip: str) -> None:
    """Raise HTTP 429 if the IP has exceeded the rate limit."""
    now = time.time()
    with _RATE_LIMIT_LOCK:
        timestamps = _RATE_LIMIT_STORE[client_ip]
        # Evict old entries outside the current window
        _RATE_LIMIT_STORE[client_ip] = [t for t in timestamps if now - t < _RATE_LIMIT_WINDOW]
        if len(_RATE_LIMIT_STORE[client_ip]) >= _RATE_LIMIT_MAX:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Rate limit exceeded: max {_RATE_LIMIT_MAX} auto-populate requests "
                    f"per {_RATE_LIMIT_WINDOW}s per IP. Please wait and retry."
                ),
                headers={"Retry-After": str(_RATE_LIMIT_WINDOW)},
            )
        _RATE_LIMIT_STORE[client_ip].append(now)


def _new_task_id() -> str:
    return uuid.uuid4().hex


def _set_task_status(task_id: str, status: str, step: str = "") -> None:
    with _AUTO_POPULATE_LOCK:
        if task_id in _AUTO_POPULATE_TASKS:
            _AUTO_POPULATE_TASKS[task_id]["status"] = status
            if step:
                _AUTO_POPULATE_TASKS[task_id]["step"] = step


# ── Pydantic schemas ──────────────────────────────────────────────────────────


class AutoPopulateBIARequest(BaseModel):
    """Minimal input required to auto-populate a full BIA form from evidence."""

    device_name: str = Field(
        ...,
        min_length=1,
        description="Name of the device, technology, or AI tool being evaluated.",
        examples=["AI Sepsis Prediction Tool"],
    )
    indication: str = Field(
        ...,
        min_length=2,
        description="Clinical indication and patient population.",
        examples=["Sepsis in ICU patients"],
    )
    setting: str = Field(
        "UK NHS Acute Trust",
        description="NHS setting (e.g. 'Acute NHS Trust', 'ICB', 'Primary Care Network').",
        examples=["Acute NHS Trust"],
    )
    device_cost_per_patient: float = Field(
        0.0,
        ge=0,
        description="Known per-patient device/licence cost (£). 0 = let evidence estimate it.",
        examples=[185.0],
    )
    expected_benefits: str = Field(
        "",
        description=(
            "Free-text description of the anticipated clinical and economic benefits "
            "as claimed by the manufacturer or sponsor."
        ),
        examples=["Earlier detection enabling faster antibiotic administration"],
    )
    forecast_years: int = Field(
        3,
        ge=1,
        le=10,
        description="Number of years to forecast in the BIA.",
    )
    model_year: int = Field(
        2025,
        ge=2024,
        le=2030,
        description="Financial year the model starts.",
    )


class AutoPopulateMarkovRequest(BaseModel):
    """Inputs for auto-populating Markov CEA parameters."""

    device_name: str = Field(..., min_length=1, description="Name of the intervention.")
    indication: str = Field(..., min_length=2, description="Clinical indication.")
    bia_inputs: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Pre-filled BIA inputs dict (output of /api/auto-populate/bia). "
            "If provided, Markov parameters are derived directly from these without "
            "re-running evidence gathering."
        ),
    )
    bia_submission_id: Optional[str] = Field(
        None,
        description="ID of a saved BIA submission to derive Markov parameters from.",
    )
    clinical_data: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Clinical data extractions dict. If omitted, the agent will run "
            "a fresh PubMed search to gather clinical evidence."
        ),
    )


class AutoBIAWorkflowRequest(BaseModel):
    """Minimal input for the one-click auto BIA workflow."""

    device_name: str = Field(..., min_length=1, examples=["AI Sepsis Prediction Tool"])
    indication: str = Field(..., min_length=2, examples=["Sepsis in ICU patients"])
    setting: str = Field("UK NHS Acute Trust", examples=["Acute NHS Trust"])
    device_cost_per_patient: float = Field(0.0, ge=0, examples=[185.0])
    expected_benefits: str = Field("", examples=["Earlier detection, faster treatment"])
    forecast_years: int = Field(3, ge=1, le=10)
    model_year: int = Field(2025, ge=2024, le=2030)
    include_validation: bool = Field(
        True,
        description="Run Claude plausibility validation on the auto-populated inputs.",
    )
    generate_report: bool = Field(
        True,
        description="Generate a PowerPoint report after the BIA calculation.",
    )


class TaskStatusResponse(BaseModel):
    """Status of a background auto-populate task."""

    task_id: str
    status: str = Field(
        ...,
        description="One of: queued, searching, extracting, populating, complete, failed",
    )
    step: str = Field("", description="Human-readable description of the current step.")
    result: Optional[Dict[str, Any]] = Field(
        None, description="Final result, populated when status=complete."
    )
    error: Optional[str] = Field(
        None, description="Error message, populated when status=failed."
    )
    created: str
    elapsed_seconds: Optional[float] = None


# ── Background worker functions ───────────────────────────────────────────────


def _run_auto_populate_bia_task(task_id: str, req: AutoPopulateBIARequest) -> None:
    """Background worker for POST /api/auto-populate/bia."""
    t0 = time.perf_counter()
    populator = _get_auto_populator()

    user_desc = {
        "device_name": req.device_name,
        "indication": req.indication,
        "setting": req.setting,
        "device_cost": req.device_cost_per_patient,
        "expected_benefits": req.expected_benefits,
        "forecast_years": req.forecast_years,
        "model_year": req.model_year,
    }

    try:
        _set_task_status(task_id, "searching", "Searching PubMed and NICE guidance...")
        result = populator.auto_populate_bia(user_desc)

        _set_task_status(task_id, "complete", "Done")
        elapsed = round(time.perf_counter() - t0, 2)

        # Build the response shape
        bia_inputs = result.get("bia_inputs", {})
        raw = result.get("raw_evidence", {})

        response_payload = {
            "bia_inputs": bia_inputs,
            "evidence_summary": {
                "papers_found": raw.get("n_pubmed_articles", 0),
                "nice_guidance_found": raw.get("n_nice_docs", 0),
                "search_queries": raw.get("search_queries", []),
                "data_quality": _score_to_quality(
                    result.get("confidence_scores", {}).get("overall", "low")
                ),
            },
            "confidence_scores": result.get("confidence_scores", {}),
            "warnings": result.get("warnings", []),
            "assumptions": result.get("assumptions", []),
            "sources": result.get("evidence_sources", []),
            "elapsed_seconds": elapsed,
        }

        with _AUTO_POPULATE_LOCK:
            _AUTO_POPULATE_TASKS[task_id].update(
                {
                    "status": "complete",
                    "result": response_payload,
                    "elapsed": elapsed,
                }
            )

    except Exception as exc:
        elapsed = round(time.perf_counter() - t0, 2)
        with _AUTO_POPULATE_LOCK:
            _AUTO_POPULATE_TASKS[task_id].update(
                {
                    "status": "failed",
                    "error": str(exc),
                    "elapsed": elapsed,
                }
            )


def _run_auto_populate_markov_task(task_id: str, req: AutoPopulateMarkovRequest) -> None:
    """Background worker for POST /api/auto-populate/markov."""
    t0 = time.perf_counter()
    populator = _get_auto_populator()

    try:
        _set_task_status(task_id, "searching", "Gathering clinical evidence...")

        # Resolve bia_inputs: from request body, from submission ID, or fresh
        bia_inputs: dict = {}
        if req.bia_inputs:
            bia_inputs = req.bia_inputs
        elif req.bia_submission_id:
            submission_path = SUBMISSIONS_DIR / f"{req.bia_submission_id}.json"
            if submission_path.exists():
                raw_sub = json.loads(submission_path.read_text())
                bia_inputs = raw_sub
            else:
                raise ValueError(f"Submission '{req.bia_submission_id}' not found")
        else:
            # Auto-populate BIA first to get bia_inputs
            _set_task_status(task_id, "searching", "Auto-populating BIA inputs first...")
            user_desc = {
                "device_name": req.device_name,
                "indication": req.indication,
            }
            bia_result = populator.auto_populate_bia(user_desc)
            bia_inputs = bia_result.get("bia_inputs", {})

        # Add intervention_name if missing
        if "intervention_name" not in bia_inputs:
            bia_inputs["intervention_name"] = req.device_name

        # Clinical data: from request or empty (Markov derivation handles it)
        clinical_data = req.clinical_data or {}

        _set_task_status(task_id, "populating", "Deriving Markov parameters from evidence...")
        result = populator.auto_populate_markov(bia_inputs, clinical_data)

        elapsed = round(time.perf_counter() - t0, 2)
        response_payload = {
            "markov_inputs": result.get("markov_inputs", {}),
            "derivation_notes": result.get("derivation_notes", []),
            "confidence_scores": result.get("confidence_scores", {}),
            "assumptions": result.get("assumptions", []),
            "warnings": result.get("warnings", []),
            "bia_inputs_used": bia_inputs,
            "elapsed_seconds": elapsed,
        }

        with _AUTO_POPULATE_LOCK:
            _AUTO_POPULATE_TASKS[task_id].update(
                {
                    "status": "complete",
                    "result": response_payload,
                    "elapsed": elapsed,
                }
            )

    except Exception as exc:
        elapsed = round(time.perf_counter() - t0, 2)
        with _AUTO_POPULATE_LOCK:
            _AUTO_POPULATE_TASKS[task_id].update(
                {
                    "status": "failed",
                    "error": str(exc),
                    "elapsed": elapsed,
                }
            )


def _run_auto_bia_workflow_task(task_id: str, req: AutoBIAWorkflowRequest) -> None:
    """Background worker for POST /api/workflows/auto-bia (magic button)."""
    t0 = time.perf_counter()
    populator = _get_auto_populator()

    user_desc = {
        "device_name": req.device_name,
        "indication": req.indication,
        "setting": req.setting,
        "device_cost": req.device_cost_per_patient,
        "expected_benefits": req.expected_benefits,
        "forecast_years": req.forecast_years,
        "model_year": req.model_year,
    }

    try:
        # ── Step a: Auto-populate ─────────────────────────────────────────
        _set_task_status(task_id, "searching", "Step 1/4 — Searching PubMed and NICE guidance...")
        populate_result = populator.auto_populate_bia(user_desc)
        bia_inputs_dict = populate_result.get("bia_inputs", {})

        # ── Step b: Save submission + calculate BIA ───────────────────────
        _set_task_status(task_id, "extracting", "Step 2/4 — Running BIA calculation...")
        try:
            bia_inputs = BIAInputs(**bia_inputs_dict)
        except Exception as val_exc:
            raise ValueError(f"Auto-populated BIA inputs failed validation: {val_exc}") from val_exc

        try:
            bia_results = calculate_budget_impact(bia_inputs)
        except Exception as calc_exc:
            raise RuntimeError(f"BIA calculation failed: {calc_exc}") from calc_exc

        # ── Step c: Generate scenarios ────────────────────────────────────
        _set_task_status(task_id, "populating", "Step 3/4 — Generating conservative/base/optimistic scenarios...")
        try:
            scenarios = calculate_scenarios(bia_inputs)
        except Exception as sc_exc:
            scenarios = {}

        # Clinical warnings from BIA validation
        clinical_warnings = validate_clinical_sense(bia_inputs)
        suggestions = suggest_missing_inputs(bia_inputs)
        confidence = estimate_confidence(bia_inputs)

        # ── Step d: Optionally validate auto-population ───────────────────
        validation_result: dict = {}
        if req.include_validation:
            _set_task_status(task_id, "populating", "Step 3b/4 — Validating auto-populated values...")
            try:
                validation_result = populator.validate_auto_population(
                    bia_inputs_dict,
                    populate_result.get("raw_evidence", {}),
                )
            except Exception:
                pass  # Non-fatal

        # ── Step e: Save submission to disk ──────────────────────────────
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        submission_path = SUBMISSIONS_DIR / f"{ts}.json"
        try:
            submission_path.write_text(
                json.dumps(
                    {
                        "workflow": "auto-bia",
                        "device_name": req.device_name,
                        "indication": req.indication,
                        "inputs": bia_inputs.model_dump(),
                        "results": bia_results.model_dump(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass  # Non-fatal

        # ── Step f: Generate PowerPoint report ───────────────────────────
        report_path: Optional[str] = None
        if req.generate_report:
            _set_task_status(task_id, "populating", "Step 4/4 — Generating PowerPoint report...")
            try:
                path = generate_bia_report(bia_inputs, bia_results)
                report_path = str(path)
            except Exception:
                pass  # Non-fatal

        elapsed = round(time.perf_counter() - t0, 2)
        raw = populate_result.get("raw_evidence", {})

        response_payload = {
            # Evidence gathering
            "evidence_summary": {
                "papers_found": raw.get("n_pubmed_articles", 0),
                "nice_guidance_found": raw.get("n_nice_docs", 0),
                "search_queries": raw.get("search_queries", []),
                "data_quality": _score_to_quality(
                    populate_result.get("confidence_scores", {}).get("overall", "low")
                ),
            },
            # Auto-populated form
            "bia_inputs": bia_inputs_dict,
            "auto_populate_confidence": populate_result.get("confidence_scores", {}),
            "auto_populate_assumptions": populate_result.get("assumptions", []),
            "auto_populate_warnings": populate_result.get("warnings", []),
            "sources": populate_result.get("evidence_sources", []),
            # BIA results
            "bia_results": bia_results.model_dump(),
            "scenarios": {k: v.model_dump() for k, v in scenarios.items()} if scenarios else {},
            "bia_validation": {
                "clinical_warnings": clinical_warnings,
                "suggestions": suggestions,
                "confidence": confidence,
            },
            # Plausibility check
            "auto_populate_validation": validation_result,
            # Outputs
            "submission_id": ts,
            "report_path": report_path,
            "report_available": report_path is not None,
            "elapsed_seconds": elapsed,
            # Guidance
            "next_steps": {
                "download_report": (
                    f"GET /api/download-report/{ts}"
                    if report_path else "Report generation failed"
                ),
                "run_cea": (
                    "POST /api/auto-populate/markov — derive Markov CEA parameters "
                    "from this BIA to generate an ICER."
                ),
                "refine_inputs": (
                    f"POST /api/calculate-bia?submission_id={ts} — re-run with "
                    "manually adjusted inputs."
                ),
            },
        }

        with _AUTO_POPULATE_LOCK:
            _AUTO_POPULATE_TASKS[task_id].update(
                {
                    "status": "complete",
                    "result": response_payload,
                    "elapsed": elapsed,
                }
            )

    except Exception as exc:
        elapsed = round(time.perf_counter() - t0, 2)
        with _AUTO_POPULATE_LOCK:
            _AUTO_POPULATE_TASKS[task_id].update(
                {
                    "status": "failed",
                    "error": str(exc),
                    "elapsed": elapsed,
                }
            )


def _score_to_quality(score: str) -> str:
    """Map confidence score string to data quality label."""
    return {"high": "good", "medium": "moderate", "low": "limited"}.get(
        score.lower() if score else "", "unknown"
    )


# ── POST /api/auto-populate/bia ───────────────────────────────────────────────


@app.post(
    "/api/auto-populate/bia",
    summary="Auto-populate BIA inputs from evidence (async)",
    response_description=(
        "Task ID for polling. Fetch result from "
        "GET /api/auto-populate/status/{task_id} when status=complete."
    ),
    tags=["Auto-populate"],
    responses={
        202: {"description": "Task accepted — poll /api/auto-populate/status/{task_id}."},
        429: {"description": "Rate limit exceeded."},
        422: {"description": "Request body validation failed."},
    },
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "sepsis_ai": {
                            "summary": "AI Sepsis Detection — ICU",
                            "value": {
                                "device_name": "AI Sepsis Prediction Tool",
                                "indication": "Sepsis in ICU patients",
                                "setting": "Acute NHS Trust",
                                "device_cost_per_patient": 185,
                                "expected_benefits": (
                                    "Earlier detection enabling faster antibiotic administration"
                                ),
                            },
                        },
                        "af_detection": {
                            "summary": "AI AF Detection — Cardiology outpatients",
                            "value": {
                                "device_name": "AI ECG Atrial Fibrillation Detector",
                                "indication": "Atrial fibrillation detection in outpatients",
                                "setting": "Acute NHS Trust",
                                "device_cost_per_patient": 45,
                                "expected_benefits": "Reduced missed diagnoses, earlier treatment",
                            },
                        },
                    }
                }
            }
        }
    },
)
async def auto_populate_bia_endpoint(
    body: AutoPopulateBIARequest,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Kick off evidence-based BIA auto-population as a background task.

    Evidence gathering (PubMed + NICE) takes 30–90 seconds. This endpoint
    returns immediately with a ``task_id``; poll
    ``GET /api/auto-populate/status/{task_id}`` until ``status == "complete"``.

    **Workflow inside the task:**

    1. Claude generates 4 targeted PubMed search queries.
    2. PubMed, NICE, NHS costs, and ONS population data are fetched in parallel.
    3. Claude extracts mortality, LOS, cost, and readmission data from abstracts.
    4. Claude synthesises all evidence into every ``BIAInputs`` field.

    The final ``bia_inputs`` object can be submitted directly to
    ``POST /api/calculate-bia`` (after saving via ``POST /api/inputs``).
    """
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)

    task_id = _new_task_id()
    with _AUTO_POPULATE_LOCK:
        _AUTO_POPULATE_TASKS[task_id] = {
            "task_id": task_id,
            "type": "auto-populate-bia",
            "status": "queued",
            "step": "Queued — waiting to start",
            "result": None,
            "error": None,
            "created": datetime.now(timezone.utc).isoformat(),
            "elapsed": None,
            "request": {
                "device_name": body.device_name,
                "indication": body.indication,
                "setting": body.setting,
            },
        }

    background_tasks.add_task(_run_auto_populate_bia_task, task_id, body)

    return JSONResponse(
        status_code=202,
        content={
            "task_id": task_id,
            "status": "queued",
            "poll_url": f"/api/auto-populate/status/{task_id}",
            "message": (
                "Evidence gathering started. This typically takes 30–90 seconds. "
                f"Poll GET /api/auto-populate/status/{task_id} for updates."
            ),
        },
    )


# ── POST /api/auto-populate/markov ───────────────────────────────────────────


@app.post(
    "/api/auto-populate/markov",
    summary="Auto-populate Markov CEA parameters from evidence (async)",
    response_description="Task ID for polling.",
    tags=["Auto-populate"],
    responses={
        202: {"description": "Task accepted."},
        429: {"description": "Rate limit exceeded."},
        422: {"description": "Request body validation failed."},
    },
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "from_bia_inputs": {
                            "summary": "Derive from existing BIA inputs",
                            "value": {
                                "device_name": "AI Sepsis Prediction Tool",
                                "indication": "Sepsis in ICU patients",
                                "bia_inputs": {
                                    "price": 185,
                                    "bed_days": 8,
                                    "los_reduced": 2.5,
                                    "complications_reduced": 20,
                                },
                            },
                        },
                        "from_submission_id": {
                            "summary": "Derive from saved BIA submission",
                            "value": {
                                "device_name": "AI Sepsis Prediction Tool",
                                "indication": "Sepsis in ICU patients",
                                "bia_submission_id": "20250301_120000_abcdef12",
                            },
                        },
                    }
                }
            }
        }
    },
)
async def auto_populate_markov_endpoint(
    body: AutoPopulateMarkovRequest,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Auto-populate Markov CEA parameters from clinical evidence.

    Derives evidence-based values for:
    - Annual mortality probability (standard care and treatment arms)
    - Annual NHS cost per patient alive (both arms)
    - EQ-5D utility weights (both arms)
    - One-off upfront treatment cost

    Supply ``bia_inputs`` or ``bia_submission_id`` to skip the BIA
    auto-population step and derive Markov parameters directly.

    Returns a ``markov_inputs`` dict ready for ``POST /api/workflows/cea``.
    """
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)

    task_id = _new_task_id()
    with _AUTO_POPULATE_LOCK:
        _AUTO_POPULATE_TASKS[task_id] = {
            "task_id": task_id,
            "type": "auto-populate-markov",
            "status": "queued",
            "step": "Queued — waiting to start",
            "result": None,
            "error": None,
            "created": datetime.now(timezone.utc).isoformat(),
            "elapsed": None,
            "request": {
                "device_name": body.device_name,
                "indication": body.indication,
            },
        }

    background_tasks.add_task(_run_auto_populate_markov_task, task_id, body)

    return JSONResponse(
        status_code=202,
        content={
            "task_id": task_id,
            "status": "queued",
            "poll_url": f"/api/auto-populate/status/{task_id}",
            "message": (
                "Markov parameter derivation started. "
                f"Poll GET /api/auto-populate/status/{task_id} for updates."
            ),
        },
    )


# ── POST /api/workflows/auto-bia ─────────────────────────────────────────────


@app.post(
    "/api/workflows/auto-bia",
    summary="One-click: minimal input → full BIA report (async)",
    response_description="Task ID for polling the complete auto-BIA workflow.",
    tags=["Workflows", "Auto-populate"],
    responses={
        202: {"description": "Workflow accepted — poll for completion."},
        429: {"description": "Rate limit exceeded."},
        422: {"description": "Request body validation failed."},
    },
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "sepsis_magic_button": {
                            "summary": "Sepsis AI — magic button (minimal input)",
                            "value": {
                                "device_name": "AI Sepsis Prediction Tool",
                                "indication": "Sepsis in ICU patients",
                                "setting": "Acute NHS Trust",
                                "device_cost_per_patient": 185,
                                "expected_benefits": (
                                    "Earlier sepsis recognition, faster antibiotics"
                                ),
                                "include_validation": True,
                                "generate_report": True,
                            },
                        },
                        "copd_remote_monitoring": {
                            "summary": "COPD remote monitoring — ICB",
                            "value": {
                                "device_name": "COPD Remote Monitoring Platform",
                                "indication": "Moderate-to-severe COPD, community management",
                                "setting": "ICB",
                                "device_cost_per_patient": 320,
                                "expected_benefits": (
                                    "Reduced exacerbations, earlier intervention, "
                                    "avoided admissions"
                                ),
                                "forecast_years": 5,
                                "include_validation": True,
                                "generate_report": True,
                            },
                        },
                    }
                }
            }
        }
    },
)
async def auto_bia_workflow_endpoint(
    body: AutoBIAWorkflowRequest,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """The \"magic button\" endpoint: minimal input → full evidence-based BIA report.

    Runs the complete pipeline as a background task:

    | Step | Action |
    |------|--------|
    | 1 | Claude generates targeted PubMed search queries |
    | 2 | PubMed, NICE, NHS costs, ONS population fetched in parallel |
    | 3 | Claude extracts mortality, LOS, cost, readmission data |
    | 4 | Claude synthesises evidence into all BIA input fields |
    | 3b | (Optional) Claude validates plausibility of auto-populated values |
    | 5 | BIA calculation: base case + conservative/optimistic scenarios |
    | 6 | (Optional) PowerPoint report generated |

    Poll ``GET /api/auto-populate/status/{task_id}`` until ``status == "complete"``.

    The response includes:
    - ``bia_inputs`` — the auto-populated form (editable before re-submitting)
    - ``bia_results`` — calculated budget impact for all scenarios
    - ``evidence_summary`` — papers found, NICE guidance, data quality
    - ``confidence_scores`` and ``warnings`` — what to review
    - ``sources`` — full citation list
    - ``report_path`` — path to the PowerPoint file (if generated)
    - ``next_steps`` — links to refine or extend the analysis

    Typical runtime: **60–120 seconds** depending on PubMed response time.
    """
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)

    task_id = _new_task_id()
    with _AUTO_POPULATE_LOCK:
        _AUTO_POPULATE_TASKS[task_id] = {
            "task_id": task_id,
            "type": "auto-bia-workflow",
            "status": "queued",
            "step": "Queued — waiting to start",
            "result": None,
            "error": None,
            "created": datetime.now(timezone.utc).isoformat(),
            "elapsed": None,
            "request": {
                "device_name": body.device_name,
                "indication": body.indication,
                "setting": body.setting,
            },
        }

    background_tasks.add_task(_run_auto_bia_workflow_task, task_id, body)

    return JSONResponse(
        status_code=202,
        content={
            "task_id": task_id,
            "status": "queued",
            "poll_url": f"/api/auto-populate/status/{task_id}",
            "message": (
                "Auto-BIA workflow started (evidence search → extract → calculate → report). "
                "Typical runtime: 60–120 seconds. "
                f"Poll GET /api/auto-populate/status/{task_id} for updates."
            ),
            "steps": [
                "1. Generate search queries",
                "2. Search PubMed + NICE + NHS costs (parallel)",
                "3. Extract clinical data from abstracts",
                "4. Synthesise evidence into BIA inputs",
                "3b. Validate plausibility (if enabled)",
                "5. Run BIA calculation + scenarios",
                "6. Generate PowerPoint report (if enabled)",
            ],
        },
    )


# ── GET /api/auto-populate/status/{task_id} ───────────────────────────────────


@app.get(
    "/api/auto-populate/status/{task_id}",
    summary="Poll auto-populate task status",
    response_model=TaskStatusResponse,
    tags=["Auto-populate"],
    responses={
        200: {"description": "Task found — check status field."},
        404: {"description": "Task ID not found."},
    },
)
def auto_populate_status(task_id: str):
    """Poll the status of a background auto-populate or auto-BIA workflow task.

    **Status lifecycle:**

    ```
    queued → searching → extracting → populating → complete
                                                 ↘ failed
    ```

    | Status | Meaning |
    |--------|---------|
    | ``queued`` | Task accepted, not yet started |
    | ``searching`` | Fetching PubMed / NICE / NHS data |
    | ``extracting`` | Claude extracting clinical data from abstracts |
    | ``populating`` | Claude synthesising into BIA/Markov form fields |
    | ``complete`` | Done — ``result`` field contains the full response |
    | ``failed`` | Error — ``error`` field contains the message |

    Recommended polling interval: **5 seconds**.
    """
    with _AUTO_POPULATE_LOCK:
        task = _AUTO_POPULATE_TASKS.get(task_id)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail=f"Task '{task_id}' not found. Tasks are held in memory and lost on server restart.",
        )

    return TaskStatusResponse(
        task_id=task["task_id"],
        status=task["status"],
        step=task.get("step", ""),
        result=task.get("result"),
        error=task.get("error"),
        created=task["created"],
        elapsed_seconds=task.get("elapsed"),
    )


# ── GET /api/auto-populate/tasks ─────────────────────────────────────────────


@app.get(
    "/api/auto-populate/tasks",
    summary="List recent auto-populate tasks",
    tags=["Auto-populate"],
    responses={200: {"description": "List of tasks, most recent first."}},
)
def list_auto_populate_tasks(
    limit: int = Query(20, ge=1, le=100, description="Maximum number of tasks to return."),
    status: Optional[str] = Query(
        None,
        description="Filter by status: queued, searching, extracting, populating, complete, failed",
    ),
):
    """List recent auto-populate tasks and their current status.

    Useful for monitoring and debugging. Tasks are held in memory and lost
    on server restart.
    """
    with _AUTO_POPULATE_LOCK:
        tasks = list(_AUTO_POPULATE_TASKS.values())

    # Sort newest first
    tasks = sorted(tasks, key=lambda t: t.get("created", ""), reverse=True)

    if status:
        tasks = [t for t in tasks if t.get("status") == status]

    tasks = tasks[:limit]

    return {
        "total": len(_AUTO_POPULATE_TASKS),
        "filtered": len(tasks),
        "tasks": [
            {
                "task_id": t["task_id"],
                "type": t.get("type", ""),
                "status": t["status"],
                "step": t.get("step", ""),
                "created": t["created"],
                "elapsed_seconds": t.get("elapsed"),
                "device_name": t.get("request", {}).get("device_name"),
                "indication": t.get("request", {}).get("indication"),
                "error": t.get("error") if t["status"] == "failed" else None,
            }
            for t in tasks
        ],
    }
