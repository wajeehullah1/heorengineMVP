"""
Auto-populate orchestrator: turns minimal user input into evidence-based BIA/Markov inputs.

This is the "magic" layer — it coordinates PubMedAgent, NICEAgent, and the
evidence agent, then uses Claude to synthesise gathered evidence into a
complete, validated BIA input form and Markov CEA parameters.

Dependencies: biopython, beautifulsoup4, requests, anthropic
Install: pip install biopython beautifulsoup4 requests anthropic

Sync usage:
    populator = AutoPopulator()
    result = populator.auto_populate_bia(user_input)
    bia_inputs = result["bia_inputs"]

Async usage:
    import asyncio
    result = await populator.async_auto_populate_bia(user_input)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Optional

from agents.pubmed_agent import PubMedAgent
from agents.nice_agent import NICEAgent
from agents.evidence_agent import (
    enrich_bia_inputs,
    fetch_nhs_reference_costs,
    fetch_ons_population_data,
    get_nice_comparators,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# BIA workforce roles typically involved in ICU/acute care pathways
_DEFAULT_WORKFORCE = [
    {"role": "Band 5 (Staff Nurse)", "minutes": 30, "frequency": "per patient"},
    {"role": "Band 6 (Senior Nurse/AHP)", "minutes": 20, "frequency": "per patient"},
]

_NICE_WTP_THRESHOLD = 20_000   # £/QALY standard NICE threshold
_NICE_DISCOUNT_RATE = 0.035    # 3.5% per NICE DSU TSD 2

# Maps "setting" text to BIAInputs NHSSetting enum value
_SETTING_MAP = {
    "acute": "Acute NHS Trust",
    "hospital": "Acute NHS Trust",
    "nhs trust": "Acute NHS Trust",
    "icu": "Acute NHS Trust",
    "icb": "ICB",
    "primary care": "Primary Care Network",
    "gp": "Primary Care Network",
}

# ---------------------------------------------------------------------------
# AutoPopulator
# ---------------------------------------------------------------------------


class AutoPopulator:
    """Orchestrates evidence gathering and auto-fills BIA/Markov input forms.

    Args:
        anthropic_api_key: Anthropic API key (defaults to ANTHROPIC_API_KEY env var).
        claude_model: Claude model ID to use for synthesis.
        entrez_email: Email for NCBI Entrez API (defaults to ENTREZ_EMAIL env var).
        max_pubmed_results: Max PubMed articles per search query.
        n_search_queries: Number of parallel PubMed search queries to generate.
    """

    def __init__(
        self,
        anthropic_api_key: Optional[str] = None,
        claude_model: str = "claude-sonnet-4-6",
        entrez_email: Optional[str] = None,
        max_pubmed_results: int = 15,
        n_search_queries: int = 4,
    ):
        self.anthropic_api_key = anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.claude_model = claude_model
        self.max_pubmed_results = max_pubmed_results
        self.n_search_queries = n_search_queries

        self._pubmed = PubMedAgent(
            entrez_email=entrez_email,
            anthropic_api_key=self.anthropic_api_key,
            claude_model=claude_model,
        )
        self._nice = NICEAgent(
            anthropic_api_key=self.anthropic_api_key,
            claude_model=claude_model,
        )
        self._anthropic_client = None

    # ------------------------------------------------------------------
    # Lazy-loaded Anthropic client
    # ------------------------------------------------------------------

    @property
    def anthropic_client(self):
        if self._anthropic_client is None:
            try:
                import anthropic  # noqa: PLC0415
            except ImportError as exc:
                raise ImportError(
                    "anthropic package required. Install with: pip install anthropic"
                ) from exc
            self._anthropic_client = anthropic.Anthropic(api_key=self.anthropic_api_key)
        return self._anthropic_client

    # ------------------------------------------------------------------
    # 1. auto_populate_bia
    # ------------------------------------------------------------------

    def auto_populate_bia(self, user_description: dict) -> dict:
        """Auto-populate a complete BIA input form from minimal user input.

        Orchestrates:
          Step 1  → Claude generates PubMed search queries
          Step 2  → Parallel: PubMed search + NICE search + NHS cost fetch + ONS population
          Step 3  → Claude extracts clinical data from PubMed abstracts
          Step 4  → Claude synthesises all evidence into BIA form fields

        Args:
            user_description: Minimal device/indication information.
                Required keys: device_name, indication.
                Optional keys: setting, device_cost, expected_benefits,
                               pricing_model, forecast_years, model_year.

        Returns:
            {
                "bia_inputs":        dict  — ready for BIAInputs(**result["bia_inputs"]),
                "evidence_sources":  list  — citations and data sources used,
                "confidence_scores": dict  — per-field confidence (high/medium/low),
                "assumptions":       list  — explicit assumptions made,
                "warnings":          list  — data quality or reasonableness warnings,
                "raw_evidence":      dict  — the gathered evidence (for audit),
            }
        """
        device_name = user_description.get("device_name", "AI Health Tool")
        indication = user_description.get("indication", "")
        setting = user_description.get("setting", "UK NHS Acute Trust")
        device_cost = user_description.get("device_cost", 0.0)
        expected_benefits = user_description.get("expected_benefits", "")

        logger.info("[AutoPopulator] Starting BIA auto-population for: %s / %s", device_name, indication)

        # ── Step 1: Generate search queries ──────────────────────────────
        logger.info("[Step 1] Generating PubMed search queries")
        queries = self._generate_search_queries(device_name, indication)
        logger.info("[Step 1] Generated %d queries: %s", len(queries), queries)

        # ── Step 2: Parallel evidence gathering ──────────────────────────
        logger.info("[Step 2] Gathering evidence in parallel")
        raw_evidence = self._gather_evidence_parallel(queries, indication, setting)
        logger.info(
            "[Step 2] Gathered: %d PubMed articles, %d NICE docs",
            len(raw_evidence.get("pubmed_articles", [])),
            len(raw_evidence.get("nice_guidance", [])),
        )

        # ── Step 3: Extract clinical data from abstracts ─────────────────
        logger.info("[Step 3] Extracting clinical data from abstracts")
        clinical_data = self._extract_clinical_data(
            raw_evidence.get("pubmed_articles", []),
            indication,
        )
        logger.info(
            "[Step 3] Extracted data: mortality=%d, los=%d, costs=%d, readmissions=%d extractions",
            len(clinical_data.get("mortality", {}).get("extractions", [])),
            len(clinical_data.get("los", {}).get("extractions", [])),
            len(clinical_data.get("costs", {}).get("extractions", [])),
            len(clinical_data.get("readmissions", {}).get("extractions", [])),
        )

        # ── Step 4: Synthesise evidence into BIA form fields ─────────────
        logger.info("[Step 4] Synthesising evidence into BIA inputs")
        synthesis = self._synthesise_bia_inputs(
            user_description=user_description,
            clinical_data=clinical_data,
            nice_comparators=raw_evidence.get("nice_comparators", {}),
            nhs_costs=raw_evidence.get("nhs_costs", {}),
            ons_population=raw_evidence.get("ons_population", {}),
            nice_guidance=raw_evidence.get("nice_guidance", []),
        )

        # ── Merge device_cost from user input (always trusted over evidence) ─
        if device_cost and device_cost > 0:
            synthesis["bia_inputs"]["price"] = float(device_cost)

        # ── Normalise setting enum ────────────────────────────────────────
        synthesis["bia_inputs"]["setting"] = self._normalise_setting(setting)

        # ── Apply model_year and forecast_years from user overrides ───────
        if "model_year" in user_description:
            synthesis["bia_inputs"]["model_year"] = int(user_description["model_year"])
        if "forecast_years" in user_description:
            synthesis["bia_inputs"]["forecast_years"] = int(user_description["forecast_years"])

        # ── Build evidence_sources list ───────────────────────────────────
        evidence_sources = self._build_evidence_sources(
            raw_evidence, clinical_data
        )

        logger.info(
            "[AutoPopulator] BIA auto-population complete. "
            "Confidence: %s, Warnings: %d, Assumptions: %d",
            synthesis.get("confidence_scores", {}).get("overall", "unknown"),
            len(synthesis.get("warnings", [])),
            len(synthesis.get("assumptions", [])),
        )

        return {
            "bia_inputs": synthesis["bia_inputs"],
            "evidence_sources": evidence_sources,
            "confidence_scores": synthesis.get("confidence_scores", {}),
            "assumptions": synthesis.get("assumptions", []),
            "warnings": synthesis.get("warnings", []),
            "raw_evidence": {
                "n_pubmed_articles": len(raw_evidence.get("pubmed_articles", [])),
                "n_nice_docs": len(raw_evidence.get("nice_guidance", [])),
                "search_queries": queries,
                "nice_comparators": raw_evidence.get("nice_comparators", {}),
                "nhs_costs_fetched": bool(raw_evidence.get("nhs_costs")),
                "ons_population_fetched": bool(raw_evidence.get("ons_population")),
            },
        }

    # ------------------------------------------------------------------
    # 2. auto_populate_markov
    # ------------------------------------------------------------------

    def auto_populate_markov(self, bia_inputs: dict, clinical_data: dict) -> dict:
        """Derive evidence-based Markov CEA parameters from BIA inputs and clinical data.

        Args:
            bia_inputs: BIA form dict (output of auto_populate_bia["bia_inputs"]).
            clinical_data: Clinical extraction dict (mortality, los, qol data).

        Returns:
            {
                "markov_inputs": dict — ready for MarkovInputs(**result["markov_inputs"]),
                "derivation_notes": list — how each parameter was derived,
                "confidence_scores": dict,
                "assumptions": list,
                "warnings": list,
            }
        """
        logger.info("[AutoPopulator] Deriving Markov parameters")

        device_name = bia_inputs.get("intervention_name", "Intervention")
        price = float(bia_inputs.get("price", 0))
        setup_cost = float(bia_inputs.get("setup_cost", 0))

        # Gather evidence summaries
        mortality_data = clinical_data.get("mortality", {})
        qol_data = clinical_data.get("qol", {})
        costs_data = clinical_data.get("costs", {})
        los_data = clinical_data.get("los", {})

        # Build evidence context for Claude
        evidence_context = {
            "mortality_extractions": mortality_data.get("extractions", [])[:10],
            "qol_extractions": qol_data.get("extractions", [])[:10],
            "costs_extractions": costs_data.get("extractions", [])[:5],
            "los_extractions": los_data.get("extractions", [])[:5],
        }

        prompt = f"""You are a senior UK health economist deriving Markov model parameters
