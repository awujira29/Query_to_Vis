"""Time series builder: Table -> VisualizationSpec. Presentation only.

One series draws one line. Several series (a comparison over time) share the x axis:
every row carries a "series" value and the spec adds a color channel, so the frontend
draws one line per series from the same rows.
"""
from app.schemas.response import Channel, VisualizationSpec
from app.schemas.shapes import Table


def build_time_series(table: Table, title: str) -> VisualizationSpec:
    encoding = {
        "x": Channel(field="start_year", type="temporal", title="Start year"),
        "y": Channel(field="trial_count", type="quantitative",
                     title="Number of trials", unit="trials"),
    }
    if any("series" in row for row in table.rows):
        series = list(dict.fromkeys(row["series"] for row in table.rows))
        encoding["color"] = Channel(field="series", type="nominal", title="Series", sort=series)
    return VisualizationSpec(type="time_series", title=title, encoding=encoding, data=table.rows)
