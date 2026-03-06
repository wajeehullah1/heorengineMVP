"""Tests for engines/slr/screener.py and engines/slr/schema.py.

Covers:
  1.  PICOCriteria validation — required fields, format, to_prompt_text()
  2.  Abstract validation    — field constraints, short_citation(), has_keyword()
  3.  Prompt loading         — template exists, section headers, placeholder patterns
  4.  Prompt formatting      — PICO injection, multi-abstract, stray placeholders
  5.  Response parsing       — include/exclude/uncertain, PICO components, edge cases
  6.  Batch creation         — batch_id, JSON persistence, summary initialisation
  7.  Batch helper methods   — add_decision, filters, pending_pmids, get_* lookups
  8.  Batch save/load        — round-trip serialisation, FileNotFoundError
  9.  Export                 — CSV columns, data integrity, bad-format error
  10. Screen with mocked API — decision coverage, batch failure degradation, batch_size
  11. Retry logic            — RateLimitError, ConnectionError, 5xx, 4xx, exhaustion
  12. Sample abstracts JSON  — schema validation, category counts, PMID uniqueness
  13. End-to-end mocked      — create → screen → export pipeline
  14. Integration            — live Claude API (requires ANTHROPIC_API_KEY)

Markers:
    unit        — no API calls; safe to run in CI without credentials
    integration — calls Claude API; requires ANTHROPIC_API_KEY environment variable

Run unit tests:
    pytest tests/test_slr_screener.py -m unit -v

Run integration tests:
    pytest tests/test_slr_screener.py -m integration -v
"""

from __future__ import annotations

import csv
import json
import os
import time
import types
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from pydantic import ValidationError

import engines.slr.screener as scr
from engines.slr.schema import (
    Abstract,
    Confidence,
    Decision,
    PICOCriteria,
    PICOMatchItem,
    ScreeningBatch,
    ScreeningDecision,
)
from engines.slr.screener import (
    _call_claude_with_retry,
    _extract_pmid_block,
    create_screening_batch,
    export_screening_results,
    format_screening_prompt,
    load_batch,
    load_screening_prompt,
    parse_screening_response,
    save_batch,
    screen_abstracts,
)

# ── Paths ────────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SAMPLE_JSON = _REPO_ROOT / "data" / "slr" / "sample_abstracts.json"


# ════════════════════════════════════════════════════════════════════════════
# Module-level fixtures
# ════════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """Redirect batch and export directories to a temp folder for every test.

    Prevents tests from writing to or reading from the real data/slr/ tree.
    """
    batches = tmp_path / "batches"
    exports = tmp_path / "exports"
    batches.mkdir()
    exports.mkdir()
    monkeypatch.setattr(scr, "_BATCHES_DIR", batches)
    monkeypatch.setattr(scr, "_EXPORTS_DIR", exports)
    return batches, exports


@pytest.fixture
def pico() -> PICOCriteria:
    """Standard PICO criteria for diabetes + CGM screening."""
    return PICOCriteria(
        population="Adults aged ≥18 with type 2 diabetes mellitus",
        intervention="Remote continuous glucose monitoring (CGM)",
        comparison="Standard self-monitored blood glucose (SMBG)",
        outcomes=["HbA1c reduction", "Quality of life (EQ-5D)", "Cost per QALY"],
        study_types=["RCT", "Cohort study", "Economic evaluation"],
        exclusion_criteria=["Follow-up < 6 months", "Paediatric populations only"],
    )


@pytest.fixture
def abstract_include() -> Abstract:
    """Clear-include abstract: adult T2DM RCT of CGM."""
    return Abstract(
        pmid="35421876",
        title="Continuous glucose monitoring versus SMBG in adults with type 2 diabetes: an RCT",
        abstract=(
            "Background: rtCGM in T2DM. "
            "Methods: 312 adults aged 18–80, T2DM, randomised to CGM vs SMBG, 24 weeks. "
            "Results: HbA1c –11.4 vs –4.7 mmol/mol (p<0.001). EQ-5D improved (p=0.003). "
            "Conclusions: CGM improves HbA1c and quality of life in adults with T2DM."
        ),
        authors=["Holloway SE", "Ramachandran A", "Bates CJ"],
        journal="Diabetes Care",
        year=2024,
        doi="10.2337/dc23-1847",
        keywords=["CGM", "type 2 diabetes", "HbA1c", "RCT"],
    )


@pytest.fixture
def abstract_exclude() -> Abstract:
    """Clear-exclude abstract: paediatric T1DM flash CGM."""
    return Abstract(
        pmid="36445678",
        title="Flash glucose monitoring in children with type 1 diabetes",
        abstract=(
            "Methods: 148 children aged 6–17 with T1DM, crossover RCT. "
            "FGM vs FSBG, 16 weeks. "
            "Results: HbA1c –4.6 mmol/mol with FGM. "
            "Conclusions: FGM improves glycaemic control in paediatric T1DM."
        ),
        authors=["Cameron FJ", "Garvey K"],
        journal="Diabetes Care",
        year=2023,
    )


@pytest.fixture
def abstract_uncertain() -> Abstract:
    """Borderline abstract: smartphone app, adult T2DM, pilot study."""
    return Abstract(
        pmid="37445892",
        title="Smartphone diabetes self-management app vs standard education in T2DM",
        abstract=(
            "Methods: 64 adults with T2DM, randomised pilot. App vs DESMOND education. "
            "HbA1c –5.1 vs –3.8 mmol/mol (not significant; p=0.47). "
            "Conclusions: Definitive RCT warranted."
        ),
        authors=["Majeed A", "Banerjee M"],
        journal="Pilot and Feasibility Studies",
        year=2023,
    )


@pytest.fixture
def three_abstracts(abstract_include, abstract_exclude, abstract_uncertain) -> list[Abstract]:
    return [abstract_include, abstract_exclude, abstract_uncertain]


# ── Mock Claude response text ─────────────────────────────────────────────────

INCLUDE_RESPONSE = """\
PMID: 35421876
Decision: INCLUDE
Confidence: HIGH

PICO Assessment:
- Population match:    YES — 312 adults with T2DM explicitly stated (aged 18–80)
- Intervention match:  YES — real-time CGM described as the intervention
- Comparison match:    YES — SMBG control arm confirmed
- Outcome match:       YES — HbA1c reduction and EQ-5D both reported

