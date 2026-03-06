import { useState } from "react";
import { createRoot } from "react-dom/client";
import HEORInputEngine from "../app/components/heor-input-engine.jsx";
import MarkovICERForm from "../app/components/markov-icer-form.jsx";
import EvidenceExplorer from "../app/components/evidence-explorer.jsx";
import SLRScreener from "../app/components/slr-screener.jsx";
import AutoFillModal from "../frontend/src/components/AutoFillModal.jsx";
import MarkovAutoFillModal from "../frontend/src/components/MarkovAutoFillModal.jsx";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const GREEN     = "#52b788";
const GREEN_DIM = "#2d6a4f";
const MUTED     = "#6a8fb5";
const MUTED2    = "#4a6482";
const BORDER    = "rgba(255,255,255,0.10)";

const tabs = [
  { key: "bia",      label: "Budget Impact Analysis",    icon: "📊" },
  { key: "cea",      label: "Cost-Effectiveness (ICER)", icon: "⚖️"  },
  { key: "evidence", label: "Evidence & Defaults",        icon: "🔬" },
  { key: "slr",      label: "SLR Screener",               icon: "📚" },
];

// ---------------------------------------------------------------------------
// EvidenceSummaryBanner
// ---------------------------------------------------------------------------

function EvidenceSummaryBanner({ fillResult, onRerun, onDismiss }) {
  const [expanded, setExpanded] = useState(false);

  if (!fillResult) return null;

  const { sources = [], warnings = [], evidenceSummary = {}, derivationNotes = [] } = fillResult;
  const papersFound = evidenceSummary.papers_found  ?? sources.filter(s => s.type === "pubmed").length;
  const niceFound   = evidenceSummary.nice_guidance_found ?? sources.filter(s => s.type === "nice").length;
  const quality     = evidenceSummary.data_quality  ?? "medium";

  const qualityColor = quality === "high" ? GREEN : quality === "medium" ? "#e8a838" : "#dc2626";
  const qualityBg    = quality === "high" ? "rgba(82,183,136,0.12)" : quality === "medium" ? "rgba(232,168,56,0.12)" : "rgba(220,38,38,0.12)";

  // Show derivation notes for Markov results (no sources array)
  const notes = derivationNotes.length > 0 ? derivationNotes : null;

  return (
    <div style={{
      background: "rgba(82,183,136,0.07)",
      borderBottom: "1px solid rgba(82,183,136,0.18)",
      padding: "12px 40px",
      display: "flex", alignItems: "flex-start", gap: "16px",
      fontSize: "12px", fontFamily: "'Georgia','Times New Roman',serif",
    }}>
      <span style={{ fontSize: "16px", flexShrink: 0, marginTop: "1px" }}>✨</span>

      <div style={{ flex: 1 }}>
        <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
          <span style={{ color: GREEN, fontWeight: "600" }}>Auto-filled from Evidence</span>

          {(papersFound > 0 || niceFound > 0) && (
            <span style={{ color: MUTED }}>
              {papersFound} paper{papersFound !== 1 ? "s" : ""} ·{" "}
              {niceFound} NICE guidance{niceFound !== 1 ? "s" : ""}
            </span>
          )}

          <span style={{
            padding: "2px 8px", borderRadius: "4px", fontSize: "11px",
            background: qualityBg, color: qualityColor, fontWeight: "600",
            textTransform: "capitalize",
          }}>
            {quality} confidence
          </span>
        </div>

        {warnings.length > 0 && (
          <div style={{ marginTop: "6px", color: "#e8a838", lineHeight: "1.5" }}>
            {warnings.map((w, i) => (
              <div key={i} style={{ display: "flex", gap: "6px" }}>
                <span>⚠</span><span>{w}</span>
              </div>
            ))}
          </div>
        )}

        {(sources.length > 0 || notes) && (
          <button
            onClick={() => setExpanded(e => !e)}
            style={{
              marginTop: "6px", background: "none", border: "none",
              color: MUTED, fontSize: "11px", cursor: "pointer", padding: 0,
              fontFamily: "inherit",
            }}
          >
            {expanded
              ? "▲ Hide details"
              : sources.length > 0
                ? `▼ Show ${sources.length} source${sources.length !== 1 ? "s" : ""}`
                : "▼ Show derivation notes"}
          </button>
        )}

        {expanded && sources.length > 0 && (
          <ul style={{ margin: "8px 0 0", padding: "0 0 0 16px", color: MUTED, lineHeight: "1.7" }}>
            {sources.map((s, i) => (
              <li key={i}>
                {s.url
                  ? <a href={s.url} target="_blank" rel="noreferrer" style={{ color: GREEN }}>{s.title || s.url}</a>
                  : <span>{s.title || s.pmid || "Source " + (i + 1)}</span>}
                {s.year && <span style={{ color: MUTED2 }}> ({s.year})</span>}
              </li>
            ))}
          </ul>
        )}

        {expanded && notes && sources.length === 0 && (
          <ul style={{ margin: "8px 0 0", padding: "0 0 0 16px", color: MUTED, lineHeight: "1.7" }}>
            {notes.map((n, i) => <li key={i}>{n}</li>)}
          </ul>
        )}
      </div>

      <div style={{ display: "flex", gap: "8px", flexShrink: 0, alignItems: "center" }}>
        <button onClick={onRerun} style={actionBtn}>Re-run</button>
        <button onClick={onDismiss} style={{ ...actionBtn, color: MUTED2, borderColor: "transparent" }}>✕</button>
      </div>
    </div>
  );
}

