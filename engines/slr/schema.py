"""Pydantic schemas for Systematic Literature Review (SLR) screening.

These models cover the full abstract-screening pipeline:

    Abstract          — bibliographic record for a single study
    PICOCriteria      — eligibility framework (Population · Intervention ·
                        Comparison · Outcome + study types)
    ScreeningDecision — AI verdict for one abstract against the PICO criteria
    ScreeningBatch    — complete screening run with aggregate summary
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Enums ──────────────────────────────────────────────────────────────

class Decision(str, Enum):
    """Possible screening outcomes for an abstract."""

    INCLUDE = "include"
    """The abstract meets all PICO criteria and should proceed to full-text review."""

    EXCLUDE = "exclude"
    """The abstract fails one or more PICO criteria and is removed from the review."""

    UNCERTAIN = "uncertain"
    """The abstract cannot be decided from the abstract alone; full-text required."""


class Confidence(str, Enum):
    """Screener's confidence in a :class:`ScreeningDecision`."""

    HIGH = "high"
    """Clear match or mismatch — decision supported by explicit abstract content."""

    MEDIUM = "medium"
    """Probable decision but some ambiguity remains in the abstract."""

    LOW = "low"
    """Abstract is uninformative; decision is tentative and should be reviewed."""


# ── Sub-models ──────────────────────────────────────────────────────────

class PICOMatchItem(BaseModel):
    """Verdict for a single PICO component within a :class:`ScreeningDecision`.

    Used as the value type in :attr:`ScreeningDecision.pico_match`.
    """

    matched: bool = Field(
        ...,
        description=(
            "True if this PICO component is satisfied by the abstract. "
            "False if the component is absent, unclear, or contradicted."
        ),
    )
    note: str = Field(
        ...,
        description=(
            "Brief rationale for the verdict, quoting or paraphrasing the "
            "relevant abstract text where possible."
        ),
    )


# ── Constants used in validators ───────────────────────────────────────

_PICO_KEYS: frozenset[str] = frozenset({"population", "intervention", "comparison", "outcome"})
_MIN_YEAR: int = 1900
_MAX_YEAR: int = datetime.now(timezone.utc).year + 1


# ── Main models ─────────────────────────────────────────────────────────

class Abstract(BaseModel):
    """Bibliographic record and full abstract text for a single study.

    Sourced from PubMed or another literature database. All text fields
    are stripped of leading/trailing whitespace on ingestion.
    """

    pmid: str = Field(
        ...,
        description="PubMed ID (PMID). Must be non-empty and unique within a batch.",
    )
    title: str = Field(
        ...,
        description="Full title of the publication as it appears in the database.",
    )
    abstract: str = Field(
        ...,
        description=(
            "Complete abstract text. Structured abstracts (Background / Methods / "
            "Results / Conclusions) are accepted as-is."
        ),
    )
    authors: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "Ordered author list in 'Surname Initials' format, "
            "e.g. ['Smith JA', 'Jones B']. At least one author required."
        ),
    )
    journal: str = Field(
        ...,
        description="Full journal name or conference proceedings title.",
    )
    year: int = Field(
        ...,
        ge=_MIN_YEAR,
        le=_MAX_YEAR,
        description=f"Four-digit publication year ({_MIN_YEAR}–{_MAX_YEAR}).",
    )
    doi: Optional[str] = Field(
        None,
        description=(
            "Digital Object Identifier without the 'https://doi.org/' prefix, "
            "e.g. '10.1016/j.diabres.2023.01.001'. Optional."
        ),
    )
    keywords: Optional[list[str]] = Field(
        None,
        description=(
            "Author-supplied keywords or MeSH terms. Optional. "
            "Used to supplement abstract-based screening."
        ),
    )

    # ── Validators ──────────────────────────────────────────────────────

    @field_validator("pmid", "title", "abstract", "journal", mode="before")
    @classmethod
    def strip_and_require(cls, v: str) -> str:
        """Strip whitespace and reject empty strings."""
        if not isinstance(v, str):
            raise ValueError("Must be a string")
        v = v.strip()
        if not v:
            raise ValueError("Must not be empty")
        return v

    @field_validator("authors", mode="before")
    @classmethod
    def strip_authors(cls, v: list) -> list[str]:
        """Strip each author string and drop blank entries."""
        cleaned = [a.strip() for a in v if isinstance(a, str) and a.strip()]
        if not cleaned:
            raise ValueError("At least one non-empty author name required")
        return cleaned

    # ── Helpers ─────────────────────────────────────────────────────────

    def short_citation(self) -> str:
        """Return a compact citation string suitable for display in tables.

        Format:
            'Smith JA et al. (2023) Lancet Diabetes Endocrinol'
            'Jones B (2021) BMJ'
        """
        lead = self.authors[0]
        suffix = " et al." if len(self.authors) > 1 else ""
        return f"{lead}{suffix} ({self.year}) {self.journal}"

    def has_keyword(self, term: str) -> bool:
        """Return True if *term* (case-insensitive) appears in any keyword.

        Returns False when :attr:`keywords` is None.
        """
        if not self.keywords:
            return False
        term_lower = term.lower()
        return any(term_lower in kw.lower() for kw in self.keywords)


