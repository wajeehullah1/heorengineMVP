"""Tests for agents/evidence_agent.py.

Covers:
  1. NHS reference costs – structure, search, category filter, plausible ranges
  2. ONS population data – fetch, region lookup, prevalence maths, bed→catchment
  3. NICE guidance – search by condition/type/intervention, comparators, thresholds
  4. enrich_bia_inputs – suggested values, warnings for unrealistic inputs
  5. validate_against_references – ICER plausibility, savings ratio, uptake, red flags

Run with:
    pytest tests/test_evidence_agent.py -v
"""

import pytest

import agents.evidence_agent as ea
from agents.evidence_agent import (
    EvidenceCache,
    calculate_catchment_from_beds,
    enrich_bia_inputs,
    estimate_eligible_population,
    fetch_nhs_reference_costs,
    fetch_ons_population_data,
    get_cost_by_category,
    get_nice_comparators,
    get_nice_threshold_context,
    get_population_by_region,
    search_nice_guidance,
    search_reference_costs,
    validate_against_references,
)


# ── Isolation fixture ──────────────────────────────────────────────────
# Every test gets a fresh cache directory so tests never influence each other.

@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Redirect the module-level cache and REFERENCE_DIR to a temp directory."""
    cache = EvidenceCache(cache_dir=tmp_path)
    monkeypatch.setattr(ea, "_cache", cache)
    monkeypatch.setattr(ea, "REFERENCE_DIR", tmp_path)


# ====================================================================
# 1. NHS Reference Costs
# ====================================================================

class TestFetchNHSReferenceCosts:
    """Structural integrity of the returned payload."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.payload = fetch_nhs_reference_costs()
        self.costs = self.payload["costs"]
        self.meta = self.payload["metadata"]

    def test_returns_dict_with_costs_and_metadata(self):
        assert isinstance(self.costs, dict), "costs should be a dict"
        assert isinstance(self.meta, dict), "metadata should be a dict"

    def test_correct_total_item_count(self):
        assert len(self.costs) == 37, (
            f"Expected 37 cost items, got {len(self.costs)}"
        )

    def test_metadata_contains_required_keys(self):
        for key in ("source", "date_fetched", "currency", "notes"):
            assert key in self.meta, f"metadata missing key: {key}"

    def test_currency_is_gbp(self):
        assert self.meta["currency"] == "GBP"

    def test_source_references_nhs_national_cost_collection(self):
        assert "NHS National Cost Collection" in self.meta["source"]

    def test_all_costs_are_positive_numbers(self):
        for name, value in self.costs.items():
            assert isinstance(value, (int, float)), (
                f"Cost '{name}' should be numeric, got {type(value)}"
            )
            assert value > 0, f"Cost '{name}' should be > 0, got {value}"

    def test_second_call_returns_cached_data(self):
        """Cache should return identical data without rebuilding."""
        second = fetch_nhs_reference_costs()
        assert second["costs"] == self.costs, "Cached call returned different costs"


class TestSearchReferenceCosts:
    """Keyword search behaviour."""

    def test_search_bed_day_returns_seven_items(self):
        results = search_reference_costs("bed day")
        assert len(results) == 7, (
            f"Expected 7 bed_day items, got {len(results)}: {[r[0] for r in results]}"
        )

    def test_search_results_are_tuples_of_name_and_cost(self):
        for item in search_reference_costs("outpatient"):
            assert isinstance(item, tuple) and len(item) == 2, (
                f"Each result should be a (name, cost) tuple, got {item}"
            )
            name, cost = item
            assert isinstance(name, str)
            assert isinstance(cost, (int, float))

    def test_search_is_case_insensitive(self):
        lower = search_reference_costs("mri")
        upper = search_reference_costs("MRI")
        assert lower == upper, "Search should be case-insensitive"

    def test_search_treats_underscores_as_spaces(self):
        with_space = search_reference_costs("bed day")
        with_underscore = search_reference_costs("bed_day")
        assert set(r[0] for r in with_space) == set(r[0] for r in with_underscore)

    def test_search_mri_returns_single_item(self):
        results = search_reference_costs("mri")
        assert results == [("mri_scan", 168)], (
            f"Expected exactly [('mri_scan', 168)], got {results}"
        )

    def test_search_outpatient_returns_five_items(self):
        results = search_reference_costs("outpatient")
        assert len(results) == 5, (
            f"Expected 5 outpatient items, got {len(results)}"
        )

    def test_results_sorted_alphabetically_by_name(self):
        results = search_reference_costs("outpatient")
        names = [r[0] for r in results]
        assert names == sorted(names), "Results should be sorted alphabetically"

    def test_empty_query_returns_empty_list(self):
        assert search_reference_costs("") == []
        assert search_reference_costs("   ") == []

    def test_unknown_term_returns_empty_list(self):
        results = search_reference_costs("zzznomatch999")
        assert results == [], (
            f"Unknown search term should return [], got {results}"
        )


