"""points: TrialRecords -> PointSet, one point per trial, for scatter plots.

Each point carries its NCT ID and citations for both coordinates (the exact fields the
x and y values came from). Large result sets are thinned deterministically (every k-th
trial in API order) so a frontend never receives tens of thousands of points; the
summary statistics in the notes are computed on all trials, before thinning.
"""
from __future__ import annotations

from app.aggregate.measures import citations, label, summary, unit, value
from app.dimensions import DIMENSIONS
from app.labels import display
from app.schemas.records import TrialRecord
from app.schemas.shapes import Column, Point, PointSet

MAX_POINTS = 2000


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if not sxx or not syy:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return round(sxy / (sxx * syy) ** 0.5, 3)


def points(records: list[TrialRecord], x: str, y: str, color: str | None = None,
           max_points: int = MAX_POINTS) -> tuple[PointSet, dict]:
    """(PointSet, statistics): the points to draw and the numbers to print beside them."""
    notes: list[str] = []
    usable = []
    for r in records:
        vx, vy = value(r, x), value(r, y)
        if vx is None or vy is None:
            continue
        usable.append((vx, vy, r))
    missing = len(records) - len(usable)
    if missing:
        notes.append(f"{missing} trials lack {label(x).lower()} or {label(y).lower()} "
                     "and are not plotted.")

    xs, ys = [float(v[0]) for v in usable], [float(v[1]) for v in usable]
    stats = {"x": summary([v[0] for v in usable]), "y": summary([v[1] for v in usable]),
             "pearson_r": _pearson(xs, ys)}

    shown = usable
    if len(usable) > max_points:
        step = -(-len(usable) // max_points)            # ceiling division
        shown = usable[::step]
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(step if step < 20 else step % 10, "th")
        notes.append(f"{len(shown)} of {len(usable)} trials are plotted (every {step}{suffix} in "
                     "API order) to keep the response small; statistics use all trials.")

    extract = DIMENSIONS[color] if color else None
    pts = []
    for vx, vy, r in shown:
        keys = extract(r) if extract else []
        pts.append(Point(x=vx, y=vy, color=display(color, keys[0]) if keys else None, nct_id=r.nct_id,
                         citations=citations(r, x) + citations(r, y)))

    if stats["pearson_r"] is not None:
        notes.append(f"Pearson correlation between {label(x).lower()} and {label(y).lower()}: "
                     f"{stats['pearson_r']} (n={stats['x']['n']}).")
    notes.append("Each point is one trial; hover data should show its NCT ID and the two "
                 "cited fields.")
    return PointSet(
        x=Column(name=x, dtype="quantitative", unit=unit(x)),
        y=Column(name=y, dtype="quantitative", unit=unit(y)),
        color=Column(name="color", dtype="nominal") if color else None,
        points=pts, total_records=len(records), missing_count=missing, notes=notes,
    ), stats
