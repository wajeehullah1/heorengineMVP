/**
 * autoFill.js — Evidence-based BIA auto-population utilities.
 *
 * Responsible for:
 *   1. Posting a task to the backend's /api/auto-populate/bia endpoint.
 *   2. Polling /api/auto-populate/status/{task_id} until the task completes.
 *   3. Mapping the snake_case BIAInputs response to the camelCase form state
 *      used by HEORInputEngine.
 *
 * Usage:
 *   import { handleAutoFill } from "../utils/autoFill";
 *
 *   const result = await handleAutoFill(
 *     { deviceName, indication, cost, benefits },
 *     (status, step) => console.log(status, step)
 *   );
 *   // result: { formData, sources, confidenceScores, warnings, evidenceSummary }
 */

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

const POLL_INTERVAL_MS = 4000;
const MAX_POLL_ATTEMPTS = 50; // 50 × 4 s ≈ 3.3 minutes

// Human-readable labels shown during BIA polling
export const PROGRESS_LABELS = {
  queued:     "Queued — waiting to start...",
  searching:  "Searching PubMed and NICE guidance...",
  extracting: "Extracting clinical data from abstracts...",
  populating: "Synthesising evidence into BIA inputs...",
  complete:   "Done",
  failed:     "Failed",
};

// Human-readable labels shown during Markov/CEA polling
export const MARKOV_PROGRESS_LABELS = {
  queued:     "Queued — waiting to start...",
  searching:  "Gathering clinical evidence for Markov model...",
  extracting: "Extracting mortality, utility, and cost data...",
  populating: "Deriving Markov parameters from evidence...",
  complete:   "Done",
  failed:     "Failed",
};

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Run the full auto-fill flow: submit → poll → map → return.
 *
 * @param {object} inputs
 *   @param {string} inputs.deviceName
 *   @param {string} inputs.indication
 *   @param {string|number} inputs.cost          - per-patient cost in GBP
 *   @param {string} [inputs.benefits]           - free-text expected benefits
 *   @param {string} [inputs.setting]            - NHS setting string
 *   @param {number} [inputs.forecastYears]
 *   @param {number} [inputs.modelYear]
 *
 * @param {function} [onProgress]  - (status: string, step: string) => void
 * @param {object}   [signal]      - AbortController signal for cancellation
 *
 * @returns {Promise<AutoFillResult>}
 *
 * @throws {Error} on network failure, server error, or timeout
 */
export async function handleAutoFill(inputs, onProgress, signal) {
  const {
    deviceName,
    indication,
    cost,
    benefits = "",
    setting = "Acute NHS Trust",
    forecastYears = 3,
    modelYear = new Date().getFullYear(),
  } = inputs;

  // ── Step 1: Kick off the background task ─────────────────────────────────
  const submitRes = await fetch(`${API_BASE}/api/auto-populate/bia`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal,
    body: JSON.stringify({
      device_name:             deviceName,
      indication,
      setting,
      device_cost_per_patient: parseFloat(cost) || 0,
      expected_benefits:       benefits,
      forecast_years:          forecastYears,
      model_year:              modelYear,
    }),
  });

  if (!submitRes.ok) {
    const err = await submitRes.json().catch(() => ({}));
    throw new Error(
      err.detail ??
        `Server returned ${submitRes.status} — ${submitRes.statusText}`
    );
  }

  const { task_id, poll_url } = await submitRes.json();

  // ── Step 2: Poll until complete ───────────────────────────────────────────
  for (let attempt = 0; attempt < MAX_POLL_ATTEMPTS; attempt++) {
    await sleep(POLL_INTERVAL_MS, signal);

    const statusRes = await fetch(`${API_BASE}${poll_url}`, { signal });
    if (!statusRes.ok) continue; // transient error — keep polling

    const statusData = await statusRes.json();

    onProgress?.(
      statusData.status,
      PROGRESS_LABELS[statusData.status] ?? statusData.step ?? "Working..."
    );

    if (statusData.status === "complete") {
      return buildResult(statusData.result ?? {});
    }

    if (statusData.status === "failed") {
      throw new Error(
        statusData.error ?? "Evidence gathering failed on the server."
      );
    }
  }

  throw new Error(
    "Timed out waiting for evidence (>3 minutes). " +
      "Please try again or fill the form manually."
  );
}

// ---------------------------------------------------------------------------
// Mapping: API snake_case BIAInputs → camelCase form state
// ---------------------------------------------------------------------------

/**
 * Convert a raw BIAInputs dict (snake_case, from the API) into the camelCase
 * form state shape expected by HEORInputEngine.
 *
 * @param {object} bia  - raw API bia_inputs dict
 * @returns {object}    - camelCase form state (partial, safe to spread)
 */
