# HEOR Engine — Markov Cost-Effectiveness Methodology

Version 0.1 | February 2026

This document describes the 2-state Markov cost-effectiveness model used
by the HEOR Engine. It complements the BIA calculation methodology and is
intended for review by HEOR analysts and for inclusion in NICE/ISPOR-aligned
submission dossiers.

---

## 1. Model Overview

The engine implements a **cohort-level, discrete-time Markov model** with
two health states:

```
                  p_death
    ┌───────┐  ──────────►  ┌──────┐
    │ Alive │               │ Dead │
    └───────┘               └──────┘
        │                       │
        └──── 1 - p_death ──────┘
              (stays alive)     (absorbing state)
```

- **Alive**: The patient incurs costs and accrues quality-adjusted life years
  (QALYs) each cycle.
- **Dead**: An absorbing state. Once a patient transitions to Dead, they
  incur no further costs or QALYs.

The model runs **two independent arms** — standard care and treatment — using
the same structure but different parameter values, then compares them.

### Why Two States?

A 2-state model is the simplest Markov structure that captures the trade-off
between survival benefit and cost. It is appropriate when:

- The primary clinical effect is on **mortality** (not disease progression).
- The intervention affects **quality of life** as a constant utility weight.
- More granular health states (e.g. progression-free, progressed) are not
  needed or the data to parameterise them is unavailable.

For NICE technology appraisals of medical devices and diagnostics, a 2-state
model is a common starting point that can be extended later.

---

## 2. Parameters

### Model Configuration

| Parameter | Default | Range | Description |
|---|---|---|---|
| `time_horizon` | 5 years | 1–50 | Duration of the simulation |
| `cycle_length` | 1.0 | 0 < x ≤ 1 | Cycle length as fraction of a year (1 = annual, 0.25 = quarterly) |
| `discount_rate` | 0.035 | 0–1 | Annual discount rate for costs and QALYs |

### Standard Care Arm

| Parameter | Description |
|---|---|
| `prob_death_standard` | Annual probability of death (0–1) |
| `cost_standard_annual` | Annual cost while alive (£) |
| `utility_standard` | Quality-of-life weight (0–1, where 1 = perfect health) |

### Treatment Arm

| Parameter | Description |
|---|---|
| `prob_death_treatment` | Annual probability of death (0–1) |
| `cost_treatment_annual` | Annual cost while alive (£) |
| `cost_treatment_initial` | One-time upfront cost at cycle 0 (£, default 0) |
| `utility_treatment` | Quality-of-life weight (0–1) |

---

## 3. Simulation Algorithm

### 3.1 Cycle Length Adjustment

When the cycle length is sub-annual (e.g. quarterly), the annual mortality
probability is converted to a per-cycle probability:

```
p_cycle = 1 − (1 − p_annual) ^ cycle_length
```

For annual cycles (`cycle_length = 1`), this reduces to `p_cycle = p_annual`.

The number of cycles is:

```
n_cycles = time_horizon / cycle_length
```

### 3.2 Cohort Simulation

A normalised cohort of size **1.0** starts in the Alive state at cycle 0.

For each cycle `t = 1, 2, …, n_cycles`:

1. **Calendar time**: `year_t = (t − 1) × cycle_length`

2. **Discount factor**: `df_t = 1 / (1 + discount_rate) ^ year_t`

3. **Costs this cycle** (beginning-of-cycle convention):
   ```
   cycle_cost = annual_cost × cycle_length × alive[t] × df_t
   ```

4. **QALYs this cycle**:
   ```
   cycle_qalys = utility × cycle_length × alive[t] × df_t
   ```

5. **State transition**:
   ```
   alive[t + 1] = alive[t] × (1 − p_cycle)
   ```

The **initial cost** (e.g. device purchase, surgery) is added at `t = 0`
without discounting, as it is incurred immediately.

### 3.3 Totals

```
total_cost  = cost_treatment_initial + Σ cycle_cost[t]   for t = 1 … n_cycles
total_qalys = Σ cycle_qalys[t]                           for t = 1 … n_cycles
```

---

