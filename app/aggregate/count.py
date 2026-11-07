"""count_by: Grouping -> Table, for bar charts (and the base of time series)."""
from app.aggregate.citations import citations_for
from app.aggregate.group import group_by
from app.dimensions import MULTI_VALUED
from app.schemas.records import NO_PHASE_LABEL, PHASE_LABELS, PHASE_ORDER, TrialRecord
from app.schemas.shapes import Column, Table

MAX_IDS_PER_DATUM = 100   # full list for small groups; capped for large ones

OTHER_LABEL = "Other"
MISSING_LABEL = "Not reported"

ORDINAL_DIMENSIONS = {"phase", "start_year"}
TEMPORAL_DIMENSIONS = {"start_year"}

_PHASE_INDEX = {PHASE_LABELS[p]: i for i, p in enumerate(PHASE_ORDER)}


def _phase_sort_key(label: str) -> tuple:
    """'Phase 1/Phase 2' sorts right after 'Phase 1'; 'No Phase Listed' last."""
    if label == NO_PHASE_LABEL:
        return (len(PHASE_ORDER), 0)
    parts = [_PHASE_INDEX.get(p, len(PHASE_ORDER)) for p in label.split("/")]
    return (parts[0], len(parts))


def _natural_order(dimension: str, keys: list[str]) -> list[str] | None:
    """Fixed order for ordered dimensions; None means sort by count."""
    if dimension == "phase":
        return sorted(keys, key=_phase_sort_key)
    if dimension == "start_year":
        return sorted(keys, key=int)
    return None


def _row(dimension: str, key: str, members: list[TrialRecord], cite_dimension: str | None) -> dict:
    ids = [r.nct_id for r in members]
    return {
        dimension: key,
        "trial_count": len(members),
        "nct_ids": ids[:MAX_IDS_PER_DATUM],
        "nct_ids_truncated": len(ids) > MAX_IDS_PER_DATUM,
        "citations": ([c.model_dump() for c in citations_for(members, cite_dimension, key)]
                      if cite_dimension else []),
    }


def count_by(records: list[TrialRecord], dimension: str, top_n: int | None = None,
             include_missing: bool = True) -> Table:
    """Count trials per group of `dimension`.

    - Ordered dimensions (phase, year) keep their natural order; others sort by count.
    - top_n keeps the largest groups and folds the rest into "Other".
    - Trials with no value become a "Not reported" row, or just a note.
    """
    grouping = group_by(records, dimension)
    notes: list[str] = []

    natural = _natural_order(dimension, list(grouping.groups))
    if natural is not None:
        kept, folded = natural, []        # ordered dimensions are never truncated
    else:
        by_count = sorted(grouping.groups, key=lambda k: (-len(grouping.groups[k]), k))
        cut = top_n if top_n and len(by_count) > top_n else len(by_count)
        kept, folded = by_count[:cut], by_count[cut:]

    rows = []

    for key in kept:
        rows.append(_row(dimension, key, grouping.groups[key], dimension))

    if folded:
        seen, members = set(), []
        for key in folded:
            for r in grouping.groups[key]:
                if r.nct_id not in seen:
                    seen.add(r.nct_id)
                    members.append(r)
        rows.append(_row(dimension, OTHER_LABEL, members, None))
        notes.append(f"{len(folded)} smaller groups combined into '{OTHER_LABEL}'.")

    if grouping.missing:
        if include_missing:
            rows.append(_row(dimension, MISSING_LABEL, grouping.missing, None))
        notes.append(f"{len(grouping.missing)} trials have no value for '{dimension}'.")

    if any(row["nct_ids_truncated"] for row in rows):
        notes.append(f"NCT ID lists are capped at {MAX_IDS_PER_DATUM} per datum; "
                     "trial_count is always the full count.")

    if dimension in MULTI_VALUED:
        notes.append(f"A trial can count in several '{dimension}' groups, "
                     "so counts may sum to more than the number of trials.")

    if dimension in TEMPORAL_DIMENSIONS:
        dtype = "temporal"
    elif dimension in ORDINAL_DIMENSIONS:
        dtype = "ordinal"
    else:
        dtype = "nominal"

    return Table(
        columns=[Column(name=dimension, dtype=dtype),
                 Column(name="trial_count", dtype="quantitative", unit="trials")],
        rows=rows,
        total_records=len(records),
        missing_count=len(grouping.missing),
        notes=notes,
    )