class TestCostRanges:
    """Sanity-check that key cost values are in plausible GBP ranges."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.costs = fetch_nhs_reference_costs()["costs"]

    @pytest.mark.parametrize("key,low,high", [
        ("bed_day_general_medicine", 300,  2_000),
        ("bed_day_icu",              1_000, 4_000),
        ("bed_day_hdu",              500,   2_500),
        ("bed_day_mental_health",    200,   1_000),
        ("outpatient_first_consultant", 50, 300),
        ("outpatient_followup_consultant", 30, 200),
        ("ed_minor_injury",          80,    400),
        ("ed_resuscitation",         200,   800),
        ("blood_test_basic",         1,     20),
        ("mri_scan",                 80,    400),
        ("ct_scan_head",             50,    250),
        ("theatre_hour_major",       500,   3_000),
        ("ambulance_convey",         100,   500),
        ("physiotherapy_session",    20,    100),
    ])
    def test_cost_within_plausible_range(self, key, low, high):
        value = self.costs[key]
        assert low <= value <= high, (
            f"'{key}' = £{value} is outside expected range £{low}–£{high}"
        )


class TestGetCostByCategory:
    """Category filtering."""

    @pytest.mark.parametrize("category,expected_count", [
        ("inpatient",   7),
        ("outpatient",  5),
        ("emergency",   3),
        ("diagnostics", 10),
        ("procedures",  5),
        ("ambulance",   3),
        ("community",   4),
    ])
    def test_correct_item_count_per_category(self, category, expected_count):
        result = get_cost_by_category(category)
        assert len(result) == expected_count, (
            f"Category '{category}': expected {expected_count} items, got {len(result)}"
        )

    def test_category_lookup_is_case_insensitive(self):
        lower = get_cost_by_category("diagnostics")
        upper = get_cost_by_category("DIAGNOSTICS")
        mixed = get_cost_by_category("Diagnostics")
        assert lower == upper == mixed

    def test_inpatient_contains_icu(self):
        result = get_cost_by_category("inpatient")
        assert "bed_day_icu" in result

    def test_unknown_category_returns_empty_dict(self):
        result = get_cost_by_category("not_a_real_category")
        assert result == {}


# ====================================================================
# 2. ONS Population Data
# ====================================================================

class TestFetchONSPopulationData:
    """Structural integrity of the returned payload."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.payload = fetch_ons_population_data()
        self.pop = self.payload["population"]
        self.meta = self.payload["metadata"]

    def test_returns_dict_with_population_and_metadata(self):
        assert "population" in self.payload
        assert "metadata" in self.payload

    def test_population_has_required_sub_keys(self):
        for key in ("uk_total", "england_regions", "age_bands", "prevalence_estimates"):
            assert key in self.pop, f"population missing key: {key}"

    def test_uk_total_is_plausible(self):
        total = self.pop["uk_total"]["total"]
        assert 65_000_000 <= total <= 70_000_000, (
            f"UK total {total:,} outside plausible range 65M–70M"
        )

    def test_england_wales_scotland_ni_sum_to_uk_total(self):
        uk = self.pop["uk_total"]
        parts_sum = uk["england"] + uk["wales"] + uk["scotland"] + uk["northern_ireland"]
        assert parts_sum == pytest.approx(uk["total"], rel=0.01), (
            "Nations should sum close to UK total"
        )

    def test_nine_england_regions(self):
        assert len(self.pop["england_regions"]) == 9, (
            f"Expected 9 England regions, got {len(self.pop['england_regions'])}"
        )

    def test_eighteen_age_bands(self):
        assert len(self.pop["age_bands"]) == 18, (
            f"Expected 18 age bands, got {len(self.pop['age_bands'])}"
        )

    def test_nine_prevalence_conditions(self):
        assert len(self.pop["prevalence_estimates"]) == 9, (
            f"Expected 9 prevalence estimates, got {len(self.pop['prevalence_estimates'])}"
        )

    def test_prevalence_rates_between_zero_and_one(self):
        for cond, rate in self.pop["prevalence_estimates"].items():
            assert 0 < rate < 1, (
                f"Prevalence for '{cond}' = {rate} should be between 0 and 1"
            )

    def test_metadata_source_references_ons(self):
        assert "ONS" in self.meta["source"]

    def test_second_call_returns_cached_data(self):
        second = fetch_ons_population_data()
        assert second["population"] == self.pop


