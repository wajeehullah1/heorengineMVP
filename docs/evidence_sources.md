# Evidence Sources Reference

This document describes every external data source used by the HEOR Engine's
evidence layer (`agents/evidence_agent.py`) and the API endpoints that expose it.

---

## 1. NHS National Cost Collection 2024/25

### What it is
The NHS National Cost Collection (NCC) is published annually by NHS England. It
contains reference unit costs for secondary care activity: inpatient stays,
outpatient attendances, emergency department attendances, diagnostic tests,
procedures, and ambulance services.

### What we use
37 curated unit cost items across seven categories:

| Category | Items | Examples |
|---|---|---|
| Inpatient | 7 | Bed day: general medicine £450, ICU £1,850 |
| Outpatient | 5 | First consultant attendance £135, follow-up £92 |
| Emergency | 3 | Minor injury £145, resuscitation £420 |
| Diagnostics | 10 | Basic blood test £3.50, MRI scan £168 |
| Procedures | 5 | Minor theatre hour £850, endoscopy £385 |
| Ambulance | 3 | Conveyance £275, hear-and-treat £28 |
| Community | 4 | District nurse visit £48, physiotherapy £38 |

### Where to find the source
- NHS England National Cost Collection:
  https://www.england.nhs.uk/national-cost-collection/

### Update frequency
Annually (financial year, published ~18 months in arrears). The 2024/25
collection covers activity from April 2024 – March 2025.

### How the engine uses it
- `fetch_nhs_reference_costs()` — returns all 37 items with metadata
- `search_reference_costs(query)` — keyword search across cost names
- `get_cost_by_category(category)` — filter by one of the seven categories
- `enrich_bia_inputs()` — compares user-supplied costs against reference
  values and flags divergences > 50%
- `POST /api/evidence/reference-costs` — REST access
- `POST /api/suggest-defaults` — provides `typical_pathway_cost`

### Limitations and caveats
- **National averages only.** Individual NHS trusts may pay substantially
  more or less. High-cost London trusts and specialist centres often exceed
  the national average by 20–40%.
- **Tariff ≠ actual cost.** The NCC reflects activity-weighted average costs,
  not the price commissioners pay under the National Tariff.
- **Community costs are NHS reference, not PSSRU.** For community nursing and
  allied health professionals, the PSSRU Unit Costs of Health and Social Care
  (University of Kent) is the authoritative source. Use NHS reference costs as
  a cross-check only.
- **No inflation adjustment.** Costs are in the price base of the collection
  year. Apply the NHS Pay Award and GDP deflator for forward projections.

---

## 2. ONS Mid-Year Population Estimates 2024

### What it is
The Office for National Statistics (ONS) Mid-Year Population Estimates provide
authoritative counts of the UK resident population, broken down by nation,
region, and single-year age band.

### What we use

| Data | Value |
|---|---|
| UK total | 67,800,000 |
| England | 57,000,000 |
| Nine NHS England regions | Individual counts (2,650,000 – 9,280,000) |
| 18 ONS age bands | 0–4 through 85+ |
| 9 disease prevalence rates | Diabetes 6.8 %, hypertension 28 %, etc. |

The prevalence rates are sourced from NHS Digital's Quality and Outcomes
Framework (QOF) and disease-specific registers, not directly from ONS.

### Where to find the source
- ONS Mid-Year Population Estimates:
  https://www.ons.gov.uk/peoplepopulationandcommunity/populationandmigration/populationestimates
- NHS Digital QOF prevalence:
  https://digital.nhs.uk/data-and-information/publications/statistical/quality-and-outcomes-framework

### Update frequency
- Population estimates: annually (mid-year, published ~12 months later)
- QOF prevalence: annually with the financial year

### How the engine uses it
- `fetch_ons_population_data()` — returns full dataset
- `get_population_by_region(region)` — single-region lookup with aliases
- `estimate_eligible_population(pop, condition, age_band)` — applies
  prevalence to a catchment population, with optional age-band scaling
- `calculate_catchment_from_beds(beds, occupancy)` — bed-to-population
  heuristic (1 bed : 500 population, adjusted for occupancy)
- `enrich_bia_inputs()` — suggests `eligible_pct` when absent
- `POST /api/evidence/population` — REST access
- `POST /api/suggest-defaults` — provides `eligible_pct` and regional warnings

### Limitations and caveats
- **National prevalence ≠ local prevalence.** Disease rates vary meaningfully
  by region. Diabetes prevalence ranges from ~5.9 % (South West) to ~7.8 %
  (West Midlands). Use local QOF data for catchment-specific estimates.
- **Registered patients ≠ resident population.** GP-registered populations
  sometimes exceed census counts (patient mobility, temporary residents).
- **Age-band scaling is approximate.** The engine applies national age
  distributions to local populations; this is a reasonable first approximation
  but may not reflect the age profile of a specific catchment.
- **Bed-to-population ratio is a rule of thumb.** The 1:500 ratio is a
  widely-cited NHS planning heuristic; it can vary from 1:300 (specialist
  centres) to 1:700 (district generals with primary care referral).

---

## 3. NICE Guidance Database (Curated MVP)

### What it is
The National Institute for Health and Care Excellence (NICE) publishes guidance
that determines NHS reimbursement decisions in England. The four main
document types relevant to HEOR are:

| Type | Description |
|---|---|
| Technology Appraisal (TA) | Formal cost-effectiveness assessment; binding for NHS commissioning |
| NICE Guideline (NG) | Clinical practice recommendations |
| Medtech Innovation Briefing (MIB) | Non-binding horizon scanning for devices and diagnostics |
| Diagnostics Guidance (DG) | Evidence review for diagnostic technologies |
| Highly Specialised Technology (HST) | Ultra-rare diseases; separate WTP threshold |

