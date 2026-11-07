"""Request schema: lenient input, strict output."""
import pytest
from pydantic import ValidationError

from app.schemas.records import Phase
from app.schemas.request import QueryRequest, parse_phase


@pytest.mark.parametrize("raw, expected", [
    ("Phase 2", Phase.PHASE2), ("phase2", Phase.PHASE2), ("PHASE2", Phase.PHASE2),
    ("2", Phase.PHASE2), ("III", Phase.PHASE3), ("Phase IV", Phase.PHASE4),
    ("early phase 1", Phase.EARLY_PHASE1), ("EARLY_PHASE1", Phase.EARLY_PHASE1),
    ("N/A", Phase.NA), ("NA", Phase.NA), (Phase.PHASE1, Phase.PHASE1),
])
def test_parse_phase(raw, expected):
    assert parse_phase(raw) == expected


def test_unknown_phase_is_rejected():
    with pytest.raises(ValidationError):
        QueryRequest(query="melanoma trials by phase", trial_phase=["Phase 9"])


def test_statuses_are_normalized_and_validated():
    r = QueryRequest(query="x trials", status=["recruiting", "Not yet recruiting", "RECRUITING"])
    assert r.status == ["RECRUITING", "NOT_YET_RECRUITING"]
    with pytest.raises(ValidationError):
        QueryRequest(query="x trials", status=["ongoing"])


def test_blank_strings_become_none_and_whitespace_collapses():
    r = QueryRequest(query="x trials", drug_name="  ", condition="  lung   cancer ")
    assert r.drug_name is None and r.condition == "lung cancer"


def test_year_order_and_bounds():
    with pytest.raises(ValidationError):
        QueryRequest(query="x trials", start_year=2020, end_year=2015)
    with pytest.raises(ValidationError):
        QueryRequest(query="x trials", start_year=1800)


def test_dimension_and_max_studies_validation():
    with pytest.raises(ValidationError):
        QueryRequest(query="x trials", dimension="colour")
    with pytest.raises(ValidationError):
        QueryRequest(query="x trials", max_studies=50)
    assert QueryRequest(query="x trials", dimension="country", max_studies=500).max_studies == 500


def test_to_filters_carries_structured_fields():
    r = QueryRequest(query="trials", drug_name="Keytruda", trial_phase=["3"], status=["recruiting"],
                     start_year=2015, country="Germany")
    f = r.to_filters()
    assert f.drug == "Keytruda" and f.phases == [Phase.PHASE3] and f.status == ["RECRUITING"]
    assert f.start_year == 2015 and f.country == "Germany" and f.condition is None
    assert r.structured_summary() == {"drug_name": "Keytruda", "trial_phase": [Phase.PHASE3],
                                      "status": ["RECRUITING"], "start_year": 2015,
                                      "country": "Germany"}
