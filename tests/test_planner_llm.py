"""LLM planner with a scripted model: what it accepts, what it rejects, and the fallback.

The model is faked at app.llm.complete_json, so these tests check the contract around
the model (validation, grounding, overrides, fallback), not the model itself.
"""
import pytest

from app import llm
from app.planner import apply_structured_fields, make_plan
from app.planner.llm_planner import build_system_prompt, llm_plan
from app.schemas.records import Phase
from app.schemas.request import QueryRequest


@pytest.fixture
def model(monkeypatch):
    """Configure a fake provider and let a test choose the model's answer."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    answers = {}

    def fake_complete_json(system, user, max_tokens=600):
        if "fail" in answers:
            raise llm.LLMUnavailable("simulated outage")
        return answers["raw"]

    monkeypatch.setattr(llm, "complete_json", fake_complete_json)
    return answers


def good_plan():
    return {"intent": "comparison", "dimension": "phase",
            "series": [{"label": "pembrolizumab", "filters": {"drug": "pembrolizumab"}},
                       {"label": "nivolumab", "filters": {"drug": "nivolumab", "phases": ["Phase 3"],
                                                          "status": ["recruiting"]}}],
            "title": "Phase: pembrolizumab vs nivolumab", "interpretation": "two series"}


def test_valid_plan_is_accepted_and_normalized(model):
    model["raw"] = good_plan()
    result = llm_plan(QueryRequest(query="Compare phases for pembrolizumab vs nivolumab trials"))
    assert result.plan is not None and result.error is None
    assert result.plan.series[1].filters.phases == [Phase.PHASE3]
    assert result.plan.series[1].filters.status == ["RECRUITING"]


def test_unknown_dimension_is_rejected_not_coerced(model):
    model["raw"] = {**good_plan(), "dimension": "colour"}
    result = llm_plan(QueryRequest(query="Compare phases for pembrolizumab vs nivolumab trials"))
    assert result.plan is None and "validation" in result.error


def test_invented_entity_is_rejected(model):
    raw = good_plan()
    raw["series"][0]["filters"]["drug"] = "atezolizumab"       # not in the question
    model["raw"] = raw
    result = llm_plan(QueryRequest(query="Compare phases for pembrolizumab vs nivolumab trials"))
    assert result.plan is None and "not grounded" in result.error and "atezolizumab" in result.error


def test_entity_from_structured_field_counts_as_grounded(model):
    model["raw"] = {"intent": "trend", "title": "t",
                    "series": [{"label": "Keytruda", "filters": {"drug": "Keytruda"}}]}
    result = llm_plan(QueryRequest(query="How has it changed over time?", drug_name="Keytruda"))
    assert result.plan is not None


def test_country_alias_is_grounded(model):
    model["raw"] = {"intent": "distribution", "dimension": "phase", "title": "t",
                    "series": [{"label": "US melanoma", "filters": {"condition": "melanoma",
                                                                    "country": "United States"}}]}
    result = llm_plan(QueryRequest(query="melanoma trials in the US by phase"))
    assert result.plan is not None


def test_off_topic_judgment_is_respected(model):
    model["raw"] = {"intent": None, "reason": "Not about clinical trials."}
    planning = make_plan(QueryRequest(query="weather in melanoma park today"))
    assert planning.plan is None and planning.planner == "llm"
    assert "not a question about clinical trials" in planning.notes[0]


def test_model_outage_falls_back_to_rules(model):
    model["fail"] = True
    planning = make_plan(QueryRequest(query="How are melanoma trials distributed across phases?"))
    assert planning.planner == "rules" and planning.plan.intent == "distribution"
    assert any("rule-based planner" in n for n in planning.notes)


def test_invalid_model_plan_falls_back_to_rules(model):
    model["raw"] = {"intent": "pie", "series": [], "title": "x"}
    planning = make_plan(QueryRequest(query="melanoma trials by phase"))
    assert planning.planner == "rules" and planning.plan.dimension == "phase"


def test_structured_fields_fill_override_or_stay_out_of_the_comparison(model):
    model["raw"] = good_plan()
    req = QueryRequest(query="Compare phases for pembrolizumab vs nivolumab trials",
                       condition="melanoma", drug_name="Keytruda", start_year=2015)
    plan, notes = apply_structured_fields(llm_plan(req).plan, req)
    # condition and start_year were absent: filled into every series
    assert all(s.filters.condition == "melanoma" and s.filters.start_year == 2015 for s in plan.series)
    # drug differs across series (the comparison axis): left alone, with a note
    assert [s.filters.drug for s in plan.series] == ["pembrolizumab", "nivolumab"]
    assert any("not applied" in n and "Keytruda" in n for n in notes)


def test_structured_field_overrides_a_single_shared_value(model):
    model["raw"] = {"intent": "trend", "title": "t",
                    "series": [{"label": "pembrolizumab", "filters": {"drug": "pembrolizumab"}}]}
    req = QueryRequest(query="pembrolizumab trials per year", drug_name="Keytruda")
    planning = make_plan(req)
    assert planning.plan.series[0].filters.drug == "Keytruda"
    assert any("overrides" in n for n in planning.notes)


def test_visualization_type_override(model):
    model["raw"] = {"intent": "distribution", "dimension": "phase", "title": "t",
                    "series": [{"label": "melanoma", "filters": {"condition": "melanoma"}}]}
    planning = make_plan(QueryRequest(query="melanoma trials by phase", visualization_type="histogram"))
    assert planning.plan.intent == "histogram" and planning.plan.measure == "enrollment_count"
    assert any("histogram" in n for n in planning.notes)


def test_system_prompt_lists_the_live_vocabulary():
    prompt = build_system_prompt()
    for word in ("sponsor_class", "intervention_type", "EARLY_PHASE1", "NOT_YET_RECRUITING",
                 "duration_months", '"drug", "sponsor", "condition"'):
        assert word in prompt