## 4. Discount Factor Calculations

The HEOR Engine uses **constant-rate exponential discounting** as recommended
by NICE (Reference Case, 2022):

```
discount_factor(t) = 1 / (1 + r) ^ t
```

Where:
- `r` = annual discount rate (default **3.5%** per NICE guidelines)
- `t` = calendar time in years from model start

### Why Discount?

Discounting reflects the economic principle of **time preference** — a pound
today is worth more than a pound in the future. NICE requires both costs and
health outcomes (QALYs) to be discounted at the same rate of 3.5% per annum.

### Example

For a 5-year model with annual cycles and 3.5% discount rate:

| Year | Calendar Time | Discount Factor |
|---|---|---|
| 1 | 0 | 1.000 |
| 2 | 1 | 0.966 |
| 3 | 2 | 0.934 |
| 4 | 3 | 0.902 |
| 5 | 4 | 0.871 |

A cost of £10,000 in Year 5 has a present value of £10,000 × 0.871 = £8,714.

---

## 5. Incremental Analysis & ICER

### Incremental Cost-Effectiveness Ratio

The ICER is the primary decision metric:

```
incremental_cost  = total_cost_treatment − total_cost_standard
incremental_qalys = total_qalys_treatment − total_qalys_standard

ICER = incremental_cost / incremental_qalys
```

The ICER represents the **additional cost per additional QALY gained** by
switching from standard care to the treatment. A lower ICER indicates better
value for money.

### Special Cases

| Scenario | Incremental Cost | Incremental QALYs | ICER | Decision |
|---|---|---|---|---|
| **Treatment dominates** | < 0 (cheaper) | > 0 (more effective) | Negative | Adopt — better outcomes at lower cost |
| **Treatment dominated** | > 0 (more expensive) | < 0 (less effective) | Negative | Reject — worse outcomes at higher cost |
| **Equal outcomes** | Any | ≈ 0 | Undefined (N/A) | Compare on cost alone |
| **Trade-off (NW quadrant)** | > 0 | > 0 | Positive | Compare ICER to WTP threshold |

When incremental QALYs are near zero (|ΔQALYs| < 10⁻⁹), the ICER is
reported as **N/A** to avoid division by near-zero values.

---

## 6. ICER Interpretation & NICE Thresholds

### NICE Willingness-to-Pay (WTP) Thresholds

NICE uses cost-per-QALY thresholds to determine whether a technology
represents acceptable value for money to the NHS:

| Threshold | £/QALY | Context |
|---|---|---|
| **Standard** | £20,000–£25,000 | Default range for most technologies |
| **Extended** | £25,000–£35,000 | May be accepted with additional factors (innovation, unmet need) |
| **End-of-life** | Up to £50,000 | For treatments meeting end-of-life criteria (life expectancy < 24 months, extends life by ≥ 3 months, small patient population) |

### Interpretation Logic

The HEOR Engine uses the following decision rules (Python-side, after R
returns raw results):

| ICER Range | Interpretation | NICE Assessment |
|---|---|---|
| Treatment dominates | "Treatment dominates (better outcomes, lower cost)" | Strong case for adoption |
| < £25,000/QALY | "Cost-effective (below £25k/QALY threshold)" | Likely to be recommended |
| £25,000–£35,000/QALY | "Potentially cost-effective (£25–35k/QALY)" | May be recommended with additional evidence |
| £35,000–£50,000/QALY | "Not cost-effective (above £35k/QALY threshold)" | Unlikely unless end-of-life criteria met |
| > £50,000/QALY | "Highly unlikely to be cost-effective (ICER > £50k/QALY)" | Very unlikely to be recommended |
| Treatment dominated | "Treatment is dominated (worse outcomes, higher cost)" | Should not be adopted |

### Boolean Flags

The engine provides two boolean flags for quick decision support:

- `cost_effective_25k`: True if ICER < £25,000 or treatment dominates
- `cost_effective_35k`: True if ICER < £35,000 or treatment dominates

---

## 7. BIA → CEA Bridge

