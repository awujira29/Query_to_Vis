"""Dimension registry: dimension name -> function returning a record's group keys.

Every extractor returns a list:
- single-valued dimensions return one key, e.g. ["Phase 2"]
- multi-valued dimensions return several, e.g. ["Japan", "United States"]
- missing data returns [], and the aggregator decides how to report it
"""
from collections.abc import Callable

from app.schemas.records import TrialRecord

Extractor = Callable[[TrialRecord], list[str]]


def _one(value) -> list[str]:
    """Wrap a single value as a key list; None becomes []."""
    return [str(value)] if value is not None else []


DIMENSIONS: dict[str, Extractor] = {
    # Single-valued
    "phase":             lambda r: [r.phase_label],
    "overall_status":    lambda r: _one(r.overall_status),
    "study_type":        lambda r: _one(r.study_type),
    "allocation":        lambda r: _one(r.allocation),
    "primary_purpose":   lambda r: _one(r.primary_purpose),
    "masking":           lambda r: _one(r.masking),
    "has_results":       lambda r: ["With results" if r.has_results else "No results"],
    "start_year":        lambda r: _one(r.start_year),
    "sponsor_class":     lambda r: _one(r.lead_sponsor.sponsor_class if r.lead_sponsor else None),
    "lead_sponsor":      lambda r: _one(r.lead_sponsor.name_normalized if r.lead_sponsor else None),

    # Multi-valued
    "country":           lambda r: r.countries,
    "drug":              lambda r: r.drugs,
    "intervention_type": lambda r: r.intervention_types,
    "condition":         lambda r: sorted({c.term_normalized for c in r.conditions if c.origin == "mesh"}),
}

MULTI_VALUED = {"country", "drug", "intervention_type", "condition"}