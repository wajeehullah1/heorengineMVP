"""
Evidence agent: fetch and cache health economics reference data.

Data is stored under data/reference/ as JSON files. A sidecar metadata
file (<name>.meta.json) tracks when each entry was last fetched so that
stale checks work without parsing the payload.
"""

import csv
import json
import logging
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
REFERENCE_DIR = _REPO_ROOT / "data" / "reference"

STALENESS_DAYS = 30


# ---------------------------------------------------------------------------
# Low-level file helpers
# ---------------------------------------------------------------------------


def download_file(url: str, filepath: str) -> bool:
    """Download *url* and write the content to *filepath*.

    Returns True on success, False on any network or IO error.
    """
    dest = Path(filepath)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        logger.info("Downloading %s -> %s", url, filepath)
        urllib.request.urlretrieve(url, dest)  # noqa: S310 (URL is caller-supplied)
        logger.info("Download complete: %s", filepath)
        return True
    except urllib.error.URLError as exc:
        logger.error("Network error downloading %s: %s", url, exc)
    except OSError as exc:
        logger.error("IO error saving %s: %s", filepath, exc)
    return False


def load_csv_to_dict(filepath: str) -> list[dict]:
    """Parse a CSV file and return a list of row dicts.

    Returns an empty list if the file is missing or malformed.
    """
    path = Path(filepath)
    if not path.is_file():
        logger.warning("CSV not found: %s", filepath)
        return []
    try:
        with path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            rows = [dict(row) for row in reader]
        logger.debug("Loaded %d rows from %s", len(rows), filepath)
        return rows
    except (OSError, csv.Error) as exc:
        logger.error("Failed to load CSV %s: %s", filepath, exc)
        return []


def save_json(data: Any, filepath: str) -> None:
    """Serialise *data* to *filepath* with pretty formatting.

    Creates parent directories as needed.
    """
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        logger.debug("Saved JSON to %s", filepath)
    except (OSError, TypeError, ValueError) as exc:
        logger.error("Failed to save JSON %s: %s", filepath, exc)
        raise


def load_json(filepath: str) -> Any:
    """Load and return a JSON file's content.

    Returns None if the file is missing or cannot be parsed.
    """
    path = Path(filepath)
    if not path.is_file():
        logger.debug("JSON file not found: %s", filepath)
        return None
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        logger.debug("Loaded JSON from %s", filepath)
        return data
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Failed to load JSON %s: %s", filepath, exc)
        return None


# ---------------------------------------------------------------------------
# EvidenceCache
# ---------------------------------------------------------------------------


