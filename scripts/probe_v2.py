"""Probe v2: MeSH resolution with a hierarchy-aware specificity test.

Changes from v1, each driven by the v1 failures:
  1. "Too broad" is no longer an upper size ratio (that wrongly rejected brand
     names and abbreviations, e.g. Ozempic, TB, whose text search undercounts).
     Instead: a term is too broad if a more specific term in the same results
     covers nearly as much and sits under it (co-occurs with it almost always).
  2. Coverage threshold 50% (correct drug terms sat at 51-58%; narrow terms <= 41%).
  3. Proposals may be a set of terms combined with OR (depression, colon cancer).
  4. A data-only pick is computed too, to see whether the AI is needed at all.

Part A re-tests the v1 cases (used to tune). Part B is a holdout set never used
for tuning, which is the honest test of whether the rules generalize.

Run from the project root:  python -m scripts.probe_v2
Uses the project client (throttled + cached), so reruns are fast.
"""
import json
from collections import Counter

from app.client import get_json

SAMPLE = 300
MIN_COVERAGE = 50          # % of sampled trials the term set must cover
MIN_RATIO = 0.4            # registry / broad search; below this the term is too narrow
NEARLY_AS_MUCH = 0.9       # a child covering >= 90% of the parent's coverage...
NESTED = 0.95              # ...and co-occurring with it >= 95% of the time => parent too broad

KINDS = {
    "condition": {"query": "query.cond", "mesh": "ConditionMeshTerm",
                  "ancestor": "ConditionAncestorTerm", "browse": "conditionBrowseModule"},
    "drug": {"query": "query.intr", "mesh": "InterventionMeshTerm",
             "ancestor": "InterventionAncestorTerm", "browse": "interventionBrowseModule"},
}

TUNING = {
    "condition": {
        "heart attack": ["Myocardial Infarction"], "high blood pressure": ["Hypertension"],
        "kidney failure": ["Renal Insufficiency"], "flu": ["Influenza, Human"],
        "preterm birth": ["Premature Birth"], "insomnia": ["Sleep Initiation and Maintenance Disorders"],
        "chronic pain": ["Chronic Pain"], "COPD": ["Pulmonary Disease, Chronic Obstructive"],
        "ADHD": ["Attention Deficit Disorder with Hyperactivity"], "HIV": ["HIV Infections"],
        "AML": ["Leukemia, Myeloid, Acute"], "CKD": ["Renal Insufficiency, Chronic"],
        "TB": ["Tuberculosis"], "alzheimer's": ["Alzheimer Disease"],
        "parkinson's": ["Parkinson Disease"], "multiple sclerosis": ["Multiple Sclerosis"],
        "epilepsy": ["Epilepsy"], "migraine": ["Migraine Disorders"],
        "depression": ["Depression", "Depressive Disorder"], "schizophrenia": ["Schizophrenia"],
        "autism": ["Autism Spectrum Disorder"], "stroke": ["Stroke"],
        "rheumatoid arthritis": ["Arthritis, Rheumatoid"], "lupus": ["Lupus Erythematosus, Systemic"],
        "crohn's disease": ["Crohn Disease"], "psoriasis": ["Psoriasis"],
        "hepatitis C": ["Hepatitis C"], "covid": ["COVID-19"], "sepsis": ["Sepsis"],
        "obesity": ["Obesity"], "type 1 diabetes": ["Diabetes Mellitus, Type 1"],
        "sickle cell": ["Anemia, Sickle Cell"], "cystic fibrosis": ["Cystic Fibrosis"],
        "atrial fibrillation": ["Atrial Fibrillation"], "prostate cancer": ["Prostatic Neoplasms"],
        "colon cancer": ["Colonic Neoplasms", "Colorectal Neoplasms"],
        "pancreatic cancer": ["Pancreatic Neoplasms"], "glioblastoma": ["Glioblastoma"],
        "lung cancer": ["Lung Neoplasms"], "nsclc": ["Carcinoma, Non-Small-Cell Lung"],
        "breast cancer": ["Breast Neoplasms"], "melanoma": ["Melanoma"],
    },
    "drug": {
        "metformin": ["Metformin"], "atorvastatin": ["Atorvastatin"], "aspirin": ["Aspirin"],
        "warfarin": ["Warfarin"], "tamoxifen": ["Tamoxifen"], "sertraline": ["Sertraline"],
        "apixaban": ["apixaban"], "semaglutide": ["semaglutide"], "osimertinib": ["osimertinib"],
        "remdesivir": ["remdesivir"], "adalimumab": ["Adalimumab"], "trastuzumab": ["Trastuzumab"],
        "rituximab": ["Rituximab"], "imatinib": ["Imatinib Mesylate"], "Humira": ["Adalimumab"],
        "Herceptin": ["Trastuzumab"], "Gleevec": ["Imatinib Mesylate"], "Ozempic": ["semaglutide"],
        "MK-3475": ["pembrolizumab"], "lithium": ["Lithium Carbonate", "Lithium Compounds"],
    },
}

