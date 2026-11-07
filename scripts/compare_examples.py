"""Compare example outputs before and after switching to resolved (MeSH) queries.

Run from the project root:  python -m scripts.compare_examples

examples/before_resolver/ holds outputs from the text-search era (query.cond / query.intr
plus post-filters); examples/ holds the current outputs. The point of the comparison is the
"analyzed" count: resolved queries find trials that text search missed (records with no
free-text mention but a MeSH term) and drop trials that text search matched by accident.
"""
import json
from pathlib import Path

EXAMPLES = ["02_time_series", "03_compare_drugs", "04_compare_conditions"]


def response_of(path: Path) -> dict:
    data = json.loads(path.read_text())
    return data["response"] if "response" in data else data     # new files wrap the response


for name in EXAMPLES:
    old_path = Path(f"examples/before_resolver/{name}.json")
    new_path = Path(f"examples/{name}.json")
    print(f"=== {name}")
    for label, path in (("before", old_path), ("after", new_path)):
        if not path.exists():
            print(f"  {label}: (missing: {path})")
            continue
        meta = response_of(path)["meta"]
        print(f"  {label:6}: analyzed {meta['analyzed']:6}   total {meta['total_available']}   "
              f"query: {meta['query']}")
    if new_path.exists():
        for r in response_of(new_path)["meta"].get("resolutions", []):
            print(f"  resolved {r['kind']} {r['query']!r} -> {r['status']} {r['terms']} ({r.get('source')})")