export function mapApiToForm(bia) {
  if (!bia || typeof bia !== "object") return {};

  const s = (v) => (v !== undefined && v !== null ? String(v) : "");

  const mapped = {
    setting:              bia.setting              ?? "",
    modelYear:            bia.model_year           ?? new Date().getFullYear(),
    forecastYears:        s(bia.forecast_years     ?? "3"),
    fundingSource:        bia.funding_source        ?? "",
    catchmentType:        bia.catchment_type        ?? "population",
    catchmentSize:        s(bia.catchment_size      ?? ""),
    eligiblePct:          s(bia.eligible_pct        ?? ""),
    uptakeY1:             s(bia.uptake_y1           ?? ""),
    uptakeY2:             s(bia.uptake_y2           ?? ""),
    uptakeY3:             s(bia.uptake_y3           ?? ""),
    prevalence:           bia.prevalence            ?? "",
    outpatientVisits:     s(bia.outpatient_visits   ?? ""),
    tests:                s(bia.tests               ?? ""),
    admissions:           s(bia.admissions          ?? ""),
    bedDays:              s(bia.bed_days            ?? ""),
    procedures:           s(bia.procedures          ?? ""),
    consumables:          s(bia.consumables         ?? ""),
    pricingModel:         bia.pricing_model         ?? "per-patient",
    price:                s(bia.price               ?? ""),
    priceUnit:            bia.price_unit            ?? "per year",
    trainingRequired:     bia.needs_training        ? "yes" : "no",
    trainingRoles:        bia.training_roles        ?? "",
    trainingHours:        s(bia.training_hours      ?? ""),
    setupCost:            s(bia.setup_cost          ?? ""),
    staffTimeSaved:       s(bia.staff_time_saved    ?? ""),
    visitsReduced:        s(bia.visits_reduced      ?? ""),
    complicationsReduced: s(bia.complications_reduced ?? ""),
    readmissionsReduced:  s(bia.readmissions_reduced  ?? ""),
    losReduced:           s(bia.los_reduced         ?? ""),
    followUpReduced:      s(bia.follow_up_reduced   ?? ""),
    comparator:           bia.comparator            ?? "none",
    comparatorNames:      bia.comparator_names      ?? "",
    discounting:          bia.discounting           ?? "off",
  };

  // Workforce rows — add stable id for React keys
  if (Array.isArray(bia.workforce) && bia.workforce.length > 0) {
    mapped.workforce = bia.workforce.map((row, i) => ({
      role:      row.role      ?? "Band 5 (Staff Nurse)",
      minutes:   s(row.minutes ?? ""),
      frequency: row.frequency ?? "per patient",
      id:        Date.now() + i,
    }));
  }

  return mapped;
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/**
 * Build the AutoFillResult from the raw task result payload.
 * @param {object} raw - statusData.result
 * @returns {AutoFillResult}
 */
function buildResult(raw) {
  return {
    formData:        mapApiToForm(raw.bia_inputs ?? {}),
    rawBiaInputs:    raw.bia_inputs        ?? {},
    sources:         raw.sources           ?? [],
    confidenceScores: raw.confidence_scores ?? {},
    warnings:        raw.warnings          ?? [],
    assumptions:     raw.assumptions       ?? [],
    evidenceSummary: raw.evidence_summary  ?? {},
  };
}

function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    const id = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(id);
      reject(new DOMException("Auto-fill cancelled.", "AbortError"));
    });
  });
}

/**
 * @typedef {object} AutoFillResult
 * @property {object} formData         - camelCase form state (spread into HEORInputEngine form)
 * @property {object} rawBiaInputs     - original snake_case API response
 * @property {Array}  sources          - evidence sources (PubMed + NICE)
 * @property {object} confidenceScores - per-field confidence: high | medium | low
 * @property {Array}  warnings         - data quality warnings
 * @property {Array}  assumptions      - explicit assumptions made
 * @property {object} evidenceSummary  - { papers_found, nice_guidance_found, data_quality }
 */

// ---------------------------------------------------------------------------
// Markov / CEA auto-fill
// ---------------------------------------------------------------------------

/**
 * Run the Markov auto-fill flow: submit → poll → map → return.
 *
 * @param {object} inputs
 *   @param {string} inputs.deviceName
 *   @param {string} inputs.indication
 *   @param {string|number} inputs.cost  - device cost per patient in GBP
 *
 * @param {function} [onProgress]  - (status: string, step: string) => void
 * @param {object}   [signal]      - AbortController signal for cancellation
 *
 * @returns {Promise<MarkovAutoFillResult>}
 */
