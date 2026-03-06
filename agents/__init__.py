"""HEOR Engine agents package."""

from agents.auto_populate import (
    AutoPopulator,
    auto_populate_bia,
    auto_populate_markov,
    validate_auto_population,
)
from agents.pubmed_agent import (
    PubMedAgent,
    extract_clinical_data,
    search_pubmed,
    synthesize_evidence,
)
from agents.nice_agent import (
    NICEAgent,
    extract_nice_data,
    get_comparator_costs,
    suggest_model_structure,
    search_nice_guidance as search_nice_guidance_web,
)
from agents.evidence_agent import (
    EvidenceCache,
    calculate_catchment_from_beds,
    download_file,
    enrich_bia_inputs,
    estimate_eligible_population,
    fetch_nhs_reference_costs,
    fetch_ons_population_data,
    get_cost_by_category,
    get_nice_comparators,
    get_nice_threshold_context,
    get_population_by_region,
    load_csv_to_dict,
    load_json,
    save_json,
    search_nice_guidance,
    search_reference_costs,
    validate_against_references,
)

__all__ = [
    # Auto-populate orchestrator
    "AutoPopulator",
    "auto_populate_bia",
    "auto_populate_markov",
    "validate_auto_population",
    # PubMed agent
    "PubMedAgent",
    "search_pubmed",
    "extract_clinical_data",
    "synthesize_evidence",
    # NICE agent
    "NICEAgent",
    "extract_nice_data",
    "get_comparator_costs",
    "suggest_model_structure",
    "search_nice_guidance_web",
    # Evidence agent (reference data)
    "EvidenceCache",
    "calculate_catchment_from_beds",
    "download_file",
    "enrich_bia_inputs",
    "estimate_eligible_population",
    "fetch_nhs_reference_costs",
    "fetch_ons_population_data",
    "get_cost_by_category",
    "get_nice_comparators",
    "get_nice_threshold_context",
    "get_population_by_region",
    "load_csv_to_dict",
    "load_json",
    "save_json",
    "search_nice_guidance",
    "search_reference_costs",
    "validate_against_references",
]