class PICOCriteria(BaseModel):
    """PICO eligibility framework used to screen abstracts.

    PICO (Population · Intervention · Comparison · Outcome) is the standard
    clinical research question structure. Study types and explicit exclusion
    criteria are also captured here because they are evaluated at the same
    screening stage.

    This model is shared across all :class:`ScreeningDecision` objects in a
    :class:`ScreeningBatch` — it represents the review protocol, not a
    per-abstract assessment.
    """

    population: str = Field(
        ...,
        description=(
            "Target patient population including key demographic and clinical "
            "characteristics, e.g. 'Adults aged ≥18 with type 2 diabetes and "
            "HbA1c ≥58 mmol/mol in a UK NHS setting'."
        ),
    )
    intervention: str = Field(
        ...,
        description=(
            "Technology or intervention under evaluation. Be specific enough to "
            "distinguish from similar interventions, e.g. 'Remote continuous glucose "
            "monitoring (CGM) device with clinician-facing dashboard'."
        ),
    )
    comparison: str = Field(
        ...,
        description=(
            "Comparator arm. Use 'Any' when no specific comparator is required, "
            "or describe explicitly, e.g. 'Self-monitored blood glucose (SMBG) "
            "or standard care'."
        ),
    )
    outcomes: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "Primary and secondary outcomes of interest. At least one required. "
            "Examples: ['HbA1c reduction ≥10 mmol/mol', 'Quality of life (EQ-5D)', "
            "'Cost per QALY gained', 'Time in range', 'Hypoglycaemia rate']."
        ),
    )
    study_types: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "Eligible study designs. At least one required. "
            "Examples: ['RCT', 'Quasi-experimental', 'Cohort study', "
            "'Economic evaluation', 'Systematic review', 'Cost-effectiveness analysis']."
        ),
    )
    exclusion_criteria: Optional[list[str]] = Field(
        None,
        description=(
            "Explicit reasons to exclude even when PICO appears satisfied. "
            "Examples: ['Paediatric population only (age <18)', "
            "'Conference abstract without full methods', 'Non-English language', "
            "'Follow-up < 6 months']."
        ),
    )

    # ── Validators ──────────────────────────────────────────────────────

    @field_validator("population", "intervention", "comparison", mode="before")
    @classmethod
    def strip_and_require(cls, v: str) -> str:
        """Strip whitespace and reject empty strings."""
        if not isinstance(v, str):
            raise ValueError("Must be a string")
        v = v.strip()
        if not v:
            raise ValueError("Must not be empty")
        return v

    @field_validator("outcomes", "study_types", mode="before")
    @classmethod
    def non_empty_list(cls, v: list) -> list[str]:
        """Strip list items and ensure at least one non-empty entry survives."""
        items = [s.strip() for s in v if isinstance(s, str) and s.strip()]
        if not items:
            raise ValueError("Must contain at least one non-empty item")
        return items

    # ── Helpers ─────────────────────────────────────────────────────────

    def to_prompt_text(self) -> str:
        """Format the PICO criteria as structured text for injection into an LLM prompt.

        Returns a multi-line string that a screener agent can use verbatim in
        its system or user prompt to communicate the eligibility framework.

        Example output::

            PICO Eligibility Criteria
            ────────────────────────────────────────
            Population   : Adults aged ≥18 with type 2 diabetes
            Intervention : Remote continuous glucose monitoring
            Comparison   : Any
            Outcomes     : HbA1c reduction, Quality of life
            Study types  : RCT, Economic evaluation
            Exclusions   : Paediatric populations only
        """
        lines = [
            "PICO Eligibility Criteria",
            "─" * 40,
            f"Population   : {self.population}",
            f"Intervention : {self.intervention}",
            f"Comparison   : {self.comparison}",
            f"Outcomes     : {', '.join(self.outcomes)}",
            f"Study types  : {', '.join(self.study_types)}",
        ]
        if self.exclusion_criteria:
            lines.append(f"Exclusions   : {'; '.join(self.exclusion_criteria)}")
        return "\n".join(lines)


