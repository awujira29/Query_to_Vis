"""Compare example outputs before and after switching to resolved queries.

Run from the project root:  python -m scripts.compare_examples
"""
import json
from pathlib import Path

EXAMPLES = ["01_phase_distribution", "02_time_series", "03_compare_drugs", "04_compare_conditions"]

for name in EXAMPLES:
    old_path = Path(f"examples/before_resolver/{name}.json")
    new_path = Path(f"examples/{name}.json")
    print(f"=== {name}")
    for label, path in (("before", old_path), ("after", new_path)):
        if not path.exists():
            print(f"  {label}: (missing: {path})")
            continue
        meta = json.loads(path.read_text())["meta"]
        print(f"  {label:6}: analyzed {meta['analyzed']:6}   total {meta['total_available']}")
    if new_path.exists():
        for r in json.loads(new_path.read_text())["meta"].get("resolutions", []):
            print(f"  resolved {r['kind']} {r['query']!r} -> {r['status']} {r['terms']}")
