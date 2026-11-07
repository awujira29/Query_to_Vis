"""Probe data-driven MeSH resolution: broad search -> dominant MeSH terms.

Run from the project root:  python -m scripts.probe_resolve
"""
import time
from collections import Counter

import httpx

CT = "https://clinicaltrials.gov/api/v2/studies"
SAMPLE = 500
QUERIES = ["lung cancer", "nsclc", "heart attack", "breast cancer", "diabetes", "melanoma"]


def sample_meshes(words):
    params = {"query.cond": words, "pageSize": SAMPLE, "countTotal": "true",
              "fields": "NCTId,ConditionMeshTerm,ConditionAncestorTerm"}
    data = httpx.get(CT, params=params, timeout=60).json()
    counts = Counter()
    for s in data.get("studies", []):
        browse = s.get("derivedSection", {}).get("conditionBrowseModule", {})
        terms = {m["term"] for m in browse.get("meshes", [])}
        terms |= {a["term"] for a in browse.get("ancestors", [])}
        counts.update(terms)              # each term counted once per trial
    return counts, len(data.get("studies", [])), data.get("totalCount")


def registry_count(term):
    adv = f'AREA[ConditionMeshTerm]"{term}" OR AREA[ConditionAncestorTerm]"{term}"'
    r = httpx.get(CT, params={"filter.advanced": adv, "countTotal": "true",
                              "pageSize": 1, "fields": "NCTId"}, timeout=60)
    time.sleep(1.5)
    return r.json().get("totalCount") if r.status_code == 200 else None


for words in QUERIES:
    counts, n, total = sample_meshes(words)
    print(f"\n=== '{words}': broad search {total} trials, sampled {n} ===")
    print(f"  {'coverage':>8}  {'registry':>8}  MeSH term")
    for term, c in counts.most_common(8):
        print(f"  {100 * c / n:7.0f}%  {registry_count(term) or '?':>8}  {term}")
    time.sleep(1.5)