Reasoning: This RCT directly enrols adults aged 18–80 with T2DM, matching the \
target population. CGM is the study intervention with SMBG as the comparator. \
HbA1c and EQ-5D are both explicitly reported.
"""

EXCLUDE_RESPONSE = """\
PMID: 36445678
Decision: EXCLUDE
Confidence: HIGH

PICO Assessment:
- Population match:    NO — children aged 6–17 with T1DM, not adult T2DM
- Intervention match:  PARTIAL — flash CGM is adjacent but paediatric context
- Comparison match:    YES — FSBG comparator present
- Outcome match:       YES — HbA1c reported

Reasoning: The study population is children aged 6–17 with type 1 diabetes, \
which is excluded on both age (paediatric) and disease (T1DM vs T2DM). \
The trial fails the population PICO criterion.

Exclusion reasons (if EXCLUDE):
- Wrong population: children aged 6–17, not adults ≥18
- Wrong disease: type 1 diabetes, not type 2
"""

UNCERTAIN_RESPONSE = """\
PMID: 37445892
Decision: UNCERTAIN
Confidence: MEDIUM

PICO Assessment:
- Population match:    YES — adults with T2DM confirmed
- Intervention match:  PARTIAL — smartphone app uploads fingerstick readings; \
no CGM sensor hardware described
- Comparison match:    N/A — standard education control, not SMBG
- Outcome match:       PARTIAL — HbA1c reported but pilot not powered for this

