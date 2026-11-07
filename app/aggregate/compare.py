"""compare: the same dimension counted across several series (drugs, conditions...).

Each series is fetched and filtered separately by the caller; this function only
groups and merges, so a grouped bar chart gets one row per (category, series).
"""
from app.aggregate.count import MISSING_LABEL, OTHER_LABEL, _natural_order, _row
from app.aggregate.group import group_by
from app.dimensions import MULTI_VALUED
from app.schemas.records import TrialRecord
from app.schemas.shapes import Column, Table


def compare(series: dict[str, list[TrialRecord]], dimension: str,
            top_n: int | None = None, include_missing: bool = True) -> Table:
    """Count `dimension` per series.

    - Every series gets every category, with zeros, so groups line up in the chart.
    - Ordered dimensions keep natural order; others sort by total across series.
    - Each row has trial_count and percent (share of that series' trials), since
      raw counts mostly show which series is bigger.
    - Trials appearing in more than one series are reported in the notes.
    """
    notes: list[str] = []
    groupings = {label: group_by(records, dimension) for label, records in series.items()}

    totals: dict[str, int] = {}
    for g in groupings.values():
        for key, members in g.groups.items():
            totals[key] = totals.get(key, 0) + len(members)

    natural = _natural_order(dimension, list(totals))
    if natural is not None:
        kept, folded = natural, []
    else:
        by_total = sorted(totals, key=lambda k: (-totals[k], k))
        cut = top_n if top_n and len(by_total) > top_n else len(by_total)
        kept, folded = by_total[:cut], by_total[cut:]

    rows = []
    for label, records in series.items():
        g = groupings[label]
        size = len(records)

        def add(key, members, cite):
            row = _row(dimension, key, members, dimension if cite else None)
            row["series"] = label
            row["percent"] = round(100 * len(members) / size, 1) if size else 0.0
            rows.append(row)

        for key in kept:
            add(key, g.groups.get(key, []), cite=True)

        if folded:
            seen, members = set(), []
            for key in folded:
                for r in g.groups.get(key, []):
                    if r.nct_id not in seen:
                        seen.add(r.nct_id)
                        members.append(r)
            add(OTHER_LABEL, members, cite=False)

        if include_missing and any(groupings[s].missing for s in series):
            add(MISSING_LABEL, g.missing, cite=False)

    if folded:
        notes.append(f"{len(folded)} smaller groups combined into '{OTHER_LABEL}'.")
    for label, g in groupings.items():
        if g.missing:
            notes.append(f"{label}: {len(g.missing)} trials have no value for '{dimension}'.")

    ids = {label: {r.nct_id for r in records} for label, records in series.items()}
    labels = list(series)
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            shared = len(ids[a] & ids[b])
            if shared:
                notes.append(f"{shared} trials appear in both '{a}' and '{b}' "
                             "and are counted in each.")

    notes.append("percent is the share of each series' trials, so series of "
                 "different sizes can be compared.")
    if dimension in MULTI_VALUED:
        notes.append(f"A trial can count in several '{dimension}' groups, so "
                     "percentages within a series may sum to more than 100.")
    if any(row["nct_ids_truncated"] for row in rows):
        notes.append("NCT ID lists are capped per datum; trial_count is always the full count.")

    dim_type = "ordinal" if dimension == "phase" else "nominal"
    return Table(
        columns=[Column(name=dimension, dtype=dim_type),
                 Column(name="series", dtype="nominal"),
                 Column(name="trial_count", dtype="quantitative", unit="trials"),
                 Column(name="percent", dtype="quantitative", unit="% of series")],
        rows=rows,
        total_records=sum(len(r) for r in series.values()),
        missing_count=sum(len(g.missing) for g in groupings.values()),
        notes=notes,
    )
