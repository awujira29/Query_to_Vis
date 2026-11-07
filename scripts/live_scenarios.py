"""Ask the running service a battery of questions and print readable summaries.

  uvicorn app.main:app            # in another terminal
  python -m scripts.live_scenarios [--only 7] [--url http://127.0.0.1:8000]

Full responses are saved to scratch/live/NN.json for inspection.
"""
import json, sys, time
from pathlib import Path
import httpx

SCENARIOS = [
    # appendix classes
    {"query": "How has the number of trials for pembrolizumab changed per year since 2015?"},
    {"query": "How many trials started each year for ovarian cancer?"},
    {"query": "How are melanoma trials distributed across phases?"},
    {"query": "What are the most common intervention types for psoriasis trials?"},
    {"query": "Compare phases for trials involving pembrolizumab vs nivolumab."},
    {"query": "Compare sponsor categories across breast cancer and lung cancer trials.", "max_studies": 5000},
    {"query": "Which countries have the most recruiting trials for lung cancer?", "top_n": 15},
    {"query": "Show a network of sponsors and drugs for melanoma trials.", "max_studies": 5000},
    {"query": "Which drugs frequently co-occur in combination studies with pembrolizumab?", "max_studies": 5000},
    # paraphrases and other classes
    {"query": "Who funds malaria trials?"},
    {"query": "Which sponsors run the most multiple sclerosis trials?", "top_n": 10},
    {"query": "What is the typical enrollment of phase 3 heart failure trials?"},
    {"query": "Is there a relationship between enrollment and duration for Parkinson's disease trials?"},
    {"query": "Compare trials per year for semaglutide vs tirzepatide since 2018"},
    # structured fields and brand names
    {"query": "How has it changed over time?", "drug_name": "Keytruda"},
    {"query": "Trials by phase", "condition": "asthma", "status": ["recruiting"], "country": "Germany"},
    # edge cases
    {"query": "Trials for zzqxnotadrug by phase"},
    {"query": "What is the weather in Boston today?"},
]


def summarize(i, req, status_code, body, seconds):
    meta, viz = body.get("meta", {}), body.get("visualization")
    print(f"\n{'=' * 100}\n[{i:02d}] {req['query']}   {json.dumps({k: v for k, v in req.items() if k != 'query'}) if len(req) > 1 else ''}")
    print(f"HTTP {status_code}  status={body.get('status')}  planner={meta.get('planner')}  "
          f"type={viz['type'] if viz else '-'}  {seconds:.1f}s")
    if body.get("message"):
        print("message:", body["message"])
    plan = meta.get("plan") or {}
    if plan:
        print(f"read as: {plan.get('interpretation')}  | series: "
              + "; ".join(f"{s['label']}={s['filters']}" for s in plan.get("series", [])))
    for r in meta.get("resolutions", []):
        print(f"resolved {r['kind']} {r['query']!r} -> {r['status']} {r.get('terms')} ({r.get('source')})")
    if viz:
        print(f"title: {viz['title']}   analyzed {meta.get('analyzed')} of {meta.get('total_available')}"
              f"{'  TRUNCATED' if meta.get('truncated') else ''}")
        data = viz["data"]
        if isinstance(data, dict):
            nodes = sorted(data["nodes"], key=lambda n: -n["weight"])[:8]
            print("  top nodes:", ", ".join(f"{n['label']} ({n['weight']})" for n in nodes))
            ids = {n["id"]: n["label"] for n in data["nodes"]}
            for e in data["edges"][:8]:
                print(f"  edge {ids[e['source']]} -- {ids[e['target']]}: {e['weight']} trials, e.g. {e['citations'][0]['nct_id'] if e['citations'] else '-'}")
        else:
            y = viz["encoding"]["y"]["field"]
            x = viz["encoding"]["x"]["field"]
            if viz["type"] == "scatter_plot":
                print(f"  {len(data)} points; stats: {meta.get('statistics')}")
            else:
                for row in data[:12]:
                    series = f" [{row['series']}]" if "series" in row else ""
                    cite = row["citations"][0] if row.get("citations") else None
                    ev = f"   e.g. {cite['nct_id']}: {cite['field'].split('.')[-1]}={cite['value'][:40]}" if cite else ""
                    print(f"  {str(row[x]):28}{series:18} {y}={row[y]}{ev}")
                if len(data) > 12:
                    print(f"  ... {len(data)} rows")
            if meta.get("statistics") and viz["type"] == "histogram":
                print("  stats:", meta["statistics"])
    for n in meta.get("notes", []):
        print("  -", n)


def main(argv):
    url = next((a.split("=", 1)[1] for a in argv if a.startswith("--url=")), "http://127.0.0.1:8000")
    only = next((int(a.split("=", 1)[1]) for a in argv if a.startswith("--only=")), None)
    Path("scratch/live").mkdir(parents=True, exist_ok=True)
    for i, req in enumerate(SCENARIOS, 1):
        if only and i != only:
            continue
        t = time.time()
        r = httpx.post(f"{url}/query", json=req, timeout=600)
        body = r.json()
        Path(f"scratch/live/{i:02d}.json").write_text(json.dumps({"request": req, "response": body}, indent=2))
        summarize(i, req, r.status_code, body, time.time() - t)


if __name__ == "__main__":
    main(sys.argv[1:])