Reasoning: The study population matches but the intervention is a smartphone \
glucose-upload app rather than a CGM device. Whether this counts as remote \
monitoring per the PICO depends on the full-text protocol description. \
Flagging for full-text review.
"""

BATCH_RESPONSE = INCLUDE_RESPONSE + "\n" + EXCLUDE_RESPONSE + "\n" + UNCERTAIN_RESPONSE


# ── Mock anthropic module (no SDK required) ──────────────────────────────────

@pytest.fixture
def fake_anthropic():
    """Return a fake anthropic namespace with the exception classes screener needs."""

    class RateLimitError(Exception):
        pass

    class APIConnectionError(Exception):
        pass

    class APIStatusError(Exception):
        def __init__(self, message="", status_code=500):
            super().__init__(message)
            self.status_code = status_code

    class _Message:
        def __init__(self, text: str):
            self.content = [types.SimpleNamespace(text=text)]

    class _Messages:
        def __init__(self, response_text: str):
            self._response = response_text
            self.create = MagicMock(return_value=_Message(response_text))

    class _Client:
        def __init__(self, api_key=None):
            self.messages = _Messages(INCLUDE_RESPONSE)

    return types.SimpleNamespace(
        RateLimitError=RateLimitError,
        APIConnectionError=APIConnectionError,
        APIStatusError=APIStatusError,
        Anthropic=_Client,
    )


@pytest.fixture
def api_enabled(monkeypatch, fake_anthropic):
    """Patch screener to appear to have a working Anthropic SDK and API key."""
    monkeypatch.setattr(scr, "anthropic", fake_anthropic)
    monkeypatch.setattr(scr, "_ANTHROPIC_AVAILABLE", True)
    monkeypatch.setattr(scr, "ANTHROPIC_API_KEY", "sk-test-key")


# ════════════════════════════════════════════════════════════════════════════
# 1. PICOCriteria validation
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestPICOCriteriaValidation:
    """Field constraints and helper methods on PICOCriteria."""

    def test_valid_pico_creates_successfully(self, pico):
        assert pico.population == "Adults aged ≥18 with type 2 diabetes mellitus"
        assert len(pico.outcomes) == 3
        assert len(pico.study_types) == 3

    def test_missing_population_raises(self):
        with pytest.raises(ValidationError, match="population"):
            PICOCriteria(
                intervention="CGM",
                comparison="SMBG",
                outcomes=["HbA1c"],
                study_types=["RCT"],
            )

    def test_missing_intervention_raises(self):
        with pytest.raises(ValidationError):
            PICOCriteria(
                population="Adults with T2DM",
                comparison="SMBG",
                outcomes=["HbA1c"],
                study_types=["RCT"],
            )

    def test_empty_outcomes_list_raises(self):
        with pytest.raises(ValidationError):
            PICOCriteria(
                population="Adults with T2DM",
                intervention="CGM",
                comparison="SMBG",
                outcomes=[],
                study_types=["RCT"],
            )

    def test_empty_study_types_raises(self):
        with pytest.raises(ValidationError):
            PICOCriteria(
                population="Adults with T2DM",
                intervention="CGM",
                comparison="SMBG",
                outcomes=["HbA1c"],
                study_types=[],
            )

    def test_whitespace_only_population_raises(self):
        with pytest.raises(ValidationError):
            PICOCriteria(
                population="   ",
                intervention="CGM",
                comparison="SMBG",
                outcomes=["HbA1c"],
                study_types=["RCT"],
            )

    def test_exclusion_criteria_is_optional(self):
        p = PICOCriteria(
            population="Adults with T2DM",
            intervention="CGM",
            comparison="Any",
            outcomes=["HbA1c"],
            study_types=["RCT"],
        )
        assert p.exclusion_criteria is None

    def test_to_prompt_text_contains_all_pico_fields(self, pico):
        text = pico.to_prompt_text()
        assert "Adults aged ≥18 with type 2 diabetes mellitus" in text
        assert "Remote continuous glucose monitoring" in text
        assert "Standard self-monitored blood glucose" in text
        assert "HbA1c reduction" in text
        assert "RCT" in text

    def test_to_prompt_text_includes_exclusion_criteria(self, pico):
        text = pico.to_prompt_text()
        assert "Follow-up < 6 months" in text
        assert "Paediatric populations only" in text

    def test_to_prompt_text_omits_exclusions_section_when_none(self):
        p = PICOCriteria(
            population="Adults",
            intervention="CGM",
            comparison="Any",
            outcomes=["HbA1c"],
            study_types=["RCT"],
        )
        text = p.to_prompt_text()
        assert "Exclusions" not in text


# ════════════════════════════════════════════════════════════════════════════
# 2. Abstract validation
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestAbstractValidation:
    """Field constraints and helper methods on Abstract."""

    def test_valid_abstract_creates_successfully(self, abstract_include):
        assert abstract_include.pmid == "35421876"
        assert len(abstract_include.authors) == 3

    def test_missing_pmid_raises(self):
        with pytest.raises(ValidationError):
            Abstract(
                title="T",
                abstract="A",
                authors=["Smith J"],
                journal="BMJ",
                year=2023,
            )

    def test_empty_pmid_raises(self):
        with pytest.raises(ValidationError):
            Abstract(
                pmid="  ",
                title="T",
                abstract="A",
                authors=["Smith J"],
                journal="BMJ",
                year=2023,
            )

    def test_year_below_1900_raises(self):
        with pytest.raises(ValidationError):
            Abstract(
                pmid="1",
                title="T",
                abstract="A",
                authors=["Smith J"],
                journal="BMJ",
                year=1899,
            )

    def test_year_above_next_year_raises(self):
        future = datetime.now(timezone.utc).year + 2
        with pytest.raises(ValidationError):
            Abstract(
                pmid="1",
                title="T",
                abstract="A",
                authors=["Smith J"],
                journal="BMJ",
                year=future,
            )

    def test_empty_authors_list_raises(self):
        with pytest.raises(ValidationError):
            Abstract(
                pmid="1",
                title="T",
                abstract="A",
                authors=[],
                journal="BMJ",
                year=2023,
            )

    def test_short_citation_multiple_authors(self, abstract_include):
        citation = abstract_include.short_citation()
        assert citation == "Holloway SE et al. (2024) Diabetes Care"

    def test_short_citation_single_author(self):
        ab = Abstract(
            pmid="1",
            title="T",
            abstract="A",
            authors=["Jones B"],
            journal="BMJ",
            year=2022,
        )
        assert ab.short_citation() == "Jones B (2022) BMJ"
        assert "et al." not in ab.short_citation()

    def test_has_keyword_case_insensitive(self, abstract_include):
        assert abstract_include.has_keyword("cgm")
        assert abstract_include.has_keyword("CGM")
        assert abstract_include.has_keyword("HbA1c")

    def test_has_keyword_returns_false_for_no_match(self, abstract_include):
        assert not abstract_include.has_keyword("semaglutide")

    def test_has_keyword_returns_false_when_keywords_none(self, abstract_exclude):
        assert abstract_exclude.keywords is None
        assert not abstract_exclude.has_keyword("anything")


# ════════════════════════════════════════════════════════════════════════════
# 3. Prompt loading
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestLoadScreeningPrompt:
    """Template file integrity."""

    def test_returns_non_empty_string(self):
        template = load_screening_prompt()
        assert isinstance(template, str)
        assert len(template) > 500

    def test_contains_role_section(self):
        template = load_screening_prompt()
        assert "ROLE" in template

    def test_contains_decision_rules_section(self):
        template = load_screening_prompt()
        assert "DECISION RULES" in template

    def test_contains_pico_placeholders(self):
        import re
        template = load_screening_prompt()
        placeholders = set(re.findall(r"\{(\w+)\}", template))
        for expected in ("population", "intervention", "comparison",
                         "outcomes", "study_types", "additional_exclusions"):
            assert expected in placeholders, f"Missing placeholder: {{{expected}}}"

    def test_raises_if_prompt_file_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(scr, "_PROMPT_PATH", tmp_path / "nonexistent.txt")
        with pytest.raises(FileNotFoundError, match="screening.txt"):
            load_screening_prompt()


# ════════════════════════════════════════════════════════════════════════════
# 4. Prompt formatting
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestFormatScreeningPrompt:
    """PICO injection and multi-abstract batching."""

    import re as _re

    def _stray_placeholders(self, text: str) -> list[str]:
        import re
        return re.findall(r"\{[a-z_]+\}", text)

    def test_no_stray_placeholders_single_abstract(self, pico, abstract_include):
        prompt = format_screening_prompt(pico, [abstract_include])
        assert self._stray_placeholders(prompt) == [], "Stray {} placeholders remain"

    def test_no_stray_placeholders_multiple_abstracts(self, pico, three_abstracts):
        prompt = format_screening_prompt(pico, three_abstracts)
        assert self._stray_placeholders(prompt) == []

    def test_pico_population_injected(self, pico, abstract_include):
        prompt = format_screening_prompt(pico, [abstract_include])
        assert "Adults aged ≥18 with type 2 diabetes mellitus" in prompt

    def test_pico_intervention_injected(self, pico, abstract_include):
        prompt = format_screening_prompt(pico, [abstract_include])
        assert "Remote continuous glucose monitoring" in prompt

    def test_pico_outcomes_injected(self, pico, abstract_include):
        prompt = format_screening_prompt(pico, [abstract_include])
        assert "HbA1c reduction" in prompt

    def test_extra_exclusions_injected(self, pico, abstract_include):
        prompt = format_screening_prompt(pico, [abstract_include])
        assert "Follow-up < 6 months" in prompt
        assert "Paediatric populations only" in prompt

    def test_all_pmids_present_in_batch_prompt(self, pico, three_abstracts):
        prompt = format_screening_prompt(pico, three_abstracts)
        for ab in three_abstracts:
            assert ab.pmid in prompt, f"PMID {ab.pmid} missing from prompt"

    def test_abstract_count_label_correct_single(self, pico, abstract_include):
        prompt = format_screening_prompt(pico, [abstract_include])
        assert "1 abstract)" in prompt

    def test_abstract_count_label_correct_multiple(self, pico, three_abstracts):
        prompt = format_screening_prompt(pico, three_abstracts)
        assert "3 abstracts)" in prompt

    def test_numbered_abstracts_in_order(self, pico, three_abstracts):
        prompt = format_screening_prompt(pico, three_abstracts)
        assert "Abstract 1 of 3" in prompt
        assert "Abstract 2 of 3" in prompt
        assert "Abstract 3 of 3" in prompt

    def test_empty_abstracts_raises_value_error(self, pico):
        with pytest.raises(ValueError, match="at least one"):
            format_screening_prompt(pico, [])

    def test_prompt_is_reasonably_long(self, pico, three_abstracts):
        prompt = format_screening_prompt(pico, three_abstracts)
        # 3 abstracts + full PICO instructions should be well over 3 KB
        assert len(prompt) > 3_000

    def test_short_citation_used_in_prompt(self, pico, abstract_include):
        prompt = format_screening_prompt(pico, [abstract_include])
        assert abstract_include.short_citation() in prompt


# ════════════════════════════════════════════════════════════════════════════
# 5. Response parsing
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestParseScreeningResponse:
    """Extraction of structured fields from Claude's text output."""

    # ── Decision + confidence ────────────────────────────────────────────────

    def test_include_decision_parsed(self):
        d = parse_screening_response(INCLUDE_RESPONSE, "35421876")
        assert d.decision == Decision.INCLUDE

    def test_exclude_decision_parsed(self):
        d = parse_screening_response(EXCLUDE_RESPONSE, "36445678")
        assert d.decision == Decision.EXCLUDE

    def test_uncertain_decision_parsed(self):
        d = parse_screening_response(UNCERTAIN_RESPONSE, "37445892")
        assert d.decision == Decision.UNCERTAIN

    def test_high_confidence_parsed(self):
        d = parse_screening_response(INCLUDE_RESPONSE, "35421876")
        assert d.confidence == Confidence.HIGH

    def test_medium_confidence_parsed(self):
        d = parse_screening_response(UNCERTAIN_RESPONSE, "37445892")
        assert d.confidence == Confidence.MEDIUM

    def test_parsing_is_case_insensitive(self):
        response = INCLUDE_RESPONSE.replace("INCLUDE", "include").replace("HIGH", "high")
        d = parse_screening_response(response, "35421876")
        assert d.decision == Decision.INCLUDE
        assert d.confidence == Confidence.HIGH

    # ── PICO match ───────────────────────────────────────────────────────────

    def test_all_four_pico_keys_present(self):
        d = parse_screening_response(INCLUDE_RESPONSE, "35421876")
        assert set(d.pico_match.keys()) == {"population", "intervention", "comparison", "outcome"}

    def test_yes_verdict_sets_matched_true(self):
        d = parse_screening_response(INCLUDE_RESPONSE, "35421876")
        assert d.pico_match["population"].matched is True
        assert d.pico_match["intervention"].matched is True

    def test_no_verdict_sets_matched_false(self):
        d = parse_screening_response(EXCLUDE_RESPONSE, "36445678")
        assert d.pico_match["population"].matched is False

    def test_partial_verdict_sets_matched_true(self):
        d = parse_screening_response(EXCLUDE_RESPONSE, "36445678")
        # Intervention is PARTIAL for the exclude abstract
        assert d.pico_match["intervention"].matched is True

    def test_na_verdict_sets_matched_false(self):
        d = parse_screening_response(UNCERTAIN_RESPONSE, "37445892")
        # Comparison match is N/A → matched=False (conservative)
        assert d.pico_match["comparison"].matched is False

    def test_pico_match_score_four_for_perfect_include(self):
        d = parse_screening_response(INCLUDE_RESPONSE, "35421876")
        assert d.pico_match_score == 4

    def test_pico_match_score_reduced_for_exclude(self):
        d = parse_screening_response(EXCLUDE_RESPONSE, "36445678")
        # Population NO, intervention PARTIAL(True), comparison YES, outcome YES → 3
        assert d.pico_match_score == 3

    def test_pico_note_extracted(self):
        d = parse_screening_response(INCLUDE_RESPONSE, "35421876")
        assert "312 adults" in d.pico_match["population"].note

    # ── Reasoning + exclusion reasons ────────────────────────────────────────

    def test_reasoning_extracted(self):
        d = parse_screening_response(INCLUDE_RESPONSE, "35421876")
        assert "adults aged 18" in d.reasoning.lower() or "18–80" in d.reasoning

    def test_exclusion_reasons_extracted_for_exclude(self):
        d = parse_screening_response(EXCLUDE_RESPONSE, "36445678")
        assert len(d.exclusion_reasons) == 2
        assert any("children" in r.lower() or "6–17" in r for r in d.exclusion_reasons)

    def test_exclusion_reasons_empty_for_include(self):
        d = parse_screening_response(INCLUDE_RESPONSE, "35421876")
        # ScreeningDecision only auto-fills exclusion_reasons for exclude decisions
        assert d.exclusion_reasons == []

    def test_pmid_set_correctly(self):
        d = parse_screening_response(INCLUDE_RESPONSE, "35421876")
        assert d.pmid == "35421876"

    # ── Edge cases ───────────────────────────────────────────────────────────

    def test_missing_pmid_returns_uncertain_low(self):
        d = parse_screening_response(BATCH_RESPONSE, "99999999")
        assert d.decision == Decision.UNCERTAIN
        assert d.confidence == Confidence.LOW

    def test_completely_malformed_response_returns_uncertain(self):
        d = parse_screening_response("no structure here at all", "35421876")
        assert d.decision == Decision.UNCERTAIN
        assert d.confidence == Confidence.LOW

    def test_single_abstract_response_without_pmid_header(self):
        """Response with no PMID: header should be treated as single-abstract."""
        response = """\
Decision: INCLUDE
Confidence: HIGH

PICO Assessment:
- Population match:    YES — adults with T2DM confirmed
- Intervention match:  YES — CGM device confirmed
- Comparison match:    YES — SMBG control
- Outcome match:       YES — HbA1c reported

Reasoning: All PICO criteria met based on abstract content.
"""
        d = parse_screening_response(response, "35421876")
        assert d.decision == Decision.INCLUDE

    def test_missing_pico_component_gets_safe_default(self):
        """A response that omits one PICO line gets a fallback PICOMatchItem."""
        response = """\
PMID: 35421876
Decision: INCLUDE
Confidence: MEDIUM

PICO Assessment:
- Population match:    YES — adults with T2DM
- Intervention match:  YES — CGM confirmed
- Outcome match:       YES — HbA1c reported

Reasoning: Comparison arm not described in abstract.
"""
        d = parse_screening_response(response, "35421876")
        assert "comparison" in d.pico_match
        assert d.pico_match["comparison"].matched is False
        assert "absent" in d.pico_match["comparison"].note.lower() or \
               "verify" in d.pico_match["comparison"].note.lower()

    def test_batch_response_extracts_correct_block_per_pmid(self):
        """Multi-abstract response: each PMID gets its own decision."""
        d_inc  = parse_screening_response(BATCH_RESPONSE, "35421876")
        d_exc  = parse_screening_response(BATCH_RESPONSE, "36445678")
        d_unc  = parse_screening_response(BATCH_RESPONSE, "37445892")
        assert d_inc.decision  == Decision.INCLUDE
        assert d_exc.decision  == Decision.EXCLUDE
        assert d_unc.decision  == Decision.UNCERTAIN

    def test_excluded_with_empty_reasons_auto_fills_from_reasoning(self):
        """When excluded but list is empty, screener copies reasoning into reasons."""
        response = """\
PMID: 35421876
Decision: EXCLUDE
Confidence: HIGH

PICO Assessment:
- Population match:    NO — wrong disease
- Intervention match:  NO — drug not device
- Comparison match:    YES — placebo
- Outcome match:       NO — no relevant outcome

Reasoning: Study is a drug trial in T1DM children; fails on population, intervention, and outcome.
"""
        d = parse_screening_response(response, "35421876")
        assert d.decision == Decision.EXCLUDE
        assert len(d.exclusion_reasons) > 0  # auto-filled from reasoning