class TestRegionLookup:
    """get_population_by_region — canonical keys and aliases."""

    @pytest.mark.parametrize("region,expected", [
        ("london",          8_980_000),
        ("LONDON",          8_980_000),
        ("London",          8_980_000),
        ("north_east",      2_650_000),
        ("North East",      2_650_000),
        ("south_east",      9_280_000),
        ("north_west",      7_420_000),
    ])
    def test_canonical_region_lookup(self, region, expected):
        result = get_population_by_region(region)
        assert result == expected, (
            f"Region '{region}': expected {expected:,}, got {result:,}"
        )

    @pytest.mark.parametrize("alias,expected", [
        ("Yorkshire",         5_540_000),
        ("East of England",   6_300_000),
        ("Yorkshire and Humber", 5_540_000),
    ])
    def test_alias_region_lookup(self, alias, expected):
        result = get_population_by_region(alias)
        assert result == expected, (
            f"Alias '{alias}': expected {expected:,}, got {result:,}"
        )

    def test_london_is_approximately_nine_million(self):
        pop = get_population_by_region("london")
        assert 8_000_000 <= pop <= 10_000_000, (
            f"London population {pop:,} should be roughly 8–10M"
        )

    def test_unknown_region_returns_zero(self):
        result = get_population_by_region("Narnia")
        assert result == 0, f"Unknown region should return 0, got {result}"

    def test_all_nine_regions_are_accessible(self):
        regions = [
            "north_east", "north_west", "yorkshire_humber", "east_midlands",
            "west_midlands", "east_england", "london", "south_east", "south_west",
        ]
        for r in regions:
            pop = get_population_by_region(r)
            assert pop > 0, f"Region '{r}' should have a population > 0"


class TestPrevalenceCalculations:
    """estimate_eligible_population — prevalence maths."""

    def test_diabetes_in_one_million_is_approximately_68k(self):
        result = estimate_eligible_population(1_000_000, "diabetes")
        assert result == pytest.approx(68_000, rel=0.01), (
            f"Diabetes in 1M should be ~68,000, got {result:,}"
        )

    def test_hypertension_in_one_million_is_approximately_280k(self):
        result = estimate_eligible_population(1_000_000, "hypertension")
        assert result == pytest.approx(280_000, rel=0.01), (
            f"Hypertension in 1M should be ~280,000, got {result:,}"
        )

    def test_case_insensitive_condition(self):
        lower = estimate_eligible_population(1_000_000, "diabetes")
        upper = estimate_eligible_population(1_000_000, "DIABETES")
        assert lower == upper

    def test_age_band_reduces_estimate(self):
        """Restricting to a single age band should give fewer patients than
        the whole-population estimate."""
        full = estimate_eligible_population(1_000_000, "diabetes")
        age_band = estimate_eligible_population(1_000_000, "diabetes", age_band="65-69")
        assert age_band < full, (
            "Age-restricted estimate should be less than full population estimate"
        )
        assert age_band > 0, "Age-restricted estimate should still be > 0"

    def test_unknown_age_band_falls_back_to_full_estimate(self):
        full = estimate_eligible_population(1_000_000, "diabetes")
        fallback = estimate_eligible_population(1_000_000, "diabetes", age_band="99-999")
        assert fallback == full, (
            "Unknown age band should fall back to full prevalence estimate"
        )

    def test_zero_population_returns_zero(self):
        assert estimate_eligible_population(0, "diabetes") == 0

    def test_unknown_condition_returns_zero(self):
        assert estimate_eligible_population(1_000_000, "purple_elephant") == 0

    def test_result_is_integer(self):
        result = estimate_eligible_population(500_000, "asthma")
        assert isinstance(result, int), f"Result should be int, got {type(result)}"


