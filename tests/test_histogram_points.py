"""Histogram and scatter aggregators, with citations back to the fields they bin or plot."""
from app.aggregate.histogram import histogram
from app.aggregate.points import points
from app.builders.histogram import build_histogram
from app.builders.scatter import build_scatter
from app.normalize import normalize_studies
from tests.studies import study


def records(*studies):
    recs, failures = normalize_studies(list(studies))
    assert not failures
    return recs


def test_histogram_bins_are_fixed_edges_with_open_end_and_citations():
    recs = records(study(1, enrollment=5), study(2, enrollment=15), study(3, enrollment=15),
                   study(4, enrollment=70000), study(5, enrollment=None))
    table, stats = histogram(recs, "enrollment_count")
    bins = {row["bin"]: row["trial_count"] for row in table.rows}
    assert bins["0-9"] == 1 and bins["10-19"] == 2 and bins["50,000+"] == 1
    assert table.rows[-1]["bin_end"] is None                       # open-ended
    assert sum(bins.values()) == 4 and table.missing_count == 1
    assert stats["n"] == 4 and stats["median"] == 15.0 and stats["max"] == 70000
    first = table.rows[0]["citations"][0]
    assert first["nct_id"] == "NCT00000001" and first["value"] == "5"
    assert first["field"].endswith("enrollmentInfo.count")
    assert any("1 trials report no enrollment" in n for n in table.notes)


def test_histogram_duration_cites_both_dates():
    recs = records(study(1, start="2020-01", completion="2021-01"))
    table, _ = histogram(recs, "duration_months")
    row = next(r for r in table.rows if r["trial_count"])
    assert row["bin"] == "12-23"
    fields = [c["field"] for c in row["citations"]]
    assert any("startDateStruct" in f for f in fields) and any("completionDateStruct" in f for f in fields)


def test_histogram_spec_orders_bins():
    recs = records(study(1, enrollment=5), study(2, enrollment=300))
    table, _ = histogram(recs, "enrollment_count")
    spec = build_histogram(table, "enrollment_count", "T")
    assert spec.type == "histogram" and spec.encoding["x"].sort == [r["bin"] for r in table.rows]
    assert spec.encoding["x"].unit == "participants"


def test_points_one_per_trial_with_color_and_two_citations():
    recs = records(study(1, start="2018", enrollment=50, phases=["PHASE1"]),
                   study(2, start="2020", enrollment=500, phases=["PHASE3"]),
                   study(3, start=None, enrollment=10))
    ps, stats = points(recs, "start_year", "enrollment_count", "phase")
    assert [p.nct_id for p in ps.points] == ["NCT00000001", "NCT00000002"]
    assert ps.points[0].color == "Phase 1" and ps.missing_count == 1
    assert len(ps.points[0].citations) == 2
    assert stats["x"]["n"] == 2 and stats["y"]["max"] == 500


def test_points_are_thinned_deterministically_and_stats_use_all():
    recs = records(*[study(i, enrollment=i) for i in range(1, 11)])
    ps, stats = points(recs, "start_year", "enrollment_count", None, max_points=5)
    assert len(ps.points) == 5 and stats["y"]["n"] == 10
    assert any("every 2nd" in n for n in ps.notes)


def test_scatter_scale_is_symlog_when_zero_present():
    recs = records(study(1, enrollment=0), study(2, enrollment=100))
    ps, _ = points(recs, "start_year", "enrollment_count", None)
    spec = build_scatter(ps, "start_year", "enrollment_count", None, "T")
    assert spec.encoding["y"].scale == "symlog" and spec.encoding["x"].scale == "linear"
    recs = records(study(1, enrollment=10), study(2, enrollment=100))
    ps, _ = points(recs, "start_year", "enrollment_count", None)
    assert build_scatter(ps, "start_year", "enrollment_count", None, "T").encoding["y"].scale == "log"
