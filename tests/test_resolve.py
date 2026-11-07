"""Resolver tests, offline. Each synthetic case mirrors a real case from the probes."""
from collections import Counter

import pytest

from app import llm
from app.resolve import (Candidate, LLMProposer, Sample, merge_params, resolve, verify)


class FakeRegistry:
    """Sample + registry counts defined by hand. Counts are keyed by lowercase term sets."""

    def __init__(self, trials, direct_terms, total, counts):
        self._sample = Sample([{t.lower() for t in tr} for tr in trials],
                              Counter(t.lower() for tr in trials for t in tr
                                      if t in direct_terms), total)
        self._counts = {frozenset(k.lower() for k in key): v for key, v in counts.items()}

    def sample(self, kind, words):
        return self._sample

    def count(self, kind, terms):
        return self._counts.get(frozenset(t.lower() for t in terms), 0)


class FixedProposer:
    def __init__(self, *candidates):
        self.candidates = list(candidates)

    def propose(self, kind, words, sample):
        return self.candidates


EMPTY = Sample([], Counter(), 0)


def lung_registry():
    trials = ([{"Lung Neoplasms", "Lung Diseases", "Neoplasms"}] * 80
              + [{"Lung Diseases"}] * 4 + [set()] * 16)
    return FakeRegistry(trials, {"Lung Neoplasms"}, 14711, {
        ("Lung Neoplasms",): 11790, ("Lung Diseases",): 40075, ("Neoplasms",): 107882})


def test_correct_proposal_is_verified_and_builds_mesh_query():
    res = resolve("condition", "lung cancer",
                  [FixedProposer(Candidate(["Lung Neoplasms"], "llm"))], lung_registry())
    assert res.status == "resolved" and res.terms == ["Lung Neoplasms"]
    expr = res.api_params["filter.advanced"]
    assert 'AREA[ConditionAncestorTerm]"Lung Neoplasms"' in expr
    # records without MeSH terms are still found through their conditions list
    assert 'AREA[Condition]EXPANSION[Concept]"lung cancer"' in expr


def test_invented_term_rejected_then_next_candidate_used():
    res = resolve("condition", "lung cancer",
                  [FixedProposer(Candidate(["Pulmonary Carcinoma Syndrome"], "llm"),
                                 Candidate(["Lung Neoplasms"], "llm"))], lung_registry())
    assert res.candidates[0].rejected_because == ["no trials in the registry",
                                                  "covers only 0% of trials found by the words",
                                                  "too narrow (registry size 0.00x the text search)"]
    assert res.terms == ["Lung Neoplasms"]


def test_too_broad_parent_rejected_and_falls_back():
    res = resolve("condition", "lung cancer",
                  [FixedProposer(Candidate(["Lung Diseases"], "llm"))], lung_registry())
    assert res.status == "fallback"
    assert "too broad" in res.candidates[0].rejected_because[0]
    assert res.api_params == {"query.cond": 'EXPANSION[Concept] "lung cancer"'}


def test_poly_hierarchy_tie_prefers_directly_assigned_term():
    # Myocardial Infarction and its ancestor Necrosis cover identical trials.
    trials = [{"Myocardial Infarction", "Necrosis", "Heart Diseases"}] * 78 + [set()] * 22
    reg = FakeRegistry(trials, {"Myocardial Infarction"}, 3971, {
        ("Myocardial Infarction",): 3002, ("Necrosis",): 3988, ("Heart Diseases",): 28214})
    res = resolve("condition", "heart attack",
                  [FixedProposer(Candidate(["Necrosis"], "llm"),
                                 Candidate(["Myocardial Infarction"], "llm"))], reg)
    assert not res.candidates[0].passed and res.terms == ["Myocardial Infarction"]


def test_near_equal_global_sizes_break_tie_by_direct_assignment():
    # Breast Neoplasms (14,571) vs Breast Diseases (14,557): within the 2% tie band.
    trials = [{"Breast Neoplasms", "Breast Diseases", "Neoplasms"}] * 83 + [set()] * 17
    reg = FakeRegistry(trials, {"Breast Neoplasms"}, 16998, {
        ("Breast Neoplasms",): 14571, ("Breast Diseases",): 14557, ("Neoplasms",): 107882})
    sample = reg.sample("condition", "breast cancer")
    assert not verify(Candidate(["Breast Diseases"], "llm"), "condition", sample, reg).passed
    assert verify(Candidate(["Breast Neoplasms"], "llm"), "condition", sample, reg).passed


