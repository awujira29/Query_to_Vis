# ClinicalTrials.gov Query-to-Visualization Agent (Backend)

A backend service that turns a natural-language question about clinical trials into a
structured visualization specification, backed by live ClinicalTrials.gov data, with every
value traceable to the trial records behind it.

```
POST /query  {"query": "Compare phases for trials involving pembrolizumab vs nivolumab."}

->  {"status": "ok",
     "visualization": {"type": "grouped_bar_chart", "title": "...", "encoding": {...}, "data": [...]},
     "meta": {"planner": "llm" | "rules", "plan": {...}, "resolutions": [...], "notes": [...], ...}}
```

Six visualization types (bar, grouped bar, time series, histogram, scatter, network graph),
one pipeline, and a response contract a frontend can render without guessing. Nine example
runs are in [`examples/`](examples/README.md); each one passes an automated check that
re-derives its counts and verifies all of its citations against the raw API records.

---

## Contents

1. [How to run](#1-how-to-run)
2. [How it works](#2-how-it-works)
3. [Request schema](#3-request-schema)
4. [Response schema](#4-response-schema)
5. [Design decisions and tradeoffs](#5-design-decisions-and-tradeoffs)
6. [How correctness was validated](#6-how-correctness-was-validated)
7. [Limitations and what I would improve](#7-limitations-and-what-i-would-improve)
8. [AI tools, and what was designed vs generated](#8-ai-tools-and-what-was-designed-vs-generated)
9. [Project layout](#9-project-layout)

---

## 1. How to run

Requires Python 3.11+ (developed on 3.13). No database, no build step.

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Optional: a language-model key. Without one the service still works, using the
# rule-based planner and the registry-grounded name resolver (see section 2).
export ANTHROPIC_API_KEY=sk-...        # or OPENAI_API_KEY=sk-...
                                        # models: ANTHROPIC_MODEL (default claude-haiku-5-5),
                                        #         OPENAI_MODEL (default gpt-4o-mini)

uvicorn app.main:app --reload           # http://127.0.0.1:8000/docs for interactive docs
```

Try it:

```bash
curl -s localhost:8000/query -H 'content-type: application/json' \
  -d '{"query": "How has the number of trials for this drug changed over time?", "drug_name": "Pembrolizumab"}'

# Only interpret the question (no data fetched), to see how it will be read:
curl -s localhost:8000/plan -H 'content-type: application/json' \
  -d '{"query": "Which countries have the most recruiting trials for lung cancer?"}'
```

Endpoints: `POST /query` (the whole pipeline), `POST /plan` (interpretation only),
`GET /health`, `GET /schema` (JSON Schema of request and response), `GET /docs`.

Other commands:

```bash
pytest -q                                   # 225 tests, all offline, under a second
python -m scripts.run_examples              # regenerate examples/ against the live API
python -m scripts.run_examples --offline    # ...or replay the cached API responses shipped in .cache/
python -m scripts.verify_examples           # check every example (counts, IDs, graph, citations)
python -m scripts.evaluate_resolver         # name resolver vs the 87-query gold set (live API)
python -m scripts.evaluate_planner          # planners vs the 38-question gold set (--planner=llm|both)
```

Environment variables (all optional, also listed in `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | unset | Enables the LLM planner and the LLM MeSH proposer. |
| `ANTHROPIC_MODEL` / `OPENAI_MODEL` | see above | Model name. |
| `CTGOV_CACHE_DIR` | `.cache/ctgov` | Where API responses are cached. |
| `CTGOV_CACHE_TTL` | `86400` | Seconds a cached response stays valid. |
| `CTGOV_OFFLINE` | `0` | `1` serves only cached responses (reproducible replays, no network). |
| `LOG_LEVEL` | `INFO` | Logging level. |

The first live request for a big query takes a while: the client fetches 1,000 records per
page with a 1.5 s pause between requests to respect the API's rate limit, so 5,000 trials
take about 10 s. Responses are cached on disk for a day, so repeated questions are fast.
The `.cache/` folder in this submission holds the responses used to produce `examples/`;
it is optional and can be deleted (it is also git-ignored).

---

## 2. How it works

One pipeline handles every question class. The language model is used in exactly two
places, both of which only *propose* something that code then *verifies*; no number in
any chart ever comes from a model.

```
 question + structured fields
          |
          v
 +-------------------+   LLM proposes a Plan (closed vocabulary JSON); pydantic validates it;
 |   1. PLAN         |   every named entity must appear in the input; else the rule-based
 |   app/planner     |   planner reads the question. Structured fields override both.
 +-------------------+
          |  Plan: intent, series (filters), dimension / network / measure, title
          v
 +-------------------+   For each series: drug and condition names -> verified MeSH terms
 |   2. RESOLVE      |   (LLM, RxNorm, exact and sample-grounded candidates, all checked
 |   app/resolve     |   against registry evidence) -> one API filter expression.
 +-------------------+
          |  API params
          v
 +-------------------+   Paged, throttled, cached GET /api/v2/studies; one TrialRecord per
 |   3. FETCH        |   study (pydantic), with the exact JSON path of every value kept for
 |   app/client      |   citations. Bad records are skipped and counted, never fatal.
 |   app/normalize   |
 +-------------------+
          |  list[TrialRecord]
          v
 +-------------------+   count_by / compare / time_series / histogram / points / network
 |   4. AGGREGATE    |   -> Table, PointSet or Graph. Every datum keeps its NCT IDs and a
 |   app/aggregate   |   sample of citations (field path + exact value).
 +-------------------+
          |
          v
 +-------------------+   Shape -> VisualizationSpec: type, title, encoding, data.
 |   5. BUILD        |   Presentation only: labels, axis titles, sort order, scales.
 |   app/builders    |
 +-------------------+
          |
          v
 QueryResponse: status, visualization, meta (plan, planner, resolutions, totals, notes)
```

**Intent to visualization.** The planner classifies the question into one of six intents,
and the mapping to a chart is fixed:

| Intent | Example question | Visualization |
|---|---|---|
| `distribution` | "How are melanoma trials distributed across phases?" | `bar_chart` |
| `comparison` | "Compare sponsor categories across breast and lung cancer trials" | `grouped_bar_chart` |
| `trend` | "How many trials started each year for pembrolizumab since 2015?" | `time_series` (one line per series) |
| `network` | "Which drugs are combined with pembrolizumab?", "sponsors and drugs for melanoma" | `network_graph` |
| `histogram` | "What is the typical enrollment of phase 3 Alzheimer's trials?" | `histogram` |
| `scatter` | "Is there a relationship between enrollment and duration for RA trials?" | `scatter_plot` |

Grouping dimensions (`phase`, `overall_status`, `study_type`, `allocation`,
`primary_purpose`, `masking`, `has_results`, `start_year`, `sponsor_class`,
`lead_sponsor`, `country`, `drug`, `intervention_type`, `condition`) are a registry in
`app/dimensions.py`: one function each, mapping a record to its group keys. Adding a
dimension is one line there plus a citation rule. Network node types are `drug`,
`sponsor`, `condition`; a `drug`-`drug` network links drugs given in the **same arm**.

**Two planners.** With an API key, the LLM planner fills in a form whose fields are all
enums (`app/planner/llm_planner.py`): the prompt is built from the same registries the code
runs on, so the model can never be offered a dimension that does not exist. Its output is
validated by the `Plan` schema (`app/schemas/plan.py`) and then *grounded*: any drug,
condition, sponsor or country it names must literally appear in the question or the
structured fields, so it cannot expand "Keytruda" to "pembrolizumab" (that is the
resolver's job, with evidence) or invent an entity. If the model is unavailable, returns an
invalid plan, or names something ungrounded, the rule-based planner
(`app/planner/rules.py`) takes over and the response says so in `meta.notes`. If the model
judges the question off-topic, that judgment is kept and the response is `unsupported`.

**Name resolution.** "lung cancer" is not a MeSH heading; "Lung Neoplasms" is, and a
MeSH-based filter (the heading or any of its descendants) finds trials a text search misses
and skips trials a text search hits by accident. Because about 9% of lung cancer trials
carry no MeSH term at all, the resolved expression also matches the trial's own conditions
list with concept expansion. `app/resolve.py` collects
candidate headings from four sources (LLM, RxNorm for brand names, the words themselves,
and headings actually assigned to a sample of matching trials), then verifies each against
the registry: the term must exist, cover at least half the trials the user's words find,
not be far narrower than the text search, and not have a more specific heading covering
nearly the same trials. The first verified candidate wins; if none does, the resolver falls
back to a field search and says so. Every candidate is respelled the way the registry
spells it, so the query string is deterministic whatever the model's capitalization.

---

## 3. Request schema

`POST /query` and `POST /plan` take a JSON object. Only `query` is required.

| Field | Type | Required | Validation | Meaning |
|---|---|---|---|---|
| `query` | string | yes | 3 to 500 characters | The question, in natural language. |
| `drug_name` | string | no | up to 120 chars, blank = unset | Drug or intervention the question is about. Any spelling: brand, generic, research code. |
| `condition` | string | no | up to 120 chars | Disease or condition. |
| `sponsor` | string | no | up to 160 chars | Organization name, matched as lead sponsor or collaborator. |
| `country` | string | no | up to 80 chars | Country with trial sites, as ClinicalTrials.gov spells it (`"United States"`). |
| `trial_phase` | list of strings | no | each parsed leniently: `"Phase 2"`, `"phase2"`, `"2"`, `"II"`, `"PHASE2"`, `"Early Phase 1"`, `"N/A"` | Keep trials in any of these phases. |
| `status` | list of strings | no | each one of `RECRUITING`, `NOT_YET_RECRUITING`, `ACTIVE_NOT_RECRUITING`, `COMPLETED`, `TERMINATED`, `WITHDRAWN`, `SUSPENDED`, `ENROLLING_BY_INVITATION`, `UNKNOWN` (case and spaces tolerated) | Overall status filter. |
| `start_year` | integer | no | 1900 to 2100 | Trials starting in or after this year. |
| `end_year` | integer | no | 1900 to 2100, not before `start_year` | Trials starting in or before this year. |
| `visualization_type` | string | no | one of the six types in section 4 | Force a chart type; the planner's reading is overridden and a note explains. |
| `dimension` | string | no | one of the dimensions in section 2 | Force the grouping dimension (bar and grouped bar charts). |
| `top_n` | integer | no | 1 to 100 | Keep the N largest groups (others fold into "Other"), or N nodes per type in a network. |
| `max_studies` | integer | no | 100 to 10000, default 3000 | Most trials fetched per series. More is slower; see `meta.truncated`. |

Structured fields are authoritative. When a field is set and the planner read a
different value from the text, the structured value wins and `meta.notes` records the
override. In a comparison, a structured field is not applied to the axis being compared
(two series that differ by drug keep their drugs even if `drug_name` is set), and a note
says so.

HTTP codes: `200` for `ok`, `no_results` and `unsupported`; `422` when the request fails
validation (FastAPI's standard error body naming the field); `502` when ClinicalTrials.gov
fails, with the same `QueryResponse` body (`status: "error"`) so clients handle one shape.

Example requests:

```json
{"query": "How has the number of trials for this drug changed over time?", "drug_name": "Pembrolizumab"}
{"query": "Which countries have the most recruiting trials for lung cancer?", "top_n": 15}
{"query": "Compare sponsor categories across breast cancer and lung cancer trials.", "max_studies": 5000}
{"query": "Show trials by phase", "condition": "melanoma", "status": ["recruiting"], "visualization_type": "bar_chart"}
```

---

## 4. Response schema

```
QueryResponse
  status          "ok" | "no_results" | "unsupported" | "error"
  message         null, or a sentence when status is not "ok"
  visualization   null, or VisualizationSpec
  meta            ResponseMeta
```

### 4.1 VisualizationSpec

```
visualization
  type       one of bar_chart, grouped_bar_chart, time_series, histogram, scatter_plot, network_graph
  title      human-readable title
  encoding   {channel: Channel}  which field of each datum feeds which visual channel
  data       list of rows/points, or {"nodes": [...], "edges": [...], "directed": false}

Channel
  field      key to read in each datum
  type       "nominal" | "ordinal" | "temporal" | "quantitative"
  title      axis or legend title (optional)
  unit       e.g. "trials", "participants", "%" (optional)
  sort       explicit category order for ordinal/nominal channels (optional)
  scale      "linear" | "log" | "symlog"   (symlog: log-like but zero is allowed)
```

Every datum carries its evidence:

```
nct_ids            NCT IDs of the trials behind the value (at most 100; see nct_ids_truncated)
nct_ids_truncated  true when more than 100 trials contributed; the count is still exact
citations          up to 5 Citation objects
Citation
  nct_id    "NCT01234567"
  url       "https://clinicaltrials.gov/study/NCT01234567"
  field     exact JSON path in the API record, e.g. "protocolSection.designModule.phases"
  value     exact text at that path, e.g. "PHASE3"
  title     the trial's brief title
```

Per type, the channels a renderer must read and the fields each datum has:

| `type` | `encoding` channels | Each datum in `data` |
|---|---|---|
| `bar_chart` | `x` (field `label`, ordinal or nominal, `sort` gives order), `y` (`trial_count`, or `source_count` when exact counts replaced a truncated sample; see `meta.render_hints.y_measure`) | `<dimension>` raw key, `label` display name, `trial_count`, optional `source_count` and `source_percent`, evidence fields. Rows `"Other"` and `"Not reported"` may appear; they carry no citations. |
| `grouped_bar_chart` | `x` (`label`), `y` (`percent` by default, `trial_count`, or `source_percent` when exact), `color` (`series`, with `sort`) | as bar_chart plus `series` and `percent` (share of that series). Every series has every category, zero-filled, so groups align. |
| `time_series` | `x` (`start_year`, temporal), `y` (`trial_count`), `color` (`series`) only when several lines | `start_year`, `trial_count`, `estimated_count` (starts marked ESTIMATED), `partial_year` (the current year), optional `series`, evidence fields. Years are consecutive; gaps are zeros. |
| `histogram` | `x` (`bin`, ordinal, `sort` gives bin order, `unit` the measure's unit), `y` (`trial_count`) | `bin` label ("20-49", "50,000+"), `bin_start`, `bin_end` (exclusive; `null` for the open last bin), `trial_count`, evidence fields. |
| `scatter_plot` | `x`, `y` (quantitative; `scale` may be log or symlog), `color` (nominal, optional), `detail` (`nct_id`) | `x`, `y`, `color` (display label or null), `nct_id`, `citations` for both coordinates. At most 2,000 points (deterministic thinning, noted); statistics in `meta.statistics` use all trials. |
| `network_graph` | `node_id` (`id`), `node_label` (`label`), `node_color` (`type_label`, with `sort`), `node_size` (`weight`), `edge_source` (`source`), `edge_target` (`target`), `edge_width` (`weight`) | `data.nodes[]`: `id` ("drug:pembrolizumab"), `label`, `type`, `type_label`, `weight` (trials linking it), `degree`, `focus` (the entity the question is about), evidence fields. `data.edges[]`: `source`, `target`, `weight` (distinct trials), evidence fields. `meta.render_hints` gives `layout` ("bipartite" for two node types, "force" for one), `focus_node`, `node_types`. |

### 4.2 ResponseMeta

| Field | Meaning |
|---|---|
| `query` | The question as received. |
| `planner` | `"llm"`, `"rules"` or `"none"`: who interpreted the question. |
| `plan` | The interpretation: `intent`, `visualization_type`, `dimension`, `network`, `measure`, `x`, `y`, `color`, `top_n`, `title`, `interpretation` (one sentence), `series[]` with `label` and `filters`. |
| `filters` | Filters of the single series, or `{"series": {label: filters}}` for several. |
| `series[]` | Per series: `label`, `filters`, `api_queries` (exact URLs called), `total_available` (matches at the source), `analyzed` (records aggregated), `truncated`, `failed_records`, `resolutions`. |
| `source` | `"clinicaltrials.gov"`. |
| `api_queries` | All API URLs behind the answer; paste one in a browser to see the raw data. |
| `total_available`, `analyzed`, `truncated` | Summed over series. `truncated` means `analyzed < total_available` for some series; `notes` explain what was done about it. |
| `missing_count` | Analyzed trials with no value for the dimension or measure (shown as "Not reported" or excluded, as noted). |
| `time_granularity` | `"year"` for time series. |
| `failed_records` | Studies dropped because the record failed validation. |
| `resolutions[]` | How each drug or condition name was read: `kind`, `query`, `status` (`resolved` or `fallback`), `terms` (MeSH), `source` (`llm`, `rxnorm`, `exact`, `sample`), `text_search_total`, `api_params`, `note`. |
| `render_hints` | Presentation hints: `y_measure` for bars, `layout`/`focus_node`/`node_types` for networks. |
| `statistics` | Histogram: `{measure: {n, min, p25, median, p75, max, mean}}`. Scatter: `{x: {...}, y: {...}, pearson_r}`. |
| `notes` | Every assumption, filter, exclusion and caveat, in plain sentences, in pipeline order. A frontend should show these next to the chart. |
| `generated_at` | ISO timestamp. |

### 4.3 A real response, trimmed

From `examples/01_phase_distribution.json` (two of nine rows shown; ID lists shortened):

```json
{
  "status": "ok",
  "visualization": {
    "type": "bar_chart",
    "title": "Melanoma trials with nivolumab by phase",
    "encoding": {
      "x": {"field": "label", "type": "ordinal", "title": "Phase",
            "sort": ["Early Phase 1", "Phase 1", "Phase 1/Phase 2", "Phase 2", "Phase 2/Phase 3",
                     "Phase 3", "Phase 4", "Not Applicable", "No Phase Listed"]},
      "y": {"field": "trial_count", "type": "quantitative", "title": "Number of trials", "unit": "trials"}
    },
    "data": [
      {"phase": "Early Phase 1", "label": "Early Phase 1", "trial_count": 10,
       "nct_ids": ["NCT03543969", "NCT05498792", "NCT05358938", "..."], "nct_ids_truncated": false,
       "citations": [{"nct_id": "NCT03543969", "url": "https://clinicaltrials.gov/study/NCT03543969",
                      "field": "protocolSection.designModule.phases", "value": "EARLY_PHASE1",
                      "title": "Adaptive BRAF-MEK Inhibitor Therapy for Advanced BRAF Mutant Melanoma"}]},
      {"phase": "Phase 1", "label": "Phase 1", "trial_count": 72, "nct_ids": ["NCT06112808", "..."],
       "nct_ids_truncated": false, "citations": [{"nct_id": "NCT06112808", "field": "protocolSection.designModule.phases", "value": "PHASE1", "...": "..."}]}
    ]
  },
  "meta": {
    "query": "How are melanoma trials testing nivolumab distributed across phases?",
    "planner": "rules",
    "plan": {"intent": "distribution", "visualization_type": "bar_chart", "dimension": "phase",
             "title": "Melanoma trials with nivolumab by phase", "interpretation": "Trials grouped by phase.",
             "series": [{"label": "melanoma", "filters": {"drug": "nivolumab", "condition": "melanoma"}}]},
    "filters": {"drug": "nivolumab", "condition": "melanoma"},
    "total_available": 329, "analyzed": 329, "truncated": false, "missing_count": 0, "failed_records": 0,
    "resolutions": [
      {"kind": "condition", "query": "melanoma", "status": "resolved", "terms": ["Melanoma"], "source": "exact", "text_search_total": 3782},
      {"kind": "drug", "query": "nivolumab", "status": "resolved", "terms": ["Nivolumab"], "source": "exact", "text_search_total": 2041}
    ],
    "api_queries": ["https://clinicaltrials.gov/api/v2/studies?filter.advanced=%28AREA%5BConditionMeshTerm%5D%22Melanoma%22+OR+..."],
    "render_hints": {"y_measure": "trial_count"},
    "notes": [
      "'melanoma' resolved to MeSH 'Melanoma' (source: exact; verified: covers 88% of trials found by the words, 3,424 trials in the registry, including subtypes).",
      "'nivolumab' resolved to MeSH 'Nivolumab' (source: exact; verified: covers 83% of trials found by the words, 1,717 trials in the registry, including subtypes).",
      "NCT ID lists are capped at 100 per datum; trial_count is always the full count."
    ]
  }
}
```

A trial with no phase (observational studies have none) is counted under "No Phase Listed"
and its citation points at `designModule.studyType = "OBSERVATIONAL"`: the field that
explains the grouping. The pipeline never invents an excerpt.

---

## 5. Design decisions and tradeoffs

**LLM for language, code for numbers.** The model is asked two narrow questions ("which
form fields does this question fill in?" and "which MeSH headings mean these words?") and
both answers are checked before use: schema validation with closed enums, grounding of
entity names in the input, and registry evidence for MeSH terms. Every count, percentage,
bin and edge weight is computed in plain Python over validated records. Tradeoff: the
planner cannot express a question outside its form (say, "trials whose title mentions
immunotherapy"); such questions get the nearest supported reading, stated in
`meta.plan.interpretation`, rather than an improvised one.

**Verified MeSH resolution instead of text search or a hand-made synonym list.** A
`query.intr=nivolumab` text search matches trials that only mention nivolumab in a
description; a MeSH filter (`InterventionMeshTerm` plus ancestors) matches trials that give
it, and includes subtypes ("Lung Neoplasms" covers non-small-cell lung carcinoma). The cost
is one sampling request and a few count requests per name, cached. The verifier's
thresholds (coverage 50%, size ratio 0.4, "nearly as much" 90%) were tuned on 62 queries
and then checked on a 25-query holdout and a 48-query fresh set; see section 6. When
nothing verifies, the resolver falls back to a field search and says so rather than forcing
a doubtful term.

**Filters are pushed to the API, aggregation happens locally.** Everything that narrows
*which* trials (names, years, phases, status, country, sponsor) becomes one
`filter.advanced` expression, so `total_available` is exact and every chart about "trials
for X" uses the same definition of X. Grouping and counting happen locally over fetched
records because the API cannot group by arbitrary fields and because local records are
what make citations possible.

**Truncation is visible and, where possible, corrected.** Fetching the first 3,000 of
16,000 breast cancer trials and counting them would be a biased sample in API order. The
response marks `truncated`, and for single-valued categorical dimensions (sponsor class,
status, study type, allocation, purpose) the chart switches to exact per-category counts
from the API, using the same filter expression plus `AREA[Field]VALUE`; the sampled counts
stay in the rows for comparison. For dimensions the API cannot count exactly (phase, where
a Phase 1/2 trial matches two values; multi-valued country and drug) the notes say the
counts describe the sample and how to raise `max_studies`. Example 04 shows both numbers
side by side: sample 73.8% vs exact 73.2% for academic breast cancer trials.

**Networks link what co-occurs in the same arm, not the same trial.** A trial comparing
favezelimab/pembrolizumab against regorafenib would otherwise draw an edge between
pembrolizumab and regorafenib, a false combination. Edge weights count distinct trials,
never arms or sites. Drugs of one class listed as alternatives in one arm ("carboplatin or
cisplatin") are not linked, using a small curated list of interchangeable classes, and the
notes report how many trials that affected. Graphs are pruned (top nodes per type, minimum
edge weight scaled to the number of trials, no orphan nodes) and every threshold is in the
notes, because an unpruned graph of 1,281 drugs is unreadable. The question's own entity
is the focus node and is always kept.

**Drug names are normalized by rules, each backed by a real record.** "BMS-936558
(Nivolumab)", "Opdivo-EU (Induction)", "Pembrolizumab 200 mg", "FOLFOX" and "5-FU" all
have to count as the drugs they are. `app/drug_names.py` handles codes, brands, salts,
doses, routes, regimen acronyms, bracketed synonyms and class descriptions; every rule
came from a record that once failed and is pinned by a regression test naming that NCT ID.
Tradeoff: the synonym and regimen tables are curated and oncology-heavy; an unknown brand
name becomes its own node until added (the LLM and RxNorm proposers cover the filter
side of that gap).

**Three intermediate shapes, not six.** Table (grouped rows), PointSet (one point per
trial) and Graph (nodes and edges) cover all six visualizations. Aggregators produce
shapes; builders turn shapes into specs and contain no arithmetic. Adding a visualization
means a builder; adding a dimension means one line in `app/dimensions.py` and a citation
rule; adding a measure means one entry in `app/aggregate/measures.py`.

**Pydantic at every boundary.** Raw API study to `TrialRecord`, model output to `Plan`,
client JSON to `QueryRequest`, and `QueryResponse` out. One bad record never breaks a
query (it is skipped and counted in `failed_records`); one bad field never breaks a record
(unknown phase values are logged and skipped). The published `GET /schema` is generated
from the same models the code runs on.

**A disk cache, with an offline mode.** API responses are cached for a day, keyed by
request parameters, which keeps development iterations fast and the API load low.
`CTGOV_OFFLINE=1` turns the cache into a replay source, which is how the example runs and
their verification are reproducible on a machine with no network.

---

## 6. How correctness was validated

**Unit and integration tests: 225, all offline, under a second** (`pytest -q`).

| Area | What is pinned |
|---|---|
| `test_drug_names` | 60+ real intervention strings and the names they must normalize to, each with the NCT ID it came from. |
| `test_resolve` | The verifier on synthetic registries mirroring real cases: an invented term is rejected, a too-broad parent is rejected, poly-hierarchy ties go to the directly assigned term, broad terms are pruned from multi-term candidates, the sample proposer and registry respelling. |
| `test_network` | Same-arm linking on a real 5-study fixture (favezelimab/pembrolizumab vs regorafenib), weights count trials not arms, placebo never a node, interchangeable classes, pruning and focus. |
| `test_timeseries`, `test_compare`, `test_fixes` | Zero-filled years, future and undated exclusion, partial-year flag; zero-filled comparison cells and percentages; the strict condition filter; exact source counts and display labels. |
| `test_request`, `test_plan` | Lenient input parsing (phases, statuses), strict rejection of unknown values, year order, the Plan's consistency rules (a comparison needs two series, same-type networks only for drugs, scatter axes must differ...). |
| `test_planner_rules` | 30+ phrasings covering every appendix query class: entities extracted, typed and placed in filters, dimension words never leaking into entity names, "A and B" and "A vs B" split, countries mapped to API spelling, structured fields winning. |
| `test_planner_llm` | With a scripted model: valid plans accepted and normalized, unknown dimensions rejected not coerced, invented entities rejected, off-topic judgment respected, outage and invalid output falling back to rules, structured-field fill/override/axis logic, visualization overrides. |
| `test_histogram_points` | Fixed bin edges and open last bin, bin sums, citations for enrollment and for both dates of a duration, point thinning, symlog when zeros are present. |
| `test_pipeline` | Every intent end to end with a controlled study set and a stubbed API: count sums equal analyzed trials, citations name the right field and value (including `studyType` for a phase-less study), truncation switching to exact counts, no_results / unsupported / error paths, multi-series trends. |
| `test_main` | HTTP codes and body shapes: 200, 422 with field errors, 502 with the standard body, `/plan` never fetches. |

**Name resolver evaluation on live data** (`scripts/evaluate_resolver.py`, results in
`scripts/eval_resolution_gold.json` and `eval_resolution_fresh.json`): a gold set of 87
queries (62 used to tune thresholds, 25 held out) and a fresh set of 48 queries written
after tuning. Final run with the LLM proposer: 84/87 on gold, of which 25/25 on the
holdout; 45/48 on the fresh set. The non-passing cases are informative rather than
dangerous: "chronic pain" resolved to "Pain" (no single honest heading exists), "kidney
failure" resolved to the chronic kidney failure headings rather than the general one,
"preterm birth" resolved to two sibling headings where the scorer accepts one (the only
"broadened" outcome, the harmful kind), and "alzhiemers" (misspelled), "vitamin D" and
"ONO-4538" (a research code) resolved to nothing and fell back to a field search rather
than to a wrong term. The earlier 86/87 run (`eval_resolver.json`) predates the last
threshold change.

**Planner evaluation** (`scripts/evaluate_planner.py`, gold set
`tests/fixtures/planner_gold.json`): 38 questions with the expected intent, dimension or
network or measures, series count and filter values, covering the nine appendix questions,
16 paraphrases with different entities, 7 histogram/scatter/country questions, 4 with
structured fields and 2 off-topic questions. The rule-based planner scored 31/38 on first
contact; the 7 misses (a "yearly" cue, node types taken in the wrong order, "which
sponsors study which conditions" not read as a network, a histogram cue, a scatter plot
overridden by the words "start year", and two off-topic questions given a plan) were fixed
and it now scores 38/38, so this set is a regression set for the rules planner. For the
LLM planner it is a fair test: none of its few-shot examples share an entity or phrasing
with the set. Run `python -m scripts.evaluate_planner --planner=both` with a key to compare.

**Design decisions re-verified against the data** (`scripts/verify_decisions.py`, replays
from the cache with `CTGOV_OFFLINE=1`): the resolved drug query and the earlier strict
post-filter agree on 330 of 331 nivolumab melanoma trials (99.7%; the one extra is a
trial listing nivolumab as an optional combination partner); the resolved condition query
and the word-based filter agree on 96.6% of 5,000 lung cancer trials (the resolved query
additionally keeps mesothelioma and pulmonary nodule trials that the registry tagged with
Lung Neoplasms, the word filter additionally keeps 35 trials naming lung cancer only in
free text); and exact per-category counts sum exactly to the fetched record count for the
same expression (3,702 = 3,702 = 3,702). The earlier filters' false exclusions and
inclusions were inspected trial by trial with `scripts/cross_check_filter.py` and
`check_exclusions.py`.

**Example outputs verified independently** (`scripts/verify_examples.py`). For each of
the nine examples: the response validates against the published schema; every field named
in `encoding` exists in every datum; counts sum correctly (single-valued dimensions to the
analyzed total, histogram bins to analyzed minus missing, grouped bars zero-filled with
percent = count / series size, exact source counts to each series total, years
consecutive); `nct_ids` lengths and `nct_ids_truncated` flags are right and cited trials
are among a datum's trials; in graphs every edge joins listed nodes, edge weight never
exceeds a node weight, degrees match, the focus node is present; and **every citation's
`field` path, resolved in the raw ClinicalTrials.gov record, yields exactly the cited
`value`**: 6,434 of 6,434 citations across the nine files.

| Example | schema | encoding | arithmetic | ids | graph | citations |
|---|---|---|---|---|---|---|
| 01 phase distribution | pass | pass | pass | pass | n/a | 43/43 |
| 02 time series | pass | pass | pass | pass | n/a | 77/77 |
| 03 compare drugs | pass | pass | pass | pass | n/a | 90/90 |
| 04 compare conditions (exact counts) | pass | pass | pass | pass | n/a | 64/64 |
| 05 countries | pass | pass | pass | pass | n/a | 75/75 |
| 06 network sponsors and drugs | pass | pass | pass | pass | pass | 288/288 |
| 07 network drug combinations | pass | pass | pass | pass | pass | 415/415 |
| 08 histogram enrollment | pass | pass | pass | pass | n/a | 60/60 |
| 09 scatter enrollment vs duration | pass | pass | pass | pass | n/a | 5322/5322 |

**Reading the outputs.** Numbers that pass arithmetic can still be wrong in meaning, so
each example was read: the pembrolizumab combination network's strongest partners are
carboplatin, cisplatin, paclitaxel, pemetrexed and lenvatinib, which are the regimens of
the KEYNOTE and LEAP programs; the phase comparison shows both PD-1 antibodies with about
44% of trials in Phase 2; the melanoma sponsor network pairs industry sponsors with their
own drugs (Bristol-Myers Squibb with nivolumab and ipilimumab, Merck with pembrolizumab)
and academic centers with many drugs. `scripts/summarize_network.py` prints a graph as text
for this purpose.

---

## 7. Limitations and what I would improve

**Interpretation**

- The rule-based planner extracts entities from phrasing patterns ("trials for X", "X
  trials", "A vs B"). Unusual phrasings can yield a wrong entity or none, in which case
  the response is `unsupported` with a hint to pass a structured field; its off-topic
  gate is a vocabulary check, so "write a poem about chemotherapy" would get a plan. The
  LLM planner handles free phrasing and off-topic detection far better but needs a key.
  The planner gold set now exists; the next step is a held-out portion, as for the resolver.
- Entity typing without an API key (is "psilocybin" a drug or a condition?) falls back to
  vocabulary heuristics; with network access it asks the registry which field finds more
  trials. Clearer: let the LLM planner type entities and verify with the same count check.
- One dimension per chart. "Phase by sponsor class" (two groupings) is not supported
  except as a comparison with one series per sponsor class, which the planner does not
  build on its own.

**Data**

- Sponsors have no controlled vocabulary in the registry; `sponsor` filters match by name,
  so "Merck" and "Merck Sharp & Dohme LLC" are different sponsors. Normalizing legal
  suffixes helps grouping but not the filter. RxNorm-style resolution for organizations
  would need an external list.
- Countries come from site locations, so a trial with sites in 20 countries counts in 20
  groups; the notes say so, and the sum exceeds the trial count by design.
- `duration_months` is completion minus start on month precision and mixes actual with
  estimated dates; `estimated_count` is reported for start years but not yet for durations.
- MeSH terms are what ClinicalTrials.gov derives from sponsor text, and they are inherited:
  about 9% of lung cancer trials carry no MeSH term (hence the extra conditions-list match),
  while some mesothelioma trials carry "Lung Neoplasms" and so count as lung cancer trials.
  Very new compounds have no MeSH heading and are matched by name and other names only.

**Scale**

- Big queries are fetched page by page with a 1.5 s pause: 10,000 records take about
  20 s on a cold cache. A background prefetch or a local snapshot of the registry (the API
  offers bulk download) would make large comparisons instant and allow exact counts for
  every dimension, not only the single-valued ones.
- The cache is a local directory; a multi-instance deployment would want Redis or a shared
  bucket, and the throttle would need to be shared too.
- Scatter plots thin to 2,000 points; a frontend wanting all points for a 50,000-trial
  question would need pagination of the data array.

**Product**

- No frontend. The spec is designed to be rendered by Vega-Lite or ECharts for the chart
  types and by Cytoscape or d3-force for networks (`render_hints.layout` says which).
- No streaming of progress; a long fetch looks like a slow request. The `/plan` endpoint
  gives an instant preview of the interpretation, which a UI can show while data loads.
- Suggested follow-up questions ("compare with nivolumab", "only recruiting trials") would
  be easy to generate from the plan and would make the agent feel conversational.

---

## 8. AI tools, and what was designed vs generated

**Tools used.** Claude (Anthropic) was used throughout as a pair programmer: for drafting
code against the designs described below, for writing regression tests from records I
found, for the LLM-facing prompts, and for drafting this README from the code and the
scripts' output. The optional runtime LLM (Claude Haiku or GPT-4o-mini) is part of the
system itself, used only in the two proposing roles described in section 2.

**Designed and implemented deliberately** (the decisions I would defend in a review):

- The architecture: plan, resolve, fetch, aggregate, build, with pydantic models at every
  boundary and three intermediate shapes; the rule that no number comes from a model.
- The resolver's verification idea (sample the user's own words, check each candidate
  heading's coverage, size ratio and specificity against the registry) and its thresholds,
  tuned on a gold set with a holdout and re-checked on a fresh set.
- Same-arm co-occurrence for drug networks, interchangeable drug classes, trial-level
  weights, and the pruning rules, after reading real trials where trial-level linking
  produced false combinations.
- Exact per-category counts at the source when a fetch is truncated, and the decision to
  show sample and exact numbers side by side.
- The response contract: raw keys next to display labels, evidence on every datum,
  everything uncertain written into `notes`.
- The verification scripts and what they check, including resolving every citation path
  against the raw record.

**Generated and then adapted** (drafted by the model, then read, tested and changed):

- The bulk of the drug-name normalization rules. The initial regular expressions were
  generated; every one was then checked against real records with
  `scripts/cross_check_filter.py`, and several were corrected (salt forms that are the
  drug, like magnesium sulfate; interferon spellings; "SG" not being a second drug).
- The rule-based planner's keyword tables and entity patterns, iterated against the 30+
  test phrasings until each produced the intended plan.
- Test scaffolding and the histogram bin edges.

**How it was validated** is section 6: offline tests for every module, live evaluation of
the resolver on held-out queries, live re-checks of the main design decisions, automated
verification of every example output down to the cited field values, and reading the
charts for clinical sense.

---

## 9. Project layout

```
app/
  main.py                 FastAPI app: /query, /plan, /health, /schema
  pipeline.py             request -> plan -> fetch -> aggregate -> build -> response
  planner/
    __init__.py           chooses LLM or rules; applies structured fields and overrides
    llm_planner.py        constrained-JSON planner with validation and grounding
    rules.py              keyword/pattern planner (fallback and baseline)
  resolve.py              names -> verified MeSH terms -> API filter expressions
  query.py                Filters -> API params (resolver + structured filters)
  client.py               throttled, retrying, paginated, cached API client; offline mode
  normalize.py            raw study -> TrialRecord, keeping JSON paths for citations
  drug_names.py           intervention text -> generic drug names
  filters.py              strict post-filters (kept for cross-checks and tests)
  dimensions.py           dimension registry: name -> record -> group keys
  labels.py               display labels for API codes
  aggregate/              count_by, compare, time_series, histogram, points, network,
                          source_counts (exact API counts), citations, measures
  builders/               bar, grouped_bar, time_series, histogram, scatter, network
  schemas/                request, plan, records, shapes, response (the contract)
tests/                    225 tests; fixtures include a real 5-study sample and the
                          resolver gold, fresh and lockbox query sets
scripts/                  run_examples, verify_examples, evaluate_planner, evaluate_resolver,
                          verify_decisions, API probes (see scripts/README.md)
examples/                 nine request/response pairs and an index (examples/README.md);
                          before_resolver/ keeps three outputs from the text-search era
.cache/                   optional: API and LLM responses used to produce the examples
```
