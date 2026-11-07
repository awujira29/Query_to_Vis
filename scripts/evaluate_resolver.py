"""Evaluate the resolver end to end on the gold set (87 queries, incl. a 25-query holdout).

  python -m scripts.evaluate_resolver            # LLM + RxNorm + exact (LLM if a key is set)
  python -m scripts.evaluate_resolver --no-llm   # RxNorm + exact only, to measure the LLM's value
  python -m scripts.evaluate_resolver --gold=tests/fixtures/resolution_fresh.json
                                                 # a fresh set never used to tune anything

Outcomes per query:
  correct      every resolved MeSH term is one of the acceptable answers
  fallback-ok  expected no honest MeSH term, and the resolver fell back
  missed       expected a term, but nothing verified (safe: falls back to a field search)
  broadened    includes an acceptable term, plus extra terms that widen the query (harmful)
  differs      resolved to different terms: review by hand, may be fine
Results are saved to scripts/eval_<gold file name>.
"""
import json
import logging
import sys
from collections import Counter

from app import llm
from app.resolve import ExactProposer, LLMProposer, RxNormProposer, resolve

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

DEFAULT_GOLD = "tests/fixtures/resolution_gold.json"


def outcome(case, res):
    expected = case["expected"]
    if expected == "fallback":
        return "fallback-ok" if res.status == "fallback" else "differs"
    if res.status == "fallback":
        return "missed"
    got = {t.lower() for t in res.terms}
    ok = {t.lower() for t in expected}
    if got <= ok:
        return "correct"
    return "broadened" if got & ok else "differs"


def main(use_llm: bool, gold: str):
    if use_llm and not llm.configured():
        print("No LLM key set (ANTHROPIC_API_KEY or OPENAI_API_KEY): running without the LLM.\n")
        use_llm = False
    proposers = ([LLMProposer()] if use_llm else []) + [RxNormProposer(), ExactProposer()]

    cases = json.load(open(gold))
    rows = []
    for case in cases:
        res = resolve(case["kind"], case["query"], proposers)
        result = outcome(case, res)
        rows.append({**case, "outcome": result, "status": res.status, "terms": res.terms,
                     "source": res.source, "tried": [c.model_dump() for c in res.candidates]})
        mark = {"correct": "OK  ", "fallback-ok": "OK  ", "missed": "MISS",
                "broadened": "WIDE", "differs": "DIFF"}[result]
        print(f"[{mark}] {case.get('tag', case['set']):10} {case['kind']:9} {case['query']:22} -> "
              f"{res.status:8} {', '.join(res.terms) or '-':42} ({res.source or '-'})")

    out = "scripts/eval_" + gold.rsplit("/", 1)[-1]
    json.dump(rows, open(out, "w"), indent=1)
    print(f"\n======== SUMMARY ({'with' if use_llm else 'without'} LLM) ========")
    for set_name in sorted({r["set"] for r in rows}):
        for kind in ("condition", "drug"):
            rs = [r for r in rows if r["set"] == set_name and r["kind"] == kind]
            if not rs:
                continue
            c = Counter(r["outcome"] for r in rs)
            ok = c["correct"] + c["fallback-ok"]
            print(f"  {set_name:8} {kind:9}: {ok}/{len(rs)} ok  "
                  f"(missed {c['missed']}, broadened {c['broadened']}, differs {c['differs']})")
    tags = sorted({r["tag"] for r in rows if "tag" in r})
    for tag in tags:
        rs = [r for r in rows if r.get("tag") == tag]
        c = Counter(r["outcome"] for r in rs)
        print(f"  tag {tag:12}: {c['correct'] + c['fallback-ok']}/{len(rs)} ok  "
              f"(missed {c['missed']}, broadened {c['broadened']}, differs {c['differs']})")
    print("  winning sources:", dict(Counter(r["source"] or "fallback" for r in rows)))
    print(f"  details saved to {out}")
    print("\nWIDE lines are real errors: the query would include unrelated trials.")
    print("Review every DIFF line: a different verified term can still be reasonable.")


if __name__ == "__main__":
    gold = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--gold=")), DEFAULT_GOLD)
    main(use_llm="--no-llm" not in sys.argv, gold=gold)