def test_parent_that_only_looks_nested_in_the_sample_is_not_preferred():
    # In a melanoma sample, nearly every "Skin Diseases" trial is a melanoma trial,
    # but Skin Diseases is far larger globally, so Melanoma is not too broad.
    trials = [{"Melanoma", "Skin Diseases", "Neoplasms"}] * 86 + [{"Melanoma"}] * 3 + [set()] * 11
    reg = FakeRegistry(trials, {"Melanoma"}, 3782, {
        ("Melanoma",): 3424, ("Skin Diseases",): 31969, ("Neoplasms",): 107882})
    assert verify(Candidate(["Melanoma"], "llm"), "condition",
                  reg.sample("condition", "melanoma"), reg).passed


def test_brand_name_with_large_ratio_passes():
    # "Ozempic" text search undercounts; semaglutide is a leaf, so a big ratio is fine.
    trials = [{"semaglutide", "Incretins", "Glucagon-Like Peptides"}] * 85 + [set()] * 15
    reg = FakeRegistry(trials, {"semaglutide"}, 104, {
        ("semaglutide",): 596, ("Incretins",): 2500, ("Glucagon-Like Peptides",): 2400})
    res = resolve("drug", "Ozempic", [FixedProposer(Candidate(["semaglutide"], "rxnorm"))], reg)
    assert res.status == "resolved"
    expr = res.api_params["filter.advanced"]
    assert 'AREA[InterventionMeshTerm]"semaglutide"' in expr
    assert 'AREA[InterventionName]"Ozempic"' in expr          # name match kept for safety


def test_too_narrow_subtype_rejected():
    trials = [{"Lung Neoplasms", "Small Cell Lung Carcinoma"}] * 10 + [{"Lung Neoplasms"}] * 70 + [set()] * 20
    reg = FakeRegistry(trials, {"Small Cell Lung Carcinoma"}, 14711, {
        ("Small Cell Lung Carcinoma",): 1206, ("Lung Neoplasms",): 11790})
    check = verify(Candidate(["Small Cell Lung Carcinoma"], "llm"), "condition",
                   reg.sample("condition", "lung cancer"), reg)
    assert not check.passed and any("covers only 10%" in r for r in check.rejected_because)


def test_drug_fallback_uses_name_fields_and_duplicates_are_skipped():
    reg = FakeRegistry([set()] * 10, set(), 50, {})
    res = resolve("drug", "ISM5939", [FixedProposer(Candidate(["ISM5939"], "llm")),
                                      FixedProposer(Candidate(["ism5939"], "exact"))], reg)
    assert res.status == "fallback" and len(res.candidates) == 1
    assert res.api_params["filter.advanced"] == (
        '(AREA[InterventionName]"ISM5939" OR AREA[InterventionOtherName]"ISM5939")')


def test_merge_params_ands_advanced_filters():
    merged = merge_params({"filter.advanced": "(A)"},
                          {"filter.advanced": "AREA[StartDate]RANGE[2015-01-01,MAX]"},
                          {"filter.overallStatus": "RECRUITING"})
    assert merged == {"filter.advanced": "(A) AND (AREA[StartDate]RANGE[2015-01-01,MAX])",
                      "filter.overallStatus": "RECRUITING"}


@pytest.mark.parametrize("raw", [
    {"candidates": [{"terms": []}]},                        # empty term list: schema violation
    {"candidates": "Lung Neoplasms"},                        # wrong type
    {"something": "else"},                                   # no candidates at all
])
def test_llm_output_is_validated(monkeypatch, raw):
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: raw)
    assert LLMProposer().propose("condition", "lung cancer", EMPTY) == []


def test_llm_unavailable_means_no_candidates(monkeypatch):
    def fail(*a, **k):
        raise llm.LLMUnavailable("no key")
    monkeypatch.setattr(llm, "complete_json", fail)
    assert LLMProposer().propose("condition", "lung cancer", EMPTY) == []


