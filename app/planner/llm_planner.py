"""LLM planner: the question -> a Plan, proposed by a language model and checked by code.

The model's job is narrow: read the question and fill in a form whose every field is a
closed vocabulary (intents, dimensions, node types, measures, status and phase codes).
It never sees data, computes a number or writes the chart. What it returns is then:

  1. parsed as JSON (the client tolerates fences and surrounding text),
  2. normalized leniently ("Phase 2" -> PHASE2, "recruiting" -> RECRUITING),
  3. validated against the Plan schema (unknown values are rejected, not coerced),
  4. grounded: every drug, condition, sponsor or country it names must appear in the
     question or the structured fields, so it cannot "correct" Keytruda to pembrolizumab
     (that is the resolver's job, with registry evidence) or invent an entity.

Any failure returns (None, reason) and the rule-based planner takes over. The system
prompt is built from the same registries the code uses, so the vocabulary offered to the
model can never drift from what the pipeline can execute.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date

from pydantic import ValidationError

from app import llm
from app.dimensions import DIMENSIONS
from app.planner.rules import COUNTRIES
from app.schemas.plan import MEASURES, Plan
from app.schemas.records import Phase
from app.schemas.request import STATUSES, QueryRequest, parse_phase

logger = logging.getLogger(__name__)

DIMENSION_HELP = {
    "phase": "trial phase (Phase 1, Phase 2, Phase 1/Phase 2 ...)",
    "overall_status": "recruitment status (RECRUITING, COMPLETED ...)",
    "study_type": "interventional / observational / expanded access",
    "allocation": "randomized / non-randomized",
    "primary_purpose": "treatment, prevention, diagnostic ...",
    "masking": "open label, single, double blind ...",
    "has_results": "whether results are posted",
    "start_year": "year the trial started (use intent 'trend' instead when asked over time)",
    "sponsor_class": "lead sponsor category: industry, NIH, academic/other ...",
    "lead_sponsor": "the lead sponsor organization by name",
    "country": "countries with trial sites",
    "drug": "drugs tested (drug-like interventions)",
    "intervention_type": "DRUG, BIOLOGICAL, DEVICE, PROCEDURE, BEHAVIORAL ...",
    "condition": "conditions studied (MeSH terms)",
}

# Few-shot examples use entities and phrasings that are NOT in examples/ or the tests,
# so example runs and tests measure the model, not its memory of the prompt.
EXAMPLES = [
    ("How many diabetes trials started each year since 2010?", {},
     {"intent": "trend", "series": [{"label": "diabetes", "filters": {"condition": "diabetes", "start_year": 2010}}],
      "title": "Diabetes trials started per year since 2010",
      "interpretation": "Count diabetes trials by start year from 2010 on."}),
    ("Which countries run the most recruiting asthma trials?", {},
     {"intent": "distribution", "dimension": "country", "top_n": 15,
      "series": [{"label": "asthma", "filters": {"condition": "asthma", "status": ["RECRUITING"]}}],
      "title": "Recruiting asthma trials by country",
      "interpretation": "Count currently recruiting asthma trials per country with a site."}),
    ("Compare sponsor types for trials of semaglutide and tirzepatide", {},
     {"intent": "comparison", "dimension": "sponsor_class",
      "series": [{"label": "semaglutide", "filters": {"drug": "semaglutide"}},
                 {"label": "tirzepatide", "filters": {"drug": "tirzepatide"}}],
      "title": "Sponsor class: semaglutide vs tirzepatide",
      "interpretation": "Share of trials by lead sponsor class, one series per drug."}),
    ("Show which drugs are tested together with atezolizumab", {},
     {"intent": "network", "network": ["drug", "drug"],
      "series": [{"label": "atezolizumab", "filters": {"drug": "atezolizumab"}}],
      "title": "Drugs given together with atezolizumab",
      "interpretation": "Drug-drug network from arms that give atezolizumab with another drug."}),
    ("Which institutions study which diseases in trials run by Novartis?", {},
     {"intent": "network", "network": ["sponsor", "condition"],
      "series": [{"label": "Novartis", "filters": {"sponsor": "Novartis"}}],
      "title": "Sponsors and conditions in Novartis trials",
      "interpretation": "Lead sponsors linked to the conditions of trials where Novartis is involved."}),
    ("What's the typical enrollment of phase 3 Alzheimer's trials?", {},
     {"intent": "histogram", "measure": "enrollment_count",
      "series": [{"label": "Alzheimer's", "filters": {"condition": "Alzheimer's", "phases": ["PHASE3"]}}],
      "title": "Enrollment of phase 3 Alzheimer's trials",
      "interpretation": "Distribution of enrollment counts across phase 3 Alzheimer's trials."}),
    ("Is there a relationship between enrollment and duration for rheumatoid arthritis trials?", {},
     {"intent": "scatter", "x": "duration_months", "y": "enrollment_count", "color": "phase",
      "series": [{"label": "rheumatoid arthritis", "filters": {"condition": "rheumatoid arthritis"}}],
      "title": "Enrollment vs duration of rheumatoid arthritis trials",
      "interpretation": "One point per trial: duration against enrollment, colored by phase."}),
    ("How has it changed over time?", {"drug_name": "Dupixent"},
     {"intent": "trend", "series": [{"label": "Dupixent", "filters": {"drug": "Dupixent"}}],
      "title": "Dupixent trials started per year",
      "interpretation": "Count Dupixent trials by start year; the drug comes from the structured field."}),
    ("What is the capital of France?", {},
     {"intent": None, "reason": "Not a question about clinical trials."}),
]


def build_system_prompt() -> str:
    dims = "\n".join(f"  - {d}: {DIMENSION_HELP.get(d, '')}" for d in DIMENSIONS)
    shots = "\n\n".join(
        f"Question: {q}\nStructured fields: {json.dumps(fields)}\nAnswer: {json.dumps(ans)}"
        for q, fields, ans in EXAMPLES)
    return f"""You turn a question about clinical trials into a plan for a data pipeline over