for a NICE cost-effectiveness submission.

Device / intervention: {device_name}
Device annual cost: £{price:,.0f} per patient
Setup cost: £{setup_cost:,.0f} one-off

BIA inputs (current pathway):
{json.dumps({k: v for k, v in bia_inputs.items() if k not in ("workforce",)}, indent=2)}

Clinical evidence extractions:
{json.dumps(evidence_context, indent=2)}

Derive Markov model parameters for a 2-state (Alive → Dead) model
comparing standard care vs the intervention. Use NICE methodological guidance:
- Discount rate: 3.5% per annum
- Perspective: NHS and PSS
- Utility instrument: EQ-5D-3L preferred

Return a JSON object:
{{
  "intervention_name": "{device_name}",
  "time_horizon": <integer years, typically 5 for acute, 10 for chronic, lifetime for oncology>,
  "cycle_length": 1.0,
  "discount_rate": 0.035,
  "prob_death_standard": <annual mortality probability 0–1 for standard care>,
  "cost_standard_annual": <annual NHS cost per patient alive under standard care, £>,
  "utility_standard": <EQ-5D utility weight 0–1 for standard care>,
  "prob_death_treatment": <annual mortality probability 0–1 with intervention>,
  "cost_treatment_annual": <annual NHS cost per patient alive under treatment, £>,
  "cost_treatment_initial": <one-off upfront cost including device cost, £>,
  "utility_treatment": <EQ-5D utility weight 0–1 with intervention>,
  "derivation_notes": [
    "prob_death_standard: derived from X (source)",
    "utility_standard: derived from Y (source)",
    "..."
  ],
  "confidence_scores": {{
    "prob_death_standard": "high|medium|low",
    "cost_standard_annual": "high|medium|low",
    "utility_standard": "high|medium|low",
    "prob_death_treatment": "high|medium|low",
    "cost_treatment_annual": "high|medium|low",
    "utility_treatment": "high|medium|low",
    "overall": "high|medium|low"
  }},
  "assumptions": ["assumption 1", "assumption 2"],
  "warnings": ["warning 1 if any"]
}}