def test_llm_candidates_parsed(monkeypatch):
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: {"candidates": [
        {"terms": ["Depression", "Depressive Disorder"], "reason": "symptom and disorder"}]})
    [c] = LLMProposer().propose("condition", "depression", EMPTY)
    assert c.terms == ["Depression", "Depressive Disorder"] and c.source == "llm"


def test_llm_prompt_is_grounded_in_observed_terms(monkeypatch):
    prompts = []
    monkeypatch.setattr(llm, "complete_json",
                        lambda system, user, **k: prompts.append(user) or {"candidates": []})
    sample = Sample([{"pulmonary disease, chronic obstructive", "lung diseases"}] * 9 + [set()],
                    Counter({"pulmonary disease, chronic obstructive": 9}), 5594,
                    {"pulmonary disease, chronic obstructive": "Pulmonary Disease, Chronic Obstructive",
                     "lung diseases": "Lung Diseases"})
    LLMProposer().propose("condition", "COPD", sample)
    assert "Pulmonary Disease, Chronic Obstructive (90% of trials, assigned directly)" in prompts[0]
    assert "Lung Diseases (90% of trials, broader category)" in prompts[0]


def test_feedback_round_recovers_from_a_synonym(monkeypatch):
    # First answer is a real synonym that ClinicalTrials.gov does not store; the
    # rejection reason goes back to the model, which then picks the stored heading.
    answers = iter([{"candidates": [{"terms": ["Chronic Obstructive Pulmonary Disease"]}]},
                    {"candidates": [{"terms": ["Pulmonary Disease, Chronic Obstructive"]}]}])
    prompts = []
    monkeypatch.setattr(llm, "complete_json",
                        lambda system, user, **k: prompts.append(user) or next(answers))
    trials = [{"Pulmonary Disease, Chronic Obstructive", "Lung Diseases"}] * 95 + [set()] * 5
    reg = FakeRegistry(trials, {"Pulmonary Disease, Chronic Obstructive"}, 5594, {
        ("Pulmonary Disease, Chronic Obstructive",): 5400, ("Lung Diseases",): 40075})
    res = resolve("condition", "COPD", [LLMProposer()], reg)
    assert res.status == "resolved" and res.terms == ["Pulmonary Disease, Chronic Obstructive"]
    assert "rejected" in prompts[1] and "Chronic Obstructive Pulmonary Disease" in prompts[1]


def test_prompt_contains_no_gold_set_answers():
    import json
    from app.resolve import LLM_SYSTEM
    gold = json.load(open("tests/fixtures/resolution_gold.json"))
    expected = {t for case in gold if case["expected"] != "fallback" for t in case["expected"]}
    leaked = [t for t in expected if f'"{t}"' in LLM_SYSTEM or f" {t} " in f" {LLM_SYSTEM} "]
    assert leaked == [], f"gold answers in the prompt: {leaked}"


def test_parent_riding_along_in_a_union_is_dropped():
    # ["Neoplasms", "Brain Neoplasms"] really means all neoplasms: drop the parent.
    trials = [{"Brain Neoplasms", "Neoplasms"}] * 85 + [{"Neoplasms"}] * 10 + [set()] * 5
    reg = FakeRegistry(trials, {"Brain Neoplasms"}, 5200, {
        ("Brain Neoplasms",): 4800, ("Neoplasms",): 107882,
        ("Brain Neoplasms", "Neoplasms"): 107882, ("Neoplasms", "Brain Neoplasms"): 107882})
    check = verify(Candidate(["Neoplasms", "Brain Neoplasms"], "llm"), "condition",
                   reg.sample("condition", "brain tumor"), reg)
    assert check.passed and check.terms == ["Brain Neoplasms"]
    assert check.dropped_terms == ["Neoplasms"]


