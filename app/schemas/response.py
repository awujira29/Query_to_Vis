"""The response a frontend receives: a visualization spec plus metadata.

Contract, in one paragraph: `visualization.type` says which renderer to use;
`visualization.encoding` maps named visual channels (x, y, color, node_size...) to field
names in `visualization.data`; every datum in `data` carries `nct_ids` and `citations`
back to the source records; `meta` says which trials were counted, how the question
was interpreted (`plan`, `planner`, `resolutions`) and every caveat (`notes`).
"""
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.shapes import DType

VizType = Literal["bar_chart", "grouped_bar_chart", "time_series",
                  "histogram", "scatter_plot", "network_graph"]


class Channel(BaseModel):
    """How one data field maps to one visual channel (x, y, color, size...)."""
    field: str
    type: DType
    title: str | None = None             # axis or legend title
    unit: str | None = None
    sort: list[str] | None = None        # explicit category order, if any
    scale: Literal["linear", "log", "symlog"] = "linear"   # symlog: log-like, but zero is allowed


class VisualizationSpec(BaseModel):
    type: VizType
    title: str
    encoding: dict[str, Channel]         # e.g. {"x": ..., "y": ...}
    data: list[dict[str, Any]] | dict[str, Any]   # rows or points, or {"nodes", "edges"} for networks


class SeriesMeta(BaseModel):
    """What was fetched for one series (one set of trials)."""
    label: str
    filters: dict[str, Any] = {}
    api_queries: list[str] = []          # exact API URLs behind this series
    total_available: int | None = None   # trials matching the filters at the source
    analyzed: int = 0                    # trials actually aggregated
    truncated: bool = False              # analyzed < total_available
    failed_records: int = 0              # studies dropped by validation
    resolutions: list[dict[str, Any]] = []   # how each drug/condition name was interpreted


class ResponseMeta(BaseModel):
    query: str
    planner: Literal["llm", "rules", "none"] = "none"   # who interpreted the question
    plan: dict[str, Any] | None = None   # the interpretation: intent, dimension, series...
    filters: dict[str, Any] = {}         # filters of the single series, or {"series": {...}}
    series: list[SeriesMeta] = []
    source: str = "clinicaltrials.gov"
    api_queries: list[str] = []          # all API URLs, across series
    total_available: int | None = None   # sum across series
    analyzed: int = 0                    # sum across series
    truncated: bool = False              # any series truncated
    missing_count: int = 0               # analyzed trials with no value for the dimension/measure
    time_granularity: Literal["year"] | None = None   # set for time series
    failed_records: int = 0
    resolutions: list[dict[str, Any]] = []   # all resolutions, across series
    render_hints: dict[str, Any] = {}    # presentation hints, e.g. network layout, y measure
    statistics: dict[str, Any] = {}      # histogram / scatter summaries (median, quartiles, r)
    notes: list[str] = []                # every assumption, filter and caveat, in plain words
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class QueryResponse(BaseModel):
    status: Literal["ok", "no_results", "unsupported", "error"] = "ok"
    message: str | None = None           # set when status is not "ok"
    visualization: VisualizationSpec | None = None
    meta: ResponseMeta
