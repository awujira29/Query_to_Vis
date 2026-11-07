"""The pipeline end to end, offline: planner -> fake fetch -> aggregate -> build -> meta.

The resolver and the API are replaced: build_query returns a plain field search and the
fetcher returns the controlled studies below. Everything downstream is the real code.
"""
import pytest

from app import pipeline
from app.client import UpstreamError
from app.planner import rules
from app.query import Query
from app.resolve import Resolution
from app.schemas.request import QueryRequest
from app.schemas.response import QueryResponse
from tests.studies import fetched, study

STUDIES = [
    study(1, phases=["PHASE1"], start="2018-01", enrollment=20, sponsor="Merck", sponsor_class="INDUSTRY",
          arms={"A": ["Pembrolizumab", "Lenvatinib"]}, countries=["United States", "Japan"]),
    study(2, phases=["PHASE2"], start="2019-05", enrollment=80, sponsor="Merck", sponsor_class="INDUSTRY",
          arms={"A": ["Pembrolizumab", "Lenvatinib"], "B": ["Docetaxel"]}, countries=["United States"]),
    study(3, phases=["PHASE2"], start="2019-09", enrollment=120, sponsor="NCI", sponsor_class="NIH",
          arms={"A": ["Pembrolizumab", "Carboplatin", "Pemetrexed"]}, countries=["Germany"]),
    study(4, phases=["PHASE3"], start="2021-02", enrollment=600, sponsor="Merck", sponsor_class="INDUSTRY",
          arms={"A": ["Pembrolizumab", "Lenvatinib"], "B": ["Placebo"]}, countries=["France", "Germany"]),
    study(5, phases=[], start=None, enrollment=None, study_type="OBSERVATIONAL", status="RECRUITING",
          arms={"A": ["Pembrolizumab"]}, countries=[]),
]


@pytest.fixture(autouse=True)
def offline_resolver(monkeypatch):
    def fake_build_query(filters):
        res = [Resolution(kind=k, query=w, status="resolved", terms=[w.title()], source="exact",
                          api_params={"q": w}, note=f"{w} resolved")
               for k, w in (("condition", filters.condition), ("drug", filters.drug)) if w]
        return Query(params={"q": " ".join(r.query for r in res)}, resolutions=res,
                     notes=[r.note for r in res])
    monkeypatch.setattr(pipeline, "build_query", fake_build_query)
    def unavailable(params):
        raise UpstreamError("offline in tests")
    monkeypatch.setattr(rules, "count_studies", unavailable)


def fetch_all(params, max_studies):
    return fetched(STUDIES)


def run(query, fetcher=fetch_all, **extra):
    response = pipeline.answer(QueryRequest(query=query, **extra), fetcher=fetcher)
    QueryResponse.model_validate(response.model_dump())      # the contract round-trips
    return response


def test_distribution_counts_sum_to_analyzed_and_cite_fields():
    r = run("How are pembrolizumab trials distributed across phases?")
    assert r.status == "ok" and r.visualization.type == "bar_chart"
    rows = r.visualization.data
    assert sum(row["trial_count"] for row in rows) == r.meta.analyzed == 5
    by_label = {row["label"]: row for row in rows}
    assert by_label["Phase 2"]["trial_count"] == 2
    assert by_label["Phase 2"]["citations"][0]["field"].endswith("designModule.phases")
    assert by_label["Phase 2"]["citations"][0]["value"] == "PHASE2"
    # an observational study has no phase: its citation points at the study type, never invented
    assert by_label["No Phase Listed"]["citations"][0]["value"] == "OBSERVATIONAL"
    assert r.meta.planner == "rules" and r.meta.plan["intent"] == "distribution"
    assert r.meta.filters == {"drug": "pembrolizumab"} and r.meta.resolutions[0]["terms"] == ["Pembrolizumab"]
    assert r.meta.series[0].analyzed == 5 and not r.meta.truncated


def test_multi_valued_dimension_is_flagged():
    r = run("Which countries have the most pembrolizumab trials?")
    assert r.visualization.encoding["x"].title == "Country"
    counts = {row["label"]: row["trial_count"] for row in r.visualization.data}
    assert counts["United States"] == 2 and counts["Germany"] == 2
    assert any("count in several" in n for n in r.meta.notes)
    assert r.meta.missing_count == 1                      # the study with no sites


def test_trend_zero_fills_and_reports_undated():
    r = run("How has the number of pembrolizumab trials changed per year?")
    assert r.visualization.type == "time_series" and r.meta.time_granularity == "year"
    years = [row["start_year"] for row in r.visualization.data]
    assert years == [2018, 2019, 2020, 2021]
    assert [row["trial_count"] for row in r.visualization.data] == [1, 2, 0, 1]
    assert any("no start date" in n for n in r.meta.notes)