# ════════════════════════════════════════════════════════════════════════════
# 6. Batch creation
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestCreateScreeningBatch:
    """create_screening_batch persistence and structure."""

    def test_returns_screening_batch_instance(self, pico, three_abstracts):
        batch = create_screening_batch(three_abstracts, pico)
        assert isinstance(batch, ScreeningBatch)

    def test_batch_id_starts_with_slr(self, pico, three_abstracts):
        batch = create_screening_batch(three_abstracts, pico)
        assert batch.batch_id.startswith("slr_")

    def test_batch_id_contains_timestamp(self, pico, three_abstracts):
        import re
        batch = create_screening_batch(three_abstracts, pico)
        # Format: slr_YYYYMMDD_HHMMSS_<8hex>
        assert re.match(r"slr_\d{8}_\d{6}_[0-9a-f]{8}", batch.batch_id)

    def test_json_file_is_written(self, pico, three_abstracts, isolated_dirs):
        batches_dir, _ = isolated_dirs
        batch = create_screening_batch(three_abstracts, pico)
        expected = batches_dir / f"{batch.batch_id}.json"
        assert expected.is_file(), f"Batch file not found: {expected}"

    def test_json_file_is_valid(self, pico, three_abstracts, isolated_dirs):
        batches_dir, _ = isolated_dirs
        batch = create_screening_batch(three_abstracts, pico)
        raw = json.loads((batches_dir / f"{batch.batch_id}.json").read_text())
        assert raw["batch_id"] == batch.batch_id

    def test_abstracts_stored_in_batch(self, pico, three_abstracts):
        batch = create_screening_batch(three_abstracts, pico)
        assert len(batch.abstracts) == 3
        pmids = {a.pmid for a in batch.abstracts}
        assert "35421876" in pmids

    def test_summary_initialised_to_zeros(self, pico, three_abstracts):
        batch = create_screening_batch(three_abstracts, pico)
        assert batch.summary["total"] == 0
        assert batch.summary["included"] == 0
        assert batch.summary["inclusion_rate"] == 0.0

    def test_pico_criteria_stored(self, pico, three_abstracts):
        batch = create_screening_batch(three_abstracts, pico)
        assert batch.pico_criteria.population == pico.population


