"""Time series builder: Table -> VisualizationSpec. Presentation only."""
from app.schemas.response import Channel, VisualizationSpec
from app.schemas.shapes import Table


def build_time_series(table: Table, title: str) -> VisualizationSpec:
    return VisualizationSpec(
        type="time_series",
        title=title,
        encoding={
            "x": Channel(field="start_year", type="temporal", title="Start year"),
            "y": Channel(field="trial_count", type="quantitative",
                         title="Number of trials", unit="trials"),
        },
        data=table.rows,
    )