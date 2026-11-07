# Example runs

Each file holds the exact request sent to `POST /query`, the HTTP status, and the full response. Regenerate with `python -m scripts.run_examples` (live) or `python -m scripts.run_examples --offline` (replay from `.cache/ctgov`), then check them with `python -m scripts.verify_examples`.

Generated with planner mode: `rules only (no LLM key)`, offline replay: `False`.

| File | Query | Visualization | Trials analyzed | Size | Planner |
|---|---|---|---|---|---|
| `01_phase_distribution` | How are melanoma trials testing nivolumab distributed across phases? | `bar_chart` | 329 of 329 | 9 data points | rules |
| `02_time_series` | How has the number of pembrolizumab trials changed over time? | `time_series` | 2,659 of 2,659 | 19 data points | rules |
| `03_compare_drugs` | Compare phases for trials involving pembrolizumab vs nivolumab. | `grouped_bar_chart` | 4,426 of 4,426 | 18 data points | rules |
| `04_compare_conditions` | Compare sponsor categories across breast cancer and lung cancer trials. | `grouped_bar_chart` | 10,000 of 28,995 | 16 data points | rules |
| `05_countries` | Which countries have the most melanoma trials? | `bar_chart` | 3,702 of 3,702 | 17 data points | rules |
| `06_network_sponsors` | Show a network of sponsors and drugs for melanoma trials. | `network_graph` | 3,702 of 3,702 | 28 nodes / 37 edges | rules |
| `07_network_drugs` | Which drugs are most often combined with pembrolizumab? | `network_graph` | 2,659 of 2,659 | 24 nodes / 59 edges | rules |
| `08_histogram_enrollment` | What is the typical enrollment of melanoma trials? | `histogram` | 3,702 of 3,702 | 12 data points | rules |
| `09_scatter` | Is there a relationship between enrollment and duration for melanoma trials? | `scatter_plot` | 3,702 of 3,702 | 1774 data points | rules |
