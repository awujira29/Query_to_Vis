"""Inspect trials excluded by the strict drug filter, to find false exclusions.

Run from the project root:  python -m scripts.check_exclusions
"""
from collections import Counter

from app.client import fetch_studies
from app.filters import has_drug
from app.normalize import normalize_studies

CONDITION = "melanoma"
DRUG = "nivolumab"
KNOWN_TRUE = {"NCT01721772", "NCT07221734", "NCT06587451"}   # trials expected to be kept


def main() -> None:
    fetched = fetch_studies({"query.cond": CONDITION, "query.intr": DRUG})
    records, _ = normalize_studies(fetched["studies"])
    excluded = [r for r in records if not has_drug(r, DRUG)]

    print(f"{len(excluded)} of {len(records)} excluded\n")
    print("By study type:", dict(Counter(r.study_type for r in excluded)), "\n")

    # Interventional trials with a phase are where false exclusions hide.
    suspects = [r for r in excluded if r.phases or r.nct_id in KNOWN_TRUE]
    print(f"--- {len(suspects)} excluded trials with a phase (check these) ---\n")
    for r in suspects:
        flag = "  <-- KNOWN TRUE" if r.nct_id in KNOWN_TRUE else ""
        print(f"{r.nct_id}  {r.phase_label:16} {r.brief_title[:70]}{flag}")
        for iv in r.interventions:
            print(f"      {iv.intervention_type:20} | {iv.name[:60]:60} | {iv.other_names[:3]}")
        print()

    missing_known = KNOWN_TRUE - {r.nct_id for r in records}
    if missing_known:
        print("Not returned by the API at all:", missing_known)


if __name__ == "__main__":
    main()