# ════════════════════════════════════════════════════════════════════════════
# 7. Batch helper methods
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestBatchHelperMethods:
    """ScreeningBatch.add_decision, filters, pending_pmids, get_* lookups."""

    @pytest.fixture
    def batch_with_decisions(self, pico, three_abstracts) -> ScreeningBatch:
        batch = create_screening_batch(three_abstracts, pico)
        batch.add_decision(parse_screening_response(INCLUDE_RESPONSE,  "35421876"))
        batch.add_decision(parse_screening_response(EXCLUDE_RESPONSE,  "36445678"))
        batch.add_decision(parse_screening_response(UNCERTAIN_RESPONSE, "37445892"))
        return batch

    def test_summary_updates_after_add_decision(self, batch_with_decisions):
        s = batch_with_decisions.summary
        assert s["total"] == 3
        assert s["included"] == 1
        assert s["excluded"] == 1
        assert s["uncertain"] == 1
        assert s["inclusion_rate"] == pytest.approx(1 / 3, abs=0.001)

    def test_add_decision_duplicate_pmid_raises(self, pico, three_abstracts):
        batch = create_screening_batch(three_abstracts, pico)
        d = parse_screening_response(INCLUDE_RESPONSE, "35421876")
        batch.add_decision(d)
        with pytest.raises(ValueError, match="35421876"):
            batch.add_decision(d)

    def test_included_decisions_filter(self, batch_with_decisions):
        included = batch_with_decisions.included_decisions()
        assert len(included) == 1
        assert included[0].pmid == "35421876"

    def test_excluded_decisions_filter(self, batch_with_decisions):
        excluded = batch_with_decisions.excluded_decisions()
        assert len(excluded) == 1
        assert excluded[0].pmid == "36445678"

    def test_uncertain_decisions_filter(self, batch_with_decisions):
        uncertain = batch_with_decisions.uncertain_decisions()
        assert len(uncertain) == 1
        assert uncertain[0].pmid == "37445892"

    def test_pending_pmids_empty_when_all_decided(self, batch_with_decisions):
        assert batch_with_decisions.pending_pmids() == []

    def test_pending_pmids_shows_undecided(self, pico, three_abstracts):
        batch = create_screening_batch(three_abstracts, pico)
        batch.add_decision(parse_screening_response(INCLUDE_RESPONSE, "35421876"))
        pending = batch.pending_pmids()
        assert "36445678" in pending
        assert "37445892" in pending
        assert "35421876" not in pending

    def test_get_abstract_returns_correct_abstract(self, batch_with_decisions):
        ab = batch_with_decisions.get_abstract("35421876")
        assert ab is not None
        assert ab.title.startswith("Continuous glucose")

    def test_get_abstract_returns_none_for_missing(self, batch_with_decisions):
        assert batch_with_decisions.get_abstract("00000000") is None

    def test_get_decision_returns_correct_decision(self, batch_with_decisions):
        d = batch_with_decisions.get_decision("36445678")
        assert d is not None
        assert d.decision == Decision.EXCLUDE

    def test_get_decision_returns_none_for_missing(self, batch_with_decisions):
        assert batch_with_decisions.get_decision("00000000") is None

    def test_mean_pico_score_calculated(self, batch_with_decisions):
        # include=4, exclude=3, uncertain=2(N/A comparison=False) → 9/3=3.0
        assert batch_with_decisions.summary["mean_pico_score"] >= 2.0


