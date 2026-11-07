"""Compare example outputs before and after switching to resolved queries.

Run from the project root:  python -m scripts.compare_examples
"""
import json

EXAMPLES = ["01_phase_distribution", "02_time_series", "03_compare_drugs", "04_compare_conditions"]

for name in EXAMPLES:
    old = json.load(open(f"examples/before_resolver/{name}.json"))["meta"]
    new = json.load(open(f"examples/{name}.json"))["meta"]
    print(f"=== {name}")
    print(f"  before: analyzed {old['analyzed']:6}   total {old['total_available']}")
    print(f"  after:  analyzed {new['analyzed']:6}   total {new['total_available']}")
    for r in new.get("resolutions", []):
        print(f"  resolved {r['kind']} {r['query']!r} -> {r['status']} {r['terms']}")