class TestCatchmentFromBeds:
    """calculate_catchment_from_beds — bed-based population estimates."""

    def test_100_beds_at_default_occupancy(self):
        result = calculate_catchment_from_beds(100)
        expected = round(100 / 0.85 * 500)
        assert result == expected, (
            f"100 beds at 85% occupancy: expected {expected:,}, got {result:,}"
        )

    def test_100_beds_full_occupancy(self):
        result = calculate_catchment_from_beds(100, bed_occupancy=1.0)
        assert result == 50_000, (
            f"100 beds at 100% occupancy: expected 50,000, got {result:,}"
        )

    def test_lower_occupancy_gives_larger_catchment(self):
        """At lower occupancy there is more slack in the system, so the
        formula (beds × 1/occupancy × 500) produces a larger catchment
        estimate — meaning the hospital serves a wider population relative
        to the beds actually in use."""
        low_occ = calculate_catchment_from_beds(100, bed_occupancy=0.60)
        high_occ = calculate_catchment_from_beds(100, bed_occupancy=0.95)
        assert low_occ > high_occ, (
            f"Lower occupancy (0.60) should produce a larger catchment than "
            f"higher occupancy (0.95): {low_occ:,} vs {high_occ:,}"
        )

    def test_bed_count_zero_returns_zero(self):
        assert calculate_catchment_from_beds(0) == 0

    def test_invalid_occupancy_defaults_to_85_pct(self):
        default = calculate_catchment_from_beds(100, bed_occupancy=0.85)
        invalid = calculate_catchment_from_beds(100, bed_occupancy=0.0)
        assert invalid == default, (
            "Occupancy=0 should default to 0.85 and give same result"
        )

    def test_result_is_integer(self):
        result = calculate_catchment_from_beds(250)
        assert isinstance(result, int)

    def test_result_is_plausible_scale(self):
        """300 acute beds in an average hospital → roughly 75k–250k catchment."""
        result = calculate_catchment_from_beds(300)
        assert 50_000 <= result <= 300_000, (
            f"300-bed hospital catchment {result:,} seems implausible"
        )


# ====================================================================
# 3. NICE Guidance
# ====================================================================

class TestSearchNICEGuidance:
    """search_nice_guidance — keyword and type filtering."""

    def test_empty_search_returns_all_records(self):
        results = search_nice_guidance("")
        assert len(results) == 15, (
            f"Empty search should return all 15 records, got {len(results)}"
        )

    def test_results_sorted_by_date_descending(self):
        results = search_nice_guidance("")
        dates = [r["date"] for r in results]
        assert dates == sorted(dates, reverse=True), (
            "Results should be sorted by date, most recent first"
        )

    def test_each_result_has_required_fields(self):
        required = {"id", "title", "type", "date", "condition", "url"}
        for record in search_nice_guidance(""):
            missing = required - record.keys()
            assert not missing, (
                f"Record {record.get('id')} is missing fields: {missing}"
            )

    def test_search_diabetes_returns_at_least_two_records(self):
        results = search_nice_guidance("diabetes")
        assert len(results) >= 2, (
            f"'diabetes' should match ≥2 records, got {len(results)}"
        )

    def test_search_stroke_returns_relevant_record(self):
        results = search_nice_guidance("stroke")
        assert any("stroke" in r["condition"].lower() or "stroke" in r["title"].lower()
                   for r in results), (
            "Search for 'stroke' should return at least one matching record"
        )

    def test_type_filter_ta_returns_five_technology_appraisals(self):
        results = search_nice_guidance("", guidance_type="ta")
        assert len(results) == 5
        assert all(r["type"] == "Technology Appraisal" for r in results), (
            "Type filter 'ta' should return only Technology Appraisals"
        )

    def test_type_filter_ng_returns_four_nice_guidelines(self):
        results = search_nice_guidance("", guidance_type="ng")
        assert len(results) == 4
        assert all(r["type"] == "NICE Guideline" for r in results)

    def test_type_filter_mib_returns_three_records(self):
        results = search_nice_guidance("", guidance_type="mib")
        assert len(results) == 3

    def test_type_filter_dg_returns_two_records(self):
        results = search_nice_guidance("", guidance_type="dg")
        assert len(results) == 2

    def test_combined_type_and_keyword_filter(self):
        results = search_nice_guidance("ai", guidance_type="ta")
        assert all(r["type"] == "Technology Appraisal" for r in results)
        assert len(results) >= 1

    def test_search_digital_returns_multiple_records(self):
        results = search_nice_guidance("digital")
        assert len(results) >= 2, (
            f"'digital' should match multiple records, got {len(results)}"
        )

    def test_search_remote_monitoring_returns_results(self):
        results = search_nice_guidance("remote monitoring")
        assert len(results) >= 1, (
            f"'remote monitoring' should match at least one record, got {len(results)}"
        )

    def test_unknown_search_returns_empty_list(self):
        results = search_nice_guidance("zzznomatch999xyzabc")
        assert results == []


