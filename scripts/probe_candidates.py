"""Check how coverage and size ratio score good vs bad MeSH candidates.

Run from the project root:  python -m scripts.probe_candidates
"""
import time

import httpx

CT = "https://clinicaltrials.gov/api/v2/studies"
SAMPLE = 500

# query -> candidates: the first is what a good AI should propose; the rest are
# deliberately bad (too broad, too narrow, wrong meaning, or invented).
CASES = {
    "lung cancer": ["Lung Neoplasms", "Lung Diseases", "Small Cell Lung Carcinoma", "Pulmonary Carcinoma Syndrome"],
    "nsclc": ["Carcinoma, Non-Small-Cell Lung", "Lung Neoplasms", "Carcinoma, Bronchogenic"],
    "heart attack": ["Myocardial Infarction", "Necrosis", "Myocardial Ischemia", "Heart Diseases"],
    "breast cancer": ["Breast Neoplasms", "Breast Diseases", "Skin Diseases"],
    "diabetes": ["Diabetes Mellitus", "Diabetes Mellitus, Type 2", "Metabolic Diseases"],
    "melanoma": ["Melanoma", "Skin Neoplasms", "Neoplasms"],
}


def get(params):
    r = httpx.get(CT, params=params, timeout=60)
    time.sleep(1.5)
    r.raise_for_status()
    return r.json()


def sample_terms(words):
    data = get({"query.cond": words, "pageSize": SAMPLE, "countTotal": "true",
                "fields": "NCTId,ConditionMeshTerm,ConditionAncestorTerm"})
    trials = []
    for s in data.get("studies", []):
        browse = s.get("derivedSection", {}).get("conditionBrowseModule", {})
        trials.append({m["term"] for m in browse.get("meshes", [])}
                      | {a["term"] for a in browse.get("ancestors", [])})
    return trials, data.get("totalCount") or 0


def registry(term):
    adv = f'AREA[ConditionMeshTerm]"{term}" OR AREA[ConditionAncestorTerm]"{term}"'
    return get({"filter.advanced": adv, "countTotal": "true", "pageSize": 1,
                "fields": "NCTId"}).get("totalCount") or 0


for words, candidates in CASES.items():
    trials, broad = sample_terms(words)
    print(f"\n=== '{words}': broad search {broad} ===")
    print(f"  {'coverage':>8} {'registry':>9} {'ratio':>6}  candidate")
    for term in candidates:
        coverage = 100 * sum(term in t for t in trials) / max(len(trials), 1)
        reg = registry(term)
        ratio = reg / broad if broad else 0
        print(f"  {coverage:7.0f}% {reg:>9} {ratio:6.2f}  {term}")
