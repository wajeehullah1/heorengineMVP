# HEOR Engine — User Guide

Version 0.1 | February 2026

> **For research and early market-access use only.**
> This tool is not a substitute for a NICE-compliant health technology assessment dossier
> prepared by a qualified health economist.

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [Quick Start](#2-quick-start)
3. [Budget Impact Analysis](#3-budget-impact-analysis)
4. [Cost-Effectiveness Analysis](#4-cost-effectiveness-analysis)
5. [Literature Screening](#5-literature-screening)
6. [Evidence Sources](#6-evidence-sources)
7. [Interpreting Results](#7-interpreting-results)
8. [Best Practices](#8-best-practices)
9. [API Reference](#9-api-reference)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Introduction

### What is HEOR Engine?

HEOR Engine is a health economics and outcomes research platform that automates
the most time-consuming parts of an economic submission for a medical device or
digital health technology in the NHS. It combines three analytical modules:

| Module | What it answers |
|---|---|
| **Budget Impact Analysis (BIA)** | "What will this technology cost the NHS over the next 3 years?" |
| **Cost-Effectiveness Analysis (CEA)** | "Is this technology good value for money compared to standard care?" |
| **Literature Screening (SLR)** | "Which published studies are relevant to my PICO question?" |

A fourth module — **Evidence Enrichment** — automatically pre-fills NHS
reference costs, ONS population data, and NICE guidance context into your
analysis, reducing the time to a first draft from days to minutes.

### Who is it for?

- **Market access managers** preparing NHS business cases or NICE submissions
- **HEOR analysts** needing a rapid first-cut model before formal analysis
- **Health economists** who want an API-first modelling backend
- **Clinical entrepreneurs** who want to understand the economics of their device
  before commissioning a full health technology assessment

### What can it do?

- Generate a 3-year NHS budget impact model in under 60 seconds
- Run a Markov cost-effectiveness model (with R) and produce a cost-effectiveness
  plane and ICER interpretation
- Screen hundreds of PubMed abstracts against PICO criteria using Claude AI
- Export analysis as a 10- or 16-slide PowerPoint deck suitable for a business case
- Return structured JSON via REST API for integration with other tools

### What it cannot do (yet)

- Probabilistic sensitivity analysis (PSA / Monte Carlo) — only one-way
  scenario analysis is supported
- Multi-state Markov models beyond the 2-state (Alive / Dead) structure
- Subgroup analysis by age, sex, or comorbidity
- Formal NICE Technology Appraisal dossiers — this tool produces a starting
  point, not a submission-ready model

---

## 2. Quick Start

### Installation

**Requirements**

| Component | Minimum version | Notes |
|---|---|---|
| Python | 3.11 | Required |
| R + Rscript | 4.x | Required for CEA (Cost-Effectiveness Analysis) |
| ANTHROPIC_API_KEY | — | Required for Literature Screening only |

**Install the API server**

```bash
# Clone the repository
git clone <your-repo-url>
cd heor-poc-v3

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Start the API server
uvicorn app.main:app --reload --port 8000
```

The API is now available at `http://localhost:8000`.
OpenAPI documentation (Swagger UI): `http://localhost:8000/docs`

**Install the Streamlit demo (optional)**

```bash
pip install -r demo/requirements.txt
streamlit run demo/app.py --server.port 8501
```

### Setting up API keys

Literature screening requires an Anthropic API key. Export it before starting
the server:

```bash
export ANTHROPIC_API_KEY="your-anthropic-api-key"   # macOS / Linux
set ANTHROPIC_API_KEY=your-anthropic-api-key        # Windows CMD
```

> **⚠️ Important:** Do not commit API keys to version control. Use a `.env` file
> or a secrets manager in production environments.

### Running your first analysis

The fastest way to get a result is the **Quick Estimate** endpoint. It needs
only five fields and uses NHS-wide defaults for everything else:

```bash
curl -X POST http://localhost:8000/api/workflows/quick-estimate \
  -H "Content-Type: application/json" \
  -d '{
    "intervention_name": "RemoteMonitor Pro",
    "condition": "diabetes",
    "catchment_population": 250000,
    "device_cost_per_patient": 800,
    "expected_visit_reduction_pct": 10
  }'
```

You will receive a JSON response containing:
- Estimated eligible patients (based on 7% diabetes prevalence)
- 3-year annual budget impacts
- Break-even year
- Plain-English interpretation
- Links to next-step endpoints for a full analysis

---

### 5-Minute Tutorial: Remote Glucose Monitor Case Study

This tutorial walks through a realistic scenario: an ICB wants to evaluate
rolling out real-time continuous glucose monitoring (rtCGM) for 250,000
registered patients across a Midlands ICB.

**Step 1 — Quick Estimate (1 minute)**

Use the Quick Estimate to get a rough sense of scale before investing time in
detailed inputs.

```python
import requests

result = requests.post(
    "http://localhost:8000/api/workflows/quick-estimate",
    json={
        "intervention_name": "Dexcom G7 rtCGM",
        "condition": "diabetes",
        "catchment_population": 250_000,
        "device_cost_per_patient": 850,
        "expected_visit_reduction_pct": 15,
        "expected_los_reduction_days": 0,
    }
).json()

estimate = result["estimate"]
print(f"Eligible patients:  {estimate['eligible_patients']:,}")
print(f"3-year net impact:  £{estimate['cumulative_3yr_net_impact_gbp']:,.0f}")
print(f"Break-even year:    {estimate['break_even_year'] or 'None within 3 years'}")
```

**Expected output:**
```
Eligible patients:  17,500
3-year net impact:  £4,230,000
Break-even year:    None within 3 years
```

**Step 2 — Full BIA (3 minutes)**

Now run a detailed BIA with your actual pathway data and evidence enrichment:

```python
bia_result = requests.post(
    "http://localhost:8000/api/workflows/bia",
    json={
        "inputs": {
            "setting": "ICB",
            "model_year": 2026,
            "forecast_years": 3,
            "funding_source": "ICB commissioning",
            "catchment_size": 250_000,
            "eligible_pct": 7.0,
            "uptake_y1": 10.0,
            "uptake_y2": 25.0,
            "uptake_y3": 40.0,
            "workforce": [
                {"role": "Band 6 (Senior Nurse / Specialist)", "minutes": 45, "frequency": "per patient"}
            ],
            "price": 850.0,
            "outpatient_visits": 4,
            "visits_reduced": 20.0,
            "complications_reduced": 15.0,
        },
        "intervention_name": "Dexcom G7 rtCGM",
        "enrich_with_evidence": True,
        "generate_report": True,
    }
).json()

print(f"Workflow ID:  {bia_result['workflow_id']}")
print(f"Year-1 impact: £{bia_result['results']['annual_budget_impact'][0]:,.0f}")
print(f"Report URL:  {bia_result['report_url']}")
```

**Step 3 — Download your report (30 seconds)**

```bash
curl http://localhost:8000/api/download-report/<submission_id> \
  --output bia_report.pptx
```

Your 10-slide PowerPoint report is ready to share with the ICB.

---

## 3. Budget Impact Analysis

### What is a Budget Impact Analysis?

A Budget Impact Analysis (BIA) estimates the **net cost or saving** to the NHS
of adopting a new technology over a defined time horizon — typically 1 to 5
years. It answers the decision-maker's question: **"Can we afford this?"**

A BIA does **not** measure value for money (that is the job of Cost-Effectiveness
Analysis). It purely quantifies the financial impact on a budget holder — a
trust, an ICB, or a primary care network.

BIA is required for:
- NICE Medical Technologies Guidance (MTG) submissions
- NICE Health Technology Evaluations (HTE)
- NHS England Transformation Fund applications
- ICB and trust business cases for technology investment

### When to use it

Use BIA when:
- You need to demonstrate budget affordability to a commissioner
- The technology replaces or reduces existing NHS activity
- You need to plan phased rollout based on uptake projections
- Your audience is a finance director or budget manager rather than a clinician

### Required inputs explained

The BIA requires a minimum of 10 fields. All other fields have sensible defaults
based on national NHS data.

#### Organisation & Model Setup

| Field | Type | Example | Notes |
|---|---|---|---|
| `setting` | Enum | `"ICB"` | One of: `"Acute NHS Trust"`, `"ICB"`, `"Primary Care Network"` |
| `model_year` | Integer | `2026` | Financial year the model starts (2024–2030) |
| `forecast_years` | Integer | `3` | How many years to project (1–10) |
| `funding_source` | Enum | `"ICB commissioning"` | See full list below |

**Funding source options:**

| Value | When to use |
|---|---|
| `"Trust operational budget"` | Revenue expenditure from existing trust budget |
| `"ICB commissioning"` | Commissioned service from ICB |
| `"Transformation / innovation funding"` | NHS England transformation programme |
| `"Capital budget"` | One-off capital purchase (equipment, infrastructure) |
| `"Industry-funded pilot"` | Funded by the technology manufacturer |
| `"Research / grant"` | Academic or research council funding |
| `"Unsure"` | Use when funding source is not yet decided |

#### Population & Uptake

| Field | Type | Example | Notes |
|---|---|---|---|
| `catchment_size` | Integer | `250000` | Total population or number of beds |
| `catchment_type` | Enum | `"population"` | `"population"` or `"beds"` |
| `eligible_pct` | Float | `7.0` | % of catchment who are eligible for the technology |
| `uptake_y1` | Float | `10.0` | % of eligible patients treated in Year 1 |
| `uptake_y2` | Float | `25.0` | % of eligible patients treated in Year 2 |
| `uptake_y3` | Float | `40.0` | % of eligible patients treated in Year 3 |

> **💡 Tip:** Use the evidence enrichment feature (`"enrich_with_evidence": true`)
> to auto-populate `eligible_pct` from NHS QOF disease prevalence data. For
> diabetes it will suggest 7.0%; for cardiovascular disease, 4.0%.

> **⚠️ Important:** Uptake percentages should be realistic. A jump from 10% to
> 90% in one year is rarely achievable and will trigger a clinical-sense warning.
> Plan for a 3–5 year adoption curve.

#### Pricing

| Field | Type | Example | Notes |
|---|---|---|---|
| `price` | Float | `850.0` | Intervention cost in £ |
| `pricing_model` | Enum | `"per-patient"` | `"per-patient"`, `"per-use"`, `"subscription"`, `"capital + consumables"` |
| `price_unit` | Enum | `"per year"` | `"per year"`, `"per patient"`, `"per use"` |
| `setup_cost` | Float | `5000.0` | One-off Year-1 setup or installation cost in £ |

#### Workforce

At least one workforce row is required. Each row defines a staff role, their
time per patient, and how often that time is spent.

```json
"workforce": [
  {
    "role": "Band 6 (Senior Nurse / Specialist)",
    "minutes": 45,
    "frequency": "per patient"
  },
  {
    "role": "Band 5 (Staff Nurse)",
    "minutes": 20,
    "frequency": "per visit"
  }
]
```

**Frequency options:** `"per patient"` · `"per visit"` · `"per admission"` · `"per year"`

**NHS AfC hourly rates used in calculations (2024/25):**

| Role | £/hour |
|---|---|
| Band 2 | £12.45 |
| Band 3 | £14.28 |
| Band 4 | £16.83 |
| Band 5 (Staff Nurse) | £21.37 |
| Band 6 (Senior Nurse / AHP) | £26.54 |
| Band 7 (Advanced Practitioner) | £32.11 |
| Band 8a (Consultant Nurse / Manager) | £40.22 |
| Registrar | £38.50 |
| Consultant | £72.00 |
| Admin / Clerical | £11.90 |

#### Current Pathway (resource use)

These fields describe the **current** pathway that the intervention replaces.
The more accurately you complete them, the more reliable the savings calculation.

| Field | Default | Description |
|---|---|---|
| `outpatient_visits` | `0` | Outpatient visits per patient per year |
| `tests` | `0` | Diagnostic tests per patient per year |
| `admissions` | `0` | Hospital admissions per patient per year |
| `bed_days` | `0` | Bed days per admission |
| `procedures` | `0` | Procedures per patient per year |
| `consumables` | `0.0` | Consumables cost per patient (£) |

**NHS reference costs used:**

| Item | £ |
|---|---|
| Outpatient first attendance | £120 |
| Outpatient follow-up | £85 |
| General ward bed day | £400 |
| Diagnostic test (proxy) | £85 |
| Theatre hour / procedure | £1,200 |

#### Savings & Offsets

These fields capture the expected resource reduction the intervention delivers.

| Field | Default | Description |
|---|---|---|
| `staff_time_saved` | `0.0` | Minutes of staff time saved per patient visit |
| `visits_reduced` | `0.0` | % reduction in outpatient visits |
| `complications_reduced` | `0.0` | % reduction in complications |
| `readmissions_reduced` | `0.0` | % reduction in unplanned readmissions |
| `los_reduced` | `0.0` | Reduction in length of stay per admission (days) |
| `follow_up_reduced` | `0.0` | % reduction in follow-up contacts |

> **⚠️ Important:** Savings figures must be supported by clinical evidence.
> If you enter `complications_reduced: 80`, the model will produce a savings
> estimate that may be unrealistically large — and will trigger a warning.
> Use published trial data or real-world evidence where possible.

### Understanding the outputs

After a successful BIA workflow, the response contains:

```json
{
  "workflow_id": "bia_20260226_141500_a3f8b2c1",
  "status": "completed",
  "results": {
    "annual_budget_impact": [-1250000, -3100000, -5800000],
    "cost_per_patient": [720, 695, 668],
    "total_treated_patients": [1750, 4375, 7000],
    "break_even_year": 1,
    "top_cost_drivers": ["Device acquisition", "Band 6 nursing time", "Outpatient visits"],
    "scenarios": {
      "conservative": { ... },
      "base": { ... },
      "optimistic": { ... }
    }
  },
  "warnings": [],
  "report_url": "/api/download-report/bia_20260226_141500_a3f8b2c1",
  "execution_time_seconds": 12.4
}
```

**Key output fields explained:**

| Field | What it means |
|---|---|
| `annual_budget_impact` | Net financial impact in £ per year. **Negative = saving. Positive = additional cost.** |
| `cost_per_patient` | Total net cost per treated patient per year (new pathway minus current pathway) |
| `total_treated_patients` | Number of patients receiving the intervention each year |
| `break_even_year` | The first year in which cumulative savings exceed cumulative costs. `null` if never. |
| `top_cost_drivers` | The three largest cost components in the model (by absolute £ value) |
| `scenarios` | Conservative, base, and optimistic projections (see Scenario Analysis below) |

### Using evidence enrichment

When you set `"enrich_with_evidence": true` (the default), the engine
automatically queries three data sources before running the BIA:

1. **NHS National Cost Collection** — validates your unit costs against national benchmarks
2. **ONS population data** — estimates eligible population from disease prevalence
3. **NICE guidance** — identifies relevant comparators and published ICERs

The enrichment result appears in the response under `enrichment_applied`:

```json
"enrichment_applied": {
  "evidence_enrichment_requested": true,
  "confidence_rating": "Medium",
  "suggestions": [
    "Outpatient visit cost adjusted to NHS Reference Cost (£85 follow-up tariff).",
    "Eligible population estimated at 7.0% using QOF diabetes prevalence for England."
  ]
}
```

> **💡 Tip:** Enrichment adds 15–30 seconds to the workflow. If you already have
> accurate local pathway costs and prevalence data, you can disable it with
> `"enrich_with_evidence": false` for faster results.

### Interpreting scenarios

The BIA engine automatically runs three scenarios by modifying your base-case inputs:

| Parameter | Conservative | Base | Optimistic |
|---|---|---|---|
| Uptake (all years) | −20% | As entered | +20% |
| Device price | +15% | As entered | −10% |
| All savings | −30% | As entered | +20% |

**Why scenarios matter:**
- The **conservative** scenario represents the realistic downside risk — slower
  adoption, procurement at full list price, and modest savings realisation. This
  is the scenario commissioners are most likely to scrutinise.
- The **optimistic** scenario assumes strong clinical champion support, a
  volume-negotiated price discount, and trial-equivalent savings. Use this
  to bound the upper benefit estimate.
- All uptake and savings values are capped at 100% after scaling.

### Common pitfalls

**Eligible percentage too high**
Using the raw national prevalence figure without accounting for patients already
on treatment, those not suitable for the technology, or those outside the
commissioning scope. Start with 50–70% of the condition prevalence as a
conservative eligible fraction.

**Savings without evidence**
Entering large savings percentages without published supporting data. Any figure
above 30–40% for complications or readmissions will need a published citation
to pass scrutiny.

**Ignoring Year-1 one-off costs**
Setup costs and staff training are automatically applied in Year 1 only. Make
sure you enter realistic setup costs — if these are large, break-even will be
pushed into Year 2 or Year 3.

**Uplift not applied to AfC rates**
The model uses the direct NHS pay rate without employer on-costs. For full
economic cost (National Insurance, pension, overhead), multiply the AfC rate by
approximately **1.35–1.55**.

---

## 4. Cost-Effectiveness Analysis

### What is CEA / ICER?

A Cost-Effectiveness Analysis (CEA) measures whether a technology is **good
value for the health it delivers**. Rather than asking "how much will this
cost?", it asks "how much does each unit of health benefit cost?"

In the NHS, health benefit is measured in **QALYs** (Quality-Adjusted Life
Years). One QALY represents one year of life in perfect health. NICE compares
the cost of gaining one additional QALY between a new technology and an
alternative to decide whether the technology should be funded.

The key output of a CEA is the **ICER** (Incremental Cost-Effectiveness Ratio):

```
ICER = (Cost of treatment − Cost of standard care)
       ─────────────────────────────────────────────
       (QALYs with treatment − QALYs with standard care)
```

### The Markov model explained

HEOR Engine uses a **2-state Markov model** to calculate the ICER. This is the
standard approach for medical device evaluations in NICE submissions.

```
                  Annual mortality probability
    ┌──────┐  ─────────────────────────────►  ┌──────┐
    │ Alive│                                   │ Dead │
    └──────┘  ◄─────────────────────────────  └──────┘
          1 − annual mortality probability    (absorbing)
```

The model simulates a cohort of patients over time. In each cycle (default:
1 year), patients either survive (remaining "Alive" and accruing costs and
QALYs) or die (entering the absorbing "Dead" state with no further costs or
health outcomes).

The model runs **two arms in parallel** — one for standard care and one for
the new technology — then compares them to calculate the ICER.

**Why a 2-state model?**

It is appropriate when:
- The intervention primarily affects mortality (not disease progression stages)
- Quality-of-life improvement can be modelled as a constant utility weight
- More granular health state data is unavailable

For a NICE Technology Appraisal, a 2-state model may need to be extended.
Discuss with a health economist before using these results in a formal
submission.

### Required inputs

| Field | Type | Default | Description |
|---|---|---|---|
| `intervention_name` | String | — | Name of the technology |
| `prob_death_standard` | Float (0–1) | — | Annual probability of death under standard care |
| `cost_standard_annual` | Float (£) | — | Annual cost per patient under standard care |
| `utility_standard` | Float (0–1) | — | EQ-5D utility score under standard care |
| `prob_death_treatment` | Float (0–1) | — | Annual probability of death with treatment |
| `cost_treatment_annual` | Float (£) | — | Annual cost per patient with treatment |
| `utility_treatment` | Float (0–1) | — | EQ-5D utility score with treatment |
| `time_horizon` | Integer | `5` | Simulation length in years (1–50) |
| `discount_rate` | Float | `0.035` | Annual discount rate (NICE default: 3.5%) |
| `cost_treatment_initial` | Float (£) | `0.0` | One-time upfront acquisition cost |

**Condition-specific starting values:**

| Condition | Baseline mortality | Baseline utility | Typical annual cost |
|---|---|---|---|
| Cancer | 0.15 (15%/yr) | 0.60 | £12,000 |
| Cardiovascular | 0.08 (8%/yr) | 0.70 | £6,000 |
| Diabetes | 0.05 (5%/yr) | 0.75 | £4,000 |
| Respiratory | 0.10 (10%/yr) | 0.65 | £5,000 |

### NICE thresholds guide

NICE assesses whether an ICER is acceptable by comparing it to a
**willingness-to-pay (WTP) threshold**:

| Threshold band | £/QALY range | When it applies |
|---|---|---|
| **Standard** | £20,000–£25,000 | Default for all technologies |
| **Extended** | £25,000–£35,000 | May be accepted with innovation or unmet-need justification |
| **End of life** | Up to £50,000 | Remaining life < 24 months AND ≥ 3 months life extension AND small patient population |
| **Highly Specialised** | £100,000–£300,000 | Highly Specialised Technologies programme, ultra-rare diseases (< 1:50,000) |

The engine automatically returns two boolean flags:
- `cost_effective_25k` — True if ICER is below £25,000/QALY
- `cost_effective_35k` — True if ICER is below £35,000/QALY

> **⚠️ Important:** The end-of-life threshold has strict eligibility criteria
> that must be assessed case by case with a NICE submission team. Do not assume
> the £50,000 threshold applies without a formal eligibility check.

### Interpreting results

**Treatment dominates** (incremental cost < 0, incremental QALYs > 0)
The technology is both cheaper and more effective than standard care. This is the
ideal outcome. The analysis strongly supports adoption.

**Below £25,000/QALY**
Cost-effective at the standard NICE threshold. Likely to be recommended.

**£25,000–£35,000/QALY**
In the "acceptable" range with additional justification. May be recommended
with clinical evidence of innovation, unmet need, or certainty in the ICER.

**Above £35,000/QALY**
Unlikely to be recommended unless the end-of-life criteria apply.

**Treatment dominated** (incremental cost > 0, incremental QALYs < 0)
The technology is more expensive and less effective. There is no economic case
for adoption.

**ICER = N/A** (no incremental QALY gain)
When the quality-of-life gain is effectively zero but costs differ, the ICER
formula is undefined. Compare on cost alone: if the treatment costs less, it
may still be adopted; if it costs more with no benefit, there is no case.

### Importing BIA cost data into CEA

If you have already run a BIA, you can bridge the cost data directly into a CEA
without re-entering values:

```python
response = requests.post(
    "http://localhost:8000/api/calculate-icer-from-bia",
    json={
        "submission_id": "bia_20260226_141500_a3f8b2c1",
        "mortality_reduction": 30,    # % reduction in annual mortality
        "utility_gain": 0.07,         # additive EQ-5D improvement
    }
)
```

The engine will derive:
- `cost_treatment_annual` from the BIA Year-1 cost per patient
- `cost_standard_annual` from the BIA workforce cost calculation
- `cost_treatment_initial` from BIA setup cost + device price (Year 1)
- `prob_death_treatment` = base mortality × (1 − mortality_reduction / 100)

You must still supply:
- **`mortality_reduction`** — the % reduction in annual mortality based on trial
  data or published evidence
- **`utility_gain`** — the absolute EQ-5D improvement from the literature

> **💡 Tip:** When using the Combined workflow (`POST /api/workflows/combined`),
> the BIA → CEA bridge is applied automatically. You only need to enter the
> mortality reduction and utility gain alongside your BIA inputs.

### Uncertainty and sensitivity

The current implementation provides **one-way scenario analysis** only:

- The 2-state Markov model produces a single deterministic ICER
- No probabilistic sensitivity analysis (PSA) is available
- No cost-effectiveness acceptability curve (CEAC) is generated

For a NICE submission, PSA with Monte Carlo simulation is required. Use the
HEOR Engine ICER as a starting point and replicate the model in a validated
economic modelling tool (e.g. R `heemod`, Excel with Crystal Ball, or TreeAge)
for full submission.

---

## 5. Literature Screening

### The PICO framework explained

PICO is the standard framework for defining a clinical research question in
systematic literature reviews:

| Letter | Stands for | Example |
|---|---|---|
| **P** | Population | Adults with type 2 diabetes |
| **I** | Intervention | Real-time continuous glucose monitoring (rtCGM) |
| **C** | Comparison | Standard self-monitoring of blood glucose (SMBG) |
| **O** | Outcomes | HbA1c reduction, time in range, quality of life |

You also specify:
- **Study types** — `RCT`, `Cohort study`, `Economic evaluation`, `Systematic review`
- **Exclusion criteria** — explicit reasons to exclude even when PICO criteria
  are met (e.g. "Paediatric populations", "Animal studies")

### How AI screening works

The HEOR Engine sends each abstract to Claude (Anthropic's AI) with your PICO
criteria and asks it to classify the study as:

| Decision | Meaning |
|---|---|
| `include` | The study meets all PICO criteria; proceed to full-text review |
| `exclude` | The study fails one or more PICO criteria; remove from review |
| `uncertain` | The abstract does not provide enough information; full-text required |

For each decision, the AI also returns:

- **Confidence** (`high` / `medium` / `low`) — How certain the AI is in its
  decision
- **PICO match scores** — A score per PICO component (0–1) showing how well
  each criterion was met
- **Reasoning** — A natural-language explanation of the decision
- **Exclusion reasons** — Specific PICO components that failed (for `exclude`
  decisions only)

### Submitting abstracts

Abstracts must be submitted as a JSON array. Each abstract requires:

```json
{
  "pmid": "35421876",
  "title": "Continuous glucose monitoring vs SMBG in T2DM: an RCT",
  "abstract": "Background: ... Methods: ... Results: ... Conclusions: ...",
  "authors": ["Smith JA", "Patel RK"],
  "journal": "Lancet Diabetes & Endocrinology",
  "year": 2022
}
```

You can also upload a CSV via the Streamlit demo (columns: `pmid`, `title`,
`abstract`, `authors`, `journal`, `year` — with multiple authors separated
by `|`).

### Reviewing uncertain decisions

Abstracts classified as `uncertain` should be sent for manual full-text review.
They are not included in the `included` count and not excluded. The engine
surfaces them separately in the summary counts:

```json
"screening_summary": {
  "total": 50,
  "included": 18,
  "excluded": 27,
  "uncertain": 5,
  "inclusion_rate": 0.36
}
```

> **💡 Tip:** A high `uncertain` count (> 20% of total) may indicate that your
> PICO criteria are ambiguous. Review the reasoning for uncertain decisions and
> consider tightening your `population` or `intervention` definitions.

### Exporting for manual review

After screening, you can export the full decision table to CSV or Excel:

```bash
# Via the workflow export URL
curl http://localhost:8000/api/workflows/<workflow_id>/export \
  --output screening_results.csv

# Or directly from the batch
curl -X POST http://localhost:8000/api/slr/export/<batch_id> \
  -H "Content-Type: application/json" \
  -d '{"format": "excel"}' \
  --output screening_results.xlsx
```

The export includes: PMID, title, decision, confidence, PICO scores per
component, reasoning, exclusion reasons, and reviewer timestamp.

### Integration with full systematic reviews

The HEOR Engine screening module is designed to **replace or supplement Title
and Abstract screening** (Stage 1 of a systematic review), not full-text
review. Typical workflow:

1. Export PubMed search results as a CSV or RIS file
2. Convert to the HEOR Engine JSON format (PMID, title, abstract, authors,
   journal, year)
3. Submit to the SLR screening endpoint
4. Export `include` and `uncertain` decisions for full-text review
5. Conduct full-text screening and data extraction manually or with other tools

> **⚠️ Important:** AI-assisted screening introduces a risk of false negatives
> (missing relevant studies). For a Cochrane-compliant or NICE-submitted
> review, independent dual screening of all abstracts is required. Use the
> HEOR Engine as the first screener and verify `uncertain` and a random sample
> of `exclude` decisions manually.

---

## 6. Evidence Sources

### NHS Reference Costs — what they are and when updated

The **NHS National Cost Collection (NCC)** is published annually by NHS England.
It contains reference unit costs for secondary care activity across England.

The HEOR Engine uses 37 curated items from the 2024/25 collection:

| Category | Examples | £ range |
|---|---|---|
| Inpatient | General ward bed day £450, ICU bed day £1,850 | £450–£1,850/day |
| Outpatient | First consultant £135, follow-up £92 | £92–£135/visit |
| Emergency | Minor injury £145, resuscitation £420 | £145–£420 |
| Diagnostics | Blood test £3.50, MRI scan £168 | £3.50–£168 |
| Procedures | Minor theatre £850, endoscopy £385 | £385–£850 |
| Ambulance | Conveyance £275, hear-and-treat £28 | £28–£275 |
| Community | District nurse visit £48, physiotherapy £38 | £38–£48 |

**Update frequency:** Annually (financial year, published ~18 months in arrears)

**⚠️ Limitations:**
- These are **national averages**. London and specialist trusts may exceed
  the national average by 20–40%.
- NCC costs reflect **activity-weighted average costs**, not the National Tariff
  price that commissioners pay.
- No inflation adjustment — apply NHS Pay Award and GDP deflator for
  forward projections.

### ONS Population Data — coverage and limitations

The **ONS Mid-Year Population Estimates** provide the UK resident population
broken down by nation, region, and age band.

The engine uses:
- UK total: 67,800,000; England: 57,000,000
- Nine NHS England regional populations
- Disease prevalence rates from NHS Digital Quality and Outcomes Framework (QOF):

| Condition | England prevalence (QOF) |
|---|---|
| Diabetes | 6.8% |
| Hypertension | 28% |
| COPD | 1.9% |
| Asthma | 6.4% |
| Heart failure | 0.9% |
| Atrial fibrillation | 2.1% |
| Coronary heart disease | 3.1% |
| Stroke / TIA | 1.7% |
| Depression | 12.5% |

**⚠️ Limitations:**
- National prevalence may not match your local catchment. Diabetes prevalence
  ranges from ~5.9% (South West) to ~7.8% (West Midlands).
- GP-registered populations sometimes exceed census counts (patient mobility).
- The bed-to-population ratio used for bed-based catchments (1:500) is a
  planning heuristic and varies from 1:300 (specialist centres) to 1:700
  (district generals).

### NICE guidance — how it's used

The engine contains a curated set of 15 NICE guidance records covering:

| Type | Description | Binding? |
|---|---|---|
| Technology Appraisal (TA) | Formal cost-effectiveness assessment | Yes — NHS must fund within 90 days |
| NICE Guideline (NG) | Clinical practice recommendations | Expectation to comply |
| Medtech Innovation Briefing (MIB) | Horizon scanning for devices | No — informational only |
| Diagnostics Guidance (DG) | Evidence review for diagnostics | Yes |
| Highly Specialised Technology (HST) | Ultra-rare diseases | Yes |

The guidance database is used to:
- Identify relevant comparators for your BIA (via `GET /api/evidence/nice-guidance`)
- Provide ICER precedents for the condition (via `POST /api/evidence/validate`)
- Populate the NICE context section of the CEA report

> **⚠️ Important:** The curated MVP database covers only 15 records. Always
> search the full NICE guidance library at
> [nice.org.uk/guidance](https://www.nice.org.uk/guidance) before finalising
> a submission. NICE guidance is updated continuously.

### Limitations and caveats — overall

1. **Point estimates only** — all reference values are single-point estimates
   without confidence intervals. Sensitivity analysis is essential.
2. **National averages** — local costs, prevalence, and pathway data will always
   be more accurate than national benchmarks for a specific commissioner.
3. **Data currency** — the reference data is bundled at the time of the engine
   release. NHS costs change annually; NICE guidance is updated continuously.
4. **Research use** — this tool is designed for early-stage market access work.
   It is **not** a validated model for regulatory or reimbursement submissions.

---

## 7. Interpreting Results

### Reading BIA outputs

**Positive annual impact** = additional cost to the NHS
**Negative annual impact** = cost saving to the NHS

If your `annual_budget_impact` for Year 1 is `+£1,250,000`, the intervention
costs the NHS £1.25m more in Year 1 than continuing with standard care.

If it is `−£3,100,000`, the intervention saves the NHS £3.1m in that year.

> **💡 Tip:** A positive Year-1 impact followed by negative impacts in Years 2
> and 3 is common when there are significant setup or training costs in Year 1.
> Look at the cumulative impact across all years for the true picture.

### Understanding break-even year

The break-even year is the **first year** in which cumulative savings exceed
cumulative costs:

```
Year 1: +£500,000 (cost)    → Cumulative: +£500,000
Year 2: −£800,000 (saving)  → Cumulative: −£300,000  ← Break-even in Year 2
Year 3: −£1,200,000 (saving) → Cumulative: −£1,500,000
```

If `break_even_year` is `null`, the intervention does not break even within
the forecast horizon. This does not necessarily mean it should not be adopted —
a technology that costs net £200,000/year but delivers significant quality-of-life
benefits may still be cost-effective (evaluated separately by CEA).

### Cost driver analysis

`top_cost_drivers` lists the three largest cost components by absolute £ value.
This helps identify where to focus negotiation or pathway redesign:

- **Device acquisition** as the top driver → focus on volume-based pricing
- **Staff nursing time** as the top driver → consider remote monitoring or
  task substitution (Band 5 vs Band 7)
- **Outpatient visits** as the top driver → demonstrate visit avoidance with
  real-world evidence

### ICER interpretation

The ICER is interpreted in terms of the NHS willingness-to-pay thresholds:

```
ICER < £25,000/QALY      → Cost-effective (standard threshold)
£25,000–£35,000/QALY     → Borderline — may be accepted with justification
£35,000–£50,000/QALY     → Above standard threshold — end-of-life criteria needed
> £50,000/QALY           → Unlikely to be recommended
Negative ICER            → Treatment dominates (check the sign of each component)
```

> **⚠️ Important:** A negative ICER has two very different meanings:
> - If incremental cost is **negative** and incremental QALYs are **positive**:
>   treatment **dominates** — it is both cheaper and more effective. Excellent.
> - If incremental cost is **positive** and incremental QALYs are **negative**:
>   treatment is **dominated** — it is more expensive and less effective. Reject.
>
> Always check both components, not just the sign of the ICER.

### NICE decision-making context

NICE does not use the ICER alone. Other factors that influence the decision:

- **Severity of condition** — more weight given to treatments for severe or
  terminal conditions
- **Innovation** — first-in-class technologies may receive additional
  consideration
- **Burden of illness** — wider societal impact beyond QALYs
- **Certainty of evidence** — a high ICER from a single small trial carries
  more uncertainty than one from a large RCT
- **Patient access schemes** — managed entry agreements can bring the effective
  ICER below the threshold

The HEOR Engine provides the ICER and threshold comparison. The broader
appraisal context requires a qualified HEOR professional.

### When to be sceptical of results

Be cautious when:

1. **Savings exceed 50% of current pathway cost** — large savings percentages
   may be overstated or not generalisable to routine NHS practice
2. **Break-even in Year 1** with no setup costs — check that one-off costs
   are not being overlooked
3. **ICER below £5,000/QALY** — very low ICERs sometimes indicate parameter
   errors (e.g. mortality reduction entered as 99% rather than 9.9%)
4. **Confidence rating is "Low"** — the evidence enrichment module has
   flagged that only minimal pathway data was entered
5. **Clinical-sense warnings are present** — the engine detected potentially
   implausible values (e.g. decreasing uptake across years, savings > 80%)

---

## 8. Best Practices

### Garbage in, garbage out

The HEOR Engine calculates correctly given whatever inputs you provide. If your
inputs are guesses, your outputs will be guesses — but they will look precise.
Always trace each input to a source:

| Input | Recommended source |
|---|---|
| Eligible percentage | Local QOF register, local clinical audit |
| Outpatient visits | Local patient pathway data, NHS Digital |
| Admission rates | Local HES (Hospital Episode Statistics) data |
| Mortality probability | Published clinical trial, disease register |
| Utility weights | Published EQ-5D study for your condition, NICE reference case |
| Device cost | Formal quotation from supplier |
| Savings percentages | Published RCT or systematic review |

### Using realistic assumptions

- **Uptake curves**: Plan for 5–15% in Year 1, 20–40% in Year 2, 40–60% in
  Year 3 for a new device entering a Trust. Faster ramp-up is possible if a
  national mandate or strong clinical champion is in place.
- **Staff training**: Budget at least 4–8 hours of training per staff member
  for a new digital technology.
- **Visit reduction**: Clinical trials typically show 15–25% reduction in
  face-to-face contacts for remote monitoring technologies. Higher figures
  need strong evidence.

### Validating against published studies

Before submitting your analysis, cross-check key outputs:

1. Compare your `cost_per_patient` against published cost-of-illness studies
   for the condition
2. Compare your ICER against ICERs from similar technologies in the NICE
   evidence database
3. Check your `eligible_pct` against published prevalence statistics

> **💡 Tip:** Use `GET /api/evidence/nice-guidance?q=<condition>` to find
> published ICERs for comparable technologies. This provides a useful
> "sanity check" range for your own ICER.

### Documenting assumptions

Every submission should include an **Assumptions Table** listing each key input,
its value, its source, and the uncertainty range. The HEOR Engine PowerPoint
report includes a "Model Assumptions & Methodology" slide — fill this in before
sharing the deck.

**Example assumptions table:**

| Parameter | Value | Source | Uncertainty range |
|---|---|---|---|
| Eligible population | 7.0% | NHS QOF 2024 | 5.5%–8.5% |
| Outpatient visits | 4/yr | Local audit, 2024 | 3–6/yr |
| Visit reduction | 20% | Smith et al. 2022 (RCT) | 12%–30% |
| Utility — treatment | 0.82 | Smith et al. 2022 (EQ-5D) | 0.76–0.88 |

### Sensitivity analysis

The engine provides three pre-defined scenarios (conservative, base, optimistic).
For additional sensitivity analysis:

1. Re-run the BIA with different values for the most important drivers (those
   in `top_cost_drivers`)
2. Re-run the CEA with alternative mortality and utility values from the
   literature
3. Consider time horizon sensitivity: run at 3, 5, and 10 years to understand
   the long-term picture

For a NICE submission, **probabilistic sensitivity analysis (PSA)** with Monte
Carlo simulation is required. This is beyond the current scope of HEOR Engine.

### When to get expert review

Engage a qualified health economist before:
- Submitting to NICE (Technology Appraisal, MTG, HTE)
- Presenting to an ICB investment committee
- Publishing or publicly sharing results
- Using results as evidence in a regulatory submission

The HEOR Engine is a decision-support tool, not a replacement for professional
health economic expertise.

---

## 9. API Reference

### Authentication

The API currently has **no authentication**. All endpoints are publicly
accessible when the server is running. For production deployments, add an
authentication layer (e.g. API key header, OAuth2) before exposing the API
externally.

The only credential required is the **Anthropic API key** for literature
screening — set as the `ANTHROPIC_API_KEY` environment variable on the server.

### Endpoint overview

#### Workflows (recommended entry points)

| Method | Path | Description |
|---|---|---|
| POST | `/api/workflows/quick-estimate` | Back-of-envelope BIA (no pathway data needed) |
| POST | `/api/workflows/bia` | Full BIA with evidence enrichment and report |
| POST | `/api/workflows/cea` | Full CEA (Markov model) with NICE context and report |
| POST | `/api/workflows/combined` | BIA + CEA in a single call |
| POST | `/api/workflows/slr` | AI abstract screening against PICO criteria |
| GET | `/api/workflows/{workflow_id}` | Get status and full results for any workflow |
| GET | `/api/workflows` | List all saved workflows (with pagination) |
| GET | `/api/workflows/{workflow_id}/report` | Download workflow report (PPTX/DOCX) |
| GET | `/api/workflows/{workflow_id}/export` | Download SLR export (CSV/XLSX) |

#### Individual calculation endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/api/calculate-bia` | Run BIA calculation only (no enrichment or report) |
| POST | `/api/calculate-icer` | Run Markov CEA only |
| POST | `/api/calculate-icer-from-bia` | Derive CEA from an existing BIA submission |
| POST | `/api/generate-report` | Generate BIA PowerPoint report |
| POST | `/api/generate-cea-report` | Generate CEA PowerPoint report |
| POST | `/api/generate-combined-report` | Generate combined BIA + CEA report |

#### Evidence endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/evidence/reference-costs` | All 37 NHS National Cost Collection items |
| GET | `/api/evidence/population` | ONS population data with optional filters |
| GET | `/api/evidence/nice-guidance` | Search NICE guidance database |
| POST | `/api/evidence/enrich-inputs` | Enrich BIA inputs with reference data |
| POST | `/api/evidence/validate` | Validate results against NICE precedents |
| POST | `/api/suggest-defaults` | Suggest BIA defaults for a condition |

#### SLR endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/api/slr/screen` | Screen a batch of abstracts against PICO |
| GET | `/api/slr/batches` | List all screening batches |
| GET | `/api/slr/batch/{batch_id}` | Get full details for a screening batch |
| POST | `/api/slr/export/{batch_id}` | Export batch to CSV or Excel |
| GET | `/api/slr/sample-pico` | Get example PICO templates |

### Request / response formats

All requests use `Content-Type: application/json`. All responses are JSON.

**Workflow response envelope:**

```json
{
  "workflow_id": "bia_20260226_141500_a3f8b2c1",
  "status": "completed",
  "results": { ... },
  "report_url": "/api/download-report/...",
  "execution_time_seconds": 14.2,
  "created_at": "2026-02-26T14:15:00Z"
}
```

**Status values:**

| Status | Meaning |
|---|---|
| `completed` | All steps executed successfully |
| `partial` | Core analysis succeeded; an optional step (e.g. report) failed |
| `failed` | A required step failed; check the error detail |

### Error codes

| HTTP Status | When it occurs | Typical `detail` message |
|---|---|---|
| `400` | Bad request — malformed JSON or invalid parameter type | `"Value must be between 0 and 100"` |
| `404` | Resource not found — workflow ID, submission ID, or batch ID does not exist | `"Workflow not found"` |
| `422` | Validation error — required field missing or value out of range | `"Input validation failed: eligible_pct is required"` |
| `500` | Internal server error — calculation engine failed | `"BIA calculation error: ..."` |
| `503` | Service unavailable — R not installed or ANTHROPIC_API_KEY not set | `"R is not installed"` |

Error responses include a `detail` field with a human-readable message:

```json
{
  "detail": {
    "message": "Input validation failed: price is required; workforce must have at least 1 row",
    "step": "validate_inputs",
    "execution_time_seconds": 0.012
  }
}
```

### Rate limits

No rate limiting is currently implemented. In production, consider adding
rate limiting per IP address using a FastAPI middleware such as `slowapi`.

### Python code examples

**Quick Estimate:**

```python
import requests

resp = requests.post(
    "http://localhost:8000/api/workflows/quick-estimate",
    json={
        "intervention_name": "RemoteMonitor Pro",
        "condition": "diabetes",
        "catchment_population": 250_000,
        "device_cost_per_patient": 800.0,
        "expected_visit_reduction_pct": 15.0,
        "expected_los_reduction_days": 0.0,
    },
    timeout=60,
)
result = resp.json()
print(result["interpretation"])
```

**Full BIA Workflow:**

```python
payload = {
    "inputs": {
        "setting": "ICB",
        "model_year": 2026,
        "forecast_years": 3,
        "funding_source": "ICB commissioning",
        "catchment_size": 250_000,
        "eligible_pct": 7.0,
        "uptake_y1": 10.0, "uptake_y2": 25.0, "uptake_y3": 40.0,
        "workforce": [
            {"role": "Band 6 (Senior Nurse / Specialist)", "minutes": 45, "frequency": "per patient"}
        ],
        "price": 850.0,
        "outpatient_visits": 4,
        "visits_reduced": 20.0,
        "complications_reduced": 15.0,
    },
    "intervention_name": "RemoteMonitor Pro",
    "enrich_with_evidence": True,
    "generate_report": True,
    "report_format": "pptx",
}

resp = requests.post(
    "http://localhost:8000/api/workflows/bia",
    json=payload,
    timeout=120,
)
data = resp.json()

# Download the report
if data.get("report_url"):
    report = requests.get(f"http://localhost:8000{data['report_url']}")
    with open("bia_report.pptx", "wb") as f:
        f.write(report.content)
    print(f"Report saved ({len(report.content) // 1024} KB)")
```

**CEA Workflow:**

```python
resp = requests.post(
    "http://localhost:8000/api/workflows/cea",
    json={
        "inputs": {
            "intervention_name": "RemoteMonitor Pro",
            "prob_death_standard": 0.05,
            "cost_standard_annual": 4_000.0,
            "utility_standard": 0.75,
            "prob_death_treatment": 0.035,
            "cost_treatment_annual": 5_200.0,
            "utility_treatment": 0.82,
            "time_horizon": 5,
            "discount_rate": 0.035,
        },
        "intervention_name": "RemoteMonitor Pro",
        "validate_against_nice": True,
        "generate_report": True,
    },
    timeout=120,
)
cea = resp.json()
results = cea["results"]
print(f"ICER: £{results['icer']:,.0f}/QALY")
print(f"Cost-effective at £25k: {results['cost_effective_25k']}")
print(f"Interpretation: {results['interpretation']}")
```

**Literature Screening:**

```python
resp = requests.post(
    "http://localhost:8000/api/workflows/slr",
    json={
        "pico_criteria": {
            "population": "Adults with type 2 diabetes",
            "intervention": "Real-time continuous glucose monitoring (rtCGM)",
            "comparison": "Self-monitoring of blood glucose (SMBG)",
            "outcomes": ["HbA1c reduction", "Time in range", "Quality of life"],
            "study_types": ["RCT", "Cohort study"],
            "exclusion_criteria": ["Paediatric populations", "Type 1 diabetes only"],
        },
        "abstracts": [
            {
                "pmid": "35421876",
                "title": "Continuous glucose monitoring versus SMBG in T2DM: an RCT",
                "abstract": "Background: ... Methods: ... Results: ... Conclusions: ...",
                "authors": ["Smith JA", "Patel RK"],
                "journal": "Lancet Diabetes & Endocrinology",
                "year": 2022,
            }
        ],
        "export_format": "csv",
    },
    timeout=300,
)
slr = resp.json()
summary = slr["screening_summary"]
print(f"Included: {summary['included']}/{summary['total']}")
```

### JavaScript / TypeScript code examples

```typescript
// Full BIA workflow
const response = await fetch("http://localhost:8000/api/workflows/bia", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    inputs: {
      setting: "ICB",
      model_year: 2026,
      forecast_years: 3,
      funding_source: "ICB commissioning",
      catchment_size: 250000,
      eligible_pct: 7.0,
      uptake_y1: 10.0,
      uptake_y2: 25.0,
      uptake_y3: 40.0,
      workforce: [
        { role: "Band 6 (Senior Nurse / Specialist)", minutes: 45, frequency: "per patient" }
      ],
      price: 850.0,
      outpatient_visits: 4,
      visits_reduced: 20.0,
    },
    intervention_name: "RemoteMonitor Pro",
    enrich_with_evidence: true,
    generate_report: true,
  }),
});

const data = await response.json();
console.log("3-year cumulative impact:",
  data.results.annual_budget_impact.reduce((a: number, b: number) => a + b, 0)
    .toLocaleString("en-GB", { style: "currency", currency: "GBP" })
);
```

---

## 10. Troubleshooting

### API connection issues

**Error: `Cannot connect to HEOR Engine API at http://localhost:8000`**

The API server is not running. Start it with:

```bash
uvicorn app.main:app --reload --port 8000
```

Then check the server is responding:

```bash
curl http://localhost:8000/api/health
```

If you are running the Streamlit demo on a different machine than the API,
change the `API_BASE` constant at the top of `demo/app.py` to the correct
host address.

---

**Error: `Connection refused`**

Check that the port is not already in use:

```bash
lsof -i :8000        # macOS / Linux
netstat -ano | findstr :8000   # Windows
```

If another process is using port 8000, start the API on a different port:

```bash
uvicorn app.main:app --port 8080
```

---

### CEA / R errors

**HTTP 503: `R is not installed. Install R from https://cran.r-project.org/`**

The Cost-Effectiveness Analysis requires R to be installed and `Rscript` to be
available on the system PATH.

1. Download and install R from [cran.r-project.org](https://cran.r-project.org/)
2. Verify installation:
   ```bash
   Rscript --version
   # Expected: R scripting front-end version 4.x.x
   ```
3. If `Rscript` is installed but not on PATH, add the R `bin` directory to your
   system PATH:
   - macOS / Linux: `export PATH="$PATH:/usr/local/bin/R"`
   - Windows: Add `C:\Program Files\R\R-4.x.x\bin` to System Environment Variables

---

**Error: `Rscript: Permission denied`**

On macOS / Linux, ensure the R scripts are executable:

```bash
chmod +x r/markov_model.R
```

---

### Literature screening errors

**HTTP 503: `ANTHROPIC_API_KEY environment variable is not set`**

Set the environment variable before starting the server:

```bash
export ANTHROPIC_API_KEY="your-anthropic-api-key"
uvicorn app.main:app --reload
```

If you set the variable after starting the server, you must restart the server
for the change to take effect.

---

**Screening returns all `uncertain` decisions**

This usually means the abstracts do not contain enough information for the AI
to make a confident decision, or the PICO criteria are too vague. Try:

1. Make the `population`, `intervention`, and `comparison` fields more specific
2. Add explicit `exclusion_criteria` to help the AI distinguish relevant from
   irrelevant studies
3. Provide more complete abstracts (abstract truncation removes the Results
   and Conclusions sections that AI uses most heavily)

---

### Report generation failures

**`report_url` is `null` despite `generate_report: true`**

The workflow status will be `partial` rather than `failed` when the core
analysis succeeds but report generation fails. Check:

1. The `data/reports/` directory exists and is writable:
   ```bash
   mkdir -p data/reports && chmod 755 data/reports
   ```
2. The `python-pptx` package is installed:
   ```bash
   pip show python-pptx
   ```
3. Re-run with a different `report_format` (try `"docx"` if `"pptx"` fails)

---

**PowerPoint file opens but shows formatting errors**

This can occur with older versions of `python-pptx`. Update to the latest:

```bash
pip install --upgrade python-pptx
```

---

### When results seem wrong

**Break-even year is Year 1 but the device costs more than current care**

Check that `uptake_y1` is not set too high. If only 5% of eligible patients
are treated in Year 1 but savings per patient are large, cumulative savings
can exceed the small Year-1 device spend. This may be correct — verify by
checking `total_treated_patients[0]` and `cost_per_patient[0]`.

---

**ICER is extremely low (e.g. £500/QALY)**

Very low ICERs are often caused by:
- `utility_gain` entered as an absolute value but intended as a percentage
  (e.g. `0.50` instead of `0.05`)
- `prob_death_treatment` set much lower than the baseline — check the
  clinical evidence supports the size of the mortality benefit
- `time_horizon` set to 50 years, greatly amplifying QALY accrual

---

**Annual budget impact is positive (cost) when you expected a saving**

The net impact equals `(new pathway cost − current pathway cost) × treated patients`.
If the new pathway cost exceeds the current pathway cost, the result is positive.
Common reasons:
- The device price is high relative to current pathway savings
- Savings percentages are low — increase `visits_reduced` or `complications_reduced`
  if clinical evidence supports it
- The current pathway has low resource use — a simple, low-cost condition may not
  generate enough savings to offset device costs

---

### Common validation errors (HTTP 422)

| Error message | Fix |
|---|---|
| `"setting is required"` | Add `"setting": "ICB"` (or `"Acute NHS Trust"` or `"Primary Care Network"`) |
| `"workforce must have at least 1 row"` | Add at least one entry to the `workforce` array |
| `"eligible_pct must be between 0 and 100"` | Check your eligible percentage is expressed as a % (7.0 not 0.07) |
| `"uptake values must be between 0 and 100"` | As above — use percentage, not decimal |
| `"prob_death_standard must be between 0 and 1"` | Use decimal (0.05 for 5%), not percentage |
| `"utility values must be between 0 and 1"` | EQ-5D utility is always 0–1 |
| `"outcomes must contain at least 1 item"` | Add at least one entry to the `outcomes` list in PICO |
| `"intervention_name is required"` | The CEA and quick-estimate endpoints require an intervention name |

---

### Getting support

- **API documentation (interactive):** `http://localhost:8000/docs`
- **Alternative docs (ReDoc):** `http://localhost:8000/redoc`
- **Methodology references:** `docs/calculation_methodology.md`,
  `docs/markov_methodology.md`, `docs/evidence_sources.md`
- **GitHub issues:** Submit bug reports and feature requests via the repository
  issue tracker

---

## Appendix A: Workflow ID format

Workflow IDs follow the pattern:

```
{type}_{YYYYMMDD}_{HHMMSS}_{8-char-uuid}
```

Examples:
- `bia_20260226_141500_a3f8b2c1`
- `cea_20260226_141523_d9e2f4a7`
- `combined_20260226_141601_b5c8e1f2`
- `slr_20260226_142015_g7h3j9k1`

Workflow state is persisted to disk under `data/workflows/` and can be
retrieved at any time via `GET /api/workflows/{workflow_id}`.

---

## Appendix B: Quick reference — key numbers

| Parameter | Value | Source |
|---|---|---|
| NICE standard WTP threshold | £25,000/QALY | NICE PMG36, 2022 |
| NICE extended WTP threshold | £35,000/QALY | NICE PMG36, 2022 |
| NICE end-of-life WTP threshold | Up to £50,000/QALY | NICE PMG36, 2022 |
| NICE discount rate (costs & QALYs) | 3.5% per annum | NICE reference case |
| NHS general ward bed day | £400 | NHS NCC 2024/25 |
| Outpatient first attendance | £120 | NHS NCC 2024/25 |
| Outpatient follow-up | £85 | NHS NCC 2024/25 |
| Average AfC hourly rate | £28.62 | PSSRU 2024/25 |
| England population | 57,000,000 | ONS 2024 |
| Diabetes prevalence (England) | 6.8% | NHS QOF 2024 |
| Bed-to-population planning ratio | 1:500 | NHS planning heuristic |

---

## Appendix C: Glossary

| Term | Definition |
|---|---|
| **AfC** | Agenda for Change — the NHS pay framework for non-medical staff |
| **BIA** | Budget Impact Analysis — quantifies financial impact on an NHS budget |
| **CEA** | Cost-Effectiveness Analysis — compares cost to health outcomes (QALYs) |
| **EQ-5D** | EuroQol 5-Dimension questionnaire — the standard NICE utility measure |
| **HES** | Hospital Episode Statistics — NHS dataset of inpatient/outpatient activity |
| **HEOR** | Health Economics and Outcomes Research |
| **HST** | Highly Specialised Technology — NICE programme for ultra-rare diseases |
| **HTA** | Health Technology Assessment — formal evaluation of a technology |
| **ICER** | Incremental Cost-Effectiveness Ratio (£/QALY) |
| **ICB** | Integrated Care Board — NHS commissioning body covering a regional population |
| **MIB** | Medtech Innovation Briefing — NICE horizon scanning for devices/diagnostics |
| **MTG** | Medical Technologies Guidance — NICE guidance type for medical devices |
| **NCC** | NHS National Cost Collection — annual reference cost dataset |
| **NICE** | National Institute for Health and Care Excellence |
| **NG** | NICE Guideline — clinical practice recommendation |
| **ONS** | Office for National Statistics |
| **PICO** | Population, Intervention, Comparison, Outcomes — research question framework |
| **PSA** | Probabilistic Sensitivity Analysis — Monte Carlo uncertainty quantification |
| **QoL** | Quality of Life |
| **QALY** | Quality-Adjusted Life Year — one year in perfect health = 1.0 QALY |
| **QOF** | Quality and Outcomes Framework — NHS primary care performance database |
| **SLR** | Systematic Literature Review |
| **TA** | Technology Appraisal — NICE's most binding guidance type |
| **WTP** | Willingness to Pay — the maximum cost per QALY a payer will accept |

---

## References

1. NICE (2022). *NICE health technology evaluations: the manual.* Process and
   methods [PMG36]. National Institute for Health and Care Excellence.

2. NICE (2017). *Developing NICE guidelines: the manual.* Section 7:
   Incorporating economic evaluation. [PMG20].

3. Briggs A, Claxton K, Sculpher M (2006). *Decision Modelling for Health
   Economic Evaluation.* Oxford University Press.

4. Drummond MF, Sculpher MJ, Claxton K, et al. (2015). *Methods for the
   Economic Evaluation of Health Care Programmes.* 4th ed. Oxford University
   Press.

5. Sullivan SD, Mauskopf JA, Augustovski F, et al. (2014). Budget impact
   analysis — principles of good practice: report of the ISPOR 2012 Budget
   Impact Analysis Good Practice II Task Force. *Value in Health*, 17(1):5–14.

6. NHS England (2024). *National Cost Collection 2024/25.*
   https://www.england.nhs.uk/national-cost-collection/

7. Office for National Statistics (2024). *Mid-Year Population Estimates 2024.*
   https://www.ons.gov.uk/

8. NHS Digital (2024). *Quality and Outcomes Framework (QOF) 2023/24.*
   https://digital.nhs.uk/data-and-information/publications/statistical/quality-and-outcomes-framework

---

*This guide is maintained alongside the HEOR Engine codebase. For corrections or
additions, submit a pull request to the repository.*