Rules:
- cost_treatment_annual = cost_standard_annual + device_annual_cost - annual_savings
- cost_treatment_initial = setup_cost (£{setup_cost:,.0f})
- prob_death_treatment must be less than prob_death_standard if the device improves mortality
- All utilities must be between 0 and 1
- If evidence is very weak, use conservative estimates and flag as low confidence
- Only return the JSON object.
"""

        logger.info("[AutoPopulator] Sending Markov derivation request to Claude")
        try:
            response = self.anthropic_client.messages.create(
                model=self.claude_model,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
        except Exception as exc:
            logger.error("Claude API failed for Markov derivation: %s", exc)
            raise RuntimeError(f"Markov derivation failed: {exc}") from exc

        parsed = _parse_json(raw)

        # Split out markov_inputs fields vs metadata
        markov_field_keys = {
            "intervention_name", "time_horizon", "cycle_length", "discount_rate",
            "prob_death_standard", "cost_standard_annual", "utility_standard",
            "prob_death_treatment", "cost_treatment_annual", "cost_treatment_initial",
            "utility_treatment",
        }
        markov_inputs = {k: v for k, v in parsed.items() if k in markov_field_keys}
        metadata = {k: v for k, v in parsed.items() if k not in markov_field_keys}

        logger.info(
            "[AutoPopulator] Markov derivation complete. "
            "prob_death_standard=%.3f, prob_death_treatment=%.3f, ICER estimate pending",
            markov_inputs.get("prob_death_standard", 0),
            markov_inputs.get("prob_death_treatment", 0),
        )

        return {
            "markov_inputs": markov_inputs,
            "derivation_notes": metadata.get("derivation_notes", []),
            "confidence_scores": metadata.get("confidence_scores", {}),
            "assumptions": metadata.get("assumptions", []),
            "warnings": metadata.get("warnings", []),
        }

    # ------------------------------------------------------------------
    # 3. validate_auto_population
    # ------------------------------------------------------------------

    def validate_auto_population(self, inputs: dict, evidence: dict) -> dict:
        """Use Claude to validate auto-populated values for reasonableness.

        Flags outliers, implausible values, and parameters that need user review.

        Args:
            inputs: Auto-populated BIA or Markov inputs dict.
            evidence: Evidence metadata (from auto_populate_bia["raw_evidence"] etc.).

        Returns:
            {
                "validation_status": "ok" | "needs_review" | "high_risk",
                "flags": list of str — specific concerns,
                "confidence": "high" | "medium" | "low",
                "recommended_overrides": dict — fields user should manually review,
                "summary": str,
            }
        """
        logger.info("[AutoPopulator] Validating auto-populated inputs")

        prompt = f"""You are a senior UK HEOR analyst reviewing auto-populated
