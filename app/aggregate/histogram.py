"""histogram: TrialRecords -> Table of bins over one numeric measure.

Trial measures are heavy-tailed (most trials enroll under 100 people, a few registries
enroll millions), so bins follow fixed "nice" edges per measure rather than equal widths:
equal widths would put nearly everything in the first bar. The last bin is open-ended.
Every bin keeps its NCT IDs and cites the exact field (e.g. enrollmentInfo.count) of a
sample of its trials.
"""
from __future__ import annotations

from app.aggregate.count import MAX_IDS_PER_DATUM
from app.aggregate.citations import MAX_CITATIONS
from app.aggregate.measures import citations, label, summary, unit, value
from app.schemas.records import TrialRecord
from app.schemas.shapes import Column, Table

EDGES: dict[str, list[int]] = {
    "enrollment_count": [0, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 50000],
    "duration_months": [0, 6, 12, 24, 36, 48, 60, 84, 120, 180, 240],
    "site_count": [0, 1, 2, 3, 5, 10, 20, 50, 100, 200, 500],
    "country_count": [0, 1, 2, 3, 5, 10, 20, 30, 50],
    "arm_count": [0, 1, 2, 3, 4, 5, 7, 10, 20],
    "intervention_count": [0, 1, 2, 3, 4, 5, 7, 10, 20],
}


def _edges(measure: str, max_value: int) -> list[int]:
    """Fixed edges; the last one opens the final bin ("50,000+"). Edges beyond the
    data are dropped so a small set does not show a row of empty bins."""
    edges = list(EDGES.get(measure, [0, 1, 2, 5, 10, 20, 50, 100]))
    while len(edges) > 2 and edges[-2] > max_value:
        edges.pop()
    return edges


def _bin_label(lo: int, hi: int, is_last: bool) -> str:
    if is_last:
        return f"{lo:,}+"
    if hi - lo == 1:
        return f"{lo:,}"
    return f"{lo:,}-{hi - 1:,}"


def histogram(records: list[TrialRecord], measure: str) -> tuple[Table, dict]:
    """(Table, statistics). Count trials per bin of `measure`; bins are [lo, hi), the last
    one open-ended. Statistics (median, quartiles...) are computed on the raw values."""
    valued = [(value(r, measure), r) for r in records]
    valued = [(v, r) for v, r in valued if v is not None]
    missing = len(records) - len(valued)
    notes: list[str] = []
    if missing:
        notes.append(f"{missing} trials report no {label(measure).lower()} and are not binned.")
    if not valued:
        return Table(columns=[Column(name="bin", dtype="ordinal"),
                              Column(name="trial_count", dtype="quantitative", unit="trials")],
                     rows=[], total_records=len(records), missing_count=missing, notes=notes), {"n": 0}

    values = [v for v, _ in valued]
    edges = _edges(measure, max(values))
    rows = []
    for i in range(len(edges)):
        lo = edges[i]
        hi = edges[i + 1] if i + 1 < len(edges) else None
        members = [r for v, r in valued if v >= lo and (hi is None or v < hi)]
        if hi is not None and not members and lo > max(values):
            break
        ids = [r.nct_id for r in members]
        cites = [c for r in members[:MAX_CITATIONS] for c in citations(r, measure)][:MAX_CITATIONS]
        rows.append({
            "bin": _bin_label(lo, hi if hi is not None else lo, hi is None),
            "bin_start": lo,
            "bin_end": hi,                       # None: open-ended
            "trial_count": len(members),
            "nct_ids": ids[:MAX_IDS_PER_DATUM],
            "nct_ids_truncated": len(ids) > MAX_IDS_PER_DATUM,
            "citations": [c.model_dump() for c in cites],
        })
    # Drop empty trailing bins beyond the data, keep empty bins inside the range.
    while rows and rows[-1]["trial_count"] == 0 and len(rows) > 1:
        rows.pop()

    stats = summary(values)
    notes.append(f"{label(measure)} ({unit(measure) or 'count'}): median {stats['median']}, "
                 f"mean {stats['mean']}, range {stats['min']} to {stats['max']} "
                 f"across {stats['n']} trials.")
    notes.append("Bins use fixed edges because the values are heavy-tailed; bin_end is "
                 "exclusive and null for the open-ended last bin.")
    if any(row["nct_ids_truncated"] for row in rows):
        notes.append(f"NCT ID lists are capped at {MAX_IDS_PER_DATUM} per bin; "
                     "trial_count is always the full count.")
    return Table(
        columns=[Column(name="bin", dtype="ordinal", unit=unit(measure)),
                 Column(name="trial_count", dtype="quantitative", unit="trials")],
        rows=rows, total_records=len(records), missing_count=missing, notes=notes), stats
