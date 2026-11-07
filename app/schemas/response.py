"""The response a frontend receives: a visualization spec plus metadata."""
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.shapes import DType

VizType = Literal["bar_chart", "grouped_bar_chart", "time_series",
                  "histogram", "box_plot", "scatter_plot", "network_graph"]


class Channel(BaseModel):
    """How one data field maps to one visual channel (x, y, color, size...)."""
    field: str
    type: DType
    title: str | None = None             # axis or legend title
    unit: str | None = None
    sort: list[str] | None = None        # explicit category order, if any
    scale: Literal["linear", "log"] = "linear"


class VisualizationSpec(BaseModel):
    type: VizType
    title: str
    encoding: dict[str, Channel]         # e.g. {"x": ..., "y": ...}
    data: list[dict[str, Any]] | dict[str, Any]   # rows, or {"nodes", "edges"} for networks


class ResponseMeta(BaseModel):
    query: str
    filters: dict[str, Any] = {}
    source: str = "clinicaltrials.gov"
    api_queries: list[str] = []          # exact API URLs behind this answer
    total_available: int | None = None   # trials matching the filters at the source
    analyzed: int = 0                    # trials actually aggregated
    truncated: bool = False              # True if analyzed < total_available
    missing_count: int = 0               # analyzed trials with no value for the dimension
    time_granularity: Literal["year"] | None = None   # set for time series
    failed_records: int = 0              # studies dropped by validation
    resolutions: list[dict[str, Any]] = []   # how each drug/condition name was interpreted
    notes: list[str] = []
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class QueryResponse(BaseModel):
    status: Literal["ok", "no_results", "unsupported", "error"] = "ok"
    visualization: VisualizationSpec | None = None
    meta: ResponseMeta