class ScreeningDecision(BaseModel):
    """AI-generated screening verdict for a single abstract.

    Produced by the screener agent for each :class:`Abstract` in a
    :class:`ScreeningBatch`. The :attr:`pico_match` dict records the verdict
    for each PICO component individually, allowing downstream analysis of
    which criteria drive most exclusions.
    """

    pmid: str = Field(
        ...,
        description="PubMed ID of the abstract being screened. Must match an Abstract.pmid.",
    )
    decision: Decision = Field(
        ...,
        description=(
            "Overall screening verdict. "
            "'include' — meets all active PICO criteria. "
            "'exclude' — fails one or more criteria. "
            "'uncertain' — abstract alone is insufficient; full text required."
        ),
    )
    confidence: Confidence = Field(
        ...,
        description=(
            "Screener's confidence in the decision. "
            "'high' — unambiguous abstract; "
            "'medium' — probable verdict but some ambiguity; "
            "'low' — abstract is sparse or contradictory."
        ),
    )
    reasoning: str = Field(
        ...,
        description=(
            "Narrative explanation of the decision. Should reference specific "
            "phrases from the abstract and identify which PICO components are "
            "met or unmet. Must be non-empty."
        ),
    )
    pico_match: dict[str, PICOMatchItem] = Field(
        ...,
        description=(
            "Per-component PICO verdict. Must contain exactly the keys "
            "'population', 'intervention', 'comparison', and 'outcome'. "
            "Each value is a PICOMatchItem with 'matched' (bool) and 'note' (str)."
        ),
    )
    exclusion_reasons: list[str] = Field(
        default_factory=list,
        description=(
            "Specific reasons for exclusion when decision is 'exclude'. "
            "Examples: ['Wrong population: children only (mean age 9 years)', "
            "'No relevant clinical outcome reported']. "
            "Empty list for 'include' or 'uncertain' decisions."
        ),
    )
    reviewer: str = Field(
        default="AI-Claude",
        description=(
            "Identifier of the reviewer who made this decision. "
            "Use 'AI-Claude' for automated screening, or a human reviewer's "
            "initials / username for manual override."
        ),
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the decision was recorded.",
    )

    # ── Validators ──────────────────────────────────────────────────────

    @field_validator("pmid", "reasoning", mode="before")
    @classmethod
    def strip_and_require(cls, v: str) -> str:
        """Strip whitespace and reject empty strings."""
        if not isinstance(v, str):
            raise ValueError("Must be a string")
        v = v.strip()
        if not v:
            raise ValueError("Must not be empty")
        return v

    @field_validator("pico_match", mode="before")
    @classmethod
    def validate_pico_match_keys(cls, v: dict) -> dict:
        """Enforce that pico_match has exactly the four required PICO keys."""
        if not isinstance(v, dict):
            raise ValueError("pico_match must be a dict")
        missing = _PICO_KEYS - set(v.keys())
        if missing:
            raise ValueError(
                f"pico_match is missing required keys: {sorted(missing)}. "
                f"Required: {sorted(_PICO_KEYS)}"
            )
        extra = set(v.keys()) - _PICO_KEYS
        if extra:
            raise ValueError(
                f"pico_match has unexpected keys: {sorted(extra)}. "
                f"Allowed: {sorted(_PICO_KEYS)}"
            )
        return v

    @model_validator(mode="after")
    def check_exclusion_consistency(self) -> ScreeningDecision:
        """Verify that excluded decisions carry at least one exclusion reason.

        This is a soft validation — the model is still constructed if the list
        is empty, but the reasoning field should compensate. A future strict
        mode could make this a hard error.
        """
        if self.decision == Decision.EXCLUDE and not self.exclusion_reasons:
            # Populate from reasoning as a fallback so downstream consumers
            # always have something to display even if the agent omitted the list.
            if self.reasoning:
                self.exclusion_reasons = [self.reasoning[:200]]
        return self

    # ── Computed properties ──────────────────────────────────────────────

    @property
    def is_included(self) -> bool:
        """True if and only if :attr:`decision` is ``Decision.INCLUDE``."""
        return self.decision == Decision.INCLUDE

    @property
    def pico_match_score(self) -> int:
        """Number of PICO components that matched (integer 0–4).

        A score of 4 means all components are satisfied. Used by
        :class:`ScreeningBatch` to compute :attr:`~ScreeningBatch.summary`
        ``mean_pico_score``.
        """
        return sum(1 for item in self.pico_match.values() if item.matched)


