"""Check every example output for internal consistency and traceability to source records.

  python -m scripts.verify_examples              # all examples
  python -m scripts.verify_examples --markdown   # summary table for the README

Checks, per example (each prints PASS or FAIL with the numbers):
  schema       the response validates against QueryResponse (the documented contract)
  encoding     every field named in `encoding` exists in every datum (rows, points, nodes, edges)
  arithmetic   single-valued dimensions: counts sum to the analyzed trials; multi-valued: to at
               least the trials with a value; histogram bins: to analyzed minus missing;
               time series: consecutive years, counts never exceed analyzed; grouped bars: every
               series has every category (zero-filled) and percent = count / series size
  ids          len(nct_ids) == min(count, 100), the truncated flag is right, IDs are unique and
               well-formed, and every cited trial is among the datum's trials
  graph        every edge joins two listed nodes; edge weight <= both node weights; the focus
               node named in render_hints is present; no node without an edge (except the focus)
  citations    each citation's `field` path resolved in the raw ClinicalTrials.gov record (from
               the response cache) gives exactly the cited `value`
  source       exact source counts, where present, sum to the series total at the source
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from app.client import CACHE_DIR
from app.schemas.response import QueryResponse

MULTI_VALUED = {"country", "drug", "intervention_type", "condition"}
MAX_IDS = 100
NCT = re.compile(r"^NCT\d{8}$")


# ---------------------------------------------------------------------------
# Raw records, from the client's cache
# ---------------------------------------------------------------------------

def load_raw_index() -> dict[str, list[dict]]:
    """nct_id -> every distinct raw version of the study seen in cached API pages.

    Different requests fetch different field sets (a MeSH-sampling request returns only
    NCTId and MeSH terms), so a citation is checked against every version: the field it
    names must be present with the cited value in at least one of them.
    """
    index: dict[str, list[dict]] = {}
    for file in CACHE_DIR.glob("*.json"):
        try:
            data = json.loads(file.read_text())
        except ValueError:
            continue
        for study in data.get("studies", []) if isinstance(data, dict) else []:
            nct = study.get("protocolSection", {}).get("identificationModule", {}).get("nctId")
            if nct and study not in index.setdefault(nct, []):
                index[nct].append(study)
    return index


def resolve_path(obj, path: str):
    """'protocolSection.armsInterventionsModule.interventions[3].name' -> the value, or KeyError."""
    for part in path.split("."):
        m = re.fullmatch(r"(\w+)(?:\[(\d+|\*)\])?", part)
        if not m:
            raise KeyError(path)
        key, idx = m.group(1), m.group(2)
        if isinstance(obj, list):            # after a [*]
            obj = [o[key] for o in obj]
        else:
            obj = obj[key]
        if idx == "*":
            pass
        elif idx is not None:
            obj = obj[int(idx)]
    return obj


def as_text(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def citation_matches(citation: dict, raw: dict) -> bool:
    try:
        actual = resolve_path(raw, citation["field"])
    except (KeyError, IndexError, TypeError):
        return False
    cited = citation["value"]
    if isinstance(actual, list) and not isinstance(cited, list):
        # "[*].country" style: the cited text is one value or a sorted, deduplicated join
        texts = [as_text(a) for a in actual]
        return cited in texts or cited == ", ".join(sorted(set(texts))) or cited == ", ".join(texts)
    text = as_text(actual)
    return text == cited or text.lower() == cited.lower()


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

class Report:
    def __init__(self, name: str):
        self.name = name
        self.results: list[tuple[str, bool, str]] = []

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        self.results.append((label, ok, detail))

    @property
    def passed(self) -> bool:
        return all(ok for _, ok, _ in self.results)


def check_ids(rep: Report, label: str, data: list[dict], count_key: str) -> None:
    problems = []
    for d in data:
        n, ids = d[count_key], d.get("nct_ids", [])
        if len(ids) != min(n, MAX_IDS):
            problems.append(f"{label}: {len(ids)} ids for count {n}")
        if d.get("nct_ids_truncated") != (n > MAX_IDS):
            problems.append(f"{label}: truncated flag wrong for count {n}")
        if len(set(ids)) != len(ids) or not all(NCT.match(i) for i in ids):
            problems.append(f"{label}: ids not unique or malformed")
        cited = {c["nct_id"] for c in d.get("citations", [])}
        if not d.get("nct_ids_truncated") and not cited <= set(ids):
            problems.append(f"{label}: cited trial not among the datum's trials")
    rep.check("ids", not problems, "; ".join(problems[:3]) or f"{len(data)} data checked")


def check_citations(rep: Report, data: list[dict], raw_index: dict) -> None:
    total = ok = missing_raw = 0
    bad = []
    for d in data:
        for c in d.get("citations", []):
            total += 1
            versions = raw_index.get(c["nct_id"])
            if not versions:
                missing_raw += 1
                continue
            if any(citation_matches(c, raw) for raw in versions):
                ok += 1
            else:
                bad.append(f"{c['nct_id']} {c['field']} = {c['value']!r}")
    rep.check("citations", not bad and total > 0,
              f"{ok}/{total} verified against raw records" + (f", {missing_raw} not in cache" if missing_raw else "")
              + ("; mismatches: " + "; ".join(bad[:3]) if bad else ""))


def verify(path: Path, raw_index: dict) -> Report:
    rep = Report(path.name)
    saved = json.loads(path.read_text())
    body = saved["response"]
    try:
        QueryResponse.model_validate(body)
        rep.check("schema", True, f"status {body['status']}")
    except Exception as e:                      # noqa: BLE001
        rep.check("schema", False, str(e)[:200])
        return rep
    viz, meta = body["visualization"], body["meta"]
    if not viz:
        rep.check("encoding", False, "no visualization")
        return rep
    data = viz["data"]
    enc = {k: v["field"] for k, v in viz["encoding"].items()}
    analyzed, missing = meta["analyzed"], meta["missing_count"]

    if viz["type"] == "network_graph":
        nodes, edges = data["nodes"], data["edges"]
        fields_ok = all(enc[k] in n for k in ("node_id", "node_label", "node_color", "node_size") for n in nodes) \
            and all(enc[k] in e for k in ("edge_source", "edge_target", "edge_width") for e in edges)
        rep.check("encoding", fields_ok, f"{len(nodes)} nodes, {len(edges)} edges")
        ids = {n["id"]: n for n in nodes}
        problems = [f"edge {e['source']}-{e['target']} has a missing node" for e in edges
                    if e["source"] not in ids or e["target"] not in ids]
        problems += [f"edge weight {e['weight']} exceeds a node weight" for e in edges
                     if e["source"] in ids and e["target"] in ids
                     and e["weight"] > min(ids[e["source"]]["weight"], ids[e["target"]]["weight"])]
        degree = {}
        for e in edges:
            degree[e["source"]] = degree.get(e["source"], 0) + 1
            degree[e["target"]] = degree.get(e["target"], 0) + 1
        problems += [f"node {n['id']} has no edge" for n in nodes if not degree.get(n["id"]) and not n["focus"]]
        problems += [f"degree mismatch for {n['id']}" for n in nodes if n["degree"] != degree.get(n["id"], 0)]
        focus = meta["render_hints"].get("focus_node")
        if focus and focus not in ids:
            problems.append(f"focus {focus} missing")
        rep.check("graph", not problems, "; ".join(problems[:3]) or "endpoints, weights, degrees, focus consistent")
        check_ids(rep, "node", nodes, "weight")
        check_ids(rep, "edge", edges, "weight")
        check_citations(rep, nodes + edges, raw_index)
        rep.check("arithmetic", meta["missing_count"] <= analyzed, f"{analyzed} analyzed, {missing} link nothing")
        return rep

    rows = data
    rep.check("encoding", all(f in r for f in enc.values() for r in rows), f"{len(rows)} rows, fields {sorted(set(enc.values()))}")

    if viz["type"] in ("bar_chart", "grouped_bar_chart", "histogram"):
        dim = meta["plan"]["dimension"] if viz["type"] != "histogram" else None
        if viz["type"] == "grouped_bar_chart":
            series = {r["series"] for r in rows}
            cats = {r["label"] for r in rows}
            sizes = {s["label"]: s["analyzed"] for s in meta["series"]}
            problems = []
            if len(rows) != len(series) * len(cats):
                problems.append("not every series has every category")
            for s in series:
                srows = [r for r in rows if r["series"] == s]
                total = sum(r["trial_count"] for r in srows)
                if dim not in MULTI_VALUED and total != sizes[s]:
                    problems.append(f"{s}: counts sum to {total}, analyzed {sizes[s]}")
                for r in srows:
                    expected = round(100 * r["trial_count"] / sizes[s], 1) if sizes[s] else 0.0
                    if abs(r["percent"] - expected) > 0.05:
                        problems.append(f"{s}/{r['label']}: percent {r['percent']} != {expected}")
            rep.check("arithmetic", not problems, "; ".join(problems[:3]) or
                      f"{len(series)} series x {len(cats)} categories zero-filled; sums and percents agree")
            if any("source_count" in r for r in rows):
                problems = []
                for s in meta["series"]:
                    total = sum(r["source_count"] or 0 for r in rows if r["series"] == s["label"])
                    if total != s["total_available"]:
                        problems.append(f"{s['label']}: source counts sum {total} vs total {s['total_available']}")
                rep.check("source", not problems, "; ".join(problems) or "exact per-category counts sum to each series total")
        else:
            total = sum(r["trial_count"] for r in rows)
            if viz["type"] == "histogram":
                ok = total == analyzed - missing
                detail = f"bins sum {total} = analyzed {analyzed} - missing {missing}"
            elif dim in MULTI_VALUED:
                ok = total >= analyzed - missing
                detail = f"multi-valued '{dim}': sum {total} >= trials with a value {analyzed - missing}"
            else:
                ok = total == analyzed
                detail = f"sum {total} == analyzed {analyzed}"
            rep.check("arithmetic", ok, detail)
            if any("source_count" in r for r in rows):
                total = sum(r["source_count"] or 0 for r in rows)
                rep.check("source", total == meta["total_available"], f"source counts sum {total} vs {meta['total_available']}")
        check_ids(rep, "row", rows, "trial_count")
        check_citations(rep, rows, raw_index)

    elif viz["type"] == "time_series":
        series = sorted({r.get("series", "_") for r in rows})
        problems = []
        for s in series:
            years = [r["start_year"] for r in rows if r.get("series", "_") == s]
            if years != list(range(years[0], years[-1] + 1)):
                problems.append(f"{s}: years not consecutive")
        total = sum(r["trial_count"] for r in rows)
        if total > analyzed - missing:
            problems.append(f"counts {total} exceed dated trials {analyzed - missing}")
        rep.check("arithmetic", not problems, "; ".join(problems) or
                  f"{len(rows)} consecutive year rows; {total} dated trials counted of {analyzed} analyzed")
        check_ids(rep, "year", rows, "trial_count")
        check_citations(rep, rows, raw_index)

    elif viz["type"] == "scatter_plot":
        n = meta["statistics"]["x"]["n"]
        ids = [r["nct_id"] for r in rows]
        ok = len(rows) <= n == analyzed - missing and len(set(ids)) == len(ids)
        rep.check("arithmetic", ok, f"{len(rows)} points drawn, statistics on {n} = analyzed {analyzed} - missing {missing}")
        rep.check("ids", all(NCT.match(i) for i in ids) and all(c["nct_id"] == r["nct_id"] for r in rows for c in r["citations"]),
                  "every point cites its own trial")
        check_citations(rep, rows, raw_index)
    return rep


def main(argv: list[str]) -> int:
    raw_index = load_raw_index()
    print(f"raw records available for citation checks: {len(raw_index):,}\n")
    reports = [verify(p, raw_index) for p in sorted(Path("examples").glob("0*.json"))]
    failed = 0
    for rep in reports:
        print(rep.name)
        for label, ok, detail in rep.results:
            print(f"  {'PASS' if ok else 'FAIL'}  {label:10} {detail}")
        failed += not rep.passed
    print(f"\n{len(reports) - failed}/{len(reports)} examples pass every check")

    if "--markdown" in argv:
        print("\n| Example | " + " | ".join(l for l, _, _ in reports[0].results) + " |")
        print("|---|" + "---|" * len(reports[0].results))
        for rep in reports:
            cells = {l: ("pass" if ok else "FAIL") for l, ok, _ in rep.results}
            print(f"| {rep.name} | " + " | ".join(cells.get(l, "n/a") for l, _, _ in reports[0].results) + " |")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
