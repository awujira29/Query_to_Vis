# Scripts

Run everything from the project root with `python -m scripts.<name>`.

## Produce and check the deliverables

| Script | What it does |
|---|---|
| `run_examples` | Sends the example requests through the HTTP app and saves request + response to `examples/`. `--offline` replays cached API responses. |
| `verify_examples` | Checks every example: schema, encoding fields, count arithmetic, NCT ID lists, graph consistency, and every citation against the raw ClinicalTrials.gov record in the cache. `--markdown` prints a summary table. |
| `summarize_network` | Prints a network example as readable text (largest nodes, strongest edges with their cited arms) to sanity-check that the graph means something. |
| `compare_examples` | Compares current outputs with `examples/before_resolver/` (text search era) to show what the MeSH resolver changed. |

## Evaluate components against live data

| Script | What it does |
|---|---|
| `evaluate_planner` | Scores the rule-based and/or LLM planner on `tests/fixtures/planner_gold.json` (38 questions with expected plans). Results: `eval_planner_rules.json`. |
| `evaluate_resolver` | Runs the name resolver on the 87-query gold set (`tests/fixtures/resolution_gold.json`, with a 25-query holdout) or the 48-query fresh set, and reports correct / fallback / missed / broadened. Results: `eval_resolution_gold.json`, `eval_resolution_fresh.json`, `eval_resolver.json`. |
| `verify_decisions` | Re-checks three design decisions against live data: resolved drug query vs the old strict post-filter (D1), resolved condition query vs the word filter (D2), exact per-category counts vs fetched records (D3). |
| `cross_check_filter`, `check_exclusions` | Inspect what the strict drug filter keeps and drops, trial by trial, against the API's own field search. Used while building `app/drug_names.py`; every regression test there came from a record these scripts surfaced. |

## Probes (how the API behaves)

`probe_api`, `probe_broad`, `probe_v2`, `probe_mesh`, `probe_nlm`, `probe_candidates`, `probe_resolve`
record what the ClinicalTrials.gov v2 API and RxNorm actually return: page size limits, error
messages, field names, how MeSH terms and ancestors are attached to records, how often a text
search misses a MeSH-tagged trial. Their outputs (`probe_output.txt`, `probe_broad.json`,
`probe_v2.json`, `metadata_tree.txt`, from `save_metadata`) are the evidence behind the
constants in `app/client.py` and `app/resolve.py`.