class ScreeningBatch(BaseModel):
    """A complete abstract-screening run.

    Bundles the PICO criteria, the abstracts that were evaluated, and the
    resulting decisions. The :attr:`summary` dict is automatically computed
    from :attr:`decisions` on construction and can be refreshed at any time
    via :meth:`recompute_summary`.

    Typical lifecycle::

        batch = ScreeningBatch(pico_criteria=pico, abstracts=abstract_list)
        for abstract in batch.abstracts:
            decision = screener.screen(abstract, batch.pico_criteria)
            batch.add_decision(decision)
        print(batch.summary)
    """

    batch_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description=(
            "Unique identifier for this screening run. "
            "Auto-generated UUID v4 if not supplied. "
            "Use a meaningful slug (e.g. 'diabetes-cgm-2024-q1') for human readability."
        ),
    )
    pico_criteria: PICOCriteria = Field(
        ...,
        description=(
            "The PICO eligibility framework applied uniformly to every abstract "
            "in this batch. Shared by all :class:`ScreeningDecision` objects."
        ),
    )
    abstracts: list[Abstract] = Field(
        default_factory=list,
        description=(
            "Abstracts submitted for screening in this batch. "
            "May be empty when decisions are appended incrementally."
        ),
    )
    decisions: list[ScreeningDecision] = Field(
        default_factory=list,
        description=(
            "One decision per abstract. Ordering need not match :attr:`abstracts`. "
            "Populated incrementally as the screener processes each abstract."
        ),
    )
    summary: dict = Field(
        default_factory=dict,
        description=(
            "Aggregate statistics computed from :attr:`decisions`. Keys: "
            "``total``, ``included``, ``excluded``, ``uncertain``, "
            "``inclusion_rate`` (float 0–1), ``mean_pico_score`` (float 0–4). "
            "Recomputed automatically on construction and after :meth:`add_decision`."
        ),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the batch was created.",
    )

    # ── Validators ──────────────────────────────────────────────────────

    @field_validator("batch_id", mode="before")
    @classmethod
    def require_batch_id(cls, v: str) -> str:
        """Reject blank batch_id strings."""
        if not isinstance(v, str) or not v.strip():
            raise ValueError("batch_id must be a non-empty string")
        return v.strip()

    @model_validator(mode="after")
    def populate_summary(self) -> ScreeningBatch:
        """Compute summary from decisions immediately after construction."""
        self.summary = self._compute_summary()
        return self

    # ── Internal helpers ─────────────────────────────────────────────────

    def _compute_summary(self) -> dict:
        """Build the summary dict from current :attr:`decisions`."""
        total = len(self.decisions)
        included = sum(1 for d in self.decisions if d.decision == Decision.INCLUDE)
        excluded = sum(1 for d in self.decisions if d.decision == Decision.EXCLUDE)
        uncertain = sum(1 for d in self.decisions if d.decision == Decision.UNCERTAIN)
        scores = [d.pico_match_score for d in self.decisions]
        return {
            "total": total,
            "included": included,
            "excluded": excluded,
            "uncertain": uncertain,
            "inclusion_rate": round(included / total, 4) if total else 0.0,
            "mean_pico_score": round(sum(scores) / total, 2) if total else 0.0,
        }

    # ── Public helpers ────────────────────────────────────────────────────

    def recompute_summary(self) -> None:
        """Recompute :attr:`summary` in-place after decisions have been modified.

        Call this if you mutate :attr:`decisions` directly rather than using
        :meth:`add_decision`.
        """
        self.summary = self._compute_summary()

    def add_decision(self, decision: ScreeningDecision) -> None:
        """Append a decision and refresh :attr:`summary`.

        Raises:
            ValueError: If a decision for the same PMID already exists in
                this batch.
        """
        existing_pmids = {d.pmid for d in self.decisions}
        if decision.pmid in existing_pmids:
            raise ValueError(
                f"A decision for PMID {decision.pmid!r} already exists in batch "
                f"{self.batch_id!r}. Use a fresh ScreeningDecision or remove the "
                "existing one first."
            )
        self.decisions.append(decision)
        self.recompute_summary()

    def included_decisions(self) -> list[ScreeningDecision]:
        """Return all decisions with ``decision == 'include'``."""
        return [d for d in self.decisions if d.decision == Decision.INCLUDE]

    def excluded_decisions(self) -> list[ScreeningDecision]:
        """Return all decisions with ``decision == 'exclude'``."""
        return [d for d in self.decisions if d.decision == Decision.EXCLUDE]

    def uncertain_decisions(self) -> list[ScreeningDecision]:
        """Return all decisions with ``decision == 'uncertain'``."""
        return [d for d in self.decisions if d.decision == Decision.UNCERTAIN]

    def get_abstract(self, pmid: str) -> Optional[Abstract]:
        """Return the :class:`Abstract` with the given *pmid*, or ``None``.

        Args:
            pmid: The PubMed ID to look up.

        Returns:
            The matching :class:`Abstract`, or ``None`` if not found.
        """
        for a in self.abstracts:
            if a.pmid == pmid:
                return a
        return None

    def get_decision(self, pmid: str) -> Optional[ScreeningDecision]:
        """Return the :class:`ScreeningDecision` for *pmid*, or ``None``."""
        for d in self.decisions:
            if d.pmid == pmid:
                return d
        return None

    def pending_pmids(self) -> list[str]:
        """Return PMIDs of abstracts that have not yet received a decision."""
        decided = {d.pmid for d in self.decisions}
        return [a.pmid for a in self.abstracts if a.pmid not in decided]
