"""Print a readable summary of a saved network example, to check that it makes sense.

  python -m scripts.summarize_network examples/06_network_sponsors.json
"""
import json
import sys


def main(path: str) -> None:
    d = json.load(open(path))
    d = d["response"] if "response" in d else d          # example files wrap the response
    meta, viz = d["meta"], d["visualization"]
    if not viz:
        print(f"status: {d['status']} (no graph)")
        return
    nodes = {n["id"]: n for n in viz["data"]["nodes"]}
    edges = viz["data"]["edges"]

    print(viz["title"])
    print(f"  {len(nodes)} nodes, {len(edges)} edges; analyzed {meta['analyzed']} of "
          f"{meta['total_available']} trials; layout {meta['render_hints'].get('layout')}")
    for note in meta["notes"]:
        print("  -", note)

    for t in meta["render_hints"].get("node_types") or sorted({n["type"] for n in nodes.values()}):
        group = sorted((n for n in nodes.values() if n["type"] == t),
                       key=lambda n: (-n["weight"], n["label"]))
        if not group:
            continue
        print(f"\n  {group[0]['type_label']} nodes, largest first ({len(group)}):")
        for n in group[:15]:
            star = " *focus*" if n["focus"] else ""
            print(f"    {n['label'][:40]:40} trials {n['weight']:5}  edges {n['degree']:3}{star}")

    print("\n  Strongest edges, with the first cited trial:")
    for e in edges[:20]:
        a, b = nodes[e["source"]]["label"], nodes[e["target"]]["label"]
        first = [c for c in e["citations"] if c["nct_id"] == e["citations"][0]["nct_id"]] \
            if e["citations"] else []
        evidence = " | ".join(str(c["value"])[:45] for c in first)
        nct = first[0]["nct_id"] if first else "-"
        print(f"    {a[:26]:26} -- {b[:26]:26} {e['weight']:5} trials  {nct}: {evidence}")


if __name__ == "__main__":
    main(sys.argv[1])
