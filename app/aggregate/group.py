"""Generic group-by: sort records into groups using a registered dimension."""
from dataclasses import dataclass, field

from app.dimensions import DIMENSIONS
from app.schemas.records import TrialRecord


@dataclass
class Grouping:
    groups: dict[str, list[TrialRecord]] = field(default_factory=dict)
    missing: list[TrialRecord] = field(default_factory=list)   # no key for this dimension


def group_by(records: list[TrialRecord], dimension: str) -> Grouping:
    """Group records by a dimension. A record may land in several groups."""
    if dimension not in DIMENSIONS:
        raise ValueError(f"unknown dimension: {dimension!r}")
    extract = DIMENSIONS[dimension]

    result = Grouping()
    for record in records:
        keys = extract(record)
        if not keys:
            result.missing.append(record)
            continue
        for key in keys:
            result.groups.setdefault(key, []).append(record)
    return result