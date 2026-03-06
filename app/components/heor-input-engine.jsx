import { useState, useRef, useEffect } from "react";

const SECTIONS = ["Setting & Scope", "Target Population", "Current Pathway", "Intervention & Pricing"];
const SECTION_ICONS = ["⚙️", "👥", "🏥", "💊"];

const BAND_RATES = {
  "Band 2": 12.45,
  "Band 3": 14.28,
  "Band 4": 16.83,
  "Band 5 (Staff Nurse)": 21.37,
  "Band 6 (Senior Nurse/AHP)": 26.54,
  "Band 7 (Advanced Practitioner)": 32.11,
  "Band 8a (Consultant Nurse/Manager)": 40.22,
  "Registrar": 38.50,
  "Consultant": 72.00,
  "Admin/Clerical": 11.90,
};

const defaultWorkforceRow = () => ({ role: "Band 5 (Staff Nurse)", minutes: "", frequency: "per patient", id: Date.now() + Math.random() });

function loadJsPDF(callback) {
  if (window.jspdf) { callback(window.jspdf.jsPDF); return; }
  const script = document.createElement("script");
  script.src = "https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js";
  script.onload = () => callback(window.jspdf.jsPDF);
  document.head.appendChild(script);
}

function generatePDF(form, totalHourlyCost) {
  loadJsPDF((JsPDF) => {
    const doc = new JsPDF({ orientation: "portrait", unit: "mm", format: "a4" });
    const W = 210, M = 18, CW = W - M * 2;
    let y = 0;

    const navy = [13, 32, 68];
    const green = [45, 106, 79];
    const greenLight = [82, 183, 136];
    const white = [255, 255, 255];
    const offWhite = [245, 248, 252];
    const midGrey = [120, 140, 170];
    const darkText = [20, 35, 60];
    const lineGrey = [210, 220, 232];

    // Header banner
    doc.setFillColor(...navy);
    doc.rect(0, 0, W, 42, "F");
    doc.setFillColor(...green);
    doc.roundedRect(M, 11, 14, 14, 2, 2, "F");
    doc.setTextColor(...white);
    doc.setFontSize(9);
    doc.setFont("helvetica", "bold");
    doc.text("HE", M + 2.5, 20);
    doc.setTextColor(...white);
    doc.setFontSize(18);
    doc.setFont("helvetica", "bold");
    doc.text("HEOR Engine", M + 20, 19);
    doc.setFontSize(8);
    doc.setFont("helvetica", "normal");
    doc.setTextColor(...greenLight);
    doc.text("INPUT COLLECTION SUMMARY", M + 20, 27);
    doc.setTextColor(...midGrey);
    doc.setFontSize(7);
    const dateStr = new Date().toLocaleDateString("en-GB", { day: "2-digit", month: "long", year: "numeric" });
    doc.text(`Generated: ${dateStr}  |  NHS Payer Perspective  |  Budget Impact Analysis`, M + 20, 35);
    doc.setTextColor(...greenLight);
    doc.text(`Model FY${form.modelYear} — ${form.forecastYears}-Year Horizon`, W - M, 35, { align: "right" });

    y = 52;

    const checkPage = (needed = 20) => {
      if (y + needed > 275) {
        doc.addPage();
        y = 18;
      }
    };

    const sectionHeader = (title) => {
      checkPage(16);
      doc.setFillColor(...green);
      doc.rect(M, y, CW, 9, "F");
      doc.setTextColor(...white);
      doc.setFontSize(8);
      doc.setFont("helvetica", "bold");
      doc.text(title.toUpperCase(), M + 4, y + 6);
      y += 13;
    };

    const dataRow = (label, value, shade) => {
      checkPage(9);
      if (shade) { doc.setFillColor(...offWhite); doc.rect(M, y, CW, 8, "F"); }
      doc.setTextColor(...midGrey);
      doc.setFontSize(7.5);
      doc.setFont("helvetica", "normal");
      doc.text(label, M + 3, y + 5.5);
      doc.setTextColor(...darkText);
      doc.setFont("helvetica", "bold");
      doc.text(String(value || "—"), M + 85, y + 5.5);
      doc.setDrawColor(...lineGrey);
      doc.setLineWidth(0.2);
      doc.line(M, y + 8, M + CW, y + 8);
      y += 8;
    };

    const subHead = (title) => {
      checkPage(10);
      y += 3;
      doc.setTextColor(...green);
      doc.setFontSize(7.5);
      doc.setFont("helvetica", "bold");
      doc.text(title, M + 3, y);
      doc.setDrawColor(...greenLight);
      doc.setLineWidth(0.3);
      doc.line(M, y + 2, M + CW, y + 2);
      y += 7;
    };

    // Section 1
    sectionHeader("1  Setting & Scope");
    dataRow("NHS Setting", form.setting, false);
    dataRow("Model Start Year", String(form.modelYear), true);
    dataRow("Forecast Horizon", `${form.forecastYears} years`, false);
    dataRow("Funding Route", form.fundingSource, true);
    y += 6;

    // Section 2
    sectionHeader("2  Target Population & Uptake");
    const eligibleN = Math.round((parseFloat(form.catchmentSize) || 0) * (parseFloat(form.eligiblePct) || 0) / 100);
    const treatedY1 = Math.round(eligibleN * (parseFloat(form.uptakeY1) || 0) / 100);
    const treatedY2 = Math.round(eligibleN * (parseFloat(form.uptakeY2) || 0) / 100);
    const treatedY3 = Math.round(eligibleN * (parseFloat(form.uptakeY3) || 0) / 100);
    dataRow("Catchment Measure", form.catchmentType === "beds" ? "Trust beds" : "Population served", false);
    dataRow("Catchment Size", form.catchmentSize ? Number(form.catchmentSize).toLocaleString() : "—", true);
    dataRow("Eligible Patients (% of catchment)", form.eligiblePct ? `${form.eligiblePct}%` : "—", false);
    dataRow("Estimated Eligible Cohort", eligibleN ? `${eligibleN.toLocaleString()} patients` : "—", true);
    dataRow("Uptake — Year 1 / Year 2 / Year 3", `${form.uptakeY1 || 0}% / ${form.uptakeY2 || 0}% / ${form.uptakeY3 || 0}%`, false);
    dataRow("Treated Patients — Year 1", treatedY1 ? treatedY1.toLocaleString() : "—", true);
    dataRow("Treated Patients — Year 2", treatedY2 ? treatedY2.toLocaleString() : "—", false);
    dataRow("Treated Patients — Year 3", treatedY3 ? treatedY3.toLocaleString() : "—", true);
    if (form.prevalence) dataRow("Prevalence / Incidence Notes", form.prevalence, false);
    y += 6;

    // Section 3 — workforce
    sectionHeader("3  Current Pathway");
    subHead("Workforce");

    // Table header
    doc.setFillColor(...navy);
    doc.rect(M, y, CW, 7.5, "F");
    doc.setTextColor(...white);
    doc.setFontSize(7);
    doc.setFont("helvetica", "bold");
    doc.text("Role", M + 3, y + 5.2);
    doc.text("Mins/Patient", M + 85, y + 5.2);
    doc.text("Frequency", M + 120, y + 5.2);
    doc.text("£/Patient", M + 157, y + 5.2);
    y += 7.5;

    form.workforce.forEach((r, idx) => {
      checkPage(8);
      const rate = BAND_RATES[r.role] || 0;
      const mins = parseFloat(r.minutes) || 0;
      const cost = rate * mins / 60;
      if (idx % 2 === 0) { doc.setFillColor(...offWhite); doc.rect(M, y, CW, 7, "F"); }
      doc.setTextColor(...darkText);
      doc.setFontSize(7);
      doc.setFont("helvetica", "normal");
      doc.text(r.role, M + 3, y + 4.8);
      doc.text(mins ? String(mins) : "—", M + 85, y + 4.8);
      doc.text(r.frequency, M + 120, y + 4.8);
      doc.setFont("helvetica", "bold");
      doc.setTextColor(...green);
      doc.text(cost > 0 ? `£${cost.toFixed(2)}` : "—", M + 157, y + 4.8);
      doc.setDrawColor(...lineGrey); doc.setLineWidth(0.2);
      doc.line(M, y + 7, M + CW, y + 7);
      y += 7;
    });

    // Total row
    doc.setFillColor(...green);
    doc.rect(M, y, CW, 8.5, "F");
    doc.setTextColor(...white);
    doc.setFontSize(7.5);
    doc.setFont("helvetica", "bold");
    doc.text("Total workforce cost (current pathway)", M + 3, y + 5.8);
    doc.text(`£${totalHourlyCost.toFixed(2)} / patient`, M + 157, y + 5.8);
    y += 13;

    subHead("Resource Utilisation");
    dataRow("Outpatient Visits / Patient / Year", form.outpatientVisits || "—", false);
    dataRow("Tests / Patient / Year", form.tests || "—", true);
    dataRow("Admissions / Patient / Year", form.admissions || "—", false);
    dataRow("Bed Days / Admission", form.bedDays || "—", true);
    dataRow("Procedures / Patient / Year", form.procedures || "—", false);
    dataRow("Consumables Cost (£/patient)", form.consumables ? `£${form.consumables}` : "—", true);
    y += 6;

    // Section 4
    sectionHeader("4  Intervention & Pricing");
    subHead("Pricing");
    dataRow("Pricing Model", form.pricingModel, false);
    dataRow("Price", form.price ? `£${Number(form.price).toLocaleString()}` : "—", true);
    dataRow("Per Unit", form.priceUnit, false);
    y += 3;

    subHead("Implementation");
    dataRow("Training Required", form.trainingRequired === "yes" ? "Yes" : "No", false);
    if (form.trainingRequired === "yes") {
      dataRow("Roles Requiring Training", form.trainingRoles || "—", true);
      dataRow("Training Hours per Person", form.trainingHours || "—", false);
    }
    dataRow("One-off Setup Cost", form.setupCost ? `£${Number(form.setupCost).toLocaleString()}` : "None", true);
    y += 3;

    subHead("Resource Changes vs Current Pathway");
    dataRow("Staff Time Saved (mins/patient)", form.staffTimeSaved ? `${form.staffTimeSaved} mins` : "—", false);
    dataRow("Visits / Tests Reduced", form.visitsReduced ? `${form.visitsReduced}%` : "—", true);
    dataRow("Complications Reduced", form.complicationsReduced ? `${form.complicationsReduced}%` : "—", false);
    dataRow("Readmissions Reduced", form.readmissionsReduced ? `${form.readmissionsReduced}%` : "—", true);
    dataRow("Length of Stay Reduced", form.losReduced ? `${form.losReduced} days` : "—", false);
    dataRow("Follow-up Visits Reduced", form.followUpReduced ? `${form.followUpReduced}%` : "—", true);
    y += 3;

    subHead("Comparator & Discounting");
    const comparatorLabels = { none: "None / manual process", digital: "Digital tool", diagnostic: "Diagnostic", device: "Device / procedure" };
    dataRow("Current Alternatives", comparatorLabels[form.comparator] || form.comparator, false);
    if (form.comparatorNames) dataRow("Named Alternatives", form.comparatorNames, true);
    dataRow("Discounting Applied", form.discounting === "on" ? "Yes — 3.5% (NICE standard)" : "Off (recommended for BIA)", false);
    y += 8;

    // Key metrics callout
    checkPage(30);
    doc.setFillColor(232, 240, 252);
    doc.roundedRect(M, y, CW, 26, 2, 2, "F");
    doc.setFillColor(...green);
    doc.roundedRect(M, y, 3, 26, 1, 1, "F");
    doc.setTextColor(...navy);
    doc.setFontSize(8);
    doc.setFont("helvetica", "bold");
    doc.text("KEY METRICS AT A GLANCE", M + 7, y + 8);
    const metrics = [
      ["Eligible Cohort", eligibleN ? `${eligibleN.toLocaleString()} pts` : "—"],
      ["Workforce Cost/pt", totalHourlyCost > 0 ? `£${totalHourlyCost.toFixed(2)}` : "—"],
      ["Intervention Price", form.price ? `£${Number(form.price).toLocaleString()}` : "—"],
      ["Forecast Horizon", `${form.forecastYears} years`],
    ];
    const colW = (CW - 10) / 4;
    metrics.forEach((m, i) => {
      const x = M + 7 + i * colW;
      doc.setTextColor(...midGrey);
      doc.setFontSize(6.5);
      doc.setFont("helvetica", "normal");
      doc.text(m[0].toUpperCase(), x, y + 16);
      doc.setTextColor(...green);
      doc.setFontSize(10);
      doc.setFont("helvetica", "bold");
      doc.text(m[1], x, y + 23);
    });
    y += 32;

    // Footer on all pages
    const totalPages = doc.internal.getNumberOfPages();
    for (let i = 1; i <= totalPages; i++) {
      doc.setPage(i);
      doc.setFillColor(...navy);
      doc.rect(0, 287, W, 10, "F");
      doc.setTextColor(...midGrey);
      doc.setFontSize(6.5);
      doc.setFont("helvetica", "normal");
      doc.text("HEOR Engine  |  Input Collection Summary  |  Confidential", M, 293);
      doc.text(`Page ${i} of ${totalPages}`, W - M, 293, { align: "right" });
    }

    doc.save(`HEOR_Engine_Inputs_FY${form.modelYear}.pdf`);
  });
}