class TestNICEComparators:
    """get_nice_comparators — comparator retrieval by condition + type."""

    def test_diabetes_digital_returns_comparators(self):
        results = get_nice_comparators("diabetes", "digital")
        assert len(results) >= 1, (
            "Should find at least one NICE comparator for digital diabetes interventions"
        )

    def test_each_comparator_has_required_fields(self):
        required = {"id", "title", "type", "decision", "comparators", "url"}
        for comp in get_nice_comparators("heart failure", "remote_monitoring"):
            missing = required - comp.keys()
            assert not missing, f"Comparator {comp.get('id')} missing fields: {missing}"

    def test_comparators_list_is_non_empty(self):
        results = get_nice_comparators("diabetes", "digital")
        for comp in results:
            assert comp["comparators"], (
                f"Comparator {comp['id']} has an empty comparators list"
            )

    def test_unknown_condition_returns_empty_list(self):
        results = get_nice_comparators("purple_elephant_disease", "digital")
        assert results == []

    def test_unknown_type_returns_empty_list(self):
        results = get_nice_comparators("diabetes", "teleportation")
        assert results == []


class TestNICEThresholds:
    """get_nice_threshold_context — WTP threshold lookup."""

    def test_standard_condition_gets_standard_threshold(self):
        ctx = get_nice_threshold_context("diabetes")
        assert ctx["threshold_category"] == "standard"
        assert ctx["standard_threshold"] == 25_000
        assert ctx["upper_threshold"] == 35_000

    def test_cancer_gets_end_of_life_threshold(self):
        ctx = get_nice_threshold_context("lung cancer")
        assert ctx["threshold_category"] == "end_of_life"
        assert ctx["upper_threshold"] == 50_000

    def test_dementia_gets_end_of_life_threshold(self):
        ctx = get_nice_threshold_context("dementia")
        assert ctx["threshold_category"] == "end_of_life"

    def test_sma_gets_highly_specialised_threshold(self):
        ctx = get_nice_threshold_context("spinal muscular atrophy")
        assert ctx["threshold_category"] == "highly_specialised"
        assert ctx["standard_threshold"] == 100_000

    def test_unknown_condition_defaults_to_standard(self):
        ctx = get_nice_threshold_context("plantar fasciitis")
        assert ctx["threshold_category"] == "standard"

    def test_response_has_required_keys(self):
        ctx = get_nice_threshold_context("diabetes")
        for key in ("standard_threshold", "upper_threshold", "threshold_category",
                    "description", "special_considerations", "precedents"):
            assert key in ctx, f"threshold context missing key: {key}"

    def test_precedents_are_sorted_by_icer_ascending(self):
        ctx = get_nice_threshold_context("diabetes")
        icers = [p["icer"] for p in ctx["precedents"] if p.get("icer")]
        assert icers == sorted(icers), (
            "Precedents should be sorted by ICER ascending"
        )

    def test_each_precedent_has_required_fields(self):
        ctx = get_nice_threshold_context("diabetes")
        required = {"id", "title", "icer", "decision", "condition", "threshold_category"}
        for prec in ctx["precedents"]:
            missing = required - prec.keys()
            assert not missing, f"Precedent {prec.get('id')} missing fields: {missing}"


# ====================================================================
# 4. enrich_bia_inputs
# ====================================================================