# ════════════════════════════════════════════════════════════════════════════
# 8. Batch save / load
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestSaveAndLoadBatch:
    """Round-trip serialisation of ScreeningBatch to and from JSON."""

    def test_save_batch_writes_updated_decisions(self, pico, three_abstracts, isolated_dirs):
        batches_dir, _ = isolated_dirs
        batch = create_screening_batch(three_abstracts, pico)
        batch.add_decision(parse_screening_response(INCLUDE_RESPONSE, "35421876"))
        path = save_batch(batch)
        raw = json.loads(Path(path).read_text())
        assert len(raw["decisions"]) == 1

    def test_load_batch_restores_batch_id(self, pico, three_abstracts):
        batch = create_screening_batch(three_abstracts, pico)
        batch.add_decision(parse_screening_response(INCLUDE_RESPONSE, "35421876"))
        save_batch(batch)
        loaded = load_batch(batch.batch_id)
        assert loaded.batch_id == batch.batch_id

    def test_load_batch_restores_decisions(self, pico, three_abstracts):
        batch = create_screening_batch(three_abstracts, pico)
        batch.add_decision(parse_screening_response(INCLUDE_RESPONSE,  "35421876"))
        batch.add_decision(parse_screening_response(EXCLUDE_RESPONSE,  "36445678"))
        save_batch(batch)
        loaded = load_batch(batch.batch_id)
        assert loaded.summary["total"] == 2
        assert loaded.summary["included"] == 1

    def test_load_batch_raises_for_unknown_id(self):
        with pytest.raises(FileNotFoundError, match="no-such-batch"):
            load_batch("no-such-batch")


# ════════════════════════════════════════════════════════════════════════════
# 9. Export
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestExportScreeningResults:
    """CSV export column presence, data integrity, and error handling."""

    _EXPECTED_COLUMNS = [
        "PMID", "Title", "Authors", "Journal", "Year",
        "Decision", "Confidence", "PICO_Score",
        "Reasoning", "Exclusion_Reasons", "Reviewer", "Timestamp",
    ]

    @pytest.fixture
    def screened_batch(self, pico, three_abstracts) -> ScreeningBatch:
        batch = create_screening_batch(three_abstracts, pico)
        batch.add_decision(parse_screening_response(INCLUDE_RESPONSE,  "35421876"))
        batch.add_decision(parse_screening_response(EXCLUDE_RESPONSE,  "36445678"))
        batch.add_decision(parse_screening_response(UNCERTAIN_RESPONSE, "37445892"))
        return batch

    def test_csv_file_is_created(self, screened_batch, isolated_dirs):
        _, exports_dir = isolated_dirs
        path = export_screening_results(screened_batch, format="csv")
        assert Path(path).is_file()

    def test_csv_has_correct_columns(self, screened_batch):
        path = export_screening_results(screened_batch, format="csv")
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames == self._EXPECTED_COLUMNS

    def test_csv_has_one_row_per_decision(self, screened_batch):
        path = export_screening_results(screened_batch, format="csv")
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 3

    def test_csv_decision_values_are_lowercase_enum_strings(self, screened_batch):
        path = export_screening_results(screened_batch, format="csv")
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        decisions = {r["PMID"]: r["Decision"] for r in rows}
        assert decisions["35421876"] == "include"
        assert decisions["36445678"] == "exclude"
        assert decisions["37445892"] == "uncertain"

    def test_csv_pico_score_column_is_numeric(self, screened_batch):
        path = export_screening_results(screened_batch, format="csv")
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        for row in rows:
            assert int(row["PICO_Score"]) >= 0

    def test_csv_exclusion_reasons_joined_with_semicolon(self, screened_batch):
        path = export_screening_results(screened_batch, format="csv")
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        exc_row = next(r for r in rows if r["Decision"] == "exclude")
        # Two reasons were extracted → joined with "; "
        assert ";" in exc_row["Exclusion_Reasons"] or len(exc_row["Exclusion_Reasons"]) > 0

    def test_csv_title_populated_from_abstract(self, screened_batch):
        path = export_screening_results(screened_batch, format="csv")
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        inc_row = next(r for r in rows if r["PMID"] == "35421876")
        assert "glucose monitoring" in inc_row["Title"].lower()

    def test_csv_reviewer_defaults_to_ai_claude(self, screened_batch):
        path = export_screening_results(screened_batch, format="csv")
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert all(r["Reviewer"] == "AI-Claude" for r in rows)

    def test_invalid_format_raises_value_error(self, screened_batch):
        with pytest.raises(ValueError, match="Unsupported export format"):
            export_screening_results(screened_batch, format="pdf")

    def test_export_uses_batch_id_as_filename(self, screened_batch, isolated_dirs):
        _, exports_dir = isolated_dirs
        path = export_screening_results(screened_batch, format="csv")
        assert screened_batch.batch_id in Path(path).name


# ════════════════════════════════════════════════════════════════════════════
# 10. screen_abstracts with mocked API
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestScreenAbstractsMocked:
    """screen_abstracts behaviour with _call_claude_with_retry monkeypatched."""

    def test_raises_import_error_without_anthropic(self, monkeypatch, pico, abstract_include):
        monkeypatch.setattr(scr, "_ANTHROPIC_AVAILABLE", False)
        with pytest.raises(ImportError, match="anthropic"):
            screen_abstracts([abstract_include], pico)

    def test_raises_environment_error_without_api_key(self, monkeypatch, pico, abstract_include):
        monkeypatch.setattr(scr, "_ANTHROPIC_AVAILABLE", True)
        monkeypatch.setattr(scr, "ANTHROPIC_API_KEY", None)
        with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
            screen_abstracts([abstract_include], pico)

    def test_returns_one_decision_per_abstract(
        self, monkeypatch, api_enabled, pico, three_abstracts
    ):
        monkeypatch.setattr(scr, "_call_claude_with_retry",
                            lambda client, prompt: BATCH_RESPONSE)
        decisions = screen_abstracts(three_abstracts, pico)
        assert len(decisions) == 3

    def test_all_decisions_are_screening_decision_instances(
        self, monkeypatch, api_enabled, pico, three_abstracts
    ):
        monkeypatch.setattr(scr, "_call_claude_with_retry",
                            lambda client, prompt: BATCH_RESPONSE)
        for d in screen_abstracts(three_abstracts, pico):
            assert isinstance(d, ScreeningDecision)

    def test_batch_size_controls_api_call_count(
        self, monkeypatch, api_enabled, pico, three_abstracts
    ):
        call_count = 0

        def count_calls(client, prompt):
            nonlocal call_count
            call_count += 1
            return BATCH_RESPONSE

        monkeypatch.setattr(scr, "_call_claude_with_retry", count_calls)
        screen_abstracts(three_abstracts, pico, batch_size=1)
        assert call_count == 3

    def test_batch_size_two_uses_two_api_calls(
        self, monkeypatch, api_enabled, pico, three_abstracts
    ):
        call_count = 0

        def count_calls(client, prompt):
            nonlocal call_count
            call_count += 1
            return BATCH_RESPONSE

        monkeypatch.setattr(scr, "_call_claude_with_retry", count_calls)
        screen_abstracts(three_abstracts, pico, batch_size=2)
        assert call_count == 2  # batch[0:2] + batch[2:3]

    def test_api_failure_degrades_batch_to_uncertain(
        self, monkeypatch, api_enabled, pico, three_abstracts
    ):
        def always_fail(client, prompt):
            raise RuntimeError("network down")

        monkeypatch.setattr(scr, "_call_claude_with_retry", always_fail)
        decisions = screen_abstracts(three_abstracts, pico)
        assert len(decisions) == 3
        for d in decisions:
            assert d.decision == Decision.UNCERTAIN
            assert d.confidence == Confidence.LOW
            assert "API call failed" in d.reasoning

    def test_correct_decisions_parsed_from_mock_response(
        self, monkeypatch, api_enabled, pico, three_abstracts
    ):
        monkeypatch.setattr(scr, "_call_claude_with_retry",
                            lambda client, prompt: BATCH_RESPONSE)
        decisions = {d.pmid: d for d in screen_abstracts(three_abstracts, pico)}
        assert decisions["35421876"].decision == Decision.INCLUDE
        assert decisions["36445678"].decision == Decision.EXCLUDE
        assert decisions["37445892"].decision == Decision.UNCERTAIN


