"""Rule-based planner on the assignment's example query types and common variants.

Offline: entity typing by registry is stubbed so unknown words count as conditions.
"""
import pytest

from app.planner import rules
from app.planner.rules import rules_plan
from app.schemas.records import Phase
from app.schemas.request import QueryRequest


@pytest.fixture(autouse=True)
def no_registry(monkeypatch):
    def unavailable(params):
        raise rules.ApiError("offline in tests")
    monkeypatch.setattr(rules, "count_studies", unavailable)


def plan(query, **extra):
    return rules_plan(QueryRequest(query=query, **extra))


def filters(p, i=0):
    return p.series[i].filters.model_dump(exclude_defaults=True)


# --- appendix: time trends ---------------------------------------------------

def test_trend_with_drug_and_since_year():
    p = plan("How has the number of trials for pembrolizumab changed per year since 2015?")
    assert p.intent == "trend" and filters(p) == {"drug": "pembrolizumab", "start_year": 2015}


def test_trend_with_condition():
    p = plan("How many trials started each year for breast cancer?")
    assert p.intent == "trend" and filters(p) == {"condition": "breast cancer"}


def test_trend_from_structured_drug_only():
    p = plan("How has the number of trials for this drug changed over time?", drug_name="Keytruda")
    assert p.intent == "trend" and filters(p) == {"drug": "Keytruda"}


# --- appendix: distributions -------------------------------------------------

def test_phase_distribution_with_two_entities():
    p = plan("How are melanoma trials testing nivolumab distributed across phases?")
    assert p.intent == "distribution" and p.dimension == "phase"
    assert filters(p) == {"drug": "nivolumab", "condition": "melanoma"}


def test_intervention_types_dimension_does_not_leak_into_entity():
    p = plan("What are the most common intervention types for melanoma trials?")
    assert p.intent == "distribution" and p.dimension == "intervention_type"
    assert filters(p) == {"condition": "melanoma"}


@pytest.mark.parametrize("query, dimension", [
    ("Breakdown of asthma trials by sponsor class", "sponsor_class"),
    ("Who funds hypertension trials?", "sponsor_class"),
    ("Which sponsors run the most diabetes trials?", "lead_sponsor"),
    ("Status of COVID-19 vaccine trials", "overall_status"),
    ("Heart disease trials by study type", "study_type"),
    ("Where are melanoma trials located?", "country"),
    ("Show me which conditions are studied with semaglutide", "condition"),
])
def test_dimension_detection(query, dimension):
    p = plan(query)
    assert p.intent == "distribution" and p.dimension == dimension


def test_heart_disease_keeps_the_word_disease():
    assert filters(plan("Heart disease trials by study type")) == {"condition": "Heart disease"}


# --- appendix: comparisons ---------------------------------------------------

def test_compare_two_drugs_by_phase():
    p = plan("Compare phases for trials involving pembrolizumab vs nivolumab.")
    assert p.intent == "comparison" and p.dimension == "phase"
    assert [s.label for s in p.series] == ["pembrolizumab", "nivolumab"]
    assert filters(p, 0) == {"drug": "pembrolizumab"} and filters(p, 1) == {"drug": "nivolumab"}


def test_compare_two_conditions_by_sponsor_class():
    p = plan("Compare sponsor categories across breast cancer and lung cancer trials.")
    assert p.intent == "comparison" and p.dimension == "sponsor_class"
    assert [s.label for s in p.series] == ["breast cancer", "lung cancer"]


def test_comparison_shares_status_filter_and_dimension():
    p = plan("Compare recruiting trials for aspirin and warfarin by country")
    assert p.intent == "comparison" and p.dimension == "country"
    assert filters(p, 0) == {"drug": "aspirin", "status": ["RECRUITING"]}
    assert filters(p, 1) == {"drug": "warfarin", "status": ["RECRUITING"]}


def test_comparison_over_time_is_a_multi_series_trend():
    p = plan("Compare trials per year for pembrolizumab vs nivolumab since 2015")
    assert p.intent == "trend" and len(p.series) == 2
    assert all(s.filters.start_year == 2015 for s in p.series)


def test_comparison_with_one_entity_degrades_to_distribution():
    p = plan("Compare phases of melanoma trials")
    assert p.intent == "distribution" and p.dimension == "phase"


# --- appendix: geography -----------------------------------------------------

def test_countries_with_recruiting_filter():
    p = plan("Which countries have the most recruiting trials for lung cancer?")
    assert p.intent == "distribution" and p.dimension == "country"
    assert filters(p) == {"condition": "lung cancer", "status": ["RECRUITING"]}


def test_country_in_text_becomes_a_filter_with_api_spelling():
    p = plan("How many lung cancer trials are recruiting in Germany?")
    assert filters(p)["country"] == "Germany" and filters(p)["status"] == ["RECRUITING"]
    p = plan("Recruiting melanoma trials in the US by phase")
    assert filters(p)["country"] == "United States"


# --- appendix: networks ------------------------------------------------------

def test_sponsor_drug_network():
    p = plan("Show a network of sponsors and drugs for melanoma trials.")
    assert p.intent == "network" and p.network == ("sponsor", "drug")
    assert filters(p) == {"condition": "melanoma"}


@pytest.mark.parametrize("query", [
    "Which drugs frequently co-occur in combination studies with pembrolizumab?",
    "Which drugs are most often combined with pembrolizumab?",
])
def test_drug_drug_network(query):
    p = plan(query)
    assert p.intent == "network" and p.network == ("drug", "drug")
    assert filters(p) == {"drug": "pembrolizumab"}


def test_network_with_sponsor_filter():
    p = plan("network of conditions and drugs for trials run by Merck")
    assert p.network == ("condition", "drug") and filters(p) == {"sponsor": "Merck"}


# --- histogram and scatter ---------------------------------------------------

def test_histogram_with_phase_filter():
    p = plan("What is the typical enrollment of phase 3 Alzheimer's disease trials?")
    assert p.intent == "histogram" and p.measure == "enrollment_count"
    assert filters(p) == {"condition": "Alzheimer's disease", "phases": [Phase.PHASE3]}


def test_duration_histogram():
    p = plan("How long do phase 3 breast cancer trials take?")
    assert p.intent == "histogram" and p.measure == "duration_months"


def test_scatter_from_two_measures():
    p = plan("Is there a relationship between enrollment and duration for rheumatoid arthritis trials?")
    assert p.intent == "scatter" and {p.x, p.y} == {"enrollment_count", "duration_months"}
    assert p.color == "phase"


# --- edge cases --------------------------------------------------------------

def test_no_entity_means_no_plan():
    assert plan("what's the weather today") is None


def test_structured_fields_win_over_text():
    p = plan("melanoma trials by phase", condition="lung cancer")
    assert filters(p) == {"condition": "lung cancer"}


def test_request_dimension_and_top_n_are_used():
    p = plan("melanoma trials", dimension="country", top_n=5)
    assert p.dimension == "country" and p.top_n == 5


def test_year_phrases():
    assert rules.detect_years("trials between 2010 and 2020") == (2010, 2020)
    assert rules.detect_years("trials from 2018") == (2018, None)
    assert rules.detect_years("trials until 2019") == (None, 2019)
    assert rules.detect_years("trials in 2021") == (2021, 2021)