class TestEnrichBIAInputsStructure:
    """The return value always has the right shape."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.enriched = enrich_bia_inputs({
            "condition": "Heart Failure",
            "intervention_type": "remote_monitoring",
            "catchment_size": 250_000,
            "eligible_pct": 0.05,
            "region": "North West",
        })

    def test_has_all_required_top_level_keys(self):
        required = {
            "inputs", "suggested_values", "warnings",
            "comparators", "reference_costs", "population_context", "metadata",
        }
        missing = required - self.enriched.keys()
        assert not missing, f"Enriched output missing keys: {missing}"

    def test_inputs_are_preserved_unchanged(self):
        assert self.enriched["inputs"]["condition"] == "Heart Failure"
        assert self.enriched["inputs"]["catchment_size"] == 250_000

    def test_metadata_contains_provenance_info(self):
        meta = self.enriched["metadata"]
        assert "enriched_at" in meta
        assert "sources" in meta
        assert len(meta["sources"]) >= 2

    def test_metadata_condition_matches_input(self):
        assert self.enriched["metadata"]["condition"] == "Heart Failure"

    def test_reference_costs_includes_cardiac_keys(self):
        """Heart Failure condition → echocardiogram should be in reference costs."""
        assert "echocardiogram" in self.enriched["reference_costs"], (
            "Heart Failure pathway should include echocardiogram reference cost"
        )

    def test_population_context_has_catchment_size(self):
        ctx = self.enriched["population_context"]
        assert ctx["catchment_size"] == 250_000

    def test_population_context_includes_region_population(self):
        ctx = self.enriched["population_context"]
        assert ctx.get("region_population") == 7_420_000, (
            "North West region population should be 7,420,000"
        )

    def test_comparators_is_a_list(self):
        assert isinstance(self.enriched["comparators"], list)

    def test_warnings_is_a_list(self):
        assert isinstance(self.enriched["warnings"], list)


class TestEnrichBIAInputsSuggestedValues:
    """Suggested values are generated when inputs are incomplete."""

    def test_missing_catchment_estimated_from_bed_count(self):
        enriched = enrich_bia_inputs({"condition": "Diabetes", "bed_count": 200})
        suggested = enriched["suggested_values"]
        assert "catchment_size" in suggested, (
            "Should suggest catchment_size when it is absent but bed_count is provided"
        )
        assert suggested["catchment_size"] > 0

    def test_missing_catchment_falls_back_to_region_population(self):
        enriched = enrich_bia_inputs({"condition": "Hypertension", "region": "London"})
        ctx = enriched["population_context"]
        assert ctx["catchment_size"] == 8_980_000, (
            "Without catchment or beds, should use the ONS region population"
        )

    def test_missing_eligible_pct_suggested_from_ons_prevalence(self):
        enriched = enrich_bia_inputs({"condition": "Diabetes", "bed_count": 200})
        suggested = enriched["suggested_values"]
        assert "eligible_pct" in suggested, (
            "Should suggest eligible_pct from ONS prevalence when it is absent"
        )
        assert pytest.approx(suggested["eligible_pct"], rel=0.01) == 0.068

    def test_empty_inputs_returns_valid_structure(self):
        enriched = enrich_bia_inputs({})
        assert isinstance(enriched["suggested_values"], dict)
        assert isinstance(enriched["warnings"], list)
        assert enriched["metadata"]["condition"] is None


class TestEnrichBIAInputsWarnings:
    """Warnings are raised when inputs look implausible."""

    def test_warning_when_catchment_estimated_from_beds(self):
        enriched = enrich_bia_inputs({"condition": "Diabetes", "bed_count": 200})
        warn_text = " ".join(enriched["warnings"]).lower()
        assert "estimated" in warn_text, (
            "Should warn that catchment_size was estimated from bed count"
        )

    def test_warning_when_eligible_pct_much_higher_than_prevalence(self):
        """50% eligible_pct for diabetes is ~7× the national prevalence."""
        enriched = enrich_bia_inputs({
            "condition": "Diabetes",
            "catchment_size": 100_000,
            "eligible_pct": 0.50,
        })
        warn_text = " ".join(enriched["warnings"]).lower()
        assert "eligible_pct" in warn_text, (
            "Should warn when eligible_pct is much higher than national prevalence"
        )

    def test_warning_when_eligible_pct_very_low(self):
        """0.1% eligible_pct for hypertension (28% prevalence) is suspiciously low."""
        enriched = enrich_bia_inputs({
            "condition": "Hypertension",
            "catchment_size": 100_000,
            "eligible_pct": 0.001,
        })
        warn_text = " ".join(enriched["warnings"]).lower()
        assert "eligible_pct" in warn_text, (
            "Should warn when eligible_pct is unusually low vs national prevalence"
        )

    def test_warning_when_cost_far_below_nhs_reference(self):
        """£5 for an outpatient visit vs NHS reference of £92 is >50% off."""
        enriched = enrich_bia_inputs({
            "condition": "Stroke",
            "costs": {"outpatient_visit": 5},
        })
        warn_text = " ".join(enriched["warnings"]).lower()
        assert "outpatient_visit" in warn_text, (
            "Should warn when user cost deviates >50% from NHS reference"
        )

    def test_warning_when_cost_far_above_nhs_reference(self):
        """£5,000 for an outpatient visit is >50× the NHS reference."""
        enriched = enrich_bia_inputs({
            "condition": "Stroke",
            "costs": {"outpatient_visit": 5_000},
        })
        warn_text = " ".join(enriched["warnings"]).lower()
        assert "outpatient_visit" in warn_text

    def test_cost_within_50pct_of_reference_does_not_warn(self):
        """£80 for outpatient visit is 13% below the £92 reference — no warning."""
        enriched = enrich_bia_inputs({
            "condition": "Diabetes",
            "costs": {"outpatient_visit": 80},
        })
        cost_warns = [w for w in enriched["warnings"] if "outpatient_visit" in w]
        assert not cost_warns, (
            f"£80 vs £92 reference (<50% divergence) should not produce a warning, "
            f"got: {cost_warns}"
        )

    def test_catchment_inconsistent_with_beds_triggers_warning(self):
        """If user says catchment=10,000 but bed_count=500, that's suspicious."""
        enriched = enrich_bia_inputs({
            "condition": "Diabetes",
            "catchment_size": 10_000,
            "bed_count": 500,  # bed estimate ≈ 294,118 → ratio 0.034
        })
        warn_text = " ".join(enriched["warnings"]).lower()
        assert "catchment_size" in warn_text or "inconsistent" in warn_text, (
            "Should warn when user catchment_size is far from the bed-based estimate"
        )

    def test_unknown_condition_generates_no_nice_warning(self):
        """Graceful degradation — no crash, just a warning about missing guidance."""
        enriched = enrich_bia_inputs({"condition": "Plantar Fasciitis"})
        assert isinstance(enriched["warnings"], list)


