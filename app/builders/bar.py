"""Bar chart builder: Table -> VisualizationSpec. Presentation only, no computation."""
from app.labels import display
from app.schemas.response import Channel, VisualizationSpec
from app.schemas.shapes import Table

LABELS = {
    "phase": "Phase", "overall_status": "Status", "study_type": "Study type",
    "allocation": "Allocation", "primary_purpose": "Primary purpose", "masking": "Masking",
    "has_results": "Results posted", "start_year": "Start year", "sponsor_class": "Sponsor class",
    "lead_sponsor": "Lead sponsor", "country": "Country", "drug": "Drug",
    "intervention_type": "Intervention type", "condition": "Condition",
}


def label(dimension: str) -> str:
    return LABELS.get(dimension, dimension.replace("_", " ").capitalize())


def build_bar(table: Table, dimension: str, title: str) -> VisualizationSpec:
    dim_col = next(c for c in table.columns if c.name == dimension)
    rows = [{**row, "label": display(dimension, row[dimension])} for row in table.rows]
    return VisualizationSpec(
        type="bar_chart",
        title=title,
        encoding={
            # "label" is the readable name; the raw code stays in rows[dimension].
            "x": Channel(field="label", type=dim_col.dtype, title=label(dimension),
                         sort=[row["label"] for row in rows]),
            "y": Channel(field="trial_count", type="quantitative",
                         title="Number of trials", unit="trials"),
        },
        data=rows,
    )
