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
    # Found while building the network graphs:
    ("Gemcitabine Hydrochloride", ["gemcitabine"]),              # NCT04267848, salt form
    ("Erlotinib Hydrochloride", ["erlotinib"]),                  # NCT03137771
    ("Pemetrexed Disodium for Injection", ["pemetrexed"]),       # NCT06956001
    ("Magnesium sulfate", ["magnesium sulfate"]),                # guard: the salt is the drug
    ("5-FU", ["fluorouracil"]),                                  # NCT07829783
    ("Abraxane", ["nab-paclitaxel"]),                            # NCT06745882
    ("nab paclitaxel", ["nab-paclitaxel"]),                      # NCT04153734
    ("Trifluridine/Tipiracil", ["trifluridine/tipiracil"]),      # NCT04149444, one product
    ("Interleukin-2", ["aldesleukin"]),                          # NCT06532812
    ("Datopotamab deruxtecan-dlnk (Dato-DXd)", ["datopotamab deruxtecan"]),  # NCT05117476
    ("Cisplatin 75 mg/m^2", ["cisplatin"]),                      # NCT03631784, no "m^2" drug
    ("Carboplatin AUC 5", ["carboplatin"]),                      # NCT07098988
    ("Pembrolizumab 200 mg Q3W or 400 mg Q6W", ["pembrolizumab"]),            # NCT05692999
    ("FOLFOX (Oxaliplatin/Leucovorin/5-Fluorouracil)",
     ["oxaliplatin", "leucovorin", "fluorouracil"]),             # NCT02009449
    ("mFOLFOX6", ["oxaliplatin", "leucovorin", "fluorouracil"]), # NCT06917079
    ("Olaparib matching placebo", ["placebo"]),                  # NCT04624204
    ("Saline placebo for pembrolizumab", ["placebo"]),           # NCT03875092
    ("Platinum-based chemotherapy", []),                         # NCT03322566, a class
    ("CDK4/6 Inhibitor", []),                                    # NCT06447623, a class
    ("BRAF inhibitor dabrafenib and MEK inhibitor trametinib",
     ["dabrafenib", "trametinib"]),                              # NCT07419932
    ("Anti-PD-L1 Monoclonal Antibody MPDL3280A", ["atezolizumab"]),           # NCT02400814
    ("Akt inhibitor MK2206", ["mk2206"]),                        # NCT01705340
    ("Combination Therapy: PY314 + Pembrolizumab", ["py314", "pembrolizumab"]),  # NCT04691375
    ("Pemetrexed+（carboplatin or Cisplatin）with or without Sintilimab",
     ["pemetrexed", "sintilimab", "carboplatin", "cisplatin"]),  # NCT06281964
    # Interferons: 52 names in the melanoma trials before these rules.
    ("Recombinant Interferon Alfa-2b", ["interferon alfa-2b"]),           # NCT01274338
    ("IFNα2b", ["interferon alfa-2b"]),                                   # NCT00871533
    ("High-Dose IFN-a2b", ["interferon alfa-2b"]),                        # NCT03435302
    ("Intron A, Interferon alpha -2b", ["interferon alfa-2b"]),           # NCT00613509
    ("Intron A (interferon alfa-2b; SCH 30500)", ["interferon alfa-2b"]), # NCT00723710
    ("PEG-Intron", ["peginterferon alfa-2b"]),                            # NCT00457418
    ("PegIFN-2b", ["peginterferon alfa-2b"]),                             # NCT02089685
    ("Sylatron", ["peginterferon alfa-2b"]),                              # NCT01496807
    ("Pegylated Interferon Alpha-2b (PGI)", ["peginterferon alfa-2b"]),   # NCT00525031
    ("pIFN (peginterferon alfa-2a)", ["peginterferon alfa-2a"]),          # NCT07288112
    ("Recombinant Interferon Alfa (IFN-A)", ["interferon alfa"]),         # NCT00002882, no subtype
    ("Pembrolizumab and high dose interferon alfa-2b (HDI)",
     ["pembrolizumab", "interferon alfa-2b"]),                            # NCT02339324
    ("P1101 (Ropeginterferon alfa-2b)", ["p1101", "ropeginterferon alfa-2b"]),  # NCT04233840
    ("Recombinant Interferon Alpha 2b-like Protein",
     ["recombinant interferon alpha 2b-like protein"]),                   # NCT04380545, new protein
    ("Recombinant Vesicular Stomatitis Virus-expressing Interferon-beta and Tyrosinase Related "
     "Protein 1", ["recombinant vesicular stomatitis virus-expressing interferon-beta",
                   "tyrosinase related protein 1"]),                      # NCT03865212, a virus
    # A bracketed abbreviation or note is not a second drug:
    ("Sacituzumab Govitecan (SG)", ["sacituzumab govitecan"]),            # NCT04724018
    ("Cisplatin (CDDP)", ["cisplatin"]),                                  # NCT00455572
    ("Keytruda®(US)", ["pembrolizumab"]),                                 # NCT06173011
    ("Pembrolizumab & Enfortumab Vedotin (PEV)",
     ["pembrolizumab", "enfortumab vedotin"]),                            # NCT07183319
    ("Pembrolizumab plus chemotherapy (FLOT)",
     ["pembrolizumab", "fluorouracil", "leucovorin", "oxaliplatin", "docetaxel"]),  # NCT06411171
    ("Neoadjuvant chemotherapy (taxol and carboplatin)",
     ["neoadjuvant", "paclitaxel", "carboplatin"]),                       # NCT00913705, brand kept
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