export async function handleMarkovAutoFill(inputs, onProgress, signal) {
  const { deviceName, indication, cost } = inputs;

  const submitRes = await fetch(`${API_BASE}/api/auto-populate/markov`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal,
    body: JSON.stringify({
      device_name: deviceName,
      indication,
      // Providing minimal bia_inputs lets the backend skip a full BIA run
      // and derive Markov parameters directly — faster and more focused.
      bia_inputs: {
        intervention_name: deviceName,
        price:             parseFloat(cost) || 0,
        setup_cost:        0,
      },
    }),
  });

  if (!submitRes.ok) {
    const err = await submitRes.json().catch(() => ({}));
    throw new Error(err.detail ?? `Server returned ${submitRes.status} — ${submitRes.statusText}`);
  }

  const { task_id, poll_url } = await submitRes.json();

  for (let attempt = 0; attempt < MAX_POLL_ATTEMPTS; attempt++) {
    await sleep(POLL_INTERVAL_MS, signal);

    const statusRes = await fetch(`${API_BASE}${poll_url}`, { signal });
    if (!statusRes.ok) continue;

    const statusData = await statusRes.json();

    onProgress?.(
      statusData.status,
      MARKOV_PROGRESS_LABELS[statusData.status] ?? statusData.step ?? "Working..."
    );

    if (statusData.status === "complete") {
      return buildMarkovResult(statusData.result ?? {});
    }
    if (statusData.status === "failed") {
      throw new Error(statusData.error ?? "Markov parameter derivation failed on the server.");
    }
  }

  throw new Error(
    "Timed out waiting for Markov parameters (>3 minutes). " +
      "Please try again or fill the form manually."
  );
}

/**
 * Convert a raw markov_inputs dict (snake_case, from the API) into the
 * camelCase form state shape expected by MarkovICERForm.
 *
 * @param {object} m  - raw API markov_inputs dict
 * @returns {object}  - camelCase form state (partial, safe to spread)
 */
export function mapMarkovApiToForm(m) {
  if (!m || typeof m !== "object") return {};

  // Serialize numeric values; decimals controls precision for radio-matching
  const s = (v, decimals) => {
    if (v === undefined || v === null || v === "") return "";
    const n = Number(v);
    if (isNaN(n)) return "";
    return decimals !== undefined ? n.toFixed(decimals) : String(n);
  };

  return {
    interventionName:     m.intervention_name      ?? "",
    timeHorizon:          s(m.time_horizon),           // "5", "10" — matches radio values
    discountRate:         s(m.discount_rate, 3),        // "0.035" — matches radio values
    probDeathStandard:    s(m.prob_death_standard, 4),
    costStandardAnnual:   s(m.cost_standard_annual),
    utilityStandard:      s(m.utility_standard, 2),
    probDeathTreatment:   s(m.prob_death_treatment, 4),
    costTreatmentAnnual:  s(m.cost_treatment_annual),
    costTreatmentInitial: s(m.cost_treatment_initial),
    utilityTreatment:     s(m.utility_treatment, 2),
    // Leave quick-calc helpers blank — AI already set derived values directly
    conditionPreset:       "",
    mortalityReductionPct: "",
    utilityGainPct:        "",
  };
}

/**
 * Build a MarkovAutoFillResult from the raw poll-complete payload.
 * @param {object} raw - statusData.result from the Markov poll endpoint
 * @returns {MarkovAutoFillResult}
 */
function buildMarkovResult(raw) {
  const quality = raw.confidence_scores?.overall ?? "medium";
  return {
    formData:         mapMarkovApiToForm(raw.markov_inputs ?? {}),
    rawMarkovInputs:  raw.markov_inputs      ?? {},
    derivationNotes:  raw.derivation_notes   ?? [],
    confidenceScores: raw.confidence_scores  ?? {},
    warnings:         raw.warnings           ?? [],
    assumptions:      raw.assumptions        ?? [],
    sources:          [],   // Markov endpoint does not return source citations
    evidenceSummary: {
      papers_found:        0,
      nice_guidance_found: 0,
      data_quality:        quality,
    },
  };
}

/**
 * @typedef {object} MarkovAutoFillResult
 * @property {object} formData          - camelCase form state for MarkovICERForm
 * @property {object} rawMarkovInputs   - original snake_case API response
 * @property {Array}  derivationNotes   - how each parameter was derived
 * @property {object} confidenceScores  - per-field confidence: high | medium | low
 * @property {Array}  warnings          - data quality warnings
 * @property {Array}  assumptions       - explicit assumptions made
 * @property {Array}  sources           - [] (Markov endpoint does not return citations)
 * @property {object} evidenceSummary   - { papers_found, nice_guidance_found, data_quality }
 */
