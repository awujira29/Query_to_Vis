from datetime import date

from app.aggregate.timeseries import time_series
from app.normalize import normalize_study


def _rec(n, start, kind="ACTUAL"):
    status = {"startDateStruct": {"date": start, "type": kind}} if start else {}
    return normalize_study({"protocolSection": {
        "identificationModule": {"nctId": f"NCT{n:08d}", "briefTitle": "t"},
        "statusModule": status}})


TODAY = date(2026, 11, 7)


def test_gaps_are_zero_filled():
    t = time_series([_rec(1, "2015-03"), _rec(2, "2018-01-02")], today=TODAY)
    assert [r["start_year"] for r in t.rows] == [2015, 2016, 2017, 2018]
    assert [r["trial_count"] for r in t.rows] == [1, 0, 0, 1]


def test_future_and_undated_are_excluded_with_notes():
    t = time_series([_rec(1, "2020"), _rec(2, "2027-12-30", "ESTIMATED"), _rec(3, None)],
                    today=TODAY)
    assert [r["start_year"] for r in t.rows] == [2020]
    assert any("after 2026-11-07" in n for n in t.notes)
    assert t.missing_count == 1


def test_start_year_bounds_and_partial_year():
    t = time_series([_rec(1, "2010"), _rec(2, "2026-02", "ESTIMATED")],
                    start_year=2024, today=TODAY)
    assert [r["start_year"] for r in t.rows] == [2024, 2025, 2026]
    assert t.rows[-1]["partial_year"] and t.rows[-1]["estimated_count"] == 1


def test_later_this_year_counts_as_future():
    t = time_series([_rec(1, "2026-03-01"), _rec(2, "2026-12-30", "ESTIMATED"),
                     _rec(3, "2026-11")], today=TODAY)
    assert t.rows[-1]["start_year"] == 2026 and t.rows[-1]["trial_count"] == 2