The engine can **derive Markov model parameters from an existing BIA
submission**, allowing users to run a cost-effectiveness analysis without
re-entering all data.

### Parameter Mapping

| Markov Parameter | Derived From | Formula |
|---|---|---|
| `cost_treatment_annual` | BIA `cost_per_patient[0]` | Year 1 cost per patient from BIA base case |
| `cost_standard_annual` | BIA workforce cost | `calculate_workforce_cost(workforce)` |
| `cost_treatment_initial` | BIA `setup_cost + price` | One-time costs from BIA |
| `prob_death_treatment` | User-supplied `mortality_reduction` | `base_mortality × (1 − mortality_reduction / 100)` |
| `utility_treatment` | User-supplied `utility_gain` | `min(base_utility + utility_gain, 1.0)` |

### Additional User Inputs Required

The BIA does not capture clinical effectiveness data, so the user must
provide:

- **`mortality_reduction`** (0–100%): Percentage reduction in annual
  mortality attributable to the treatment.
- **`utility_gain`** (0–1): Absolute improvement in quality-of-life weight.
- **`base_mortality`** (default 0.08): Annual mortality under standard care.
- **`base_utility`** (default 0.70): Baseline quality-of-life weight.

### When to Use the Bridge

Use `POST /api/calculate-icer-from-bia` when:

- A BIA submission already exists and you want to extend the analysis.
- Cost data is already captured in the BIA and should not be re-entered.
- You have clinical trial or literature data for mortality/utility effects.

---

## 8. When to Use BIA vs CEA vs Both

### Budget Impact Analysis (BIA)

**Purpose**: Estimate the **financial impact** of adopting a technology on
the NHS budget over a short time horizon (typically 1–5 years).

**Perspective**: NHS payer (trust or commissioner).

**Use when**:
- The decision-maker needs to know "Can we afford this?"
- The focus is on **affordability** and budget planning.
- Required for NICE Medical Technologies Guidance (MTG) and Health
  Technology Evaluations (HTE).

**Does NOT answer**: Whether the technology is good value for money relative
to health outcomes.

### Cost-Effectiveness Analysis (CEA)

**Purpose**: Determine whether a technology provides **value for money** by
comparing its costs and health outcomes (QALYs) to an alternative.

**Perspective**: NHS and personal social services (PSS).

**Use when**:
- The decision-maker needs to know "Is this worth the money?"
- The technology has a measurable effect on **mortality or quality of life**.
- Required for NICE Technology Appraisals (TA) and Highly Specialised
  Technologies (HST).

**Does NOT answer**: Whether the NHS can afford it in absolute terms.

### Combined BIA + CEA

**Purpose**: Provide a **complete economic case** — both affordability and
value for money — in a single analysis.

**Use when**:
- Preparing a comprehensive NICE submission or trust business case.
- The technology is both cost-saving (BIA) and clinically effective (CEA).
- Stakeholders need the full picture for investment decisions.

The HEOR Engine generates a **combined 16-slide PowerPoint report** with:
- 10 BIA slides (population, budget impact, scenarios, assumptions)
- 6 CEA slides (model structure, inputs, results, CE plane, interpretation)

### Decision Matrix

| Question | Analysis | Endpoint |
|---|---|---|
| "What will this cost the trust?" | BIA only | `POST /api/calculate-bia` |
| "Is this treatment value for money?" | CEA only | `POST /api/calculate-icer` |
| "Can we afford it AND is it worth it?" | BIA + CEA | `POST /api/generate-combined-report` |
| "Extend my BIA with effectiveness data" | BIA → CEA bridge | `POST /api/calculate-icer-from-bia` |

---

## 9. Assumptions & Limitations

### Assumptions

1. **Constant transition probabilities**: Mortality risk does not change over
   time. In reality, mortality may increase with age or disease progression.
   A time-dependent model would require age-stratified life tables.

2. **Two health states only**: The model does not capture intermediate states
   (e.g. disease progression, remission, adverse events). This is appropriate
   for interventions with a binary survival effect but may underestimate
   value for treatments that also reduce morbidity.