# ====================================================================
# 5. validate_against_references
# ====================================================================

class TestValidatePassCase:
    """A realistic, well-formed result set should pass."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.report = validate_against_references(
            inputs={"condition": "Diabetes", "intervention_type": "digital"},
            results={
                "icer": 18_500,
                "net_savings": 200_000,
                "intervention_cost": 100_000,
                "year1_uptake": 0.12,
                "annual_savings": [60_000, 70_000, 70_000],
            },
        )

    def test_overall_status_is_pass_or_warning(self):
        assert self.report["overall_status"] in ("pass", "warning"), (
            f"Realistic inputs should not fail, got: {self.report['overall_status']}"
        )

    def test_icer_verdict_is_likely_cost_effective(self):
        assert self.report["icer_assessment"]["verdict"] == "likely cost-effective", (
            f"ICER £18,500 is well below the £25k threshold, "
            f"got: {self.report['icer_assessment']['verdict']}"
        )

    def test_savings_verdict_is_plausible(self):
        assert self.report["savings_assessment"]["verdict"] == "plausible", (
            f"2× savings ratio is plausible, "
            f"got: {self.report['savings_assessment']['verdict']}"
        )

    def test_savings_to_cost_ratio_is_correct(self):
        assert self.report["savings_assessment"]["savings_to_cost_ratio"] == pytest.approx(2.0)

    def test_uptake_verdict_is_plausible(self):
        assert self.report["uptake_assessment"]["verdict"] == "plausible", (
            f"12% year-1 uptake should be plausible, "
            f"got: {self.report['uptake_assessment']['verdict']}"
        )

    def test_no_red_flags(self):
        assert self.report["red_flags"] == [], (
            f"Should have no red flags, got: {self.report['red_flags']}"
        )

    def test_has_all_required_keys(self):
        required = {
            "overall_status", "icer_assessment", "savings_assessment",
            "uptake_assessment", "red_flags", "warnings",
            "precedents_used", "validated_at",
        }
        missing = required - self.report.keys()
        assert not missing, f"Report missing keys: {missing}"


class TestValidateICERPlausibility:
    """ICER-specific checks across the threshold bands."""

    def test_icer_below_25k_is_likely_cost_effective(self):
        report = validate_against_references({}, {"icer": 10_000})
        assert report["icer_assessment"]["verdict"] == "likely cost-effective"

    def test_icer_between_25k_and_35k_is_borderline(self):
        report = validate_against_references({"condition": "Diabetes"}, {"icer": 30_000})
        assert report["icer_assessment"]["verdict"] == "borderline cost-effective"

    def test_icer_above_35k_is_above_threshold(self):
        report = validate_against_references({"condition": "Diabetes"}, {"icer": 40_000})
        assert report["icer_assessment"]["verdict"] == "above threshold — cost-effectiveness uncertain"

    def test_icer_45k_for_eol_condition_is_borderline(self):
        """Under end-of-life rules the upper threshold is £50k."""
        report = validate_against_references({"condition": "lung cancer"}, {"icer": 45_000})
        assert report["icer_assessment"]["threshold_category"] == "end_of_life"
        assert report["icer_assessment"]["verdict"] == "borderline cost-effective"

    def test_implausibly_low_icer_triggers_red_flag(self):
        report = validate_against_references({}, {"icer": 50})
        assert any("implausibly low" in f.lower() for f in report["red_flags"]), (
            f"ICER £50 should trigger a red flag, got: {report['red_flags']}"
        )
        assert report["overall_status"] == "fail"

    def test_implausibly_high_icer_triggers_red_flag(self):
        report = validate_against_references({}, {"icer": 600_000})
        assert any("500,000" in f or "500000" in f.replace(",","")
                   for f in report["red_flags"]), (
            f"ICER £600k should trigger a red flag, got: {report['red_flags']}"
        )
        assert report["overall_status"] == "fail"

    def test_very_high_icer_100k_per_qaly_flagged(self):
        """£100k/QALY is above standard and EOL thresholds — unrealistic for most
        conditions without highly specialised consideration."""
        report = validate_against_references({"condition": "Diabetes"}, {"icer": 100_000})
        assert report["overall_status"] in ("warning", "fail"), (
            "£100k ICER for standard condition should generate at least a warning"
        )

    def test_missing_icer_does_not_crash(self):
        report = validate_against_references({}, {})
        assert report["icer_assessment"]["verdict"] == "not provided — cannot assess"


class TestValidateSavingsPlausibility:
    """Savings ratio and annual savings consistency checks."""

    def test_savings_five_times_cost_triggers_red_flag(self):
        report = validate_against_references({}, {
            "net_savings": 500_001,
            "intervention_cost": 100_000,
        })
        assert any("net savings" in f.lower() for f in report["red_flags"]), (
            f"Savings >5× cost should trigger red flag, got: {report['red_flags']}"
        )

    def test_savings_two_times_cost_is_plausible(self):
        report = validate_against_references({}, {
            "net_savings": 200_000,
            "intervention_cost": 100_000,
        })
        assert report["savings_assessment"]["verdict"] == "plausible"

    def test_net_cost_increase_generates_warning(self):
        """Net savings = -£60k means the intervention costs more than it saves
        (ratio = -0.6, which is below the -0.5 warning threshold)."""
        report = validate_against_references({}, {
            "net_savings": -60_000,
            "intervention_cost": 100_000,
        })
        assert len(report["warnings"]) > 0, (
            "Net cost increase (ratio < -0.5) should generate at least one warning"
        )

    def test_discontinuous_annual_savings_generates_warning(self):
        """Large year-on-year jumps in savings suggest discontinuous assumptions."""
        report = validate_against_references({}, {
            "annual_savings": [100_000, 200_000, 10_000],
            "net_savings": 310_000,
            "intervention_cost": 50_000,
        })
        warn_text = " ".join(report["warnings"]).lower()
        assert "year-on-year" in warn_text, (
            f"Discontinuous savings should generate a year-on-year warning, "
            f"got: {report['warnings']}"
        )

    def test_missing_savings_does_not_crash(self):
        report = validate_against_references({}, {})
        assert "savings_assessment" in report


class TestValidateUptakePlausibility:
    """Year-1 uptake assessment."""

    def test_uptake_above_30pct_is_flagged_as_optimistic(self):
        report = validate_against_references({}, {"year1_uptake": 0.80})
        assert report["uptake_assessment"]["verdict"] == "optimistic", (
            "80% year-1 uptake should be flagged as optimistic"
        )

    def test_optimistic_uptake_generates_warning(self):
        report = validate_against_references({}, {"year1_uptake": 0.80})
        assert len(report["warnings"]) > 0
        assert report["overall_status"] in ("warning", "fail")

    def test_uptake_below_2pct_is_flagged_as_conservative(self):
        report = validate_against_references({}, {"year1_uptake": 0.005})
        assert report["uptake_assessment"]["verdict"] == "conservative"

    def test_reasonable_uptake_12pct_is_plausible(self):
        report = validate_against_references({}, {"year1_uptake": 0.12})
        assert report["uptake_assessment"]["verdict"] == "plausible"

    def test_missing_uptake_does_not_crash(self):
        report = validate_against_references({}, {})
        assert report["uptake_assessment"]["verdict"] == "not provided — cannot assess"


class TestValidateOverallStatus:
    """overall_status aggregation logic."""

    def test_empty_results_pass(self):
        report = validate_against_references({}, {})
        assert report["overall_status"] == "pass"
        assert report["red_flags"] == []

    def test_red_flag_sets_status_to_fail(self):
        report = validate_against_references({}, {"icer": 10})
        assert report["overall_status"] == "fail"

    def test_warning_only_sets_status_to_warning(self):
        report = validate_against_references({}, {"year1_uptake": 0.80})
        assert report["overall_status"] == "warning"

    def test_validated_at_is_present(self):
        report = validate_against_references({}, {})
        assert "validated_at" in report
        assert isinstance(report["validated_at"], str)

    def test_precedents_used_is_a_list(self):
        report = validate_against_references({"condition": "Diabetes"}, {"icer": 18_500})
        assert isinstance(report["precedents_used"], list)
