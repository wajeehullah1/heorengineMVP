/**
 * MarkovAutoFillModal.jsx
 *
 * Full-screen overlay modal for the "Quick Start — Auto-fill CEA from Evidence" flow.
 *
 * Props:
 *   onComplete(result)  — called with a MarkovAutoFillResult when auto-fill succeeds
 *   onSkip()            — called when user dismisses the modal without auto-filling
 */

import { useState, useRef } from "react";
import { handleMarkovAutoFill, MARKOV_PROGRESS_LABELS } from "../utils/autoFill.js";

// ---------------------------------------------------------------------------
// Style tokens (matches dark theme)
// ---------------------------------------------------------------------------
const NAVY      = "#0d2044";
const GREEN     = "#52b788";
const GREEN_DIM = "#2d6a4f";
const TEXT      = "#e8edf5";
const MUTED     = "#6a8fb5";
const MUTED2    = "#4a6482";
const BORDER    = "rgba(255,255,255,0.10)";
const INPUT_BG  = "rgba(255,255,255,0.05)";

// ---------------------------------------------------------------------------
// MarkovAutoFillModal
// ---------------------------------------------------------------------------

export default function MarkovAutoFillModal({ onComplete, onSkip, initialInputs = {}, onFieldsChange }) {
  const [fields, setFields] = useState({
    deviceName: initialInputs.deviceName ?? "",
    indication: initialInputs.indication ?? "",
    cost:       initialInputs.cost       ?? "",
  });

  const [status, setStatus]     = useState("idle");
  const [progress, setProgress] = useState("");
  const [error, setError]       = useState(null);

  const abortRef = useRef(null);

  const set = (key, val) => setFields(f => {
    const next = { ...f, [key]: val };
    onFieldsChange?.(next);
    return next;
  });

  const canSubmit =
    fields.deviceName.trim() &&
    fields.indication.trim() &&
    fields.cost.toString().trim() &&
    status !== "loading";

  // ── Handlers ──────────────────────────────────────────────────────────────

  const handleSubmit = async () => {
    setStatus("loading");
    setError(null);
    setProgress(MARKOV_PROGRESS_LABELS.queued);

    abortRef.current = new AbortController();

    try {
      const result = await handleMarkovAutoFill(
        {
          deviceName: fields.deviceName.trim(),
          indication: fields.indication.trim(),
          cost:       parseFloat(fields.cost) || 0,
        },
        (_status, label) => setProgress(label),
        abortRef.current.signal
      );

      setStatus("done");
      onComplete(result);
    } catch (err) {
      if (err.name === "AbortError") return;
      setStatus("error");
      setError(
        err.message ||
          "Evidence gathering failed. You can try again or fill the form manually."
      );
    }
  };

  const handleCancel = () => {
    abortRef.current?.abort();
    setStatus("idle");
    setProgress("");
    setError(null);
  };

  const handleSkip = () => {
    abortRef.current?.abort();
    onSkip();
  };

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div style={overlay}>
      <div style={card}>
        {/* ── Header ── */}
        <div style={header}>
          <div style={logoIcon}>HE</div>
          <div>
            <div style={logoTitle}>HEOR Engine</div>
            <div style={logoSub}>CEA Auto-fill from Evidence</div>
          </div>
          <button onClick={handleSkip} style={closeBtn} title="Skip to manual entry">
            ✕
          </button>
        </div>

        {/* ── Hero text ── */}
        <div style={heroSection}>
          <div style={heroEmoji}>⚖️</div>
          <div>
            <h2 style={heroTitle}>Quick Start — Cost-Effectiveness</h2>
            <p style={heroDesc}>
              Describe your intervention and we'll derive mortality rates, utility
              values, and annual costs from PubMed and NICE Technology Appraisals
              to pre-fill the Markov model. Takes about 60 seconds.
            </p>
          </div>
        </div>

        {/* ── Fields ── */}
        <div>
          <Field
            label="Device / intervention name *"
            placeholder="e.g. AI Sepsis Prediction Tool"
            value={fields.deviceName}
            onChange={v => set("deviceName", v)}
            disabled={status === "loading"}
          />

          <Field
            label="Clinical indication *"
            placeholder="e.g. Sepsis in ICU patients"
            value={fields.indication}
            onChange={v => set("indication", v)}
            disabled={status === "loading"}
          />

          <Field
            label="Device cost per patient (£) *"
            placeholder="e.g. 185"
            type="number"
            value={fields.cost}
            onChange={v => set("cost", v)}
            disabled={status === "loading"}
          />
        </div>

        {/* ── Progress indicator ── */}
        {status === "loading" && (
          <div style={progressBox}>
            <PulsingDots />
            <span style={{ fontSize: "13px", color: "#8fd4b0" }}>
              {progress || "Working..."}
            </span>
          </div>
        )}

        {/* ── Error ── */}
        {status === "error" && (
          <div style={errorBox}>
            <span style={{ marginRight: "6px" }}>⚠</span>
            {error}
          </div>
        )}

        {/* ── Buttons ── */}
        <div style={btnRow}>
          {status === "loading" ? (
            <button style={btnSecondary} onClick={handleCancel}>
              Cancel
            </button>
          ) : (
            <>
              <button
                style={canSubmit ? btnPrimary : { ...btnPrimary, ...btnDisabled }}
                onClick={handleSubmit}
                disabled={!canSubmit}
              >
                Auto-fill from Evidence
              </button>
              <button style={btnSecondary} onClick={handleSkip}>
                Skip — manual entry
              </button>
            </>
          )}
        </div>

        {/* ── Footer note ── */}
        <p style={footerNote}>
          Searches PubMed · NICE Technology Appraisals for mortality, utility, and cost data
          · Review every value before calculating ICER
        </p>
      </div>

      <style>{`
        @keyframes hePulse {
          0%, 100% { opacity: 0.3; transform: scale(0.85); }
          50%       { opacity: 1;   transform: scale(1);    }
        }
        @keyframes heFadeIn {
          from { opacity: 0; transform: translateY(16px); }
          to   { opacity: 1; transform: translateY(0);    }
        }
      `}</style>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Field sub-component
// ---------------------------------------------------------------------------

function Field({ label, placeholder, value, onChange, type = "text", disabled, multiline }) {
  const shared = {
    width: "100%", padding: "11px 14px", borderRadius: "7px",
    border: `1.5px solid ${BORDER}`,
    background: disabled ? "rgba(255,255,255,0.03)" : INPUT_BG,
    color: disabled ? MUTED : TEXT,
    fontSize: "14px", outline: "none", boxSizing: "border-box",
    fontFamily: "'Georgia','Times New Roman',serif",
    transition: "border-color 0.2s",
    marginBottom: 0,
  };

  return (
    <div style={{ marginBottom: "18px" }}>
      <label style={fieldLabel}>{label}</label>
      {multiline ? (
        <textarea
          rows={3}
          placeholder={placeholder}
          value={value}
          onChange={e => onChange(e.target.value)}
          disabled={disabled}
          style={{ ...shared, resize: "vertical", lineHeight: "1.5" }}
        />
      ) : (
        <input
          type={type}
          placeholder={placeholder}
          value={value}
          onChange={e => onChange(e.target.value)}
          disabled={disabled}
          style={shared}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// PulsingDots sub-component
// ---------------------------------------------------------------------------

function PulsingDots() {
  return (
    <div style={{ display: "flex", gap: "5px", flexShrink: 0 }}>
      {[0, 1, 2].map(i => (
        <div
          key={i}
          style={{
            width: "7px", height: "7px", borderRadius: "50%",
            background: GREEN,
            animation: `hePulse 1.2s ease-in-out ${i * 0.4}s infinite`,
          }}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const overlay = {
  position: "fixed", inset: 0,
  background: "rgba(6,14,32,0.88)",
  backdropFilter: "blur(4px)",
  display: "flex", alignItems: "center", justifyContent: "center",
  zIndex: 1000, padding: "20px",
  animation: "heFadeIn 0.25s ease",
};

const card = {
  background: `linear-gradient(160deg, #0d1e3a 0%, ${NAVY} 100%)`,
  border: `1px solid ${BORDER}`,
  borderRadius: "16px",
  padding: "36px 40px",
  width: "100%", maxWidth: "540px",
  boxShadow: "0 32px 80px rgba(0,0,0,0.6)",
  animation: "heFadeIn 0.3s ease",
};

const header = {
  display: "flex", alignItems: "center", gap: "12px",
  marginBottom: "28px",
};

const logoIcon = {
  width: "36px", height: "36px", flexShrink: 0,
  background: `linear-gradient(135deg, ${GREEN_DIM}, ${GREEN})`,
  borderRadius: "8px",
  display: "flex", alignItems: "center", justifyContent: "center",
  fontSize: "11px", fontWeight: "800", color: "#fff",
};

const logoTitle = { fontSize: "17px", fontWeight: "700", color: TEXT, lineHeight: "1.2" };
const logoSub   = { fontSize: "10px", color: GREEN, letterSpacing: "1.8px", textTransform: "uppercase" };

const closeBtn = {
  marginLeft: "auto", background: "transparent",
  border: "1px solid rgba(255,255,255,0.1)", borderRadius: "6px",
  color: MUTED, padding: "5px 10px", fontSize: "14px",
  cursor: "pointer", lineHeight: 1,
};

const heroSection = {
  display: "flex", alignItems: "flex-start", gap: "16px",
  marginBottom: "28px",
};

const heroEmoji = { fontSize: "32px", lineHeight: 1, flexShrink: 0, marginTop: "2px" };

const heroTitle = {
  margin: "0 0 6px", fontSize: "20px", fontWeight: "400", color: TEXT,
  fontFamily: "'Georgia','Times New Roman',serif",
};

const heroDesc = {
  margin: 0, fontSize: "13px", color: MUTED, lineHeight: "1.65",
  fontFamily: "'Georgia','Times New Roman',serif",
};

const fieldLabel = {
  display: "block", fontSize: "11px", letterSpacing: "1.2px",
  textTransform: "uppercase", color: "#7a9fc4", marginBottom: "7px",
  fontFamily: "'Georgia','Times New Roman',serif",
};

const progressBox = {
  display: "flex", alignItems: "center", gap: "12px",
  background: "rgba(82,183,136,0.08)", border: "1px solid rgba(82,183,136,0.22)",
  borderRadius: "8px", padding: "13px 16px", marginBottom: "18px",
};

const errorBox = {
  background: "rgba(220,38,38,0.08)", border: "1px solid rgba(220,38,38,0.25)",
  borderRadius: "8px", padding: "12px 16px", marginBottom: "18px",
  color: "#fca5a5", fontSize: "13px",
  fontFamily: "'Georgia','Times New Roman',serif",
};

const btnRow = { display: "flex", gap: "12px", marginBottom: "16px" };

const btnPrimary = {
  flex: 1, padding: "13px 20px", borderRadius: "8px", border: "none",
  background: `linear-gradient(135deg, ${GREEN_DIM}, ${GREEN})`,
  color: "#fff", fontSize: "14px", fontWeight: "600",
  cursor: "pointer", fontFamily: "'Georgia','Times New Roman',serif",
};

const btnSecondary = {
  padding: "13px 20px", borderRadius: "8px",
  background: "transparent", border: `1px solid ${BORDER}`,
  color: MUTED, fontSize: "13px", cursor: "pointer",
  fontFamily: "'Georgia','Times New Roman',serif",
  whiteSpace: "nowrap",
};

const btnDisabled = {
  opacity: 0.45, cursor: "not-allowed",
  background: "rgba(82,183,136,0.25)",
};

const footerNote = {
  margin: 0, textAlign: "center", fontSize: "11px",
  color: MUTED2, fontStyle: "italic", lineHeight: "1.5",
  fontFamily: "'Georgia','Times New Roman',serif",
};