def test_comparison_is_zero_filled_and_percent_based():
    def fetch_by_series(params, max_studies):
        if "lenvatinib" in params["q"]:
            return fetched([STUDIES[0], STUDIES[1], STUDIES[3]])
        return fetched([STUDIES[2]])
    r = run("Compare phases for trials involving lenvatinib vs carboplatin.", fetcher=fetch_by_series)
    assert r.visualization.type == "grouped_bar_chart"
    assert r.visualization.encoding["y"].field == "percent"
    cells = {(row["series"], row["label"]): row for row in r.visualization.data}
    assert cells[("lenvatinib", "Phase 3")]["trial_count"] == 1
    assert cells[("carboplatin", "Phase 3")]["trial_count"] == 0       # zero-filled
    assert cells[("carboplatin", "Phase 2")]["percent"] == 100.0
    assert len(r.meta.series) == 2 and r.meta.analyzed == 4


def test_truncated_categorical_distribution_switches_to_exact_source_counts(monkeypatch):
    exact = {"INDUSTRY": 300, "NIH": 100}
    monkeypatch.setattr(pipeline, "add_source_counts",
                        lambda table, dim, params, totals: pipeline_add(table, dim, exact))

    def pipeline_add(table, dim, counts):
        for row in table.rows:
            row["source_count"] = counts.get(row[dim])
        table.notes.append("exact counts used")
        return table

    r = run("pembrolizumab trials by sponsor class",
            fetcher=lambda p, max_studies: fetched(STUDIES, total=400, truncated=True))
    assert r.meta.truncated and r.visualization.encoding["y"].field == "source_count"
    assert r.meta.render_hints == {"y_measure": "source_count"}
    assert any("only the first 5 of 400" in n for n in r.meta.notes)


def test_truncated_non_categorical_distribution_warns():
    r = run("pembrolizumab trials by country",
            fetcher=lambda p, max_studies: fetched(STUDIES, total=9000, truncated=True))
    assert r.visualization.encoding["y"].field == "trial_count"
    assert any("describe a sample" in n for n in r.meta.notes)


def test_drug_network_uses_same_arm_only_and_focus():
    r = run("Which drugs are most often combined with pembrolizumab?", top_n=10)
    assert r.visualization.type == "network_graph"
    data = r.visualization.data
    ids = {n["id"] for n in data["nodes"]}
    assert "drug:pembrolizumab" in ids and "drug:lenvatinib" in ids
    assert "drug:docetaxel" not in ids                    # a comparator arm, never combined
    assert "drug:placebo" not in ids
    edge = next(e for e in data["edges"] if {e["source"], e["target"]} == {"drug:pembrolizumab", "drug:lenvatinib"})
    assert edge["weight"] == 3 and edge["citations"][0]["field"].endswith("interventionNames")
    assert r.meta.render_hints["focus_node"] == "drug:pembrolizumab"
    assert r.meta.render_hints["layout"] == "force"


def test_sponsor_drug_network_is_bipartite():
    r = run("Show a network of sponsors and drugs for pembrolizumab trials.", top_n=10)
    data = r.visualization.data
    types = {n["type"] for n in data["nodes"]}
    assert types == {"sponsor", "drug"} and r.meta.render_hints["layout"] == "bipartite"
    merck = next(n for n in data["nodes"] if n["id"] == "sponsor:merck")
    assert merck["weight"] == 3 and merck["label"] == "Merck"


def test_histogram_and_statistics():
    r = run("What is the typical enrollment of pembrolizumab trials?")
    assert r.visualization.type == "histogram"
    assert sum(row["trial_count"] for row in r.visualization.data) == 4
    assert r.meta.statistics["enrollment_count"]["median"] == 100.0
    assert r.meta.missing_count == 1


def test_scatter_points_and_correlation():
    r = run("Is there a relationship between enrollment and duration for pembrolizumab trials?")
    assert r.visualization.type == "scatter_plot"
    assert len(r.visualization.data) == 4 and "pearson_r" in r.meta.statistics
    assert r.visualization.encoding["color"].field == "color"


def test_no_results_and_unsupported_and_error():
    r = run("melanoma trials by phase", fetcher=lambda p, max_studies: fetched([], total=0))
    assert r.status == "no_results" and r.visualization is None and r.meta.analyzed == 0

    r = run("tell me a joke")
    assert r.status == "unsupported" and r.meta.planner == "none" and "Name a drug" in r.message

    def broken(params, max_studies):
        raise UpstreamError("HTTP 503")
    r = run("melanoma trials by phase", fetcher=broken)
    assert r.status == "error" and "503" in r.message and r.meta.plan["intent"] == "distribution"


def test_found_trials_but_nothing_to_chart():
    undated = [study(1, start=None, completion=None)]
    r = run("How has the number of pembrolizumab trials changed per year?",
            fetcher=lambda p, max_studies: fetched(undated))
    assert r.status == "no_results" and "none has the information" in r.message


def test_multi_series_trend_shares_the_year_axis():
    def fetch_by_series(params, max_studies):
        return fetched([STUDIES[0]] if "lenvatinib" in params["q"] else [STUDIES[3]])
    r = run("Compare trials per year for lenvatinib vs pembrolizumab", fetcher=fetch_by_series)
    assert r.visualization.type == "time_series" and "color" in r.visualization.encoding
    rows = r.visualization.data
    assert {row["series"] for row in rows} == {"lenvatinib", "pembrolizumab"}
    assert [row["start_year"] for row in rows if row["series"] == "lenvatinib"] == [2018, 2019, 2020, 2021]
    assert sum(row["trial_count"] for row in rows) == 2
