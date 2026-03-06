import { useState, useCallback } from "react";

const API = "http://localhost:8000";

const CONDITIONS = [
  "Diabetes", "Hypertension", "Heart Failure", "COPD",
  "Atrial Fibrillation", "Chronic Kidney Disease", "Asthma",
  "Depression", "Dementia", "Lung Cancer",
];
const INTERVENTION_TYPES = ["digital", "remote_monitoring", "diagnostic", "ai", "pharmaceutical"];
const INTERVENTION_LABELS = {
  digital: "Digital Health Tool",
  remote_monitoring: "Remote Monitoring",
  diagnostic: "Diagnostic Technology",
  ai: "AI / Decision Support",
  pharmaceutical: "Pharmaceutical",
};
const SETTINGS = ["Acute NHS Trust", "ICB", "Primary Care Network"];
const CATEGORIES = ["inpatient", "outpatient", "emergency", "diagnostics", "procedures", "ambulance", "community"];
const REGIONS = [
  "London", "South East", "South West", "East of England",
  "East Midlands", "West Midlands", "Yorkshire and the Humber",
  "North West", "North East",
];
const NICE_TYPES = [
  { key: "all", label: "All types" },
  { key: "ta", label: "Technology Appraisal" },
  { key: "ng", label: "Guideline" },
  { key: "mib", label: "MIB" },
  { key: "dg", label: "Diagnostics" },
  { key: "hst", label: "Highly Specialised" },
];

// ─── Shared styles ────────────────────────────────────────────────────────────
const theme = {
  card: {
    background: "rgba(255,255,255,0.03)",
    border: "1px solid rgba(255,255,255,0.08)",
    borderRadius: "10px",
    padding: "24px",
  },
  label: {
    display: "block",
    fontSize: "11px",
    letterSpacing: "1.2px",
    textTransform: "uppercase",
    color: "#5a7fa8",
    marginBottom: "8px",
  },
  input: {
    width: "100%",
    background: "rgba(255,255,255,0.05)",
    border: "1px solid rgba(255,255,255,0.12)",
    borderRadius: "6px",
    color: "#e8edf5",
    padding: "10px 14px",
    fontSize: "14px",
    fontFamily: "'Georgia','Times New Roman',serif",
    boxSizing: "border-box",
    outline: "none",
  },
  select: {
    width: "100%",
    background: "rgba(13,32,68,0.8)",
    border: "1px solid rgba(255,255,255,0.12)",
    borderRadius: "6px",
    color: "#e8edf5",
    padding: "10px 14px",
    fontSize: "14px",
    fontFamily: "'Georgia','Times New Roman',serif",
    boxSizing: "border-box",
    outline: "none",
    cursor: "pointer",
  },
  btn: {
    background: "linear-gradient(135deg,#2d6a4f,#52b788)",
    border: "none",
    borderRadius: "6px",
    color: "#fff",
    padding: "11px 28px",
    fontSize: "14px",
    cursor: "pointer",
    fontWeight: "600",
    fontFamily: "'Georgia','Times New Roman',serif",
  },
  btnOutline: {
    background: "transparent",
    border: "1px solid rgba(82,183,136,0.4)",
    borderRadius: "6px",
    color: "#52b788",
    padding: "7px 16px",
    fontSize: "12px",
    cursor: "pointer",
    fontFamily: "'Georgia','Times New Roman',serif",
  },
  tag: (active) => ({
    background: active ? "rgba(82,183,136,0.15)" : "rgba(255,255,255,0.04)",
    border: `1px solid ${active ? "rgba(82,183,136,0.5)" : "rgba(255,255,255,0.1)"}`,
    borderRadius: "5px",
    color: active ? "#52b788" : "#8fa8c8",
    padding: "6px 14px",
    fontSize: "12px",
    cursor: "pointer",
    fontFamily: "'Georgia','Times New Roman',serif",
    whiteSpace: "nowrap",
  }),
  pill: (color) => ({
    display: "inline-block",
    background: `${color}22`,
    border: `1px solid ${color}55`,
    borderRadius: "4px",
    color: color,
    padding: "2px 8px",
    fontSize: "11px",
    fontWeight: "600",
    letterSpacing: "0.5px",
  }),
  row: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "10px 0",
    borderBottom: "1px solid rgba(255,255,255,0.05)",
  },
  sectionTitle: {
    fontSize: "13px",
    fontWeight: "700",
    color: "#8fa8c8",
    letterSpacing: "0.5px",
    marginBottom: "12px",
    marginTop: "20px",
  },
  hint: {
    background: "rgba(82,183,136,0.07)",
    border: "1px solid rgba(82,183,136,0.2)",
    borderRadius: "6px",
    padding: "12px 16px",
    fontSize: "13px",
    color: "#52b788",
    marginBottom: "16px",
  },
  warn: {
    background: "rgba(255,193,7,0.07)",
    border: "1px solid rgba(255,193,7,0.25)",
    borderRadius: "6px",
    padding: "10px 14px",
    fontSize: "12px",
    color: "#ffc107",
    marginBottom: "8px",
  },
  error: {
    background: "rgba(220,53,69,0.08)",
    border: "1px solid rgba(220,53,69,0.25)",
    borderRadius: "6px",
    padding: "12px 16px",
    fontSize: "13px",
    color: "#e85d6f",
    marginBottom: "12px",
  },
  grid2: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" },
  grid3: { display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "12px" },
};

