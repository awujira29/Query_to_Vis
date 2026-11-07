"""Re-verify earlier design decisions against live data, now that the resolver exists.

  python -m scripts.verify_decisions

Each check prints PASS or REVIEW with the numbers behind it:
  D1  strict drug post-filter (on a text search)  vs  resolved drug query (API-side)
  D2  word-based condition filter                 vs  resolved condition query (MeSH)
  D3  exact category counts and fetched records use the same definition (sums must match)
Uses the cached client, so data fetched by earlier scripts is reused.
"""
from collections import Counter

from app.client import count_studies, fetch_studies, get_json
from app.filters import has_condition, has_drug
from app.labels import DISPLAY
from app.normalize import normalize_studies
from app.resolve import merge_params, resolve


def fetch_ids(params: dict, cap: int = 20000) -> set[str]:
    ids, token = [], None
    while True:
        p = {**params, "fields": "NCTId", "pageSize": 1000}
        if token:
            p["pageToken"] = token
        page = get_json("/studies", p)
        ids += [s["protocolSection"]["identificationModule"]["nctId"] for s in page.get("studies", [])]
        token = page.get("nextPageToken")
        if not token or len(ids) >= cap:
            return set(ids)


def verdict(ok: bool) -> str:
    return "PASS  " if ok else "REVIEW"


def show(title, records_by_id, ids, n=5):
    print(f"    {title} ({len(ids)}):")
    for nct in sorted(ids)[:n]:
        r = records_by_id.get(nct)
        print(f"      {nct}  {r.brief_title[:75] if r else '(not in the text-search records)'}")


def d1_drug():
    print("\n=== D1: strict drug filter vs resolved drug query (nivolumab, melanoma) ===")
    broad = fetch_studies({"query.cond": "melanoma", "query.intr": "nivolumab"})
    records, _ = normalize_studies(broad["studies"])
    by_id = {r.nct_id: r for r in records}
    old = {r.nct_id for r in records if has_drug(r, "nivolumab")}

    res = resolve("drug", "nivolumab")
    print(f"  resolver: {res.note}")
    new = fetch_ids(merge_params(res.api_params, {"query.cond": "melanoma"}))

    jaccard = len(old & new) / len(old | new) if old | new else 1.0
    print(f"  {verdict(jaccard >= 0.95)} old filter {len(old)}, resolved {len(new)}, "
          f"shared {len(old & new)} (agreement {100 * jaccard:.1f}%)")
    show("only the old filter", by_id, old - new)
    show("only the resolved query", by_id, new - old)


def d2_condition():
    print("\n=== D2: word-based condition filter vs resolved condition query (lung cancer) ===")
    broad = fetch_studies({"query.cond": "lung cancer"}, max_studies=5000)
    records, _ = normalize_studies(broad["studies"])
    by_id = {r.nct_id: r for r in records}
    word_keep = {r.nct_id for r in records if has_condition(r, "lung cancer")}

    res = resolve("condition", "lung cancer")
    print(f"  resolver: {res.note}")
    resolved = fetch_ids(res.api_params)

    sample = set(by_id)
    both = sample & word_keep & resolved
    only_word = (sample & word_keep) - resolved
    only_mesh = (sample & resolved) - word_keep
    agree = 1 - (len(only_word) + len(only_mesh)) / len(sample)
    print(f"  {verdict(agree >= 0.9)} within the {len(sample)} text-search trials: "
          f"both keep {len(both)}, only words {len(only_word)}, only resolved {len(only_mesh)} "
          f"(agreement {100 * agree:.1f}%)")
    for label, ids in [("conditions of trials kept only by the word filter", only_word),
                       ("conditions of trials kept only by the resolved query", only_mesh)]:
        terms = Counter(c.term for nct in ids for c in by_id[nct].conditions if c.origin == "sponsor")
        print(f"    {label}: {terms.most_common(8)}")


def d3_counts():
    print("\n=== D3: exact category counts match the resolved record set (melanoma, sponsor class) ===")
    res = resolve("condition", "melanoma")
    print(f"  resolver: {res.note}")
    total = count_studies(res.api_params)
    per_class = {c: count_studies(merge_params(res.api_params,
                                               {"filter.advanced": f"AREA[LeadSponsorClass]{c}"}))
                 for c in DISPLAY["sponsor_class"]}
    fetched = len(fetch_ids(res.api_params))
    ok = sum(per_class.values()) == total == fetched
    print(f"  {verdict(ok)} total {total}, sum of per-class counts {sum(per_class.values())}, "
          f"records fetched {fetched}")
    print(f"    per class: { {k: v for k, v in per_class.items() if v} }")


if __name__ == "__main__":
    d1_drug()
    d2_condition()
    d3_counts()