const actionBtn = {
  padding: "4px 10px", borderRadius: "5px",
  background: "transparent", border: `1px solid ${BORDER}`,
  color: MUTED, fontSize: "11px", cursor: "pointer",
  fontFamily: "'Georgia','Times New Roman',serif",
};

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

function App() {
  const [activeTab, setActiveTab] = useState("bia");

  // Shared search inputs — persisted across tabs
  const [sharedSearch, setSharedSearch] = useState({ deviceName: "", indication: "", cost: "", benefits: "" });

  // BIA auto-fill modal state
  const [showModal, setShowModal]   = useState(false);
  const [fillResult, setFillResult] = useState(null);
  const [fillKey, setFillKey]       = useState(0);

  // Markov / CEA auto-fill modal state
  const [showMarkovModal, setShowMarkovModal]     = useState(false);
  const [markovFillResult, setMarkovFillResult]   = useState(null);
  const [markovFillKey, setMarkovFillKey]         = useState(0);

  // ── BIA handlers ──────────────────────────────────────────────────────────

  const handleModalComplete = (result) => {
    setFillResult(result);
    setFillKey(k => k + 1);
    setShowModal(false);
  };

  const handleModalSkip = () => setShowModal(false);

  const handleRerun = () => {
    setFillResult(null);
    setShowModal(true);
  };

  const handleDismissBanner = () => setFillResult(null);

  // ── Markov handlers ───────────────────────────────────────────────────────

  const handleMarkovModalComplete = (result) => {
    setMarkovFillResult(result);
    setMarkovFillKey(k => k + 1);
    setShowMarkovModal(false);
  };

  const handleMarkovModalSkip = () => setShowMarkovModal(false);

  const handleMarkovRerun = () => {
    setMarkovFillResult(null);
    setShowMarkovModal(true);
  };

  const handleDismissMarkovBanner = () => setMarkovFillResult(null);

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div style={{
      minHeight: "100vh",
      background: "linear-gradient(135deg, #0a1628 0%, #0d2044 50%, #0a1f3d 100%)",
      fontFamily: "'Georgia','Times New Roman',serif",
      color: "#e8edf5",
    }}>

      {/* ── Header ── */}
      <div style={{
        background: "rgba(255,255,255,0.03)",
        borderBottom: "1px solid rgba(255,255,255,0.08)",
        padding: "0 40px",
        display: "flex", alignItems: "stretch",
      }}>
        <div style={{
          display: "flex", alignItems: "center", gap: "10px",
          paddingRight: "32px", marginRight: "8px",
          borderRight: "1px solid rgba(255,255,255,0.08)",
        }}>
          <div style={{
            width: "34px", height: "34px",
            background: `linear-gradient(135deg,${GREEN_DIM},${GREEN})`,
            borderRadius: "7px",
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: "11px", fontWeight: "800", color: "#fff",
          }}>HE</div>
          <div>
            <div style={{ fontSize: "18px", fontWeight: "700", color: "#fff", lineHeight: "1.2" }}>HEOR Engine</div>
            <div style={{ fontSize: "9px", color: GREEN, letterSpacing: "1.5px", textTransform: "uppercase" }}>NHS Economic Analysis</div>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "stretch" }}>
          {tabs.map((tab) => {
            const active = activeTab === tab.key;
            return (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                style={{
                  padding: "18px 28px", fontSize: "13px",
                  fontWeight: active ? "700" : "500",
                  fontFamily: "'Georgia','Times New Roman',serif",
                  color: active ? "#fff" : "rgba(255,255,255,0.45)",
                  background: active ? "rgba(255,255,255,0.04)" : "transparent",
                  border: "none",
                  borderBottom: active ? `2px solid ${GREEN}` : "2px solid transparent",
                  cursor: "pointer", transition: "all 0.2s",
                  display: "flex", alignItems: "center", gap: "8px",
                }}
              >
                <span style={{ fontSize: "15px" }}>{tab.icon}</span>
                {tab.label}
              </button>
            );
          })}
        </div>

        <div style={{
          marginLeft: "auto", display: "flex", alignItems: "center",
          fontSize: "12px", color: "#5a7fa8", fontStyle: "italic",
        }}>
          {activeTab === "bia"
            ? "Budget Impact Analysis — NHS Payer Perspective"
            : activeTab === "cea"
            ? "Markov Model / ICER Calculator"
            : activeTab === "evidence"
            ? "NHS Costs · ONS Population · NICE Guidance"
            : "AI Abstract Screening · PICO Framework · Claude"}
        </div>
      </div>

      {/* ── BIA tab ── */}
      {activeTab === "bia" && (
        <>
          {fillResult ? (
            <EvidenceSummaryBanner
              fillResult={fillResult}
              onRerun={handleRerun}
              onDismiss={handleDismissBanner}
            />
          ) : (
            <div style={{
              padding: "12px 40px",
              background: "rgba(82,183,136,0.05)",
              borderBottom: "1px solid rgba(255,255,255,0.06)",
              display: "flex", alignItems: "center", gap: "14px",
            }}>
              <button
                onClick={() => setShowModal(true)}
                style={{
                  background: `linear-gradient(135deg,${GREEN_DIM},${GREEN})`,
                  border: "none", borderRadius: "7px",
                  color: "#fff", padding: "9px 20px",
                  fontSize: "13px", fontWeight: "600",
                  cursor: "pointer", fontFamily: "'Georgia',serif",
                }}
              >
                ✨ Auto-fill from Evidence
              </button>
              <span style={{ fontSize: "12px", color: MUTED, fontStyle: "italic" }}>
                AI populates budget impact parameters from PubMed and NICE
              </span>
            </div>
          )}
          <HEORInputEngine
            key={fillKey}
            hideChrome
            externalFillData={fillResult?.formData ?? null}
            skipQuickStart
          />
        </>
      )}

      {/* ── CEA tab ── */}
      {activeTab === "cea" && (
        <>
          {markovFillResult && (
            <EvidenceSummaryBanner
              fillResult={markovFillResult}
              onRerun={handleMarkovRerun}
              onDismiss={handleDismissMarkovBanner}
            />
          )}

          {!markovFillResult && (
            <div style={{
              padding: "12px 40px",
              background: "rgba(82,183,136,0.05)",
              borderBottom: "1px solid rgba(255,255,255,0.06)",
              display: "flex", alignItems: "center", gap: "14px",
            }}>
              <button
                onClick={() => setShowMarkovModal(true)}
                style={{
                  background: `linear-gradient(135deg,${GREEN_DIM},${GREEN})`,
                  border: "none", borderRadius: "7px",
                  color: "#fff", padding: "9px 20px",
                  fontSize: "13px", fontWeight: "600",
                  cursor: "pointer", fontFamily: "'Georgia',serif",
                }}
              >
                ✨ Auto-fill from Evidence
              </button>
              <span style={{ fontSize: "12px", color: MUTED, fontStyle: "italic" }}>
                AI derives mortality, utility, and cost parameters from PubMed and NICE
              </span>
            </div>
          )}

          <MarkovICERForm
            key={markovFillKey}
            hideChrome
            externalFillData={markovFillResult?.formData ?? null}
            skipQuickStart
          />
        </>
      )}

      {activeTab === "evidence" && <EvidenceExplorer />}
      {activeTab === "slr"      && <SLRScreener />}

      {/* ── BIA auto-fill modal ── */}
      {activeTab === "bia" && showModal && (
        <AutoFillModal
          onComplete={handleModalComplete}
          onSkip={handleModalSkip}
          initialInputs={sharedSearch}
          onFieldsChange={setSharedSearch}
        />
      )}

      {/* ── Markov auto-fill modal ── */}
      {activeTab === "cea" && showMarkovModal && (
        <MarkovAutoFillModal
          onComplete={handleMarkovModalComplete}
          onSkip={handleMarkovModalSkip}
          initialInputs={sharedSearch}
          onFieldsChange={setSharedSearch}
        />
      )}
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
