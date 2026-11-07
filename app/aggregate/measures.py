"""Per-trial numeric measures shared by histograms and scatter plots, with their evidence.

A measure is a number each TrialRecord has (or lacks): enrollment, duration, number of
sites... `value()` reads it; `evidence()` returns the exact API fields it came from, so a
bin or a point can cite its source records like every other datum.
"""
from __future__ import annotations

import statistics

from app.aggregate.citations import cite
from app.normalize import CONTACTS_PATH, ARMS_PATH
from app.schemas.records import TrialRecord
from app.schemas.shapes import Citation

MEASURE_LABELS = {
    "enrollment_count": ("Enrollment", "participants"),
    "duration_months": ("Duration", "months"),
    "site_count": ("Sites", "sites"),
    "country_count": ("Countries", "countries"),
    "arm_count": ("Arms", "arms"),
    "intervention_count": ("Interventions", "interventions"),
    "start_year": ("Start year", None),
}


def label(measure: str) -> str:
    return MEASURE_LABELS.get(measure, (measure.replace("_", " ").capitalize(), None))[0]


def unit(measure: str) -> str | None:
    return MEASURE_LABELS.get(measure, (None, None))[1]


def value(record: TrialRecord, measure: str) -> int | None:
    v = getattr(record, measure)
    return int(v) if v is not None else None


def evidence(record: TrialRecord, measure: str) -> list[tuple[str, str]]:
    """(json_path, exact_value) pairs that produce this record's measure value."""
    if measure == "enrollment_count" and record.enrollment_count is not None:
        return [(record.scalar_sources.get("enrollment_count", "enrollmentInfo.count"),
                 str(record.enrollment_count))]
    if measure == "start_year" and record.start:
        return [(f"{record.start.source_path}.date", record.start.raw)]
    if measure == "duration_months" and record.start:
        end = record.completion or record.primary_completion
        if end:
            return [(f"{record.start.source_path}.date", record.start.raw),
                    (f"{end.source_path}.date", end.raw)]
    if measure == "site_count":
        return [(f"{CONTACTS_PATH}.locations", f"{record.site_count} locations")]
    if measure == "country_count":
        return [(f"{CONTACTS_PATH}.locations[*].country", ", ".join(record.countries))]
    if measure == "arm_count":
        return [(f"{ARMS_PATH}.armGroups", f"{record.arm_count} arm groups")]
    if measure == "intervention_count":
        return [(f"{ARMS_PATH}.interventions", f"{record.intervention_count} interventions")]
    return []


def citations(record: TrialRecord, measure: str) -> list[Citation]:
    return [cite(record, path, val) for path, val in evidence(record, measure)]


def summary(values: list[int | float]) -> dict:
    """Descriptive statistics a reader expects next to a histogram or scatter axis."""
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    q = statistics.quantiles(ordered, n=4, method="inclusive") if len(ordered) >= 2 else [ordered[0]] * 3
    return {
        "n": len(ordered),
        "min": ordered[0],
        "p25": round(q[0], 1),
        "median": round(statistics.median(ordered), 1),
        "p75": round(q[2], 1),
        "max": ordered[-1],
        "mean": round(statistics.fmean(ordered), 1),
    }
