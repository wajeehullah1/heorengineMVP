import { useState, useEffect, useRef } from "react";

const API = "http://localhost:8000";

// ── Design tokens (matches HEOR Engine branding) ──────────────────────────────
const C = {
  bg:         "rgba(255,255,255,0.03)",
  border:     "1px solid rgba(255,255,255,0.08)",
  borderFocus:"1px solid rgba(82,183,136,0.5)",
  radius:     "8px",
  label:      { display:"block", fontSize:"11px", letterSpacing:"1.2px",
                textTransform:"uppercase", color:"#5a7fa8", marginBottom:"6px" },
  input:      { width:"100%", background:"rgba(255,255,255,0.05)",
                border:"1px solid rgba(255,255,255,0.12)", borderRadius:"6px",
                color:"#e8edf5", padding:"9px 12px", fontSize:"13px",
                fontFamily:"'Georgia','Times New Roman',serif",
                boxSizing:"border-box", outline:"none", resize:"vertical" },
  btnPrimary: { background:"linear-gradient(135deg,#2d6a4f,#52b788)", border:"none",
                borderRadius:"6px", color:"#fff", padding:"10px 26px",
                fontSize:"13px", fontWeight:"600", cursor:"pointer",
                fontFamily:"'Georgia','Times New Roman',serif" },
  btnGhost:   { background:"rgba(255,255,255,0.05)", border:"1px solid rgba(255,255,255,0.12)",
                borderRadius:"6px", color:"#a0b4cc", padding:"8px 18px",
                fontSize:"12px", cursor:"pointer",
                fontFamily:"'Georgia','Times New Roman',serif" },
  chip:       { display:"inline-flex", alignItems:"center", gap:"5px",
                background:"rgba(82,183,136,0.12)", border:"1px solid rgba(82,183,136,0.25)",
                borderRadius:"20px", padding:"4px 10px", fontSize:"12px", color:"#52b788" },
};

const DECISION_STYLE = {
  include:  { bg:"rgba(82,183,136,0.13)", badge:"#1a3d2e", badgeText:"#52b788" },
  exclude:  { bg:"rgba(220,80,80,0.10)",  badge:"#3d1a1a", badgeText:"#e07070" },
  uncertain:{ bg:"rgba(232,184,75,0.10)", badge:"#3d3010", badgeText:"#e8b84b" },
};

const STUDY_TYPE_OPTIONS = [
  "RCT",
  "Cohort study",
  "Economic evaluation",
  "Systematic review",
  "Meta-analysis",
  "Diagnostic accuracy study",
  "Cross-sectional",
  "Case-control",
];

// ── Hardcoded templates (also fetched from API) ───────────────────────────────
const BUILTIN_TEMPLATES = {
  diabetes_remote_monitoring: {
    label: "Diabetes Remote Monitoring",
    pico: {
      population: "Adults (≥18 years) with type 2 diabetes mellitus",
      intervention: "Continuous glucose monitoring (CGM) or remote glucose monitoring with clinician-facing dashboard or alert system",
      comparison: "Standard care or self-monitoring of blood glucose (SMBG)",
      outcomes: ["HbA1c reduction", "Time in range (TIR)", "Quality of life (EQ-5D)", "Cost-effectiveness (ICER per QALY)", "Hospitalization rates"],
      study_types: ["RCT", "Cohort study", "Economic evaluation"],
      exclusion_criteria: ["Paediatric populations (age < 18)", "Type 1 diabetes only studies", "Animal or in vitro studies"],
    },
  },
  ai_diagnostic_tools: {
    label: "AI Diagnostic Tools",
    pico: {
      population: "Adult patients (≥18 years) referred to secondary care for diagnostic workup",
      intervention: "AI-powered or machine-learning diagnostic decision support system",
      comparison: "Standard clinical assessment without AI assistance",
      outcomes: ["Diagnostic accuracy (sensitivity, specificity)", "Time to diagnosis", "Cost per correct diagnosis", "Clinician acceptance"],
      study_types: ["RCT", "Diagnostic accuracy study", "Economic evaluation"],
      exclusion_criteria: ["Paediatric-only studies", "Single-centre feasibility studies without comparative arm", "Editorials or letters"],
    },
  },
  cardiovascular_prevention: {
    label: "Cardiovascular Prevention",
    pico: {
      population: "Adults (≥18 years) at high cardiovascular risk (QRISK ≥10%) in primary care",
      intervention: "Structured cardiovascular prevention programme: statin therapy, blood pressure management, or lifestyle intervention",
      comparison: "Usual care or no active intervention",
      outcomes: ["Major adverse cardiovascular events (MACE)", "Blood pressure reduction", "LDL-C reduction", "QALYs gained", "Cost per QALY"],
      study_types: ["RCT", "Cohort study", "Economic evaluation", "Systematic review"],
      exclusion_criteria: ["Secondary prevention (existing CVD diagnosis)", "Inpatient or secondary care setting", "Follow-up < 6 months"],
    },
  },
  economic_evaluations: {
    label: "Health Technology CEA",
    pico: {
      population: "Adult NHS patients eligible for the technology under appraisal",
      intervention: "Any health technology with a published economic evaluation",
      comparison: "Current standard of care or best supportive care",
      outcomes: ["Incremental cost-effectiveness ratio (ICER)", "Cost per QALY gained", "Life-years gained", "Budget impact (NHS perspective)"],
      study_types: ["Economic evaluation", "Systematic review"],
      exclusion_criteria: ["Non-UK or non-NHS perspective", "Studies without incremental analysis", "Non-English language"],
    },
  },
};

