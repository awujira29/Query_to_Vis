from app.aggregate.compare import compare
from app.normalize import normalize_study


def _rec(n, phases, sponsor_class=None):
    study = {"protocolSection": {
        "identificationModule": {"nctId": f"NCT{n:08d}", "briefTitle": "t"},
        "designModule": {"phases": phases}}}
    if sponsor_class:
        study["protocolSection"]["sponsorCollaboratorsModule"] = {
            "leadSponsor": {"name": "S", "class": sponsor_class}}
    return normalize_study(study)


def test_cells_are_zero_filled_and_aligned():
    t = compare({"A": [_rec(1, ["PHASE2"]), _rec(2, ["PHASE3"])],
                 "B": [_rec(3, ["PHASE2"])]}, "phase")
    cells = {(r["series"], r["phase"]): r["trial_count"] for r in t.rows}
    assert cells == {("A", "Phase 2"): 1, ("A", "Phase 3"): 1,
                     ("B", "Phase 2"): 1, ("B", "Phase 3"): 0}


def test_percent_is_share_of_series():
    t = compare({"A": [_rec(1, ["PHASE2"]), _rec(2, ["PHASE3"])],
                 "B": [_rec(3, ["PHASE2"])]}, "phase")
    pct = {(r["series"], r["phase"]): r["percent"] for r in t.rows}
    assert pct[("A", "Phase 2")] == 50.0 and pct[("B", "Phase 2")] == 100.0


def test_overlap_is_reported():
    shared = _rec(1, ["PHASE2"])
    t = compare({"A": [shared], "B": [shared]}, "phase")
    assert any("appear in both" in n for n in t.notes)


def test_missing_rows_for_every_series():
    t = compare({"A": [_rec(1, [], "INDUSTRY")], "B": [_rec(2, [])]}, "sponsor_class")
    missing = {r["series"]: r["trial_count"] for r in t.rows if r["sponsor_class"] == "Not reported"}
    assert missing == {"A": 0, "B": 1}
