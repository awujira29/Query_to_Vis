"""Exact category counts at the source, for when fetched results are truncated.

Fetching every trial for a big query (e.g. 17,000 breast cancer trials) is slow,
and analyzing only the first N is a biased sample: N results in the API's order.
For single-valued categorical dimensions the API can count exactly instead:
one tiny request per category, AREA[Field]VALUE with countTotal.

The queries are the same resolved expressions used to fetch the records, so the
exact counts and the sampled records share one definition of each series.
"""
from collections.abc import Callable

from app.client import count_studies
from app.schemas.shapes import Table

# Dimension -> API field usable in AREA[...]. Single-valued enum fields only:
# phase is excluded because a Phase 1/2 trial matches both PHASE1 and PHASE2.
SOURCE_COUNT_FIELDS = {
    "sponsor_class": "LeadSponsorClass",
    "overall_status": "OverallStatus",
    "study_type": "StudyType",
    "allocation": "DesignAllocation",
    "primary_purpose": "DesignPrimaryPurpose",
}

SKIP_KEYS = {"Other", "Not reported"}
SINGLE = "_single"      # series key for a plain bar chart Table (rows have no "series")


def _with_area(params: dict, field: str, value: str) -> dict:
    area = f"AREA[{field}]{value}"
    existing = params.get("filter.advanced")
    return {**params, "filter.advanced": f"({existing}) AND {area}" if existing else area}


def add_source_counts(table: Table, dimension: str, series_params: dict[str, dict],
                      series_totals: dict[str, int],
                      counter: Callable[[dict], int] = count_studies) -> Table:
    """Add exact `source_count` and `source_percent` to each row of a Table.

    `series_params` are the API params used to fetch each series, and
    `series_totals` the total matches per series; a plain bar chart Table (no
    "series" column) uses the key SINGLE. `counter` is injectable for tests.
    """
    field = SOURCE_COUNT_FIELDS[dimension]
    for row in table.rows:
        key = str(row[dimension])
        if key in SKIP_KEYS:
            row["source_count"] = None
            row["source_percent"] = None
            continue
        series = row.get("series", SINGLE)
        total = series_totals.get(series) or 0
        count = counter(_with_area(series_params[series], field, key))
        row["source_count"] = count
        row["source_percent"] = round(100 * count / total, 1) if total else 0.0

    table.columns.append(table.columns[-1].model_copy(update={"name": "source_count", "unit": "trials"}))
    table.columns.append(table.columns[-1].model_copy(update={"name": "source_percent", "unit": "% of source total"}))
    table.notes.append(
        "source_count / source_percent are exact counts from ClinicalTrials.gov for each "
        "category, using the same query as the records; trial_count / percent come from "
        "the analyzed sample, which was truncated.")
    return table
