"""The Plan: a question, interpreted into a closed vocabulary the pipeline can execute.

A Plan is the only thing the planner (LLM or rules) produces, and the only thing the
pipeline consumes. Everything in it is an enum or a bounded value, so a planner can
never ask for a dimension, chart or node type that does not exist: an invalid plan
fails validation here and is replaced by the rule-based planner's reading.

    intent          what kind of answer the question wants
    series          which trials: one Filters per series (two or more for comparisons)
    dimension       how to group them (distribution, comparison)
    network         which two entity types to link (network)
    measure / x / y which per-trial number to bin or plot (histogram, scatter)

The pipeline maps intent to visualization type deterministically:
    distribution -> bar_chart            trend (1 series)  -> time_series
    comparison   -> grouped_bar_chart    trend (2+ series) -> time_series (one line each)
    network      -> network_graph        histogram -> histogram, scatter -> scatter_plot
"""
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.dimensions import DIMENSIONS
from app.query import Filters

Intent = Literal["distribution", "trend", "comparison", "network", "histogram", "scatter"]
NodeType = Literal["drug", "sponsor", "condition"]

# Per-trial numeric fields a histogram can bin or a scatter plot can place.
Measure = Literal["enrollment_count", "duration_months", "site_count", "country_count",
                  "arm_count", "intervention_count", "start_year"]
MEASURES: tuple[str, ...] = Measure.__args__

MAX_SERIES = 4


class SeriesSpec(BaseModel):
    """One set of trials: a legend label and the filters that select it."""
    label: str = Field(min_length=1, max_length=80)
    filters: Filters

    @property
    def has_filters(self) -> bool:
        return bool(self.filters.model_dump(exclude_defaults=True))


class Plan(BaseModel):
    intent: Intent
    series: list[SeriesSpec] = Field(min_length=1, max_length=MAX_SERIES)
    dimension: str | None = None            # a key of app.dimensions.DIMENSIONS
    network: tuple[NodeType, NodeType] | None = None
    measure: Measure | None = None          # histogram
    x: Measure | None = None                # scatter
    y: Measure | None = None                # scatter
    color: str | None = None                # scatter: a single-valued dimension, or None
    top_n: int | None = Field(default=None, ge=1, le=100)
    title: str = Field(min_length=1, max_length=200)
    interpretation: str = Field(default="", max_length=500)   # the question, as understood

    @model_validator(mode="after")
    def _consistent(self) -> "Plan":
        labels = [s.label for s in self.series]
        if len(set(labels)) != len(labels):
            raise ValueError("series labels must be distinct")

        if self.intent in ("distribution", "comparison"):
            if self.dimension not in DIMENSIONS:
                raise ValueError(f"{self.intent} needs a dimension from "
                                 f"{sorted(DIMENSIONS)}; got {self.dimension!r}")
        if self.intent == "distribution" and len(self.series) != 1:
            raise ValueError("a distribution has exactly one series")
        if self.intent == "comparison" and len(self.series) < 2:
            raise ValueError("a comparison needs at least two series")

        if self.intent == "network":
            if not self.network:
                raise ValueError("a network needs two node types")
            a, b = self.network
            if a == b and a != "drug":
                raise ValueError("same-type networks are only defined for drugs")
            if len(self.series) != 1:
                raise ValueError("a network has exactly one series")

        if self.intent == "histogram" and not self.measure:
            raise ValueError("a histogram needs a measure")
        if self.intent == "scatter":
            if not (self.x and self.y):
                raise ValueError("a scatter plot needs x and y measures")
            if self.x == self.y:
                raise ValueError("scatter x and y must differ")
            if self.color is not None and self.color not in DIMENSIONS:
                raise ValueError(f"scatter color must be a dimension; got {self.color!r}")
        return self

    @property
    def visualization_type(self) -> str:
        return {
            "distribution": "bar_chart",
            "comparison": "grouped_bar_chart",
            "trend": "time_series",
            "network": "network_graph",
            "histogram": "histogram",
            "scatter": "scatter_plot",
        }[self.intent]
