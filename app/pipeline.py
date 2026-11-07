"""Pipeline: QueryRequest -> QueryResponse. One path for every question class.

    plan      planner (LLM, validated; else rules)      -> Plan
    fetch     per series: resolve names -> API params -> pages -> TrialRecords
    aggregate by intent: Table / PointSet / Graph       (all numbers computed here)
    build     shape -> VisualizationSpec                 (presentation only)
    report    metadata: what was fetched, how names were read, every caveat

Nothing numeric is ever produced by a language model: the LLM only proposes a Plan
and MeSH terms, both of which are validated against closed vocabularies and
registry evidence before use.

Reliability rules enforced here:
  - a series that fetched fewer trials than exist is marked truncated, the response
    says so, and for single-valued categorical dimensions the chart switches to exact
    per-category counts from the API (same query, no sampling bias);
  - an empty result is "no_results", an un-plannable question is "unsupported", an
    API failure is "error": never an empty chart presented as an answer;
  - every note from every stage ends up in meta.notes, prefixed by series when a
    response has several.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from app.aggregate.compare import compare
from app.aggregate.count import count_by
from app.aggregate.histogram import histogram
from app.aggregate.network import focus_id, network
from app.aggregate.points import points
from app.aggregate.source_counts import SINGLE, SOURCE_COUNT_FIELDS, add_source_counts
from app.aggregate.timeseries import time_series
from app.builders.bar import build_bar
from app.builders.grouped_bar import build_grouped_bar
from app.builders.histogram import build_histogram
from app.builders.network import build_network, render_hints
from app.builders.scatter import build_scatter
from app.builders.time_series import build_time_series
from app.client import ApiError, fetch_studies
from app.normalize import normalize_studies
from app.planner import Planning, make_plan
from app.query import Query, build_query
from app.schemas.plan import Plan, SeriesSpec
from app.schemas.records import TrialRecord
from app.schemas.request import QueryRequest
from app.schemas.response import QueryResponse, ResponseMeta, SeriesMeta, VisualizationSpec
from app.schemas.shapes import Table

logger = logging.getLogger(__name__)

Fetcher = Callable[..., dict]


@dataclass
class FetchedSeries:
    spec: SeriesSpec
    query: Query
    records: list[TrialRecord]
    total_available: int | None
    truncated: bool
    api_urls: list[str]
    failed: int

    @property
    def label(self) -> str:
        return self.spec.label

    def meta(self) -> SeriesMeta:
        return SeriesMeta(
            label=self.label, filters=self.spec.filters.model_dump(exclude_defaults=True),
            api_queries=self.api_urls, total_available=self.total_available,
            analyzed=len(self.records), truncated=self.truncated, failed_records=self.failed,
            resolutions=[r.model_dump(exclude={"candidates"}) for r in self.query.resolutions])


@dataclass
class Built:
    """What an intent handler returns: the spec plus the metadata only it knows."""
    spec: VisualizationSpec | None
    notes: list[str] = field(default_factory=list)
    missing_count: int = 0
    render_hints: dict = field(default_factory=dict)
    statistics: dict = field(default_factory=dict)
    time_granularity: str | None = None


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_series(spec: SeriesSpec, max_studies: int, fetcher: Fetcher = fetch_studies) -> FetchedSeries:
    query = build_query(spec.filters)
    fetched = fetcher(query.params, max_studies=max_studies)
    records, failures = normalize_studies(fetched["studies"])
    return FetchedSeries(spec=spec, query=query, records=records,
                         total_available=fetched.get("total_available"),
                         truncated=bool(fetched.get("truncated")), api_urls=fetched.get("api_urls", []),
                         failed=len(failures))


def _union(series: list[FetchedSeries]) -> list[TrialRecord]:
    seen: set[str] = set()
    out = []
    for s in series:
        for r in s.records:
            if r.nct_id not in seen:
                seen.add(r.nct_id)
                out.append(r)
    return out


def _prefixed(series: list[FetchedSeries], notes_of: Callable[[FetchedSeries], list[str]]) -> list[str]:
    many = len(series) > 1
    return [f"{s.label}: {n}" if many else n for s in series for n in notes_of(s)]


# ---------------------------------------------------------------------------
# Intent handlers
# ---------------------------------------------------------------------------

def _distribution(plan: Plan, series: list[FetchedSeries], request: QueryRequest) -> Built:
    s = series[0]
    dimension = plan.dimension
    table = count_by(s.records, dimension, top_n=plan.top_n)
    if not table.rows:
        return Built(None, notes=table.notes, missing_count=table.missing_count)
    measure = "trial_count"
    notes = list(table.notes)
    if s.truncated and dimension in SOURCE_COUNT_FIELDS:
        table = add_source_counts(table, dimension, {SINGLE: s.query.params},
                                  {SINGLE: s.total_available or 0})
        measure = "source_count"
        notes = list(table.notes)
    elif s.truncated:
        notes.append(f"Counts come from the first {len(s.records)} of {s.total_available} matching "
                     "trials (API order), so they describe a sample, not the whole set; raise "
                     "max_studies for a complete count.")
    return Built(build_bar(table, dimension, plan.title, measure=measure), notes=notes,
                 missing_count=table.missing_count, render_hints={"y_measure": measure})


def _comparison(plan: Plan, series: list[FetchedSeries], request: QueryRequest) -> Built:
    dimension = plan.dimension
    table = compare({s.label: s.records for s in series}, dimension, top_n=plan.top_n)
    if not table.rows:
        return Built(None, notes=table.notes, missing_count=table.missing_count)
    measure = "percent" if len(series) > 1 else "trial_count"
    notes = list(table.notes)
    if any(s.truncated for s in series) and dimension in SOURCE_COUNT_FIELDS:
        table = add_source_counts(table, dimension, {s.label: s.query.params for s in series},
                                  {s.label: s.total_available or 0 for s in series})
        measure = "source_percent"
        notes = list(table.notes)
    elif any(s.truncated for s in series):
        notes.append("Some series were truncated: percentages describe the analyzed sample "
                     "(first trials in API order), not the whole set.")
    return Built(build_grouped_bar(table, dimension, plan.title, measure=measure), notes=notes,
                 missing_count=table.missing_count, render_hints={"y_measure": measure})


def _trend(plan: Plan, series: list[FetchedSeries], request: QueryRequest) -> Built:
    tables = {}
    for s in series:
        f = s.spec.filters
        tables[s.label] = time_series(s.records, start_year=f.start_year, end_year=f.end_year)
    if len(series) == 1:
        table = tables[series[0].label]
        if not table.rows:
            return Built(None, notes=table.notes, missing_count=table.missing_count)
        return Built(build_time_series(table, plan.title), notes=table.notes,
                     missing_count=table.missing_count, time_granularity="year")

    # Several series: align all lines on one span of years, zero-filled.
    years = sorted({row["start_year"] for t in tables.values() for row in t.rows})
    if not years:
        return Built(None, notes=[n for t in tables.values() for n in t.notes])
    rows, notes = [], []
    for label, t in tables.items():
        by_year = {row["start_year"]: row for row in t.rows}
        for year in range(years[0], years[-1] + 1):
            row = by_year.get(year, {"trial_count": 0, "estimated_count": 0, "nct_ids": [],
                                     "nct_ids_truncated": False, "citations": [],
                                     "partial_year": False})
            rows.append({**row, "start_year": year, "series": label})
        notes += [f"{label}: {n}" for n in t.notes if "capped" not in n]
    if any(row.get("nct_ids_truncated") for row in rows):
        notes.append("NCT ID lists are capped per datum; trial_count is always the full count.")
    table = Table(columns=tables[series[0].label].columns, rows=rows,
                  total_records=sum(len(s.records) for s in series),
                  missing_count=sum(t.missing_count for t in tables.values()), notes=notes)
    return Built(build_time_series(table, plan.title), notes=notes,
                 missing_count=table.missing_count, time_granularity="year")


def _network(plan: Plan, series: list[FetchedSeries], request: QueryRequest) -> Built:
    records = _union(series)
    pair = plan.network
    f = series[0].spec.filters
    focus = None
    if "drug" in pair and f.drug:
        focus = focus_id("drug", f.drug)
    elif "sponsor" in pair and f.sponsor:
        focus = focus_id("sponsor", f.sponsor)
    elif "condition" in pair and f.condition:
        resolved = next((r for r in series[0].query.resolutions
                         if r.kind == "condition" and r.terms), None)
        focus = focus_id("condition", resolved.terms[0] if resolved else f.condition)
    same = pair[0] == pair[1]
    n = len(records)
    min_edge = max(2, n // 250) if same else max(2, n // 700)
    top_n = plan.top_n or (25 if same else 15)
    graph = network(records, pair, focus=focus, top_n=top_n, min_edge_weight=min_edge)
    if not graph.nodes:
        return Built(None, notes=graph.notes, missing_count=graph.missing_count)
    return Built(build_network(graph, plan.title), notes=graph.notes,
                 missing_count=graph.missing_count, render_hints=render_hints(graph))


def _histogram(plan: Plan, series: list[FetchedSeries], request: QueryRequest) -> Built:
    records = _union(series)
    table, stats = histogram(records, plan.measure)
    if not table.rows:
        return Built(None, notes=table.notes, missing_count=table.missing_count)
    return Built(build_histogram(table, plan.measure, plan.title), notes=table.notes,
                 missing_count=table.missing_count, statistics={plan.measure: stats})


def _scatter(plan: Plan, series: list[FetchedSeries], request: QueryRequest) -> Built:
    records = _union(series)
    pointset, stats = points(records, plan.x, plan.y, plan.color)
    if not pointset.points:
        return Built(None, notes=pointset.notes, missing_count=pointset.missing_count)
    return Built(build_scatter(pointset, plan.x, plan.y, plan.color, plan.title),
                 notes=pointset.notes, missing_count=pointset.missing_count, statistics=stats)


HANDLERS = {"distribution": _distribution, "comparison": _comparison, "trend": _trend,
            "network": _network, "histogram": _histogram, "scatter": _scatter}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _plan_meta(planning: Planning) -> dict | None:
    if planning.plan is None:
        return None
    plan = planning.plan
    return {
        "intent": plan.intent, "visualization_type": plan.visualization_type,
        "dimension": plan.dimension, "network": list(plan.network) if plan.network else None,
        "measure": plan.measure, "x": plan.x, "y": plan.y, "color": plan.color,
        "top_n": plan.top_n, "title": plan.title, "interpretation": plan.interpretation,
        "series": [{"label": s.label, "filters": s.filters.model_dump(exclude_defaults=True)}
                   for s in plan.series],
    }


def _filters_meta(plan: Plan) -> dict:
    if len(plan.series) == 1:
        return plan.series[0].filters.model_dump(exclude_defaults=True)
    return {"series": {s.label: s.filters.model_dump(exclude_defaults=True) for s in plan.series}}


def answer(request: QueryRequest, *, fetcher: Fetcher = fetch_studies,
           planner: Callable[[QueryRequest], Planning] = make_plan) -> QueryResponse:
    """Interpret, fetch, aggregate, build and explain. Never raises for API problems."""
    planning = planner(request)
    base_meta = {"query": request.query, "planner": planning.planner, "plan": _plan_meta(planning)}

    if planning.plan is None:
        return QueryResponse(status="unsupported", message=" ".join(planning.notes),
                             meta=ResponseMeta(**base_meta, notes=planning.notes))
    plan = planning.plan

    try:
        series = [fetch_series(s, request.max_studies, fetcher) for s in plan.series]
    except ApiError as e:
        logger.error("ClinicalTrials.gov request failed: %s", e)
        return QueryResponse(status="error", message=f"ClinicalTrials.gov request failed: {e}",
                             meta=ResponseMeta(**base_meta, filters=_filters_meta(plan),
                                               notes=planning.notes))

    notes = list(planning.notes)
    notes += _prefixed(series, lambda s: s.query.notes)
    for s in series:
        if s.truncated:
            notes.append(f"{s.label + ': ' if len(series) > 1 else ''}only the first "
                         f"{len(s.records)} of {s.total_available} matching trials were analyzed "
                         f"(max_studies={request.max_studies}).")
        if s.failed:
            notes.append(f"{s.label + ': ' if len(series) > 1 else ''}{s.failed} studies were "
                         "dropped because their records failed validation.")

    meta_common = dict(
        **base_meta, filters=_filters_meta(plan), series=[s.meta() for s in series],
        api_queries=[u for s in series for u in s.api_urls],
        total_available=sum(s.total_available or 0 for s in series),
        analyzed=sum(len(s.records) for s in series), truncated=any(s.truncated for s in series),
        failed_records=sum(s.failed for s in series),
        resolutions=[r for s in series for r in s.meta().resolutions],
    )

    if not any(s.records for s in series):
        message = "No trials match these filters."
        return QueryResponse(status="no_results", message=message,
                             meta=ResponseMeta(**meta_common, notes=notes + [message]))

    built = HANDLERS[plan.intent](plan, series, request)
    notes += built.notes
    if built.spec is None:
        message = "Trials were found, but none has the information this chart needs."
        return QueryResponse(status="no_results", message=message,
                             meta=ResponseMeta(**meta_common, missing_count=built.missing_count,
                                               notes=notes + [message]))

    return QueryResponse(
        status="ok",
        visualization=built.spec,
        meta=ResponseMeta(**meta_common, missing_count=built.missing_count,
                          time_granularity=built.time_granularity, render_hints=built.render_hints,
                          statistics=built.statistics, notes=notes),
    )
