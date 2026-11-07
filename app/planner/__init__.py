"""Planner: request -> Plan, with a record of how the plan was made.

Order of authority, highest first:
  1. the request's structured fields and overrides (visualization_type, dimension, top_n)
  2. the language model's reading of the question (if a key is configured and its plan
     passes validation and grounding)
  3. the rule-based planner's reading

The result says which planner produced the plan and why any fallback happened, so a
reader of the response can judge the interpretation, not just the numbers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import ValidationError

from app import llm
from app.planner.llm_planner import llm_plan
from app.planner.rules import rules_plan
from app.query import Filters
from app.schemas.plan import Plan, SeriesSpec
from app.schemas.request import QueryRequest

PlannerName = Literal["llm", "rules", "none"]

STRUCTURED_TO_FILTER = {"drug_name": "drug", "condition": "condition", "sponsor": "sponsor",
                        "country": "country", "status": "status", "trial_phase": "phases",
                        "start_year": "start_year", "end_year": "end_year"}

INTENT_FOR_TYPE = {"bar_chart": "distribution", "grouped_bar_chart": "comparison",
                   "time_series": "trend", "network_graph": "network",
                   "histogram": "histogram", "scatter_plot": "scatter"}


@dataclass
class Planning:
    plan: Plan | None
    planner: PlannerName
    notes: list[str] = field(default_factory=list)
    llm_error: str | None = None


def _set(series: SeriesSpec, **changes) -> SeriesSpec:
    filters = Filters.model_validate({**series.filters.model_dump(), **changes})
    return series.model_copy(update={"filters": filters})


def apply_structured_fields(plan: Plan, request: QueryRequest) -> tuple[Plan, list[str]]:
    """Make the plan agree with the request's structured fields.

    For each field the client set: if no series uses it, it is added to every series; if
    all series share one other value, the client's value replaces it (and a note says so);
    if the series differ on it, that field is the comparison axis and is left alone.
    """
    notes: list[str] = []
    series = list(plan.series)
    for req_field, filt_field in STRUCTURED_TO_FILTER.items():
        wanted = getattr(request, req_field)
        if wanted in (None, [], ""):
            continue
        values = [getattr(s.filters, filt_field) for s in series]
        if all(v in (None, []) for v in values):
            series = [_set(s, **{filt_field: wanted}) for s in series]
        elif len({repr(v) for v in values}) == 1:
            if repr(values[0]) != repr(wanted):
                notes.append(f"Structured field {req_field}={wanted!r} overrides "
                             f"{values[0]!r} read from the question.")
                series = [_set(s, **{filt_field: wanted}) for s in series]
        else:
            if all(repr(v) != repr(wanted) for v in values):
                notes.append(f"Structured field {req_field}={wanted!r} was not applied: the "
                             f"series differ on {filt_field} ({values}), which is the comparison.")
    return plan.model_copy(update={"series": series}), notes


def apply_overrides(plan: Plan, request: QueryRequest) -> tuple[Plan, list[str]]:
    """Honor visualization_type, dimension and top_n from the request where the plan allows."""
    notes: list[str] = []
    changes: dict = {}
    if request.top_n:
        changes["top_n"] = request.top_n
    if request.dimension and plan.intent in ("distribution", "comparison"):
        changes["dimension"] = request.dimension
    elif request.dimension and plan.intent not in ("distribution", "comparison"):
        notes.append(f"dimension={request.dimension!r} was ignored: a {plan.visualization_type} "
                     "has no grouping dimension.")
    if request.visualization_type:
        intent = INTENT_FOR_TYPE[request.visualization_type]
        if intent != plan.intent:
            changes["intent"] = intent
            if intent in ("distribution", "comparison") and not plan.dimension:
                changes["dimension"] = request.dimension or "phase"
            if intent == "network" and not plan.network:
                changes["network"] = ("drug", "drug") if plan.series[0].filters.drug else ("sponsor", "drug")
            if intent == "histogram" and not plan.measure:
                changes["measure"] = "enrollment_count"
            if intent == "scatter" and not (plan.x and plan.y):
                changes.update(x="start_year", y="enrollment_count", color="phase")
            if intent in ("distribution", "network", "histogram", "scatter", "trend") and len(plan.series) > 1 \
                    and intent != "trend":
                changes["series"] = plan.series[:1]
                notes.append(f"visualization_type={request.visualization_type!r} shows one series; "
                             f"kept {plan.series[0].label!r}.")
    if not changes:
        return plan, notes
    try:
        updated = Plan.model_validate({**plan.model_dump(), **changes})
    except ValidationError as e:
        notes.append(f"Requested overrides could not be applied ({str(e).splitlines()[0][:120]}); "
                     "kept the planner's reading.")
        return plan, notes
    if "intent" in changes:
        notes.append(f"visualization_type={request.visualization_type!r} requested; the planner "
                     f"read the question as {plan.visualization_type}.")
    return updated, notes


def make_plan(request: QueryRequest) -> Planning:
    notes: list[str] = []
    plan: Plan | None = None
    planner: PlannerName = "none"
    llm_error = None

    if llm.configured():
        result = llm_plan(request)
        plan, llm_error = result.plan, result.error
        if plan:
            planner = "llm"
        elif result.off_topic:
            # The model's judgment that this is not a trials question is kept: the
            # rule-based planner would only find a spurious entity in the words.
            return Planning(None, "llm", [f"The question was judged {llm_error.rstrip('.')}."], llm_error)
        else:
            notes.append(f"Language-model planner not used ({llm_error}); "
                         "the rule-based planner interpreted the question.")
    if plan is None:
        plan = rules_plan(request)
        if plan:
            planner = "rules"

    if plan is None:
        notes.append("Could not tell which trials the question is about. Name a drug, "
                     "condition, sponsor or country, or pass one as a structured field.")
        return Planning(None, "none", notes, llm_error)

    plan, more = apply_structured_fields(plan, request)
    notes += more
    plan, more = apply_overrides(plan, request)
    notes += more
    return Planning(plan, planner, notes, llm_error)
