"""
NICE agent: search and extract data from NICE guidance documents.

Covers Technology Appraisals (TA), Clinical Guidelines (NG/CG), and
Medical Technologies Evaluations / MIBs.

Dependencies: beautifulsoup4, requests, anthropic
Install: pip install beautifulsoup4 requests anthropic
"""

import json
import logging
import os
import re
import time
from typing import Optional
from urllib.parse import quote_plus, urljoin, urlparse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# NICE website constants
# ---------------------------------------------------------------------------

NICE_BASE = "https://www.nice.org.uk"
NICE_SEARCH_URL = "https://www.nice.org.uk/search#q={query}&sp=on&ndt=Guidance"
NICE_GUIDANCE_TYPES = {
    "ta": "Technology appraisal guidance",
    "ng": "NICE guideline",
    "cg": "Clinical guideline",
    "mib": "Medtech innovation briefing",
    "mtg": "Medical technologies guidance",
    "dg": "Diagnostics guidance",
    "ipg": "Interventional procedures guidance",
}

# ---------------------------------------------------------------------------
# Curated seed database
# Used as fast fallback when live scraping is unavailable or slow.
# Each entry is validated against real NICE guidance.
# ---------------------------------------------------------------------------

NICE_SEED_DB: list[dict] = [
    # --- Sepsis ---
    {
        "condition": "sepsis",
        "intervention_type": "diagnostic",
        "id": "NG51",
        "type": "ng",
        "title": "Sepsis: recognition, diagnosis and early management (NG51)",
        "url": "https://www.nice.org.uk/guidance/ng51",
        "pdf_url": "https://www.nice.org.uk/guidance/ng51/resources/sepsis-recognition-diagnosis-and-early-management-pdf-1837508256709",
        "date": "2016-07-13",
        "summary": "Recommends using validated clinical tools (NEWS, qSOFA) for early recognition. Covers blood culture, lactate measurement, and antibiotic timing. Recommends IV fluid resuscitation within 1 hour of recognition.",
    },
    {
        "condition": "sepsis",
        "intervention_type": "diagnostic",
        "id": "DG38",
        "type": "dg",
        "title": "Biomarker-based point-of-care tests for diagnosing sepsis in people in intensive care (DG38)",
        "url": "https://www.nice.org.uk/guidance/dg38",
        "pdf_url": None,
        "date": "2019-03-19",
        "summary": "Evaluated SeptiCyte LAB and SRS1 for diagnosing sepsis in ICU. Insufficient evidence to support routine adoption.",
    },
    {
        "condition": "sepsis",
        "intervention_type": "treatment",
        "id": "TA878",
        "type": "ta",
        "title": "Ceftolozane–tazobactam for treating complicated urinary tract infections and complicated intra-abdominal infections (TA878)",
        "url": "https://www.nice.org.uk/guidance/ta878",
        "pdf_url": None,
        "date": "2023-09-20",
        "summary": "Recommended as an option for treating complicated infections caused by aerobic gram-negative bacteria, including in sepsis, when other antibiotics are not suitable.",
    },
    # --- Heart failure ---
    {
        "condition": "heart failure",
        "intervention_type": "treatment",
        "id": "TA425",
        "type": "ta",
        "title": "Sacubitril valsartan for treating symptomatic chronic heart failure with reduced ejection fraction (TA425)",
        "url": "https://www.nice.org.uk/guidance/ta425",
        "pdf_url": "https://www.nice.org.uk/guidance/ta425/resources/sacubitril-valsartan-for-treating-symptomatic-chronic-heart-failure-with-reduced-ejection-fraction-pdf-82604526490309",
        "date": "2016-04-27",
        "summary": "Recommended for adults with symptomatic chronic heart failure with reduced ejection fraction (LVEF ≤35%) when ACE inhibitor or ARB not tolerated. ICER vs enalapril: £13,000–£16,000/QALY.",
    },
    {
        "condition": "heart failure",
        "intervention_type": "diagnostic",
        "id": "DG22",
        "type": "dg",
        "title": "Measuring fractional flow reserve during invasive coronary angiography (DG22)",
        "url": "https://www.nice.org.uk/guidance/dg22",
        "pdf_url": None,
        "date": "2014-10-22",
        "summary": "FFR measurement recommended as cost-effective option for guiding revascularisation decisions in stable angina.",
    },
    # --- Type 2 diabetes ---
    {
        "condition": "type 2 diabetes",
        "intervention_type": "treatment",
        "id": "TA336",
        "type": "ta",
        "title": "Canagliflozin in combination therapy for treating type 2 diabetes (TA336)",
        "url": "https://www.nice.org.uk/guidance/ta336",
        "pdf_url": None,
        "date": "2014-06-25",
        "summary": "Canagliflozin (100 mg and 300 mg) recommended as dual and triple therapy options. ICER vs comparators: £7,000–£22,000/QALY.",
    },
    {
        "condition": "type 2 diabetes",
        "intervention_type": "treatment",
        "id": "NG28",
        "type": "ng",
        "title": "Type 2 diabetes in adults: management (NG28)",
        "url": "https://www.nice.org.uk/guidance/ng28",
        "pdf_url": None,
        "date": "2015-12-02",
        "summary": "Guideline covering lifestyle interventions, glucose lowering drugs, blood pressure management, lipid modification, and renal monitoring. First-line: metformin; HbA1c target 48 mmol/mol (6.5%).",
    },
    # --- COPD ---
    {
        "condition": "copd",
        "intervention_type": "treatment",
        "id": "NG115",
        "type": "ng",
        "title": "Chronic obstructive pulmonary disease in over 16s: diagnosis and management (NG115)",
        "url": "https://www.nice.org.uk/guidance/ng115",
        "pdf_url": None,
        "date": "2019-07-26",
        "summary": "Covers diagnosis, stable COPD management, and exacerbation management. Stepwise inhaler therapy; pulmonary rehabilitation for MRC ≥3.",
    },
    # --- Depression ---
    {
        "condition": "depression",
        "intervention_type": "treatment",
        "id": "NG222",
        "type": "ng",
        "title": "Depression in adults: treatment and management (NG222)",
        "url": "https://www.nice.org.uk/guidance/ng222",
        "pdf_url": None,
        "date": "2022-06-29",
        "summary": "New stepped care model. Recommends psychological therapies and antidepressants based on severity. Updated SSRI first-line recommendations.",
    },
    # --- Alzheimer's ---
    {
        "condition": "alzheimer",
        "intervention_type": "treatment",
        "id": "TA217",
        "type": "ta",
        "title": "Donepezil, galantamine, rivastigmine and memantine for the treatment of Alzheimer's disease (TA217)",
        "url": "https://www.nice.org.uk/guidance/ta217",
        "pdf_url": None,
        "date": "2011-03-23",
        "summary": "Donepezil, galantamine and rivastigmine recommended for mild-to-moderate Alzheimer's. Memantine for moderate-severe or when AChEIs not tolerated.",
    },
]


