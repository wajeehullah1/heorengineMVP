"""Python-to-R bridge for running Markov model scripts.

Provides helpers to invoke R scripts via ``Rscript``, passing parameters
as JSON and reading structured JSON results from stdout.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Union

from pydantic import ValidationError

from .schema import MarkovInputs, MarkovResults

log = logging.getLogger(__name__)


class RScriptError(Exception):
    """Raised when an R script exits with a non-zero return code."""

    def __init__(self, returncode: int, stderr: str) -> None:
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"Rscript exited with code {returncode}:\n{stderr.strip()}"
        )


def check_r_installed() -> bool:
    """Return *True* if ``Rscript`` is available on the system PATH.

    Prints a human-readable message and logs a warning when R is not found.
    """
    try:
        result = subprocess.run(
            ["Rscript", "--version"],
            capture_output=True,
            text=True,
        )
        # Rscript --version writes to stderr on most platforms
        version = (result.stderr or result.stdout).strip()
        log.info("R is installed: %s", version)
        return True
    except FileNotFoundError:
        msg = (
            "Rscript not found. Install R from https://cran.r-project.org/ "
            "and ensure 'Rscript' is on your PATH."
        )
        log.warning(msg)
        print(msg)
        return False


def run_r_script(script_path: str, params: dict) -> dict:
    """Execute an R script, passing *params* as JSON and returning JSON output.

    Workflow
    -------
    1. Serialise *params* to a temporary ``.json`` file.
    2. Invoke ``Rscript <script_path> <json_filepath>``.
    3. The R script is expected to write a single JSON object to **stdout**.
    4. Parse the JSON and return it as a Python dict.

    Args:
        script_path: Absolute or relative path to an ``.R`` file.
        params: Arbitrary parameter dict to pass to the R script.

    Returns:
        Parsed JSON dict produced by the R script.

    Raises:
        FileNotFoundError: If *script_path* does not exist or ``Rscript``
            is not installed.
        RScriptError: If the R process exits with a non-zero return code.
        json.JSONDecodeError: If R's stdout is not valid JSON.
    """
    script = Path(script_path)
    if not script.exists():
        raise FileNotFoundError(f"R script not found: {script}")

    # Write params to a temp JSON file that R will read
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False,
    ) as tmp:
        json.dump(params, tmp)
        json_filepath = tmp.name

    log.debug("Running Rscript %s with params file %s", script, json_filepath)

    try:
        result = subprocess.run(
            ["Rscript", str(script), json_filepath],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        raise FileNotFoundError(
            "Rscript not found. Install R from https://cran.r-project.org/ "
            "and ensure 'Rscript' is on your PATH."
        )
    finally:
        # Clean up the temp file regardless of outcome
        Path(json_filepath).unlink(missing_ok=True)

    # Log any messages R wrote to stderr (warnings, cat(..., file=stderr()))
    if result.stderr:
        log.debug("R stderr:\n%s", result.stderr.strip())

    if result.returncode != 0:
        raise RScriptError(result.returncode, result.stderr)

    stdout = result.stdout.strip()
    if not stdout:
        raise ValueError(
            "R script produced no output on stdout. "
            "Ensure the script writes JSON via cat(toJSON(...))."
        )

    return json.loads(stdout)


# ── Markov-specific helpers ───────────────────────────────────────────

_MARKOV_SCRIPT = str(Path(__file__).resolve().parent.parent.parent / "r" / "markov_model.R")

_REQUIRED_PARAMS = [
    "prob_death_standard",
    "cost_standard",
    "utility_standard",
    "prob_death_treatment",
    "cost_treatment",
    "utility_treatment",
]

_PROBABILITY_FIELDS = ["prob_death_standard", "prob_death_treatment"]
_NON_NEGATIVE_FIELDS = [
    "cost_standard",
    "utility_standard",
    "cost_treatment",
    "cost_treatment_initial",
    "utility_treatment",
    "time_horizon",
    "cycle_length",
    "discount_rate",
]


def validate_markov_params(params: dict) -> None:
    """Validate Markov model parameters, raising ``ValueError`` on problems.

    Checks:
    - All required fields are present.
    - Probability fields are in [0, 1].
    - Cost, utility, and configuration fields are non-negative.
    """
    missing = [f for f in _REQUIRED_PARAMS if f not in params]
    if missing:
        raise ValueError(f"Missing required parameters: {', '.join(missing)}")

    for field in _PROBABILITY_FIELDS:
        val = params.get(field)
        if val is not None and not (0 <= val <= 1):
            raise ValueError(
                f"{field} must be between 0 and 1, got {val}"
            )

    for field in _NON_NEGATIVE_FIELDS:
        val = params.get(field)
        if val is not None and val < 0:
            raise ValueError(f"{field} must be non-negative, got {val}")


def interpret_icer(icer: float | None, incremental_cost: float, incremental_qalys: float) -> str:
    """Return a plain-English interpretation of the ICER against NICE thresholds.

    Args:
        icer: The ICER value, or *None* when QALYs are equal.
        incremental_cost: Treatment cost minus standard cost.
        incremental_qalys: Treatment QALYs minus standard QALYs.

    Returns:
        Human-readable interpretation string.
    """
    if icer is None or abs(incremental_qalys) < 1e-9:
        return "Equal outcomes — no difference in QALYs between arms"
    if incremental_cost < 0 and incremental_qalys > 0:
        return "Treatment dominates (better outcomes, lower cost)"
    if incremental_cost > 0 and incremental_qalys < 0:
        return "Treatment is dominated (worse outcomes, higher cost)"
    if icer > 50_000:
        return "Highly unlikely to be cost-effective (ICER > £50k/QALY)"
    if icer >= 35_000:
        return "Not cost-effective (above £35k/QALY threshold)"
    if icer >= 25_000:
        return "Potentially cost-effective (£25–35k/QALY)"
    return "Cost-effective (below £25k/QALY threshold)"


def run_markov_model(params: Union[MarkovInputs, dict]) -> Union[MarkovResults, dict]:
    """Run a 2-state Markov cost-effectiveness model via R.

    Accepts either a :class:`MarkovInputs` instance (recommended) or a raw
    dict (legacy).  When *params* is a ``MarkovInputs``, the function returns
    a fully-typed :class:`MarkovResults`; when a dict is passed, the raw R
    output dict is returned for backward compatibility.

    MarkovInputs path
    -----------------
    The ``MarkovInputs`` model is validated by Pydantic, converted to the R
    parameter format via :meth:`MarkovInputs.to_r_params`, and the R output
    is parsed into a ``MarkovResults`` with NICE threshold flags and a
    detailed interpretation.

    Dict path (legacy)
    ------------------
    The dict is validated with :func:`validate_markov_params`, defaults are
    filled in, and the raw JSON dict from R is returned.

    Required dict parameters:
        prob_death_standard  – Annual mortality probability, standard arm (0–1).
        cost_standard        – Annual cost while alive, standard arm (£).
        utility_standard     – Annual utility (QoL weight 0–1), standard arm.
        prob_death_treatment – Annual mortality probability, treatment arm (0–1).
        cost_treatment       – Annual cost while alive, treatment arm (£).
        utility_treatment    – Annual utility (QoL weight 0–1), treatment arm.

    Optional dict parameters (defaults applied if absent):
        time_horizon          – Number of years to simulate (default 5).
        cycle_length          – Fraction of a year per cycle (default 1 = annual).
        discount_rate         – Annual discount rate for costs & QALYs (default 0.035).
        cost_treatment_initial – One-time upfront treatment cost (default 0).

    Returns:
        ``MarkovResults`` when called with ``MarkovInputs``, otherwise a raw dict.

    Raises:
        ValueError: If required parameters are missing or out of range.
        FileNotFoundError: If R or the Markov script is not found.
        RScriptError: If the R process fails.

    Example (schema path)::

        >>> from engines.markov.schema import MarkovInputs
        >>> from engines.markov.runner import run_markov_model
        >>> inputs = MarkovInputs(
        ...     intervention_name="Drug X",
        ...     prob_death_standard=0.05, cost_standard_annual=5000,
        ...     utility_standard=0.7, prob_death_treatment=0.03,
        ...     cost_treatment_annual=8000, utility_treatment=0.85,
        ... )
        >>> results = run_markov_model(inputs)
        >>> print(results.get_summary())

    Example (legacy dict path)::

        >>> result = run_markov_model({
        ...     "prob_death_standard": 0.05, "cost_standard": 5000,
        ...     "utility_standard": 0.7, "prob_death_treatment": 0.03,
        ...     "cost_treatment": 8000, "utility_treatment": 0.85,
        ... })
        >>> result["interpretation"]
        'Not cost-effective'
    """
    if isinstance(params, MarkovInputs):
        log.info("Running Markov model for '%s'", params.intervention_name)
        r_params = params.to_r_params()
        raw = run_r_script(_MARKOV_SCRIPT, r_params)

        # Build MarkovResults with Python-side interpretation
        inc = raw["incremental"]
        icer_raw = inc["icer"]
        icer = None if icer_raw == "NA" else float(icer_raw)

        interpretation = interpret_icer(icer, inc["cost"], inc["qalys"])
        log.info("ICER: %s — %s", icer, interpretation)

        dominant = inc["cost"] < 0 and inc["qalys"] > 0
        cost_effective_25k = dominant or (icer is not None and icer < 25_000)
        cost_effective_35k = dominant or (icer is not None and icer < 35_000)

        return MarkovResults(
            standard_care=raw["standard_care"],
            treatment=raw["treatment"],
            incremental_cost=inc["cost"],
            incremental_qalys=inc["qalys"],
            icer=icer,
            interpretation=interpretation,
            cost_effective_25k=cost_effective_25k,
            cost_effective_35k=cost_effective_35k,
        )

    # Legacy dict path
    validate_markov_params(params)

    full_params = {
        "time_horizon": 5,
        "cycle_length": 1,
        "discount_rate": 0.035,
        "cost_treatment_initial": 0,
    }
    full_params.update(params)

    return run_r_script(_MARKOV_SCRIPT, full_params)


def run_markov_with_validation(input_dict: dict) -> MarkovResults:
    """Validate a raw dict, run the Markov model, and return typed results.

    This is the recommended entry point for API endpoints and external
    callers that receive unvalidated user input as a dictionary.

    Workflow:
        1. Validate *input_dict* against :class:`MarkovInputs` (Pydantic).
        2. Call :func:`run_markov_model` with the validated inputs.
        3. Return a :class:`MarkovResults` with interpretation and threshold flags.

    Args:
        input_dict: Raw parameter dict — must contain at minimum
            ``intervention_name``, ``prob_death_standard``, ``cost_standard_annual``,
            ``utility_standard``, ``prob_death_treatment``, ``cost_treatment_annual``,
            and ``utility_treatment``.  Optional keys (``time_horizon``,
            ``cycle_length``, ``discount_rate``, ``cost_treatment_initial``)
            will use Pydantic defaults if absent.

    Returns:
        A fully-populated :class:`MarkovResults` instance.

    Raises:
        ValueError: If *input_dict* fails Pydantic validation.  The message
            lists every field error so callers can display them to users.
        FileNotFoundError: If R is not installed.
        RScriptError: If the R script fails.

    Example::

        >>> from engines.markov.runner import run_markov_with_validation
        >>> results = run_markov_with_validation({
        ...     "intervention_name": "Drug X",
        ...     "prob_death_standard": 0.05,
        ...     "cost_standard_annual": 5000,
        ...     "utility_standard": 0.7,
        ...     "prob_death_treatment": 0.03,
        ...     "cost_treatment_annual": 8000,
        ...     "utility_treatment": 0.85,
        ... })
        >>> print(results.get_summary())
    """
    try:
        inputs = MarkovInputs(**input_dict)
    except ValidationError as e:
        errors = "; ".join(
            f"{err['loc'][-1]}: {err['msg']}" for err in e.errors()
        )
        log.warning("Markov input validation failed: %s", errors)
        raise ValueError(f"Invalid Markov parameters — {errors}") from e

    log.info("Validated Markov inputs for '%s'", inputs.intervention_name)
    return run_markov_model(inputs)


def calculate_icer(
    standard_cost: float,
    standard_qalys: float,
    treatment_cost: float,
    treatment_qalys: float,
) -> float:
    """Calculate the Incremental Cost-Effectiveness Ratio (ICER).

    ICER = (treatment_cost − standard_cost) / (treatment_qalys − standard_qalys)

    Args:
        standard_cost:  Total cost of the standard care arm.
        standard_qalys: Total QALYs of the standard care arm.
        treatment_cost: Total cost of the treatment arm.
        treatment_qalys: Total QALYs of the treatment arm.

    Returns:
        The ICER as a float (£ per QALY gained).

    Raises:
        ZeroDivisionError: If there is no QALY difference between arms.
    """
    incremental_qalys = treatment_qalys - standard_qalys
    if abs(incremental_qalys) < 1e-9:
        raise ZeroDivisionError(
            "Cannot calculate ICER: no difference in QALYs between arms."
        )
    return (treatment_cost - standard_cost) / incremental_qalys
