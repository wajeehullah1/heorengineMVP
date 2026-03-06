"""Markov model engine — cost-effectiveness and state-transition modelling."""

from .runner import (
    RScriptError,
    calculate_icer,
    check_r_installed,
    interpret_icer,
    run_markov_model,
    run_markov_with_validation,
    run_r_script,
    validate_markov_params,
)
from .schema import ArmResult, MarkovInputs, MarkovResults