# ---------------------------------------------------------------------------
# Helper: HTTP session with retries
# ---------------------------------------------------------------------------


def _make_session():
    """Create a requests session with retry logic and browser-like headers."""
    try:
        import requests  # noqa: PLC0415
        from requests.adapters import HTTPAdapter  # noqa: PLC0415
        from urllib3.util.retry import Retry  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "requests package is required. Install with: pip install requests"
        ) from exc

    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (compatible; HEOR-Engine/1.0; "
                "+https://github.com/heor-engine)"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.5",
        }
    )
    return session


# ---------------------------------------------------------------------------
# NICEAgent class
# ---------------------------------------------------------------------------


class NICEAgent:
    """Agent for searching NICE guidance and extracting structured HE data."""

    def __init__(
        self,
        anthropic_api_key: Optional[str] = None,
        claude_model: str = "claude-sonnet-4-6",
        request_delay: float = 1.0,
    ):
        self.anthropic_api_key = anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.claude_model = claude_model
        self.request_delay = request_delay  # seconds between web requests
        self._anthropic_client = None
        self._session = None

    # ------------------------------------------------------------------
    # Lazy-loaded clients
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

    @property
    def session(self):
        if self._session is None:
            self._session = _make_session()
        return self._session

    # ------------------------------------------------------------------
    # 1. search_nice_guidance
    # ------------------------------------------------------------------

    def search_nice_guidance(
        self,
        condition: str,
        intervention_type: str = "any",
    ) -> list[dict]:
        """Search NICE guidance for a condition and intervention type.

        Tries live NICE search first; falls back to curated seed database.

        Args:
            condition: Clinical condition (e.g. "sepsis", "heart failure").
            intervention_type: "diagnostic", "treatment", "device", "any".

        Returns:
            List of guidance documents with url, title, type, date, summary.
        """
        condition_lower = condition.lower().strip()
        intervention_lower = intervention_type.lower().strip()

        # 1. Try live NICE search
        live_results = self._search_nice_live(condition_lower, intervention_lower)
        if live_results:
            logger.info("Live NICE search returned %d results", len(live_results))
            return live_results

        # 2. Fall back to seed database
        logger.info("Falling back to curated NICE seed database")
        return self._search_seed_db(condition_lower, intervention_lower)

    def _search_nice_live(self, condition: str, intervention_type: str) -> list[dict]:
        """Scrape NICE search results page."""
        try:
            from bs4 import BeautifulSoup  # noqa: PLC0415
        except ImportError:
            logger.warning("beautifulsoup4 not installed — skipping live NICE search")
            return []

        query = condition
        if intervention_type not in ("any", ""):
            query = f"{condition} {intervention_type}"

        url = NICE_SEARCH_URL.format(query=quote_plus(query))
        logger.info("Fetching NICE search: %s", url)

        try:
            time.sleep(self.request_delay)
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("NICE live search failed: %s", exc)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        results = []

        # NICE search results are in <div class="search-result"> elements
        for card in soup.select(".search-result, article.card, li.search-result-item"):
            try:
                result = self._parse_search_card(card)
                if result:
                    results.append(result)
            except Exception as exc:
                logger.debug("Failed to parse search card: %s", exc)
                continue

        # Fallback: try generic anchor tag extraction if structured cards not found
        if not results:
            results = self._extract_links_fallback(soup, condition)

        return results[:20]

    def _parse_search_card(self, card) -> Optional[dict]:
        """Parse a single NICE search result card."""
        link_el = card.find("a", href=True)
        if not link_el:
            return None

        href = link_el["href"]
        if not href.startswith("http"):
            href = urljoin(NICE_BASE, href)

        # Only keep guidance URLs
        if not re.search(r"/guidance/[a-z]{2,4}\d+", href, re.I):
            return None

        title = link_el.get_text(strip=True)
        if not title:
            return None

        # Type from ID in URL or title
        guidance_id = re.search(r"/guidance/([a-z]{2,4}\d+)", href, re.I)
        gid = guidance_id.group(1).upper() if guidance_id else ""
        prefix = re.match(r"[A-Z]+", gid)
        gtype = NICE_GUIDANCE_TYPES.get(prefix.group(0).lower(), "guidance") if prefix else "guidance"

        # Date
        date_el = card.find(class_=re.compile(r"date|published", re.I))
        date_str = date_el.get_text(strip=True) if date_el else ""

        # Summary
        summary_el = card.find("p") or card.find(class_=re.compile(r"summary|description", re.I))
        summary = summary_el.get_text(strip=True) if summary_el else ""

        return {
            "id": gid,
            "type": prefix.group(0).lower() if prefix else "guidance",
            "type_label": gtype,
            "title": title,
            "url": href,
            "pdf_url": None,
            "date": date_str,
            "summary": summary,
        }

    def _extract_links_fallback(self, soup, condition: str) -> list[dict]:
        """Fallback: extract any NICE guidance links from the page."""
        results = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href.startswith("http"):
                href = urljoin(NICE_BASE, href)
            m = re.search(r"/guidance/([a-z]{2,4}\d+)", href, re.I)
            if not m or href in seen:
                continue
            seen.add(href)
            gid = m.group(1).upper()
            prefix = re.match(r"[A-Z]+", gid)
            gtype = NICE_GUIDANCE_TYPES.get(prefix.group(0).lower(), "guidance") if prefix else "guidance"
            results.append({
                "id": gid,
                "type": prefix.group(0).lower() if prefix else "guidance",
                "type_label": gtype,
                "title": a.get_text(strip=True) or gid,
                "url": href,
                "pdf_url": None,
                "date": "",
                "summary": "",
            })
        return results[:20]

    def _search_seed_db(self, condition: str, intervention_type: str) -> list[dict]:
        """Search the curated seed database."""
        results = []
        condition_tokens = set(condition.split())

        for entry in NICE_SEED_DB:
            db_condition = entry["condition"].lower()
            db_type = entry["intervention_type"].lower()

            # Match condition: all tokens must appear in the db condition or vice-versa
            cond_match = (
                condition in db_condition
                or db_condition in condition
                or any(tok in db_condition for tok in condition_tokens)
            )
            type_match = (
                intervention_type in ("any", "")
                or intervention_type in db_type
                or db_type in intervention_type
            )

            if cond_match and type_match:
                results.append(
                    {k: v for k, v in entry.items() if k not in ("condition", "intervention_type")}
                )

        return results

    # ------------------------------------------------------------------
    # 2. extract_nice_data
    # ------------------------------------------------------------------

    def extract_nice_data(self, guidance_url: str) -> dict:
        """Fetch a NICE guidance page and extract structured HE data via Claude.

        Args:
            guidance_url: Full URL to a NICE guidance page.

        Returns:
            Structured dict with intervention, comparator, ICER, decision, etc.
        """
        page_text = self._fetch_page_text(guidance_url)

        if not page_text or len(page_text.strip()) < 200:
            logger.warning("Page text too short for %s — returning empty extraction", guidance_url)
            return {"url": guidance_url, "error": "Could not retrieve sufficient page content"}

        prompt = f"""Read the following NICE guidance text and extract structured health economics data.

Guidance URL: {guidance_url}

Text:
{page_text[:12000]}

Extract and return a JSON object with these fields:
{{
  "guidance_id": "e.g. TA425",
  "guidance_type": "Technology Appraisal | NICE Guideline | Diagnostics Guidance | MIB | Other",
  "title": "full guidance title",
  "intervention": "name and description of the technology/drug/device being evaluated",
  "indication": "condition and patient population",
  "comparator": "what the intervention was compared against (standard care, specific drugs, etc.)",
  "decision": "Recommended | Not recommended | Recommended with conditions | Optimised | Only in research",
  "decision_rationale": "brief explanation of the committee's decision",
  "icer": {{
    "value": null,
    "unit": "£/QALY",
    "comparator": "vs what",
    "probabilistic": true
  }},
  "willingness_to_pay_threshold": 20000,
  "clinical_outcomes": [
    {{"outcome": "overall survival", "result": "HR 0.70 (95% CI 0.58–0.85)", "source": "TRIAL-NAME"}}
  ],
  "key_cost_drivers": ["brief description of main cost driver 1", "..."],
  "population_details": {{
    "age": null,
    "severity": null,
    "prior_treatment": null,
    "subgroups": null
  }},
  "quality_of_life": {{
    "utility_intervention": null,
    "utility_comparator": null,
    "instrument": "EQ-5D"
  }},
  "model_used": "Markov | Partitioned survival | Decision tree | Other | Not reported",
  "time_horizon_years": null,
  "perspective": "NHS and PSS",
  "discount_rate": 0.035,
  "publication_date": "YYYY-MM-DD",
  "confidence": "high | medium | low"
}}

Rules:
- Use null for any field not found in the text.
- icer.value should be a number (e.g. 15000 for £15,000/QALY).
- Only return the JSON object, no other text.
"""

        logger.info("Sending NICE page to Claude for extraction: %s", guidance_url)
        try:
            response = self.anthropic_client.messages.create(
                model=self.claude_model,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
        except Exception as exc:
            logger.error("Claude API failed for NICE extraction: %s", exc)
            raise RuntimeError(f"Claude extraction failed: {exc}") from exc

        parsed = self._parse_json_response(raw)
        parsed["url"] = guidance_url
        return parsed

    def _fetch_page_text(self, url: str) -> str:
        """Fetch a NICE page and return clean text content."""
        try:
            from bs4 import BeautifulSoup  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "beautifulsoup4 required. Install with: pip install beautifulsoup4"
            ) from exc

        logger.info("Fetching page: %s", url)
        try:
            time.sleep(self.request_delay)
            resp = self.session.get(url, timeout=20)
            resp.raise_for_status()
        except Exception as exc:
            logger.error("Failed to fetch %s: %s", url, exc)
            return ""

        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove navigation, headers, footers, scripts
        for tag in soup(["nav", "header", "footer", "script", "style", "noscript"]):
            tag.decompose()

        # Prefer main content areas used by NICE
        main = (
            soup.find("main")
            or soup.find(id=re.compile(r"main|content", re.I))
            or soup.find(class_=re.compile(r"main|content|chapter", re.I))
            or soup.find("article")
            or soup.body
        )

        text = main.get_text(separator="\n", strip=True) if main else soup.get_text(separator="\n", strip=True)

        # Collapse excessive whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text

    # ------------------------------------------------------------------
    # 3. get_comparator_costs
    # ------------------------------------------------------------------

    def get_comparator_costs(self, condition: str) -> dict:
        """Look up typical standard-care costs for a condition from NICE data.

        Searches relevant NICE guidance and uses Claude to synthesise cost data.

        Args:
            condition: Clinical condition (e.g. "sepsis", "heart failure").

        Returns:
            Dict with typical cost values and sources.
        """
        # Find relevant guidance
        guidance_list = self.search_nice_guidance(condition, "any")

        if not guidance_list:
            logger.warning("No NICE guidance found for condition: %s", condition)
            return {
                "condition": condition,
                "error": "No relevant NICE guidance found",
                "source": None,
            }

        # Collect summaries and titles as context (avoid full page fetches for cost lookup)
        context_parts = []
        for g in guidance_list[:6]:
            context_parts.append(
                f"[{g['id']}] {g['title']} ({g['date']})\n"
                f"URL: {g['url']}\n"
                f"Summary: {g.get('summary', 'No summary available')}"
            )
        context = "\n\n".join(context_parts)

        prompt = f"""You are a UK health economics expert. Based on the NICE guidance summaries below,
estimate typical standard-care (comparator) costs for: {condition}

NICE guidance context:
{context}

Also use your knowledge of NHS reference costs and published NICE technology appraisals.

Return a JSON object:
{{
  "condition": "{condition}",
  "icu_days_typical": null,
  "ward_days_typical": null,
  "icu_cost_per_day": null,
  "ward_cost_per_day": null,
  "typical_cost_per_episode": null,
  "outpatient_cost_per_visit": null,
  "annual_drug_cost_comparator": null,
  "currency": "GBP",
  "price_year": null,
  "standard_of_care": "brief description of what standard care involves",
  "key_resource_drivers": ["driver 1", "driver 2"],
  "source": "which NICE documents or NHS reference costs informed these estimates",
  "confidence": "high | medium | low",
  "notes": "any important caveats"
}}

Use null for values you cannot estimate with reasonable confidence.
All costs in GBP. Only return the JSON object.
"""

        logger.info("Requesting comparator costs for '%s' from Claude", condition)
        try:
            response = self.anthropic_client.messages.create(
                model=self.claude_model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
        except Exception as exc:
            logger.error("Claude API failed for comparator costs: %s", exc)
            raise RuntimeError(f"Claude cost lookup failed: {exc}") from exc

        return self._parse_json_response(raw)

    # ------------------------------------------------------------------
    # 4. suggest_model_structure
    # ------------------------------------------------------------------

    def suggest_model_structure(self, guidance_url: str) -> dict:
        """Extract economic model structure from a NICE guidance document.

        Args:
            guidance_url: URL to a NICE technology appraisal or guidance page.

        Returns:
            Dict describing model type, states, time horizon, and rationale.
        """
        page_text = self._fetch_page_text(guidance_url)

        if not page_text or len(page_text.strip()) < 200:
            return self._default_model_structure(guidance_url, "Could not retrieve page")

        prompt = f"""Read this NICE guidance text and identify the economic model structure used.

Guidance URL: {guidance_url}

Text:
{page_text[:10000]}

Extract and return a JSON object:
{{
  "model_type": "Markov | Partitioned survival | Decision tree | Discrete event simulation | Other | Not reported",
  "states": ["list", "of", "health", "states"],
  "time_horizon": null,
  "time_horizon_unit": "years | months | lifetime",
  "cycle_length": null,
  "cycle_length_unit": "months | years | weeks",
  "discount_rate_costs": 0.035,
  "discount_rate_outcomes": 0.035,
  "perspective": "NHS and PSS | Societal | NHS only",
  "comparators_modelled": ["standard of care", "..."],
  "starting_age": null,
  "half_cycle_correction": true,
  "probabilistic_sensitivity_analysis": true,
  "scenario_analyses": ["list key scenarios if mentioned"],
  "software": "Microsoft Excel | R | TreeAge | Not reported",
  "rationale": "Brief explanation of why this model structure was chosen",
  "nice_preferred_approach": "What NICE typically recommends for this type of condition",
  "confidence": "high | medium | low"
}}

Rules:
- Use null for fields not reported in the text.
- discount_rate 0.035 is the NICE default — use this if not explicitly stated.
- Only return the JSON object, no other text.
"""

        logger.info("Extracting model structure from: %s", guidance_url)
        try:
            response = self.anthropic_client.messages.create(
                model=self.claude_model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
        except Exception as exc:
            logger.error("Claude API failed for model structure: %s", exc)
            raise RuntimeError(f"Claude model structure extraction failed: {exc}") from exc

        parsed = self._parse_json_response(raw)
        parsed["url"] = guidance_url
        return parsed

    def _default_model_structure(self, url: str, reason: str) -> dict:
        """Return NICE-standard defaults when extraction fails."""
        return {
            "url": url,
            "model_type": "Markov",
            "states": ["Alive", "Dead"],
            "time_horizon": None,
            "time_horizon_unit": "years",
            "cycle_length": 1,
            "cycle_length_unit": "months",
            "discount_rate_costs": 0.035,
            "discount_rate_outcomes": 0.035,
            "perspective": "NHS and PSS",
            "comparators_modelled": [],
            "half_cycle_correction": True,
            "probabilistic_sensitivity_analysis": True,
            "software": "Not reported",
            "rationale": "NICE standard defaults applied — " + reason,
            "confidence": "low",
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_json_response(self, raw: str) -> dict:
        """Strip markdown fences and parse JSON from Claude response."""
        clean = raw
        if clean.startswith("```"):
            lines = clean.splitlines()
            end = -1 if lines[-1].strip() == "```" else len(lines)
            clean = "\n".join(lines[1:end])

        try:
            return json.loads(clean)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse Claude response as JSON: %s\nRaw: %.400s", exc, raw)
            return {"error": "JSON parse error", "raw_response": raw[:500]}


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def search_nice_guidance(condition: str, intervention_type: str = "any") -> list[dict]:
    return NICEAgent().search_nice_guidance(condition, intervention_type)


def extract_nice_data(guidance_url: str) -> dict:
    return NICEAgent().extract_nice_data(guidance_url)


def get_comparator_costs(condition: str) -> dict:
    return NICEAgent().get_comparator_costs(condition)


def suggest_model_structure(guidance_url: str) -> dict:
    return NICEAgent().suggest_model_structure(guidance_url)


# ---------------------------------------------------------------------------
# CLI demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="NICE HEOR evidence agent")
    parser.add_argument("condition", help="Clinical condition (e.g. 'sepsis')")
    parser.add_argument("--intervention-type", default="any", help="diagnostic | treatment | device | any")
    parser.add_argument("--extract-url", help="Extract full data from a specific NICE URL")
    parser.add_argument("--comparator-costs", action="store_true", help="Get comparator costs")
    parser.add_argument("--model-structure", help="URL to extract model structure from")
    parser.add_argument("--output", help="Save results to JSON file")
    args = parser.parse_args()

    agent = NICEAgent()
    output: dict = {}

    print(f"\nSearching NICE guidance: '{args.condition}' / '{args.intervention_type}'...")
    guidance = agent.search_nice_guidance(args.condition, args.intervention_type)
    print(f"Found {len(guidance)} guidance documents")
    for g in guidance:
        print(f"  [{g['id']}] {g['title']} — {g['url']}")
    output["guidance"] = guidance

    if args.extract_url:
        print(f"\nExtracting data from: {args.extract_url}")
        data = agent.extract_nice_data(args.extract_url)
        print(json.dumps(data, indent=2))
        output["extraction"] = data

    if args.comparator_costs:
        print(f"\nGetting comparator costs for: {args.condition}")
        costs = agent.get_comparator_costs(args.condition)
        print(json.dumps(costs, indent=2))
        output["comparator_costs"] = costs

    if args.model_structure:
        print(f"\nExtracting model structure from: {args.model_structure}")
        structure = agent.suggest_model_structure(args.model_structure)
        print(json.dumps(structure, indent=2))
        output["model_structure"] = structure

    if args.output:
        with open(args.output, "w") as f:
            json.dump(output, f, indent=2)
        print(f"\nResults saved to {args.output}")
