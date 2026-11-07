"""Bar chart slice, no planner yet: one hand-written question, end to end.

Run from the project root:  python -m scripts.run_slice
"""
from app.aggregate.count import count_by
from app.builders.bar import build_bar, label
from app.client import fetch_studies
from app.normalize import normalize_studies
from app.query import Filters, build_query
from app.schemas.response import QueryResponse, ResponseMeta

QUERY = "How are melanoma trials testing nivolumab distributed across phases?"
FILTERS = Filters(condition="melanoma", drug="nivolumab")
DIMENSION = "phase"


def main() -> None:
    query = build_query(FILTERS)
    fetched = fetch_studies(query.params)
    records, failures = normalize_studies(fetched["studies"])
    table = count_by(records, DIMENSION)

    response = QueryResponse(
        status="ok" if records else "no_results",
        visualization=build_bar(table, DIMENSION, title=(
            f"Nivolumab melanoma trials by {label(DIMENSION).lower()}")) if records else None,
        meta=ResponseMeta(
            query=QUERY,
            filters=FILTERS.model_dump(exclude_defaults=True),
            api_queries=fetched["api_urls"],
            total_available=fetched["total_available"],
            analyzed=len(records),
            truncated=fetched["truncated"],
            missing_count=table.missing_count,
            failed_records=len(failures),
            resolutions=[r.model_dump(exclude={"candidates"}) for r in query.resolutions],
            notes=query.notes + table.notes,
        ),
    )
    print(response.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
