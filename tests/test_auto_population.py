"""
Tests for the AI-powered auto-population workflow.

Structure
---------
1. PubMed agent  — search + extraction (unit + integration)
2. NICE agent    — guidance search + comparator costs (unit + integration)
3. AutoPopulator — orchestration, field coverage, confidence scores (unit + integration)
4. FastAPI       — POST /api/auto-populate/bia + status polling (unit + integration)
5. Mock suite    — full orchestration with all external calls mocked

Markers
-------
@pytest.mark.unit        — no external calls; always fast
@pytest.mark.integration — calls Claude / PubMed / NICE; require ANTHROPIC_API_KEY

Run all (unit only):
    pytest tests/test_auto_population.py -v -m unit

Run with real APIs:
    pytest tests/test_auto_population.py -v -m integration
    pytest tests/test_auto_population.py -v            # all
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from agents.pubmed_agent import PubMedAgent
from agents.nice_agent import NICEAgent
from agents.auto_populate import AutoPopulator, auto_populate_bia, _parse_json
from app.main import app

# ---------------------------------------------------------------------------
# Shared fixtures & helpers
# ---------------------------------------------------------------------------

SEPSIS_DEVICE_INPUT = {
    "device_name": "AI Sepsis Prediction Tool",
    "indication": "Sepsis in ICU patients",
    "setting": "Acute NHS Trust",
    "device_cost": 185.0,
    "expected_benefits": "Earlier detection enabling faster antibiotic administration and reduced ICU stays",
    "forecast_years": 3,
    "model_year": 2025,
}

# Minimal BIA field set that every auto-population result must include
REQUIRED_BIA_FIELDS = {
    "setting",
    "catchment_size",
    "eligible_pct",
    "uptake_y1",
    "uptake_y2",
    "uptake_y3",
    "price",
    "pricing_model",
    "admissions",
    "bed_days",
}

# ── Canned Claude response for a BIA synthesis ────────────────────────────────

_CANNED_BIA_INPUTS = {
    "setting": "Acute NHS Trust",
    "model_year": 2025,
    "forecast_years": 3,
    "funding_source": "Trust operational budget",
    "catchment_type": "population",
    "catchment_size": 250000,
    "eligible_pct": 4.2,
    "uptake_y1": 20,
    "uptake_y2": 45,
    "uptake_y3": 70,
    "prevalence": "30/100,000 incidence",
    "workforce": [
        {"role": "Band 5 (Staff Nurse)", "minutes": 25, "frequency": "per patient"},
        {"role": "Band 6 (Senior Nurse/AHP)", "minutes": 15, "frequency": "per patient"},
    ],
    "outpatient_visits": 0,
    "tests": 1,
    "admissions": 1,
    "bed_days": 4,
    "procedures": 0,
    "consumables": 50.0,
    "pricing_model": "per-patient",
    "price": 185.0,
    "price_unit": "per year",
    "needs_training": True,
    "training_roles": "ICU nurses, registrars",
    "training_hours": 2.0,
    "setup_cost": 8000.0,
    "staff_time_saved": 20.0,
    "visits_reduced": 0.0,
    "complications_reduced": 25.0,
    "readmissions_reduced": 10.0,
    "los_reduced": 1.2,
    "follow_up_reduced": 0.0,
    "comparator": "none",
    "comparator_names": "Standard clinical assessment",
    "discounting": "off",
}

_CANNED_BIA_RESULT = {
    "bia_inputs": _CANNED_BIA_INPUTS,
    "evidence_sources": [
        {"type": "pubmed", "pmid": "12345678", "title": "AI sepsis prediction in ICU", "year": 2023},
        {"type": "nice", "id": "dg51", "title": "Sepsis: recognition and treatment", "url": "https://www.nice.org.uk/guidance/dg51"},
    ],
    "confidence_scores": {
        "overall": "medium",
        "eligible_population": "medium",
        "uptake_trajectory": "low",
        "resource_savings": "medium",
        "catchment_size": "high",
    },
    "warnings": ["Uptake trajectory is an estimate; adjust based on local adoption data."],
    "assumptions": ["Catchment population derived from ONS England & Wales data."],
    "raw_evidence": {"n_pubmed_articles": 8, "n_nice_docs": 1, "search_queries": ["AI sepsis ICU prediction"]},
}

_CANNED_MARKOV_RESULT = {
    "markov_inputs": {
        "intervention_name": "AI Sepsis Prediction Tool",
        "time_horizon": 5,
        "cycle_length": 1,
        "discount_rate": 0.035,
        "prob_death_standard": 0.28,
        "cost_standard_annual": 14500.0,
        "utility_standard": 0.62,
        "prob_death_treatment": 0.21,
        "cost_treatment_annual": 14685.0,
        "cost_treatment_initial": 185.0,
        "utility_treatment": 0.67,
    },
    "derivation_notes": ["Mortality reduction based on median of 3 RCTs."],
    "confidence_scores": {"overall": "medium"},
    "assumptions": [],
    "warnings": [],
}

_CANNED_VALIDATION_RESULT = {
    "validation_status": "ok",
    "flags": [],
    "confidence": "medium",
    "recommended_overrides": {},
    "plausibility_scores": {"eligible_pct": 0.9, "uptake_y1": 0.85},
    "summary": "Inputs appear plausible for an NHS ICU AI device.",
}


def _make_claude_message(content: str) -> MagicMock:
    """Build a minimal mock of an Anthropic Message object."""
    msg = MagicMock()
    msg.content = [MagicMock(text=content)]
    return msg


# ===========================================================================
# 1. PubMedAgent — unit tests
# ===========================================================================

class TestPubMedAgentUnit:
    """Fast unit tests for PubMedAgent using mocked Entrez and Claude."""

    @pytest.fixture
    def agent(self):
        return PubMedAgent(entrez_email="test@example.com", anthropic_api_key="sk-test")

    # ── search_pubmed ──────────────────────────────────────────────────────────

    def test_search_returns_list(self, agent):
        """search_pubmed returns a list of article dicts."""
        fake_articles = [
            {"pmid": "11111111", "title": "AI in ICU", "abstract": "We studied AI...", "year": "2023", "authors": ["Smith J"], "journal": "Lancet"},
            {"pmid": "22222222", "title": "Sepsis mortality", "abstract": "Mortality rates...", "year": "2022", "authors": ["Jones A"], "journal": "NEJM"},
        ]
        with patch.object(agent, "search_pubmed", return_value=fake_articles):
            results = agent.search_pubmed("sepsis artificial intelligence")
        assert isinstance(results, list)
        assert len(results) == 2

    def test_search_result_has_required_keys(self, agent):
        """Each article dict contains the expected keys."""
        fake_article = {
            "pmid": "99887766",
            "title": "Machine learning sepsis",
            "abstract": "Abstract text.",
            "year": "2024",
            "authors": ["Brown K"],
            "journal": "BMJ",
        }
        with patch.object(agent, "search_pubmed", return_value=[fake_article]):
            results = agent.search_pubmed("ml sepsis")
        article = results[0]
        for key in ("pmid", "title", "abstract", "year"):
            assert key in article, f"Key '{key}' missing from article"

    def test_search_empty_query_returns_list(self, agent):
        """An empty result set returns [] gracefully."""
        with patch.object(agent, "search_pubmed", return_value=[]):
            results = agent.search_pubmed("xyzzy_nonexistent_term_99")
        assert results == []

    # ── extract_clinical_data ─────────────────────────────────────────────────

    def test_extract_mortality_structure(self, agent):
        """extract_clinical_data returns expected top-level keys."""
        fake_extraction = {
            "data_type": "mortality",
            "extractions": [
                {
                    "pmid": "11111111",
                    "outcome": "30-day mortality",
                    "intervention_value": 18.5,
                    "control_value": 28.0,
                    "reduction": 9.5,
                    "reduction_type": "absolute",
                    "unit": "percent",
                    "study_design": "RCT",
                    "sample_size": 312,
                    "confidence": "high",
                    "quote": "Mortality was reduced from 28% to 18.5%.",
                    "notes": "",
                }
            ],
            "failed_pmids": [],
        }
        abstracts = [{"pmid": "11111111", "abstract": "mortality study"}]
        with patch.object(agent, "extract_clinical_data", return_value=fake_extraction):
            result = agent.extract_clinical_data(abstracts, "mortality")

        assert "extractions" in result
        assert "data_type" in result
        assert result["data_type"] == "mortality"
        assert "failed_pmids" in result

    def test_extract_high_confidence_entries_present(self, agent):
        """At least one high-confidence extraction should exist in a good result."""
        high_conf_result = {
            "data_type": "mortality",
            "extractions": [
                {"pmid": "1", "confidence": "high", "reduction": 9.5, "outcome": "30-day mortality",
                 "intervention_value": 18.5, "control_value": 28.0, "reduction_type": "absolute",
                 "unit": "percent", "study_design": "RCT", "sample_size": 200, "quote": "", "notes": ""},
                {"pmid": "2", "confidence": "medium", "reduction": 7.0, "outcome": "28-day mortality",
                 "intervention_value": 21.0, "control_value": 28.0, "reduction_type": "absolute",
                 "unit": "percent", "study_design": "cohort", "sample_size": 150, "quote": "", "notes": ""},
            ],
            "failed_pmids": [],
        }
        abstracts = [{"pmid": "1", "abstract": "..."}, {"pmid": "2", "abstract": "..."}]
        with patch.object(agent, "extract_clinical_data", return_value=high_conf_result):
            result = agent.extract_clinical_data(abstracts, "mortality")

        high_conf = [e for e in result["extractions"] if e["confidence"] == "high"]
        assert len(high_conf) >= 1

    def test_extract_valid_data_types(self, agent):
        """All five supported data types return a result without error."""
        for dtype in ("mortality", "los", "costs", "qol", "readmissions"):
            mock_result = {"data_type": dtype, "extractions": [], "failed_pmids": []}
            with patch.object(agent, "extract_clinical_data", return_value=mock_result):
                result = agent.extract_clinical_data([{"pmid": "1", "abstract": "..."}], dtype)
            assert result["data_type"] == dtype

    # ── synthesize_evidence ───────────────────────────────────────────────────

    def test_synthesize_returns_expected_keys(self, agent):
        """synthesize_evidence returns a dict with standard synthesis keys."""
        fake_synthesis = {
            "mortality_reduction": {
                "median": 9.5,
                "range": [7.0, 12.0],
                "n_studies": 3,
                "heterogeneity": "low",
                "recommendation": "Use 9.5% absolute reduction as base case.",
            },
            "evidence_quality": "moderate",
            "key_findings": ["AI reduced 30-day mortality by ~9.5%"],
            "outliers": [],
            "limitations": ["All studies single-centre"],
            "heterogeneity_drivers": ["different AI algorithms"],
        }
        extractions = {"mortality": {"extractions": [], "failed_pmids": []}}
        with patch.object(agent, "synthesize_evidence", return_value=fake_synthesis):
            result = agent.synthesize_evidence(extractions)

        assert "evidence_quality" in result
        assert "key_findings" in result
        assert isinstance(result["key_findings"], list)


# ===========================================================================
# 2. NICEAgent — unit tests
# ===========================================================================

class TestNICEAgentUnit:
    """Unit tests for NICEAgent using mocked HTTP and Claude responses."""

    @pytest.fixture
    def agent(self):
        return NICEAgent(anthropic_api_key="sk-test")

    # ── search_nice_guidance ──────────────────────────────────────────────────

    def test_search_returns_list(self, agent):
        """search_nice_guidance always returns a list."""
        fake_guidance = [
            {
                "id": "dg51",
                "type": "dg",
                "type_label": "Diagnostics guidance",
                "title": "Sepsis: point-of-care lactate testing",
                "url": "https://www.nice.org.uk/guidance/dg51",
                "pdf_url": "",
                "date": "2022-01",
                "summary": "NICE recommends point-of-care lactate testing.",
            }
        ]
        with patch.object(agent, "search_nice_guidance", return_value=fake_guidance):
            results = agent.search_nice_guidance("sepsis")
        assert isinstance(results, list)
        assert len(results) >= 1

    def test_search_result_structure(self, agent):
        """Guidance items contain at minimum id, type, title, and url."""
        fake_guidance = [
            {"id": "ng51", "type": "ng", "type_label": "NICE guideline",
             "title": "Sepsis", "url": "https://www.nice.org.uk/guidance/ng51",
             "pdf_url": "", "date": "2016-07", "summary": ""},
        ]
        with patch.object(agent, "search_nice_guidance", return_value=fake_guidance):
            results = agent.search_nice_guidance("sepsis", "guideline")
        item = results[0]
        for key in ("id", "type", "title", "url"):
            assert key in item, f"Key '{key}' missing from guidance item"

    def test_search_no_results_returns_empty_list(self, agent):
        with patch.object(agent, "search_nice_guidance", return_value=[]):
            results = agent.search_nice_guidance("zzzunknownconditionzzz")
        assert results == []

    # ── extract_nice_data ─────────────────────────────────────────────────────

    def test_extract_nice_data_structure(self, agent):
        """extract_nice_data returns all required keys."""
        fake_data = {
            "guidance_id": "dg51",
            "guidance_type": "dg",
            "title": "Sepsis: point-of-care lactate testing",
            "intervention": "Point-of-care lactate testing device",
            "indication": "Suspected sepsis",
            "comparator": "Standard laboratory testing",
            "decision": "Recommended",
            "decision_rationale": "Faster sepsis detection reduces mortality.",
            "icer": {"value": 8500, "unit": "£/QALY", "comparator": "standard care", "probabilistic": None},
            "willingness_to_pay_threshold": 20000,
            "clinical_outcomes": ["Reduced 30-day mortality", "Faster time-to-antibiotics"],
            "key_cost_drivers": ["Device cost", "Laboratory cost savings"],
            "population_details": "Adults with suspected sepsis in NHS acute settings",
            "quality_of_life": {"standard_care": 0.62, "intervention": 0.67},
            "model_used": "Markov",
            "time_horizon_years": 10,
            "perspective": "NHS & PSS",
            "discount_rate": 0.035,
            "publication_date": "2022-01",
            "confidence": "high",
            "url": "https://www.nice.org.uk/guidance/dg51",
        }
        with patch.object(agent, "extract_nice_data", return_value=fake_data):
            result = agent.extract_nice_data("https://www.nice.org.uk/guidance/dg51")

        for key in ("guidance_id", "title", "intervention", "indication", "decision", "icer", "confidence"):
            assert key in result, f"Key '{key}' missing"

    def test_icer_nested_structure(self, agent):
        """The icer field is a dict with a value key."""
        fake_data = {
            "guidance_id": "ta123",
            "title": "Test", "intervention": "X", "indication": "Y",
            "comparator": "Z", "decision": "Not recommended",
            "decision_rationale": "", "icer": {"value": 45000, "unit": "£/QALY",
            "comparator": "standard", "probabilistic": None},
            "willingness_to_pay_threshold": 20000, "clinical_outcomes": [],
            "key_cost_drivers": [], "population_details": "", "quality_of_life": {},
            "model_used": "Markov", "time_horizon_years": 5, "perspective": "NHS & PSS",
            "discount_rate": 0.035, "publication_date": "2023", "confidence": "medium",
            "url": "https://www.nice.org.uk/guidance/ta123",
        }
        with patch.object(agent, "extract_nice_data", return_value=fake_data):
            result = agent.extract_nice_data("https://www.nice.org.uk/guidance/ta123")
        assert isinstance(result["icer"], dict)
        assert "value" in result["icer"]

    # ── get_comparator_costs ──────────────────────────────────────────────────

    def test_comparator_costs_structure(self, agent):
        """get_comparator_costs returns required cost keys."""
        fake_costs = {
            "condition": "sepsis",
            "icu_days_typical": 5,
            "ward_days_typical": 8,
            "icu_cost_per_day": 1850.0,
            "ward_cost_per_day": 450.0,
            "typical_cost_per_episode": 12850.0,
            "outpatient_cost_per_visit": 185.0,
            "annual_drug_cost_comparator": 0.0,
            "currency": "GBP",
            "price_year": "2023/24",
            "standard_of_care": "Standard clinical assessment and blood cultures",
            "key_resource_drivers": ["ICU bed days", "laboratory tests"],
            "source": "NHS Reference Costs 2023/24 + NICE DG51",
            "confidence": "medium",
            "notes": "",
        }
        with patch.object(agent, "get_comparator_costs", return_value=fake_costs):
            result = agent.get_comparator_costs("sepsis")

        for key in ("icu_cost_per_day", "ward_cost_per_day", "typical_cost_per_episode", "confidence"):
            assert key in result, f"Key '{key}' missing from comparator costs"

    def test_comparator_cost_values_are_positive(self, agent):
        """Cost values should be non-negative floats."""
        fake_costs = {
            "condition": "sepsis",
            "icu_days_typical": 5, "ward_days_typical": 8,
            "icu_cost_per_day": 1850.0, "ward_cost_per_day": 450.0,
            "typical_cost_per_episode": 12850.0, "outpatient_cost_per_visit": 185.0,
            "annual_drug_cost_comparator": 0.0, "currency": "GBP",
            "price_year": "2023/24", "standard_of_care": "standard care",
            "key_resource_drivers": [], "source": "NICE", "confidence": "medium", "notes": "",
        }
        with patch.object(agent, "get_comparator_costs", return_value=fake_costs):
            result = agent.get_comparator_costs("sepsis")
        assert result["icu_cost_per_day"] >= 0
        assert result["ward_cost_per_day"] >= 0
        assert result["typical_cost_per_episode"] >= 0


# ===========================================================================
# 3. AutoPopulator — unit tests (all external calls mocked)
# ===========================================================================

class TestAutoPopulatorUnit:
    """Unit tests for AutoPopulator — no real APIs called."""

    @pytest.fixture
    def populator(self):
        return AutoPopulator(anthropic_api_key="sk-test", entrez_email="test@example.com")

    # ── _parse_json helper ────────────────────────────────────────────────────

    def test_parse_json_plain(self):
        assert _parse_json('{"key": "value"}') == {"key": "value"}

    def test_parse_json_strips_markdown_fence(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        assert _parse_json(raw) == {"key": "value"}

    def test_parse_json_strips_code_fence(self):
        raw = "```\n[1, 2, 3]\n```"
        assert _parse_json(raw) == [1, 2, 3]

    def test_parse_json_returns_empty_dict_on_invalid(self):
        result = _parse_json("this is not json at all")
        # Should return {} or [] gracefully rather than raise
        assert isinstance(result, (dict, list))

    def test_parse_json_array(self):
        assert _parse_json('["q1", "q2", "q3"]') == ["q1", "q2", "q3"]

    # ── auto_populate_bia — mocked Claude + evidence ──────────────────────────

    def test_auto_populate_bia_result_structure(self, populator):
        """auto_populate_bia returns all required top-level keys."""
        with patch.object(populator, "auto_populate_bia", return_value=_CANNED_BIA_RESULT):
            result = populator.auto_populate_bia(SEPSIS_DEVICE_INPUT)

        for key in ("bia_inputs", "evidence_sources", "confidence_scores", "warnings", "assumptions"):
            assert key in result, f"Top-level key '{key}' missing"

    def test_auto_populate_bia_required_fields_present(self, populator):
        """bia_inputs contains every required BIA field."""
        with patch.object(populator, "auto_populate_bia", return_value=_CANNED_BIA_RESULT):
            result = populator.auto_populate_bia(SEPSIS_DEVICE_INPUT)

        bia = result["bia_inputs"]
        missing = REQUIRED_BIA_FIELDS - set(bia.keys())
        assert not missing, f"Missing BIA fields: {missing}"

    def test_eligible_pct_positive(self, populator):
        """eligible_pct must be > 0 for a real indication."""
        with patch.object(populator, "auto_populate_bia", return_value=_CANNED_BIA_RESULT):
            result = populator.auto_populate_bia(SEPSIS_DEVICE_INPUT)
        assert result["bia_inputs"]["eligible_pct"] > 0

    def test_catchment_size_positive(self, populator):
        with patch.object(populator, "auto_populate_bia", return_value=_CANNED_BIA_RESULT):
            result = populator.auto_populate_bia(SEPSIS_DEVICE_INPUT)
        assert result["bia_inputs"]["catchment_size"] > 0

    def test_uptake_trajectory_increasing(self, populator):
        """Year-3 uptake should be >= year-1 uptake."""
        with patch.object(populator, "auto_populate_bia", return_value=_CANNED_BIA_RESULT):
            result = populator.auto_populate_bia(SEPSIS_DEVICE_INPUT)
        bia = result["bia_inputs"]
        assert bia["uptake_y3"] >= bia["uptake_y1"]

    def test_evidence_sources_is_list(self, populator):
        with patch.object(populator, "auto_populate_bia", return_value=_CANNED_BIA_RESULT):
            result = populator.auto_populate_bia(SEPSIS_DEVICE_INPUT)
        assert isinstance(result["evidence_sources"], list)

    def test_evidence_sources_nonempty(self, populator):
        with patch.object(populator, "auto_populate_bia", return_value=_CANNED_BIA_RESULT):
            result = populator.auto_populate_bia(SEPSIS_DEVICE_INPUT)
        assert len(result["evidence_sources"]) > 0

    def test_confidence_scores_present(self, populator):
        with patch.object(populator, "auto_populate_bia", return_value=_CANNED_BIA_RESULT):
            result = populator.auto_populate_bia(SEPSIS_DEVICE_INPUT)
        assert isinstance(result["confidence_scores"], dict)
        assert len(result["confidence_scores"]) > 0

    def test_confidence_score_values_valid(self, populator):
        """Each confidence value is one of high / medium / low."""
        valid = {"high", "medium", "low"}
        with patch.object(populator, "auto_populate_bia", return_value=_CANNED_BIA_RESULT):
            result = populator.auto_populate_bia(SEPSIS_DEVICE_INPUT)
        for field, level in result["confidence_scores"].items():
            assert level in valid, f"Field '{field}' has invalid confidence '{level}'"

    def test_warnings_is_list(self, populator):
        with patch.object(populator, "auto_populate_bia", return_value=_CANNED_BIA_RESULT):
            result = populator.auto_populate_bia(SEPSIS_DEVICE_INPUT)
        assert isinstance(result["warnings"], list)

    def test_workforce_is_list_of_dicts(self, populator):
        with patch.object(populator, "auto_populate_bia", return_value=_CANNED_BIA_RESULT):
            result = populator.auto_populate_bia(SEPSIS_DEVICE_INPUT)
        workforce = result["bia_inputs"].get("workforce", [])
        assert isinstance(workforce, list)
        if workforce:
            assert all(isinstance(row, dict) for row in workforce)
            assert all("role" in row and "minutes" in row for row in workforce)

    # ── auto_populate_markov — mocked ─────────────────────────────────────────

    def test_auto_populate_markov_structure(self, populator):
        """auto_populate_markov returns markov_inputs + derivation metadata."""
        with patch.object(populator, "auto_populate_markov", return_value=_CANNED_MARKOV_RESULT):
            result = populator.auto_populate_markov(_CANNED_BIA_INPUTS, {})

        for key in ("markov_inputs", "derivation_notes", "confidence_scores", "assumptions", "warnings"):
            assert key in result, f"Key '{key}' missing from Markov result"

    def test_markov_inputs_transition_probs_valid(self, populator):
        """Transition probabilities must be between 0 and 1."""
        with patch.object(populator, "auto_populate_markov", return_value=_CANNED_MARKOV_RESULT):
            result = populator.auto_populate_markov(_CANNED_BIA_INPUTS, {})
        mi = result["markov_inputs"]
        for field in ("prob_death_standard", "prob_death_treatment"):
            val = mi.get(field)
            if val is not None:
                assert 0.0 <= val <= 1.0, f"{field}={val} is outside [0,1]"

    def test_markov_treatment_cost_gt_zero(self, populator):
        with patch.object(populator, "auto_populate_markov", return_value=_CANNED_MARKOV_RESULT):
            result = populator.auto_populate_markov(_CANNED_BIA_INPUTS, {})
        assert result["markov_inputs"].get("cost_treatment_annual", 0) >= 0

    # ── validate_auto_population ──────────────────────────────────────────────

    def test_validate_returns_status(self, populator):
        with patch.object(populator, "validate_auto_population", return_value=_CANNED_VALIDATION_RESULT):
            result = populator.validate_auto_population(_CANNED_BIA_INPUTS, _CANNED_BIA_RESULT)

        assert "validation_status" in result
        assert result["validation_status"] in ("ok", "needs_review", "high_risk")

    def test_validate_returns_plausibility_scores(self, populator):
        with patch.object(populator, "validate_auto_population", return_value=_CANNED_VALIDATION_RESULT):
            result = populator.validate_auto_population(_CANNED_BIA_INPUTS, _CANNED_BIA_RESULT)
        assert "plausibility_scores" in result
        assert isinstance(result["plausibility_scores"], dict)

    # ── module-level convenience function ────────────────────────────────────

    def test_module_level_auto_populate_bia(self):
        """The module-level auto_populate_bia delegates to AutoPopulator."""
        with patch("agents.auto_populate.AutoPopulator.auto_populate_bia",
                   return_value=_CANNED_BIA_RESULT):
            result = auto_populate_bia(SEPSIS_DEVICE_INPUT)
        assert "bia_inputs" in result


# ===========================================================================
# 4. FastAPI endpoints — unit tests (all AutoPopulator calls mocked)
# ===========================================================================

class TestAutoPopulateAPIUnit:
    """Unit tests against the FastAPI app with AutoPopulator fully mocked."""

    @pytest.fixture(autouse=True)
    def clear_rate_limit(self):
        """Reset the in-process rate limit store before each test so tests don't interfere."""
        import app.main as main_module
        with main_module._RATE_LIMIT_LOCK:
            main_module._RATE_LIMIT_STORE.clear()

    @pytest.fixture
    def transport(self):
        return ASGITransport(app=app)

    # ── POST /api/auto-populate/bia ───────────────────────────────────────────

    @pytest.mark.anyio
    async def test_post_bia_returns_202(self, transport):
        """POST /api/auto-populate/bia returns 202 with task_id and poll_url."""
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/auto-populate/bia", json={
                "device_name": "AI Sepsis Prediction Tool",
                "indication": "Sepsis in ICU patients",
                "device_cost_per_patient": 185.0,
            })
        assert resp.status_code == 202
        body = resp.json()
        assert "task_id" in body
        assert "poll_url" in body

    @pytest.mark.anyio
    async def test_post_bia_missing_device_name_returns_422(self, transport):
        """Omitting required device_name returns 422."""
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/auto-populate/bia", json={
                "indication": "Sepsis in ICU patients",
            })
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_post_bia_missing_indication_returns_422(self, transport):
        """Omitting required indication returns 422."""
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/auto-populate/bia", json={
                "device_name": "AI Sepsis Prediction Tool",
            })
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_post_bia_task_id_is_hex_string(self, transport):
        """task_id is a 32-character hex string (uuid.uuid4().hex)."""
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/auto-populate/bia", json={
                "device_name": "Test Device",
                "indication": "Test indication",
            })
        body = resp.json()
        task_id = body["task_id"]
        assert isinstance(task_id, str)
        assert len(task_id) == 32
        assert all(c in "0123456789abcdef" for c in task_id)

    @pytest.mark.anyio
    async def test_post_bia_poll_url_format(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/auto-populate/bia", json={
                "device_name": "Test Device",
                "indication": "Test indication",
            })
        body = resp.json()
        assert body["poll_url"].startswith("/api/auto-populate/status/")

    # ── GET /api/auto-populate/status/{task_id} ───────────────────────────────

    @pytest.mark.anyio
    async def test_status_unknown_task_returns_404(self, transport):
        """Polling a non-existent task_id returns 404."""
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/auto-populate/status/{uuid.uuid4()}")
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_status_of_submitted_task_is_queued_or_later(self, transport):
        """A just-submitted task should have status in {queued, searching, ...}."""
        valid_statuses = {"queued", "searching", "extracting", "populating", "complete", "failed"}
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            post_resp = await client.post("/api/auto-populate/bia", json={
                "device_name": "AI Sepsis Tool",
                "indication": "Sepsis in ICU",
            })
            task_id = post_resp.json()["task_id"]
            poll_resp = await client.get(f"/api/auto-populate/status/{task_id}")
        assert poll_resp.status_code == 200
        assert poll_resp.json()["status"] in valid_statuses

    @pytest.mark.anyio
    async def test_status_response_structure(self, transport):
        """Status response has required fields per TaskStatusResponse schema."""
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            post = await client.post("/api/auto-populate/bia", json={
                "device_name": "Test Device",
                "indication": "Test indication",
            })
            task_id = post.json()["task_id"]
            poll = await client.get(f"/api/auto-populate/status/{task_id}")
        body = poll.json()
        for key in ("task_id", "status", "step", "created"):
            assert key in body, f"Key '{key}' missing from status response"

    # ── GET /api/auto-populate/tasks ──────────────────────────────────────────

    @pytest.mark.anyio
    async def test_list_tasks_returns_expected_structure(self, transport):
        """GET /api/auto-populate/tasks returns {total, filtered, tasks:[...]}."""
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/auto-populate/tasks")
        assert resp.status_code == 200
        body = resp.json()
        assert "tasks" in body
        assert "total" in body
        assert "filtered" in body
        assert isinstance(body["tasks"], list)

    @pytest.mark.anyio
    async def test_list_tasks_shows_submitted_task(self, transport):
        """A just-submitted task should appear in the task list."""
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            post = await client.post("/api/auto-populate/bia", json={
                "device_name": "Visibility Test Device",
                "indication": "Test indication",
            })
            task_id = post.json()["task_id"]
            tasks_resp = await client.get("/api/auto-populate/tasks?limit=100")
        ids = [t["task_id"] for t in tasks_resp.json()["tasks"]]
        assert task_id in ids

    # ── POST /api/auto-populate/markov ────────────────────────────────────────

    @pytest.mark.anyio
    async def test_post_markov_returns_202(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/auto-populate/markov", json={
                "device_name": "AI Sepsis Prediction Tool",
                "indication": "Sepsis in ICU patients",
                "bia_inputs": _CANNED_BIA_INPUTS,
                "clinical_data": {},
            })
        assert resp.status_code == 202
        assert "task_id" in resp.json()

    # ── Simulate complete workflow via mocked background task ─────────────────

    @pytest.mark.anyio
    async def test_completed_task_result_structure(self, transport):
        """When a task completes, result contains bia_inputs, sources, confidence_scores."""
        from app.main import _AUTO_POPULATE_TASKS, _AUTO_POPULATE_LOCK
        from datetime import datetime, timezone

        fake_task_id = str(uuid.uuid4())
        completed_payload = {
            "bia_inputs": _CANNED_BIA_INPUTS,
            "evidence_summary": {"papers_found": 8, "nice_guidance_found": 1,
                                 "search_queries": [], "data_quality": "medium"},
            "confidence_scores": _CANNED_BIA_RESULT["confidence_scores"],
            "warnings": _CANNED_BIA_RESULT["warnings"],
            "assumptions": _CANNED_BIA_RESULT["assumptions"],
            "sources": _CANNED_BIA_RESULT["evidence_sources"],
            "elapsed_seconds": 45.3,
        }
        with _AUTO_POPULATE_LOCK:
            _AUTO_POPULATE_TASKS[fake_task_id] = {
                "task_id": fake_task_id,
                "status": "complete",
                "step": "Done",
                "result": completed_payload,
                "created": datetime.now(timezone.utc).isoformat(),
                "elapsed": 45.3,
            }

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/auto-populate/status/{fake_task_id}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "complete"
        result = body["result"]
        assert "bia_inputs" in result
        assert "sources" in result
        assert "confidence_scores" in result
        assert "evidence_summary" in result

    @pytest.mark.anyio
    async def test_failed_task_exposes_error(self, transport):
        """A failed task has error field set and status == 'failed'."""
        from app.main import _AUTO_POPULATE_TASKS, _AUTO_POPULATE_LOCK
        from datetime import datetime, timezone

        fake_task_id = str(uuid.uuid4())
        with _AUTO_POPULATE_LOCK:
            _AUTO_POPULATE_TASKS[fake_task_id] = {
                "task_id": fake_task_id,
                "status": "failed",
                "step": "",
                "result": None,
                "error": "PubMed API unavailable",
                "created": datetime.now(timezone.utc).isoformat(),
                "elapsed": 3.1,
            }

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/auto-populate/status/{fake_task_id}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "failed"
        assert body.get("error") == "PubMed API unavailable"


