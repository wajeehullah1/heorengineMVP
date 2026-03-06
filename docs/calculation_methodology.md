# HEOR Engine — BIA Calculation Methodology

Version 0.1 | February 2026

This document describes the step-by-step methodology used by the Budget
Impact Analysis (BIA) engine. It is intended for review by HEOR experts
and for inclusion in NICE/ISPOR-aligned submission dossiers.

---

## 1. Population & Uptake

### Eligible cohort

```
eligible_patients = catchment_size × (eligible_pct / 100)
```

`catchment_size` is either the trust population served or the number of
beds, depending on the user's selection.

### Treated patients per year

```
treated_yN = eligible_patients × (uptake_yN / 100)
```

Uptake is entered independently for each year (Y1, Y2, Y3), allowing
non-linear adoption curves (e.g. slow pilot → ramp-up → plateau).

---

## 2. Current Pathway Cost per Patient

The current pathway cost is the sum of six components. All costs are
per patient per year unless noted otherwise.

| Component | Formula | Source |
|---|---|---|
| **Staff time** | `Σ (hourly_rate × minutes / 60)` for each workforce row | AfC band rates (PSSRU 2024-25) |
| **Outpatient visits** | `£120 (first) + (n − 1) × £85 (follow-up)` | NHS Reference Costs |
| **Bed days** | `admissions × bed_days × £400` | NHS Reference Costs (general ward) |
| **Tests** | `n_tests × £85` | Proxied at follow-up outpatient tariff |
| **Procedures** | `n_procedures × £1,200` | Theatre-hour national average |
| **Consumables** | Entered directly (£/patient) | User input |

```
current_pathway_cost = staff_time + outpatient + bed_days + tests + procedures + consumables
```

### AfC Band Rates Used

| Role | £/hour |
|---|---|
| Band 2 | 12.45 |
| Band 3 | 14.28 |
| Band 4 | 16.83 |
| Band 5 (Staff Nurse) | 21.37 |
| Band 6 (Senior Nurse/AHP) | 26.54 |
| Band 7 (Advanced Practitioner) | 32.11 |
| Band 8a (Consultant Nurse/Manager) | 40.22 |
| Registrar | 38.50 |
| Consultant | 72.00 |
| Admin/Clerical | 11.90 |

### NHS Reference Costs Used

| Item | £ |
|---|---|
| General ward bed day | 400.00 |
| ICU bed day | 1,800.00 |
| Outpatient first attendance | 120.00 |
| Outpatient follow-up | 85.00 |
| Emergency department attendance | 180.00 |
| Theatre hour | 1,200.00 |

---

## 3. New Pathway Cost per Patient

The new pathway cost starts with the device price and subtracts savings
achieved by the intervention.

### Device cost

```
device_cost = price   (per patient per year, as entered)
```

### Savings

| Saving | Formula |
|---|---|
| **Staff time saved** | `(staff_time_saved_mins / 60) × average_hourly_rate` |
| **Visit reduction** | `outpatient_cost × (visits_reduced / 100)` |
| **Bed-day reduction** | `admissions × los_reduced_days × £400` |
| **Complication reduction** | `current_pathway_cost × 0.10 × (complications_reduced / 100)` |
| **Readmission reduction** | `bed_day_cost × (readmissions_reduced / 100)` |
| **Follow-up reduction** | `outpatient_cost × (follow_up_reduced / 100)` |

The **average hourly rate** is the mean across all ten AfC band rates
(£28.62). This is used when the specific role mix of saved time is not
known.

```
total_savings = staff_saving + visit_saving + bed_day_saving
              + complication_saving + readmission_saving + follow_up_saving

new_pathway_cost = device_cost − total_savings
```

---

## 4. Net Budget Impact

### Annual impact

```
net_impact_yN = (new_pathway_cost − current_pathway_cost) × treated_yN
```

A **negative** net impact means the intervention **saves money**
(new pathway cheaper than current). A **positive** net impact means
additional cost to the trust.

### Year 1 one-off costs

Year 1 includes additional one-off costs:

```
year_1_impact = net_impact_y1 + setup_cost + training_cost
```

Where:

```
training_cost = training_hours × average_hourly_rate × n_staff
```

`n_staff` defaults to **10** (a conservative assumption for an acute
trust ward) when no explicit headcount is provided.

### Discounting (optional)

When enabled, future costs are discounted at the NICE reference rate:

```
discounted_cost = cost / (1 + 0.035) ^ (year − 1)
```

Year 1 is never discounted. This follows NICE's standard 3.5% annual
discount rate for costs. Discounting is **off by default** for BIA
(recommended by NICE for short-horizon budget impact analyses) and can
be toggled on by the user.

---

## 5. Break-Even Year

The break-even year is the **first year** in which the cumulative net
budget impact becomes non-positive (i.e. cumulative savings exceed
cumulative costs):

```
cumulative = 0
for year in [1, 2, 3]:
    cumulative += net_impact[year]
    if cumulative ≤ 0:
        break_even = year
        break
```

If the cumulative impact never reaches zero or below within the forecast
horizon, `break_even_year` is reported as `null`.

---

## 6. Cost Driver Ranking

The top 3 cost drivers are identified by comparing the absolute
magnitude of each cost component:

- Current pathway: Staff time, Outpatient visits, Bed days, Tests,
  Procedures, Consumables
- New pathway: Device acquisition

Components are ranked by absolute £ value (descending) and the top 3
are returned. This helps decision-makers understand which cost
categories dominate the analysis.

---

## 7. Scenario Analysis

Three scenarios are run by modifying the base-case inputs:

