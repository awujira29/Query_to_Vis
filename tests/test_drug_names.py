"""Regression tests for drug-name normalization and the strict drug filter.

Every case here came from a real ClinicalTrials.gov record that once failed.
Run from the project root:  pytest -q
"""
import pytest

from app.drug_names import normalize_drug_names
from app.filters import has_drug
from app.normalize import normalize_study


@pytest.mark.parametrize("raw, expected", [
    ("BMS-936558 (Nivolumab)", ["nivolumab"]),                    # NCT01721772
    ("Placebo matching BMS-936558 (Nivolumab)", ["placebo"]),
    ("Nivolumab IV", ["nivolumab"]),
    ("intrathecal nivolumab and intrathecal ipilimumab", ["nivolumab", "ipilimumab"]),
    ("Pembrolizumab or Nivolumab", ["pembrolizumab", "nivolumab"]),
    ("Relatlimab and nivolumab fixed dose combination (FDC)", ["relatlimab", "nivolumab"]),
    ("Nivolumab-Relatlimab", ["nivolumab", "relatlimab"]),
    ("OPDIVO®", ["nivolumab"]),
    ("Nivolumab: Cohort 1 (Cutaneous)", ["nivolumab"]),          # NCT03850691
    ("Nivolumab & Ipilimumab", ["nivolumab", "ipilimumab"]),     # NCT07501117
    ("Nivolumab Injection [Opdivo]", ["nivolumab"]),             # NCT03563729
    ("EU-Opdivo®", ["nivolumab"]),                               # NCT07221734
    ("Opdivo-EU (Induction)", ["nivolumab"]),                    # NCT06587451
    ("favezelimab/pembrolizumab", ["favezelimab", "pembrolizumab"]),
    ("Pembrolizumab 200 mg", ["pembrolizumab"]),
    ("Cisplatin + Pemetrexed", ["cisplatin", "pemetrexed"]),
    ("Nab-paclitaxel", ["nab-paclitaxel"]),                      # hyphen kept
    ("FMT + ICI", ["fmt"]),                                      # generic term dropped
])
def test_normalize_drug_names(raw, expected):
    assert normalize_drug_names(raw) == expected


def _record(*interventions):
    return normalize_study({"protocolSection": {
        "identificationModule": {"nctId": "NCT00000001", "briefTitle": "test"},
        "armsInterventionsModule": {"interventions": [
            {"type": t, "name": n, "otherNames": o} for t, n, o in interventions]},
    }})


@pytest.mark.parametrize("interventions, expected", [
    ([("BIOLOGICAL", "BMS-936558 (Nivolumab)", [])], True),
    ([("BIOLOGICAL", "Anti-PD1", ["nivolumab (OPDIVO)"])], True),
    ([("DRUG", "Combination of TIL Transfer", ["anti-PD-1 Therapy Nivolumab"])], True),
    ([("DRUG", "Placebo matching BMS-936558 (Nivolumab)", [])], False),
    ([("DRUG", "PD-1 inhibitors", [])], False),
    ([("DRUG", "Immune Checkpoint Inhibitors", [])], False),
    ([("BIOLOGICAL", "Pembrolizumab", ["Keytruda"])], False),
    ([("PROCEDURE", "Nivolumab infusion timing", [])], False),   # not drug-like
])
def test_has_drug_nivolumab(interventions, expected):
    assert has_drug(_record(*interventions), "nivolumab") is expected