ClinicalTrials.gov. Return only one JSON object with these fields:

  intent: one of "distribution", "trend", "comparison", "network", "histogram", "scatter",
          or null (with a "reason") if the question cannot be answered from trial records
  series: 1 to 4 objects {{"label": str, "filters": {{...}}}}. One series describes one set of
          trials. Use several series only for comparisons or multi-line trends ("A vs B").
  filters: any of
      drug (str)         the drug or intervention, EXACTLY as the user wrote it
      condition (str)    the disease or condition, EXACTLY as the user wrote it
      sponsor (str)      an organization name, EXACTLY as written
      country (str)      a country name as ClinicalTrials.gov spells it, e.g. "United States"
      status (list)      from {list(STATUSES)}
      phases (list)      from {[p.value for p in Phase]}
      start_year, end_year (int)
  dimension: for "distribution" and "comparison", one of:
{dims}
  network: for "network", a pair of node types from ["drug", "sponsor", "condition"];
           ["drug", "drug"] means drugs given together in the same arm. Same-type pairs
           are only allowed for drugs.
  measure: for "histogram", one of {list(MEASURES)}
  x, y:    for "scatter", two different measures from the same list
  color:   for "scatter", optionally a dimension to color points by (usually "phase")
  top_n:   optional, how many largest groups or nodes per type to keep (1-100)
  title:   a short human-readable chart title
  interpretation: one sentence saying how you read the question

Rules:
- Copy entity names from the question or the structured fields verbatim. Do not expand,
  translate or correct them (write "Keytruda" if the user wrote Keytruda): a later step
  resolves names against the registry with evidence.
- Structured fields are authoritative. Use them; do not contradict them.
- "A vs B" or "compare A and B" means two series, one per entity, labeled by the entity.
- Questions "over time", "per year", "since 2015", "trend" are intent "trend", never a
  distribution by start_year.
- "which drugs are combined with X" is a ["drug", "drug"] network with drug X.
- "recruiting trials" is a status filter, not a dimension.
- Never invent fields or values outside the lists above. Today is {date.today().isoformat()}.

{shots}"""


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _ground(plan: Plan, request: QueryRequest) -> str | None:
    """None if every named entity appears in the input, else what was invented."""
    haystack = _norm(request.query + " " + json.dumps(request.structured_summary()))
    for s in plan.series:
        for field in ("drug", "condition", "sponsor"):
            value = getattr(s.filters, field)
            if value and _norm(value) not in haystack:
                return f"{field} {value!r} is not in the question or structured fields"
        if s.filters.country:
            value = s.filters.country
            aliases = [a for a, c in COUNTRIES.items() if c == value] + [value]
            if not any(_norm(a) in haystack for a in aliases):
                return f"country {value!r} is not in the question or structured fields"
    return None


def _normalize_raw(raw: dict) -> dict:
    """Lenient cleanup before strict validation: phase and status spellings, nulls, tuples."""
    out = dict(raw)
    if isinstance(out.get("network"), list):
        out["network"] = tuple(out["network"])
    for s in out.get("series") or []:
        f = s.get("filters") or {}
        if f.get("phases"):
            f["phases"] = [parse_phase(p).value for p in f["phases"]]
        if f.get("status"):
            f["status"] = [re.sub(r"[\s\-]+", "_", str(c).strip().upper()) for c in f["status"]]
            unknown = [c for c in f["status"] if c not in STATUSES]
            if unknown:
                raise ValueError(f"unknown status codes {unknown}")
        for key in ("drug", "condition", "sponsor", "country"):
            if isinstance(f.get(key), str):
                f[key] = " ".join(f[key].split()) or None
        s["filters"] = {k: v for k, v in f.items() if v not in (None, [], "")}
    return {k: v for k, v in out.items() if v is not None}


@dataclass
class LLMPlanResult:
    plan: Plan | None = None
    error: str | None = None       # why there is no plan (call failed, invalid, ungrounded)
    off_topic: bool = False        # the model judged the question not answerable from trials


def llm_plan(request: QueryRequest) -> LLMPlanResult:
    """A validated, grounded plan, or the reason there is none, so callers can fall back."""
    user = (f"Question: {request.query}\n"
            f"Structured fields: {json.dumps(request.structured_summary())}\nAnswer:")
    try:
        raw = llm.complete_json(build_system_prompt(), user, max_tokens=900)
    except llm.LLMUnavailable as e:
        return LLMPlanResult(error=f"model call failed: {e}")

    if not isinstance(raw, dict):
        return LLMPlanResult(error="model returned no JSON object")
    if raw.get("intent") is None:
        reason = str(raw.get("reason") or "no reason given")[:200]
        return LLMPlanResult(error=f"not a question about clinical trials: {reason}", off_topic=True)
    try:
        plan = Plan.model_validate(_normalize_raw(raw))
    except (ValidationError, ValueError, TypeError) as e:
        logger.warning("LLM plan rejected: %s", str(e)[:300])
        return LLMPlanResult(error=f"model plan failed validation: {str(e).splitlines()[0][:200]}")

    if problem := _ground(plan, request):
        logger.warning("LLM plan rejected as ungrounded: %s", problem)
        return LLMPlanResult(error=f"model plan not grounded in the input: {problem}")
    return LLMPlanResult(plan=plan)
