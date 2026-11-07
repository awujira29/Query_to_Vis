"""Comparison slices, no planner yet. Two appendix questions:

  python -m scripts.run_compare drugs        # Compare phases: pembrolizumab vs nivolumab
  python -m scripts.run_compare conditions   # Sponsor categories: breast vs lung cancer
"""
import sys

from app.aggregate.compare import compare
from app.aggregate.source_counts import SOURCE_COUNT_FIELDS, add_source_counts
from app.builders.bar import label
from app.builders.grouped_bar import build_grouped_bar
from app.client import fetch_studies
from app.normalize import normalize_studies
from app.query import Filters, build_query
from app.schemas.response import QueryResponse, ResponseMeta

EXAMPLES = {
    "drugs": {
        "query": "Compare phases for trials involving pembrolizumab vs nivolumab.",
        "series": {"pembrolizumab": Filters(drug="pembrolizumab"),
                   "nivolumab": Filters(drug="nivolumab")},
        "dimension": "phase",
    },
    "conditions": {
        "query": "Compare sponsor categories across breast cancer and lung cancer trials.",
        "series": {"breast cancer": Filters(condition="breast cancer"),
                   "lung cancer": Filters(condition="lung cancer")},
        "dimension": "sponsor_class",
    },
}
MAX_PER_SERIES = 5000


def main(name: str) -> None:
    ex = EXAMPLES[name]
    series, series_params, series_totals = {}, {}, {}
    api_urls, notes, resolutions = [], [], []
    total_available, truncated, failed = 0, False, 0

    for series_label, filters in ex["series"].items():
        query = build_query(filters)
        fetched = fetch_studies(query.params, max_studies=MAX_PER_SERIES)
        records, failures = normalize_studies(fetched["studies"])

        series[series_label] = records
        series_params[series_label] = query.params
        series_totals[series_label] = fetched["total_available"] or 0
        resolutions += [r.model_dump(exclude={"candidates"}) for r in query.resolutions]
        notes += [f"{series_label}: {n}" for n in query.notes]
        api_urls += fetched["api_urls"]
        total_available += fetched["total_available"] or 0
        truncated = truncated or fetched["truncated"]
        failed += len(failures)
        if fetched["truncated"]:
            notes.append(f"{series_label}: only the first {MAX_PER_SERIES} of "
                         f"{fetched['total_available']} trials were analyzed.")

    table = compare(series, ex["dimension"])
    measure = "trial_count"
    if truncated and ex["dimension"] in SOURCE_COUNT_FIELDS:
        table = add_source_counts(table, ex["dimension"], series_params, series_totals)
        measure = "source_percent"

    response = QueryResponse(
        status="ok" if table.rows else "no_results",
        visualization=build_grouped_bar(
            table, ex["dimension"], title=f"{label(ex['dimension'])}: {' vs '.join(series)}",
            measure=measure),
        meta=ResponseMeta(
            query=ex["query"],
            filters={"series": {k: f.model_dump(exclude_defaults=True)
                                for k, f in ex["series"].items()},
                     "dimension": ex["dimension"]},
            api_queries=api_urls,
            total_available=total_available,
            analyzed=sum(len(r) for r in series.values()),
            truncated=truncated,
            missing_count=table.missing_count,
            failed_records=failed,
            resolutions=resolutions,
            notes=notes + table.notes,
        ),
    )
    print(response.model_dump_json(indent=2))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "drugs")