def test_close_relatives_in_a_union_are_kept():
    # Parent and child of similar size: the parent is what the words mean, keep both.
    trials = [{"Sleep Apnea Syndromes", "Sleep Apnea, Obstructive"}] * 60 + \
             [{"Sleep Apnea Syndromes"}] * 25 + [set()] * 15
    reg = FakeRegistry(trials, {"Sleep Apnea, Obstructive", "Sleep Apnea Syndromes"}, 3000, {
        ("Sleep Apnea Syndromes",): 2600, ("Sleep Apnea, Obstructive",): 1900,
        ("Sleep Apnea Syndromes", "Sleep Apnea, Obstructive"): 2600})
    check = verify(Candidate(["Sleep Apnea Syndromes", "Sleep Apnea, Obstructive"], "llm"),
                   "condition", reg.sample("condition", "sleep apnea"), reg)
    assert check.passed and check.dropped_terms == []


def test_every_term_must_be_specific_not_just_one():
    # A broad term that is not a parent of the other term is still rejected on its own.
    trials = [{"Insulin Glargine", "Insulin", "Insulins"}] * 80 + [set()] * 20
    reg = FakeRegistry(trials, {"Insulin Glargine"}, 900, {
        ("Insulin Glargine",): 800, ("Insulin",): 2500,
        ("Insulin", "Insulin Glargine"): 2500, ("Insulin Glargine", "Insulin"): 2500})
    check = verify(Candidate(["Insulin", "Insulin Glargine"], "llm"), "drug",
                   reg.sample("drug", "insulin glargine"), reg)
    assert check.terms == ["Insulin Glargine"] and check.dropped_terms == ["Insulin"]


# --- sample-grounded proposer and registry respelling (added with the planner) ---------

from app.resolve import SampleProposer


def _named_sample(trials, direct_terms, total):
    names = {t.lower(): t for tr in trials for t in tr}
    return Sample([{t.lower() for t in tr} for tr in trials],
                  Counter(t.lower() for tr in trials for t in tr if t in direct_terms), total, names)


def test_sample_proposer_prefers_short_direct_headings_sharing_a_word():
    trials = ([{"Breast Neoplasms", "Neoplasms by Site", "Neoplasms", "Breast Diseases"}] * 80
              + [{"Triple Negative Breast Neoplasms", "Breast Neoplasms", "Neoplasms"}] * 10
              + [set()] * 10)
    sample = _named_sample(trials, {"Breast Neoplasms", "Triple Negative Breast Neoplasms"}, 1000)
    terms = [c.terms for c in SampleProposer().propose("condition", "breast cancer", sample)]
    # "Breast Diseases" is not proposed: the query's cancer word needs a cancer word in the heading
    assert terms == [["Breast Neoplasms"], ["Triple Negative Breast Neoplasms"]]


def test_sample_proposer_needs_a_shared_word():
    trials = [{"Myocardial Infarction", "Necrosis"}] * 50
    sample = _named_sample(trials, {"Myocardial Infarction"}, 100)
    assert SampleProposer().propose("condition", "heart attack", sample) == []


def test_candidates_are_respelled_as_the_registry_spells_them():
    reg = lung_registry()
    reg._sample.names = {"lung neoplasms": "Lung Neoplasms"}
    res = resolve("condition", "lung cancer",
                  [FixedProposer(Candidate(["LUNG NEOPLASMS"], "llm"))], reg)
    assert res.terms == ["Lung Neoplasms"]
    assert 'AREA[ConditionMeshTerm]"Lung Neoplasms"' in res.api_params["filter.advanced"]


def test_sample_proposer_resolves_when_exact_words_fail():
    trials = ([{"Lung Neoplasms", "Lung Diseases", "Neoplasms"}] * 80
              + [{"Lung Diseases"}] * 4 + [set()] * 16)
    reg = FakeRegistry(trials, {"Lung Neoplasms"}, 14711, {
        ("Lung Neoplasms",): 11790, ("Lung Diseases",): 40075, ("Neoplasms",): 107882})
    reg._sample.names = {"lung neoplasms": "Lung Neoplasms", "lung diseases": "Lung Diseases",
                         "neoplasms": "Neoplasms"}
    from app.resolve import ExactProposer
    res = resolve("condition", "lung cancer", [ExactProposer(), SampleProposer()], reg)
    assert res.status == "resolved" and res.terms == ["Lung Neoplasms"] and res.source == "sample"
    assert res.candidates[0].source == "exact" and not res.candidates[0].passed