const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

function buildPayload(form) {
  return {
    setting: form.setting,
    model_year: parseInt(form.modelYear) || 2026,
    forecast_years: parseInt(form.forecastYears) || 3,
    funding_source: form.fundingSource,
    catchment_type: form.catchmentType,
    catchment_size: parseInt(form.catchmentSize) || 0,
    eligible_pct: parseFloat(form.eligiblePct) || 0,
    uptake_y1: parseFloat(form.uptakeY1) || 0,
    uptake_y2: parseFloat(form.uptakeY2) || 0,
    uptake_y3: parseFloat(form.uptakeY3) || 0,
    prevalence: form.prevalence || null,
    workforce: form.workforce.map(r => ({
      role: r.role,
      minutes: parseFloat(r.minutes) || 0,
      frequency: r.frequency,
    })),
    outpatient_visits: parseInt(form.outpatientVisits) || 0,
    tests: parseInt(form.tests) || 0,
    admissions: parseInt(form.admissions) || 0,
    bed_days: parseInt(form.bedDays) || 0,
    procedures: parseInt(form.procedures) || 0,
    consumables: parseFloat(form.consumables) || 0,
    pricing_model: form.pricingModel,
    price: parseFloat(form.price) || 0,
    price_unit: form.priceUnit,
    needs_training: form.trainingRequired === "yes",
    training_roles: form.trainingRequired === "yes" ? (form.trainingRoles || null) : null,
    training_hours: form.trainingRequired === "yes" ? (parseFloat(form.trainingHours) || null) : null,
    setup_cost: parseFloat(form.setupCost) || 0,
    staff_time_saved: parseFloat(form.staffTimeSaved) || 0,
    visits_reduced: parseFloat(form.visitsReduced) || 0,
    complications_reduced: parseFloat(form.complicationsReduced) || 0,
    readmissions_reduced: parseFloat(form.readmissionsReduced) || 0,
    los_reduced: parseFloat(form.losReduced) || 0,
    follow_up_reduced: parseFloat(form.followUpReduced) || 0,
    comparator: form.comparator,
    comparator_names: form.comparatorNames || null,
    discounting: form.discounting,
  };
}

// ── Inline confidence badge ─────────────────────────────────────────────────
function AiBadge({ level }) {
  const colours = {
    high:   { bg: "rgba(82,183,136,0.18)",  text: "#52b788" },
    medium: { bg: "rgba(255,193,7,0.18)",   text: "#ffc107" },
    low:    { bg: "rgba(255,100,100,0.18)", text: "#ff8080" },
  };
  const c = colours[level] || colours.low;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: "3px",
      marginLeft: "8px", padding: "1px 7px", borderRadius: "10px",
      background: c.bg, color: c.text,
      fontSize: "10px", fontWeight: "700", letterSpacing: "0.5px",
      verticalAlign: "middle", textTransform: "none",
    }}>
      AI · {level}
    </span>
  );
}

