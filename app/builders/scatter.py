"""Scatter plot builder: PointSet -> VisualizationSpec. Presentation only."""
from app.aggregate.measures import label, unit
from app.builders.bar import label as dimension_label
from app.schemas.response import Channel, VisualizationSpec
from app.schemas.shapes import PointSet

# Heavy-tailed measures read better on a log axis.
LOG_MEASURES = {"enrollment_count", "site_count"}


def _scale(measure: str, values: list[float]) -> str:
    if measure not in LOG_MEASURES:
        return "linear"
    return "log" if all(v > 0 for v in values) else "symlog"


def build_scatter(pointset: PointSet, x: str, y: str, color: str | None, title: str) -> VisualizationSpec:
    encoding = {
        "x": Channel(field="x", type="quantitative", title=label(x), unit=unit(x),
                     scale=_scale(x, [p.x for p in pointset.points])),
        "y": Channel(field="y", type="quantitative", title=label(y), unit=unit(y),
                     scale=_scale(y, [p.y for p in pointset.points])),
        "detail": Channel(field="nct_id", type="nominal", title="Trial"),
    }
    if color:
        encoding["color"] = Channel(field="color", type="nominal", title=dimension_label(color))
    return VisualizationSpec(
        type="scatter_plot",
        title=title,
        encoding=encoding,
        data=[p.model_dump() for p in pointset.points],
    )
