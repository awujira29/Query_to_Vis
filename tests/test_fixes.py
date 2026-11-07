"""Tests for display labels, the strict condition filter, and exact source counts."""
import pytest

from app.aggregate.compare import compare
from app.aggregate.source_counts import add_source_counts
from app.builders.grouped_bar import build_grouped_bar
from app.filters import has_condition
from app.labels import display
from app.normalize import normalize_study


def _rec(n, conditions=(), meshes=(), sponsor_class=None):
    study = {"protocolSection": {
        "identificationModule": {"nctId": f"NCT{n:08d}", "briefTitle": "t"},
        "conditionsModule": {"conditions": list(conditions)}},
        "derivedSection": {"conditionBrowseModule": {"meshes": [{"term": m} for m in meshes]}}}
    if sponsor_class:
        study["protocolSection"]["sponsorCollaboratorsModule"] = {
            "leadSponsor": {"name": "S", "class": sponsor_class}}
    return normalize_study(study)


def test_display_labels():
    assert display("sponsor_class", "OTHER") == "Academic / Other"
    assert display("sponsor_class", "SOMETHING_NEW") == "SOMETHING_NEW"
    assert display("phase", "Phase 2") == "Phase 2"


@pytest.mark.parametrize("conditions, meshes, expected", [
    (["Non-Small Cell Lung Cancer"], [], True),
    ([], ["Carcinoma, Non-Small-Cell Lung"], True),
    ([], ["Lung Neoplasms"], True),
    (["Small Cell Lung Carcinoma"], [], True),
    (["Asthma"], [], False),                                   # NCT00282516
    (["COVID-19", "Hematologic Diseases"], [], False),         # NCT04422470
    (["Lung Transplant Rejection"], [], False),                # lung, but not cancer
    (["Breast Cancer"], [], False),
])
def test_has_condition_lung_cancer(conditions, meshes, expected):
    assert has_condition(_rec(1, conditions, meshes), "lung cancer") is expected


def test_has_condition_non_cancer_term():
    assert has_condition(_rec(1, ["Cutaneous Melanoma"]), "melanoma")
    assert not has_condition(_rec(1, ["Basal Cell Carcinoma"]), "melanoma")


def test_source_counts_and_labels_in_spec():
    t = compare({"A": [_rec(1, sponsor_class="OTHER"), _rec(2, sponsor_class="INDUSTRY")]},
                "sponsor_class", include_missing=False)
    fake = {"OTHER": 700, "INDUSTRY": 300}
    def counter(params):
        return fake[params["filter.advanced"].split("]")[-1]]
    t = add_source_counts(t, "sponsor_class", {"A": {"query.cond": "x"}}, {"A": 1000}, counter)
    by_key = {r["sponsor_class"]: r for r in t.rows}
    assert by_key["OTHER"]["source_count"] == 700 and by_key["OTHER"]["source_percent"] == 70.0

    spec = build_grouped_bar(t, "sponsor_class", "T", measure="source_percent")
    assert spec.encoding["x"].field == "label"
    assert "Academic / Other" in spec.encoding["x"].sort
    assert spec.encoding["y"].field == "source_percent"


def test_existing_filter_is_combined():
    from app.aggregate.source_counts import _with_area
    p = _with_area({"filter.advanced": "AREA[StartDate]RANGE[2015-01-01,MAX]"}, "LeadSponsorClass", "NIH")
    assert p["filter.advanced"] == "(AREA[StartDate]RANGE[2015-01-01,MAX]) AND AREA[LeadSponsorClass]NIH"
