import { useState, useEffect } from "react";

const API_BASE = "http://localhost:8000";

const CONDITION_DEFAULTS = {
  cancer: { label: "Cancer", prob_death: 0.15, utility: 0.60, cost: 12000 },
  cardiovascular: { label: "Cardiovascular", prob_death: 0.08, utility: 0.70, cost: 6000 },
  diabetes: { label: "Diabetes", prob_death: 0.05, utility: 0.75, cost: 4000 },
  respiratory: { label: "Respiratory", prob_death: 0.10, utility: 0.65, cost: 5000 },
};

const SECTIONS = ["Intervention Details", "Standard Care", "Treatment Arm", "Quick Calculators"];
const SECTION_ICONS = ["\u{1F3AF}", "\u{1F3E5}", "\u{1F48A}", "\u{1F9EE}"];

function fmtGBP(v) {
  if (v == null) return "\u2014";
  return "\u00A3" + Number(v).toLocaleString("en-GB", { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}

function fmtQALY(v) {
  if (v == null) return "\u2014";
  return Number(v).toFixed(2);
}

export default function MarkovICERForm({ hideChrome = false, externalFillData = null, skipQuickStart = false }) {
  const [section, setSection] = useState(0);
  const [completed, setCompleted] = useState([]);
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [results, setResults] = useState(null);
  const [reportUrl, setReportUrl] = useState(null);
  const [apiError, setApiError] = useState(null);

  const [form, setForm] = useState({
    interventionName: "",
    timeHorizon: "5",
    discountRate: "0.035",
    probDeathStandard: "",
    costStandardAnnual: "",
    utilityStandard: "0.70",
    probDeathTreatment: "",
    costTreatmentAnnual: "",
    costTreatmentInitial: "",
    utilityTreatment: "0.80",
    // Quick calculator helpers
    conditionPreset: "",
    mortalityReductionPct: "",
    utilityGainPct: "",
  });

  const [errors, setErrors] = useState({});

  // Merge externally-provided fill data (from App.jsx MarkovAutoFillModal)
  useEffect(() => {
    if (!externalFillData) return;
    setForm(f => ({ ...f, ...externalFillData }));
  }, [externalFillData]);

  const update = (field, value) => {
    setForm(f => ({ ...f, [field]: value }));
    setErrors(e => ({ ...e, [field]: undefined }));
  };

  // Apply condition preset
  const applyCondition = (key) => {
    if (!key || !CONDITION_DEFAULTS[key]) return;
    const d = CONDITION_DEFAULTS[key];
    setForm(f => ({
      ...f,
      conditionPreset: key,
      probDeathStandard: String(d.prob_death),
      costStandardAnnual: String(d.cost),
      utilityStandard: String(d.utility),
    }));
    setErrors({});
  };

  // Mortality reduction calculator
  const applyMortalityReduction = () => {
    const baseMort = parseFloat(form.probDeathStandard);
    const reductionPct = parseFloat(form.mortalityReductionPct);
    if (!isNaN(baseMort) && !isNaN(reductionPct) && reductionPct > 0 && reductionPct <= 100) {
      const trtMort = baseMort * (1 - reductionPct / 100);
      update("probDeathTreatment", trtMort.toFixed(4));
    }
  };

  // Utility gain calculator
  const applyUtilityGain = () => {
    const baseUtil = parseFloat(form.utilityStandard);
    const gainPct = parseFloat(form.utilityGainPct);
    if (!isNaN(baseUtil) && !isNaN(gainPct) && gainPct > 0) {
      const trtUtil = Math.min(baseUtil + gainPct / 100, 1.0);
      update("utilityTreatment", trtUtil.toFixed(2));
    }
  };

  // Auto-recalculate when dependencies change
  useEffect(() => {
    if (form.mortalityReductionPct && form.probDeathStandard) applyMortalityReduction();
  }, [form.probDeathStandard, form.mortalityReductionPct]);

  useEffect(() => {
    if (form.utilityGainPct && form.utilityStandard) applyUtilityGain();
  }, [form.utilityStandard, form.utilityGainPct]);

  // Validation
  const validate = () => {
    const e = {};
    if (!form.interventionName.trim()) e.interventionName = "Required";
    const pdStd = parseFloat(form.probDeathStandard);
    if (isNaN(pdStd) || pdStd < 0 || pdStd > 1) e.probDeathStandard = "Must be 0\u20131";
    const cStd = parseFloat(form.costStandardAnnual);
    if (isNaN(cStd) || cStd < 0) e.costStandardAnnual = "Must be \u2265 0";
    const uStd = parseFloat(form.utilityStandard);
    if (isNaN(uStd) || uStd < 0 || uStd > 1) e.utilityStandard = "Must be 0\u20131";
    const pdTrt = parseFloat(form.probDeathTreatment);
    if (isNaN(pdTrt) || pdTrt < 0 || pdTrt > 1) e.probDeathTreatment = "Must be 0\u20131";
    const cTrt = parseFloat(form.costTreatmentAnnual);
    if (isNaN(cTrt) || cTrt < 0) e.costTreatmentAnnual = "Must be \u2265 0";
    const uTrt = parseFloat(form.utilityTreatment);
    if (isNaN(uTrt) || uTrt < 0 || uTrt > 1) e.utilityTreatment = "Must be 0\u20131";
    const cInit = form.costTreatmentInitial ? parseFloat(form.costTreatmentInitial) : 0;
    if (isNaN(cInit) || cInit < 0) e.costTreatmentInitial = "Must be \u2265 0";
    setErrors(e);
    return Object.keys(e).length === 0;
  };

  const handleSubmit = async () => {
    if (!validate()) return;
    setSubmitting(true);
    setApiError(null);
    setReportUrl(null);
    try {
      const payload = {
        intervention_name: form.interventionName.trim(),
        time_horizon: parseInt(form.timeHorizon),
        discount_rate: parseFloat(form.discountRate),
        prob_death_standard: parseFloat(form.probDeathStandard),
        cost_standard_annual: parseFloat(form.costStandardAnnual),
        utility_standard: parseFloat(form.utilityStandard),
        prob_death_treatment: parseFloat(form.probDeathTreatment),
        cost_treatment_annual: parseFloat(form.costTreatmentAnnual),
        cost_treatment_initial: parseFloat(form.costTreatmentInitial) || 0,
        utility_treatment: parseFloat(form.utilityTreatment),
      };
      const resp = await fetch(`${API_BASE}/api/generate-cea-report`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(err.detail || JSON.stringify(err));
      }
      const data = await resp.json();
      setResults(data.results);
      setReportUrl(`${API_BASE}${data.download_url}`);
      setSubmitted(true);
    } catch (e) {
      setApiError(e.message || "Failed to connect to API");
    } finally {
      setSubmitting(false);
    }
  };

  const handleNext = () => {
    if (!completed.includes(section)) setCompleted(c => [...c, section]);
    if (section < SECTIONS.length - 1) {
      setSection(s => s + 1);
    } else {
      handleSubmit();
    }
  };

  const handleReset = () => {
    setSubmitted(false);
    setResults(null);
    setReportUrl(null);
    setApiError(null);
    setSection(0);
    setCompleted([]);
  };

  const progress = Math.round(((completed.length) / SECTIONS.length) * 100);

  // ─── Styles (matching BIA form) ───
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
    inputError: { borderColor: "#e85d6f" },
    select: { width: "100%", background: "#0d2044", border: "1px solid rgba(255,255,255,0.12)", borderRadius: "6px", padding: "10px 14px", color: "#e8edf5", fontSize: "14px", outline: "none", boxSizing: "border-box", fontFamily: "'Georgia',serif" },
    grid2: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" },
    grid3: { display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "16px" },
    infoBox: { background: "rgba(82,183,136,0.08)", border: "1px solid rgba(82,183,136,0.2)", borderRadius: "8px", padding: "14px 18px", marginBottom: "24px", fontSize: "13px", color: "#8fd4b0", lineHeight: "1.6" },
    radioGroup: { display: "flex", gap: "10px", flexWrap: "wrap" },
    radioBtn: (active) => ({ padding: "8px 16px", borderRadius: "6px", border: active ? "1px solid #52b788" : "1px solid rgba(255,255,255,0.12)", background: active ? "rgba(82,183,136,0.15)" : "rgba(255,255,255,0.04)", color: active ? "#52b788" : "#8fa8c8", cursor: "pointer", fontSize: "13px", fontFamily: "'Georgia',serif" }),
    hint: { fontSize: "11px", color: "#5a7fa8", marginTop: "4px" },
    errorText: { fontSize: "11px", color: "#e85d6f", marginTop: "4px" },
    divider: { borderColor: "rgba(255,255,255,0.07)", margin: "24px 0" },
    divLabel: { fontSize: "10px", letterSpacing: "2px", textTransform: "uppercase", color: "#5a7fa8", marginBottom: "16px" },
    btnRow: { display: "flex", gap: "12px", marginTop: "32px" },
    btnBack: { background: "transparent", border: "1px solid rgba(255,255,255,0.15)", borderRadius: "6px", color: "#8fa8c8", padding: "11px 22px", fontSize: "13px", cursor: "pointer", fontFamily: "'Georgia',serif" },
    btnNext: { background: "linear-gradient(135deg,#2d6a4f,#52b788)", border: "none", borderRadius: "6px", color: "#fff", padding: "11px 28px", fontSize: "13px", cursor: "pointer", fontWeight: "600", marginLeft: "auto", fontFamily: "'Georgia',serif" },
    btnDisabled: { opacity: 0.5, cursor: "not-allowed" },
    progressWrap: { marginTop: "auto", padding: "0 24px 16px" },
    progressBar: { height: "3px", background: "rgba(255,255,255,0.08)", borderRadius: "2px", margin: "12px 0 6px" },
    progressFill: (pct) => ({ height: "100%", background: "linear-gradient(90deg,#2d6a4f,#52b788)", borderRadius: "2px", width: `${pct}%`, transition: "width 0.3s ease" }),
    slider: { width: "100%", accentColor: "#52b788", cursor: "pointer" },
    sliderLabels: { display: "flex", justifyContent: "space-between", fontSize: "10px", color: "#5a7fa8", marginTop: "2px" },
    sliderValue: { fontSize: "20px", fontWeight: "700", color: "#52b788", textAlign: "center", marginBottom: "4px" },
    calcBox: { background: "rgba(45,106,79,0.1)", border: "1px solid rgba(82,183,136,0.25)", borderRadius: "8px", padding: "16px 18px", marginBottom: "20px" },
    calcTitle: { fontSize: "12px", letterSpacing: "1px", textTransform: "uppercase", color: "#52b788", marginBottom: "10px", fontWeight: "600" },
    calcRow: { display: "flex", alignItems: "center", gap: "10px", marginBottom: "8px" },
    calcBtn: { background: "rgba(82,183,136,0.2)", border: "1px solid rgba(82,183,136,0.4)", borderRadius: "6px", color: "#52b788", padding: "6px 14px", fontSize: "12px", cursor: "pointer", fontFamily: "'Georgia',serif", whiteSpace: "nowrap" },
    comingSoon: { background: "rgba(255,255,255,0.04)", border: "1px dashed rgba(255,255,255,0.15)", borderRadius: "8px", padding: "16px 18px", textAlign: "center", color: "#5a7fa8", fontSize: "13px", fontStyle: "italic" },
    // Results styles
    resultsCard: { background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.10)", borderRadius: "10px", padding: "24px", marginBottom: "20px" },
    resultsTitle: { fontSize: "18px", fontWeight: "600", color: "#fff", marginBottom: "16px" },
    resultRow: { display: "flex", justifyContent: "space-between", padding: "8px 0", borderBottom: "1px solid rgba(255,255,255,0.06)" },
    resultLabel: { color: "#8fa8c8", fontSize: "13px" },
    resultValue: { color: "#e8edf5", fontSize: "14px", fontWeight: "600" },
    icerBadge: (ce25, ce35) => ({
      display: "inline-block",
      padding: "6px 16px",
      borderRadius: "20px",
      fontSize: "13px",
      fontWeight: "700",
      background: ce25 ? "rgba(82,183,136,0.2)" : ce35 ? "rgba(255,193,7,0.15)" : "rgba(220,53,69,0.15)",
      color: ce25 ? "#52b788" : ce35 ? "#ffc107" : "#e85d6f",
      border: ce25 ? "1px solid rgba(82,183,136,0.4)" : ce35 ? "1px solid rgba(255,193,7,0.3)" : "1px solid rgba(220,53,69,0.3)",
    }),
    thresholdRow: { display: "flex", alignItems: "center", gap: "8px", padding: "6px 0", fontSize: "13px" },
    thresholdIcon: (pass) => ({ color: pass ? "#52b788" : "#e85d6f", fontSize: "16px" }),
  };

  const fg = { marginBottom: "24px" };

  // ─── Input field helper (plain function, not a component — avoids remount on re-render) ───
  const field = (label, fieldName, { type = "number", placeholder, hint, step, style: extraStyle } = {}) => (
    <div style={fg} key={fieldName}>
      <label style={S.label}>{label}</label>
      <input
        type={type}
        style={{ ...S.input, ...(errors[fieldName] ? S.inputError : {}), ...extraStyle }}
        value={form[fieldName]}
        onChange={e => update(fieldName, e.target.value)}
        placeholder={placeholder}
        step={step}
      />
      {errors[fieldName] && <div style={S.errorText}>{errors[fieldName]}</div>}
      {hint && !errors[fieldName] && <div style={S.hint}>{hint}</div>}
    </div>
  );

  // ─── Slider helper (plain function) ───
  const utilitySlider = (label, fieldName) => {
    const val = parseFloat(form[fieldName]) || 0;
    return (
      <div style={fg} key={fieldName}>
        <label style={S.label}>{label}</label>
        <div style={S.sliderValue}>{val.toFixed(2)}</div>
        <input
          type="range"
          min="0" max="1" step="0.01"
          style={S.slider}
          value={val}
          onChange={e => update(fieldName, e.target.value)}
        />
        <div style={S.sliderLabels}>
          <span>0.0 — Dead</span>
          <span>0.5 — Moderate</span>
          <span>1.0 — Perfect health</span>
        </div>
        {errors[fieldName] && <div style={S.errorText}>{errors[fieldName]}</div>}
      </div>
    );
  };

  // ─── Section renders ───
  const renderSection = () => {
    switch (section) {
      case 0: return (
        <>
          <div style={S.sectionTitle}>Intervention Details</div>
          <div style={S.sectionSub}>Define the treatment being evaluated and model configuration.</div>
          <div style={S.infoBox}>
            The Markov model compares a treatment arm against standard care over a defined time horizon, calculating the Incremental Cost-Effectiveness Ratio (ICER) against NICE willingness-to-pay thresholds.
          </div>
          {field("Intervention name", "interventionName", { type: "text", placeholder: "e.g., AI Wound Camera, New Cancer Drug" })}
          <div style={S.grid2}>
            <div style={fg}>
              <label style={S.label}>Time horizon</label>
              <div style={S.radioGroup}>
                {["3", "5", "10"].map(y => (
                  <div key={y} style={S.radioBtn(form.timeHorizon === y)} onClick={() => update("timeHorizon", y)}>{y} years</div>
                ))}
              </div>
            </div>
            <div style={fg}>
              <label style={S.label}>Discount rate</label>
              <div style={S.radioGroup}>
                {[
                  { v: "0.035", l: "3.5% (NICE)" },
                  { v: "0.015", l: "1.5%" },
                  { v: "0", l: "None" },
                ].map(r => (
                  <div key={r.v} style={S.radioBtn(form.discountRate === r.v)} onClick={() => update("discountRate", r.v)}>{r.l}</div>
                ))}
              </div>
            </div>
          </div>
        </>
      );

      case 1: return (
        <>
          <div style={S.sectionTitle}>Standard Care Arm</div>
          <div style={S.sectionSub}>Define outcomes and costs for the current standard of care.</div>
          <div style={S.infoBox}>
            Use the condition preset below to auto-fill typical values, or enter custom figures from your evidence base.
          </div>
          <div style={fg}>
            <label style={S.label}>Condition preset</label>
            <div style={S.radioGroup}>
              {Object.entries(CONDITION_DEFAULTS).map(([k, v]) => (
                <div key={k} style={S.radioBtn(form.conditionPreset === k)} onClick={() => applyCondition(k)}>{v.label}</div>
              ))}
            </div>
            <div style={S.hint}>Pre-fills standard care values with population-level averages</div>
          </div>
          <hr style={S.divider} />
          <div style={S.grid2}>
            {field("Annual mortality probability", "probDeathStandard", { placeholder: "e.g., 0.08", step: "0.01", hint: "0.05 = 5% annual mortality" })}
            {field("Annual cost (\u00A3)", "costStandardAnnual", { placeholder: "e.g., 8000", hint: "Cost per patient per year under standard care" })}
          </div>
          {utilitySlider("Quality of life (utility)", "utilityStandard")}
        </>
      );

      case 2: return (
        <>
          <div style={S.sectionTitle}>Treatment Arm</div>
          <div style={S.sectionSub}>Define outcomes and costs for the new treatment.</div>
          <div style={S.grid2}>
            {field("Annual mortality probability", "probDeathTreatment", { placeholder: "e.g., 0.04", step: "0.01", hint: "Should be lower than standard care if treatment reduces mortality" })}
            {field("Annual cost (\u00A3)", "costTreatmentAnnual", { placeholder: "e.g., 12000", hint: "Ongoing annual cost per patient" })}
          </div>
          {field("Initial cost (\u00A3) \u2014 optional", "costTreatmentInitial", { placeholder: "e.g., 25000", hint: "One-time upfront cost (device purchase, surgery, etc.)" })}
          {utilitySlider("Quality of life (utility)", "utilityTreatment")}
        </>
      );

      case 3: return (
        <>
          <div style={S.sectionTitle}>Quick Calculators</div>
          <div style={S.sectionSub}>Helper tools to derive treatment values from relative improvements.</div>

          <div style={S.calcBox}>
            <div style={S.calcTitle}>Mortality Reduction Calculator</div>
            <div style={S.calcRow}>
              <span style={{ color: "#8fa8c8", fontSize: "13px" }}>Treatment reduces mortality by</span>
              <input
                type="number"
                style={{ ...S.input, width: "80px", textAlign: "center" }}
                value={form.mortalityReductionPct}
                onChange={e => update("mortalityReductionPct", e.target.value)}
                placeholder="%"
                min="0" max="100"
              />
              <span style={{ color: "#8fa8c8", fontSize: "13px" }}>%</span>
            </div>
            {form.probDeathStandard && form.mortalityReductionPct && (
              <div style={{ fontSize: "12px", color: "#52b788", marginTop: "4px" }}>
                {form.probDeathStandard} \u00D7 (1 \u2212 {form.mortalityReductionPct}%) = {form.probDeathTreatment} annual mortality
              </div>
            )}
          </div>

          <div style={S.calcBox}>
            <div style={S.calcTitle}>Utility Gain Calculator</div>
            <div style={S.calcRow}>
              <span style={{ color: "#8fa8c8", fontSize: "13px" }}>Treatment improves QoL by</span>
              <input
                type="number"
                style={{ ...S.input, width: "80px", textAlign: "center" }}
                value={form.utilityGainPct}
                onChange={e => update("utilityGainPct", e.target.value)}
                placeholder="%"
                min="0" max="100"
              />
              <span style={{ color: "#8fa8c8", fontSize: "13px" }}>% (absolute points)</span>
            </div>
            {form.utilityStandard && form.utilityGainPct && (
              <div style={{ fontSize: "12px", color: "#52b788", marginTop: "4px" }}>
                {form.utilityStandard} + {form.utilityGainPct}% = {form.utilityTreatment} utility
              </div>
            )}
          </div>

          <hr style={S.divider} />
          <div style={S.divLabel}>Input Summary</div>
          <div style={S.grid2}>
            <div style={{ ...S.resultsCard, padding: "16px" }}>
              <div style={{ fontSize: "11px", color: "#5a7fa8", textTransform: "uppercase", letterSpacing: "1px", marginBottom: "8px" }}>Standard Care</div>
              <div style={{ fontSize: "13px", color: "#8fa8c8", marginBottom: "4px" }}>Mortality: <span style={{ color: "#e8edf5", fontWeight: "600" }}>{form.probDeathStandard || "\u2014"}</span></div>
              <div style={{ fontSize: "13px", color: "#8fa8c8", marginBottom: "4px" }}>Cost/yr: <span style={{ color: "#e8edf5", fontWeight: "600" }}>{form.costStandardAnnual ? fmtGBP(form.costStandardAnnual) : "\u2014"}</span></div>
              <div style={{ fontSize: "13px", color: "#8fa8c8" }}>Utility: <span style={{ color: "#e8edf5", fontWeight: "600" }}>{form.utilityStandard || "\u2014"}</span></div>
            </div>
            <div style={{ ...S.resultsCard, padding: "16px" }}>
              <div style={{ fontSize: "11px", color: "#5a7fa8", textTransform: "uppercase", letterSpacing: "1px", marginBottom: "8px" }}>Treatment</div>
              <div style={{ fontSize: "13px", color: "#8fa8c8", marginBottom: "4px" }}>Mortality: <span style={{ color: "#e8edf5", fontWeight: "600" }}>{form.probDeathTreatment || "\u2014"}</span></div>
              <div style={{ fontSize: "13px", color: "#8fa8c8", marginBottom: "4px" }}>Cost/yr: <span style={{ color: "#e8edf5", fontWeight: "600" }}>{form.costTreatmentAnnual ? fmtGBP(form.costTreatmentAnnual) : "\u2014"}</span></div>
              <div style={{ fontSize: "13px", color: "#8fa8c8", marginBottom: "4px" }}>Initial: <span style={{ color: "#e8edf5", fontWeight: "600" }}>{form.costTreatmentInitial ? fmtGBP(form.costTreatmentInitial) : "\u00A30"}</span></div>
              <div style={{ fontSize: "13px", color: "#8fa8c8" }}>Utility: <span style={{ color: "#e8edf5", fontWeight: "600" }}>{form.utilityTreatment || "\u2014"}</span></div>
            </div>
          </div>

          <hr style={S.divider} />
          <div style={S.comingSoon}>
            <div style={{ fontSize: "14px", marginBottom: "4px" }}>Use BIA Data</div>
            <div>Import costs from an existing BIA submission \u2014 Coming soon</div>
          </div>
        </>
      );

      default: return null;
    }
  };

  // ─── Results view ───
  const renderResults = () => {
    if (!results) return null;
    const r = results;
    const icer = r.icer;
    const ce25 = r.cost_effective_25k;
    const ce35 = r.cost_effective_35k;

    return (
      <div style={{ ...S.content, borderRadius: "12px", border: "1px solid rgba(255,255,255,0.07)" }}>
        <div style={{ fontSize: "28px", fontWeight: "400", color: "#fff", marginBottom: "6px" }}>Cost-Effectiveness Results</div>
        <div style={{ fontSize: "13px", color: "#6a8fb5", marginBottom: "32px", fontStyle: "italic" }}>{form.interventionName} \u2014 {form.timeHorizon}-year horizon, {parseFloat(form.discountRate) * 100}% discount rate</div>

        {/* ICER banner */}
        <div style={{ background: "rgba(255,255,255,0.04)", borderRadius: "10px", padding: "24px", textAlign: "center", marginBottom: "24px", border: "1px solid rgba(255,255,255,0.08)" }}>
          <div style={{ fontSize: "11px", letterSpacing: "2px", textTransform: "uppercase", color: "#5a7fa8", marginBottom: "8px" }}>Incremental Cost-Effectiveness Ratio</div>
          <div style={{ fontSize: "36px", fontWeight: "700", color: ce25 ? "#52b788" : ce35 ? "#ffc107" : "#e85d6f", marginBottom: "12px" }}>
            {icer != null ? `${fmtGBP(Math.round(icer))}/QALY` : "N/A"}
          </div>
          <div style={S.icerBadge(ce25, ce35)}>{r.interpretation}</div>
        </div>

        {/* Arm comparison */}
        <div style={S.grid2}>
          <div style={S.resultsCard}>
            <div style={S.resultsTitle}>Standard Care</div>
            <div style={S.resultRow}><span style={S.resultLabel}>Total cost</span><span style={S.resultValue}>{fmtGBP(r.standard_care.total_cost)}</span></div>
            <div style={S.resultRow}><span style={S.resultLabel}>Total QALYs</span><span style={S.resultValue}>{fmtQALY(r.standard_care.total_qalys)}</span></div>
          </div>
          <div style={S.resultsCard}>
            <div style={S.resultsTitle}>Treatment</div>
            <div style={S.resultRow}><span style={S.resultLabel}>Total cost</span><span style={S.resultValue}>{fmtGBP(r.treatment.total_cost)}</span></div>
            <div style={S.resultRow}><span style={S.resultLabel}>Total QALYs</span><span style={S.resultValue}>{fmtQALY(r.treatment.total_qalys)}</span></div>
          </div>
        </div>

        {/* Incremental */}
        <div style={S.resultsCard}>
          <div style={S.resultsTitle}>Incremental Analysis</div>
          <div style={S.resultRow}>
            <span style={S.resultLabel}>Incremental cost</span>
            <span style={{ ...S.resultValue, color: r.incremental_cost < 0 ? "#52b788" : "#e85d6f" }}>{fmtGBP(r.incremental_cost)}</span>
          </div>
          <div style={S.resultRow}>
            <span style={S.resultLabel}>Incremental QALYs</span>
            <span style={{ ...S.resultValue, color: r.incremental_qalys > 0 ? "#52b788" : "#e85d6f" }}>{fmtQALY(r.incremental_qalys)}</span>
          </div>
        </div>

        {/* NICE thresholds */}
        <div style={S.resultsCard}>
          <div style={S.resultsTitle}>NICE Threshold Assessment</div>
          {[
            { label: "\u00A325,000/QALY \u2014 Standard threshold", pass: ce25 },
            { label: "\u00A335,000/QALY \u2014 Extended threshold", pass: ce35 },
            { label: "\u00A350,000/QALY \u2014 End-of-life criteria", pass: icer != null && icer < 50000 },
          ].map((t, i) => (
            <div key={i} style={S.thresholdRow}>
              <span style={S.thresholdIcon(t.pass)}>{t.pass ? "\u2713" : "\u2717"}</span>
              <span style={{ color: t.pass ? "#52b788" : "#e85d6f" }}>{t.label}</span>
            </div>
          ))}
        </div>

        {/* Download report */}
        {reportUrl && (
          <div style={{ background: "rgba(82,183,136,0.08)", border: "1px solid rgba(82,183,136,0.25)", borderRadius: "10px", padding: "20px 24px", marginBottom: "20px", display: "flex", alignItems: "center", gap: "16px" }}>
            <div style={{ fontSize: "32px" }}>📊</div>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: "15px", fontWeight: "600", color: "#fff", marginBottom: "4px" }}>CEA Report Ready</div>
              <div style={{ fontSize: "12px", color: "#8fd4b0" }}>6-slide PowerPoint with model structure, results, CE plane, and NICE interpretation</div>
            </div>
            <a
              href={reportUrl}
              style={{
                background: "linear-gradient(135deg,#2d6a4f,#52b788)",
                border: "none",
                borderRadius: "8px",
                color: "#fff",
                padding: "12px 24px",
                fontSize: "13px",
                fontWeight: "600",
                cursor: "pointer",
                fontFamily: "'Georgia',serif",
                textDecoration: "none",
                display: "inline-flex",
                alignItems: "center",
                gap: "8px",
                whiteSpace: "nowrap",
              }}
            >
              ⬇ Download PPTX
            </a>
          </div>
        )}

        <div style={S.btnRow}>
          <button style={S.btnBack} onClick={handleReset}>Run New Analysis</button>
        </div>
      </div>
    );
  };

  // ─── Main layout ───
  if (submitted && results) {
    const resultsContent = (
      <div style={{ maxWidth: "900px", margin: "40px auto", padding: "0 20px" }}>
        {renderResults()}
      </div>
    );
    if (hideChrome) return resultsContent;
    return (
      <div style={S.app}>
        <header style={S.header}>
          <div style={S.logoIcon}><span style={{ color: "#fff", fontWeight: "800", fontSize: "12px" }}>HE</span></div>
          <div><div style={S.logoText}>HEOR Engine</div><div style={S.logoSub}>Cost-Effectiveness Analysis</div></div>
          <div style={S.headerRight}>Markov Model / ICER Calculator</div>
        </header>
        {resultsContent}
      </div>
    );
  }

  const formContent = (
    <div style={S.main}>
        {/* Sidebar */}
        <nav style={S.sidebar}>
          <div style={S.sidebarTitle}>Sections</div>
          {SECTIONS.map((name, idx) => (
            <div key={idx} style={S.navItem(idx)} onClick={() => setSection(idx)}>
              <span style={{ fontSize: "16px" }}>{SECTION_ICONS[idx]}</span>
              <span style={{ fontSize: "13px" }}>{name}</span>
              <div style={S.navStep(idx)}>{completed.includes(idx) ? "\u2713" : idx + 1}</div>
            </div>
          ))}
          <div style={S.progressWrap}>
            <div style={{ fontSize: "10px", color: "#5a7fa8", letterSpacing: "1px" }}>PROGRESS</div>
            <div style={S.progressBar}><div style={S.progressFill(progress)} /></div>
            <div style={{ fontSize: "11px", color: "#52b788" }}>{progress}% complete</div>
          </div>
        </nav>

        {/* Content */}
        <div style={S.content}>
          {renderSection()}

          {apiError && (
            <div style={{ background: "rgba(220,53,69,0.1)", border: "1px solid rgba(220,53,69,0.3)", borderRadius: "8px", padding: "14px 18px", marginTop: "16px", fontSize: "13px", color: "#e85d6f" }}>
              {apiError}
            </div>
          )}

          <div style={S.btnRow}>
            {section > 0 && <button style={S.btnBack} onClick={() => setSection(s => s - 1)}>Back</button>}
            <button
              style={{ ...S.btnNext, ...(submitting ? S.btnDisabled : {}) }}
              onClick={handleNext}
              disabled={submitting}
            >
              {submitting ? "Calculating..." : section < SECTIONS.length - 1 ? "Continue" : "Calculate ICER"}
            </button>
          </div>
        </div>
      </div>
  );
  if (hideChrome) return formContent;
  return (
    <div style={S.app}>
      <header style={S.header}>
        <div style={S.logoIcon}><span style={{ color: "#fff", fontWeight: "800", fontSize: "12px" }}>HE</span></div>
        <div><div style={S.logoText}>HEOR Engine</div><div style={S.logoSub}>Cost-Effectiveness Analysis</div></div>
        <div style={S.headerRight}>Markov Model / ICER Calculator</div>
      </header>
      {formContent}
    </div>
  );
}
