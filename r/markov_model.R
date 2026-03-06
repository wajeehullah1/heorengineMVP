# markov_model.R — Simple 2-state Markov cost-effectiveness model.
#
# States:  Alive  ──(p_death)──►  Dead
#
# A cohort of 1.0 starts in "Alive".  Each cycle (year), a fraction
# transitions to "Dead" based on the arm-specific mortality probability.
# Costs and QALYs accrue only while alive and are discounted at a
# constant annual rate.
#
# The model runs two arms (standard care vs. treatment), calculates
# incremental cost and QALYs, and derives the ICER.
#
# Usage:  Rscript r/markov_model.R <path_to_json_params>
#
# Expected JSON keys:
#   time_horizon, cycle_length, discount_rate,
#   prob_death_standard, cost_standard, utility_standard,
#   prob_death_treatment, cost_treatment, cost_treatment_initial,
#   utility_treatment

library(jsonlite)

# ── 1. Read command-line arguments ────────────────────────────────────
args <- commandArgs(trailingOnly = TRUE)

if (length(args) < 1) {
  cat("Error: no JSON file path provided\n", file = stderr())
  quit(status = 1)
}

json_file <- args[1]

if (!file.exists(json_file)) {
  cat(paste0("Error: file not found: ", json_file, "\n"), file = stderr())
  quit(status = 1)
}

tryCatch({
  params <- fromJSON(json_file)
}, error = function(e) {
  cat(paste0("Error: invalid JSON — ", e$message, "\n"), file = stderr())
  quit(status = 1)
})

# ── 2. Extract parameters with defaults ───────────────────────────────
time_horizon <- if (!is.null(params$time_horizon)) params$time_horizon else 5
cycle_length <- if (!is.null(params$cycle_length)) params$cycle_length else 1
discount_rate <- if (!is.null(params$discount_rate)) params$discount_rate else 0.035

# Standard care arm
prob_death_std <- params$prob_death_standard
cost_std       <- params$cost_standard
utility_std    <- params$utility_standard

# Treatment arm
prob_death_trt    <- params$prob_death_treatment
cost_trt          <- params$cost_treatment
cost_trt_initial  <- if (!is.null(params$cost_treatment_initial)) params$cost_treatment_initial else 0
utility_trt       <- params$utility_treatment

# Validate required parameters
required <- c("prob_death_standard", "cost_standard", "utility_standard",
              "prob_death_treatment", "cost_treatment", "utility_treatment")
missing  <- required[!required %in% names(params)]

if (length(missing) > 0) {
  cat(paste0("Error: missing required parameters: ",
             paste(missing, collapse = ", "), "\n"), file = stderr())
  quit(status = 1)
}

# ── 3. Markov simulation ─────────────────────────────────────────────
#
# For each cycle t = 0 … (time_horizon - 1):
#
#   alive[t+1] = alive[t] * (1 - p_death)
#
#   discount_factor[t] = 1 / (1 + discount_rate)^t
#
#   Costs and QALYs accrue at the START of each cycle (beginning-of-cycle
#   convention), weighted by the proportion alive and the discount factor.
#
# Number of cycles depends on cycle_length:
#   n_cycles = time_horizon / cycle_length
# Costs and utilities are scaled by cycle_length so that annual values
# are spread across sub-annual cycles when cycle_length < 1.
# ─────────────────────────────────────────────────────────────────────

n_cycles <- as.integer(time_horizon / cycle_length)

run_arm <- function(p_death, annual_cost, annual_utility, initial_cost = 0) {
  # Per-cycle transition probability (adjusted for cycle length).
  # For annual cycles (cycle_length = 1) this equals p_death directly.
  # For shorter cycles we convert:  p_cycle = 1 - (1 - p_annual)^cycle_length
  p_cycle <- 1 - (1 - p_death)^cycle_length

  alive       <- numeric(n_cycles + 1)
  alive[1]    <- 1.0            # full cohort starts alive

  total_cost  <- 0.0
  total_qalys <- 0.0

  # Add the one-time initial cost at t=0 (undiscounted — incurred immediately)
  total_cost <- total_cost + initial_cost

  for (t in 1:n_cycles) {
    # Discount factor for this cycle.
    # Calendar time in years = (t - 1) * cycle_length
    year_t          <- (t - 1) * cycle_length
    discount_factor <- 1 / (1 + discount_rate)^year_t

    # Costs and QALYs accruing this cycle (scaled by cycle length)
    cycle_cost  <- annual_cost    * cycle_length * alive[t] * discount_factor
    cycle_qalys <- annual_utility * cycle_length * alive[t] * discount_factor

    total_cost  <- total_cost  + cycle_cost
    total_qalys <- total_qalys + cycle_qalys

    # Transition: some of the alive cohort dies this cycle
    alive[t + 1] <- alive[t] * (1 - p_cycle)
  }

  list(total_cost = total_cost, total_qalys = total_qalys)
}

std_results <- run_arm(prob_death_std, cost_std, utility_std)
trt_results <- run_arm(prob_death_trt, cost_trt, utility_trt, cost_trt_initial)

# ── 4. Incremental analysis ──────────────────────────────────────────
incremental_cost  <- trt_results$total_cost  - std_results$total_cost
incremental_qalys <- trt_results$total_qalys - std_results$total_qalys

# ICER = incremental cost / incremental QALYs
# Guard against division by zero (identical effectiveness)
if (abs(incremental_qalys) < 1e-9) {
  icer <- NA
} else {
  icer <- incremental_cost / incremental_qalys
}

# ── 5. Interpretation against NICE willingness-to-pay thresholds ─────
#   < £20,000/QALY  → cost-effective
#   £20,000–£30,000  → potentially cost-effective
#   > £30,000         → not cost-effective
# Special cases: treatment dominates (cheaper & more effective) or is dominated.
if (is.na(icer)) {
  interpretation <- "No QALY difference — cannot calculate ICER"
} else if (incremental_cost < 0 && incremental_qalys > 0) {
  interpretation <- "Dominant (less costly, more effective)"
} else if (incremental_cost > 0 && incremental_qalys < 0) {
  interpretation <- "Dominated (more costly, less effective)"
} else if (icer < 20000) {
  interpretation <- "Cost-effective"
} else if (icer < 30000) {
  interpretation <- "Potentially cost-effective"
} else {
  interpretation <- "Not cost-effective"
}

# ── 6. Output ─────────────────────────────────────────────────────────
results <- list(
  standard_care = list(
    total_cost  = round(std_results$total_cost, 2),
    total_qalys = round(std_results$total_qalys, 4)
  ),
  treatment = list(
    total_cost  = round(trt_results$total_cost, 2),
    total_qalys = round(trt_results$total_qalys, 4)
  ),
  incremental = list(
    cost  = round(incremental_cost, 2),
    qalys = round(incremental_qalys, 4),
    icer  = if (is.na(icer)) "NA" else round(icer, 2)
  ),
  parameters = list(
    time_horizon  = time_horizon,
    cycle_length  = cycle_length,
    discount_rate = discount_rate,
    n_cycles      = n_cycles
  ),
  interpretation = interpretation
)

cat(toJSON(results, auto_unbox = TRUE))