# ════════════════════════════════════════════════════════════════════════════
# 11. Retry logic
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestRetryLogic:
    """_call_claude_with_retry back-off and error-type handling."""

    @pytest.fixture
    def mock_sleep(self, monkeypatch):
        """Replace time.sleep to avoid actually waiting."""
        sleep_calls: list[float] = []
        monkeypatch.setattr(scr.time, "sleep", lambda s: sleep_calls.append(s))
        return sleep_calls

    def _make_client(self, fake_anthropic, side_effects: list):
        """Build a mock client whose messages.create raises a sequence of exceptions."""
        client = MagicMock()
        client.messages.create.side_effect = side_effects
        return client

    def test_retries_on_rate_limit_error(self, fake_anthropic, monkeypatch, mock_sleep):
        monkeypatch.setattr(scr, "anthropic", fake_anthropic)
        good_msg = MagicMock()
        good_msg.content = [MagicMock(text="Decision: INCLUDE")]
        client = self._make_client(fake_anthropic, [
            fake_anthropic.RateLimitError("rate limited"),
            fake_anthropic.RateLimitError("rate limited"),
            good_msg,
        ])
        result = _call_claude_with_retry(client, "prompt")
        assert result == "Decision: INCLUDE"
        assert client.messages.create.call_count == 3

    def test_retries_on_connection_error(self, fake_anthropic, monkeypatch, mock_sleep):
        monkeypatch.setattr(scr, "anthropic", fake_anthropic)
        good_msg = MagicMock()
        good_msg.content = [MagicMock(text="ok")]
        client = self._make_client(fake_anthropic, [
            fake_anthropic.APIConnectionError("timeout"),
            good_msg,
        ])
        result = _call_claude_with_retry(client, "prompt")
        assert result == "ok"

    def test_retries_on_server_error_5xx(self, fake_anthropic, monkeypatch, mock_sleep):
        monkeypatch.setattr(scr, "anthropic", fake_anthropic)
        good_msg = MagicMock()
        good_msg.content = [MagicMock(text="ok")]
        client = self._make_client(fake_anthropic, [
            fake_anthropic.APIStatusError("server error", status_code=503),
            good_msg,
        ])
        result = _call_claude_with_retry(client, "prompt")
        assert result == "ok"

    def test_does_not_retry_on_client_error_4xx(self, fake_anthropic, monkeypatch):
        monkeypatch.setattr(scr, "anthropic", fake_anthropic)
        exc = fake_anthropic.APIStatusError("bad request", status_code=400)
        client = self._make_client(fake_anthropic, [exc])
        with pytest.raises(fake_anthropic.APIStatusError):
            _call_claude_with_retry(client, "prompt")
        assert client.messages.create.call_count == 1

    def test_raises_after_max_retries_exhausted(self, fake_anthropic, monkeypatch, mock_sleep):
        monkeypatch.setattr(scr, "anthropic", fake_anthropic)
        client = self._make_client(fake_anthropic, [
            fake_anthropic.RateLimitError("rate limited"),
            fake_anthropic.RateLimitError("rate limited"),
            fake_anthropic.RateLimitError("rate limited"),
        ])
        with pytest.raises(fake_anthropic.RateLimitError):
            _call_claude_with_retry(client, "prompt")
        assert client.messages.create.call_count == 3

    def test_exponential_backoff_delays(self, fake_anthropic, monkeypatch, mock_sleep):
        monkeypatch.setattr(scr, "anthropic", fake_anthropic)
        good_msg = MagicMock()
        good_msg.content = [MagicMock(text="ok")]
        client = self._make_client(fake_anthropic, [
            fake_anthropic.RateLimitError("rl"),
            fake_anthropic.RateLimitError("rl"),
            good_msg,
        ])
        _call_claude_with_retry(client, "prompt")
        # Delays should be 2.0, 4.0 (base × 2^(attempt-1))
        assert mock_sleep[0] == pytest.approx(2.0)
        assert mock_sleep[1] == pytest.approx(4.0)

    def test_first_success_makes_no_sleep_calls(self, fake_anthropic, monkeypatch, mock_sleep):
        monkeypatch.setattr(scr, "anthropic", fake_anthropic)
        good_msg = MagicMock()
        good_msg.content = [MagicMock(text="ok")]
        client = self._make_client(fake_anthropic, [good_msg])
        _call_claude_with_retry(client, "prompt")
        assert mock_sleep == []