export default function HEORInputEngine({ hideChrome = false, externalFillData = null, skipQuickStart = false }) {
  const [section, setSection] = useState(0);
  const [completed, setCompleted] = useState([]);
  const [submitted, setSubmitted] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [generatingPptx, setGeneratingPptx] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [apiResult, setApiResult] = useState(null);
  const [apiError, setApiError] = useState(null);

  const [suggestLoading, setSuggestLoading] = useState(false);
  const [suggestResult, setSuggestResult] = useState(null);
  const [suggestError, setSuggestError] = useState(null);

  // ── Auto-fill from evidence ──
  const [showAutoFill, setShowAutoFill] = useState(!skipQuickStart);
  const [autoFilling, setAutoFilling] = useState(false);
  const [autoFillError, setAutoFillError] = useState(null);
  const [autoFillProgress, setAutoFillProgress] = useState("");
  const [autoFillEvidence, setAutoFillEvidence] = useState(null);
  const [quickStart, setQuickStart] = useState({ deviceName: "", indication: "", cost: "", benefits: "" });
  const pollingStopped = useRef(false);

  // Merge externally-provided fill data (from App.jsx AutoFillModal) into form
  useEffect(() => {
    if (!externalFillData) return;
    setForm(f => ({ ...f, ...externalFillData }));
    setShowAutoFill(false);
  }, [externalFillData]);

  const fetchSuggestedDefaults = async () => {
    if (!form.condition || !form.interventionType) return;
    setSuggestLoading(true);
    setSuggestError(null);
    try {
      const res = await fetch(`${API_BASE}/api/suggest-defaults`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          condition: form.condition,
          intervention_type: form.interventionType,
          setting: form.setting || "Acute NHS Trust",
        }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
      const data = await res.json();
      setSuggestResult(data);
    } catch (e) {
      setSuggestError(e.message);
    } finally {
      setSuggestLoading(false);
    }
  };

  // ── Map snake_case BIAInputs → camelCase form state ──────────────────
  const mapApiToForm = (bia) => {
    if (!bia) return {};
    const str = (v) => (v !== undefined && v !== null ? String(v) : "");
    const mapped = {
      setting:             bia.setting              || "",
      modelYear:           bia.model_year           || new Date().getFullYear(),
      forecastYears:       str(bia.forecast_years   || "3"),
      fundingSource:       bia.funding_source        || "",
      catchmentType:       bia.catchment_type        || "population",
      catchmentSize:       str(bia.catchment_size    || ""),
      eligiblePct:         str(bia.eligible_pct      || ""),
      uptakeY1:            str(bia.uptake_y1         || ""),
      uptakeY2:            str(bia.uptake_y2         || ""),
      uptakeY3:            str(bia.uptake_y3         || ""),
      prevalence:          bia.prevalence            || "",
      outpatientVisits:    str(bia.outpatient_visits || ""),
      tests:               str(bia.tests             || ""),
      admissions:          str(bia.admissions        || ""),
      bedDays:             str(bia.bed_days          || ""),
      procedures:          str(bia.procedures        || ""),
      consumables:         str(bia.consumables       || ""),
      pricingModel:        bia.pricing_model         || "per-patient",
      price:               str(bia.price             || ""),
      priceUnit:           bia.price_unit            || "per year",
      trainingRequired:    bia.needs_training        ? "yes" : "no",
      trainingRoles:       bia.training_roles        || "",
      trainingHours:       str(bia.training_hours    || ""),
      setupCost:           str(bia.setup_cost        || ""),
      staffTimeSaved:      str(bia.staff_time_saved  || ""),
      visitsReduced:       str(bia.visits_reduced    || ""),
      complicationsReduced:str(bia.complications_reduced || ""),
      readmissionsReduced: str(bia.readmissions_reduced  || ""),
      losReduced:          str(bia.los_reduced        || ""),
      followUpReduced:     str(bia.follow_up_reduced  || ""),
      comparator:          bia.comparator            || "none",
      comparatorNames:     bia.comparator_names      || "",
      discounting:         bia.discounting           || "off",
    };
    // Workforce rows — add stable id for React keys
    if (Array.isArray(bia.workforce) && bia.workforce.length > 0) {
      mapped.workforce = bia.workforce.map((r, i) => ({
        role: r.role || "Band 5 (Staff Nurse)",
        minutes: str(r.minutes || ""),
        frequency: r.frequency || "per patient",
        id: Date.now() + i,
      }));
    }
    return mapped;
  };

  // ── Auto-fill handler — POSTs task, polls until complete ──────────────
  const handleAutoFill = async () => {
    setAutoFilling(true);
    setAutoFillError(null);
    setAutoFillProgress("Submitting request...");
    pollingStopped.current = false;

    const PROGRESS_LABELS = {
      queued:     "Queued — waiting to start...",
      searching:  "Searching PubMed and NICE guidance (this takes ~30 s)...",
      extracting: "Extracting clinical data from abstracts...",
      populating: "Synthesising evidence into BIA inputs...",
    };

    try {
      // Kick off the background task
      const res = await fetch(`${API_BASE}/api/auto-populate/bia`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          device_name:            quickStart.deviceName,
          indication:             quickStart.indication,
          setting:                "Acute NHS Trust",
          device_cost_per_patient: parseFloat(quickStart.cost) || 0,
          expected_benefits:      quickStart.benefits,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Server error ${res.status}`);
      }
      const { task_id, poll_url } = await res.json();

      // Poll until complete, failed, or timeout
      let attempts = 0;
      const MAX_ATTEMPTS = 50; // 50 × 4 s = 3.3 minutes max

      while (attempts < MAX_ATTEMPTS && !pollingStopped.current) {
        await new Promise(r => setTimeout(r, 4000));
        attempts++;

        const statusRes = await fetch(`${API_BASE}${poll_url}`);
        if (!statusRes.ok) continue;
        const statusData = await statusRes.json();

        setAutoFillProgress(
          PROGRESS_LABELS[statusData.status] || statusData.step || "Working..."
        );

        if (statusData.status === "complete") {
          const result = statusData.result || {};
          setForm(f => ({ ...f, ...mapApiToForm(result.bia_inputs || {}) }));
          setAutoFillEvidence({
            sources:         result.sources          || [],
            confidence:      result.confidence_scores || {},
            warnings:        result.warnings         || [],
            assumptions:     result.assumptions      || [],
            evidenceSummary: result.evidence_summary || {},
          });
          setShowAutoFill(false);
          setSection(0);
          return;
        }
        if (statusData.status === "failed") {
          throw new Error(statusData.error || "Evidence gathering failed on the server.");
        }
      }

      if (!pollingStopped.current) {
        throw new Error("Timed out waiting for evidence. Please try again or fill the form manually.");
      }
    } catch (err) {
      setAutoFillError(err.message || "Evidence gathering failed. You can fill the form manually.");
    } finally {
      setAutoFilling(false);
      setAutoFillProgress("");
    }
  };

  const applyDefaults = () => {
    if (!suggestResult?.suggestions) return;
    const s = suggestResult.suggestions;
    setForm(f => ({
      ...f,
      eligiblePct: s.eligible_pct !== undefined ? String((s.eligible_pct * 100).toFixed(1)) : f.eligiblePct,
      uptakeY1: s.uptake_y1 !== undefined ? String(s.uptake_y1) : f.uptakeY1,
      uptakeY2: s.uptake_y2 !== undefined ? String(s.uptake_y2) : f.uptakeY2,
      uptakeY3: s.uptake_y3 !== undefined ? String(s.uptake_y3) : f.uptakeY3,
    }));
    setSuggestResult(null);
  };

  const [form, setForm] = useState({
    condition: "",
    interventionType: "",
    setting: "",
    modelYear: new Date().getFullYear(),
    forecastYears: "3",
    fundingSource: "",
    catchmentType: "population",
    catchmentSize: "",
    eligiblePct: "",
    uptakeY1: "",
    uptakeY2: "",
    uptakeY3: "",
    prevalence: "",
    workforce: [defaultWorkforceRow()],
    outpatientVisits: "",
    tests: "",
    admissions: "",
    bedDays: "",
    procedures: "",
    consumables: "",
    pricingModel: "per-patient",
    price: "",
    priceUnit: "per year",
    trainingRequired: "no",
    trainingRoles: "",
    trainingHours: "",
    setupCost: "",
    staffTimeSaved: "",
    visitsReduced: "",
    complicationsReduced: "",
    readmissionsReduced: "",
    losReduced: "",
    followUpReduced: "",
    comparator: "none",
    comparatorNames: "",
    discounting: "off",
  });

  const update = (field, value) => setForm(f => ({ ...f, [field]: value }));
  const updateWorkforce = (id, field, value) =>
    setForm(f => ({ ...f, workforce: f.workforce.map(r => r.id === id ? { ...r, [field]: value } : r) }));
  const addWorkforceRow = () => setForm(f => ({ ...f, workforce: [...f.workforce, defaultWorkforceRow()] }));
  const removeWorkforceRow = (id) => setForm(f => ({ ...f, workforce: f.workforce.filter(r => r.id !== id) }));

  const markComplete = async () => {
    if (!completed.includes(section)) setCompleted(c => [...c, section]);
    if (section < SECTIONS.length - 1) {
      setSection(s => s + 1);
      return;
    }

    // Final section — submit to BIA workflow
    setSubmitting(true);
    setApiError(null);
    try {
      const response = await fetch(`${API_BASE}/api/workflows/bia`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          inputs: buildPayload(form),
          enrich_with_evidence: true,
          generate_report: true,
          intervention_name: form.interventionType || "Medical Device",
        }),
      });
      if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: response.statusText }));
        const detail = err.detail;
        if (Array.isArray(detail)) {
          const fields = detail.map(e => `${e.loc ? e.loc.join(" → ") : "field"}: ${e.msg}`).join("; ");
          throw new Error(`Validation failed — ${fields}`);
        }
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail || err));
      }
      const result = await response.json();
      setApiResult(result);
      setSubmitted(true);
    } catch (e) {
      setApiError(e.message || "Failed to connect to API");
    } finally {
      setSubmitting(false);
    }
  };

  const totalHourlyCost = form.workforce.reduce((sum, r) => {
    const rate = BAND_RATES[r.role] || 0;
    const mins = parseFloat(r.minutes) || 0;
    return sum + (rate * mins / 60);
  }, 0);

  const eligibleN = Math.round((parseFloat(form.catchmentSize) || 0) * (parseFloat(form.eligiblePct) || 0) / 100);

  const handleDownload = () => {
    setDownloading(true);
    setTimeout(() => { generatePDF(form, totalHourlyCost); setDownloading(false); }, 400);
  };

  const handlePptxDownload = async () => {
    if (!apiResult?.report_url) return;
    setGeneratingPptx(true);
    try {
      const dlResp = await fetch(`${API_BASE}${apiResult.report_url}`);
      if (!dlResp.ok) throw new Error("Download failed");
      const blob = await dlResp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `HEOR_BIA_Report_${apiResult.workflow_id || "report"}.pptx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      alert(`PowerPoint download failed: ${e.message}`);
    } finally {
      setGeneratingPptx(false);
    }
  };

  // ─── Styles ───
  const S = {
    app: { minHeight: "100vh", background: "linear-gradient(135deg, #0a1628 0%, #0d2044 50%, #0a1f3d 100%)", fontFamily: "'Georgia','Times New Roman',serif", color: "#e8edf5" },
    header: { background: "rgba(255,255,255,0.03)", borderBottom: "1px solid rgba(255,255,255,0.08)", padding: "20px 40px", display: "flex", alignItems: "center", gap: "16px" },
    logoIcon: { width: "38px", height: "38px", background: "linear-gradient(135deg,#2d6a4f,#52b788)", borderRadius: "8px", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "18px" },
    logoText: { fontSize: "22px", fontWeight: "700", color: "#fff" },
    logoSub: { fontSize: "11px", color: "#52b788", letterSpacing: "2px", textTransform: "uppercase" },
    headerRight: { marginLeft: "auto", fontSize: "13px", color: "#8fa8c8", fontStyle: "italic" },
    main: { display: "grid", gridTemplateColumns: "260px 1fr", maxWidth: "1100px", margin: "40px auto", padding: "0 20px", minHeight: "600px" },
    sidebar: { background: "rgba(255,255,255,0.03)", borderRadius: "12px 0 0 12px", border: "1px solid rgba(255,255,255,0.07)", borderRight: "none", padding: "28px 0", display: "flex", flexDirection: "column" },
    sidebarTitle: { fontSize: "10px", letterSpacing: "2.5px", textTransform: "uppercase", color: "#5a7fa8", padding: "0 24px", marginBottom: "16px" },
    navItem: (idx) => ({ display: "flex", alignItems: "center", gap: "12px", padding: "14px 24px", cursor: "pointer", borderLeft: section === idx ? "3px solid #52b788" : "3px solid transparent", background: section === idx ? "rgba(82,183,136,0.08)" : "transparent", color: section === idx ? "#fff" : "#8fa8c8" }),
    navStep: (idx) => ({ width: "20px", height: "20px", borderRadius: "50%", background: completed.includes(idx) ? "#52b788" : section === idx ? "rgba(82,183,136,0.3)" : "rgba(255,255,255,0.1)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "10px", color: completed.includes(idx) ? "#0a1628" : "#8fa8c8", fontWeight: "700", marginLeft: "auto", flexShrink: 0 }),
    content: { background: "rgba(255,255,255,0.035)", borderRadius: "0 12px 12px 0", border: "1px solid rgba(255,255,255,0.07)", padding: "36px 40px" },
    sectionTitle: { fontSize: "24px", fontWeight: "400", color: "#fff", marginBottom: "6px" },
    sectionSub: { fontSize: "13px", color: "#6a8fb5", marginBottom: "32px", fontStyle: "italic" },
    label: { display: "block", fontSize: "12px", letterSpacing: "1px", textTransform: "uppercase", color: "#7a9fc4", marginBottom: "8px" },
    input: { width: "100%", background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.12)", borderRadius: "6px", padding: "10px 14px", color: "#e8edf5", fontSize: "14px", outline: "none", boxSizing: "border-box", fontFamily: "'Georgia',serif" },
    select: { width: "100%", background: "#0d2044", border: "1px solid rgba(255,255,255,0.12)", borderRadius: "6px", padding: "10px 14px", color: "#e8edf5", fontSize: "14px", outline: "none", boxSizing: "border-box", fontFamily: "'Georgia',serif" },
    grid2: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" },
    grid3: { display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "16px" },
    infoBox: { background: "rgba(82,183,136,0.08)", border: "1px solid rgba(82,183,136,0.2)", borderRadius: "8px", padding: "14px 18px", marginBottom: "24px", fontSize: "13px", color: "#8fd4b0", lineHeight: "1.6" },
    radioGroup: { display: "flex", gap: "10px", flexWrap: "wrap" },
    radioBtn: (active) => ({ padding: "8px 16px", borderRadius: "6px", border: active ? "1px solid #52b788" : "1px solid rgba(255,255,255,0.12)", background: active ? "rgba(82,183,136,0.15)" : "rgba(255,255,255,0.04)", color: active ? "#52b788" : "#8fa8c8", cursor: "pointer", fontSize: "13px", fontFamily: "'Georgia',serif" }),
    wfHeader: { display: "grid", gridTemplateColumns: "2fr 1fr 1fr 36px", gap: "8px", marginBottom: "6px" },
    wfHeaderCell: { fontSize: "10px", letterSpacing: "1px", textTransform: "uppercase", color: "#5a7fa8" },
    wfRow: { display: "grid", gridTemplateColumns: "2fr 1fr 1fr 36px", gap: "8px", marginBottom: "8px", alignItems: "center" },
    removeBtn: { background: "rgba(220,53,69,0.15)", border: "1px solid rgba(220,53,69,0.3)", borderRadius: "4px", color: "#e85d6f", cursor: "pointer", padding: "6px", fontSize: "14px", display: "flex", alignItems: "center", justifyContent: "center" },
    addBtn: { background: "rgba(82,183,136,0.1)", border: "1px dashed rgba(82,183,136,0.3)", borderRadius: "6px", color: "#52b788", cursor: "pointer", padding: "8px 16px", fontSize: "13px", width: "100%", marginBottom: "16px", fontFamily: "'Georgia',serif" },
    hint: { background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.07)", borderRadius: "6px", padding: "12px 16px", fontSize: "13px", color: "#6a8fb5", marginBottom: "24px" },
    hintVal: { color: "#52b788", fontWeight: "700", fontSize: "16px" },
    divider: { borderColor: "rgba(255,255,255,0.07)", margin: "24px 0" },
    divLabel: { fontSize: "10px", letterSpacing: "2px", textTransform: "uppercase", color: "#5a7fa8", marginBottom: "16px" },
    btnRow: { display: "flex", gap: "12px", marginTop: "32px" },
    btnBack: { background: "transparent", border: "1px solid rgba(255,255,255,0.15)", borderRadius: "6px", color: "#8fa8c8", padding: "11px 22px", fontSize: "13px", cursor: "pointer", fontFamily: "'Georgia',serif" },
    btnNext: { background: "linear-gradient(135deg,#2d6a4f,#52b788)", border: "none", borderRadius: "6px", color: "#fff", padding: "11px 28px", fontSize: "13px", cursor: "pointer", fontWeight: "600", marginLeft: "auto", fontFamily: "'Georgia',serif" },
    progressWrap: { marginTop: "auto", padding: "0 24px 16px" },
    progressBar: { height: "3px", background: "rgba(255,255,255,0.08)", borderRadius: "2px", margin: "12px 0 6px" },
    progressFill: (pct) => ({ height: "100%", background: "linear-gradient(90deg,#2d6a4f,#52b788)", borderRadius: "2px", width: `${pct}%` }),
  };

  const fg = { marginBottom: "24px" };

  // ─── Section renders ───
  const renderSection = () => {
    switch (section) {
      case 0: return (
        <>
          <div style={S.infoBox}>Define the NHS context for this model. These settings anchor all downstream cost translations and population estimates.</div>
          <div style={S.grid2}>
            <div style={fg}>
              <label style={S.label}>Clinical condition</label>
              <select style={S.select} value={form.condition} onChange={e=>update("condition",e.target.value)}>
                <option value="">Select condition…</option>
                {["Diabetes","Hypertension","Heart Failure","COPD","Atrial Fibrillation","Chronic Kidney Disease","Asthma","Depression","Dementia","Lung Cancer"].map(c=><option key={c}>{c}</option>)}
              </select>
            </div>
            <div style={fg}>
              <label style={S.label}>Intervention type</label>
              <select style={S.select} value={form.interventionType} onChange={e=>update("interventionType",e.target.value)}>
                <option value="">Select type…</option>
                <option value="digital">Digital Health Tool</option>
                <option value="remote_monitoring">Remote Monitoring</option>
                <option value="diagnostic">Diagnostic Technology</option>
                <option value="ai">AI / Decision Support</option>
                <option value="pharmaceutical">Pharmaceutical</option>
              </select>
            </div>
          </div>
          <div style={fg}>
            <label style={S.label}>Where will this technology be deployed?</label>
            <div style={S.radioGroup}>
              {["Acute NHS Trust","ICB","Primary Care Network"].map(v => (
                <div key={v} style={S.radioBtn(form.setting===v)} onClick={()=>update("setting",v)}>{v}</div>
              ))}
            </div>
          </div>
          <div style={S.grid2}>
            <div style={fg}>
              <label style={S.label}>Model start year</label>
              <select style={S.select} value={form.modelYear} onChange={e=>update("modelYear",e.target.value)}>
                {[2025,2026,2027].map(y=><option key={y}>{y}</option>)}
              </select>
            </div>
            <div style={fg}>
              <label style={S.label}>Forecast horizon</label>
              <div style={S.radioGroup}>
                {["3","4","5"].map(y=>(
                  <div key={y} style={S.radioBtn(form.forecastYears===y)} onClick={()=>update("forecastYears",y)}>{y} years</div>
                ))}
              </div>
            </div>
          </div>
          <div style={fg}>
            <label style={S.label}>Most likely funding route</label>
            <select style={S.select} value={form.fundingSource} onChange={e=>update("fundingSource",e.target.value)}>
              <option value="">Select funding source...</option>
              {["Trust operational budget","ICB commissioning","Transformation / innovation funding","Capital budget","Industry-funded pilot","Research / grant","Unsure"].map(v=><option key={v}>{v}</option>)}
            </select>
          </div>
        </>
      );
      case 1: return (
        <>
          <div style={S.infoBox}>Define who is eligible and how quickly uptake will grow. These drive population-level cost and saving estimates.</div>

          {/* Evidence-backed Suggest Defaults banner */}
          {(form.condition || form.interventionType) && (
            <div style={{
              background: "rgba(82,183,136,0.07)",
              border: "1px solid rgba(82,183,136,0.25)",
              borderRadius: "8px",
              padding: "16px 20px",
              marginBottom: "24px",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: "16px",
              flexWrap: "wrap",
            }}>
              <div>
                <div style={{ color: "#52b788", fontSize: "13px", fontWeight: "700", marginBottom: "3px" }}>
                  Evidence-backed defaults available
                </div>
                <div style={{ color: "#5a7fa8", fontSize: "12px" }}>
                  {[form.condition, form.interventionType].filter(Boolean).join(" · ")} — from NHS Cost Collection, ONS &amp; NICE
                </div>
              </div>
              <button
                onClick={fetchSuggestedDefaults}
                disabled={suggestLoading}
                style={{
                  background: "linear-gradient(135deg,#2d6a4f,#52b788)",
                  border: "none", borderRadius: "6px", color: "#fff",
                  padding: "9px 20px", fontSize: "13px", cursor: suggestLoading ? "not-allowed" : "pointer",
                  fontFamily: "'Georgia',serif", fontWeight: "600", whiteSpace: "nowrap",
                  opacity: suggestLoading ? 0.6 : 1,
                }}
              >
                {suggestLoading ? "Fetching…" : "Suggest Defaults"}
              </button>
            </div>
          )}

          {suggestError && (
            <div style={{ background: "rgba(220,53,69,0.08)", border: "1px solid rgba(220,53,69,0.25)", borderRadius: "6px", padding: "12px 16px", fontSize: "13px", color: "#e85d6f", marginBottom: "16px" }}>
              {suggestError}
            </div>
          )}

          {suggestResult?.suggestions && (
            <div style={{
              background: "rgba(255,255,255,0.03)",
              border: "1px solid rgba(82,183,136,0.3)",
              borderRadius: "8px",
              padding: "16px 20px",
              marginBottom: "24px",
            }}>
              <div style={{ color: "#52b788", fontSize: "12px", letterSpacing: "1px", textTransform: "uppercase", marginBottom: "12px" }}>
                Suggested values — tap Apply to fill the form
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: "12px", marginBottom: "14px" }}>
                {[
                  ["Eligible %", `${((suggestResult.suggestions.eligible_pct||0)*100).toFixed(1)}%`],
                  ["Uptake Y1", `${suggestResult.suggestions.uptake_y1}%`],
                  ["Uptake Y2", `${suggestResult.suggestions.uptake_y2}%`],
                  ["Uptake Y3", `${suggestResult.suggestions.uptake_y3}%`],
                ].map(([label, val]) => (
                  <div key={label} style={{ textAlign: "center", background: "rgba(255,255,255,0.03)", borderRadius: "6px", padding: "10px 6px" }}>
                    <div style={{ color: "#5a7fa8", fontSize: "11px", marginBottom: "3px" }}>{label}</div>
                    <div style={{ color: "#52b788", fontWeight: "700", fontSize: "17px" }}>{val}</div>
                  </div>
                ))}
              </div>
              {suggestResult.warnings?.length > 0 && (
                <div style={{ color: "#ffc107", fontSize: "12px", marginBottom: "10px" }}>
                  ⚠ {suggestResult.warnings[0]}
                </div>
              )}
              <div style={{ display: "flex", gap: "10px" }}>
                <button
                  onClick={applyDefaults}
                  style={{ background: "linear-gradient(135deg,#2d6a4f,#52b788)", border: "none", borderRadius: "6px", color: "#fff", padding: "8px 20px", fontSize: "13px", cursor: "pointer", fontFamily: "'Georgia',serif", fontWeight: "600" }}
                >
                  Apply to form
                </button>
                <button
                  onClick={() => setSuggestResult(null)}
                  style={{ background: "transparent", border: "1px solid rgba(255,255,255,0.15)", borderRadius: "6px", color: "#8fa8c8", padding: "8px 16px", fontSize: "13px", cursor: "pointer", fontFamily: "'Georgia',serif" }}
                >
                  Dismiss
                </button>
              </div>
            </div>
          )}

          <div style={fg}>
            <label style={S.label}>Catchment measure</label>
            <div style={S.radioGroup}>
              <div style={S.radioBtn(form.catchmentType==="population")} onClick={()=>update("catchmentType","population")}>Population served</div>
              <div style={S.radioBtn(form.catchmentType==="beds")} onClick={()=>update("catchmentType","beds")}>Trust beds</div>
            </div>
          </div>
          <div style={S.grid2}>
            <div style={fg}>
              <label style={S.label}>
                Catchment size (n)
                {autoFillEvidence?.confidence?.catchment_size && (
                  <AiBadge level={autoFillEvidence.confidence.catchment_size} />
                )}
              </label>
              <input style={S.input} type="number" placeholder="e.g. 250000" value={form.catchmentSize} onChange={e=>update("catchmentSize",e.target.value)}/>
            </div>
            <div style={fg}>
              <label style={S.label}>
                Eligible patients (% of catchment)
                {autoFillEvidence?.confidence?.eligible_population && (
                  <AiBadge level={autoFillEvidence.confidence.eligible_population} />
                )}
              </label>
              <input style={S.input} type="number" placeholder="e.g. 5" value={form.eligiblePct} onChange={e=>update("eligiblePct",e.target.value)}/>
            </div>
          </div>
          <div style={S.divLabel}>
            Uptake trajectory (%)
            {autoFillEvidence?.confidence?.uptake_trajectory && (
              <AiBadge level={autoFillEvidence.confidence.uptake_trajectory} />
            )}
          </div>
          <div style={S.grid3}>
            {[["uptakeY1","Year 1","20"],["uptakeY2","Year 2","50"],["uptakeY3","Year 3","80"]].map(([f,l,ph])=>(
              <div key={f} style={fg}>
                <label style={S.label}>{l}</label>
                <input style={S.input} type="number" placeholder={ph} value={form[f]} onChange={e=>update(f,e.target.value)}/>
              </div>
            ))}
          </div>
          <div style={fg}>
            <label style={S.label}>Prevalence / incidence notes (optional)</label>
            <input style={S.input} type="text" placeholder="e.g. 12/100,000 incidence; rising trend" value={form.prevalence} onChange={e=>update("prevalence",e.target.value)}/>
          </div>
          {eligibleN > 0 && (
            <div style={S.hint}>
              Estimated eligible cohort: <span style={S.hintVal}>{eligibleN.toLocaleString()} patients</span>
              {form.uptakeY1 && <> · Year 1 treated: ~<span style={S.hintVal}>{Math.round(eligibleN*form.uptakeY1/100).toLocaleString()}</span></>}
            </div>
          )}
        </>
      );
      case 2: return (
        <>
          <div style={S.infoBox}>Map the current care pathway to establish the cost baseline the intervention will be compared against.</div>
          <div style={S.divLabel}>Workforce inputs</div>
          <div style={S.wfHeader}>
            {["Role","Mins / patient","Frequency",""].map(h=><div key={h} style={S.wfHeaderCell}>{h}</div>)}
          </div>
          {form.workforce.map(row=>(
            <div key={row.id} style={S.wfRow}>
              <select style={S.select} value={row.role} onChange={e=>updateWorkforce(row.id,"role",e.target.value)}>
                {Object.keys(BAND_RATES).map(r=><option key={r}>{r}</option>)}
              </select>
              <input style={S.input} type="number" placeholder="mins" value={row.minutes} onChange={e=>updateWorkforce(row.id,"minutes",e.target.value)}/>
              <select style={S.select} value={row.frequency} onChange={e=>updateWorkforce(row.id,"frequency",e.target.value)}>
                {["per patient","per visit","per admission","per year"].map(f=><option key={f}>{f}</option>)}
              </select>
              <button style={S.removeBtn} onClick={()=>removeWorkforceRow(row.id)}>✕</button>
            </div>
          ))}
          <button style={S.addBtn} onClick={addWorkforceRow}>+ Add another role</button>
          {totalHourlyCost > 0 && (
            <div style={S.hint}>
              Estimated workforce cost (current pathway): <span style={S.hintVal}>£{totalHourlyCost.toFixed(2)} / patient</span>
              <div style={{marginTop:"4px",fontSize:"12px",color:"#4a6fa5"}}>Using NHS Agenda for Change band rates</div>
            </div>
          )}
          <hr style={S.divider}/>
          <div style={S.divLabel}>Resource utilisation</div>
          <div style={S.grid2}>
            {[["outpatientVisits","Outpatient visits / patient / year","e.g. 4"],["tests","Tests / patient / year","e.g. 2"],["admissions","Admissions / patient / year","e.g. 1"],["bedDays","Bed days / admission","e.g. 3"],["procedures","Procedures / patient / year","e.g. 0"],["consumables","Consumables cost (£/patient)","e.g. 45"]].map(([f,l,ph])=>(
              <div key={f} style={fg}>
                <label style={S.label}>{l}</label>
                <input style={S.input} type="number" placeholder={ph} value={form[f]} onChange={e=>update(f,e.target.value)}/>
              </div>
            ))}
          </div>
        </>
      );
      case 3: return (
        <>
          <div style={S.infoBox}>Define the new intervention's costs and the resource changes it creates versus the current pathway.</div>
          <div style={S.divLabel}>Pricing model</div>
          <div style={fg}>
            <div style={S.radioGroup}>
              {["per-patient","per-use","subscription","capital + consumables"].map(p=>(
                <div key={p} style={S.radioBtn(form.pricingModel===p)} onClick={()=>update("pricingModel",p)}>{p.charAt(0).toUpperCase()+p.slice(1)}</div>
              ))}
            </div>
          </div>
          <div style={S.grid2}>
            <div style={fg}>
              <label style={S.label}>Price (£)</label>
              <input style={S.input} type="number" placeholder="e.g. 1200" value={form.price} onChange={e=>update("price",e.target.value)}/>
            </div>
            <div style={fg}>
              <label style={S.label}>Per unit</label>
              <select style={S.select} value={form.priceUnit} onChange={e=>update("priceUnit",e.target.value)}>
                {["per year","per patient","per use"].map(u=><option key={u}>{u}</option>)}
              </select>
            </div>
          </div>
          <hr style={S.divider}/>
          <div style={S.divLabel}>Implementation costs</div>
          <div style={S.grid2}>
            <div style={fg}>
              <label style={S.label}>Training required?</label>
              <div style={S.radioGroup}>
                <div style={S.radioBtn(form.trainingRequired==="yes")} onClick={()=>update("trainingRequired","yes")}>Yes</div>
                <div style={S.radioBtn(form.trainingRequired==="no")} onClick={()=>update("trainingRequired","no")}>No</div>
              </div>
            </div>
            <div style={fg}>
              <label style={S.label}>One-off setup cost (£)</label>
              <input style={S.input} type="number" placeholder="e.g. 5000" value={form.setupCost} onChange={e=>update("setupCost",e.target.value)}/>
            </div>
          </div>
          {form.trainingRequired==="yes" && (
            <div style={S.grid2}>
              <div style={fg}>
                <label style={S.label}>Roles requiring training</label>
                <input style={S.input} type="text" placeholder="e.g. Band 5 nurses, registrars" value={form.trainingRoles} onChange={e=>update("trainingRoles",e.target.value)}/>
              </div>
              <div style={fg}>
                <label style={S.label}>Training hours per person</label>
                <input style={S.input} type="number" placeholder="e.g. 2" value={form.trainingHours} onChange={e=>update("trainingHours",e.target.value)}/>
              </div>
            </div>
          )}
          <hr style={S.divider}/>
          <div style={S.divLabel}>
            Resource use changes (delta vs current pathway)
            {autoFillEvidence?.confidence?.resource_savings && (
              <AiBadge level={autoFillEvidence.confidence.resource_savings} />
            )}
          </div>
          <div style={S.grid2}>
            {[["staffTimeSaved","Staff time saved (mins/patient)","e.g. 15"],["visitsReduced","Visits / tests reduced (%)","e.g. 20"],["complicationsReduced","Complications reduced (%)","e.g. 30"],["readmissionsReduced","Readmissions reduced (%)","e.g. 15"],["losReduced","Length of stay reduced (days)","e.g. 1"],["followUpReduced","Follow-up visits reduced (%)","e.g. 25"]].map(([f,l,ph])=>(
              <div key={f} style={fg}>
                <label style={S.label}>{l}</label>
                <input style={S.input} type="number" placeholder={ph} value={form[f]} onChange={e=>update(f,e.target.value)}/>
              </div>
            ))}
          </div>
          <hr style={S.divider}/>
          <div style={S.divLabel}>Comparator & discounting</div>
          <div style={S.grid2}>
            <div style={fg}>
              <label style={S.label}>Current alternatives in use</label>
              <select style={S.select} value={form.comparator} onChange={e=>update("comparator",e.target.value)}>
                {[["none","None / manual process"],["digital","Digital tool"],["diagnostic","Diagnostic"],["device","Device / procedure"]].map(([v,l])=><option key={v} value={v}>{l}</option>)}
              </select>
            </div>
            <div style={fg}>
              <label style={S.label}>Name alternatives (optional)</label>
              <input style={S.input} type="text" placeholder="e.g. Paper-based triage, existing EPR" value={form.comparatorNames} onChange={e=>update("comparatorNames",e.target.value)}/>
            </div>
          </div>
          <div style={fg}>
            <label style={S.label}>Apply discounting to future costs?</label>
            <div style={S.radioGroup}>
              <div style={S.radioBtn(form.discounting==="off")} onClick={()=>update("discounting","off")}>Off (recommended for BIA)</div>
              <div style={S.radioBtn(form.discounting==="on")} onClick={()=>update("discounting","on")}>On — 3.5% (NICE standard)</div>
            </div>
          </div>
        </>
      );
      default: return null;
    }
  };

  // ─── Loading screen ───
  if (submitting) {
    const loadingContent = (
      <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",minHeight:"400px",gap:"28px",padding:"60px 20px"}}>
        <div style={{fontSize:"48px",animation:"spin 2s linear infinite",display:"inline-block"}}>⚙️</div>
        <div style={{textAlign:"center"}}>
          <div style={{fontSize:"22px",color:"#fff",fontWeight:"400",marginBottom:"10px"}}>Processing Your Submission...</div>
          <div style={{fontSize:"14px",color:"#6a8fb5",fontStyle:"italic"}}>Running budget impact analysis and generating reports</div>
        </div>
        <div style={{display:"flex",gap:"8px",alignItems:"center"}}>
          {[0,1,2].map(i => (
            <div key={i} style={{width:"8px",height:"8px",borderRadius:"50%",background:"#52b788",opacity:0.3,animation:`pulse 1.2s ease-in-out ${i*0.4}s infinite`}}/>
          ))}
        </div>
        <style>{`@keyframes spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}@keyframes pulse{0%,100%{opacity:0.3}50%{opacity:1}}`}</style>
      </div>
    );
    if (hideChrome) return loadingContent;
    return (
      <div style={S.app}>
        <div style={S.header}>
          <div style={{display:"flex",alignItems:"center",gap:"10px"}}>
            <div style={S.logoIcon}>⚙️</div>
            <div><div style={S.logoText}>HEOR Engine</div><div style={S.logoSub}>Input Collection Engine</div></div>
          </div>
          <div style={S.headerRight}>Budget Impact Analysis — NHS Payer Perspective</div>
        </div>
        {loadingContent}
      </div>
    );
  }

  // ─── Quick Start / Auto-fill screen ───
  if (showAutoFill && !submitted) {
    const qsInputStyle = {
      width: "100%", padding: "11px 14px", borderRadius: "7px",
      border: "1.5px solid rgba(255,255,255,0.12)",
      background: "rgba(255,255,255,0.05)", color: "#e8edf5",
      fontSize: "14px", outline: "none", boxSizing: "border-box",
      fontFamily: "'Georgia',serif", marginBottom: "18px",
      transition: "border-color 0.2s",
    };
    const qsLabelStyle = {
      display: "block", fontSize: "12px", letterSpacing: "1px",
      textTransform: "uppercase", color: "#7a9fc4", marginBottom: "7px",
    };

    const quickStartContent = (
      <div style={{ maxWidth: "680px", margin: "60px auto", padding: "0 20px" }}>
        {/* Hero */}
        <div style={{ textAlign: "center", marginBottom: "32px" }}>
          <div style={{ fontSize: "40px", marginBottom: "12px" }}>✨</div>
          <h1 style={{ color: "#fff", fontSize: "26px", fontWeight: "400", margin: "0 0 10px" }}>
            Auto-fill from Evidence
          </h1>
          <p style={{ color: "#6a8fb5", fontSize: "14px", margin: 0, lineHeight: "1.6" }}>
            Describe your device and we'll search PubMed, NICE guidance, and NHS data
            to pre-fill realistic estimates — takes about 60 seconds.
          </p>
        </div>

        {/* Form card */}
        <div style={{
          background: "rgba(255,255,255,0.04)", padding: "32px 36px",
          borderRadius: "12px", border: "1px solid rgba(255,255,255,0.1)",
          marginBottom: "16px",
        }}>
          <label style={qsLabelStyle}>Device / intervention name *</label>
          <input
            type="text"
            placeholder="e.g. AI Sepsis Prediction Tool"
            value={quickStart.deviceName}
            onChange={e => setQuickStart(q => ({ ...q, deviceName: e.target.value }))}
            style={qsInputStyle}
          />

          <label style={qsLabelStyle}>Clinical indication *</label>
          <input
            type="text"
            placeholder="e.g. Sepsis in ICU patients"
            value={quickStart.indication}
            onChange={e => setQuickStart(q => ({ ...q, indication: e.target.value }))}
            style={qsInputStyle}
          />

          <label style={qsLabelStyle}>Cost per patient (£) *</label>
          <input
            type="number"
            placeholder="e.g. 185"
            value={quickStart.cost}
            onChange={e => setQuickStart(q => ({ ...q, cost: e.target.value }))}
            style={qsInputStyle}
          />

          <label style={qsLabelStyle}>Expected benefits (optional)</label>
          <textarea
            placeholder="e.g. Earlier detection enabling faster antibiotic administration and reduced ICU stays"
            value={quickStart.benefits}
            onChange={e => setQuickStart(q => ({ ...q, benefits: e.target.value }))}
            rows={3}
            style={{ ...qsInputStyle, resize: "vertical", lineHeight: "1.5" }}
          />

          {/* Progress indicator */}
          {autoFilling && (
            <div style={{
              background: "rgba(82,183,136,0.08)", border: "1px solid rgba(82,183,136,0.25)",
              borderRadius: "8px", padding: "14px 18px", marginBottom: "18px",
              display: "flex", alignItems: "center", gap: "12px",
            }}>
              <div style={{ display: "flex", gap: "5px", flexShrink: 0 }}>
                {[0, 1, 2].map(i => (
                  <div key={i} style={{
                    width: "7px", height: "7px", borderRadius: "50%",
                    background: "#52b788",
                    animation: `pulse 1.2s ease-in-out ${i * 0.4}s infinite`,
                  }}/>
                ))}
              </div>
              <div style={{ fontSize: "13px", color: "#8fd4b0" }}>{autoFillProgress || "Working..."}</div>
            </div>
          )}

          {/* Error */}
          {autoFillError && (
            <div style={{
              background: "rgba(220,38,38,0.08)", border: "1px solid rgba(220,38,38,0.25)",
              borderRadius: "8px", padding: "12px 16px", marginBottom: "18px",
              color: "#fca5a5", fontSize: "13px",
            }}>
              ⚠ {autoFillError}
            </div>
          )}

          {/* Action buttons */}
          <div style={{ display: "flex", gap: "12px", marginTop: "4px" }}>
            <button
              onClick={handleAutoFill}
              disabled={autoFilling || !quickStart.deviceName || !quickStart.indication || !quickStart.cost}
              style={{
                flex: 1,
                background: autoFilling
                  ? "rgba(82,183,136,0.3)"
                  : "linear-gradient(135deg,#2d6a4f,#52b788)",
                border: "none", borderRadius: "8px", color: "#fff",
                padding: "13px", fontSize: "14px", fontWeight: "600",
                cursor: (autoFilling || !quickStart.deviceName || !quickStart.indication || !quickStart.cost)
                  ? "not-allowed" : "pointer",
                opacity: (!quickStart.deviceName || !quickStart.indication || !quickStart.cost) ? 0.5 : 1,
                fontFamily: "'Georgia',serif",
              }}
            >
              {autoFilling ? "Gathering Evidence..." : "Auto-fill from Evidence"}
            </button>
            <button
              onClick={() => { pollingStopped.current = true; setShowAutoFill(false); setSection(0); }}
              disabled={autoFilling}
              style={{
                background: "transparent", border: "1px solid rgba(255,255,255,0.18)",
                borderRadius: "8px", color: "#6a8fb5", padding: "13px 20px",
                fontSize: "13px", cursor: autoFilling ? "not-allowed" : "pointer",
                fontFamily: "'Georgia',serif", whiteSpace: "nowrap",
              }}
            >
              Skip — manual entry
            </button>
          </div>
        </div>

        <p style={{ color: "#4a6482", fontSize: "12px", textAlign: "center", margin: 0, fontStyle: "italic" }}>
          Searches PubMed, NICE Technology Appraisals, and NHS Reference Costs · You can review and edit every value before submitting
        </p>
        <style>{`@keyframes pulse{0%,100%{opacity:0.3}50%{opacity:1}}`}</style>
      </div>
    );

    if (hideChrome) return quickStartContent;
    return (
      <div style={S.app}>
        <div style={S.header}>
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <div style={S.logoIcon}>⚙️</div>
            <div><div style={S.logoText}>HEOR Engine</div><div style={S.logoSub}>Input Collection Engine</div></div>
          </div>
          <div style={S.headerRight}>Budget Impact Analysis — NHS Payer Perspective</div>
        </div>
        {quickStartContent}
      </div>
    );
  }

  // ─── Success screen ───
  if (submitted) {
    const successContent = (
        <div style={{maxWidth:"800px",margin:"50px auto",padding:"0 20px"}}>
          {/* Success banner */}
          <div style={{background:"rgba(82,183,136,0.1)",border:"1px solid rgba(82,183,136,0.25)",borderRadius:"12px",padding:"28px 36px",marginBottom:"24px",display:"flex",alignItems:"center",gap:"20px"}}>
            <div style={{fontSize:"42px"}}>✅</div>
            <div>
              <div style={{fontSize:"22px",color:"#fff",marginBottom:"6px",fontWeight:"400"}}>All inputs captured</div>
              <div style={{fontSize:"13px",color:"#6a8fb5",fontStyle:"italic"}}>Your model inputs are structured and ready. Download the summary report below, then proceed to the BIA Engine.</div>
            </div>
          </div>

          {/* Summary grid */}
          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:"12px",marginBottom:"24px"}}>
            {[
              ["Setting", form.setting || "—"],
              ["Forecast", `${form.forecastYears} years from ${form.modelYear}`],
              ["Eligible cohort", eligibleN ? `${eligibleN.toLocaleString()} patients` : "—"],
              ["Uptake Y1 / Y2 / Y3", `${form.uptakeY1||0}% / ${form.uptakeY2||0}% / ${form.uptakeY3||0}%`],
              ["Pricing", `£${form.price||"0"} ${form.priceUnit} (${form.pricingModel})`],
              ["Workforce cost / patient", totalHourlyCost > 0 ? `£${totalHourlyCost.toFixed(2)}` : "—"],
              ["Funding route", form.fundingSource || "—"],
              ["Setup cost", form.setupCost ? `£${Number(form.setupCost).toLocaleString()}` : "None"],
            ].map(([label, value]) => (
              <div key={label} style={{background:"rgba(255,255,255,0.04)",border:"1px solid rgba(255,255,255,0.08)",borderRadius:"8px",padding:"16px 20px"}}>
                <div style={{fontSize:"10px",letterSpacing:"1.5px",textTransform:"uppercase",color:"#5a7fa8",marginBottom:"4px"}}>{label}</div>
                <div style={{fontSize:"15px",color:"#e8edf5"}}>{value}</div>
              </div>
            ))}
          </div>

          {/* Download cards */}
          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:"12px",marginBottom:"16px"}}>
            <div style={{background:"rgba(255,255,255,0.035)",border:"1px solid rgba(255,255,255,0.1)",borderRadius:"12px",padding:"24px 28px",display:"flex",flexDirection:"column",gap:"16px"}}>
              <div>
                <div style={{fontSize:"16px",color:"#fff",marginBottom:"6px",display:"flex",alignItems:"center",gap:"10px"}}>
                  <span style={{fontSize:"22px"}}>📄</span> Input Summary (PDF)
                </div>
                <div style={{fontSize:"12px",color:"#6a8fb5",lineHeight:"1.6"}}>
                  All four sections, workforce cost table, key metrics callout, and model assumptions.
                </div>
              </div>
              <button
                onClick={handleDownload}
                disabled={downloading}
                style={{background:downloading?"rgba(82,183,136,0.4)":"linear-gradient(135deg,#2d6a4f,#52b788)",border:"none",borderRadius:"8px",color:"#fff",padding:"12px 24px",fontSize:"14px",cursor:downloading?"wait":"pointer",fontWeight:"600",fontFamily:"'Georgia',serif",display:"flex",alignItems:"center",gap:"10px",justifyContent:"center",whiteSpace:"nowrap",marginTop:"auto"}}
              >
                {downloading ? <><span>⏳</span> Generating...</> : <><span>📥</span> Download PDF</>}
              </button>
            </div>
            <div style={{background:"rgba(255,255,255,0.035)",border:"1px solid rgba(255,255,255,0.1)",borderRadius:"12px",padding:"24px 28px",display:"flex",flexDirection:"column",gap:"16px"}}>
              <div>
                <div style={{fontSize:"16px",color:"#fff",marginBottom:"6px",display:"flex",alignItems:"center",gap:"10px"}}>
                  <span style={{fontSize:"22px"}}>📊</span> BIA Report (PowerPoint)
                </div>
                <div style={{fontSize:"12px",color:"#6a8fb5",lineHeight:"1.6"}}>
                  10-slide branded deck — executive summary, budget impact tables, scenario comparison, and assumptions.
                </div>
              </div>
              <button
                onClick={handlePptxDownload}
                disabled={generatingPptx}
                style={{background:generatingPptx?"rgba(45,106,79,0.4)":"linear-gradient(135deg,#1a4731,#2d6a4f)",border:"none",borderRadius:"8px",color:"#fff",padding:"12px 24px",fontSize:"14px",cursor:generatingPptx?"wait":"pointer",fontWeight:"600",fontFamily:"'Georgia',serif",display:"flex",alignItems:"center",gap:"10px",justifyContent:"center",whiteSpace:"nowrap",marginTop:"auto"}}
              >
                {generatingPptx ? <><span>⏳</span> Generating...</> : <><span>📥</span> Download PPTX</>}
              </button>
            </div>
          </div>

          {/* BIA Results */}
          {apiResult && apiResult.results && (() => {
            const res = apiResult.results;
            const fmt = v => v < 0 ? `-£${Math.abs(v).toLocaleString(undefined,{minimumFractionDigits:0,maximumFractionDigits:0})}` : `£${v.toLocaleString(undefined,{minimumFractionDigits:0,maximumFractionDigits:0})}`;
            const total3yr = res.annual_budget_impact.reduce((a,b) => a+b, 0);
            const warnings = apiResult.warnings || [];
            const scenarios = res.scenarios || {};
            return (
              <div style={{marginBottom:"24px"}}>
                {/* Warnings */}
                {warnings.length > 0 && (
                  <div style={{background:"rgba(82,183,136,0.08)",border:"1px solid rgba(82,183,136,0.2)",borderRadius:"8px",padding:"14px 18px",marginBottom:"16px",fontSize:"13px",color:"#8fd4b0"}}>
                    {warnings.map((w,i) => <div key={i}>⚠ {w}</div>)}
                  </div>
                )}

                {/* Key result cards */}
                <div style={{display:"grid",gridTemplateColumns:"1fr 1fr 1fr",gap:"12px",marginBottom:"16px"}}>
                  <div style={{background:"rgba(255,255,255,0.04)",border:"1px solid rgba(255,255,255,0.08)",borderRadius:"8px",padding:"16px 20px",textAlign:"center"}}>
                    <div style={{fontSize:"10px",letterSpacing:"1.5px",textTransform:"uppercase",color:"#5a7fa8",marginBottom:"6px"}}>3-Year Net Impact</div>
                    <div style={{fontSize:"22px",fontWeight:"700",color:total3yr<0?"#52b788":"#e85d6f"}}>{fmt(total3yr)}</div>
                    <div style={{fontSize:"11px",color:"#6a8fb5",marginTop:"4px"}}>{total3yr<0?"Net saving":"Net cost"}</div>
                  </div>
                  <div style={{background:"rgba(255,255,255,0.04)",border:"1px solid rgba(255,255,255,0.08)",borderRadius:"8px",padding:"16px 20px",textAlign:"center"}}>
                    <div style={{fontSize:"10px",letterSpacing:"1.5px",textTransform:"uppercase",color:"#5a7fa8",marginBottom:"6px"}}>Break-Even Year</div>
                    <div style={{fontSize:"22px",fontWeight:"700",color:"#52b788"}}>{res.break_even_year ? `Year ${res.break_even_year}` : "N/A"}</div>
                    <div style={{fontSize:"11px",color:"#6a8fb5",marginTop:"4px"}}>{res.break_even_year ? "Cumulative savings exceed costs" : "Not within forecast horizon"}</div>
                  </div>
                  <div style={{background:"rgba(255,255,255,0.04)",border:"1px solid rgba(255,255,255,0.08)",borderRadius:"8px",padding:"16px 20px",textAlign:"center"}}>
                    <div style={{fontSize:"10px",letterSpacing:"1.5px",textTransform:"uppercase",color:"#5a7fa8",marginBottom:"6px"}}>Top Cost Driver</div>
                    <div style={{fontSize:"16px",fontWeight:"700",color:"#e8edf5"}}>{(res.top_cost_drivers||[])[0]}</div>
                    <div style={{fontSize:"11px",color:"#6a8fb5",marginTop:"4px"}}>{(res.top_cost_drivers||[]).slice(1).join(" > ")}</div>
                  </div>
                </div>

                {/* Year-by-year table */}
                <div style={{background:"rgba(255,255,255,0.03)",border:"1px solid rgba(255,255,255,0.08)",borderRadius:"8px",overflow:"hidden",marginBottom:"16px"}}>
                  <div style={{background:"rgba(13,32,68,0.8)",padding:"10px 18px",fontSize:"10px",letterSpacing:"2px",textTransform:"uppercase",color:"#5a7fa8"}}>Annual Budget Impact (Base Case)</div>
                  <table style={{width:"100%",borderCollapse:"collapse",fontSize:"13px"}}>
                    <thead>
                      <tr style={{borderBottom:"1px solid rgba(255,255,255,0.08)"}}>
                        {["Year","Treated Patients","Cost/Patient","Annual Impact"].map(h => (
                          <th key={h} style={{padding:"10px 18px",textAlign:"right",color:"#7a9fc4",fontWeight:"400",fontSize:"11px",letterSpacing:"0.5px"}}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {[0,1,2].map(yr => (
                        <tr key={yr} style={{borderBottom:"1px solid rgba(255,255,255,0.05)",background:yr%2===0?"rgba(255,255,255,0.02)":"transparent"}}>
                          <td style={{padding:"10px 18px",textAlign:"right",color:"#e8edf5"}}>Year {yr+1}</td>
                          <td style={{padding:"10px 18px",textAlign:"right",color:"#e8edf5"}}>{(res.total_treated_patients[yr]||0).toLocaleString()}</td>
                          <td style={{padding:"10px 18px",textAlign:"right",color:"#e8edf5"}}>{fmt(res.cost_per_patient[yr]||0)}</td>
                          <td style={{padding:"10px 18px",textAlign:"right",fontWeight:"600",color:res.annual_budget_impact[yr]<0?"#52b788":"#e85d6f"}}>{fmt(res.annual_budget_impact[yr]||0)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* Scenario comparison */}
                {Object.keys(scenarios).length > 0 && (
                  <div style={{background:"rgba(255,255,255,0.03)",border:"1px solid rgba(255,255,255,0.08)",borderRadius:"8px",overflow:"hidden"}}>
                    <div style={{background:"rgba(13,32,68,0.8)",padding:"10px 18px",fontSize:"10px",letterSpacing:"2px",textTransform:"uppercase",color:"#5a7fa8"}}>Scenario Comparison (3-Year Total)</div>
                    {["conservative","base","optimistic"].filter(n => scenarios[n]).map(name => {
                      const sc = scenarios[name];
                      const t = sc.annual_budget_impact.reduce((a,b) => a+b, 0);
                      const maxAbs = Math.max(...["conservative","base","optimistic"].filter(n=>scenarios[n]).map(n=>Math.abs(scenarios[n].annual_budget_impact.reduce((a,b)=>a+b,0))));
                      const barPct = maxAbs > 0 ? Math.round(Math.abs(t) / maxAbs * 100) : 0;
                      return (
                        <div key={name} style={{display:"flex",alignItems:"center",padding:"12px 18px",borderBottom:"1px solid rgba(255,255,255,0.05)",gap:"16px"}}>
                          <div style={{width:"100px",fontSize:"12px",color:"#7a9fc4",textTransform:"capitalize"}}>{name}</div>
                          <div style={{flex:1,height:"8px",background:"rgba(255,255,255,0.06)",borderRadius:"4px",overflow:"hidden"}}>
                            <div style={{height:"100%",width:`${barPct}%`,background:t<0?"linear-gradient(90deg,#2d6a4f,#52b788)":"linear-gradient(90deg,#c0392b,#e74c3c)",borderRadius:"4px"}}/>
                          </div>
                          <div style={{width:"140px",textAlign:"right",fontSize:"13px",fontWeight:"600",color:t<0?"#52b788":"#e85d6f"}}>{fmt(t)}</div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })()}

          {/* Next step */}
          <div style={{padding:"14px 20px",background:"rgba(13,32,68,0.6)",borderRadius:"8px",border:"1px solid rgba(255,255,255,0.06)",fontSize:"12px",color:"#5a7fa8",marginBottom:"16px"}}>
            {apiResult ? "✓ Inputs saved and BIA calculated successfully." : "→ Next: pass these inputs to the BIA Engine to generate annual net budget impact, break-even year, and scenario outputs."}
            {apiResult && apiResult.workflow_id && <span style={{marginLeft:"8px",color:"#4a6fa5"}}>Workflow ID: {apiResult.workflow_id}</span>}
          </div>

          <div style={{ display: "flex", gap: "10px", flexWrap: "wrap" }}>
            <button
              onClick={() => {
                setSubmitted(false); setSection(0); setCompleted([]);
                setApiResult(null); setApiError(null);
                setAutoFillEvidence(null); setShowAutoFill(false);
              }}
              style={{ background: "transparent", border: "1px solid rgba(255,255,255,0.1)", borderRadius: "6px", color: "#5a7fa8", padding: "10px 20px", fontSize: "12px", cursor: "pointer", fontFamily: "'Georgia',serif" }}
            >
              ← Start new inputs (manual)
            </button>
            <button
              onClick={() => {
                setSubmitted(false); setSection(0); setCompleted([]);
                setApiResult(null); setApiError(null);
                setAutoFillEvidence(null); setShowAutoFill(true);
                setQuickStart({ deviceName: "", indication: "", cost: "", benefits: "" });
              }}
              style={{ background: "rgba(82,183,136,0.1)", border: "1px solid rgba(82,183,136,0.25)", borderRadius: "6px", color: "#52b788", padding: "10px 20px", fontSize: "12px", cursor: "pointer", fontFamily: "'Georgia',serif" }}
            >
              ✨ New auto-fill
            </button>
          </div>
        </div>
    );
    if (hideChrome) return successContent;
    return (
      <div style={S.app}>
        <div style={S.header}>
          <div style={{display:"flex",alignItems:"center",gap:"10px"}}>
            <div style={S.logoIcon}>⚙️</div>
            <div><div style={S.logoText}>HEOR Engine</div><div style={S.logoSub}>Input Collection</div></div>
          </div>
          <div style={S.headerRight}>Budget Impact Analysis — NHS Payer Perspective</div>
        </div>
        {successContent}
      </div>
    );
  }

  // ─── Main form ───
  const formContent = (
    <>
      <div style={S.main}>
        <div style={S.sidebar}>
          <div style={S.sidebarTitle}>Sections</div>
          {SECTIONS.map((sec, idx) => (
            <div key={sec} style={S.navItem(idx)} onClick={()=>setSection(idx)}>
              <span style={{fontSize:"18px",width:"28px",textAlign:"center"}}>{SECTION_ICONS[idx]}</span>
              <span style={{fontSize:"13.5px"}}>{sec}</span>
              <div style={S.navStep(idx)}>{completed.includes(idx)?"✓":idx+1}</div>
            </div>
          ))}
          <div style={S.progressWrap}>
            <div style={{fontSize:"10px",letterSpacing:"1.5px",textTransform:"uppercase",color:"#3a5a7a"}}>Progress</div>
            <div style={S.progressBar}><div style={S.progressFill((section+1)/SECTIONS.length*100)}/></div>
            <div style={{fontSize:"12px",color:"#4a6fa5"}}>{completed.length} of {SECTIONS.length} complete</div>
          </div>
        </div>

        <div style={S.content}>
          <div style={{fontSize:"11px",letterSpacing:"2px",textTransform:"uppercase",color:"#3a5a7a",marginBottom:"8px"}}>Step {section+1} of {SECTIONS.length}</div>
          <div style={S.sectionTitle}>{SECTIONS[section]}</div>
          <div style={S.sectionSub}>
            {["Define the NHS setting, forecast horizon, and funding context.","Quantify who is eligible and model uptake over time.","Map the current pathway — workforce time and resource use.","Set intervention pricing, implementation costs, and resource deltas."][section]}
          </div>

          {/* ── Evidence panel (shown when auto-fill was used) ── */}
          {autoFillEvidence && (
            <div style={{
              background: "rgba(82,183,136,0.07)",
              border: "1px solid rgba(82,183,136,0.22)",
              borderRadius: "8px", marginBottom: "22px", overflow: "hidden",
            }}>
              {/* Summary bar */}
              <div style={{
                padding: "12px 18px", display: "flex", alignItems: "center",
                justifyContent: "space-between", flexWrap: "wrap", gap: "10px",
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                  <span style={{ fontSize: "16px" }}>📚</span>
                  <div>
                    <span style={{ color: "#52b788", fontWeight: "700", fontSize: "13px" }}>
                      Auto-filled from evidence
                    </span>
                    <span style={{ color: "#5a7fa8", fontSize: "12px", marginLeft: "10px" }}>
                      {autoFillEvidence.evidenceSummary.papers_found || 0} papers ·{" "}
                      {autoFillEvidence.evidenceSummary.nice_guidance_found || 0} NICE docs ·{" "}
                      quality: {autoFillEvidence.evidenceSummary.data_quality || "unknown"}
                    </span>
                  </div>
                </div>
                <div style={{ display: "flex", gap: "8px" }}>
                  {/* Overall confidence pill */}
                  {autoFillEvidence.confidence.overall && (
                    <span style={{
                      padding: "3px 10px", borderRadius: "12px", fontSize: "11px", fontWeight: "600",
                      background: autoFillEvidence.confidence.overall === "high"
                        ? "rgba(82,183,136,0.25)" : autoFillEvidence.confidence.overall === "medium"
                        ? "rgba(255,193,7,0.2)" : "rgba(255,100,100,0.2)",
                      color: autoFillEvidence.confidence.overall === "high" ? "#52b788"
                        : autoFillEvidence.confidence.overall === "medium" ? "#ffc107" : "#ff6464",
                    }}>
                      {autoFillEvidence.confidence.overall} confidence
                    </span>
                  )}
                  <button
                    onClick={() => setShowAutoFill(true)}
                    style={{
                      background: "transparent", border: "1px solid rgba(82,183,136,0.35)",
                      borderRadius: "5px", color: "#52b788", padding: "3px 10px",
                      fontSize: "11px", cursor: "pointer", fontFamily: "'Georgia',serif",
                    }}
                  >
                    Re-run
                  </button>
                  <button
                    onClick={() => setAutoFillEvidence(null)}
                    style={{
                      background: "transparent", border: "1px solid rgba(255,255,255,0.1)",
                      borderRadius: "5px", color: "#5a7fa8", padding: "3px 8px",
                      fontSize: "11px", cursor: "pointer", fontFamily: "'Georgia',serif",
                    }}
                  >
                    ✕
                  </button>
                </div>
              </div>

              {/* Warnings */}
              {autoFillEvidence.warnings.length > 0 && (
                <div style={{
                  borderTop: "1px solid rgba(255,193,7,0.2)",
                  background: "rgba(255,193,7,0.06)", padding: "10px 18px",
                }}>
                  <div style={{ fontSize: "11px", fontWeight: "700", color: "#ffc107", marginBottom: "5px" }}>
                    ⚠ Review needed
                  </div>
                  {autoFillEvidence.warnings.slice(0, 3).map((w, i) => (
                    <div key={i} style={{ fontSize: "12px", color: "#ffb84d", marginBottom: "3px" }}>
                      • {w}
                    </div>
                  ))}
                </div>
              )}

              {/* Sources (collapsible) */}
              {autoFillEvidence.sources.length > 0 && (
                <details style={{ borderTop: "1px solid rgba(255,255,255,0.06)" }}>
                  <summary style={{
                    padding: "10px 18px", cursor: "pointer", fontSize: "12px",
                    color: "#6a8fb5", listStyle: "none", userSelect: "none",
                  }}>
                    ▸ {autoFillEvidence.sources.length} evidence sources used
                  </summary>
                  <div style={{ maxHeight: "200px", overflowY: "auto", padding: "0 18px 12px" }}>
                    {autoFillEvidence.sources.map((src, i) => (
                      <div key={i} style={{
                        padding: "7px 0", borderBottom: "1px solid rgba(255,255,255,0.05)",
                        fontSize: "12px",
                      }}>
                        <div style={{ color: "#e8edf5" }}>
                          {src.url
                            ? <a href={src.url} target="_blank" rel="noreferrer"
                                style={{ color: "#7ab8e8", textDecoration: "none" }}>
                                {src.title || src.id || src.url}
                              </a>
                            : (src.title || src.id || src.source || "—")
                          }
                        </div>
                        <div style={{ color: "#4a6482", fontSize: "11px", marginTop: "2px" }}>
                          [{src.type}]
                          {src.pmid && <> · PMID {src.pmid}</>}
                          {src.year && <> · {src.year}</>}
                          {src.journal && <> · {src.journal}</>}
                        </div>
                      </div>
                    ))}
                  </div>
                </details>
              )}
            </div>
          )}

          {renderSection()}
          {apiError && (
            <div style={{background:"rgba(220,53,69,0.1)",border:"1px solid rgba(220,53,69,0.3)",borderRadius:"8px",padding:"12px 18px",marginTop:"16px",fontSize:"13px",color:"#e85d6f"}}>
              API Error: {apiError}
            </div>
          )}
          <div style={S.btnRow}>
            {section > 0 && <button style={S.btnBack} onClick={()=>setSection(s=>s-1)}>← Back</button>}
            <button style={S.btnNext} onClick={markComplete} disabled={submitting}>
              {submitting ? "Submitting..." : section===SECTIONS.length-1 ? "Submit inputs →" : "Save & continue →"}
            </button>
          </div>
        </div>
      </div>
    </>
  );
  if (hideChrome) return formContent;
  return (
    <div style={S.app}>
      <div style={S.header}>
        <div style={{display:"flex",alignItems:"center",gap:"10px"}}>
          <div style={S.logoIcon}>⚙️</div>
          <div><div style={S.logoText}>HEOR Engine</div><div style={S.logoSub}>Input Collection Engine</div></div>
        </div>
        <div style={S.headerRight}>Budget Impact Analysis — NHS Payer Perspective</div>
      </div>
      {formContent}
    </div>
  );
}
