"""Grouped bar chart builder: comparison Table -> VisualizationSpec."""
from typing import Literal

from app.builders.bar import label, row_label
from app.schemas.response import Channel, VisualizationSpec
from app.schemas.shapes import Table

Measure = Literal["trial_count", "percent", "source_count", "source_percent"]

Y_CHANNELS = {
    "trial_count": ("Number of trials", "trials"),
    "percent": ("Share of analyzed trials", "%"),
    "source_count": ("Number of trials (exact, at source)", "trials"),
    "source_percent": ("Share of trials (exact, at source)", "%"),
}


def build_grouped_bar(table: Table, dimension: str, title: str,
                      measure: Measure = "trial_count") -> VisualizationSpec:
    dim_col = next(c for c in table.columns if c.name == dimension)
    rows = [{**row, "label": row_label(dimension, row)} for row in table.rows]
    categories = list(dict.fromkeys(row["label"] for row in rows))
    series = list(dict.fromkeys(row["series"] for row in rows))
    y_title, y_unit = Y_CHANNELS[measure]
    return VisualizationSpec(
        type="grouped_bar_chart",
        title=title,
        encoding={
            "x": Channel(field="label", type=dim_col.dtype, title=label(dimension), sort=categories),
            "y": Channel(field=measure, type="quantitative", title=y_title, unit=y_unit),
            "color": Channel(field="series", type="nominal", title="Series", sort=series),
        },
        data=rows,
    )