### What we use
15 curated records (MVP subset) covering:

- AI and digital health tools (TA123, MIB298, MIB312)
- Remote monitoring (NG185, MIB234, NG206)
- Diabetes management (TA894, NG28)
- Metabolic and cardiometabolic interventions (TA878, NG136)
- Oncology (TA548, DG61)
- Dementia / neurology (TA902)
- Rare diseases (HST18)
- Vascular access (DG56)

Each record includes: NICE ID, title, document type, publication date,
condition, intervention type tags, comparator list or recommendations,
ICER where published, and NICE decision.

### Where to find the source
- NICE guidance search: https://www.nice.org.uk/guidance
- NICE evidence search: https://www.nice.org.uk/evidence

### Update frequency
NICE publishes new guidance continuously. Technology Appraisals are reviewed
every 3–5 years. The curated MVP database should be reviewed and extended
**at least annually**, or when a new appraisal is published for a relevant
condition.

### How the engine uses it
- `search_nice_guidance(term, guidance_type)` — keyword and type search
- `get_nice_comparators(condition, intervention_type)` — returns NICE-approved
  alternatives for use in BIA/CEA comparator selection
- `get_nice_threshold_context(condition)` — returns the applicable WTP
  threshold band and precedent ICERs
- `enrich_bia_inputs()` — populates `comparators` and clinical context
- `validate_against_references()` — checks submitted ICER against precedent
  range and applicable threshold
- `POST /api/evidence/nice-guidance` — REST access
- `POST /api/suggest-defaults` — provides `relevant_nice_guidance` and
  `comparator_tools`

### NICE Willingness-to-Pay Thresholds

| Band | Threshold | Applies when |
|---|---|---|
| Standard | £25,000 – £35,000 / QALY | Default for all conditions |
| End of life | £35,000 – £50,000 / QALY | Remaining life < 24 months AND ≥ 3 months extension |
| Highly Specialised | £100,000 – £300,000 / QALY | HST programme, ultra-rare (< 1:50,000) |
| Rare disease | £25,000 – £100,000 / QALY | Orphan designation or Innovative Medicines Fund |

These thresholds are encoded in `_WTP_THRESHOLDS` and used by
`get_nice_threshold_context()` and `validate_against_references()`.

### Limitations and caveats
- **MVP database is not exhaustive.** Only 15 records are included. Real
  submissions should search the full NICE guidance library.
- **ICERs are point estimates.** NICE submissions contain probabilistic
  sensitivity analyses; the published ICER is the deterministic base case.
- **Comparators may be out of date.** Standard of care evolves; always verify
  that the comparator list reflects current NHS practice.
- **MIBs are not recommendations.** Medtech Innovation Briefings present
  evidence but do not constitute a NICE recommendation for commissioning.
- **Threshold criteria must be checked.** The end-of-life uplift has specific
  eligibility criteria that must be assessed on a case-by-case basis.

---

## 4. Workforce Cost Benchmarks

### What it is
Per-patient workforce time estimates derived from the resource-use sections of
NICE Technology Appraisals and Medtech Innovation Briefings for digital and
device-based interventions.

### What we use

| Intervention type | Role | Setup (min) | Follow-up (min) | Source |
|---|---|---|---|---|
| digital | Band 5 Nurse | 30 | 15 | NICE NG28 / MIB234 |
| remote_monitoring | Band 6 Senior Nurse | 45 | 20 | NICE NG185 / MIB234 |
| diagnostic | Band 5 Nurse | 20 | 10 | NICE DG56 / DG61 |
| ai | Admin/Clerical | 15 | 5 | NICE TA123 |
| pharmaceutical | Consultant | 30 | 20 | NICE TA894 / TA878 |

Staff costs are calculated using NHS Agenda for Change (AfC) band rates from
the 2024/25 pay agreement.

### Limitations and caveats
- **Benchmarks are illustrative.** Actual time will vary by trust, workflow
  maturity, and patient complexity.
- **Only covers initial setup and structured follow-up.** Unplanned contacts,
  alerts, and escalation time are not included.
- **AfC rates are national averages** and do not include on-costs (employer
  NI ~13.8 %, pension ~20.9 %, overheads). Apply a multiplier of ~1.35–1.55
  for full economic cost.

---

## 5. Caching Behaviour

All reference datasets are cached as JSON files in `data/reference/` and
served from cache when less than 30 days old. This avoids redundant computation
and allows offline use.

| Cache key | File | TTL |
|---|---|---|
| `nhs_reference_costs` | `nhs_reference_costs.json` | 30 days |
| `ons_population_data` | `ons_population_data.json` | 30 days |
| `nice_guidance_db` | `nice_guidance_db.json` | 30 days |

Each entry has a companion `<name>.meta.json` file containing the
`fetched_at` timestamp. Cache entries can be cleared with:

```python
from agents.evidence_agent import EvidenceCache
EvidenceCache().clear()          # clear all
EvidenceCache().clear("nhs_reference_costs")  # clear one entry
```

---

## 6. Intended Use and Disclaimer

The reference data provided by this module is intended to support **early-stage
economic modelling and health technology assessment** within the HEOR Engine.
It is **not** a substitute for:

- A formal NHS costing exercise using local patient-level data
- A NICE-compliant economic model with probabilistic sensitivity analysis
- Official ONS or NHS Digital data for regulatory or published submissions

All values should be reviewed by a qualified health economist before use in a
formal HTA dossier or commissioner submission. Cost estimates are point values
and carry uncertainty; ranges and sensitivity analyses should always be reported
alongside central estimates.