HOLDOUT = {
    "condition": {
        "heart failure": ["Heart Failure"], "osteoporosis": ["Osteoporosis"],
        "anxiety": ["Anxiety Disorders", "Anxiety"], "bipolar": ["Bipolar Disorder"],
        "ovarian cancer": ["Ovarian Neoplasms"], "liver cancer": ["Liver Neoplasms"],
        "leukemia": ["Leukemia"], "malaria": ["Malaria"],
        "RSV": ["Respiratory Syncytial Virus Infections"], "endometriosis": ["Endometriosis"],
        "PCOS": ["Polycystic Ovary Syndrome"], "gout": ["Gout"],
        "macular degeneration": ["Macular Degeneration"],
        "opioid addiction": ["Opioid-Related Disorders"], "IBS": ["Irritable Bowel Syndrome"],
    },
    "drug": {
        "ibuprofen": ["Ibuprofen"], "Keytruda": ["pembrolizumab"], "Lipitor": ["Atorvastatin"],
        "insulin glargine": ["Insulin Glargine"], "dexamethasone": ["Dexamethasone"],
        "bevacizumab": ["Bevacizumab"], "Avastin": ["Bevacizumab"],
        "levothyroxine": ["Thyroxine"], "dupilumab": ["dupilumab"], "ketamine": ["Ketamine"],
    },
}


def sample(kind, words):
    k = KINDS[kind]
    data = get_json("/studies", {k["query"]: words, "pageSize": SAMPLE, "countTotal": "true",
                                 "fields": f"NCTId,{k['mesh']},{k['ancestor']}"})
    trials, direct = [], Counter()
    for s in data.get("studies", []):
        browse = s.get("derivedSection", {}).get(k["browse"], {})
        own = {m["term"] for m in browse.get("meshes", [])}       # terms assigned to the trial
        direct.update(own)
        trials.append(own | {a["term"] for a in browse.get("ancestors", [])})
    return trials, direct, data.get("totalCount") or 0


def registry(kind, terms):
    k = KINDS[kind]
    adv = " OR ".join(f'AREA[{k["mesh"]}]"{t}" OR AREA[{k["ancestor"]}]"{t}"' for t in terms)
    data = get_json("/studies", {"filter.advanced": adv, "countTotal": "true",
                                 "pageSize": 1, "fields": "NCTId"})
    return data.get("totalCount") or 0