health economic model inputs for a NICE submission.

Auto-populated inputs:
{json.dumps(inputs, indent=2)}

Evidence metadata used:
{json.dumps(evidence, indent=2)}

Review these inputs for:
1. Clinical plausibility (are mortality rates, utilities, LOS realistic for the condition?)
2. UK NHS cost reasonableness (are costs in plausible ranges for NHS tariffs?)
3. Population sizing (is eligible population %, uptake trajectory realistic?)
4. Technology adoption (is uptake trajectory realistic for a new device/AI tool?)
5. Resource savings (are staff time, LOS, complication reduction claims plausible?)
6. Evidence gaps (where was evidence weak or missing?)

NICE typical ranges for context:
- Standard care annual costs: £3,000–£50,000 depending on condition severity
- ICU cost/day: £1,500–£2,500; Ward cost/day: £300–£600
- Utility weights: 0.3–0.5 for severe acute illness; 0.6–0.8 for chronic managed conditions
- Mortality reduction for diagnostic tools: typically 5–20% (not >40%)
- LOS reduction for early detection tools: typically 0.5–3 days
- Year 1 uptake for NHS digital tools: typically 10–30%

Return a JSON object:
{{
  "validation_status": "ok|needs_review|high_risk",
  "flags": [
    "Mortality reduction of 40% exceeds typical evidence base for diagnostic tools — verify",
    "No direct cost data found — used NICE comparator estimates only",
    "..."
  ],
  "confidence": "high|medium|low",
  "recommended_overrides": {{
    "field_name": "Suggested value or range with rationale"
  }},
  "plausibility_scores": {{
    "mortality_reduction": "plausible|questionable|implausible",
    "cost_estimates": "plausible|questionable|implausible",
    "population_sizing": "plausible|questionable|implausible",
    "uptake_trajectory": "plausible|questionable|implausible",
    "resource_savings": "plausible|questionable|implausible"
  }},
  "summary": "One paragraph summary of the overall quality of the auto-population"
}}

validation_status guide:
- "ok": All values plausible, minor concerns only
- "needs_review": 1–3 significant concerns that user should verify before submitting
- "high_risk": >3 significant concerns or one critical implausibility