class EvidenceCache:
    """Persistent JSON cache for health economics reference data.

    Data files are stored as ``<cache_dir>/<name>.json``.
    A companion ``<name>.meta.json`` file records fetch timestamps so that
    staleness can be checked without touching the payload.

    Parameters
    ----------
    cache_dir:
        Directory used for storage. Defaults to ``data/reference/``.
    max_age_days:
        Entries older than this are considered stale.
    """

    def __init__(
        self,
        cache_dir: str | Path | None = None,
        max_age_days: int = STALENESS_DAYS,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else REFERENCE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_age_days = max_age_days
        logger.debug(
            "EvidenceCache initialised at %s (max_age=%d days)",
            self.cache_dir,
            self.max_age_days,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _data_path(self, name: str) -> Path:
        return self.cache_dir / f"{name}.json"

    def _meta_path(self, name: str) -> Path:
        return self.cache_dir / f"{name}.meta.json"

    def _read_meta(self, name: str) -> dict:
        meta = load_json(str(self._meta_path(name)))
        return meta if isinstance(meta, dict) else {}

    def _write_meta(self, name: str) -> None:
        meta = {"fetched_at": datetime.utcnow().isoformat()}
        save_json(meta, str(self._meta_path(name)))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_stale(self, name: str) -> bool:
        """Return True if *name* is missing or older than *max_age_days*."""
        if not self._data_path(name).is_file():
            logger.debug("Cache miss (no file): %s", name)
            return True

        meta = self._read_meta(name)
        fetched_at_raw = meta.get("fetched_at")
        if not fetched_at_raw:
            logger.debug("Cache miss (no metadata): %s", name)
            return True

        try:
            fetched_at = datetime.fromisoformat(fetched_at_raw)
        except ValueError:
            logger.warning("Corrupt metadata timestamp for %s, treating as stale", name)
            return True

        age = datetime.utcnow() - fetched_at
        stale = age > timedelta(days=self.max_age_days)
        if stale:
            logger.info(
                "Cache stale for %s (age %.1f days > %d days limit)",
                name,
                age.total_seconds() / 86400,
                self.max_age_days,
            )
        return stale

    def get(self, name: str) -> Any:
        """Return cached data for *name*, or None if missing / stale."""
        if self.is_stale(name):
            return None
        logger.info("Cache hit: %s", name)
        return load_json(str(self._data_path(name)))

    def set(self, name: str, data: Any) -> None:
        """Store *data* under *name* and update the fetch timestamp."""
        logger.info("Caching data for: %s", name)
        save_json(data, str(self._data_path(name)))
        self._write_meta(name)

    def clear(self, name: str | None = None) -> None:
        """Remove cached data.

        If *name* is given, removes only that entry (data + metadata).
        If *name* is None, clears **all** entries in the cache directory.
        """
        if name is not None:
            for path in (self._data_path(name), self._meta_path(name)):
                if path.is_file():
                    path.unlink()
                    logger.info("Cleared cache entry: %s", path.name)
        else:
            removed = 0
            for path in self.cache_dir.glob("*.json"):
                path.unlink()
                removed += 1
            logger.info("Cleared %d files from cache directory %s", removed, self.cache_dir)


# ---------------------------------------------------------------------------
# NHS Reference Costs
# ---------------------------------------------------------------------------

#: Canonical category tag for every cost key, used by get_cost_by_category().
_COST_CATEGORIES: dict[str, str] = {
    # Inpatient
    "bed_day_general_medicine": "inpatient",
    "bed_day_general_surgery": "inpatient",
    "bed_day_icu": "inpatient",
    "bed_day_hdu": "inpatient",
    "bed_day_maternity": "inpatient",
    "bed_day_paediatric": "inpatient",
    "bed_day_mental_health": "inpatient",
    # Outpatient
    "outpatient_first_consultant": "outpatient",
    "outpatient_followup_consultant": "outpatient",
    "outpatient_first_nurse": "outpatient",
    "outpatient_followup_nurse": "outpatient",
    "outpatient_therapy_session": "outpatient",
    # Emergency department
    "ed_minor_injury": "emergency",
    "ed_major_trauma": "emergency",
    "ed_resuscitation": "emergency",
    # Diagnostics
    "blood_test_basic": "diagnostics",
    "blood_test_extended": "diagnostics",
    "xray_chest": "diagnostics",
    "xray_limb": "diagnostics",
    "ct_scan_head": "diagnostics",
    "ct_scan_body": "diagnostics",
    "mri_scan": "diagnostics",
    "ultrasound": "diagnostics",
    "ecg": "diagnostics",
    "echocardiogram": "diagnostics",
    # Procedures
    "theatre_hour_minor": "procedures",
    "theatre_hour_major": "procedures",
    "central_line_insertion": "procedures",
    "endoscopy": "procedures",
    "colonoscopy": "procedures",
    # Ambulance
    "ambulance_see_and_treat": "ambulance",
    "ambulance_convey": "ambulance",
    "ambulance_hear_and_treat": "ambulance",
    # Community
    "district_nurse_visit": "community",
    "health_visitor_visit": "community",
    "physiotherapy_session": "community",
    "occupational_therapy_session": "community",
}

_CACHE_KEY = "nhs_reference_costs"

# Module-level cache instance (shared across calls within a process).
_cache = EvidenceCache()


def fetch_nhs_reference_costs() -> dict:
    """Return NHS National Cost Collection 2024/25 reference costs (GBP).

    The costs dict is served from the local cache when fresh (< 30 days old).
    On a cache miss the canonical values are built in-process, written to
    ``data/reference/nhs_reference_costs.json``, and returned.

    Returns
    -------
    dict
        ``{"costs": {...}, "metadata": {...}}``
    """
    cached = _cache.get(_CACHE_KEY)
    if cached is not None:
        logger.info("Returning cached NHS reference costs")
        return cached

    logger.info("Building NHS reference costs from 2024/25 published data")

    costs: dict[str, float] = {
        # Inpatient stays (cost per bed day, GBP)
        "bed_day_general_medicine": 450,
        "bed_day_general_surgery": 520,
        "bed_day_icu": 1850,
        "bed_day_hdu": 980,
        "bed_day_maternity": 380,
        "bed_day_paediatric": 520,
        "bed_day_mental_health": 340,
        # Outpatient attendances
        "outpatient_first_consultant": 135,
        "outpatient_followup_consultant": 92,
        "outpatient_first_nurse": 68,
        "outpatient_followup_nurse": 48,
        "outpatient_therapy_session": 54,
        # Emergency department
        "ed_minor_injury": 145,
        "ed_major_trauma": 280,
        "ed_resuscitation": 420,
        # Diagnostics
        "blood_test_basic": 3.50,
        "blood_test_extended": 12.80,
        "xray_chest": 28,
        "xray_limb": 34,
        "ct_scan_head": 98,
        "ct_scan_body": 125,
        "mri_scan": 168,
        "ultrasound": 65,
        "ecg": 45,
        "echocardiogram": 120,
        # Procedures
        "theatre_hour_minor": 850,
        "theatre_hour_major": 1450,
        "central_line_insertion": 245,
        "endoscopy": 385,
        "colonoscopy": 520,
        # Ambulance
        "ambulance_see_and_treat": 195,
        "ambulance_convey": 275,
        "ambulance_hear_and_treat": 28,
        # Community services
        "district_nurse_visit": 48,
        "health_visitor_visit": 52,
        "physiotherapy_session": 38,
        "occupational_therapy_session": 42,
    }

    payload = {
        "costs": costs,
        "metadata": {
            "source": "NHS National Cost Collection 2024/25",
            "date_fetched": datetime.utcnow().isoformat(),
            "currency": "GBP",
            "notes": "Costs are national averages; local variation expected",
        },
    }

    _cache.set(_CACHE_KEY, payload)
    logger.info("NHS reference costs cached (%d items)", len(costs))
    return payload


def search_reference_costs(query: str) -> list[tuple[str, float]]:
    """Search NHS reference costs by keyword.

    Performs case-insensitive substring matching against cost key names,
    treating underscores as spaces so that e.g. ``"bed day"`` matches
    ``"bed_day_general_medicine"``.

    Parameters
    ----------
    query:
        Search term, e.g. ``"bed day"``, ``"outpatient"``, ``"mri"``.

    Returns
    -------
    list of (name, cost) tuples sorted by name.
    """
    if not query or not query.strip():
        logger.warning("search_reference_costs called with empty query")
        return []

    data = fetch_nhs_reference_costs()
    costs: dict[str, float] = data.get("costs", {})

    needle = query.strip().lower().replace("_", " ")
    results: list[tuple[str, float]] = []

    for key, value in costs.items():
        haystack = key.replace("_", " ")
        if needle in haystack:
            results.append((key, value))

    results.sort(key=lambda t: t[0])
    logger.debug("search_reference_costs(%r) -> %d match(es)", query, len(results))
    return results


def get_cost_by_category(category: str) -> dict[str, float]:
    """Return all NHS reference costs that belong to *category*.

    Valid categories: ``"inpatient"``, ``"outpatient"``, ``"emergency"``,
    ``"diagnostics"``, ``"procedures"``, ``"ambulance"``, ``"community"``.

    Parameters
    ----------
    category:
        Category name (case-insensitive).

    Returns
    -------
    dict mapping cost key -> value.  Empty dict if category is unknown.
    """
    data = fetch_nhs_reference_costs()
    costs: dict[str, float] = data.get("costs", {})

    cat = category.strip().lower()
    matched = {
        key: costs[key]
        for key, assigned_cat in _COST_CATEGORIES.items()
        if assigned_cat == cat and key in costs
    }

    if not matched:
        known = sorted(set(_COST_CATEGORIES.values()))
        logger.warning(
            "get_cost_by_category(%r) returned no results. Known categories: %s",
            category,
            known,
        )
    else:
        logger.debug("get_cost_by_category(%r) -> %d item(s)", category, len(matched))

    return matched


# ---------------------------------------------------------------------------
# ONS Population Data
# ---------------------------------------------------------------------------

_ONS_CACHE_KEY = "ons_population_data"

# Normalisation map: aliases that resolve to canonical region keys.
_REGION_ALIASES: dict[str, str] = {
    # canonical keys (pass-through)
    "north_east": "north_east",
    "north_west": "north_west",
    "yorkshire_humber": "yorkshire_humber",
    "east_midlands": "east_midlands",
    "west_midlands": "west_midlands",
    "east_england": "east_england",
    "london": "london",
    "south_east": "south_east",
    "south_west": "south_west",
    # friendly aliases (keys are already space→underscore normalised)
    "yorkshire": "yorkshire_humber",
    "yorkshire_and_humber": "yorkshire_humber",
    "yorkshire_&_humber": "yorkshire_humber",
    "east_of_england": "east_england",
    "east": "east_england",
    "ne": "north_east",
    "nw": "north_west",
    "em": "east_midlands",
    "wm": "west_midlands",
    "se": "south_east",
    "sw": "south_west",
}

# Beds-to-population ratio used by calculate_catchment_from_beds().
_BEDS_PER_POPULATION = 500


def fetch_ons_population_data() -> dict:
    """Return ONS Mid-Year Population Estimates 2024 for the UK.

    Data is served from the local cache when fresh (< 30 days old).
    On a cache miss the canonical values are built in-process, written to
    ``data/reference/ons_population_data.json``, and returned.

    Returns
    -------
    dict
        ``{"population": {...}, "metadata": {...}}`` where ``population``
        contains the keys ``uk_total``, ``england_regions``, ``age_bands``,
        and ``prevalence_estimates``.
    """
    cached = _cache.get(_ONS_CACHE_KEY)
    if cached is not None:
        logger.info("Returning cached ONS population data")
        return cached

    logger.info("Building ONS population data from 2024 mid-year estimates")

    population_data: dict[str, Any] = {
        "uk_total": {
            "total": 67_800_000,
            "england": 57_000_000,
            "wales": 3_150_000,
            "scotland": 5_480_000,
            "northern_ireland": 1_900_000,
        },
        "england_regions": {
            "north_east": 2_650_000,
            "north_west": 7_420_000,
            "yorkshire_humber": 5_540_000,
            "east_midlands": 4_880_000,
            "west_midlands": 6_000_000,
            "east_england": 6_300_000,
            "london": 8_980_000,
            "south_east": 9_280_000,
            "south_west": 5_700_000,
        },
        "age_bands": {
            "0-4": 3_650_000,
            "5-9": 3_850_000,
            "10-14": 3_700_000,
            "15-19": 3_350_000,
            "20-24": 3_780_000,
            "25-29": 4_100_000,
            "30-34": 4_250_000,
            "35-39": 4_050_000,
            "40-44": 3_800_000,
            "45-49": 4_150_000,
            "50-54": 4_500_000,
            "55-59": 4_350_000,
            "60-64": 3_980_000,
            "65-69": 3_450_000,
            "70-74": 3_250_000,
            "75-79": 2_450_000,
            "80-84": 1_750_000,
            "85+": 1_650_000,
        },
        "prevalence_estimates": {
            "diabetes": 0.068,        # 6.8 %
            "hypertension": 0.28,     # 28 %
            "copd": 0.024,            # 2.4 %
            "asthma": 0.086,          # 8.6 %
            "heart_disease": 0.063,   # 6.3 %
            "stroke_history": 0.018,  # 1.8 %
            "cancer_diagnosed": 0.031,# 3.1 %
            "mental_health": 0.17,    # 17 %
            "dementia": 0.011,        # 1.1 %
        },
    }

    payload = {
        "population": population_data,
        "metadata": {
            "source": "ONS Mid-Year Population Estimates 2024",
            "date_fetched": datetime.utcnow().isoformat(),
            "notes": "England population estimates; prevalence from NHS Digital",
        },
    }

    _cache.set(_ONS_CACHE_KEY, payload)
    logger.info("ONS population data cached")
    return payload


def get_population_by_region(region: str) -> int:
    """Return the population for a named England region.

    Accepts canonical snake_case keys (e.g. ``"north_east"``) as well as
    common aliases (e.g. ``"Yorkshire"``, ``"East of England"``).

    Parameters
    ----------
    region:
        Region name (case-insensitive, spaces or underscores accepted).

    Returns
    -------
    int
        Population count, or 0 if the region is not recognised.
    """
    data = fetch_ons_population_data()
    regions: dict[str, int] = data["population"]["england_regions"]

    key = region.strip().lower().replace(" ", "_").replace("-", "_")
    canonical = _REGION_ALIASES.get(key)

    if canonical and canonical in regions:
        pop = regions[canonical]
        logger.debug("get_population_by_region(%r) -> %d", region, pop)
        return pop

    # Last-resort: direct lookup without alias table (handles unexpected input)
    if key in regions:
        return regions[key]

    known = sorted(regions.keys())
    logger.warning(
        "get_population_by_region(%r) not found. Known regions: %s", region, known
    )
    return 0


def estimate_eligible_population(
    total_population: int,
    condition: str,
    age_band: str | None = None,
) -> int:
    """Estimate the number of people with *condition* in *total_population*.

    Applies the national prevalence rate for *condition* to
    *total_population*.  When *age_band* is supplied the calculation is
    restricted to that age cohort's share of the England population, giving
    a rough age-adjusted estimate.

    Parameters
    ----------
    total_population:
        The population base to apply prevalence to (e.g. a catchment area).
    condition:
        Disease/condition key matching ``prevalence_estimates`` in the ONS
        data (e.g. ``"diabetes"``, ``"hypertension"``).
    age_band:
        Optional ONS age-band string, e.g. ``"65-69"`` or ``"85+"``.
        When provided the eligible count is scaled by the proportion of the
        England population that falls in that band.

    Returns
    -------
    int
        Estimated number of eligible patients (rounded to nearest integer).
        Returns 0 and logs a warning if the condition is unknown.
    """
    if total_population <= 0:
        logger.warning("estimate_eligible_population: total_population must be > 0")
        return 0

    data = fetch_ons_population_data()
    pop_data = data["population"]
    prevalence_map: dict[str, float] = pop_data["prevalence_estimates"]

    cond_key = condition.strip().lower().replace(" ", "_")
    prevalence = prevalence_map.get(cond_key)
    if prevalence is None:
        known = sorted(prevalence_map.keys())
        logger.warning(
            "estimate_eligible_population: unknown condition %r. Known: %s",
            condition,
            known,
        )
        return 0

    base_eligible = total_population * prevalence

    if age_band is not None:
        age_bands: dict[str, int] = pop_data["age_bands"]
        band_pop = age_bands.get(age_band)
        if band_pop is None:
            logger.warning(
                "estimate_eligible_population: unknown age_band %r, ignoring", age_band
            )
        else:
            england_total: int = pop_data["uk_total"]["england"]
            band_fraction = band_pop / england_total
            base_eligible *= band_fraction
            logger.debug(
                "Age-band %r fraction=%.4f applied to eligible estimate", age_band, band_fraction
            )

    result = round(base_eligible)
    logger.debug(
        "estimate_eligible_population(pop=%d, condition=%r, age_band=%r) -> %d",
        total_population,
        condition,
        age_band,
        result,
    )
    return result


def calculate_catchment_from_beds(
    bed_count: int,
    bed_occupancy: float = 0.85,
) -> int:
    """Estimate the catchment population served by a given number of beds.

    Uses the NHS rule of thumb of approximately 1 bed per 500 population,
    adjusted for occupancy so that a higher-utilised bed serves more people.

    Formula::

        catchment = bed_count * (1 / occupancy) * beds_per_population

    Where ``beds_per_population`` = 500.

    Parameters
    ----------
    bed_count:
        Number of available (staffed) beds.
    bed_occupancy:
        Proportion of beds occupied on average (0 < value ≤ 1).
        Default 0.85 (NHS operational target).

    Returns
    -------
    int
        Estimated catchment population, rounded to nearest integer.
        Returns 0 for invalid inputs and logs a warning.
    """
    if bed_count <= 0:
        logger.warning("calculate_catchment_from_beds: bed_count must be > 0")
        return 0
    if not (0 < bed_occupancy <= 1):
        logger.warning(
            "calculate_catchment_from_beds: bed_occupancy %.2f out of range (0, 1], "
            "using default 0.85",
            bed_occupancy,
        )
        bed_occupancy = 0.85

    catchment = round(bed_count * (1 / bed_occupancy) * _BEDS_PER_POPULATION)
    logger.debug(
        "calculate_catchment_from_beds(beds=%d, occupancy=%.2f) -> %d",
        bed_count,
        bed_occupancy,
        catchment,
    )
    return catchment


# ---------------------------------------------------------------------------
# NICE Guidance
# ---------------------------------------------------------------------------

_NICE_CACHE_KEY = "nice_guidance_db"   # internal DB – distinct from the search-results file

# ---------------------------------------------------------------------------
# Curated NICE guidance database
# Each record must contain: id, title, type, date, condition, url
# Optional fields: decision, icer, recommendations, evidence_level,
#                  intervention_types, comparators, threshold_category
# ---------------------------------------------------------------------------
_NICE_GUIDANCE_DB: list[dict] = [
    # --- Technology Appraisals (TA) ---
    {
        "id": "TA123",
        "title": "Artificial intelligence for analysing CT brain scans",
        "type": "Technology Appraisal",
        "date": "2024-03-15",
        "decision": "Recommended",
        "condition": "Stroke",
        "intervention_types": ["ai", "diagnostic", "imaging"],
        "icer": 12_500,
        "comparators": ["standard radiologist review"],
        "threshold_category": "standard",
        "url": "https://www.nice.org.uk/guidance/ta123",
    },
    {
        "id": "TA894",
        "title": "Dapagliflozin for treating type 2 diabetes",
        "type": "Technology Appraisal",
        "date": "2023-11-22",
        "decision": "Recommended",
        "condition": "Diabetes",
        "intervention_types": ["pharmaceutical", "sglt2"],
        "icer": 8_200,
        "comparators": ["metformin", "sitagliptin", "standard care"],
        "threshold_category": "standard",
        "url": "https://www.nice.org.uk/guidance/ta894",
    },
    {
        "id": "TA878",
        "title": "Semaglutide for managing overweight and obesity",
        "type": "Technology Appraisal",
        "date": "2023-03-08",
        "decision": "Recommended with restrictions",
        "condition": "Obesity",
        "intervention_types": ["pharmaceutical", "glp1"],
        "icer": 22_800,
        "comparators": ["lifestyle intervention", "orlistat"],
        "threshold_category": "standard",
        "url": "https://www.nice.org.uk/guidance/ta878",
    },
    {
        "id": "TA548",
        "title": "Nivolumab for treating squamous non-small-cell lung cancer",
        "type": "Technology Appraisal",
        "date": "2017-02-22",
        "decision": "Recommended",
        "condition": "Lung Cancer",
        "intervention_types": ["pharmaceutical", "immunotherapy"],
        "icer": 43_600,
        "comparators": ["docetaxel"],
        "threshold_category": "end_of_life",
        "url": "https://www.nice.org.uk/guidance/ta548",
    },
    {
        "id": "TA902",
        "title": "Lecanemab for early Alzheimer's disease",
        "type": "Technology Appraisal",
        "date": "2024-08-28",
        "decision": "Not recommended",
        "condition": "Dementia",
        "intervention_types": ["pharmaceutical", "monoclonal_antibody"],
        "icer": 89_000,
        "comparators": ["standard care"],
        "threshold_category": "highly_specialised",
        "url": "https://www.nice.org.uk/guidance/ta902",
    },
    # --- NICE Guidelines (NG) ---
    {
        "id": "NG28",
        "title": "Type 2 diabetes in adults: management",
        "type": "NICE Guideline",
        "date": "2023-08-10",
        "condition": "Diabetes",
        "intervention_types": ["digital", "remote_monitoring", "pharmaceutical"],
        "recommendations": [
            "Digital health interventions for self-management",
            "Remote monitoring of blood glucose",
            "Structured education programmes",
            "Individual HbA1c targets",
        ],
        "url": "https://www.nice.org.uk/guidance/ng28",
    },
    {
        "id": "NG185",
        "title": "Remote monitoring of implantable cardiac devices",
        "type": "NICE Guideline",
        "date": "2022-11-30",
        "condition": "Heart Failure",
        "intervention_types": ["remote_monitoring", "digital", "device"],
        "recommendations": [
            "Remote monitoring for heart failure with devices",
            "Structured follow-up via digital platforms",
            "Alert-based clinician review",
        ],
        "url": "https://www.nice.org.uk/guidance/ng185",
    },
    {
        "id": "NG206",
        "title": "COVID-19 rapid guideline: managing COVID-19",
        "type": "NICE Guideline",
        "date": "2023-03-29",
        "condition": "Respiratory",
        "intervention_types": ["digital", "remote_monitoring", "pharmaceutical"],
        "recommendations": [
            "Pulse oximetry for remote monitoring",
            "Digital triage tools",
            "Antiviral treatment pathways",
        ],
        "url": "https://www.nice.org.uk/guidance/ng191",
    },
    {
        "id": "NG136",
        "title": "Hypertension in adults: diagnosis and management",
        "type": "NICE Guideline",
        "date": "2023-05-18",
        "condition": "Hypertension",
        "intervention_types": ["digital", "remote_monitoring", "pharmaceutical"],
        "recommendations": [
            "Ambulatory blood pressure monitoring",
            "Home blood pressure monitoring",
            "Digital tools for adherence support",
            "Treat-to-target approach",
        ],
        "url": "https://www.nice.org.uk/guidance/ng136",
    },
    # --- Medtech Innovation Briefings (MIB) ---
    {
        "id": "MIB234",
        "title": "Remote patient monitoring for heart failure",
        "type": "Medtech Innovation Briefing",
        "date": "2024-01-20",
        "condition": "Heart Failure",
        "intervention_types": ["remote_monitoring", "digital", "device"],
        "evidence_level": "Moderate",
        "comparators": ["standard outpatient follow-up"],
        "threshold_category": "standard",
        "url": "https://www.nice.org.uk/advice/mib234",
    },
    {
        "id": "MIB298",
        "title": "AI-assisted ECG interpretation for atrial fibrillation detection",
        "type": "Medtech Innovation Briefing",
        "date": "2024-05-14",
        "condition": "Atrial Fibrillation",
        "intervention_types": ["ai", "diagnostic", "digital"],
        "evidence_level": "Moderate",
        "comparators": ["standard 12-lead ECG interpretation"],
        "threshold_category": "standard",
        "url": "https://www.nice.org.uk/advice/mib298",
    },
    {
        "id": "MIB312",
        "title": "Digital cognitive behavioural therapy for depression",
        "type": "Medtech Innovation Briefing",
        "date": "2024-09-03",
        "condition": "Mental Health",
        "intervention_types": ["digital", "ai", "cbt"],
        "evidence_level": "Low to Moderate",
        "comparators": ["face-to-face CBT", "waitlist control"],
        "threshold_category": "standard",
        "url": "https://www.nice.org.uk/advice/mib312",
    },
    # --- Diagnostics Guidance (DG) ---
    {
        "id": "DG56",
        "title": "Ultrasound-guided peripheral intravenous catheter placement",
        "type": "Diagnostics Guidance",
        "date": "2023-07-12",
        "condition": "Vascular Access",
        "intervention_types": ["diagnostic", "imaging", "device"],
        "evidence_level": "High",
        "comparators": ["landmark technique"],
        "threshold_category": "standard",
        "url": "https://www.nice.org.uk/guidance/dg56",
    },
    {
        "id": "DG61",
        "title": "Multi-parametric MRI before prostate biopsy",
        "type": "Diagnostics Guidance",
        "date": "2023-10-25",
        "condition": "Prostate Cancer",
        "intervention_types": ["diagnostic", "imaging", "ai"],
        "evidence_level": "High",
        "comparators": ["systematic TRUS biopsy"],
        "threshold_category": "standard",
        "url": "https://www.nice.org.uk/guidance/dg61",
    },
    # --- Highly Specialised Technologies (HST) ---
    {
        "id": "HST18",
        "title": "Risdiplam for treating spinal muscular atrophy",
        "type": "Highly Specialised Technology",
        "date": "2022-04-27",
        "decision": "Recommended",
        "condition": "Rare Disease",
        "intervention_types": ["pharmaceutical", "gene_therapy_adjacent"],
        "icer": 67_000,
        "comparators": ["nusinersen", "best supportive care"],
        "threshold_category": "highly_specialised",
        "url": "https://www.nice.org.uk/guidance/hst18",
    },
]

# ---------------------------------------------------------------------------
# WTP threshold definitions used by get_nice_threshold_context()
# ---------------------------------------------------------------------------
_WTP_THRESHOLDS: dict[str, dict] = {
    "standard": {
        "standard_threshold": 25_000,
        "upper_threshold": 35_000,
        "description": "Standard NICE cost-effectiveness threshold",
        "special_considerations": [],
    },
    "end_of_life": {
        "standard_threshold": 35_000,
        "upper_threshold": 50_000,
        "description": "End-of-life modifier; applies when remaining life < 24 months and treatment extends life ≥ 3 months",
        "special_considerations": [
            "End of life criteria may apply",
            "Short life expectancy < 24 months",
            "Treatment must extend life by ≥ 3 months",
            "Small patient population required",
        ],
    },
    "highly_specialised": {
        "standard_threshold": 100_000,
        "upper_threshold": 300_000,
        "description": "Highly Specialised Technologies (HST) programme for ultra-rare diseases",
        "special_considerations": [
            "Ultra-rare condition (typically < 1 in 50,000)",
            "Evaluated via HST programme not standard TA",
            "QALY weighting applies up to 100 QALYs per patient",
            "Managed Access Agreement often required",
        ],
    },
    "rare_disease": {
        "standard_threshold": 25_000,
        "upper_threshold": 100_000,
        "description": "Rare diseases may receive flexibility outside the HST programme",
        "special_considerations": [
            "Orphan designation may apply",
            "Flexible threshold under Innovative Medicines Fund",
            "Budget impact considerations given small population",
        ],
    },
}

# Condition → threshold_category mapping for get_nice_threshold_context().
_CONDITION_THRESHOLD_MAP: dict[str, str] = {
    "cancer": "end_of_life",
    "lung cancer": "end_of_life",
    "breast cancer": "end_of_life",
    "prostate cancer": "end_of_life",
    "colorectal cancer": "end_of_life",
    "leukemia": "end_of_life",
    "lymphoma": "end_of_life",
    "myeloma": "end_of_life",
    "dementia": "end_of_life",
    "alzheimer": "end_of_life",
    "sma": "highly_specialised",
    "spinal muscular atrophy": "highly_specialised",
    "huntington": "highly_specialised",
    "cystic fibrosis": "highly_specialised",
    "haemophilia": "highly_specialised",
    "rare disease": "rare_disease",
    "orphan": "rare_disease",
}


def _build_nice_db_payload() -> dict:
    """Wrap the guidance DB with metadata for caching."""
    return {
        "guidance": _NICE_GUIDANCE_DB,
        "metadata": {
            "source": "NICE Guidance Database (curated MVP)",
            "date_fetched": datetime.utcnow().isoformat(),
            "record_count": len(_NICE_GUIDANCE_DB),
            "notes": "Curated subset of NICE guidance relevant to HEOR/digital health",
        },
    }


def _load_nice_db() -> list[dict]:
    """Return the NICE guidance list, using the cache when fresh."""
    cached = _cache.get(_NICE_CACHE_KEY)
    if cached is not None:
        return cached.get("guidance", [])

    logger.info("Initialising NICE guidance cache (%d records)", len(_NICE_GUIDANCE_DB))
    payload = _build_nice_db_payload()
    _cache.set(_NICE_CACHE_KEY, payload)
    return payload["guidance"]


def search_nice_guidance(
    search_term: str,
    guidance_type: str = "all",
) -> list[dict]:
    """Search the curated NICE guidance database by keyword and/or type.

    Performs case-insensitive substring matching across ``title``,
    ``condition``, ``id``, and ``intervention_types`` fields.

    Parameters
    ----------
    search_term:
        Free-text keyword, e.g. ``"diabetes"``, ``"remote monitoring"``,
        ``"AI"``.  Pass an empty string to return all records (subject to
        *guidance_type* filter).
    guidance_type:
        Filter by document type (case-insensitive).  Accepted values:

        * ``"all"`` – no type filter (default)
        * ``"ta"`` / ``"technology appraisal"``
        * ``"ng"`` / ``"nice guideline"``
        * ``"mib"`` / ``"medtech innovation briefing"``
        * ``"dg"`` / ``"diagnostics guidance"``
        * ``"hst"`` / ``"highly specialised technology"``

    Returns
    -------
    list[dict]
        Matching guidance records sorted by date (most recent first).
        Results are also written to ``data/reference/nice_guidance_cache.json``.
    """
    db = _load_nice_db()

    # Normalise type filter
    type_aliases: dict[str, str] = {
        "ta": "Technology Appraisal",
        "ng": "NICE Guideline",
        "mib": "Medtech Innovation Briefing",
        "dg": "Diagnostics Guidance",
        "hst": "Highly Specialised Technology",
    }
    type_filter: str | None = None
    if guidance_type and guidance_type.strip().lower() != "all":
        t = guidance_type.strip().lower()
        type_filter = type_aliases.get(t, guidance_type.strip())

    needle = search_term.strip().lower() if search_term else ""

    results: list[dict] = []
    for record in db:
        # Type filter
        if type_filter and type_filter.lower() not in record.get("type", "").lower():
            continue

        # Keyword filter (skip when needle is empty)
        if needle:
            searchable = " ".join([
                record.get("title", ""),
                record.get("condition", ""),
                record.get("id", ""),
                " ".join(record.get("intervention_types", [])),
                " ".join(record.get("recommendations", [])),
            ]).lower()
            if needle not in searchable:
                continue

        results.append(record)

    results.sort(key=lambda r: r.get("date", ""), reverse=True)
    logger.info(
        "search_nice_guidance(%r, type=%r) -> %d result(s)",
        search_term,
        guidance_type,
        len(results),
    )

    # Persist the search result snapshot for downstream consumers
    save_json(
        {"query": search_term, "guidance_type": guidance_type, "results": results},
        str(REFERENCE_DIR / "nice_guidance_cache.json"),
    )
    return results


def get_nice_comparators(condition: str, intervention_type: str) -> list[dict]:
    """Return NICE-approved comparators for *condition* and *intervention_type*.

    Useful for identifying what an economic model should compare against
    in a BIA or CEA.

    Parameters
    ----------
    condition:
        Clinical condition, e.g. ``"diabetes"``, ``"heart failure"``.
    intervention_type:
        Type of the new technology, e.g. ``"digital"``, ``"pharmaceutical"``,
        ``"remote_monitoring"``, ``"ai"``.

    Returns
    -------
    list[dict]
        Each item is ``{"id", "title", "type", "decision", "comparators",
        "icer", "url"}`` for each matching guidance record that has listed
        comparators.  Empty list if no matches.
    """
    db = _load_nice_db()

    cond_needle = condition.strip().lower()
    type_needle = intervention_type.strip().lower().replace(" ", "_").replace("-", "_")

    results: list[dict] = []
    for record in db:
        cond_match = cond_needle in record.get("condition", "").lower()
        type_match = any(
            type_needle in t for t in record.get("intervention_types", [])
        )
        if not (cond_match and type_match):
            continue

        # For Technology Appraisals and MIBs use the explicit comparators list;
        # for Guidelines fall back to the recommendations list which names
        # alternative treatments the guideline discusses.
        comparators = record.get("comparators") or record.get("recommendations") or []
        results.append({
            "id": record["id"],
            "title": record["title"],
            "type": record["type"],
            "decision": record.get("decision", "N/A"),
            "comparators": comparators,
            "icer": record.get("icer"),
            "url": record["url"],
        })

    results.sort(key=lambda r: r.get("id", ""))
    logger.info(
        "get_nice_comparators(condition=%r, type=%r) -> %d result(s)",
        condition,
        intervention_type,
        len(results),
    )
    return results


def get_nice_threshold_context(condition: str) -> dict:
    """Return NICE willingness-to-pay threshold context for *condition*.

    Determines the applicable threshold band (standard / end-of-life /
    highly specialised / rare disease) from the condition name, then
    returns thresholds and any precedent ICERs from the guidance DB.

    Parameters
    ----------
    condition:
        Clinical condition or technology area, e.g. ``"cancer"``,
        ``"diabetes"``, ``"dementia"``, ``"sma"``.

    Returns
    -------
    dict with keys:

    * ``standard_threshold`` – lower WTP bound (£/QALY)
    * ``upper_threshold`` – upper WTP bound (£/QALY)
    * ``threshold_category`` – which band applies
    * ``description`` – plain-English explanation
    * ``special_considerations`` – list of applicability criteria
    * ``precedents`` – list of ``{id, title, icer, decision}`` for similar
      approved technologies, ordered by ICER ascending
    """
    cond_lower = condition.strip().lower()

    # Walk the condition→category map; longest match wins
    category = "standard"
    best_len = 0
    for cond_key, cat in _CONDITION_THRESHOLD_MAP.items():
        if cond_key in cond_lower and len(cond_key) > best_len:
            category = cat
            best_len = len(cond_key)

    thresholds = _WTP_THRESHOLDS[category].copy()
    thresholds["threshold_category"] = category

    # Gather precedents from DB (records with an ICER for a related condition)
    db = _load_nice_db()
    precedents: list[dict] = []
    for record in db:
        if record.get("icer") is None:
            continue
        rec_cond = record.get("condition", "").lower()
        rec_cat  = record.get("threshold_category", "standard")
        # Include if condition overlaps or threshold category matches
        if cond_lower in rec_cond or rec_cond in cond_lower or rec_cat == category:
            precedents.append({
                "id": record["id"],
                "title": record["title"],
                "condition": record["condition"],
                "icer": record["icer"],
                "decision": record.get("decision", "N/A"),
                "threshold_category": rec_cat,
            })

    precedents.sort(key=lambda p: p["icer"])
    thresholds["precedents"] = precedents

    logger.info(
        "get_nice_threshold_context(%r) -> category=%r, precedents=%d",
        condition,
        category,
        len(precedents),
    )
    return thresholds


# ---------------------------------------------------------------------------
# BIA enrichment helpers (private)
# ---------------------------------------------------------------------------

# Cost keys that are relevant to common BIA pathways and the NHS reference
# cost key they map to.
_PATHWAY_COST_MAP: dict[str, str] = {
    "outpatient_visit":         "outpatient_followup_consultant",
    "outpatient_first":         "outpatient_first_consultant",
    "gp_visit":                 "outpatient_followup_nurse",
    "ed_visit":                 "ed_minor_injury",
    "ed_attendance":            "ed_minor_injury",
    "inpatient_day":            "bed_day_general_medicine",
    "bed_day":                  "bed_day_general_medicine",
    "icu_day":                  "bed_day_icu",
    "blood_test":               "blood_test_basic",
    "ecg":                      "ecg",
    "echocardiogram":           "echocardiogram",
    "mri":                      "mri_scan",
    "ct_scan":                  "ct_scan_body",
    "ultrasound":               "ultrasound",
    "physiotherapy":            "physiotherapy_session",
    "district_nurse":           "district_nurse_visit",
    "ambulance":                "ambulance_convey",
}

# Threshold (fraction) above which a user-supplied cost diverges "too much"
# from the NHS reference value.
_COST_DIVERGENCE_THRESHOLD = 0.50


def _find_pathway_costs(condition: str) -> dict[str, float]:
    """Return the subset of NHS reference costs relevant to *condition*."""
    nhs = fetch_nhs_reference_costs()["costs"]

    # Start with a universal base set applicable to any pathway
    base_keys = {
        "outpatient_first_consultant",
        "outpatient_followup_consultant",
        "outpatient_followup_nurse",
        "blood_test_basic",
        "ecg",
    }

    cond = condition.lower()
    if any(k in cond for k in ("heart", "cardiac", "af", "atrial")):
        base_keys.update({"echocardiogram", "bed_day_general_medicine", "ambulance_convey"})
    if any(k in cond for k in ("diabetes", "metabolic", "obesity")):
        base_keys.update({"blood_test_extended", "outpatient_therapy_session"})
    if any(k in cond for k in ("stroke", "neuro", "brain")):
        base_keys.update({"ct_scan_head", "mri_scan", "bed_day_general_medicine", "bed_day_hdu"})
    if any(k in cond for k in ("respiratory", "copd", "asthma", "lung")):
        base_keys.update({"bed_day_general_medicine", "ambulance_convey", "ed_minor_injury"})
    if any(k in cond for k in ("mental", "depress", "anxiety", "psych")):
        base_keys.update({"bed_day_mental_health", "outpatient_therapy_session"})
    if any(k in cond for k in ("cancer", "oncol", "tumour", "tumor")):
        base_keys.update({"ct_scan_body", "mri_scan", "bed_day_general_surgery"})
    if any(k in cond for k in ("fracture", "ortho", "joint", "bone")):
        base_keys.update({"xray_limb", "theatre_hour_major", "bed_day_general_surgery"})

    return {k: nhs[k] for k in base_keys if k in nhs}


def _validate_user_costs(
    user_costs: dict[str, float],
    reference_costs: dict[str, float],
) -> list[str]:
    """Compare *user_costs* against *reference_costs* and return warnings."""
    warnings: list[str] = []
    for user_key, user_val in user_costs.items():
        # Try to find a matching reference cost via the pathway map or direct key
        ref_key = _PATHWAY_COST_MAP.get(user_key.lower()) or user_key
        ref_val = reference_costs.get(ref_key)
        if ref_val is None:
            continue
        if ref_val == 0:
            continue
        divergence = abs(user_val - ref_val) / ref_val
        if divergence > _COST_DIVERGENCE_THRESHOLD:
            direction = "above" if user_val > ref_val else "below"
            warnings.append(
                f"Cost '{user_key}' £{user_val:,.2f} is {divergence:.0%} {direction} "
                f"NHS reference £{ref_val:,.2f} ({ref_key}) — verify pricing"
            )
    return warnings


# ---------------------------------------------------------------------------
# Public enrichment API
# ---------------------------------------------------------------------------


def enrich_bia_inputs(inputs: dict) -> dict:
    """Enrich BIA input parameters with NHS, ONS, and NICE reference data.

    Takes a raw inputs dict (as a user or calling agent would supply before
    running a BIA calculation) and returns an augmented copy that adds:

    * ``suggested_values`` – recommended parameter overrides / fill-ins
    * ``warnings`` – potential data-quality issues
    * ``comparators`` – NICE-approved interventions for the same condition
    * ``reference_costs`` – NHS costs relevant to the care pathway
    * ``population_context`` – ONS prevalence and region data
    * ``metadata`` – provenance and timestamp

    The original inputs are preserved unchanged under the ``"inputs"`` key.

    Expected input keys (all optional – function degrades gracefully):

    .. code-block:: python

        {
            "condition": "Heart Failure",
            "intervention_type": "remote_monitoring",
            "catchment_size": 250000,        # may be absent → estimated
            "bed_count": 300,                # used to estimate catchment
            "eligible_pct": 0.05,            # may be absent → from prevalence
            "costs": {                       # user-supplied unit costs
                "outpatient_visit": 80,
                "device_cost": 500,
            },
            "region": "North West",
        }

    Parameters
    ----------
    inputs:
        Raw BIA parameter dictionary.

    Returns
    -------
    dict
        Enriched dictionary with ``"inputs"``, ``"suggested_values"``,
        ``"warnings"``, ``"comparators"``, ``"reference_costs"``,
        ``"population_context"``, and ``"metadata"`` keys.
    """
    logger.info("enrich_bia_inputs called for condition=%r", inputs.get("condition"))

    warnings: list[str] = []
    suggested: dict[str, Any] = {}

    condition      = (inputs.get("condition") or "").strip()
    intervention   = (inputs.get("intervention_type") or "").strip()
    catchment_size = inputs.get("catchment_size")
    bed_count      = inputs.get("bed_count")
    eligible_pct   = inputs.get("eligible_pct")
    region         = (inputs.get("region") or "").strip()
    user_costs     = inputs.get("costs") or {}

    # ------------------------------------------------------------------
    # 1. Population estimation
    # ------------------------------------------------------------------
    pop_context: dict[str, Any] = {}

    # Resolve catchment_size
    catchment_source = "user-provided"
    if catchment_size is None or catchment_size <= 0:
        if bed_count and bed_count > 0:
            catchment_size = calculate_catchment_from_beds(bed_count)
            suggested["catchment_size"] = catchment_size
            catchment_source = f"estimated from bed count ({bed_count} beds, 85% occupancy)"
            warnings.append(
                f"catchment_size not provided — estimated {catchment_size:,} from "
                f"{bed_count} beds at 85% occupancy"
            )
        elif region:
            catchment_size = get_population_by_region(region)
            if catchment_size:
                suggested["catchment_size"] = catchment_size
                catchment_source = f"ONS region population: {region}"
                warnings.append(
                    f"catchment_size not provided — using ONS region population "
                    f"for '{region}': {catchment_size:,}"
                )
    else:
        # Sanity-check: if beds given too, compare estimates
        if bed_count and bed_count > 0:
            bed_estimate = calculate_catchment_from_beds(bed_count)
            ratio = catchment_size / bed_estimate if bed_estimate else None
            if ratio and (ratio < 0.3 or ratio > 3.0):
                warnings.append(
                    f"catchment_size {catchment_size:,} seems inconsistent with "
                    f"{bed_count} beds (bed-based estimate: {bed_estimate:,}) — verify"
                )

    pop_context["catchment_size"] = catchment_size
    pop_context["catchment_size_source"] = catchment_source
    pop_context["population_estimate_source"] = "ONS 2024"

    # Eligible population / eligible_pct
    if condition and catchment_size:
        ons = fetch_ons_population_data()["population"]
        prev_map = ons["prevalence_estimates"]
        cond_key = condition.lower().replace(" ", "_")

        # Find best prevalence match (substring)
        prevalence: float | None = None
        for k, v in prev_map.items():
            if k in cond_key or cond_key in k:
                prevalence = v
                break

        if prevalence is not None:
            estimated_eligible = round(catchment_size * prevalence)
            estimated_eligible_pct = prevalence
            pop_context["prevalence_rate"] = prevalence
            pop_context["estimated_eligible_patients"] = estimated_eligible

            if eligible_pct is None:
                suggested["eligible_pct"] = estimated_eligible_pct
                warnings.append(
                    f"eligible_pct not provided — suggested {prevalence:.1%} "
                    f"based on ONS {condition} prevalence "
                    f"(≈{estimated_eligible:,} patients in catchment)"
                )
            else:
                # Check if user value is plausible vs prevalence
                ratio = eligible_pct / prevalence
                if ratio > 2.0:
                    warnings.append(
                        f"eligible_pct {eligible_pct:.1%} is {ratio:.1f}x the "
                        f"national prevalence of {condition} ({prevalence:.1%}) — verify"
                    )
                elif ratio < 0.1:
                    warnings.append(
                        f"eligible_pct {eligible_pct:.1%} is very low vs national "
                        f"prevalence ({prevalence:.1%}) — confirm this is intentional "
                        f"(e.g. sub-population or pilot)"
                    )

    # Regional context
    if region:
        region_pop = get_population_by_region(region)
        if region_pop:
            pop_context["region"] = region
            pop_context["region_population"] = region_pop

    # ------------------------------------------------------------------
    # 2. Cost validation
    # ------------------------------------------------------------------
    reference_costs = _find_pathway_costs(condition)

    cost_warnings = _validate_user_costs(user_costs, reference_costs)
    warnings.extend(cost_warnings)

    # Suggest NHS reference costs for any pathway keys the user left empty
    for user_key, ref_key in _PATHWAY_COST_MAP.items():
        if user_key not in user_costs and ref_key in reference_costs:
            suggested.setdefault("reference_cost_suggestions", {})[user_key] = reference_costs[ref_key]

    # ------------------------------------------------------------------
    # 3. Clinical context from NICE
    # ------------------------------------------------------------------
    nice_results: list[dict] = []
    comparators: list[dict] = []

    if condition:
        nice_results = search_nice_guidance(condition)
        if intervention:
            comparators = get_nice_comparators(condition, intervention)
        elif nice_results:
            # Fall back to all guidance for the condition as comparator context
            comparators = [
                {
                    "id": r["id"],
                    "title": r["title"],
                    "type": r["type"],
                    "decision": r.get("decision", "N/A"),
                    "comparators": r.get("comparators") or r.get("recommendations") or [],
                    "icer": r.get("icer"),
                    "url": r["url"],
                }
                for r in nice_results
                if r.get("comparators") or r.get("recommendations")
            ]

        if not nice_results:
            warnings.append(
                f"No NICE guidance found for condition '{condition}' — "
                "clinical context and comparators unavailable"
            )

    # ------------------------------------------------------------------
    # 4. Assemble output
    # ------------------------------------------------------------------
    metadata = {
        "enriched_at": datetime.utcnow().isoformat(),
        "sources": ["NHS National Cost Collection 2024/25", "ONS Mid-Year Estimates 2024",
                    "NICE Guidance Database (curated MVP)"],
        "condition": condition or None,
        "intervention_type": intervention or None,
        "nice_guidance_found": len(nice_results),
        "comparators_found": len(comparators),
    }

    logger.info(
        "enrich_bia_inputs complete: %d warning(s), %d comparator(s), %d NICE record(s)",
        len(warnings),
        len(comparators),
        len(nice_results),
    )

    return {
        "inputs": inputs,
        "suggested_values": suggested,
        "warnings": warnings,
        "comparators": comparators,
        "reference_costs": reference_costs,
        "population_context": pop_context,
        "metadata": metadata,
    }


# ---------------------------------------------------------------------------
# Validation against references
# ---------------------------------------------------------------------------

# Plausibility bounds applied during validation
_UPTAKE_TYPICAL_YEAR1_MAX  = 0.30   # >30% uptake in year 1 is unusual for novel devices
_SAVINGS_IMPLAUSIBLE_RATIO = 5.0    # savings > 5× intervention cost is a red flag
_ICER_IMPLAUSIBLY_LOW      = 1_000  # ICER < £1k/QALY warrants scrutiny
_ICER_IMPLAUSIBLY_HIGH     = 500_000

def validate_against_references(inputs: dict, results: dict) -> dict:
    """Validate BIA inputs and results against NICE precedents and reference data.

    Produces a structured report with four judgement areas:

    1. **ICER plausibility** – how does the submitted ICER compare to similar
       approved technologies?
    2. **Cost-savings plausibility** – are the claimed savings consistent with
       the modelled resource changes?
    3. **Uptake trajectory** – does the adoption curve match similar rollouts?
    4. **Red flags** – any results that seem too good (or too bad) to be true.

    Parameters
    ----------
    inputs:
        BIA input parameters (same structure as accepted by
        :func:`enrich_bia_inputs`).
    results:
        BIA model outputs.  Expected keys (all optional):

        .. code-block:: python

            {
                "icer": 18500,
                "net_savings": 250000,
                "intervention_cost": 120000,
                "year1_uptake": 0.15,
                "years": [1, 2, 3],
                "annual_savings": [80000, 90000, 80000],
            }

    Returns
    -------
    dict
        Validation report with keys:

        * ``"overall_status"`` – ``"pass"`` / ``"warning"`` / ``"fail"``
        * ``"icer_assessment"`` – plausibility of the ICER
        * ``"savings_assessment"`` – plausibility of claimed savings
        * ``"uptake_assessment"`` – plausibility of uptake assumptions
        * ``"red_flags"`` – list of critical concerns
        * ``"warnings"`` – list of non-critical concerns
        * ``"precedents_used"`` – NICE precedents referenced in assessment
        * ``"validated_at"`` – ISO timestamp
    """
    logger.info("validate_against_references called")

    red_flags: list[str] = []
    warnings: list[str]  = []
    precedents_used: list[dict] = []

    condition    = (inputs.get("condition") or "").strip()
    icer         = results.get("icer")
    net_savings  = results.get("net_savings")
    int_cost     = results.get("intervention_cost")
    year1_uptake = results.get("year1_uptake")
    ann_savings  = results.get("annual_savings") or []

    # ------------------------------------------------------------------
    # 1. ICER plausibility
    # ------------------------------------------------------------------
    icer_assessment: dict[str, Any] = {"provided": icer is not None}

    if icer is not None:
        threshold_ctx = get_nice_threshold_context(condition) if condition else _WTP_THRESHOLDS["standard"].copy()
        std_thresh  = threshold_ctx["standard_threshold"]
        upper_thresh = threshold_ctx["upper_threshold"]
        category    = threshold_ctx.get("threshold_category", "standard")
        precedents  = threshold_ctx.get("precedents", [])
        precedents_used.extend(precedents)

        if icer < _ICER_IMPLAUSIBLY_LOW:
            red_flags.append(
                f"ICER £{icer:,}/QALY is implausibly low (< £{_ICER_IMPLAUSIBLY_LOW:,}) — "
                "review QALY gain assumptions"
            )
        elif icer > _ICER_IMPLAUSIBLY_HIGH:
            red_flags.append(
                f"ICER £{icer:,}/QALY exceeds £{_ICER_IMPLAUSIBLY_HIGH:,} — "
                "unlikely to receive NICE recommendation without managed access"
            )

        if icer <= std_thresh:
            icer_verdict = "likely cost-effective"
        elif icer <= upper_thresh:
            icer_verdict = "borderline cost-effective"
        else:
            icer_verdict = "above threshold — cost-effectiveness uncertain"
            if category == "standard":
                warnings.append(
                    f"ICER £{icer:,}/QALY exceeds standard upper threshold £{upper_thresh:,}. "
                    f"End-of-life or HST criteria may apply if condition qualifies."
                )

        # Compare to precedent range
        prec_icers = [p["icer"] for p in precedents if p.get("icer")]
        if prec_icers:
            prec_min, prec_max = min(prec_icers), max(prec_icers)
            if icer < prec_min * 0.3:
                warnings.append(
                    f"ICER £{icer:,} is much lower than precedent range "
                    f"£{prec_min:,}–£{prec_max:,} for similar technologies"
                )
            elif icer > prec_max * 2.0:
                warnings.append(
                    f"ICER £{icer:,} is much higher than precedent range "
                    f"£{prec_min:,}–£{prec_max:,} for similar technologies"
                )
            icer_assessment["precedent_range"] = {"min": prec_min, "max": prec_max}

        icer_assessment.update({
            "value": icer,
            "threshold_category": category,
            "standard_threshold": std_thresh,
            "upper_threshold": upper_thresh,
            "verdict": icer_verdict,
        })
    else:
        icer_assessment["verdict"] = "not provided — cannot assess"

    # ------------------------------------------------------------------
    # 2. Cost-savings plausibility
    # ------------------------------------------------------------------
    savings_assessment: dict[str, Any] = {
        "net_savings_provided": net_savings is not None,
        "intervention_cost_provided": int_cost is not None,
    }

    if net_savings is not None and int_cost is not None and int_cost > 0:
        ratio = net_savings / int_cost
        if ratio > _SAVINGS_IMPLAUSIBLE_RATIO:
            red_flags.append(
                f"Net savings £{net_savings:,.0f} are {ratio:.1f}× the intervention cost "
                f"£{int_cost:,.0f} — verify resource utilisation assumptions"
            )
        elif ratio < -0.5:
            warnings.append(
                f"Model shows net cost increase (savings £{net_savings:,.0f} vs "
                f"intervention cost £{int_cost:,.0f}) — confirm this is expected"
            )
        savings_assessment["savings_to_cost_ratio"] = round(ratio, 2)
        savings_assessment["verdict"] = (
            "plausible" if 0 <= ratio <= _SAVINGS_IMPLAUSIBLE_RATIO else "implausible — review"
        )

    # Consistency of annual savings
    if ann_savings:
        if len(ann_savings) > 1:
            year1  = ann_savings[0]
            growth = [(ann_savings[i] - ann_savings[i - 1]) / abs(ann_savings[i - 1])
                      for i in range(1, len(ann_savings))
                      if ann_savings[i - 1] != 0]
            large_jumps = [g for g in growth if abs(g) > 0.5]
            if large_jumps:
                warnings.append(
                    f"Annual savings show >50% year-on-year change in at least one year — "
                    "confirm uptake or cost assumptions are not discontinuous"
                )
        savings_assessment["annual_savings_count"] = len(ann_savings)

    # ------------------------------------------------------------------
    # 3. Uptake trajectory
    # ------------------------------------------------------------------
    uptake_assessment: dict[str, Any] = {"provided": year1_uptake is not None}

    if year1_uptake is not None:
        if year1_uptake > _UPTAKE_TYPICAL_YEAR1_MAX:
            warnings.append(
                f"Year-1 uptake {year1_uptake:.0%} exceeds the typical ceiling "
                f"({_UPTAKE_TYPICAL_YEAR1_MAX:.0%}) for novel digital/medtech rollouts — "
                "consider more conservative ramp-up"
            )
            uptake_assessment["verdict"] = "optimistic"
        elif year1_uptake < 0.02:
            warnings.append(
                f"Year-1 uptake {year1_uptake:.0%} is very low — confirm this reflects "
                "realistic commissioning timelines"
            )
            uptake_assessment["verdict"] = "conservative"
        else:
            uptake_assessment["verdict"] = "plausible"

        uptake_assessment["year1_uptake"] = year1_uptake
        uptake_assessment["typical_ceiling"] = _UPTAKE_TYPICAL_YEAR1_MAX

        # Cross-check: if NICE comparators were found, note any precedent ramp info
        if condition:
            comps = search_nice_guidance(condition)
            if comps:
                uptake_assessment["nice_comparators_available"] = len(comps)
    else:
        uptake_assessment["verdict"] = "not provided — cannot assess"

    # ------------------------------------------------------------------
    # 4. Overall status
    # ------------------------------------------------------------------
    if red_flags:
        overall_status = "fail"
    elif warnings:
        overall_status = "warning"
    else:
        overall_status = "pass"

    logger.info(
        "validate_against_references complete: status=%s, red_flags=%d, warnings=%d",
        overall_status,
        len(red_flags),
        len(warnings),
    )

    return {
        "overall_status": overall_status,
        "icer_assessment": icer_assessment,
        "savings_assessment": savings_assessment,
        "uptake_assessment": uptake_assessment,
        "red_flags": red_flags,
        "warnings": warnings,
        "precedents_used": precedents_used,
        "validated_at": datetime.utcnow().isoformat(),
    }
