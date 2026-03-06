"""
PubMed agent: search PubMed and extract clinical/economic data using Claude.

Dependencies: biopython, anthropic
Install: pip install biopython anthropic
"""

import json
import logging
import os
import statistics
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data type configurations
# ---------------------------------------------------------------------------

DATA_TYPE_DESCRIPTIONS = {
    "mortality": "mortality rates, death rates, survival rates (e.g., 30-day mortality, in-hospital mortality, 1-year survival)",
    "los": "length of stay in hospital or ICU (e.g., ICU LOS, hospital LOS, days on ventilator)",
    "costs": "healthcare costs, resource utilization, economic burden (e.g., hospitalization cost, ICU cost, total treatment cost)",
    "qol": "quality of life, utility values, QALY, patient-reported outcomes (e.g., EQ-5D, SF-36, utility scores)",
    "readmissions": "readmission rates, rehospitalization, return visits (e.g., 30-day readmission, 90-day readmission)",
}


# ---------------------------------------------------------------------------
# PubMedAgent class
# ---------------------------------------------------------------------------


class PubMedAgent:
    """Agent for searching PubMed and extracting clinical/economic data."""

    def __init__(
        self,
        entrez_email: Optional[str] = None,
        anthropic_api_key: Optional[str] = None,
        claude_model: str = "claude-sonnet-4-6",
    ):
        self.entrez_email = entrez_email or os.environ.get("ENTREZ_EMAIL", "heor@example.com")
        self.anthropic_api_key = anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.claude_model = claude_model
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
                    "anthropic package is required. Install with: pip install anthropic"
                ) from exc
            self._anthropic_client = anthropic.Anthropic(api_key=self.anthropic_api_key)
        return self._anthropic_client

    # ------------------------------------------------------------------
    # 1. search_pubmed
    # ------------------------------------------------------------------

    def search_pubmed(self, query: str, max_results: int = 20) -> list[dict]:
        """Search PubMed and return article metadata with abstracts.

        Args:
            query: Natural language search query (e.g. "sepsis AI prediction mortality").
            max_results: Maximum number of articles to retrieve (default 20).

        Returns:
            List of dicts with keys: pmid, title, abstract, authors, journal, year.
        """
        try:
            from Bio import Entrez  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "biopython package is required. Install with: pip install biopython"
            ) from exc

        Entrez.email = self.entrez_email

        # --- Search for PMIDs ---
        logger.info("Searching PubMed for: %s (max %d results)", query, max_results)
        try:
            handle = Entrez.esearch(db="pubmed", term=query, retmax=max_results, sort="relevance")
            search_results = Entrez.read(handle)
            handle.close()
        except Exception as exc:
            logger.error("PubMed search failed: %s", exc)
            raise RuntimeError(f"PubMed search failed: {exc}") from exc

        pmid_list = search_results.get("IdList", [])
        if not pmid_list:
            logger.warning("No results found for query: %s", query)
            return []

        logger.info("Found %d PMIDs, fetching details...", len(pmid_list))

        # --- Fetch article details (with retry for rate limits) ---
        articles = []
        for attempt in range(3):
            try:
                time.sleep(0.35)  # Respect NCBI rate limit (3 req/sec for unregistered)
                handle = Entrez.efetch(
                    db="pubmed",
                    id=",".join(pmid_list),
                    rettype="xml",
                    retmode="xml",
                )
                records = Entrez.read(handle)
                handle.close()
                break
            except Exception as exc:
                if attempt == 2:
                    logger.error("Failed to fetch article details after 3 attempts: %s", exc)
                    raise RuntimeError(f"PubMed fetch failed: {exc}") from exc
                wait = 2 ** attempt
                logger.warning("Rate limit or error (attempt %d), retrying in %ds: %s", attempt + 1, wait, exc)
                time.sleep(wait)

        # --- Parse records ---
        for record in records.get("PubmedArticle", []):
            try:
                article = self._parse_pubmed_record(record)
                articles.append(article)
            except Exception as exc:
                logger.warning("Failed to parse record: %s", exc)
                continue

        logger.info("Successfully parsed %d articles", len(articles))
        return articles

    def _parse_pubmed_record(self, record: dict) -> dict:
        """Extract relevant fields from a PubMed XML record."""
        medline = record.get("MedlineCitation", {})
        article = medline.get("Article", {})

        # PMID
        pmid = str(medline.get("PMID", ""))

        # Title
        title = str(article.get("ArticleTitle", ""))

        # Abstract
        abstract_obj = article.get("Abstract", {})
        abstract_texts = abstract_obj.get("AbstractText", [])
        if isinstance(abstract_texts, list):
            abstract = " ".join(str(t) for t in abstract_texts)
        else:
            abstract = str(abstract_texts)

        # Authors
        author_list = article.get("AuthorList", [])
        authors = []
        for author in author_list:
            last = author.get("LastName", "")
            initials = author.get("Initials", "")
            if last:
                authors.append(f"{last} {initials}".strip())
        if len(authors) > 6:
            authors = authors[:6] + ["et al."]

        # Journal and year
        journal_info = article.get("Journal", {})
        journal = str(journal_info.get("Title", ""))
        journal_issue = journal_info.get("JournalIssue", {})
        pub_date = journal_issue.get("PubDate", {})
        year = str(pub_date.get("Year", pub_date.get("MedlineDate", "")[:4] if pub_date.get("MedlineDate") else ""))

        return {
            "pmid": pmid,
            "title": title,
            "abstract": abstract,
            "authors": authors,
            "journal": journal,
            "year": year,
        }

    # ------------------------------------------------------------------
    # 2. extract_clinical_data
    # ------------------------------------------------------------------

    def extract_clinical_data(self, abstracts: list[dict], data_type: str) -> dict:
        """Use Claude to extract specific clinical/economic data from abstracts.

        Args:
            abstracts: List of article dicts (from search_pubmed).
            data_type: One of "mortality", "los", "costs", "qol", "readmissions".

        Returns:
            Dict with keys: data_type, extractions (list), failed_pmids (list).
        """
        if data_type not in DATA_TYPE_DESCRIPTIONS:
            raise ValueError(
                f"Unknown data_type '{data_type}'. "
                f"Choose from: {', '.join(DATA_TYPE_DESCRIPTIONS)}"
            )

        if not abstracts:
            return {"data_type": data_type, "extractions": [], "failed_pmids": []}

        # Filter to abstracts that have actual text
        usable = [a for a in abstracts if a.get("abstract", "").strip()]
        no_abstract_pmids = [a["pmid"] for a in abstracts if not a.get("abstract", "").strip()]
        if no_abstract_pmids:
            logger.warning("Skipping %d articles with no abstract", len(no_abstract_pmids))

        if not usable:
            return {"data_type": data_type, "extractions": [], "failed_pmids": no_abstract_pmids}

        data_description = DATA_TYPE_DESCRIPTIONS[data_type]

        # Build formatted abstracts block
        abstracts_text = "\n\n".join(
            f"[PMID: {a['pmid']}] {a.get('title', 'No title')}\n"
            f"Authors: {', '.join(a.get('authors', []))}\n"
            f"Journal: {a.get('journal', '')} ({a.get('year', '')})\n"
            f"Abstract: {a['abstract']}"
            for a in usable
        )

        prompt = f"""Read these PubMed abstracts and extract {data_type} data ({data_description}).

For each abstract, identify:
- The specific outcome measure (e.g., "30-day mortality", "ICU length of stay")
- The numerical value or range
- The comparator (control vs intervention)
- The study design (RCT, cohort, observational, meta-analysis, etc.)
- Your confidence in the extraction (high/medium/low)

Return a JSON array. Each element should be:
{{
  "pmid": "12345",
  "outcome": "30-day mortality",
  "intervention_value": 0.15,
  "control_value": 0.25,
  "reduction": 0.40,
  "reduction_type": "relative",
  "unit": "%",
  "study_design": "RCT",
  "sample_size": 500,
  "confidence": "high",
  "quote": "exact text from abstract supporting this extraction",
  "notes": "any caveats or clarifications"
}}

Rules:
- Use null for fields that are not reported or cannot be inferred.
- If the abstract does not contain {data_type} data, set confidence to "low" and explain in notes.
- reduction_type must be "relative" (e.g., RRR, HR) or "absolute" (e.g., ARR, percentage points).
- intervention_value and control_value should be raw proportions (0–1) for rates/probabilities, or actual values with units for LOS/costs.
- Only return the JSON array, no other text.

Abstracts:
{abstracts_text}"""

        logger.info("Sending %d abstracts to Claude for %s extraction", len(usable), data_type)

        try:
            response = self.anthropic_client.messages.create(
                model=self.claude_model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
        except Exception as exc:
            logger.error("Claude API call failed during extraction: %s", exc)
            raise RuntimeError(f"Claude extraction failed: {exc}") from exc

        # Parse JSON response
        extractions, failed_pmids = self._parse_extraction_response(raw, usable)

        return {
            "data_type": data_type,
            "extractions": extractions,
            "failed_pmids": no_abstract_pmids + failed_pmids,
        }

    def _parse_extraction_response(self, raw: str, usable: list[dict]) -> tuple[list, list]:
        """Parse Claude's JSON extraction response."""
        # Strip markdown code fences if present
        clean = raw
        if clean.startswith("```"):
            lines = clean.splitlines()
            # Drop first and last fence lines
            clean = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            parsed = json.loads(clean)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse Claude extraction response as JSON: %s\nRaw: %s", exc, raw[:500])
            return [], [a["pmid"] for a in usable]

        if not isinstance(parsed, list):
            parsed = [parsed]

        valid_pmids = {a["pmid"] for a in usable}
        extractions = []
        failed_pmids = []

        for item in parsed:
            pmid = str(item.get("pmid", ""))
            if pmid in valid_pmids:
                extractions.append(item)
            else:
                logger.warning("Extraction returned unknown PMID: %s", pmid)

        # PMIDs in usable but not in extractions → failed
        extracted_pmids = {str(e.get("pmid", "")) for e in extractions}
        for a in usable:
            if a["pmid"] not in extracted_pmids:
                failed_pmids.append(a["pmid"])

        return extractions, failed_pmids

    # ------------------------------------------------------------------
    # 3. synthesize_evidence
    # ------------------------------------------------------------------

    def synthesize_evidence(self, extractions: dict) -> dict:
        """Synthesize evidence across multiple extracted papers using Claude.

        Args:
            extractions: Output from extract_clinical_data (dict with 'extractions' list).

        Returns:
            Structured synthesis with ranges, heterogeneity, and recommendations.
        """
        items = extractions.get("extractions", []) if isinstance(extractions, dict) else extractions
        data_type = extractions.get("data_type", "outcome") if isinstance(extractions, dict) else "outcome"

        if not items:
            return {
                data_type: {
                    "median": None,
                    "range": [None, None],
                    "n_studies": 0,
                    "heterogeneity": "unknown",
                    "recommendation": "No data available",
                },
                "evidence_quality": "insufficient (0 studies)",
            }

        # Compute basic statistics from high/medium confidence extractions
        high_conf = [e for e in items if e.get("confidence") in ("high", "medium")]
        reductions = [
            e["reduction"]
            for e in high_conf
            if isinstance(e.get("reduction"), (int, float))
        ]

        stats: dict = {}
        if reductions:
            median_val = statistics.median(reductions)
            min_val = min(reductions)
            max_val = max(reductions)
            stats = {
                "median": round(median_val, 4),
                "range": [round(min_val, 4), round(max_val, 4)],
                "n_high_medium_confidence": len(reductions),
            }

        # Count study designs
        design_counts: dict[str, int] = {}
        for e in items:
            design = e.get("study_design") or "unknown"
            design_counts[design] = design_counts.get(design, 0) + 1

        # Total sample size
        total_n = sum(
            e.get("sample_size") or 0
            for e in items
            if isinstance(e.get("sample_size"), (int, float))
        )

        # Build prompt for Claude synthesis
        extractions_json = json.dumps(items, indent=2)

        prompt = f"""You are a health economics expert conducting a systematic evidence synthesis.

Below are structured data extractions from {len(items)} PubMed abstracts about {data_type}.

Extractions:
{extractions_json}

Preliminary statistics computed from high/medium-confidence extractions:
{json.dumps(stats, indent=2)}

Study design breakdown: {json.dumps(design_counts, indent=2)}
Total sample size across studies: {total_n if total_n else "unknown"}

Please synthesize this evidence and return a JSON object with this structure:
{{
  "{data_type}_reduction": {{
    "median": <number or null>,
    "range": [<min>, <max>],
    "n_studies": <int>,
    "heterogeneity": "low|moderate|high|unknown",
    "recommendation": "Use X in base case, Y–Z in sensitivity analysis"
  }},
  "evidence_quality": "<brief summary, e.g. 'moderate (3 RCTs, 2 cohort studies, total n=1,234)'>",
  "key_findings": ["<bullet 1>", "<bullet 2>", ...],
  "outliers": ["<pmid or description of outlier>"],
  "limitations": "<brief summary of evidence limitations>",
  "heterogeneity_drivers": "<what explains variability across studies>"
}}

Guidelines:
- Identify and flag outliers (studies >2 SD from mean or with very different results).
- Assess heterogeneity based on variance in reported values and study designs.
- Recommendations should be actionable for building a health economic model.
- Only return the JSON object, no other text.
"""

        logger.info("Sending %d extractions to Claude for synthesis", len(items))

        try:
            response = self.anthropic_client.messages.create(
                model=self.claude_model,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
        except Exception as exc:
            logger.error("Claude API call failed during synthesis: %s", exc)
            raise RuntimeError(f"Claude synthesis failed: {exc}") from exc

        # Parse JSON response
        clean = raw
        if clean.startswith("```"):
            lines = clean.splitlines()
            clean = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            synthesis = json.loads(clean)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse Claude synthesis response: %s\nRaw: %s", exc, raw[:500])
            # Return a minimal fallback with the statistics we computed
            synthesis = {
                f"{data_type}_reduction": {
                    **stats,
                    "n_studies": len(items),
                    "heterogeneity": "unknown",
                    "recommendation": "Manual review required — Claude synthesis parsing failed",
                },
                "evidence_quality": f"{len(items)} studies found (synthesis parsing error)",
                "key_findings": [],
                "outliers": [],
                "limitations": "Synthesis parsing failed",
                "heterogeneity_drivers": "Unknown",
            }

        return synthesis


# ---------------------------------------------------------------------------
# Module-level convenience functions (matching the functional API in the spec)
# ---------------------------------------------------------------------------


def search_pubmed(query: str, max_results: int = 20) -> list[dict]:
    """Module-level wrapper around PubMedAgent.search_pubmed."""
    return PubMedAgent().search_pubmed(query, max_results)


def extract_clinical_data(abstracts: list[dict], data_type: str) -> dict:
    """Module-level wrapper around PubMedAgent.extract_clinical_data."""
    return PubMedAgent().extract_clinical_data(abstracts, data_type)


def synthesize_evidence(extractions: list[dict] | dict) -> dict:
    """Module-level wrapper around PubMedAgent.synthesize_evidence."""
    return PubMedAgent().synthesize_evidence(extractions)


# ---------------------------------------------------------------------------
# CLI demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="PubMed HEOR evidence agent")
    parser.add_argument("query", help="PubMed search query")
    parser.add_argument("--data-type", default="mortality", choices=list(DATA_TYPE_DESCRIPTIONS))
    parser.add_argument("--max-results", type=int, default=10)
    parser.add_argument("--output", help="Save synthesis to JSON file")
    args = parser.parse_args()

    agent = PubMedAgent()

    print(f"\nSearching PubMed: '{args.query}' (max {args.max_results} results)...")
    articles = agent.search_pubmed(args.query, max_results=args.max_results)
    print(f"Found {len(articles)} articles\n")

    print(f"Extracting '{args.data_type}' data...")
    extraction_result = agent.extract_clinical_data(articles, args.data_type)
    n_extracted = len(extraction_result.get("extractions", []))
    print(f"Extracted data from {n_extracted} abstracts\n")

    print("Synthesizing evidence...")
    synthesis = agent.synthesize_evidence(extraction_result)

    print("\n=== SYNTHESIS ===")
    print(json.dumps(synthesis, indent=2))

    if args.output:
        with open(args.output, "w") as f:
            json.dump(
                {
                    "query": args.query,
                    "data_type": args.data_type,
                    "n_articles": len(articles),
                    "extractions": extraction_result,
                    "synthesis": synthesis,
                },
                f,
                indent=2,
            )
        print(f"\nResults saved to {args.output}")
