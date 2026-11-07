from app.query import Filters, build_query
from app.resolve import Resolution


def fake_resolver(kind, words):
    return Resolution(kind=kind, query=words, status="resolved", terms=[words.title()],
                      api_params={"filter.advanced": f"(MESH[{words}])"}, note=f"{words} ok")


def test_names_are_resolved_and_combined_with_structured_filters():
    q = build_query(Filters(drug="nivolumab", condition="melanoma", start_year=2015,
                            phases=["PHASE2", "PHASE3"], status=["RECRUITING"]),
                    resolver=fake_resolver)
    assert q.params["filter.advanced"] == (
        "(MESH[melanoma]) AND (MESH[nivolumab]) AND (AREA[StartDate]RANGE[2015-01-01,MAX])"
        " AND (AREA[Phase](PHASE2 OR PHASE3))")
    assert q.params["filter.overallStatus"] == "RECRUITING"
    assert [r.kind for r in q.resolutions] == ["condition", "drug"]
    assert q.notes == ["melanoma ok", "nivolumab ok"]


def test_condition_fallback_param_survives_merge():
    def fallback(kind, words):
        return Resolution(kind=kind, query=words, status="fallback",
                          api_params={"query.cond": f'EXPANSION[Concept] "{words}"'}, note="fb")
    q = build_query(Filters(condition="chronic pain", end_year=2020), resolver=fallback)
    assert q.params == {"query.cond": 'EXPANSION[Concept] "chronic pain"',
                        "filter.advanced": "(AREA[StartDate]RANGE[MIN,2020-12-31])"}


def test_no_filters_means_no_params():
    assert build_query(Filters(), resolver=fake_resolver).params == {}