| Parameter | Conservative | Base | Optimistic |
|---|---|---|---|
| Uptake (Y1/Y2/Y3) | × 0.80 (−20%) | As entered | × 1.20 (+20%) |
| Device price | × 1.15 (+15%) | As entered | × 0.90 (−10%) |
| All savings | × 0.70 (−30%) | As entered | × 1.20 (+20%) |

### Rationale

- **Conservative**: Slower adoption, higher-than-expected procurement
  cost, and smaller realised efficiency gains. Represents risk to the
  business case.
- **Base**: Uses client-supplied inputs without adjustment.
- **Optimistic**: Faster adoption (e.g. strong clinical champion),
  negotiated volume discount on the device, and better-than-expected
  outcomes based on trial data.

All uptake and percentage-based savings values are **clamped to
0–100%** after scaling.

Each scenario runs the full BIA pipeline independently, producing its
own break-even year, cost drivers, and annual impacts.

---

## 8. Clinical-Sense Validation

Before running the calculation, the engine checks for values that may
indicate data-entry errors. These produce **warnings** (not hard
errors):

| Check | Trigger |
|---|---|
| Uptake decreasing | Y2 < Y1 or Y3 < Y2 |
| Savings too high | Any reduction percentage > 80% |
| Price outlier | Price < £10 or > £100,000 |
| Cohort exceeds catchment | `eligible_patients > catchment_size` |
| Saving more than exists | `staff_time_saved > total_workforce_minutes` |

---

## 9. Confidence Rating

Data completeness is scored across 8 criteria:

| Criterion | Points |
|---|---|
| Workforce data present | +1 |
| Outpatient visits > 0 | +1 |
| Admissions + bed days > 0 | +1 |
| Tests > 0 | +1 |
| Procedures > 0 | +1 |
| Consumables > 0 | +1 |
| At least one savings field > 0 | +1 |
| Prevalence notes provided | +1 |

| Score | Rating |
|---|---|
| 6–8 | **High** — detailed pathway and savings data |
| 3–5 | **Medium** — some resource data present |
| 0–2 | **Low** — only device price, minimal pathway detail |

---

## 10. Key Assumptions & Limitations

### Assumptions

1. **Complication cost proxy**: Complication-related costs are modelled
   as **10% of total current pathway cost**. This is a standard
   simplification when no specific complication tariff is available.

2. **Training headcount**: Defaults to **10 staff** when no explicit
   count is provided.

3. **Average hourly rate**: Staff time saved is valued at the mean AfC
   rate (£28.62/hr) when the specific role mix is unknown.

4. **Test costing**: Tests are proxied at the NHS follow-up outpatient
   tariff (£85). This may over- or under-estimate depending on the
   actual test type.

5. **Procedure costing**: Each procedure is costed at one theatre-hour
   (£1,200). Minor procedures may be overcosted.

6. **No inflation adjustment**: All costs are in current-year prices.
   No uplift is applied across the forecast horizon.

7. **Static eligible cohort**: The eligible population does not change
   across the forecast years. Population growth / decline is not
   modelled.

### Limitations

- The model uses **national average** NHS Reference Costs. Trust-level
  costs may differ significantly, particularly for specialist services.

- **Indirect costs** (e.g. carer time, productivity losses) are not
  included. The perspective is NHS payer only.

- Savings are applied as **simple percentage reductions** of current
  pathway costs. In reality, savings may be non-linear or dependent on
  patient acuity.

- The **3-year horizon** may not capture the full lifetime impact of
  devices with long-term benefits (e.g. implants, chronic disease
  management tools).

- Scenario analysis uses **fixed multipliers** (±20% uptake, ±15/10%
  price, ±30/20% savings). Probabilistic sensitivity analysis (PSA)
  with parameter-level distributions is not yet implemented.

- **No subgroup analysis** — all eligible patients are treated as a
  homogeneous cohort. Future versions will support stratification by
  age, comorbidity, or disease severity.

---

## Worked Example

**Device**: AI wound-assessment camera, £750/patient/year

**Setting**: Acute NHS Trust, 350,000 catchment, 2.5% eligible

**Workforce**: Band 5 (45 min) + Band 6 (15 min) + Consultant (10 min)
+ Admin (5 min)

### Current pathway cost

| Component | Calculation | £ |
|---|---|---|
| Staff time | (21.37×45 + 26.54×15 + 72×10 + 11.90×5) / 60 | 29.67 |
| Outpatient (6 visits) | 120 + 5×85 | 545.00 |
| Bed days (1 adm × 4 days) | 1×4×400 | 1,600.00 |
| Tests (3) | 3×85 | 255.00 |
| Consumables | | 85.00 |
| **Total** | | **2,514.67** |

### New pathway cost

| Component | Calculation | £ |
|---|---|---|
| Device | | 750.00 |
| Staff saving | (20/60) × 28.62 | −9.54 |
| Visit reduction (25%) | 545 × 0.25 | −136.25 |
| Bed-day reduction (0.5 day) | 1×0.5×400 | −200.00 |
| Complication reduction (15%) | 2,514.67 × 0.10 × 0.15 | −37.72 |
| Readmission reduction (10%) | 1,600 × 0.10 | −160.00 |
| Follow-up reduction (30%) | 545 × 0.30 | −163.50 |
| **Net new pathway** | | **43.00** |

### Net budget impact (base case)

| Year | Treated | Net impact/pt | Annual impact |
|---|---|---|---|
| 1 | 1,313 | 43.00 − 2,514.67 = −2,471.67 | −3,243,283 + 8,000 (setup) + 8,586 (training) = −3,226,697 |
| 2 | 3,500 | −2,471.67 | −8,650,845 |
| 3 | 5,688 | −2,471.67 | −14,060,867 |

Break-even: **Year 1** (device saves money from the start despite setup costs).

*Note: The worked example values are approximate. The engine uses
precise intermediate values which may differ slightly due to rounding.*
