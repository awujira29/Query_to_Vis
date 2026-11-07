"""Broad test of MeSH resolution checks across many conditions and drugs.

For each query:
  - "proposed" is the MeSH term a good AI should suggest (written by hand here,
    as a stand-in for the model) -> tests whether correct proposals pass.
  - the top terms found in the data -> tests how often wrong terms also pass.

Run from the project root:  python -m scripts.probe_broad
Takes ~10-15 minutes (throttled). Results also saved to scripts/probe_broad.json.
"""
import json
import time
from collections import Counter

import httpx

CT = "https://clinicaltrials.gov/api/v2/studies"
SAMPLE = 300
TOP = 4
MIN_COVERAGE, MIN_RATIO, MAX_RATIO = 60, 0.5, 1.2

CONDITIONS = {
    # lay terms
    "heart attack": "Myocardial Infarction",
    "high blood pressure": "Hypertension",
    "kidney failure": "Renal Insufficiency",
    "flu": "Influenza, Human",
    "preterm birth": "Premature Birth",
    "insomnia": "Sleep Initiation and Maintenance Disorders",
    "chronic pain": "Chronic Pain",
    # abbreviations
    "COPD": "Pulmonary Disease, Chronic Obstructive",
    "ADHD": "Attention Deficit Disorder with Hyperactivity",
    "HIV": "HIV Infections",
    "AML": "Leukemia, Myeloid, Acute",
    "CKD": "Renal Insufficiency, Chronic",
    "TB": "Tuberculosis",
    # neurology / psychiatry
    "alzheimer's": "Alzheimer Disease",
    "parkinson's": "Parkinson Disease",
    "multiple sclerosis": "Multiple Sclerosis",
    "epilepsy": "Epilepsy",
    "migraine": "Migraine Disorders",
    "depression": "Depressive Disorder",
    "schizophrenia": "Schizophrenia",
    "autism": "Autism Spectrum Disorder",
    "stroke": "Stroke",
    # immunology / infection / metabolic
    "rheumatoid arthritis": "Arthritis, Rheumatoid",
    "lupus": "Lupus Erythematosus, Systemic",
    "crohn's disease": "Crohn Disease",
    "psoriasis": "Psoriasis",
    "hepatitis C": "Hepatitis C",
    "covid": "COVID-19",
    "sepsis": "Sepsis",
    "obesity": "Obesity",
    "type 1 diabetes": "Diabetes Mellitus, Type 1",
    "sickle cell": "Anemia, Sickle Cell",
    "cystic fibrosis": "Cystic Fibrosis",
    "atrial fibrillation": "Atrial Fibrillation",
    # cancers
    "prostate cancer": "Prostatic Neoplasms",
    "colon cancer": "Colonic Neoplasms",
    "pancreatic cancer": "Pancreatic Neoplasms",
    "glioblastoma": "Glioblastoma",
}

DRUGS = {
    # generics
    "metformin": "Metformin",
    "atorvastatin": "Atorvastatin",
    "aspirin": "Aspirin",
    "warfarin": "Warfarin",
    "tamoxifen": "Tamoxifen",
    "sertraline": "Sertraline",
    "apixaban": "apixaban",
    "semaglutide": "semaglutide",
    "osimertinib": "osimertinib",
    "remdesivir": "remdesivir",
    # biologics
    "adalimumab": "Adalimumab",
    "trastuzumab": "Trastuzumab",
    "rituximab": "Rituximab",
    "imatinib": "Imatinib Mesylate",
    # brand names and codes (the hard cases)
    "Humira": "Adalimumab",
    "Herceptin": "Trastuzumab",
    "Gleevec": "Imatinib Mesylate",
    "Ozempic": "semaglutide",
    "MK-3475": "pembrolizumab",
    "lithium": "Lithium Carbonate",
}

KINDS = {
    "condition": {"query": "query.cond", "mesh": "ConditionMeshTerm",
                  "ancestor": "ConditionAncestorTerm", "browse": "conditionBrowseModule"},
    "drug": {"query": "query.intr", "mesh": "InterventionMeshTerm",
             "ancestor": "InterventionAncestorTerm", "browse": "interventionBrowseModule"},
}


def get(params):
    for attempt in range(4):
        r = httpx.get(CT, params=params, timeout=60)
        time.sleep(1.5)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            time.sleep(2 ** attempt * 5)
            continue
        return {"error": f"{r.status_code}: {r.text[:120]}"}
    return {"error": "rate limited"}


def sample(kind, words):
    k = KINDS[kind]
    data = get({k["query"]: words, "pageSize": SAMPLE, "countTotal": "true",
                "fields": f"NCTId,{k['mesh']},{k['ancestor']}"})
    trials = []
    for s in data.get("studies", []):
        browse = s.get("derivedSection", {}).get(k["browse"], {})
        trials.append({m["term"] for m in browse.get("meshes", [])}
                      | {a["term"] for a in browse.get("ancestors", [])})
    return trials, data.get("totalCount") or 0


def registry(kind, term):
    k = KINDS[kind]
    adv = f'AREA[{k["mesh"]}]"{term}" OR AREA[{k["ancestor"]}]"{term}"'
    data = get({"filter.advanced": adv, "countTotal": "true", "pageSize": 1, "fields": "NCTId"})
    return data.get("totalCount") or 0


def score(kind, term, trials, broad):
    coverage = 100 * sum(term in t for t in trials) / max(len(trials), 1)
    reg = registry(kind, term)
    ratio = reg / broad if broad else 0
    passed = reg > 0 and coverage >= MIN_COVERAGE and MIN_RATIO <= ratio <= MAX_RATIO
    return {"term": term, "coverage": round(coverage), "registry": reg,
            "ratio": round(ratio, 2), "passed": passed}


def run(kind, cases):
    results = []
    for words, proposed in cases.items():
        trials, broad = sample(kind, words)
        prop = score(kind, proposed, trials, broad)
        counts = Counter(t for tr in trials for t in tr)
        others = [score(kind, t, trials, broad)
                  for t, _ in counts.most_common(TOP + 1) if t != proposed][:TOP]
        wrong_passing = [o["term"] for o in others if o["passed"]]
        results.append({"kind": kind, "query": words, "broad": broad,
                        "proposed": prop, "others": others, "others_passing": wrong_passing})
        mark = "PASS" if prop["passed"] else "FAIL"
        print(f"[{mark}] {kind:9} {words:22} -> {proposed:40} "
              f"cov {prop['coverage']:3}%  ratio {prop['ratio']:5}  "
              f"| other terms passing: {wrong_passing or '-'}")
    return results


if __name__ == "__main__":
    results = run("condition", CONDITIONS) + run("drug", DRUGS)
    json.dump(results, open("scripts/probe_broad.json", "w"), indent=2)

    for kind in KINDS:
        rs = [r for r in results if r["kind"] == kind]
        ok = sum(r["proposed"]["passed"] for r in rs)
        ambiguous = sum(bool(r["others_passing"]) for r in rs)
        print(f"\n{kind}s: proposed term passed {ok}/{len(rs)}; "
              f"another term also passed in {ambiguous}/{len(rs)}")
    print("\nFailures (proposed term rejected):")
    for r in results:
        if not r["proposed"]["passed"]:
            p = r["proposed"]
            print(f"  {r['kind']:9} {r['query']:22} {p['term']:40} "
                  f"registry {p['registry']:6}  cov {p['coverage']:3}%  ratio {p['ratio']}")
