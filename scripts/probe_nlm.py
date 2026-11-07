"""Compare NLM's existing tools against our expected answers.

1. ClinicalTrials.gov default search (Relaxation) vs EXPANSION[Concept]
2. NCBI E-utilities MeSH search: does it map lay words to the expected descriptor?
3. RxNorm: does it map brands and codes to the expected ingredient?

Run from the project root:  python -m scripts.probe_nlm
"""
import time

import httpx

CT = "https://clinicaltrials.gov/api/v2/studies"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
RXNAV = "https://rxnav.nlm.nih.gov/REST"

CONDITIONS = {
    "heart attack": "Myocardial Infarction", "lung cancer": "Lung Neoplasms",
    "nsclc": "Carcinoma, Non-Small-Cell Lung", "high blood pressure": "Hypertension",
    "COPD": "Pulmonary Disease, Chronic Obstructive", "colon cancer": "Colonic Neoplasms",
    "depression": "Depressive Disorder", "TB": "Tuberculosis",
    "opioid addiction": "Opioid-Related Disorders", "PCOS": "Polycystic Ovary Syndrome",
    "chronic pain": "Chronic Pain", "covid": "COVID-19",
}
DRUGS = {
    "Keytruda": "pembrolizumab", "MK-3475": "pembrolizumab", "BMS-936558": "nivolumab",
    "Ozempic": "semaglutide", "Humira": "adalimumab", "Lipitor": "atorvastatin",
    "Gleevec": "imatinib", "Tylenol": "acetaminophen",
}


def get(url, params):
    r = httpx.get(url, params=params, timeout=60)
    time.sleep(1.2)
    r.raise_for_status()
    return r.json()


def ct_count(params):
    return get(CT, {**params, "countTotal": "true", "pageSize": 1,
                    "fields": "NCTId"}).get("totalCount")


def mesh_descriptors(words):
    ids = get(f"{EUTILS}/esearch.fcgi", {"db": "mesh", "term": words, "retmode": "json",
                                        "retmax": 3})["esearchresult"]["idlist"]
    if not ids:
        return []
    summ = get(f"{EUTILS}/esummary.fcgi", {"db": "mesh", "id": ",".join(ids),
                                          "retmode": "json"})["result"]
    return [summ[i]["ds_meshterms"][0] for i in ids if summ.get(i, {}).get("ds_meshterms")]


def rxnorm_ingredient(name):
    data = get(f"{RXNAV}/drugs.json", {"name": name})
    for group in data.get("drugGroup", {}).get("conceptGroup", []) or []:
        for c in group.get("conceptProperties", []) or []:
            return c["name"]
    cands = get(f"{RXNAV}/approximateTerm.json", {"term": name, "maxEntries": 1})
    cand = (cands.get("approximateGroup", {}).get("candidate") or [{}])[0]
    return cand.get("name") or f"rxcui {cand.get('rxcui')}"


print("=== 1+2. Conditions: search modes and NLM MeSH mapping ===")
print(f"{'query':20} {'default':>8} {'Concept':>8}  NLM MeSH mapping (expected)")
for words, expected in CONDITIONS.items():
    default = ct_count({"query.cond": words})
    concept = ct_count({"query.cond": f'EXPANSION[Concept] "{words}"'})
    mapped = mesh_descriptors(words)
    mark = "OK " if expected in mapped else "?? "
    print(f"{words:20} {default:>8} {concept:>8}  {mark}{mapped} ({expected})")

print("\n=== 3. Drugs: RxNorm resolution ===")
for name, expected in DRUGS.items():
    got = rxnorm_ingredient(name)
    mark = "OK " if expected.lower() in str(got).lower() else "?? "
    print(f"{name:12} -> {mark}{got}   (expected {expected})")
