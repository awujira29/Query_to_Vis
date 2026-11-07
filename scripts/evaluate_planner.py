"""Score the planners on the gold set of questions with expected plans.

  python -m scripts.evaluate_planner                 # rules planner (no key needed, no network needed)
  python -m scripts.evaluate_planner --planner llm   # LLM planner (needs ANTHROPIC_API_KEY or OPENAI_API_KEY)
  python -m scripts.evaluate_planner --planner both  # both, side by side

Gold: tests/fixtures/planner_gold.json. A case passes when the plan has the expected intent,
dimension / network / measure(s), the same number of series, and every expected filter value
(case-insensitive). Off-topic cases pass when the planner returns no plan. Entity typing by
registry (drug or condition?) is stubbed offline unless --live is passed, so an unknown word
counts as a condition.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

from app import llm
from app.planner import llm_planner as llm_mod, rules as rules_mod
from app.client import ApiError
from app.schemas.plan import Plan
from app.schemas.request import QueryRequest

GOLD = "tests/fixtures/planner_gold.json"


def _norm(v):
    if isinstance(v, str):
        return v.lower()
    if isinstance(v, list):
        return sorted(_norm(x) for x in v)
    return v


def matches(case: dict, plan: Plan | None) -> tuple[bool, str]:
    if case["intent"] is None:
        return (plan is None, "" if plan is None else f"planned {plan.intent} for an off-topic question")
    if plan is None:
        return False, "no plan"
    if plan.intent != case["intent"]:
        return False, f"intent {plan.intent} != {case['intent']}"
    if case.get("dimension") and plan.dimension != case["dimension"]:
        return False, f"dimension {plan.dimension} != {case['dimension']}"
    if case.get("network") and sorted(plan.network or ()) != sorted(case["network"]):
        return False, f"network {plan.network} != {case['network']}"
    if case.get("measure") and plan.measure != case["measure"]:
        return False, f"measure {plan.measure} != {case['measure']}"
    if case.get("measures") and sorted([plan.x, plan.y]) != sorted(case["measures"]):
        return False, f"measures {[plan.x, plan.y]} != {case['measures']}"
    if len(plan.series) != len(case["series"]):
        return False, f"{len(plan.series)} series, expected {len(case['series'])}"
    for expected, got in zip(case["series"], plan.series):
        actual = got.filters.model_dump(mode="json", exclude_defaults=True)
        for key, value in expected.items():
            if _norm(actual.get(key)) != _norm(value):
                return False, f"series {got.label!r}: {key}={actual.get(key)!r}, expected {value!r}"
    return True, ""


def run(planner: str, cases: list[dict]) -> list[dict]:
    rows = []
    for case in cases:
        request = QueryRequest(query=case["query"], **case.get("fields", {}))
        if planner == "rules":
            plan = rules_mod.rules_plan(request)
            detail = ""
        else:
            result = llm_mod.llm_plan(request)
            plan, detail = result.plan, (result.error or "")
        ok, why = matches(case, plan)
        rows.append({**case, "planner": planner, "ok": ok, "why": why or detail,
                     "plan": plan.model_dump(mode="json") if plan else None})
        mark = "OK  " if ok else "MISS"
        print(f"[{mark}] {planner:5} {case['set']:10} {case['query'][:70]:70} {why or detail}"[:160])
    return rows


def main(argv: list[str]) -> int:
    planner = next((a.split("=", 1)[1] for a in argv if a.startswith("--planner=")), "rules")
    if "--live" not in argv:
        def unavailable(params):
            raise ApiError("offline evaluation: registry typing disabled")
        rules_mod.count_studies = unavailable
    planners = ["rules", "llm"] if planner == "both" else [planner]
    if "llm" in planners and not llm.configured():
        print("No LLM key set (ANTHROPIC_API_KEY or OPENAI_API_KEY): evaluating the rules planner only.\n")
        planners = ["rules"]

    cases = json.load(open(GOLD))
    all_rows = []
    for p in planners:
        all_rows += run(p, cases)
        print()

    print("======== SUMMARY ========")
    for p in planners:
        rows = [r for r in all_rows if r["planner"] == p]
        by_set = Counter(r["set"] for r in rows if r["ok"])
        total_by_set = Counter(r["set"] for r in rows)
        parts = ", ".join(f"{s} {by_set[s]}/{total_by_set[s]}" for s in total_by_set)
        print(f"  {p:5}: {sum(r['ok'] for r in rows)}/{len(rows)} ok  ({parts})")
    out = f"scripts/eval_planner_{'_'.join(planners)}.json"
    json.dump(all_rows, open(out, "w"), indent=1)
    print(f"  details saved to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