// ── Text parsers ──────────────────────────────────────────────────────────────
function parseCSVRow(line) {
  const result = [];
  let cur = "", inQ = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      if (inQ && line[i + 1] === '"') { cur += '"'; i++; }
      else inQ = !inQ;
    } else if (ch === "," && !inQ) {
      result.push(cur); cur = "";
    } else cur += ch;
  }
  result.push(cur);
  return result;
}

function parseCSV(text) {
  const lines = text.trim().split(/\r?\n/).filter(Boolean);
  if (lines.length < 2) throw new Error("CSV must have a header row and at least one data row.");
  const headers = parseCSVRow(lines[0]).map((h) => h.trim().toLowerCase().replace(/[^a-z]/g, ""));
  const need = ["pmid", "title", "abstract"];
  const missing = need.filter((f) => !headers.includes(f));
  if (missing.length) throw new Error(`CSV missing required columns: ${missing.join(", ")}.`);
  const out = [];
  for (let i = 1; i < lines.length; i++) {
    if (!lines[i].trim()) continue;
    const vals = parseCSVRow(lines[i]);
    const row = Object.fromEntries(headers.map((h, j) => [h, (vals[j] || "").trim()]));
    if (!row.pmid || !row.title || !row.abstract) continue;
    const authRaw = row.authors || row.author || "Unknown";
    out.push({
      pmid: row.pmid,
      title: row.title,
      abstract: row.abstract,
      authors: authRaw.split(/[;|]/).map((a) => a.trim()).filter(Boolean),
      journal: row.journal || row.source || "Unknown",
      year: parseInt(row.year || row.date) || new Date().getFullYear(),
    });
  }
  if (!out.length) throw new Error("No valid rows found. Check that PMID, Title, and Abstract columns are non-empty.");
  return out;
}

function parsePastedText(text) {
  const blocks = text.trim().split(/\n\s*\n+/).filter(Boolean);
  const out = [];
  for (const block of blocks) {
    const fields = {};
    let key = null, val = [];
    for (const line of block.split("\n")) {
      const m = line.match(/^([A-Za-z ]+?)\s*:\s*(.*)$/);
      if (m) {
        if (key) fields[key.toLowerCase().replace(/\s+/g, "")] = val.join(" ").trim();
        key = m[1];
        val = [m[2]];
      } else if (key) val.push(line.trim());
    }
    if (key) fields[key.toLowerCase().replace(/\s+/g, "")] = val.join(" ").trim();
    const pmid = fields.pmid || fields.pubmedid || fields.id || "";
    const title = fields.title || "";
    const abstract = fields.abstract || fields.text || "";
    if (!pmid || !title || !abstract) continue;
    const authRaw = fields.authors || fields.author || "Unknown";
    out.push({
      pmid: String(pmid).trim(),
      title: title.trim(),
      abstract: abstract.trim(),
      authors: authRaw.split(/[,;]/).map((a) => a.trim()).filter(Boolean),
      journal: (fields.journal || fields.source || "Unknown").trim(),
      year: parseInt(fields.year || fields.date) || new Date().getFullYear(),
    });
  }
  return out;
}

// ── Sub-components ────────────────────────────────────────────────────────────
function DynamicList({ label, items, placeholder, onAdd, onRemove, onChange }) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const v = draft.trim();
    if (v) { onAdd(v); setDraft(""); }
  };
  return (
    <div style={{ marginBottom: "16px" }}>
      <span style={C.label}>{label}</span>
      <div style={{ display:"flex", gap:"6px", marginBottom:"6px" }}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), add())}
          placeholder={placeholder}
          style={{ ...C.input, resize:"none", flex:1 }}
        />
        <button onClick={add} style={{ ...C.btnGhost, padding:"8px 14px", fontSize:"18px", lineHeight:1 }}>+</button>
      </div>
      <div style={{ display:"flex", flexWrap:"wrap", gap:"6px" }}>
        {items.map((item, i) => (
          <div key={i} style={{ ...C.chip, background:"rgba(255,255,255,0.05)", border:"1px solid rgba(255,255,255,0.12)", color:"#a0b4cc" }}>
            <span style={{ maxWidth:"260px", overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap" }}>{item}</span>
            <span onClick={() => onRemove(i)} style={{ cursor:"pointer", color:"#666", fontSize:"14px", marginLeft:"2px", lineHeight:1 }}>×</span>
          </div>
        ))}
        {!items.length && <span style={{ fontSize:"11px", color:"#3d5470", fontStyle:"italic" }}>None added yet</span>}
      </div>
    </div>
  );
}