def too_broad(term, trials, counts, direct, kind):
    """The more specific term that makes `term` too broad, or None.

    B is more specific than `term` when, inside the sample, nearly every trial
    with B also has `term` and B covers nearly as many trials; AND, globally,
    B is not larger than `term` in the whole registry. The global check fixes
    the direction: a sample searched for "melanoma" makes its parent "Skin
    Diseases" look nested inside it, but Skin Diseases has 9x more trials
    overall, so it cannot be a child. When global sizes are within 2%, the
    term assigned directly to more trials (a leaf, not an ancestor) wins.
    """
    a = counts.get(term, 0)
    if not a:
        return None
    for b, nb in counts.most_common(60):
        if b == term or nb < NEARLY_AS_MUCH * a or nb > a:
            continue
        both = sum(1 for t in trials if b in t and term in t)
        if both < NESTED * nb:
            continue                                  # b is not inside term, even locally
        reg_a, reg_b = registry(kind, [term]), registry(kind, [b])
        if not reg_a or not reg_b:
            continue
        r = reg_b / reg_a
        if r < 0.98:
            return b                                  # clearly smaller overall: a real child
        if r <= 1.02 and direct.get(b, 0) > direct.get(term, 0):
            return b                                  # tie: the directly assigned term wins
    return None


def evaluate(kind, terms, trials, counts, direct, broad):
    covered = sum(1 for t in trials if any(x in t for x in terms))
    coverage = 100 * covered / max(len(trials), 1)
    reg = registry(kind, terms)
    ratio = reg / broad if broad else 0
    broader_than = [too_broad(t, trials, counts, direct, kind) for t in terms]
    reasons = []
    if reg == 0:
        reasons.append("not found")
    if coverage < MIN_COVERAGE:
        reasons.append(f"coverage {coverage:.0f}%")
    if ratio < MIN_RATIO:
        reasons.append(f"ratio {ratio:.2f}")
    if all(broader_than):
        reasons.append(f"too broad (prefer {broader_than[0]})")
    return {"terms": terms, "coverage": round(coverage), "registry": reg,
            "ratio": round(ratio, 2), "passed": not reasons, "reasons": reasons}


def data_only_pick(trials, counts, direct, kind):
    """Without AI: the highest-coverage term that passes and isn't too broad."""
    n = max(len(trials), 1)
    for term, c in counts.most_common(60):
        if 100 * c / n < MIN_COVERAGE:
            break
        if not too_broad(term, trials, counts, direct, kind):
            return term
    return None


def run(name, cases):
    rows = []
    print(f"\n######## {name} ########")
    for kind, queries in cases.items():
        for words, proposed in queries.items():
            trials, direct, broad = sample(kind, words)
            counts = Counter(t for tr in trials for t in tr)
            res = evaluate(kind, proposed, trials, counts, direct, broad)
            pick = data_only_pick(trials, counts, direct, kind)
            agrees = pick in proposed
            rows.append({"set": name, "kind": kind, "query": words, "broad": broad,
                         "proposal": res, "data_only": pick, "data_only_agrees": agrees})
            mark = "PASS" if res["passed"] else "FAIL"
            print(f"[{mark}] {kind:9} {words:22} -> {' | '.join(proposed):45} "
                  f"cov {res['coverage']:3}% ratio {res['ratio']:5} "
                  f"{'; '.join(res['reasons']) or ''}")
            print(f"        data-only pick: {pick}{'' if agrees else '   <-- differs'}")
    return rows


def summary(rows, name):
    for kind in KINDS:
        rs = [r for r in rows if r["set"] == name and r["kind"] == kind]
        if not rs:
            continue
        ok = sum(r["proposal"]["passed"] for r in rs)
        agree = sum(r["data_only_agrees"] for r in rs)
        print(f"  {name:8} {kind:9}: proposal passed {ok}/{len(rs)}, "
              f"data-only agrees with proposal {agree}/{len(rs)}")


if __name__ == "__main__":
    rows = run("tuning", TUNING) + run("holdout", HOLDOUT)
    json.dump(rows, open("scripts/probe_v2.json", "w"), indent=2)
    print("\n======== SUMMARY ========")
    summary(rows, "tuning")
    summary(rows, "holdout")
