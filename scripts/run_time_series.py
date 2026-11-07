"""Time series slice, no planner yet:
"How has the number of trials for pembrolizumab changed per year since 2015?"

Run from the project root:  python -m scripts.run_time_series
"""
from app.aggregate.timeseries import time_series
from app.builders.time_series import build_time_series
from app.client import fetch_studies
from app.normalize import normalize_studies
from app.query import Filters, build_query
from app.schemas.response import QueryResponse, ResponseMeta

QUERY = "How has the number of trials for pembrolizumab changed per year since 2015?"
FILTERS = Filters(drug="pembrolizumab", start_year=2015)


def main() -> None:
    query = build_query(FILTERS)                     # the year filter goes to the API too
    fetched = fetch_studies(query.params, max_studies=5000)
    records, failures = normalize_studies(fetched["studies"])
    table = time_series(records, start_year=FILTERS.start_year)

    response = QueryResponse(
        status="ok" if table.rows else "no_results",
        visualization=build_time_series(
            table, f"Pembrolizumab trials started per year since {FILTERS.start_year}")
        if table.rows else None,
        meta=ResponseMeta(
            query=QUERY,
            filters=FILTERS.model_dump(exclude_defaults=True),
            api_queries=fetched["api_urls"],
            total_available=fetched["total_available"],
            analyzed=len(records),
            truncated=fetched["truncated"],
            missing_count=table.missing_count,
            time_granularity="year",
            failed_records=len(failures),
            resolutions=[r.model_dump(exclude={"candidates"}) for r in query.resolutions],
            notes=query.notes + table.notes,
        ),
    )
    print(response.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