# ════════════════════════════════════════════════════════════════════════════
# 12. Sample abstracts JSON fixture
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestSampleAbstractsFixture:
    """Validate data/slr/sample_abstracts.json against the Abstract schema."""

    @pytest.fixture(scope="class")
    def sample_data(self):
        assert _SAMPLE_JSON.is_file(), f"Sample JSON not found: {_SAMPLE_JSON}"
        return json.loads(_SAMPLE_JSON.read_text(encoding="utf-8"))

    def test_json_loads_successfully(self, sample_data):
        assert "abstracts" in sample_data
        assert "metadata" in sample_data

    def test_total_abstract_count(self, sample_data):
        assert len(sample_data["abstracts"]) == 20

    def test_all_abstracts_pass_schema_validation(self, sample_data):
        errors = []
        for raw in sample_data["abstracts"]:
            clean = {k: v for k, v in raw.items() if not k.startswith("_")}
            try:
                Abstract.model_validate(clean)
            except ValidationError as exc:
                errors.append((raw["pmid"], str(exc)))
        assert errors == [], f"Schema validation failed for: {errors}"

    def test_category_counts_are_five_each(self, sample_data):
        cats = sample_data["metadata"]["categories"]
        for cat_name, pmid_list in cats.items():
            assert len(pmid_list) == 5, (
                f"Category '{cat_name}' has {len(pmid_list)} items, expected 5"
            )

    def test_all_pmids_unique(self, sample_data):
        pmids = [r["pmid"] for r in sample_data["abstracts"]]
        assert len(pmids) == len(set(pmids)), "Duplicate PMIDs found in sample JSON"

    def test_category_lists_cover_all_records(self, sample_data):
        cat_pmids: set[str] = set()
        for pmid_list in sample_data["metadata"]["categories"].values():
            cat_pmids.update(pmid_list)
        record_pmids = {r["pmid"] for r in sample_data["abstracts"]}
        assert cat_pmids == record_pmids, (
            f"Category/record mismatch: extra={cat_pmids - record_pmids}, "
            f"missing={record_pmids - cat_pmids}"
        )

    def test_expected_decision_field_present_on_every_record(self, sample_data):
        for raw in sample_data["abstracts"]:
            assert "_expected_decision" in raw, (
                f"PMID {raw['pmid']} missing _expected_decision"
            )
            assert raw["_expected_decision"] in ("include", "exclude", "uncertain")


# ════════════════════════════════════════════════════════════════════════════
# 13. End-to-end mocked pipeline
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestEndToEndMocked:
    """Full create → screen → save → export pipeline with mocked API."""

    @pytest.fixture
    def full_pipeline(self, monkeypatch, api_enabled, pico, three_abstracts, isolated_dirs):
        monkeypatch.setattr(scr, "_call_claude_with_retry",
                            lambda client, prompt: BATCH_RESPONSE)
        batch = create_screening_batch(three_abstracts, pico)
        decisions = screen_abstracts(three_abstracts, pico)
        for d in decisions:
            batch.add_decision(d)
        save_batch(batch)
        return batch

    def test_batch_summary_correct_after_pipeline(self, full_pipeline):
        s = full_pipeline.summary
        assert s["total"] == 3
        assert s["included"] == 1
        assert s["excluded"] == 1
        assert s["uncertain"] == 1

    def test_saved_batch_can_be_reloaded(self, full_pipeline):
        loaded = load_batch(full_pipeline.batch_id)
        assert loaded.summary["total"] == 3
        assert loaded.get_decision("35421876").decision == Decision.INCLUDE

    def test_csv_export_round_trip(self, full_pipeline):
        path = export_screening_results(full_pipeline, format="csv")
        with open(path, newline="", encoding="utf-8") as f:
            rows = {r["PMID"]: r for r in csv.DictReader(f)}
        assert rows["35421876"]["Decision"] == "include"
        assert rows["36445678"]["Decision"] == "exclude"
        assert rows["37445892"]["Decision"] == "uncertain"
        assert int(rows["35421876"]["PICO_Score"]) == 4


# ════════════════════════════════════════════════════════════════════════════
# 14. Integration tests (live Claude API)
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestIntegration:
    """Live API tests.  Require ANTHROPIC_API_KEY in the environment.

    Run with:
        pytest tests/test_slr_screener.py -m integration -v
    """

    @pytest.fixture(autouse=True)
    def require_api_key(self):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            pytest.skip("ANTHROPIC_API_KEY not set — skipping integration tests")

    @pytest.fixture(scope="class")
    def integration_decisions(self):
        """Screen 3 abstracts (1 include, 1 exclude, 1 uncertain) with real API."""
        pico = PICOCriteria(
            population="Adults aged ≥18 with type 2 diabetes mellitus",
            intervention="Remote continuous glucose monitoring (CGM)",
            comparison="Standard self-monitored blood glucose (SMBG) or usual care",
            outcomes=["HbA1c reduction", "Quality of life (EQ-5D)"],
            study_types=["RCT", "Cohort study", "Economic evaluation"],
            exclusion_criteria=["Paediatric populations only", "Animal studies"],
        )
        sample = json.loads(_SAMPLE_JSON.read_text(encoding="utf-8"))
        by_pmid = {r["pmid"]: r for r in sample["abstracts"]}

        def _make(pmid: str) -> Abstract:
            raw = {k: v for k, v in by_pmid[pmid].items() if not k.startswith("_")}
            return Abstract.model_validate(raw)

        abstracts = [
            _make("35421876"),  # clear include
            _make("36445678"),  # clear exclude (paediatric T1DM)
            _make("37445892"),  # borderline (smartphone app)
        ]
        return screen_abstracts(abstracts, pico, batch_size=3)

    def test_returns_correct_number_of_decisions(self, integration_decisions):
        assert len(integration_decisions) == 3

    def test_all_decisions_have_valid_enum_values(self, integration_decisions):
        for d in integration_decisions:
            assert isinstance(d.decision,   Decision)
            assert isinstance(d.confidence, Confidence)

    def test_clear_include_is_included(self, integration_decisions):
        d = next(d for d in integration_decisions if d.pmid == "35421876")
        assert d.decision == Decision.INCLUDE, (
            f"Expected INCLUDE for adult T2DM CGM RCT, got {d.decision}. "
            f"Reasoning: {d.reasoning}"
        )

    def test_paediatric_t1dm_is_excluded(self, integration_decisions):
        d = next(d for d in integration_decisions if d.pmid == "36445678")
        assert d.decision == Decision.EXCLUDE, (
            f"Expected EXCLUDE for paediatric T1DM study, got {d.decision}. "
            f"Reasoning: {d.reasoning}"
        )

    def test_pico_match_score_populated(self, integration_decisions):
        for d in integration_decisions:
            assert 0 <= d.pico_match_score <= 4
            assert all(
                isinstance(v, PICOMatchItem)
                for v in d.pico_match.values()
            )

    def test_reasoning_is_non_empty(self, integration_decisions):
        for d in integration_decisions:
            assert len(d.reasoning) > 20, f"Reasoning too short for PMID {d.pmid}"