3. **Homogeneous cohort**: All patients have the same risk profile. No
   subgroup analysis by age, sex, comorbidity, or disease severity.

4. **Beginning-of-cycle convention**: Costs and QALYs accrue at the start of
   each cycle. This slightly overestimates both compared to a mid-cycle or
   end-of-cycle convention. For annual cycles the difference is small.

5. **Constant utility weights**: Quality of life does not change over time
   within an arm. In practice, utility may decline as patients age or
   disease progresses.

6. **No adverse events**: Treatment-related side effects and their costs are
   not explicitly modelled. They should be incorporated into the annual
   treatment cost if significant.

7. **Equal discount rate for costs and outcomes**: Both are discounted at
   3.5% per NICE guidelines. Some jurisdictions use differential rates
   (e.g. 1.5% for outcomes in the Netherlands).

8. **One-way sensitivity implicit**: Scenario analysis via the BIA engine
   (conservative/base/optimistic) provides some parameter variation, but
   formal probabilistic sensitivity analysis (PSA) with Monte Carlo
   simulation is not yet implemented.

### Limitations

- **No probabilistic sensitivity analysis (PSA)**: Parameter uncertainty is
  not characterised with distributions. Results are point estimates only.
  Future versions will support PSA with second-order Monte Carlo simulation.

- **No value-of-information analysis**: The model does not quantify the
  expected value of perfect or partial information (EVPI/EVPPI).

- **No half-cycle correction**: The beginning-of-cycle convention is used
  throughout. A half-cycle correction would improve accuracy for longer
  cycle lengths.

- **No mortality by age**: The model uses a single constant mortality
  probability rather than age-specific life tables. This may over- or
  under-estimate survival depending on the patient population.

- **R dependency**: The Markov simulation runs in R via `Rscript`. This
  requires R to be installed on the server. If R is unavailable, the CEA
  endpoints return a 503 error.

- **No structural uncertainty**: Only one model structure (2-state) is
  available. Structural sensitivity analysis (e.g. comparing 2-state vs
  3-state models) is not supported.

---

## 10. Worked Example

**Intervention**: AI-powered wound assessment camera

**Parameters**:
- Time horizon: 5 years, annual cycles, 3.5% discount rate
- Standard care: 8% annual mortality, £8,000/year, utility 0.65
- Treatment: 4% annual mortality, £9,500/year, £2,000 upfront, utility 0.78

### Simulation Results

| Metric | Standard Care | Treatment |
|---|---|---|
| Total discounted cost | £32,045 | £43,097 |
| Total discounted QALYs | 2.60 | 3.37 |

### Incremental Analysis

```
Incremental cost  = £43,097 − £32,045 = £11,052
Incremental QALYs = 3.37 − 2.60 = 0.77

ICER = £11,052 / 0.77 = £14,342/QALY
```

### Interpretation

- ICER of **£14,342/QALY** is below the NICE standard threshold of £25,000.
- **Cost-effective**: The treatment provides acceptable value for money.
- `cost_effective_25k = true`, `cost_effective_35k = true`

### Combined with BIA

When paired with a cost-saving BIA (3-year total: −£26M), the recommendation
is:

> **Strong case for adoption** — cost-saving BIA and cost-effective CEA.

---

## References

1. NICE (2022). *NICE health technology evaluations: the manual*. Process
   and methods [PMG36]. National Institute for Health and Care Excellence.

2. NICE (2017). *Developing NICE guidelines: the manual*. Section 7:
   Incorporating economic evaluation. PMG20.

3. Briggs A, Claxton K, Sculpher M (2006). *Decision Modelling for Health
   Economic Evaluation*. Oxford University Press.

4. Drummond MF, Sculpher MJ, Claxton K, Stoddart GL, Torrance GW (2015).
   *Methods for the Economic Evaluation of Health Care Programmes*. 4th ed.
   Oxford University Press.

5. Sullivan SD, Mauskopf JA, Augustovski F, et al. (2014). Budget impact
   analysis — principles of good practice: report of the ISPOR 2012 Budget
   Impact Analysis Good Practice II Task Force. *Value in Health*,
   17(1):5-14.
