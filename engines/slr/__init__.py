"""Systematic Literature Review (SLR) engine.

Provides abstract screening, deduplication, and data extraction tools
for health technology assessment literature reviews.

Public API
----------
Schema models (``engines.slr.schema``):

    Abstract          — bibliographic record for a single study
    PICOCriteria      — PICO eligibility framework
    PICOMatchItem     — per-component verdict within a ScreeningDecision
    ScreeningDecision — AI verdict for one abstract
    ScreeningBatch    — complete screening run with aggregate summary
    Decision          — enum: include / exclude / uncertain
    Confidence        — enum: high / medium / low
"""

from .schema import (
    Abstract,
    Confidence,
    Decision,
    PICOCriteria,
    PICOMatchItem,
    ScreeningBatch,
    ScreeningDecision,
)

__all__ = [
    "Abstract",
    "Confidence",
    "Decision",
    "PICOCriteria",
    "PICOMatchItem",
    "ScreeningBatch",
    "ScreeningDecision",
]
