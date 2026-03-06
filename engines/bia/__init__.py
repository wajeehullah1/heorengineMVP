"""Budget Impact Analysis engine."""

from .schema import BIAInputs, BIAResults, WorkforceRow, ScenarioResult
from .model import calculate_budget_impact, calculate_scenarios, create_scenario_variant
from .validation import validate_clinical_sense, suggest_missing_inputs, estimate_confidence
