"""Network graph tests, offline. The arm-level case uses a real record from the fixture."""
import json
from pathlib import Path

import pytest

from app.aggregate.network import focus_id, network
from app.builders.network import build_network, render_hints
from app.drug_names import interchangeable
from app.normalize import normalize_studies, normalize_study

FIXTURE = Path(__file__).parent / "fixtures" / "pembrolizumab_sample.json"


def _trial(n, arms, sponsor="Sponsor A"):
    """A trial whose arms give the named drugs; every drug is a DRUG intervention."""
    names = sorted({d for drugs in arms.values() for d in drugs})
    return normalize_study({"protocolSection": {
        "identificationModule": {"nctId": f"NCT{n:08d}", "briefTitle": f"Trial {n}"},
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": sponsor, "class": "INDUSTRY"}},
        "armsInterventionsModule": {
            "interventions": [{"type": "DRUG", "name": d,
                               "armGroupLabels": [a for a, ds in arms.items() if d in ds]}
                              for d in names],
            "armGroups": [{"label": a, "type": "EXPERIMENTAL",
                           "interventionNames": [f"Drug: {d}" for d in ds]}
                          for a, ds in arms.items()]}}})


def _pairs(graph):
    return {frozenset((e.source, e.target)) for e in graph.edges}


def test_real_trial_links_drugs_within_an_arm_not_across_arms():
    # NCT05064059: favezelimab/pembrolizumab versus regorafenib or TAS-102.
    records, _ = normalize_studies(json.loads(FIXTURE.read_text())["studies"])
    g = network(records, ("drug", "drug"), top_n=50, min_edge_weight=1)
    assert frozenset({"drug:favezelimab", "drug:pembrolizumab"}) in _pairs(g)
    for comparator in ("drug:regorafenib", "drug:trifluridine/tipiracil"):
        assert frozenset({"drug:pembrolizumab", comparator}) not in _pairs(g)


def test_weight_counts_trials_not_arms():
    t = _trial(1, {"A": ["Pembrolizumab", "Carboplatin"], "B": ["Pembrolizumab", "Carboplatin"]})
    single = _trial(2, {"A": ["Pembrolizumab"]})
    g = network([t, single], ("drug", "drug"), min_edge_weight=1)
    assert [e.weight for e in g.edges] == [1]
    assert g.missing_count == 1                     # the single-drug trial links nothing


def test_placebo_is_never_a_drug():
    t = _trial(1, {"A": ["Pembrolizumab", "Placebo", "Lenvatinib"]})
    assert t.drugs == ["lenvatinib", "pembrolizumab"]
    assert t.arms[0].intervention_names == ["Drug: Pembrolizumab", "Drug: Placebo", "Drug: Lenvatinib"]
    g = network([t], ("drug", "drug"), min_edge_weight=1)
    assert "drug:placebo" not in {n.id for n in g.nodes}
    assert any("Placebos are not counted" in note for note in g.notes)


def test_interchangeable_drugs_listed_in_one_arm_are_not_linked():
    t = _trial(1, {"A": ["Pembrolizumab", "Carboplatin", "Cisplatin", "Pemetrexed"]})
    g = network([t], ("drug", "drug"), min_edge_weight=1)
    assert frozenset({"drug:carboplatin", "drug:cisplatin"}) not in _pairs(g)
    assert frozenset({"drug:carboplatin", "drug:pemetrexed"}) in _pairs(g)
    assert any("platinum agents (1 trial)" in note for note in g.notes)


def test_interchangeable_classes():
    assert interchangeable("carboplatin", "cisplatin") == "platinum agents"
    assert interchangeable("pembrolizumab", "nivolumab") == "PD-1/PD-L1 inhibitors"
    assert interchangeable("nivolumab", "ipilimumab") is None       # a real combination
    assert interchangeable("carboplatin", "paclitaxel") is None


def test_focus_drug_uses_only_arms_that_give_it():
    t = _trial(1, {"Pembro + Lenvatinib": ["Pembrolizumab", "Lenvatinib"],
                   "Control": ["Carboplatin", "Paclitaxel"]})
    focused = network([t], ("drug", "drug"), focus="drug:pembrolizumab", min_edge_weight=1)
    assert {(e.source, e.target) for e in focused.edges} == {("drug:lenvatinib", "drug:pembrolizumab")}
    assert [n.id for n in focused.nodes if n.focus] == ["drug:pembrolizumab"]
    unfocused = network([t], ("drug", "drug"), min_edge_weight=1)
    assert frozenset({"drug:carboplatin", "drug:paclitaxel"}) in _pairs(unfocused)


def test_focus_id_resolves_brand_names():
    assert focus_id("drug", "Keytruda") == "drug:pembrolizumab"
    assert focus_id("sponsor", "Merck Sharp & Dohme LLC") == "sponsor:merck sharp & dohme"


def test_sponsor_drug_edges_cite_both_ends_and_spellings_merge():
    trials = [_trial(1, {"A": ["Pembrolizumab"]}, sponsor="Merck Sharp & Dohme LLC"),
              _trial(2, {"A": ["Pembrolizumab"]}, sponsor="Merck Sharp & Dohme LLC"),
              _trial(3, {"A": ["Pembrolizumab", "Lenvatinib"]}, sponsor="Merck Sharp & Dohme Corp.")]
    g = network(trials, ("sponsor", "drug"), min_edge_weight=1)
    sponsor = next(n for n in g.nodes if n.type == "sponsor")
    assert sponsor.id == "sponsor:merck sharp & dohme" and sponsor.weight == 3
    assert sponsor.label == "Merck Sharp & Dohme LLC"           # most common exact spelling
    edge = next(e for e in g.edges if e.target == "drug:pembrolizumab")
    assert edge.source == sponsor.id and edge.weight == 3
    assert len(edge.citations) == 4                              # two trials, both ends each
    assert edge.citations[0].field.endswith("leadSponsor.name")
    assert edge.citations[1].field.endswith("interventions[0].name")


