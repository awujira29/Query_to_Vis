"""Independent checks of the strict drug filter.

1. Cross-check against the API's own field-restricted search: ask ClinicalTrials.gov
   for trials where the drug appears in the intervention name or other-name fields,
   and compare with what our filter kept. Disagreements are what to inspect.
2. Precision sample: for random kept trials, show WHICH intervention matched, so
   false inclusions (trials kept by mistake) become visible.

Run from the project root:  python -m scripts.cross_check_filter
"""
import random

from app.client import fetch_studies
from app.drug_names import mentions_drug, normalize_drug_names
from app.filters import has_drug
from app.normalize import normalize_studies

CONDITION = "melanoma"
DRUG = "nivolumab"
SAMPLE_SIZE = 15
SEED = 42            # fixed, so the sample is reproducible


def matching_intervention(record, drug):
    """The intervention that made has_drug() true, for display."""
    terms = set(normalize_drug_names(drug))
    for iv in record.interventions:
        if not iv.is_drug_like:
            continue
        names = set(iv.names_normalized)
        for other in iv.other_names:
            names.update(normalize_drug_names(other))
        if terms & names:
            return iv, "rules"
        if any(mentions_drug(t, drug) for t in [iv.name, *iv.other_names]):
            return iv, "word fallback"
    return None, None


def show(record):
    print(f"  {record.nct_id}  {record.phase_label:16} {record.brief_title[:70]}")
    for iv in record.interventions:
        print(f"        {iv.intervention_type:20} | {iv.name[:55]:55} | {iv.other_names[:2]}")


def main() -> None:
    broad = fetch_studies({"query.cond": CONDITION, "query.intr": DRUG})
    records, _ = normalize_studies(broad["studies"])
    ours = {r.nct_id for r in records if has_drug(r, DRUG)}
    by_id = {r.nct_id: r for r in records}

    # ---- Check 1: the API's own intervention-field search --------------------
    advanced = f"AREA[InterventionName]{DRUG} OR AREA[InterventionOtherName]{DRUG}"
    api = fetch_studies({"query.cond": CONDITION, "filter.advanced": advanced})
    api_ids = {s["protocolSection"]["identificationModule"]["nctId"] for s in api["studies"]}

    print("=== Check 1: our filter vs the API's intervention-field search ===")
    print(f"our filter kept: {len(ours)}   API field search: {len(api_ids)}   "
          f"agree on: {len(ours & api_ids)}\n")

    only_ours = sorted(ours - api_ids)
    print(f"--- {len(only_ours)} kept by us, not by the API search "
          "(codes/brands we resolve, or false inclusions) ---")
    for nct in only_ours:
        show(by_id[nct])

    only_api = sorted(api_ids - ours)
    print(f"\n--- {len(only_api)} found by the API search, excluded by us "
          "(possible false exclusions) ---")
    for nct in only_api:
        if nct in by_id:
            show(by_id[nct])
        else:
            print(f"  {nct}  (not in the broad search results)")

    # ---- Check 2: precision sample of kept trials ----------------------------
    print(f"\n=== Check 2: {SAMPLE_SIZE} random kept trials and what matched ===")
    sample = random.Random(SEED).sample(sorted(ours), min(SAMPLE_SIZE, len(ours)))
    for nct in sample:
        iv, how = matching_intervention(by_id[nct], DRUG)
        print(f"  {nct}  [{how:13}]  {iv.intervention_type:12} | {iv.name[:60]}")


if __name__ == "__main__":
    main()