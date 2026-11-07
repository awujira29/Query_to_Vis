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


def row_label(dimension: str, row: dict) -> str:
    """Readable name for a row: display label for codes; for lead sponsors, the sponsor's
    own name as cited (the group key is a normalized form such as 'novartis pharmaceuticals')."""
    if dimension == "lead_sponsor" and row.get("citations"):
        return row["citations"][0]["value"]
    return display(dimension, row[dimension])


Y_CHANNELS = {
    "trial_count": ("Number of trials", "trials"),
    "source_count": ("Number of trials (exact, at source)", "trials"),
}


def build_bar(table: Table, dimension: str, title: str,
              measure: str = "trial_count") -> VisualizationSpec:
    dim_col = next(c for c in table.columns if c.name == dimension)
    rows = [{**row, "label": row_label(dimension, row)} for row in table.rows]
    y_title, y_unit = Y_CHANNELS[measure]
    return VisualizationSpec(
        type="bar_chart",
        title=title,
        encoding={
            # "label" is the readable name; the raw code stays in rows[dimension].
            "x": Channel(field="label", type=dim_col.dtype, title=label(dimension),
                         sort=[row["label"] for row in rows]),
            "y": Channel(field=measure, type="quantitative", title=y_title, unit=y_unit),
        },
        data=rows,
    )
