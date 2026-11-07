"""time_series: trials per start year, with gaps filled and time edge cases explained."""
from datetime import date

from app.aggregate.count import count_by
from app.schemas.records import DateType, TrialRecord
from app.schemas.shapes import Column, Table


def _starts_after(record: TrialRecord, today: date) -> bool:
    """True if the start date is later than today. Partial dates use their
    earliest possible day, so "2026-11" counts as started in November 2026."""
    d = record.start
    return (d.year, d.month or 1, d.day or 1) > (today.year, today.month, today.day)


def time_series(records: list[TrialRecord], start_year: int | None = None,
                end_year: int | None = None, include_future: bool = False,
                today: date | None = None) -> Table:
    """Count trials per start year.

    - Years with no trials appear as 0, so a line never silently skips a year.
    - Future start dates are planned, not real; they are excluded by default,
      since a bucket of planned trials looks like a decline or a spike.
    - The current year is flagged as partial.
    - Each row reports how many of its start dates are ESTIMATED.
    """
    today = today or date.today()
    notes: list[str] = []

    dated = [r for r in records if r.start_year is not None]
    undated = len(records) - len(dated)
    if undated:
        notes.append(f"{undated} trials have no start date and are not shown.")

    if not include_future:
        future = [r for r in dated if _starts_after(r, today)]
        if future:
            notes.append(f"{len(future)} trials with planned start dates after "
                         f"{today.isoformat()} are excluded.")
        dated = [r for r in dated if not _starts_after(r, today)]

    lo = start_year if start_year is not None else min((r.start_year for r in dated), default=None)
    hi = end_year if end_year is not None else max((r.start_year for r in dated), default=None)
    in_range = [r for r in dated if lo is not None and lo <= r.start_year <= hi]

    counted = count_by(in_range, "start_year", include_missing=False)
    by_year = {int(row["start_year"]): row for row in counted.rows}
    estimated = {}
    for r in in_range:
        if r.start.date_type == DateType.ESTIMATED:
            estimated[r.start_year] = estimated.get(r.start_year, 0) + 1

    rows = []
    if lo is not None:
        for year in range(lo, hi + 1):
            row = by_year.get(year, {"trial_count": 0, "nct_ids": [],
                                     "nct_ids_truncated": False, "citations": []})
            row = {"start_year": year, **{k: v for k, v in row.items() if k != "start_year"},
                   "estimated_count": estimated.get(year, 0),
                   "partial_year": year == today.year}
            rows.append(row)

    if any(row["partial_year"] for row in rows):
        notes.append(f"{today.year} is still in progress, so its count is incomplete.")
    if any(row["estimated_count"] for row in rows):
        notes.append("estimated_count is the number of trials per year whose start "
                     "date is ESTIMATED rather than ACTUAL.")
    notes += [n for n in counted.notes if "capped" in n]

    return Table(
        columns=[Column(name="start_year", dtype="temporal"),
                 Column(name="trial_count", dtype="quantitative", unit="trials"),
                 Column(name="estimated_count", dtype="quantitative", unit="trials")],
        rows=rows,
        total_records=len(records),
        missing_count=undated,
        notes=notes,
    )