def test_pruning_hides_weak_edges_and_removes_orphans():
    strong = [_trial(i, {"A": ["Pembrolizumab", "Carboplatin"]}) for i in range(1, 4)]
    weak = _trial(9, {"A": ["Aspirin", "Metformin"]})
    g = network(strong + [weak], ("drug", "drug"), min_edge_weight=2)
    assert {n.id for n in g.nodes} == {"drug:carboplatin", "drug:pembrolizumab"}
    assert g.edges[0].weight == 3 and all(n.degree == 1 for n in g.nodes)
    assert any("at least 2 trials" in note for note in g.notes)
    assert any("2 of the top nodes had no edge" in note for note in g.notes)


def test_top_n_applies_per_node_type():
    trials = [_trial(i, {"A": ["Pembrolizumab"]}, sponsor=f"Sponsor {s}")
              for i, s in enumerate("AABBBC", 1)]
    g = network(trials, ("sponsor", "drug"), top_n=2, min_edge_weight=1)
    assert sorted(n.label for n in g.nodes if n.type == "sponsor") == ["Sponsor A", "Sponsor B"]
    assert [n.label for n in g.nodes if n.type == "drug"] == ["Pembrolizumab"]


def test_edge_cites_the_most_specific_arm():
    menu = _trial(1, {"Menu": ["Pembrolizumab", "Carboplatin", "Paclitaxel", "Bevacizumab"]})
    doublet = _trial(2, {"Doublet": ["Carboplatin", "Paclitaxel"]})
    g = network([menu, doublet], ("drug", "drug"), min_edge_weight=1)
    edge = next(e for e in g.edges if (e.source, e.target) == ("drug:carboplatin", "drug:paclitaxel"))
    assert edge.weight == 2
    assert edge.citations[0].nct_id == "NCT00000002"
    assert edge.citations[0].field.endswith("armGroups[0].interventionNames")
    assert edge.citations[0].value == "Drug: Carboplatin, Drug: Paclitaxel"


def test_unsupported_pairs_raise():
    with pytest.raises(ValueError):
        network([], ("sponsor", "sponsor"))
    with pytest.raises(ValueError):
        network([], ("drug", "investigator"))


def test_spec_and_render_hints():
    g = network([_trial(1, {"A": ["Pembrolizumab"]}, sponsor="Merck Sharp & Dohme LLC")],
                ("sponsor", "drug"), min_edge_weight=1)
    spec = build_network(g, "T")
    assert spec.type == "network_graph"
    assert spec.encoding["node_color"].field == "type_label"
    assert spec.encoding["node_color"].sort == ["Lead sponsor", "Drug"]
    assert {n["type_label"] for n in spec.data["nodes"]} == {"Lead sponsor", "Drug"}
    assert spec.data["edges"][0]["source"] == "sponsor:merck sharp & dohme"
    assert render_hints(g)["layout"] == "bipartite"

    drugs = network([_trial(2, {"A": ["Pembrolizumab", "Lenvatinib"]})], ("drug", "drug"),
                    focus="drug:pembrolizumab", min_edge_weight=1)
    assert render_hints(drugs) == {"layout": "force", "focus_node": "drug:pembrolizumab",
                                   "node_types": ["drug"]}


def test_lowercase_spelling_gets_a_capital_label():
    # Older NCI melanoma records write "aldesleukin"; the label should read "Aldesleukin".
    trials = [_trial(i, {"A": ["aldesleukin", "Cyclophosphamide"]}) for i in (1, 2)]
    g = network(trials, ("drug", "drug"), min_edge_weight=1)
    assert {n.label for n in g.nodes} == {"Aldesleukin", "Cyclophosphamide"}


def test_sponsor_network_says_why_trials_add_no_edge():
    no_drug = normalize_study({"protocolSection": {
        "identificationModule": {"nctId": "NCT00000009", "briefTitle": "Surgery only"},
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Sponsor A", "class": "OTHER"}}}})
    g = network([_trial(1, {"A": ["Pembrolizumab"]}), no_drug], ("sponsor", "drug"),
                min_edge_weight=1)
    assert any("1 of 2 trials add no edge: 1 list no drug." in note for note in g.notes)


def test_interferon_spellings_are_one_node():
    # Melanoma trials spell interferon alfa-2b dozens of ways; pegylated stays a separate drug.
    spellings = ["Recombinant Interferon Alfa-2b", "IFNα2b", "Intron A", "Interferon Alpha-2b"]
    trials = [_trial(i, {"A": [s]}) for i, s in enumerate(spellings)] + [_trial(9, {"A": ["PEG-Intron"]})]
    g = network(trials, ("sponsor", "drug"), min_edge_weight=1)
    assert {n.id: n.weight for n in g.nodes if n.type == "drug"} == {
        "drug:interferon alfa-2b": 4, "drug:peginterferon alfa-2b": 1}


def test_bracketed_abbreviation_draws_no_combination_edge():
    # NCT07183319 "Pembrolizumab & Enfortumab Vedotin (PEV)": one combination, not three.
    t = _trial(1, {"A": ["Pembrolizumab & Enfortumab Vedotin (PEV)"]})
    g = network([t], ("drug", "drug"), min_edge_weight=1)
    assert _pairs(g) == {frozenset({"drug:enfortumab vedotin", "drug:pembrolizumab"})}