Only return the JSON object.
"""

        logger.info("[AutoPopulator] Sending validation request to Claude")
        try:
            response = self.anthropic_client.messages.create(
                model=self.claude_model,
                max_tokens=1536,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
        except Exception as exc:
            logger.error("Claude API failed for validation: %s", exc)
            return {
                "validation_status": "needs_review",
                "flags": [f"Validation step failed: {exc} — manual review required"],
                "confidence": "low",
                "recommended_overrides": {},
                "plausibility_scores": {},
                "summary": "Automated validation could not be completed.",
            }

        result = _parse_json(raw)
        logger.info(
            "[AutoPopulator] Validation complete. Status=%s, Flags=%d",
            result.get("validation_status", "unknown"),
            len(result.get("flags", [])),
        )
        return result

    # ------------------------------------------------------------------
    # Async wrappers (run sync methods in a thread pool)
    # ------------------------------------------------------------------

    async def async_auto_populate_bia(self, user_description: dict) -> dict:
        """Async wrapper for auto_populate_bia — runs in a thread pool executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.auto_populate_bia, user_description)

    async def async_auto_populate_markov(self, bia_inputs: dict, clinical_data: dict) -> dict:
        """Async wrapper for auto_populate_markov."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self.auto_populate_markov, bia_inputs, clinical_data
        )

    async def async_validate_auto_population(self, inputs: dict, evidence: dict) -> dict:
        """Async wrapper for validate_auto_population."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self.validate_auto_population, inputs, evidence
        )

    # ------------------------------------------------------------------
    # Private: Step 1 — generate search queries
    # ------------------------------------------------------------------

    def _generate_search_queries(self, device_name: str, indication: str) -> list[str]:
        """Use Claude to generate targeted PubMed search queries."""
        prompt = f"""Given this medical device/technology:
Device: {device_name}
Clinical indication: {indication}

Generate {self.n_search_queries} PubMed search queries to find:
1. Randomised controlled trials or prospective studies showing clinical efficacy
2. Real-world evidence or observational studies on outcomes in practice
3. Health economic evaluations or cost-effectiveness analyses
4. Length of stay, readmission or resource utilisation impact studies

Each query should be 5–10 words using MeSH terms or standard medical vocabulary.
Target high-yield searches that will return relevant results on PubMed.

Return a JSON array of strings only, e.g.:
["query 1", "query 2", "query 3", "query 4"]

Only return the JSON array.
"""
        try:
            response = self.anthropic_client.messages.create(
                model=self.claude_model,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            parsed = _parse_json(raw)
            if isinstance(parsed, list):
                return [str(q) for q in parsed if q][: self.n_search_queries]
        except Exception as exc:
            logger.warning("Query generation failed (%s) — using fallback queries", exc)

        # Fallback: simple derived queries
        base = f"{indication} clinical outcomes"
        return [
            base,
            f"{indication} length of stay hospital",
            f"{indication} artificial intelligence prediction",
            f"{indication} cost-effectiveness economic evaluation",
        ][: self.n_search_queries]

    # ------------------------------------------------------------------
    # Private: Step 2 — parallel evidence gathering
    # ------------------------------------------------------------------

    def _gather_evidence_parallel(
        self,
        queries: list[str],
        indication: str,
        setting: str,
    ) -> dict:
        """Run PubMed searches, NICE search, NHS costs, and ONS population in parallel."""
        results: dict[str, Any] = {
            "pubmed_articles": [],
            "nice_guidance": [],
            "nice_comparators": {},
            "nhs_costs": {},
            "ons_population": {},
        }

        tasks: dict[str, Any] = {}

        # One PubMed task per query + one combined deduplication step
        pubmed_tasks = {f"pubmed_{i}": q for i, q in enumerate(queries)}
        # NICE, NHS costs, ONS population
        other_tasks = {
            "nice_search": indication,
            "nice_comparators": indication,
            "nhs_costs": None,
            "ons_population": None,
        }
        tasks.update(pubmed_tasks)
        tasks.update(other_tasks)

        def _run_pubmed(query: str) -> list[dict]:
            try:
                return self._pubmed.search_pubmed(query, max_results=self.max_pubmed_results)
            except Exception as exc:
                logger.warning("PubMed search failed for '%s': %s", query, exc)
                return []

        def _run_nice_search(indication: str) -> list[dict]:
            try:
                return self._nice.search_nice_guidance(indication, "any")
            except Exception as exc:
                logger.warning("NICE search failed: %s", exc)
                return []

        def _run_nice_comparators(indication: str) -> dict:
            try:
                return self._nice.get_comparator_costs(indication)
            except Exception as exc:
                logger.warning("NICE comparator costs failed: %s", exc)
                return {}

        def _run_nhs_costs() -> dict:
            try:
                return fetch_nhs_reference_costs()
            except Exception as exc:
                logger.warning("NHS reference costs fetch failed: %s", exc)
                return {}

        def _run_ons_population() -> dict:
            try:
                return fetch_ons_population_data()
            except Exception as exc:
                logger.warning("ONS population fetch failed: %s", exc)
                return {}

        # Submit all tasks to thread pool
        futures: dict = {}
        with ThreadPoolExecutor(max_workers=8) as executor:
            for i, query in enumerate(queries):
                futures[executor.submit(_run_pubmed, query)] = f"pubmed_{i}"
            futures[executor.submit(_run_nice_search, indication)] = "nice_search"
            futures[executor.submit(_run_nice_comparators, indication)] = "nice_comparators"
            futures[executor.submit(_run_nhs_costs)] = "nhs_costs"
            futures[executor.submit(_run_ons_population)] = "ons_population"

            for future in as_completed(futures):
                task_name = futures[future]
                try:
                    task_result = future.result()
                except Exception as exc:
                    logger.warning("Task '%s' raised an exception: %s", task_name, exc)
                    task_result = [] if task_name.startswith("pubmed") else {}

                if task_name.startswith("pubmed_"):
                    results["pubmed_articles"].extend(task_result)
                elif task_name == "nice_search":
                    results["nice_guidance"] = task_result
                elif task_name == "nice_comparators":
                    results["nice_comparators"] = task_result
                elif task_name == "nhs_costs":
                    results["nhs_costs"] = task_result
                elif task_name == "ons_population":
                    results["ons_population"] = task_result

        # Deduplicate PubMed results by PMID
        seen_pmids: set[str] = set()
        unique_articles = []
        for article in results["pubmed_articles"]:
            pmid = article.get("pmid", "")
            if pmid and pmid not in seen_pmids:
                seen_pmids.add(pmid)
                unique_articles.append(article)
        results["pubmed_articles"] = unique_articles

        logger.info(
            "[gather_evidence] PubMed: %d unique articles, NICE: %d docs",
            len(results["pubmed_articles"]),
            len(results["nice_guidance"]),
        )
        return results

    # ------------------------------------------------------------------
    # Private: Step 3 — extract clinical data
    # ------------------------------------------------------------------

    def _extract_clinical_data(
        self,
        articles: list[dict],
        indication: str,
    ) -> dict:
        """Run parallel clinical data extraction across multiple data types."""
        if not articles:
            return {dt: {"extractions": [], "data_type": dt} for dt in
                    ("mortality", "los", "costs", "qol", "readmissions")}

        data_types = ["mortality", "los", "costs", "readmissions"]
        clinical_data: dict = {}

        def _extract(dt: str) -> tuple[str, dict]:
            try:
                result = self._pubmed.extract_clinical_data(articles, dt)
                logger.info(
                    "[extract_clinical_data] %s: %d extractions",
                    dt,
                    len(result.get("extractions", [])),
                )
                return dt, result
            except Exception as exc:
                logger.warning("Extraction failed for %s: %s", dt, exc)
                return dt, {"data_type": dt, "extractions": [], "failed_pmids": []}

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures_map = {executor.submit(_extract, dt): dt for dt in data_types}
            for future in as_completed(futures_map):
                dt, result = future.result()
                clinical_data[dt] = result

        return clinical_data

    # ------------------------------------------------------------------
    # Private: Step 4 — synthesise into BIA inputs
    # ------------------------------------------------------------------

    def _synthesise_bia_inputs(
        self,
        user_description: dict,
        clinical_data: dict,
        nice_comparators: dict,
        nhs_costs: dict,
        ons_population: dict,
        nice_guidance: list[dict],
    ) -> dict:
        """Use Claude to synthesise evidence into BIA form fields."""

        device_name = user_description.get("device_name", "")
        indication = user_description.get("indication", "")
        setting = user_description.get("setting", "UK NHS Acute Trust")
        device_cost = float(user_description.get("device_cost", 0))
        expected_benefits = user_description.get("expected_benefits", "")
        model_year = int(user_description.get("model_year", datetime.now().year))
        forecast_years = int(user_description.get("forecast_years", 3))

        # Summarise extractions concisely
        def _summarise_extractions(data: dict, n: int = 8) -> list[dict]:
            return [
                {k: v for k, v in e.items() if k in
                 ("pmid", "outcome", "intervention_value", "control_value",
                  "reduction", "reduction_type", "unit", "confidence", "notes")}
                for e in data.get("extractions", [])[:n]
                if e.get("confidence") in ("high", "medium")
            ]

        evidence_summary = {
            "mortality": _summarise_extractions(clinical_data.get("mortality", {})),
            "los_days": _summarise_extractions(clinical_data.get("los", {})),
            "costs": _summarise_extractions(clinical_data.get("costs", {})),
            "readmissions": _summarise_extractions(clinical_data.get("readmissions", {})),
            "nice_comparators": {
                k: nice_comparators.get(k)
                for k in ("icu_days_typical", "ward_days_typical",
                          "typical_cost_per_episode", "icu_cost_per_day",
                          "ward_cost_per_day", "standard_of_care")
                if nice_comparators.get(k)
            },
            "nice_guidance_titles": [g.get("title", "") for g in nice_guidance[:5]],
        }

        nice_setting = self._normalise_setting(setting)

        prompt = f"""You are a senior UK HEOR analyst auto-populating a Budget Impact Analysis
for a UK NHS submission. Use the evidence below to fill in realistic estimates.

Device / intervention: {device_name}
Clinical indication: {indication}
NHS setting: {nice_setting}
Device price: £{device_cost:,.0f} per patient
Expected benefits claimed by manufacturer: {expected_benefits}
Model start year: {model_year}
Forecast years: {forecast_years}

Evidence gathered from PubMed and NICE:
{json.dumps(evidence_summary, indent=2)}

Produce a JSON object that maps to the BIAInputs schema below.
Fill every field with your best evidence-based estimate.

Required BIAInputs schema:
{{
  "setting": "{nice_setting}",
  "model_year": {model_year},
  "forecast_years": {forecast_years},
  "funding_source": "Trust operational budget|ICB commissioning|Transformation / innovation funding|Capital budget|Industry-funded pilot|Research / grant|Unsure",
  "catchment_type": "population|beds",
  "catchment_size": <int: typical NHS acute trust catchment ~500,000 population, or ~400 beds>,
  "eligible_pct": <float 0–100: % of catchment who have the condition and are eligible>,
  "uptake_y1": <float 0–100: % of eligible patients receiving the device in Year 1>,
  "uptake_y2": <float 0–100>,
  "uptake_y3": <float 0–100>,
  "prevalence": "<free text: incidence/prevalence notes with source>",
  "workforce": [
    {{"role": "Band 5 (Staff Nurse)", "minutes": <int>, "frequency": "per patient"}},
    {{"role": "Band 6 (Senior Nurse/AHP)", "minutes": <int>, "frequency": "per patient"}}
  ],
  // IMPORTANT: role must be EXACTLY one of: "Band 2", "Band 3", "Band 4",
  // "Band 5 (Staff Nurse)", "Band 6 (Senior Nurse/AHP)", "Band 7 (Advanced Practitioner)",
  // "Band 8a (Consultant Nurse/Manager)", "Registrar", "Consultant", "Admin/Clerical"
  "outpatient_visits": <int per patient per year>,
  "tests": <int diagnostic tests per patient per year>,
  "admissions": <int admissions per patient per year>,
  "bed_days": <int bed days per admission>,
  "procedures": <int procedures per patient per year>,
  "consumables": <float £ consumables cost per patient>,
  "pricing_model": "per-patient|per-use|subscription|capital + consumables",
  "price": {device_cost if device_cost > 0 else "<estimate £>"},
  "price_unit": "per patient|per year|per use",
  "needs_training": <true|false>,
  "training_roles": "<e.g. 'Band 5 nurses, Band 6 senior nurses, registrars'>",
  "training_hours": <float hours per person>,
  "setup_cost": <float £ one-off setup cost>,
  "staff_time_saved": <float minutes saved per patient>,
  "visits_reduced": <float % reduction in visits/tests 0–100>,
  "complications_reduced": <float % reduction in complications 0–100>,
  "readmissions_reduced": <float % reduction in readmissions 0–100>,
  "los_reduced": <float days reduction in length of stay>,
  "follow_up_reduced": <float % reduction in follow-up visits 0–100>,
  "comparator": "none|digital|diagnostic|device",
  "comparator_names": "<named alternatives, e.g. 'Existing clinical scoring (NEWS, qSOFA)'>",
  "discounting": "off"
}}

Also return alongside the bia_inputs:
{{
  "bia_inputs": {{...above...}},
  "confidence_scores": {{
    "catchment_size": "high|medium|low",
    "eligible_pct": "high|medium|low",
    "uptake_trajectory": "high|medium|low",
    "current_pathway_costs": "high|medium|low",
    "resource_savings": "high|medium|low",
    "overall": "high|medium|low"
  }},
  "assumptions": [
    "Assumption 1 with rationale",
    "Assumption 2 with rationale"
  ],
  "warnings": [
    "Warning 1 — data quality concern",
    "Warning 2 — extrapolation applied"
  ]
}}

Guidelines:
- eligible_pct: for an ICU setting with sepsis, ~2–5% of hospital admissions per year
- uptake_y1 for new digital/AI tools: typically 15–25% of eligible patients
- uptake_y2: typically 40–60%; uptake_y3: typically 60–80%
- needs_training=true for AI/digital tools; training_hours 1–4 hours
- staff_time_saved: for AI decision support, typically 10–30 min per patient
- los_reduced: use evidence; if none, state assumption in assumptions
- complications_reduced / readmissions_reduced: use evidence if available
- comparator: "diagnostic" for AI diagnostic tools, "digital" for digital apps
- If evidence is limited for a field, give a conservative estimate and add a warning
- Only return the JSON object.
"""

        logger.info("[synthesise_bia] Sending synthesis request to Claude")
        try:
            response = self.anthropic_client.messages.create(
                model=self.claude_model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
        except Exception as exc:
            logger.error("Claude synthesis failed: %s", exc)
            raise RuntimeError(f"BIA synthesis failed: {exc}") from exc

        parsed = _parse_json(raw)

        # Handle either flat (just bia_inputs fields) or nested response
        if "bia_inputs" in parsed:
            return parsed
        else:
            # Claude returned flat fields — wrap them
            bia_keys = {
                "setting", "model_year", "forecast_years", "funding_source",
                "catchment_type", "catchment_size", "eligible_pct",
                "uptake_y1", "uptake_y2", "uptake_y3", "prevalence",
                "workforce", "outpatient_visits", "tests", "admissions",
                "bed_days", "procedures", "consumables", "pricing_model",
                "price", "price_unit", "needs_training", "training_roles",
                "training_hours", "setup_cost", "staff_time_saved",
                "visits_reduced", "complications_reduced", "readmissions_reduced",
                "los_reduced", "follow_up_reduced", "comparator",
                "comparator_names", "discounting",
            }
            bia_inputs = {k: v for k, v in parsed.items() if k in bia_keys}
            meta_keys = {"confidence_scores", "assumptions", "warnings"}
            meta = {k: parsed[k] for k in meta_keys if k in parsed}
            return {"bia_inputs": bia_inputs, **meta}

    # ------------------------------------------------------------------
    # Private: helpers
    # ------------------------------------------------------------------

    def _normalise_setting(self, setting: str) -> str:
        """Map free-text setting to a BIAInputs NHSSetting enum value."""
        lower = setting.lower()
        for key, value in _SETTING_MAP.items():
            if key in lower:
                return value
        return "Acute NHS Trust"

    def _build_evidence_sources(
        self, raw_evidence: dict, clinical_data: dict
    ) -> list[dict]:
        """Build a structured list of evidence sources used."""
        sources = []

        # PubMed articles
        for article in raw_evidence.get("pubmed_articles", [])[:20]:
            sources.append({
                "type": "PubMed",
                "pmid": article.get("pmid"),
                "title": article.get("title", ""),
                "authors": article.get("authors", []),
                "journal": article.get("journal", ""),
                "year": article.get("year", ""),
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{article.get('pmid')}/",
            })

        # NICE guidance
        for doc in raw_evidence.get("nice_guidance", [])[:10]:
            sources.append({
                "type": "NICE Guidance",
                "id": doc.get("id", ""),
                "title": doc.get("title", ""),
                "date": doc.get("date", ""),
                "url": doc.get("url", ""),
            })

        # NICE comparator costs
        if raw_evidence.get("nice_comparators"):
            sources.append({
                "type": "NICE Comparator Costs",
                "source": raw_evidence["nice_comparators"].get("source", "NICE guidance"),
                "condition": raw_evidence["nice_comparators"].get("condition", ""),
            })

        # NHS / ONS data
        if raw_evidence.get("nhs_costs"):
            sources.append({"type": "NHS Reference Costs", "source": "NHS England"})
        if raw_evidence.get("ons_population"):
            sources.append({"type": "ONS Population Data", "source": "Office for National Statistics"})

        return sources


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def auto_populate_bia(user_description: dict, **kwargs) -> dict:
    """Module-level wrapper. See AutoPopulator.auto_populate_bia."""
    return AutoPopulator(**kwargs).auto_populate_bia(user_description)


def auto_populate_markov(bia_inputs: dict, clinical_data: dict, **kwargs) -> dict:
    """Module-level wrapper. See AutoPopulator.auto_populate_markov."""
    return AutoPopulator(**kwargs).auto_populate_markov(bia_inputs, clinical_data)


def validate_auto_population(inputs: dict, evidence: dict, **kwargs) -> dict:
    """Module-level wrapper. See AutoPopulator.validate_auto_population."""
    return AutoPopulator(**kwargs).validate_auto_population(inputs, evidence)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_json(raw: str) -> dict | list:
    """Strip markdown code fences and parse JSON."""
    clean = raw.strip()
    if clean.startswith("```"):
        lines = clean.splitlines()
        end = -1 if lines[-1].strip() == "```" else len(lines)
        clean = "\n".join(lines[1:end])

    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        # Try to extract JSON object/array with regex
        m = re.search(r"(\{[\s\S]+\}|\[[\s\S]+\])", clean)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        logger.error("Could not parse Claude response as JSON. Raw: %.300s", raw)
        return {}


# ---------------------------------------------------------------------------
# CLI demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )

    parser = argparse.ArgumentParser(description="Auto-populate BIA/Markov from minimal user input")
    parser.add_argument("--device-name", default="AI Sepsis Prediction Tool")
    parser.add_argument("--indication", default="Sepsis in ICU patients")
    parser.add_argument("--setting", default="UK NHS Acute Trust")
    parser.add_argument("--device-cost", type=float, default=185.0)
    parser.add_argument("--expected-benefits", default="Earlier detection, faster treatment")
    parser.add_argument("--no-markov", action="store_true", help="Skip Markov derivation")
    parser.add_argument("--no-validate", action="store_true", help="Skip validation")
    parser.add_argument("--output", help="Save full results to JSON file")
    args = parser.parse_args()

    user_input = {
        "device_name": args.device_name,
        "indication": args.indication,
        "setting": args.setting,
        "device_cost": args.device_cost,
        "expected_benefits": args.expected_benefits,
    }

    populator = AutoPopulator()

    print(f"\n{'='*60}")
    print(f"Auto-populating BIA for: {args.device_name}")
    print(f"Indication: {args.indication}")
    print(f"{'='*60}\n")

    bia_result = populator.auto_populate_bia(user_input)

    print("\n=== BIA INPUTS ===")
    print(json.dumps(bia_result["bia_inputs"], indent=2))

    print(f"\n=== CONFIDENCE SCORES ===")
    print(json.dumps(bia_result["confidence_scores"], indent=2))

    print(f"\n=== ASSUMPTIONS ({len(bia_result['assumptions'])}) ===")
    for a in bia_result["assumptions"]:
        print(f"  • {a}")

    print(f"\n=== WARNINGS ({len(bia_result['warnings'])}) ===")
    for w in bia_result["warnings"]:
        print(f"  ⚠ {w}")

    print(f"\n=== EVIDENCE SOURCES ({len(bia_result['evidence_sources'])}) ===")
    for s in bia_result["evidence_sources"][:5]:
        print(f"  [{s['type']}] {s.get('title') or s.get('id') or s.get('source')}")
    if len(bia_result["evidence_sources"]) > 5:
        print(f"  ... and {len(bia_result['evidence_sources']) - 5} more")

    output: dict = {"user_input": user_input, "bia_result": bia_result}

    if not args.no_markov:
        print("\n=== DERIVING MARKOV PARAMETERS ===")
        # Use empty clinical_data if not separately fetched
        markov_result = populator.auto_populate_markov(
            bia_result["bia_inputs"],
            clinical_data={},
        )
        print(json.dumps(markov_result["markov_inputs"], indent=2))
        output["markov_result"] = markov_result

    if not args.no_validate:
        print("\n=== VALIDATING AUTO-POPULATION ===")
        validation = populator.validate_auto_population(
            bia_result["bia_inputs"],
            bia_result["raw_evidence"],
        )
        print(f"Status: {validation['validation_status'].upper()}")
        print(f"Confidence: {validation['confidence']}")
        for flag in validation.get("flags", []):
            print(f"  ⚑ {flag}")
        output["validation"] = validation

    if args.output:
        with open(args.output, "w") as f:
            json.dump(output, f, indent=2)
        print(f"\nFull results saved to {args.output}")
