"""Probe MeSH-based matching before building on it.

Run from the project root:  python -m scripts.probe_mesh
"""
import json
import time

import httpx

CT = "https://clinicaltrials.gov/api/v2/studies"
MESH = "https://id.nlm.nih.gov/mesh/lookup"


def ct_count(label, advanced, extra=None):
    params = {"filter.advanced": advanced, "countTotal": "true", "pageSize": 1, "fields": "NCTId",
              **(extra or {})}
    r = httpx.get(CT, params=params, timeout=60)
    print(f"{label:55} status={r.status_code}  ",
          r.json().get("totalCount") if r.status_code == 200 else r.text[:150])
    time.sleep(2)


def mesh_lookup(kind, label):
    r = httpx.get(f"{MESH}/{kind}", params={"label": label, "match": "contains", "limit": 5}, timeout=60)
    print(f"MeSH {kind:10} '{label}': status={r.status_code}")
    print("   ", json.dumps(r.json())[:400] if r.status_code == 200 else r.text[:200])
    time.sleep(1)


print("=== 1. Does ClinicalTrials.gov accept MeSH fields in AREA[...]? ===")
ct_count("broad text search: query.cond=lung cancer", "", {"query.cond": "lung cancer"})
ct_count("ConditionMeshTerm = Lung Neoplasms", 'AREA[ConditionMeshTerm]"Lung Neoplasms"')
ct_count("ConditionAncestorTerm = Lung Neoplasms", 'AREA[ConditionAncestorTerm]"Lung Neoplasms"')
ct_count("Mesh OR Ancestor = Lung Neoplasms",
         'AREA[ConditionMeshTerm]"Lung Neoplasms" OR AREA[ConditionAncestorTerm]"Lung Neoplasms"')
ct_count("InterventionMeshTerm = pembrolizumab", 'AREA[InterventionMeshTerm]pembrolizumab')
ct_count("InterventionName OR OtherName = pembrolizumab",
         "AREA[InterventionName]pembrolizumab OR AREA[InterventionOtherName]pembrolizumab")

print("\n=== 2. Can NLM resolve plain words to a MeSH descriptor? ===")
for words in ["lung cancer", "breast cancer", "heart attack", "nsclc"]:
    mesh_lookup("descriptor", words)
    mesh_lookup("term", words)