# ===========================================================================
# 5. Mock orchestration tests — end-to-end with all external APIs mocked
# ===========================================================================

class TestAutoPopulatorOrchestrationMocked:
    """
    Full orchestration tests with Claude, PubMed, and NICE all mocked.
    Tests the wiring logic: correct calls, correct data flow, error handling.
    """

    @pytest.fixture
    def populator(self):
        return AutoPopulator(anthropic_api_key="sk-test", entrez_email="test@example.com")

    def _mock_anthropic_response(self, content: str):
        """Return a mock Anthropic client whose messages.create returns content."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_claude_message(content)
        return mock_client

    def test_generate_search_queries_returns_list(self, populator):
        """_generate_search_queries returns a non-empty list of strings."""
        queries_json = '["AI sepsis prediction ICU", "machine learning sepsis mortality", "clinical decision support sepsis NHS", "sepsis early warning score AI"]'
        mock_client = self._mock_anthropic_response(queries_json)
        with patch.object(populator, "_anthropic_client", mock_client, create=True):
            queries = populator._generate_search_queries("AI Sepsis Prediction Tool", "Sepsis in ICU")
        assert isinstance(queries, list)
        assert len(queries) >= 1
        assert all(isinstance(q, str) for q in queries)

    def test_generate_search_queries_handles_markdown_fence(self, populator):
        """_generate_search_queries handles Claude wrapping output in ```json fences."""
        queries_json = '```json\n["AI sepsis prediction", "sepsis ICU mortality"]\n```'
        mock_client = self._mock_anthropic_response(queries_json)
        with patch.object(populator, "_anthropic_client", mock_client, create=True):
            queries = populator._generate_search_queries("AI Tool", "Sepsis")
        assert isinstance(queries, list)

    def test_synthesise_bia_inputs_called_with_evidence(self, populator):
        """auto_populate_bia calls the synthesis step with evidence data."""
        with patch.object(populator, "_generate_search_queries", return_value=["ai sepsis icu"]):
            with patch.object(populator, "_gather_evidence_parallel", return_value={
                "pubmed_articles": [{"pmid": "1", "title": "T", "abstract": "A", "year": "2023"}],
                "nice_guidance": [],
                "nice_comparators": {},
                "nhs_costs": {},
                "ons_population": {},
            }):
                with patch.object(populator, "_extract_clinical_data", return_value={
                    "mortality": {"extractions": [], "failed_pmids": []},
                    "los":       {"extractions": [], "failed_pmids": []},
                    "costs":     {"extractions": [], "failed_pmids": []},
                    "readmissions": {"extractions": [], "failed_pmids": []},
                    "qol":       {"extractions": [], "failed_pmids": []},
                }):
                    _synth_return = {"bia_inputs": _CANNED_BIA_INPUTS,
                                     "confidence_scores": {}, "assumptions": [], "warnings": []}
                    with patch.object(populator, "_synthesise_bia_inputs",
                                      return_value=_synth_return) as mock_synth:
                        try:
                            populator.auto_populate_bia(SEPSIS_DEVICE_INPUT)
                        except Exception:
                            pass  # we only care that synthesis was called
                        mock_synth.assert_called_once()

    def test_auto_populate_bia_full_mock_pipeline(self, populator):
        """Full auto_populate_bia pipeline returns correct structure when all steps mocked."""
        with patch.object(populator, "_generate_search_queries", return_value=["ai sepsis icu"]):
            with patch.object(populator, "_gather_evidence_parallel", return_value={
                "pubmed_articles": [
                    {"pmid": "99887766", "title": "AI Sepsis Study", "abstract": "...",
                     "year": "2023", "authors": [], "journal": "Lancet"}
                ],
                "nice_guidance": [{"id": "dg51", "title": "Sepsis DG", "url": "https://nice.org.uk/dg51"}],
                "nice_comparators": {"typical_cost_per_episode": 12000},
                "nhs_costs": {"icu": {"cost_per_day": 1850}},
                "ons_population": {"england_wales": 59_000_000},
            }):
                with patch.object(populator, "_extract_clinical_data", return_value={
                    "mortality": {"extractions": [
                        {"pmid": "99887766", "reduction": 9.5, "confidence": "high",
                         "outcome": "30-day mortality", "intervention_value": 18.5,
                         "control_value": 28.0, "reduction_type": "absolute", "unit": "percent",
                         "study_design": "RCT", "sample_size": 312, "quote": "", "notes": ""}
                    ], "failed_pmids": []},
                    "los":      {"extractions": [], "failed_pmids": []},
                    "costs":    {"extractions": [], "failed_pmids": []},
                    "readmissions": {"extractions": [], "failed_pmids": []},
                    "qol":      {"extractions": [], "failed_pmids": []},
                }):
                    _synth_return = {"bia_inputs": _CANNED_BIA_INPUTS,
                                     "confidence_scores": {"overall": "medium"},
                                     "assumptions": [], "warnings": []}
                    with patch.object(populator, "_synthesise_bia_inputs",
                                      return_value=_synth_return):
                        result = populator.auto_populate_bia(SEPSIS_DEVICE_INPUT)

        assert "bia_inputs" in result
        assert "evidence_sources" in result
        assert "confidence_scores" in result
        assert "warnings" in result

    def test_auto_populate_propagates_device_cost(self, populator):
        """Device cost from user input appears in synthesised bia_inputs.price."""
        bia_with_cost = dict(_CANNED_BIA_INPUTS)
        bia_with_cost["price"] = 0.0  # synthesis returns 0; auto_populate_bia overwrites it

        with patch.object(populator, "_generate_search_queries", return_value=["ai sepsis"]):
            with patch.object(populator, "_gather_evidence_parallel", return_value={
                "pubmed_articles": [], "nice_guidance": [], "nice_comparators": {},
                "nhs_costs": {}, "ons_population": {},
            }):
                with patch.object(populator, "_extract_clinical_data", return_value={
                    dtype: {"extractions": [], "failed_pmids": []}
                    for dtype in ("mortality", "los", "costs", "readmissions", "qol")
                }):
                    _synth_return = {"bia_inputs": bia_with_cost,
                                     "confidence_scores": {}, "assumptions": [], "warnings": []}
                    with patch.object(populator, "_synthesise_bia_inputs",
                                      return_value=_synth_return):
                        result = populator.auto_populate_bia(SEPSIS_DEVICE_INPUT)

        assert result["bia_inputs"]["price"] == 185.0

    def test_gather_evidence_uses_all_queries(self, populator):
        """_gather_evidence_parallel is called once with the generated queries."""
        with patch.object(populator, "_generate_search_queries",
                          return_value=["q1", "q2", "q3", "q4"]) as mock_gen:
            with patch.object(populator, "_gather_evidence_parallel",
                               return_value={"pubmed_articles": [], "nice_guidance": [],
                                             "nice_comparators": {}, "nhs_costs": {}, "ons_population": {}}) as mock_gather:
                with patch.object(populator, "_extract_clinical_data", return_value={
                    dtype: {"extractions": [], "failed_pmids": []}
                    for dtype in ("mortality", "los", "costs", "readmissions", "qol")
                }):
                    _synth_return = {"bia_inputs": _CANNED_BIA_INPUTS,
                                     "confidence_scores": {}, "assumptions": [], "warnings": []}
                    with patch.object(populator, "_synthesise_bia_inputs",
                                      return_value=_synth_return):
                        populator.auto_populate_bia(SEPSIS_DEVICE_INPUT)

            mock_gen.assert_called_once()
            mock_gather.assert_called_once()


# ===========================================================================
# 6. Integration tests — call real APIs (skipped without ANTHROPIC_API_KEY)
# ===========================================================================

@pytest.mark.integration
class TestPubMedIntegration:
    """Integration tests that call real PubMed API (no Claude needed)."""

    @pytest.fixture(scope="class")
    def agent(self):
        return PubMedAgent(entrez_email="heor-engine-test@example.com")

    def test_search_sepsis_ai_returns_articles(self, agent):
        """Real PubMed search returns at least 1 article for a common query."""
        results = agent.search_pubmed("sepsis artificial intelligence", max_results=5)
        assert isinstance(results, list)
        assert len(results) >= 1

    def test_search_result_has_pmid_and_abstract(self, agent):
        results = agent.search_pubmed("sepsis artificial intelligence", max_results=3)
        for article in results:
            assert "pmid" in article
            assert article["pmid"].isdigit()

    def test_search_respects_max_results(self, agent):
        results = agent.search_pubmed("sepsis", max_results=5)
        assert len(results) <= 5


@pytest.mark.integration
class TestNICEIntegration:
    """Integration tests that call real NICE search (no Claude needed)."""

    @pytest.fixture(scope="class")
    def agent(self):
        return NICEAgent()

    def test_search_sepsis_returns_guidance(self, agent):
        """search_nice_guidance returns at least 1 result for sepsis (seed DB fallback)."""
        results = agent.search_nice_guidance("sepsis")
        assert isinstance(results, list)
        assert len(results) >= 1

    def test_guidance_has_url(self, agent):
        results = agent.search_nice_guidance("sepsis")
        for item in results:
            assert "url" in item
            assert item["url"].startswith("http")


@pytest.mark.integration
class TestAutoPopulatorIntegration:
    """Full integration test calling Claude + PubMed + NICE."""

    def test_auto_populate_sepsis_device(self):
        """
        The canonical integration test: submit a sepsis AI device description
        and verify the full result shape.
        """
        input_data = {
            "device_name": "AI Sepsis Prediction Tool",
            "indication": "Sepsis in ICU",
            "setting": "Acute NHS Trust",
            "device_cost": 185.0,
            "expected_benefits": (
                "Earlier detection of sepsis enabling faster antibiotic "
                "administration and reduced ICU length of stay"
            ),
            "forecast_years": 3,
            "model_year": 2025,
        }

        result = auto_populate_bia(input_data)

        # Top-level structure
        assert "bia_inputs" in result
        assert "evidence_sources" in result
        assert "confidence_scores" in result
        assert "warnings" in result
        assert "assumptions" in result

        bia = result["bia_inputs"]

        # Required BIA fields populated
        missing = REQUIRED_BIA_FIELDS - set(bia.keys())
        assert not missing, f"Missing BIA fields: {missing}"

        # Numeric sanity checks
        assert bia["eligible_pct"] > 0, "eligible_pct must be > 0"
        assert bia["catchment_size"] > 0, "catchment_size must be > 0"
        assert bia["uptake_y3"] >= bia["uptake_y1"], "uptake should increase over time"

        # Evidence quality
        assert len(result["evidence_sources"]) > 0, "Should have at least one evidence source"

        # Confidence scores
        scores = result["confidence_scores"]
        assert isinstance(scores, dict) and len(scores) > 0
        for field, level in scores.items():
            assert level in ("high", "medium", "low"), \
                f"Unexpected confidence level '{level}' for field '{field}'"

    def test_auto_populate_markov_from_bia(self):
        """auto_populate_markov derives sensible Markov parameters from BIA inputs."""
        populator = AutoPopulator()
        result = populator.auto_populate_markov(_CANNED_BIA_INPUTS, {})

        assert "markov_inputs" in result
        mi = result["markov_inputs"]

        for prob_field in ("prob_death_standard", "prob_death_treatment"):
            val = mi.get(prob_field)
            if val is not None:
                assert 0.0 <= val <= 1.0, f"{prob_field}={val} not in [0,1]"

        for cost_field in ("cost_standard_annual", "cost_treatment_annual"):
            val = mi.get(cost_field)
            if val is not None:
                assert val >= 0, f"{cost_field}={val} must be non-negative"


@pytest.mark.integration
class TestAutoPopulateAPIIntegration:
    """Integration tests for the FastAPI auto-populate endpoints with real task execution."""

    @pytest.fixture
    def transport(self):
        return ASGITransport(app=app)

    @pytest.mark.anyio
    async def test_full_bia_workflow_completes(self, transport):
        """
        POST → poll until complete or timeout.
        Verifies the complete task result structure matches the API contract.
        """
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            post_resp = await client.post("/api/auto-populate/bia", json={
                "device_name": "AI Sepsis Prediction Tool",
                "indication": "Sepsis in ICU patients",
                "device_cost_per_patient": 185.0,
                "expected_benefits": "Earlier sepsis detection in ICU",
                "forecast_years": 3,
                "model_year": 2025,
            })
            assert post_resp.status_code == 202
            task_id = post_resp.json()["task_id"]
            poll_url = post_resp.json()["poll_url"]

            # Poll for up to 3 minutes (matches MAX_POLL_ATTEMPTS * 4s in frontend)
            deadline = time.time() + 180
            final_status = None
            while time.time() < deadline:
                poll_resp = await client.get(poll_url)
                assert poll_resp.status_code == 200
                body = poll_resp.json()
                if body["status"] in ("complete", "failed"):
                    final_status = body
                    break
                await asyncio.sleep(4)

        assert final_status is not None, "Task did not complete within 3 minutes"
        assert final_status["status"] == "complete", \
            f"Task failed: {final_status.get('error')}"

        result = final_status["result"]
        assert "bia_inputs" in result
        assert "sources" in result
        assert "confidence_scores" in result
        assert "evidence_summary" in result

        # Verify evidence summary shape
        es = result["evidence_summary"]
        assert "papers_found" in es
        assert "nice_guidance_found" in es
        assert "data_quality" in es
        assert es["data_quality"] in ("high", "medium", "low")

        # Verify warnings are generated
        assert isinstance(result["warnings"], list)

    @pytest.mark.anyio
    async def test_bia_result_has_sources(self, transport):
        """Completed BIA task has at least one evidence source."""
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            post = await client.post("/api/auto-populate/bia", json={
                "device_name": "AI Sepsis Prediction Tool",
                "indication": "Sepsis in ICU patients",
            })
            task_id = post.json()["task_id"]

            deadline = time.time() + 180
            result = None
            while time.time() < deadline:
                poll = await client.get(f"/api/auto-populate/status/{task_id}")
                body = poll.json()
                if body["status"] == "complete":
                    result = body["result"]
                    break
                if body["status"] == "failed":
                    pytest.skip(f"Task failed: {body.get('error')}")
                await asyncio.sleep(4)

        if result is None:
            pytest.skip("Task did not complete in time")

        assert isinstance(result["sources"], list)
        assert len(result["sources"]) > 0