// ─── Helper ────────────────────────────────────────────────────────────────────
async function apiFetch(path, options = {}) {
  const res = await fetch(`${API}${path}`, options);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

function Spinner() {
  return (
    <span style={{
      display: "inline-block", width: "14px", height: "14px",
      border: "2px solid rgba(255,255,255,0.2)",
      borderTopColor: "#52b788",
      borderRadius: "50%",
      animation: "spin 0.7s linear infinite",
      marginRight: "8px",
      verticalAlign: "middle",
    }} />
  );
}

// ─── Panel: Suggest Defaults ───────────────────────────────────────────────────
function SuggestDefaultsPanel() {
  const [condition, setCondition] = useState("");
  const [interventionType, setInterventionType] = useState("");
  const [setting, setSetting] = useState("Acute NHS Trust");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const fetchSuggestions = async () => {
    if (!condition || !interventionType) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const data = await apiFetch("/api/suggest-defaults", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ condition, intervention_type: interventionType, setting }),
      });
      setResult(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const sug = result?.suggestions;

  return (
    <div>
      <p style={{ color: "#8fa8c8", fontSize: "14px", marginBottom: "24px", lineHeight: "1.6" }}>
        Enter a clinical condition and intervention type to receive evidence-based default values
        for your BIA model, drawn from NHS Cost Collection, ONS population data, and NICE guidance.
      </p>

      <div style={{ ...theme.card, marginBottom: "24px" }}>
        <div style={theme.grid3}>
          <div>
            <label style={theme.label}>Clinical condition</label>
            <select style={theme.select} value={condition} onChange={e => setCondition(e.target.value)}>
              <option value="">Select condition...</option>
              {CONDITIONS.map(c => <option key={c}>{c}</option>)}
            </select>
          </div>
          <div>
            <label style={theme.label}>Intervention type</label>
            <select style={theme.select} value={interventionType} onChange={e => setInterventionType(e.target.value)}>
              <option value="">Select type...</option>
              {INTERVENTION_TYPES.map(t => (
                <option key={t} value={t}>{INTERVENTION_LABELS[t]}</option>
              ))}
            </select>
          </div>
          <div>
            <label style={theme.label}>NHS setting</label>
            <select style={theme.select} value={setting} onChange={e => setSetting(e.target.value)}>
              {SETTINGS.map(s => <option key={s}>{s}</option>)}
            </select>
          </div>
        </div>
        <div style={{ marginTop: "20px" }}>
          <button
            style={{
              ...theme.btn,
              opacity: (!condition || !interventionType || loading) ? 0.5 : 1,
              cursor: (!condition || !interventionType || loading) ? "not-allowed" : "pointer",
            }}
            onClick={fetchSuggestions}
            disabled={!condition || !interventionType || loading}
          >
            {loading && <Spinner />}
            {loading ? "Fetching evidence..." : "Get Suggested Defaults"}
          </button>
        </div>
      </div>

      {error && <div style={theme.error}>{error}</div>}

      {result && sug && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "20px" }}>

          {/* Population & Uptake */}
          <div style={theme.card}>
            <div style={{ fontSize: "12px", letterSpacing: "1.5px", textTransform: "uppercase", color: "#52b788", marginBottom: "16px" }}>
              Population &amp; Uptake
            </div>
            <div style={theme.row}>
              <span style={{ color: "#8fa8c8", fontSize: "13px" }}>Eligible % of catchment</span>
              <span style={{ color: "#fff", fontWeight: "700", fontSize: "16px" }}>
                {sug.eligible_pct !== undefined ? `${(sug.eligible_pct * 100).toFixed(1)}%` : "—"}
              </span>
            </div>
            <div style={{ color: "#5a7fa8", fontSize: "11px", marginBottom: "16px", marginTop: "4px" }}>
              {sug.eligible_pct_source}
            </div>
            <div style={theme.sectionTitle}>Uptake trajectory</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "10px" }}>
              {[["Year 1", sug.uptake_y1], ["Year 2", sug.uptake_y2], ["Year 3", sug.uptake_y3]].map(([label, val]) => (
                <div key={label} style={{ textAlign: "center", background: "rgba(255,255,255,0.03)", borderRadius: "6px", padding: "12px 8px" }}>
                  <div style={{ color: "#5a7fa8", fontSize: "11px", marginBottom: "4px" }}>{label}</div>
                  <div style={{ color: "#52b788", fontWeight: "700", fontSize: "20px" }}>{val}%</div>
                </div>
              ))}
            </div>
            {sug.uptake_rationale && (
              <div style={{ color: "#5a7fa8", fontSize: "12px", marginTop: "12px", fontStyle: "italic" }}>
                {sug.uptake_rationale}
              </div>
            )}
          </div>

          {/* Costs & Workforce */}
          <div style={theme.card}>
            <div style={{ fontSize: "12px", letterSpacing: "1.5px", textTransform: "uppercase", color: "#52b788", marginBottom: "16px" }}>
              Costs &amp; Workforce
            </div>
            {sug.typical_pathway_cost && (
              <div style={theme.row}>
                <span style={{ color: "#8fa8c8", fontSize: "13px" }}>Typical pathway cost / patient</span>
                <span style={{ color: "#fff", fontWeight: "700", fontSize: "16px" }}>
                  £{sug.typical_pathway_cost.toLocaleString()}
                </span>
              </div>
            )}
            {sug.workforce_suggestion && (
              <>
                <div style={theme.sectionTitle}>Workforce benchmark</div>
                <div style={{ ...theme.row, borderBottom: "none" }}>
                  <span style={{ color: "#8fa8c8", fontSize: "13px" }}>Staff role</span>
                  <span style={{ color: "#e8edf5", fontSize: "13px" }}>{sug.workforce_suggestion.role}</span>
                </div>
                <div style={theme.row}>
                  <span style={{ color: "#8fa8c8", fontSize: "13px" }}>Setup time</span>
                  <span style={{ color: "#e8edf5", fontSize: "13px" }}>{sug.workforce_suggestion.setup_minutes} min / patient</span>
                </div>
                <div style={{ ...theme.row, borderBottom: "none" }}>
                  <span style={{ color: "#8fa8c8", fontSize: "13px" }}>Follow-up time</span>
                  <span style={{ color: "#e8edf5", fontSize: "13px" }}>{sug.workforce_suggestion.followup_minutes} min / patient</span>
                </div>
              </>
            )}
          </div>

          {/* NICE Guidance */}
          {sug.relevant_nice_guidance && sug.relevant_nice_guidance.length > 0 && (
            <div style={{ ...theme.card, gridColumn: "1 / -1" }}>
              <div style={{ fontSize: "12px", letterSpacing: "1.5px", textTransform: "uppercase", color: "#52b788", marginBottom: "16px" }}>
                Relevant NICE Guidance
              </div>
              {sug.relevant_nice_guidance.map(g => (
                <div key={g.id} style={{ ...theme.row, alignItems: "flex-start", flexDirection: "column", gap: "6px" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                    <span style={theme.pill("#5a7fa8")}>{g.id}</span>
                    <span style={theme.pill(
                      g.type === "Technology Appraisal" ? "#52b788" :
                      g.type === "NICE Guideline" ? "#5a7fa8" :
                      g.type === "Medtech Innovation Briefing" ? "#a38bf5" : "#f5a623"
                    )}>{g.type}</span>
                    {g.decision && (
                      <span style={theme.pill(
                        g.decision === "Recommended" ? "#52b788" :
                        g.decision === "Not recommended" ? "#e85d6f" : "#ffc107"
                      )}>{g.decision}</span>
                    )}
                  </div>
                  <div style={{ color: "#e8edf5", fontSize: "13px" }}>{g.title}</div>
                  {g.icer && (
                    <div style={{ color: "#5a7fa8", fontSize: "12px" }}>
                      ICER: £{g.icer.toLocaleString()} / QALY
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Warnings */}
          {result.warnings && result.warnings.length > 0 && (
            <div style={{ gridColumn: "1 / -1" }}>
              {result.warnings.map((w, i) => (
                <div key={i} style={theme.warn}>⚠ {w}</div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Panel: NHS Reference Costs ────────────────────────────────────────────────
function NHSCostsPanel() {
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [loaded, setLoaded] = useState(false);

  const fetchCosts = useCallback(async (q = search, cat = category) => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (q) params.set("search", q);
      if (cat) params.set("category", cat);
      const data = await apiFetch(`/api/evidence/reference-costs?${params}`);
      setResults(data);
      setLoaded(true);
    } catch (e) {
      if (e.message.includes("404") || e.message.includes("No costs")) {
        setResults({ costs: {}, metadata: {} });
      } else {
        setError(e.message);
      }
    } finally {
      setLoading(false);
    }
  }, [search, category]);

  const handleCategoryClick = (cat) => {
    const newCat = cat === category ? "" : cat;
    setCategory(newCat);
    fetchCosts(search, newCat);
  };

  const handleSearch = (e) => {
    e.preventDefault();
    fetchCosts();
  };

  const costs = results?.costs || {};
  const meta = results?.metadata || {};
  const entries = Object.entries(costs);

  return (
    <div>
      <p style={{ color: "#8fa8c8", fontSize: "14px", marginBottom: "24px", lineHeight: "1.6" }}>
        NHS National Cost Collection 2024/25 — 37 unit costs across 7 categories. Search or filter to find reference values for your model inputs.
      </p>

      {/* Category filter */}
      <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginBottom: "16px" }}>
        {CATEGORIES.map(cat => (
          <button key={cat} style={theme.tag(category === cat)} onClick={() => handleCategoryClick(cat)}>
            {cat.charAt(0).toUpperCase() + cat.slice(1)}
          </button>
        ))}
        {category && (
          <button style={theme.tag(false)} onClick={() => { setCategory(""); fetchCosts(search, ""); }}>
            × Clear filter
          </button>
        )}
      </div>

      {/* Search */}
      <form onSubmit={handleSearch} style={{ display: "flex", gap: "12px", marginBottom: "24px" }}>
        <input
          style={{ ...theme.input, flex: 1 }}
          type="text"
          placeholder='Search by name, e.g. "MRI", "bed day", "outpatient"…'
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
        <button type="submit" style={theme.btn}>
          {loading ? <><Spinner />Searching…</> : "Search"}
        </button>
        {!loaded && (
          <button
            type="button"
            style={theme.btnOutline}
            onClick={() => fetchCosts("", "")}
          >
            Load all costs
          </button>
        )}
      </form>

      {error && <div style={theme.error}>{error}</div>}

      {results && (
        <>
          {meta.source && (
            <div style={{ color: "#5a7fa8", fontSize: "11px", marginBottom: "12px" }}>
              Source: {meta.source} · {entries.length} item{entries.length !== 1 ? "s" : ""} shown
            </div>
          )}

          {entries.length === 0 ? (
            <div style={{ color: "#5a7fa8", fontSize: "14px", textAlign: "center", padding: "40px" }}>
              No costs matched your search.
            </div>
          ) : (
            <div style={theme.card}>
              <div style={{
                display: "grid",
                gridTemplateColumns: "1fr auto",
                gap: "0",
                fontSize: "11px",
                letterSpacing: "1px",
                textTransform: "uppercase",
                color: "#5a7fa8",
                paddingBottom: "8px",
                borderBottom: "1px solid rgba(255,255,255,0.08)",
                marginBottom: "4px",
              }}>
                <span>Cost item</span>
                <span style={{ textAlign: "right" }}>Unit cost (£)</span>
              </div>
              {entries.map(([name, value]) => (
                <div key={name} style={theme.row}>
                  <span style={{ color: "#e8edf5", fontSize: "13px" }}>{name.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase())}</span>
                  <span style={{ color: "#52b788", fontWeight: "700", fontSize: "15px", fontVariantNumeric: "tabular-nums" }}>
                    £{Number(value).toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                  </span>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {!loaded && !loading && !error && (
        <div style={{ textAlign: "center", padding: "60px 0", color: "#5a7fa8" }}>
          <div style={{ fontSize: "32px", marginBottom: "12px" }}>£</div>
          <div style={{ fontSize: "14px" }}>Select a category or search to view reference costs</div>
        </div>
      )}
    </div>
  );
}

// ─── Panel: Population Data ─────────────────────────────────────────────────────
function PopulationPanel() {
  const [region, setRegion] = useState("");
  const [condition, setCondition] = useState("");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const fetchData = async (r = region, c = condition) => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (r) params.set("region", r);
      if (c) params.set("condition", c.toLowerCase().replace(/ /g, "_"));
      const result = await apiFetch(`/api/evidence/population?${params}`);
      setData(result);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const handleChange = (field, value) => {
    if (field === "region") {
      setRegion(value);
      fetchData(value, condition);
    } else {
      setCondition(value);
      fetchData(region, value);
    }
  };

  const pop = data?.population;
  const rd = data?.region_detail;
  const pd = data?.prevalence_detail;

  return (
    <div>
      <p style={{ color: "#8fa8c8", fontSize: "14px", marginBottom: "24px", lineHeight: "1.6" }}>
        ONS Mid-Year Population Estimates 2024 with NHS Digital QOF prevalence rates.
        Select a region and condition to estimate your eligible patient population.
      </p>

      <div style={{ ...theme.grid2, marginBottom: "24px" }}>
        <div>
          <label style={theme.label}>NHS England region</label>
          <select style={theme.select} value={region} onChange={e => handleChange("region", e.target.value)}>
            <option value="">England (all regions)</option>
            {REGIONS.map(r => <option key={r}>{r}</option>)}
          </select>
        </div>
        <div>
          <label style={theme.label}>Clinical condition</label>
          <select style={theme.select} value={condition} onChange={e => handleChange("condition", e.target.value)}>
            <option value="">All conditions</option>
            {CONDITIONS.map(c => <option key={c}>{c}</option>)}
          </select>
        </div>
      </div>

      {loading && (
        <div style={{ color: "#52b788", fontSize: "14px" }}><Spinner />Loading population data…</div>
      )}
      {error && <div style={theme.error}>{error}</div>}

      {data && !loading && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "20px" }}>

          {/* Population summary */}
          <div style={theme.card}>
            <div style={{ fontSize: "12px", letterSpacing: "1.5px", textTransform: "uppercase", color: "#52b788", marginBottom: "16px" }}>
              Population
            </div>
            {rd ? (
              <>
                <div style={{ fontSize: "28px", fontWeight: "700", color: "#fff", marginBottom: "4px" }}>
                  {rd.population.toLocaleString()}
                </div>
                <div style={{ color: "#5a7fa8", fontSize: "13px", marginBottom: "16px" }}>{rd.region} population</div>
                <div style={theme.row}>
                  <span style={{ color: "#8fa8c8", fontSize: "13px" }}>UK total</span>
                  <span style={{ color: "#e8edf5", fontSize: "13px" }}>{pop?.uk_total?.total?.toLocaleString()}</span>
                </div>
                <div style={{ ...theme.row, borderBottom: "none" }}>
                  <span style={{ color: "#8fa8c8", fontSize: "13px" }}>England total</span>
                  <span style={{ color: "#e8edf5", fontSize: "13px" }}>{pop?.uk_total?.england?.toLocaleString()}</span>
                </div>
              </>
            ) : (
              <>
                <div style={{ fontSize: "28px", fontWeight: "700", color: "#fff", marginBottom: "4px" }}>
                  {pop?.uk_total?.england?.toLocaleString()}
                </div>
                <div style={{ color: "#5a7fa8", fontSize: "13px", marginBottom: "16px" }}>England population</div>
                <div style={theme.row}>
                  <span style={{ color: "#8fa8c8", fontSize: "13px" }}>UK total</span>
                  <span style={{ color: "#e8edf5", fontSize: "13px" }}>{pop?.uk_total?.total?.toLocaleString()}</span>
                </div>
              </>
            )}
          </div>

          {/* Eligible population */}
          {pd && (
            <div style={theme.card}>
              <div style={{ fontSize: "12px", letterSpacing: "1.5px", textTransform: "uppercase", color: "#52b788", marginBottom: "16px" }}>
                Eligible Patients
              </div>
              <div style={{ fontSize: "28px", fontWeight: "700", color: "#52b788", marginBottom: "4px" }}>
                {pd.estimated_eligible_in_region?.toLocaleString() ?? pd.estimated_eligible_england?.toLocaleString()}
              </div>
              <div style={{ color: "#5a7fa8", fontSize: "13px", marginBottom: "16px" }}>
                estimated {condition.toLowerCase()} patients {region ? `in ${region}` : "in England"}
              </div>
              <div style={theme.row}>
                <span style={{ color: "#8fa8c8", fontSize: "13px" }}>National prevalence</span>
                <span style={{ color: "#e8edf5", fontSize: "13px" }}>{(pd.national_prevalence_rate * 100).toFixed(1)}%</span>
              </div>
              {pd.estimated_eligible_england && (
                <div style={{ ...theme.row, borderBottom: "none" }}>
                  <span style={{ color: "#8fa8c8", fontSize: "13px" }}>Estimated in England</span>
                  <span style={{ color: "#e8edf5", fontSize: "13px" }}>{pd.estimated_eligible_england.toLocaleString()}</span>
                </div>
              )}
            </div>
          )}

          {/* Regions table */}
          {!region && pop?.england_regions && (
            <div style={{ ...theme.card, gridColumn: "1 / -1" }}>
              <div style={{ fontSize: "12px", letterSpacing: "1.5px", textTransform: "uppercase", color: "#52b788", marginBottom: "16px" }}>
                NHS England Regions
              </div>
              {Object.entries(pop.england_regions)
                .sort((a, b) => b[1] - a[1])
                .map(([reg, count]) => {
                  const max = Math.max(...Object.values(pop.england_regions));
                  const pct = count / max;
                  return (
                    <div key={reg} style={{ marginBottom: "10px" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "4px" }}>
                        <span style={{ color: "#e8edf5", fontSize: "13px" }}>
                          {reg.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase())}
                        </span>
                        <span style={{ color: "#8fa8c8", fontSize: "13px" }}>{count.toLocaleString()}</span>
                      </div>
                      <div style={{ height: "4px", background: "rgba(255,255,255,0.06)", borderRadius: "2px" }}>
                        <div style={{ height: "100%", width: `${pct * 100}%`, background: "linear-gradient(90deg,#2d6a4f,#52b788)", borderRadius: "2px" }} />
                      </div>
                    </div>
                  );
                })}
            </div>
          )}
        </div>
      )}

      {!data && !loading && (
        <div style={{ textAlign: "center", padding: "60px 0", color: "#5a7fa8" }}>
          <div style={{ fontSize: "32px", marginBottom: "12px" }}>👥</div>
          <div style={{ fontSize: "14px" }}>Select a region or condition above to load population data</div>
        </div>
      )}
    </div>
  );
}

// ─── Panel: NICE Guidance ──────────────────────────────────────────────────────
function NICEPanel() {
  const [search, setSearch] = useState("");
  const [guidanceType, setGuidanceType] = useState("all");
  const [results, setResults] = useState(null);
  const [threshold, setThreshold] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const fetchGuidance = async (q = search, type = guidanceType) => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (q) params.set("search", q);
      if (type && type !== "all") params.set("type", type);
      params.set("include_threshold", "true");
      const data = await apiFetch(`/api/evidence/nice-guidance?${params}`);
      setResults(data.guidance);
      setThreshold(data.threshold_context || null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const TYPE_COLORS = {
    "Technology Appraisal": "#52b788",
    "NICE Guideline": "#5a7fa8",
    "Medtech Innovation Briefing": "#a38bf5",
    "Diagnostics Guidance": "#f5a623",
    "Highly Specialised Technology": "#e85d6f",
  };

  const DECISION_COLORS = {
    "Recommended": "#52b788",
    "Not recommended": "#e85d6f",
    "Optimised": "#ffc107",
    "Only in research": "#ffc107",
  };

  return (
    <div>
      <p style={{ color: "#8fa8c8", fontSize: "14px", marginBottom: "24px", lineHeight: "1.6" }}>
        Curated NICE guidance database — Technology Appraisals, Guidelines, MIBs, and Diagnostics Guidance
        relevant to digital health and remote monitoring interventions.
      </p>

      {/* Type filter */}
      <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginBottom: "16px" }}>
        {NICE_TYPES.map(({ key, label }) => (
          <button
            key={key}
            style={theme.tag(guidanceType === key)}
            onClick={() => { setGuidanceType(key); fetchGuidance(search, key); }}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Search */}
      <form
        onSubmit={e => { e.preventDefault(); fetchGuidance(); }}
        style={{ display: "flex", gap: "12px", marginBottom: "24px" }}
      >
        <input
          style={{ ...theme.input, flex: 1 }}
          type="text"
          placeholder='Search by condition, technology type, or keyword…'
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
        <button type="submit" style={theme.btn}>
          {loading ? <><Spinner />Searching…</> : "Search"}
        </button>
        {results === null && (
          <button type="button" style={theme.btnOutline} onClick={() => fetchGuidance("", "all")}>
            Show all
          </button>
        )}
      </form>

      {error && <div style={theme.error}>{error}</div>}

      {/* WTP threshold context */}
      {threshold && (
        <div style={{ ...theme.card, marginBottom: "20px", borderColor: "rgba(82,183,136,0.2)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div>
              <div style={{ fontSize: "12px", letterSpacing: "1.2px", textTransform: "uppercase", color: "#52b788", marginBottom: "6px" }}>
                NICE WTP Threshold — {threshold.threshold_category.replace(/_/g, " ")}
              </div>
              <div style={{ color: "#fff", fontSize: "18px", fontWeight: "700" }}>
                £{threshold.standard_threshold.toLocaleString()} – £{threshold.upper_threshold.toLocaleString()} / QALY
              </div>
            </div>
            {threshold.precedents?.length > 0 && (
              <div style={{ textAlign: "right" }}>
                <div style={{ color: "#5a7fa8", fontSize: "11px", marginBottom: "4px" }}>Precedent ICERs</div>
                <div style={{ color: "#8fa8c8", fontSize: "13px" }}>
                  {threshold.precedents.filter(p => p.icer).slice(0, 3).map(p => `£${p.icer.toLocaleString()}`).join(" · ")}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {results !== null && (
        <>
          <div style={{ color: "#5a7fa8", fontSize: "11px", marginBottom: "12px" }}>
            {results.length} record{results.length !== 1 ? "s" : ""} found
          </div>
          {results.length === 0 ? (
            <div style={{ color: "#5a7fa8", fontSize: "14px", textAlign: "center", padding: "40px" }}>
              No guidance matched your search.
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              {results.map(g => (
                <div key={g.id} style={theme.card}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "16px" }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "8px", flexWrap: "wrap" }}>
                        <span style={theme.pill("#5a7fa8")}>{g.id}</span>
                        <span style={theme.pill(TYPE_COLORS[g.type] || "#8fa8c8")}>{g.type}</span>
                        {g.decision && (
                          <span style={theme.pill(DECISION_COLORS[g.decision] || "#8fa8c8")}>{g.decision}</span>
                        )}
                        <span style={{ color: "#5a7fa8", fontSize: "11px" }}>{g.date}</span>
                      </div>
                      <div style={{ color: "#e8edf5", fontSize: "14px", fontWeight: "600", marginBottom: "6px" }}>
                        {g.title}
                      </div>
                      <div style={{ color: "#8fa8c8", fontSize: "12px" }}>
                        Condition: {g.condition}
                        {g.intervention_types?.length > 0 && (
                          <> · {g.intervention_types.join(", ")}</>
                        )}
                      </div>
                    </div>
                    {g.icer && (
                      <div style={{ textAlign: "right", flexShrink: 0 }}>
                        <div style={{ color: "#5a7fa8", fontSize: "10px", letterSpacing: "1px", textTransform: "uppercase", marginBottom: "2px" }}>ICER</div>
                        <div style={{ color: "#52b788", fontWeight: "700", fontSize: "18px" }}>
                          £{g.icer.toLocaleString()}
                        </div>
                        <div style={{ color: "#5a7fa8", fontSize: "11px" }}>/ QALY</div>
                      </div>
                    )}
                  </div>
                  {(g.comparators?.length > 0 || g.recommendations?.length > 0) && (
                    <div style={{ marginTop: "12px", paddingTop: "12px", borderTop: "1px solid rgba(255,255,255,0.06)" }}>
                      <div style={{ color: "#5a7fa8", fontSize: "11px", letterSpacing: "1px", textTransform: "uppercase", marginBottom: "6px" }}>
                        {g.comparators?.length > 0 ? "Comparators" : "Recommendations"}
                      </div>
                      <div style={{ color: "#8fa8c8", fontSize: "12px" }}>
                        {(g.comparators || g.recommendations || []).join(" · ")}
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {results === null && !loading && (
        <div style={{ textAlign: "center", padding: "60px 0", color: "#5a7fa8" }}>
          <div style={{ fontSize: "32px", marginBottom: "12px" }}>📋</div>
          <div style={{ fontSize: "14px" }}>Search for guidance or click "Show all" to browse all 15 records</div>
        </div>
      )}
    </div>
  );
}

// ─── Main component ────────────────────────────────────────────────────────────
const PANELS = [
  { key: "suggest", label: "Suggest Defaults", icon: "✨" },
  { key: "costs", label: "NHS Reference Costs", icon: "£" },
  { key: "population", label: "Population Data", icon: "👥" },
  { key: "nice", label: "NICE Guidance", icon: "📋" },
];

export default function EvidenceExplorer() {
  const [activePanel, setActivePanel] = useState("suggest");

  return (
    <div style={{ maxWidth: "1100px", margin: "0 auto", padding: "40px 40px 80px" }}>
      {/* Page header */}
      <div style={{ marginBottom: "32px" }}>
        <h1 style={{ fontSize: "26px", fontWeight: "700", color: "#fff", margin: "0 0 8px" }}>
          Evidence &amp; Reference Data
        </h1>
        <p style={{ color: "#5a7fa8", fontSize: "14px", margin: 0 }}>
          NHS Cost Collection · ONS Population Estimates · NICE Guidance Database
        </p>
      </div>

      {/* Internal nav */}
      <div style={{
        display: "flex",
        gap: "0",
        borderBottom: "1px solid rgba(255,255,255,0.08)",
        marginBottom: "32px",
      }}>
        {PANELS.map(p => {
          const active = activePanel === p.key;
          return (
            <button
              key={p.key}
              onClick={() => setActivePanel(p.key)}
              style={{
                padding: "12px 24px",
                fontSize: "13px",
                fontWeight: active ? "700" : "500",
                fontFamily: "'Georgia','Times New Roman',serif",
                color: active ? "#fff" : "rgba(255,255,255,0.45)",
                background: active ? "rgba(255,255,255,0.04)" : "transparent",
                border: "none",
                borderBottom: active ? "2px solid #52b788" : "2px solid transparent",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                gap: "8px",
                transition: "all 0.15s",
              }}
            >
              <span>{p.icon}</span>
              {p.label}
            </button>
          );
        })}
      </div>

      {/* CSS for spinner */}
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>

      {/* Panel content */}
      {activePanel === "suggest" && <SuggestDefaultsPanel />}
      {activePanel === "costs" && <NHSCostsPanel />}
      {activePanel === "population" && <PopulationPanel />}
      {activePanel === "nice" && <NICEPanel />}
    </div>
  );
}
