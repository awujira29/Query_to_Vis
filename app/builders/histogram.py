"""Histogram builder: binned Table -> VisualizationSpec. Presentation only."""
from app.aggregate.measures import label, unit
from app.schemas.response import Channel, VisualizationSpec
from app.schemas.shapes import Table


def build_histogram(table: Table, measure: str, title: str) -> VisualizationSpec:
    return VisualizationSpec(
        type="histogram",
        title=title,
        encoding={
            # Bins are pre-computed: draw one bar per row, in row order.
            "x": Channel(field="bin", type="ordinal", title=label(measure), unit=unit(measure),
                         sort=[row["bin"] for row in table.rows]),
            "y": Channel(field="trial_count", type="quantitative",
                         title="Number of trials", unit="trials"),
        },
        data=table.rows,
    )
