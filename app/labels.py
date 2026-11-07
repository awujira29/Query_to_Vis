"""Display labels for raw API codes. Raw codes stay in the data and citations;
labels are only for what a reader sees on an axis or legend."""

DISPLAY = {
    "sponsor_class": {
        "OTHER": "Academic / Other",
        "INDUSTRY": "Industry",
        "NIH": "NIH",
        "OTHER_GOV": "Other government",
        "FED": "US federal",
        "NETWORK": "Network / Cooperative group",
        "INDIV": "Individual",
        "AMBIG": "Ambiguous",
        "UNKNOWN": "Unknown",
    },
    "overall_status": {
        "RECRUITING": "Recruiting", "NOT_YET_RECRUITING": "Not yet recruiting",
        "ACTIVE_NOT_RECRUITING": "Active, not recruiting", "COMPLETED": "Completed",
        "TERMINATED": "Terminated", "WITHDRAWN": "Withdrawn", "SUSPENDED": "Suspended",
        "ENROLLING_BY_INVITATION": "Enrolling by invitation", "UNKNOWN": "Unknown status",
    },
    "study_type": {
        "INTERVENTIONAL": "Interventional", "OBSERVATIONAL": "Observational",
        "EXPANDED_ACCESS": "Expanded access",
    },
    "allocation": {"RANDOMIZED": "Randomized", "NON_RANDOMIZED": "Non-randomized", "NA": "Not applicable"},
    "masking": {"NONE": "None (open label)", "SINGLE": "Single", "DOUBLE": "Double",
                "TRIPLE": "Triple", "QUADRUPLE": "Quadruple"},
    "intervention_type": {
        "DRUG": "Drug", "BIOLOGICAL": "Biological", "DEVICE": "Device",
        "PROCEDURE": "Procedure", "RADIATION": "Radiation", "BEHAVIORAL": "Behavioral",
        "GENETIC": "Genetic", "DIETARY_SUPPLEMENT": "Dietary supplement",
        "COMBINATION_PRODUCT": "Combination product", "DIAGNOSTIC_TEST": "Diagnostic test",
        "OTHER": "Other",
    },
}


def display(dimension: str, key) -> str:
    """Human-readable label for a group key; unknown codes pass through unchanged."""
    return DISPLAY.get(dimension, {}).get(str(key), str(key))
