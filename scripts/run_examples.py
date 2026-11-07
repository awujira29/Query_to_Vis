"""Produce the example runs: real requests through the HTTP app, saved with their responses.

  python -m scripts.run_examples                 # live: calls ClinicalTrials.gov (and the LLM if a key is set)
  python -m scripts.run_examples --offline       # replay: cached API responses only (CTGOV_OFFLINE=1)
  python -m scripts.run_examples --only 03       # one example

Each examples/NN_name.json holds {"request": ..., "http_status": ..., "response": ...}, exactly
as POST /query received and returned them. examples/README.md is regenerated as an index.
Check the outputs afterwards with:  python -m scripts.verify_examples
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

if "--offline" in sys.argv:
    os.environ["CTGOV_OFFLINE"] = "1"

from fastapi.testclient import TestClient          # noqa: E402  (after the env is set)

from app.main import app                            # noqa: E402

OUT = Path("examples")

# Chosen to cover every question class in the assignment appendix and every visualization
# type the service produces. max_studies is raised where the full set is a few pages.
EXAMPLES = [
    ("01_phase_distribution",
     {"query": "How are melanoma trials testing nivolumab distributed across phases?"}),
    ("02_time_series",
     {"query": "How has the number of pembrolizumab trials changed over time?", "max_studies": 5000}),
    ("03_compare_drugs",
     {"query": "Compare phases for trials involving pembrolizumab vs nivolumab.", "max_studies": 5000}),
    ("04_compare_conditions",
     {"query": "Compare sponsor categories across breast cancer and lung cancer trials.",
      "max_studies": 5000}),
    ("05_countries",
     {"query": "Which countries have the most melanoma trials?", "top_n": 15, "max_studies": 5000}),
    ("06_network_sponsors",
     {"query": "Show a network of sponsors and drugs for melanoma trials.", "max_studies": 5000}),
    ("07_network_drugs",
     {"query": "Which drugs are most often combined with pembrolizumab?", "max_studies": 5000}),
    ("08_histogram_enrollment",
     {"query": "What is the typical enrollment of melanoma trials?", "max_studies": 5000}),
    ("09_scatter",
     {"query": "Is there a relationship between enrollment and duration for melanoma trials?",
      "max_studies": 5000}),
]


def summarize(name: str, request: dict, body: dict) -> str:
    meta, viz = body["meta"], body.get("visualization")
    kind = viz["type"] if viz else body["status"]
    size = ""
    if viz:
        data = viz["data"]
        size = (f"{len(data['nodes'])} nodes / {len(data['edges'])} edges" if isinstance(data, dict)
                else f"{len(data)} data points")
    return (f"| `{name}` | {request['query']} | `{kind}` | {meta.get('analyzed', 0):,} of "
            f"{meta.get('total_available') or 0:,} | {size} | {meta.get('planner')} |")


def main(argv: list[str]) -> None:
    only = next((a.split("=", 1)[1] for a in argv if a.startswith("--only=")), None)
    client = TestClient(app)
    OUT.mkdir(exist_ok=True)
    rows = []
    for name, request in EXAMPLES:
        if only and not name.startswith(only):
            continue
        t = time.time()
        http = client.post("/query", json=request)
        body = http.json()
        (OUT / f"{name}.json").write_text(json.dumps(
            {"request": request, "http_status": http.status_code, "response": body}, indent=2))
        viz = body.get("visualization")
        print(f"{name:28} HTTP {http.status_code}  {body['status']:10} "
              f"{(viz or {}).get('type', '-'):18} analyzed {body['meta'].get('analyzed', 0):>6}  "
              f"{time.time() - t:5.1f}s  planner={body['meta'].get('planner')}")
        if body["status"] != "ok":
            print("    ", body.get("message"))
        rows.append(summarize(name, request, body))

    if not only:
        health = client.get("/health").json()
        index = ["# Example runs", "",
                 "Each file holds the exact request sent to `POST /query`, the HTTP status, and the "
                 "full response. Regenerate with `python -m scripts.run_examples` (live) or "
                 "`python -m scripts.run_examples --offline` (replay from `.cache/ctgov`), then check "
                 "them with `python -m scripts.verify_examples`.", "",
                 f"Generated with planner mode: `{health['planner']}`, offline replay: `{health['offline']}`.",
                 "",
                 "| File | Query | Visualization | Trials analyzed | Size | Planner |",
                 "|---|---|---|---|---|---|", *rows, ""]
        (OUT / "README.md").write_text("\n".join(index))
        print(f"\nwrote {len(rows)} examples and examples/README.md")


if __name__ == "__main__":
    main(sys.argv[1:])