function DecisionBadge({ decision }) {
  const s = DECISION_STYLE[decision] || DECISION_STYLE.uncertain;
  return (
    <span style={{
      display:"inline-block", padding:"3px 9px", borderRadius:"12px",
      fontSize:"11px", fontWeight:"700", letterSpacing:"0.5px",
      background:s.badge, color:s.badgeText, textTransform:"uppercase",
    }}>
      {decision === "include" ? "✓ Include" : decision === "exclude" ? "✗ Exclude" : "? Uncertain"}
    </span>
  );
}

function Spinner() {
  return (
    <span style={{ display:"inline-block", width:"14px", height:"14px", border:"2px solid rgba(255,255,255,0.2)",
      borderTopColor:"#52b788", borderRadius:"50%", animation:"slr-spin 0.7s linear infinite", verticalAlign:"middle" }}>
      <style>{`@keyframes slr-spin{to{transform:rotate(360deg)}}`}</style>
    </span>
  );
}

// ── Main component ────────────────────────────────────────────────────────────
export default function SLRScreener() {
  // PICO state
  const [population,   setPopulation]   = useState("");
  const [intervention, setIntervention] = useState("");
  const [comparison,   setComparison]   = useState("");
  const [outcomes,     setOutcomes]     = useState([]);
  const [studyTypes,   setStudyTypes]   = useState(["RCT", "Cohort study", "Economic evaluation"]);
  const [exclusions,   setExclusions]   = useState([]);

  // Abstract input state
  const [inputMode,    setInputMode]    = useState("paste"); // "paste" | "csv"
  const [pasteText,    setPasteText]    = useState("");
  const [csvError,     setCsvError]     = useState(null);
  const [parsedCount,  setParsedCount]  = useState(0);
  const [parsedAbstracts, setParsedAbstracts] = useState([]);
  const fileRef = useRef(null);

  // Template state
  const [selectedTemplate, setSelectedTemplate] = useState("");

  // Screening state
  const [loading,      setLoading]      = useState(false);
  const [loadingMsg,   setLoadingMsg]   = useState("");
  const [error,        setError]        = useState(null);
  const [batch,        setBatch]        = useState(null); // full batch response
  const [filter,       setFilter]       = useState("all");
  const [expanded,     setExpanded]     = useState({}); // pmid → bool
  const [exporting,    setExporting]    = useState(false);

  // ── Template application ───────────────────────────────────────────────────
  function applyTemplate(key) {
    if (!key || !BUILTIN_TEMPLATES[key]) return;
    const t = BUILTIN_TEMPLATES[key].pico;
    setPopulation(t.population);
    setIntervention(t.intervention);
    setComparison(t.comparison);
    setOutcomes([...t.outcomes]);
    setStudyTypes([...t.study_types]);
    setExclusions([...t.exclusion_criteria]);
    setSelectedTemplate(key);
  }

  // ── Paste / CSV parsing ────────────────────────────────────────────────────
  useEffect(() => {
    setCsvError(null);
    if (inputMode === "paste") {
      if (!pasteText.trim()) { setParsedAbstracts([]); setParsedCount(0); return; }
      const parsed = parsePastedText(pasteText);
      setParsedAbstracts(parsed);
      setParsedCount(parsed.length);
      if (!parsed.length) setCsvError("No valid abstracts detected. Check the format guide below.");
    }
  }, [pasteText, inputMode]);

  function handleCSVUpload(e) {
    const file = e.target.files[0];
    if (!file) return;
    setCsvError(null);
    const reader = new FileReader();
    reader.onload = (ev) => {
      try {
        const parsed = parseCSV(ev.target.result);
        setParsedAbstracts(parsed);
        setParsedCount(parsed.length);
      } catch (err) {
        setCsvError(err.message);
        setParsedAbstracts([]);
        setParsedCount(0);
      }
    };
    reader.readAsText(file);
  }

  // ── Validation ────────────────────────────────────────────────────────────
  function validate() {
    if (!population.trim()) return "Population is required.";
    if (!intervention.trim()) return "Intervention is required.";
    if (!comparison.trim()) return "Comparison is required.";
    if (!outcomes.length) return "Add at least one outcome.";
    if (!studyTypes.length) return "Select at least one study type.";
    if (!parsedAbstracts.length) return "No abstracts to screen. Add abstracts via paste or CSV upload.";
    return null;
  }

  // ── Submit ─────────────────────────────────────────────────────────────────
  async function runScreening() {
    const err = validate();
    if (err) { setError(err); return; }
    setError(null);
    setBatch(null);
    setFilter("all");
    setLoading(true);
    setLoadingMsg(`Sending ${parsedAbstracts.length} abstract${parsedAbstracts.length !== 1 ? "s" : ""} to Claude...`);

    const payload = {
      pico: {
        population,
        intervention,
        comparison,
        outcomes,
        study_types: studyTypes,
        exclusion_criteria: exclusions.length ? exclusions : null,
      },
      abstracts: parsedAbstracts,
      batch_size: 10,
    };

    try {
      const res = await fetch(`${API}/api/slr/screen`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setBatch(data);
    } catch (err) {
      setError(`Screening failed: ${err.message}`);
    } finally {
      setLoading(false);
      setLoadingMsg("");
    }
  }

  // ── Export ─────────────────────────────────────────────────────────────────
  async function exportCSV() {
    if (!batch?.batch_id) return;
    setExporting(true);
    try {
      const res = await fetch(`${API}/api/slr/export/${batch.batch_id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ format: "csv" }),
      });
      if (!res.ok) throw new Error(`Export failed (HTTP ${res.status})`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `slr_${batch.batch_id}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(`Export failed: ${err.message}`);
    } finally {
      setExporting(false);
    }
  }

  // ── Derived data ───────────────────────────────────────────────────────────
  const decisions = batch?.decisions || [];
  const counts = {
    all:       decisions.length,
    include:   decisions.filter((d) => d.decision === "include").length,
    exclude:   decisions.filter((d) => d.decision === "exclude").length,
    uncertain: decisions.filter((d) => d.decision === "uncertain").length,
  };
  const visibleDecisions = filter === "all"
    ? decisions
    : decisions.filter((d) => d.decision === filter);

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div style={{ padding:"32px 40px", maxWidth:"1280px", margin:"0 auto", fontFamily:"'Georgia','Times New Roman',serif" }}>

      {/* Page title */}
      <div style={{ marginBottom:"28px" }}>
        <h1 style={{ margin:0, fontSize:"22px", fontWeight:"700", color:"#e8edf5" }}>
          SLR Abstract Screener
        </h1>
        <p style={{ margin:"6px 0 0", fontSize:"13px", color:"#5a7fa8" }}>
          AI-powered PICO screening using Claude · Results saved automatically
        </p>
      </div>

      {/* ── Main two-column layout ── */}
      <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:"24px", alignItems:"start" }}>

        {/* ── LEFT: PICO Builder ── */}
        <div style={{ background:C.bg, border:C.border, borderRadius:C.radius, padding:"24px" }}>
          <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"20px" }}>
            <h2 style={{ margin:0, fontSize:"14px", fontWeight:"700", color:"#c8d8e8", letterSpacing:"0.5px" }}>
              PICO Criteria
            </h2>
            {/* Template selector */}
            <div style={{ display:"flex", alignItems:"center", gap:"8px" }}>
              <span style={{ fontSize:"11px", color:"#5a7fa8" }}>Template:</span>
              <select
                value={selectedTemplate}
                onChange={(e) => applyTemplate(e.target.value)}
                style={{ ...C.input, width:"auto", fontSize:"12px", padding:"5px 10px", resize:"none",
                  background:"rgba(13,32,68,0.8)", cursor:"pointer" }}
              >
                <option value="">— select —</option>
                {Object.entries(BUILTIN_TEMPLATES).map(([key, t]) => (
                  <option key={key} value={key}>{t.label}</option>
                ))}
              </select>
            </div>
          </div>

          {/* Population */}
          <div style={{ marginBottom:"14px" }}>
            <label style={C.label}>Population</label>
            <textarea
              rows={2}
              value={population}
              onChange={(e) => setPopulation(e.target.value)}
              placeholder="e.g. Adults ≥18 years with type 2 diabetes mellitus"
              style={C.input}
            />
          </div>

          {/* Intervention */}
          <div style={{ marginBottom:"14px" }}>
            <label style={C.label}>Intervention</label>
            <textarea
              rows={2}
              value={intervention}
              onChange={(e) => setIntervention(e.target.value)}
              placeholder="e.g. Continuous glucose monitoring (CGM) with clinician dashboard"
              style={C.input}
            />
          </div>

          {/* Comparison */}
          <div style={{ marginBottom:"14px" }}>
            <label style={C.label}>Comparison</label>
            <textarea
              rows={2}
              value={comparison}
              onChange={(e) => setComparison(e.target.value)}
              placeholder="e.g. Standard care or self-monitoring of blood glucose (SMBG)"
              style={C.input}
            />
          </div>

          {/* Outcomes */}
          <DynamicList
            label="Outcomes"
            items={outcomes}
            placeholder="e.g. HbA1c reduction — press Enter"
            onAdd={(v) => setOutcomes([...outcomes, v])}
            onRemove={(i) => setOutcomes(outcomes.filter((_, j) => j !== i))}
          />

          {/* Study types */}
          <div style={{ marginBottom:"16px" }}>
            <span style={C.label}>Study Types</span>
            <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:"6px" }}>
              {STUDY_TYPE_OPTIONS.map((st) => {
                const checked = studyTypes.includes(st);
                return (
                  <label key={st} style={{
                    display:"flex", alignItems:"center", gap:"8px",
                    fontSize:"12px", color: checked ? "#c8d8e8" : "#5a7fa8",
                    cursor:"pointer", padding:"5px 8px",
                    background: checked ? "rgba(82,183,136,0.08)" : "transparent",
                    border: checked ? "1px solid rgba(82,183,136,0.2)" : "1px solid transparent",
                    borderRadius:"5px", transition:"all 0.15s",
                  }}>
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() =>
                        setStudyTypes(checked
                          ? studyTypes.filter((s) => s !== st)
                          : [...studyTypes, st])
                      }
                      style={{ accentColor:"#52b788", width:"13px", height:"13px" }}
                    />
                    {st}
                  </label>
                );
              })}
            </div>
          </div>

          {/* Exclusion criteria */}
          <DynamicList
            label="Exclusion Criteria (optional)"
            items={exclusions}
            placeholder="e.g. Paediatric populations — press Enter"
            onAdd={(v) => setExclusions([...exclusions, v])}
            onRemove={(i) => setExclusions(exclusions.filter((_, j) => j !== i))}
          />
        </div>

        {/* ── RIGHT: Abstract Input ── */}
        <div style={{ background:C.bg, border:C.border, borderRadius:C.radius, padding:"24px" }}>
          <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"20px" }}>
            <h2 style={{ margin:0, fontSize:"14px", fontWeight:"700", color:"#c8d8e8" }}>
              Abstracts to Screen
            </h2>
            {parsedCount > 0 && (
              <span style={{ fontSize:"11px", background:"rgba(82,183,136,0.15)",
                border:"1px solid rgba(82,183,136,0.3)", borderRadius:"20px",
                padding:"3px 10px", color:"#52b788" }}>
                {parsedCount} abstract{parsedCount !== 1 ? "s" : ""} ready
              </span>
            )}
          </div>

          {/* Mode tabs */}
          <div style={{ display:"flex", gap:"0", marginBottom:"18px",
            border:"1px solid rgba(255,255,255,0.08)", borderRadius:"6px", overflow:"hidden" }}>
            {[["paste","Paste Text"],["csv","Upload CSV"],["pubmed","PubMed IDs"]].map(([mode, lbl]) => (
              <button
                key={mode}
                onClick={() => { if (mode !== "pubmed") setInputMode(mode); }}
                style={{
                  flex:1, padding:"9px 0", fontSize:"12px", border:"none", cursor: mode === "pubmed" ? "default" : "pointer",
                  fontFamily:"'Georgia','Times New Roman',serif",
                  background: inputMode === mode ? "rgba(82,183,136,0.15)" : "transparent",
                  color: mode === "pubmed" ? "#3d5470"
                    : inputMode === mode ? "#52b788" : "#5a7fa8",
                  borderRight: mode !== "pubmed" ? "1px solid rgba(255,255,255,0.08)" : "none",
                  fontWeight: inputMode === mode ? "600" : "400",
                }}
              >
                {lbl}{mode === "pubmed" ? " ⚙" : ""}
              </button>
            ))}
          </div>

          {/* Paste mode */}
          {inputMode === "paste" && (
            <>
              <textarea
                rows={12}
                value={pasteText}
                onChange={(e) => setPasteText(e.target.value)}
                placeholder={`Paste one or more abstracts. Separate records with a blank line.\n\nExample:\nPMID: 12345678\nTitle: CGM versus SMBG in type 2 diabetes\nAuthors: Smith JA, Jones B\nJournal: Lancet Diabetes\nYear: 2023\nAbstract: Background: We investigated...\n\nPMID: 87654321\nTitle: Next abstract...`}
                style={{ ...C.input, fontFamily:"'Menlo','Courier New',monospace", fontSize:"11.5px", lineHeight:"1.6" }}
              />
              {parsedCount > 0 && (
                <p style={{ margin:"8px 0 0", fontSize:"11px", color:"#52b788" }}>
                  ✓ Parsed {parsedCount} abstract{parsedCount !== 1 ? "s" : ""} from paste
                </p>
              )}
              {csvError && pasteText && (
                <p style={{ margin:"8px 0 0", fontSize:"11px", color:"#e07070" }}>{csvError}</p>
              )}
              <div style={{ marginTop:"14px", background:"rgba(255,255,255,0.02)",
                border:"1px solid rgba(255,255,255,0.06)", borderRadius:"6px", padding:"12px" }}>
                <p style={{ margin:"0 0 6px", fontSize:"11px", color:"#5a7fa8", fontWeight:"600", letterSpacing:"0.8px", textTransform:"uppercase" }}>
                  Format guide
                </p>
                <p style={{ margin:0, fontSize:"11px", color:"#3d5470", lineHeight:"1.7", fontFamily:"'Menlo','Courier New',monospace" }}>
                  PMID: &lt;number&gt;<br/>
                  Title: &lt;title text&gt;<br/>
                  Authors: Smith JA, Jones B<br/>
                  Journal: &lt;journal name&gt;<br/>
                  Year: &lt;year&gt;<br/>
                  Abstract: &lt;full text&gt;<br/>
                  <span style={{ color:"#2a3f55" }}>&lt;blank line separates records&gt;</span>
                </p>
              </div>
            </>
          )}

          {/* CSV mode */}
          {inputMode === "csv" && (
            <>
              <div
                onClick={() => fileRef.current?.click()}
                style={{
                  border:"2px dashed rgba(82,183,136,0.3)", borderRadius:"8px",
                  padding:"36px 24px", textAlign:"center", cursor:"pointer",
                  background:"rgba(82,183,136,0.03)",
                  transition:"border-color 0.2s",
                }}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  const file = e.dataTransfer.files[0];
                  if (file) handleCSVUpload({ target: { files: [file] } });
                }}
              >
                <div style={{ fontSize:"28px", marginBottom:"10px" }}>📄</div>
                <p style={{ margin:"0 0 6px", fontSize:"13px", color:"#a0b4cc" }}>
                  Click to select or drag &amp; drop a CSV file
                </p>
                <p style={{ margin:0, fontSize:"11px", color:"#3d5470" }}>
                  Required columns: PMID, Title, Abstract
                </p>
              </div>
              <input
                ref={fileRef}
                type="file"
                accept=".csv,text/csv"
                onChange={handleCSVUpload}
                style={{ display:"none" }}
              />
              {parsedCount > 0 && (
                <p style={{ margin:"10px 0 0", fontSize:"11px", color:"#52b788" }}>
                  ✓ Loaded {parsedCount} abstract{parsedCount !== 1 ? "s" : ""} from CSV
                </p>
              )}
              {csvError && (
                <p style={{ margin:"10px 0 0", fontSize:"11px", color:"#e07070" }}>⚠ {csvError}</p>
              )}
              <div style={{ marginTop:"14px", background:"rgba(255,255,255,0.02)",
                border:"1px solid rgba(255,255,255,0.06)", borderRadius:"6px", padding:"12px" }}>
                <p style={{ margin:"0 0 6px", fontSize:"11px", color:"#5a7fa8", fontWeight:"600", letterSpacing:"0.8px", textTransform:"uppercase" }}>
                  Expected columns
                </p>
                <div style={{ display:"flex", flexWrap:"wrap", gap:"6px" }}>
                  {[["PMID","required"],["Title","required"],["Abstract","required"],
                    ["Authors","optional"],["Journal","optional"],["Year","optional"]].map(([col,req]) => (
                    <span key={col} style={{
                      fontSize:"11px", padding:"3px 8px", borderRadius:"4px",
                      background: req === "required" ? "rgba(82,183,136,0.12)" : "rgba(255,255,255,0.04)",
                      color: req === "required" ? "#52b788" : "#5a7fa8",
                      border: `1px solid ${req === "required" ? "rgba(82,183,136,0.25)" : "rgba(255,255,255,0.08)"}`,
                    }}>
                      {col} <span style={{ opacity:0.6 }}>({req})</span>
                    </span>
                  ))}
                </div>
              </div>
            </>
          )}

          {/* PubMed mode — coming soon */}
          {inputMode === "pubmed" && (
            <div style={{ textAlign:"center", padding:"48px 24px" }}>
              <div style={{ fontSize:"32px", marginBottom:"12px" }}>🔍</div>
              <p style={{ margin:"0 0 8px", fontSize:"14px", color:"#5a7fa8", fontWeight:"600" }}>
                PubMed Import — Coming Soon
              </p>
              <p style={{ margin:0, fontSize:"12px", color:"#3d5470" }}>
                Fetch abstracts directly from NCBI PubMed using PMIDs or a search query.
                Use Paste Text or CSV Upload in the meantime.
              </p>
            </div>
          )}
        </div>
      </div>

      {/* ── Submit bar ── */}
      <div style={{ marginTop:"24px", display:"flex", alignItems:"center", gap:"16px",
        background:C.bg, border:C.border, borderRadius:C.radius, padding:"18px 24px" }}>
        <button
          onClick={runScreening}
          disabled={loading}
          style={{
            ...C.btnPrimary,
            opacity: loading ? 0.7 : 1,
            display:"flex", alignItems:"center", gap:"8px",
            padding:"12px 32px", fontSize:"14px",
          }}
        >
          {loading ? <><Spinner /> Screening…</> : "▶  Run Screening"}
        </button>

        {loading && (
          <span style={{ fontSize:"12px", color:"#5a7fa8", fontStyle:"italic" }}>{loadingMsg}</span>
        )}

        {!loading && batch && (
          <span style={{ fontSize:"12px", color:"#52b788" }}>
            ✓ Screening complete — {counts.all} decision{counts.all !== 1 ? "s" : ""}
          </span>
        )}

        {error && (
          <span style={{ fontSize:"12px", color:"#e07070", flex:1 }}>⚠ {error}</span>
        )}

        {/* PICO completeness mini-check */}
        <div style={{ marginLeft:"auto", display:"flex", gap:"8px", fontSize:"11px" }}>
          {[
            ["P", !!population.trim()],
            ["I", !!intervention.trim()],
            ["C", !!comparison.trim()],
            ["O", outcomes.length > 0],
          ].map(([ltr, ok]) => (
            <span key={ltr} style={{
              width:"22px", height:"22px", display:"flex", alignItems:"center", justifyContent:"center",
              borderRadius:"50%", fontWeight:"700",
              background: ok ? "rgba(82,183,136,0.2)" : "rgba(255,255,255,0.05)",
              color: ok ? "#52b788" : "#3d5470",
              border: `1px solid ${ok ? "rgba(82,183,136,0.4)" : "rgba(255,255,255,0.06)"}`,
            }}>
              {ltr}
            </span>
          ))}
          <span style={{
            padding:"0 8px", height:"22px", display:"flex", alignItems:"center",
            borderRadius:"11px", fontWeight:"600",
            background: parsedCount > 0 ? "rgba(82,183,136,0.2)" : "rgba(255,255,255,0.05)",
            color: parsedCount > 0 ? "#52b788" : "#3d5470",
            border: `1px solid ${parsedCount > 0 ? "rgba(82,183,136,0.4)" : "rgba(255,255,255,0.06)"}`,
          }}>
            {parsedCount} abs
          </span>
        </div>
      </div>

      {/* ── Results section ── */}
      {batch && (
        <div style={{ marginTop:"24px" }}>

          {/* Summary cards */}
          <div style={{ display:"grid", gridTemplateColumns:"repeat(4,1fr)", gap:"12px", marginBottom:"20px" }}>
            {[
              ["Total",    counts.all,       "#5a7fa8", "rgba(90,127,168,0.12)"],
              ["Included", counts.include,   "#52b788", "rgba(82,183,136,0.12)"],
              ["Excluded", counts.exclude,   "#e07070", "rgba(220,80,80,0.12)"],
              ["Uncertain",counts.uncertain, "#e8b84b", "rgba(232,184,75,0.12)"],
            ].map(([lbl, n, col, bg]) => (
              <div key={lbl} style={{ background:bg, border:`1px solid ${col}33`,
                borderRadius:C.radius, padding:"16px 20px", textAlign:"center" }}>
                <div style={{ fontSize:"28px", fontWeight:"700", color:col, lineHeight:1 }}>{n}</div>
                <div style={{ fontSize:"11px", color:col, opacity:0.8, marginTop:"4px",
                  textTransform:"uppercase", letterSpacing:"0.8px" }}>{lbl}</div>
                <div style={{ fontSize:"11px", color:col, opacity:0.6, marginTop:"2px" }}>
                  {counts.all > 0 ? `${Math.round(n / counts.all * 100)}%` : "—"}
                </div>
              </div>
            ))}
          </div>

          {/* Filter + Export bar */}
          <div style={{ display:"flex", alignItems:"center", gap:"8px", marginBottom:"14px" }}>
            {[
              ["all",      `All (${counts.all})`],
              ["include",  `✓ Included (${counts.include})`],
              ["exclude",  `✗ Excluded (${counts.exclude})`],
              ["uncertain",`? Uncertain (${counts.uncertain})`],
            ].map(([key, lbl]) => (
              <button
                key={key}
                onClick={() => setFilter(key)}
                style={{
                  ...C.btnGhost,
                  padding:"7px 14px",
                  background: filter === key ? "rgba(82,183,136,0.15)" : "rgba(255,255,255,0.04)",
                  color: filter === key ? "#52b788" : "#5a7fa8",
                  border: filter === key ? "1px solid rgba(82,183,136,0.35)" : "1px solid rgba(255,255,255,0.08)",
                  fontWeight: filter === key ? "600" : "400",
                }}
              >
                {lbl}
              </button>
            ))}

            <button
              onClick={exportCSV}
              disabled={exporting}
              style={{ ...C.btnGhost, marginLeft:"auto", display:"flex", alignItems:"center", gap:"6px",
                color:"#52b788", border:"1px solid rgba(82,183,136,0.3)" }}
            >
              {exporting ? <Spinner /> : "⬇"} Export CSV
            </button>
          </div>

          {/* Results table */}
          <div style={{ background:C.bg, border:C.border, borderRadius:C.radius, overflow:"hidden" }}>
            {/* Table header */}
            <div style={{
              display:"grid",
              gridTemplateColumns:"90px 1fr 130px 100px 80px",
              gap:"0",
              background:"rgba(255,255,255,0.03)",
              borderBottom:"1px solid rgba(255,255,255,0.08)",
              padding:"10px 16px",
            }}>
              {["PMID","Title","Decision","Confidence","PICO"].map((h) => (
                <span key={h} style={{ fontSize:"10px", fontWeight:"700", letterSpacing:"1px",
                  textTransform:"uppercase", color:"#3d5470" }}>{h}</span>
              ))}
            </div>

            {visibleDecisions.length === 0 && (
              <div style={{ padding:"32px", textAlign:"center", color:"#3d5470", fontSize:"13px" }}>
                No decisions match this filter.
              </div>
            )}

            {visibleDecisions.map((dec, idx) => {
              const ds = DECISION_STYLE[dec.decision] || DECISION_STYLE.uncertain;
              const isExpanded = !!expanded[dec.pmid];
              const abstract = parsedAbstracts.find((a) => a.pmid === dec.pmid);
              const title = abstract?.title || dec.pmid;
              const score = dec.pico_match
                ? Object.values(dec.pico_match).filter((v) => v?.matched).length
                : "—";

              return (
                <div
                  key={dec.pmid}
                  style={{
                    background: idx % 2 === 0 ? "transparent" : "rgba(255,255,255,0.01)",
                    borderBottom:"1px solid rgba(255,255,255,0.05)",
                    borderLeft:`3px solid ${ds.badgeText}33`,
                  }}
                >
                  {/* Main row */}
                  <div
                    onClick={() => setExpanded((p) => ({ ...p, [dec.pmid]: !p[dec.pmid] }))}
                    style={{
                      display:"grid",
                      gridTemplateColumns:"90px 1fr 130px 100px 80px",
                      gap:"0",
                      padding:"11px 16px",
                      cursor:"pointer",
                      background: isExpanded ? ds.bg : "transparent",
                      transition:"background 0.15s",
                    }}
                  >
                    <span style={{ fontSize:"12px", color:"#5a7fa8", fontFamily:"'Menlo','Courier New',monospace" }}>
                      {dec.pmid}
                    </span>
                    <span style={{ fontSize:"12px", color:"#c8d8e8", paddingRight:"16px",
                      overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap" }}>
                      {title}
                    </span>
                    <span><DecisionBadge decision={dec.decision} /></span>
                    <span style={{ fontSize:"11px", color:"#5a7fa8", textTransform:"uppercase", letterSpacing:"0.5px" }}>
                      {dec.confidence || "—"}
                    </span>
                    <span style={{ fontSize:"12px", color:score >= 3 ? "#52b788" : "#5a7fa8", fontWeight:"600" }}>
                      {typeof score === "number" ? `${score}/4` : "—"}
                    </span>
                  </div>

                  {/* Expanded detail */}
                  {isExpanded && (
                    <div style={{ padding:"12px 20px 16px", background:ds.bg,
                      borderTop:"1px solid rgba(255,255,255,0.04)" }}>
                      <p style={{ margin:"0 0 10px", fontSize:"12px", color:"#a0b4cc", lineHeight:"1.6" }}>
                        <strong style={{ color:"#c8d8e8" }}>Reasoning: </strong>
                        {dec.reasoning}
                      </p>

                      {/* PICO match grid */}
                      {dec.pico_match && (
                        <div style={{ display:"grid", gridTemplateColumns:"repeat(4,1fr)", gap:"8px", marginBottom:"10px" }}>
                          {Object.entries(dec.pico_match).map(([key, val]) => (
                            <div key={key} style={{
                              background:"rgba(255,255,255,0.03)", borderRadius:"5px",
                              padding:"7px 10px", border:`1px solid ${val?.matched ? "rgba(82,183,136,0.2)" : "rgba(220,80,80,0.15)"}`,
                            }}>
                              <div style={{ fontSize:"9px", color:"#3d5470", textTransform:"uppercase", letterSpacing:"1px", marginBottom:"3px" }}>
                                {key}
                              </div>
                              <div style={{ fontSize:"11px", fontWeight:"600",
                                color: val?.matched ? "#52b788" : "#e07070" }}>
                                {val?.matched ? "✓ Match" : "✗ No match"}
                              </div>
                              {val?.note && (
                                <div style={{ fontSize:"10px", color:"#5a7fa8", marginTop:"3px", lineHeight:"1.4" }}>
                                  {val.note.length > 80 ? val.note.slice(0, 77) + "…" : val.note}
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      )}

                      {/* Exclusion reasons */}
                      {dec.exclusion_reasons?.length > 0 && (
                        <div>
                          <span style={{ fontSize:"10px", color:"#3d5470", textTransform:"uppercase",
                            letterSpacing:"0.8px", marginRight:"8px" }}>Exclusion reasons:</span>
                          <span style={{ fontSize:"11px", color:"#e07070" }}>
                            {dec.exclusion_reasons.join(" · ")}
                          </span>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Batch metadata footer */}
          <div style={{ marginTop:"10px", fontSize:"11px", color:"#2a3f55", textAlign:"right" }}>
            Batch ID: {batch.batch_id} · Click any row to expand reasoning
          </div>
        </div>
      )}
    </div>
  );
}
