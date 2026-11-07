"""Build Citations: for a record and a group key, find the exact field and value
that put the record in that group."""
from app.schemas.records import TrialRecord
from app.schemas.shapes import Citation

MAX_CITATIONS = 5    # per datum; the full list is always in "nct_ids"


def cite(record: TrialRecord, field: str, value) -> Citation:
    return Citation(nct_id=record.nct_id, url=record.url, field=field,
                    value=str(value), title=record.brief_title)


def _scalar(record: TrialRecord, attr: str) -> tuple[str, str] | None:
    value = getattr(record, attr)
    path = record.scalar_sources.get(attr)
    if value is None or not path:
        return None
    if isinstance(value, list):
        value = ", ".join(str(v.value if hasattr(v, "value") else v) for v in value)
    return path, str(value)


def evidence(record: TrialRecord, dimension: str, key: str) -> tuple[str, str] | None:
    """(json_path, exact_value) showing why `record` belongs to group `key`."""
    if dimension == "phase":
        if not record.phases:
            # The phase field is absent, so cite the study type that explains why
            # (e.g. OBSERVATIONAL studies have no phase). Never invent an excerpt.
            return _scalar(record, "study_type")
        return _scalar(record, "phases")

    if dimension in ("overall_status", "study_type", "allocation",
                     "primary_purpose", "masking"):
        return _scalar(record, dimension)

    if dimension == "start_year" and record.start:
        return f"{record.start.source_path}.date", record.start.raw

    if dimension in ("sponsor_class", "lead_sponsor") and record.lead_sponsor:
        s = record.lead_sponsor
        if dimension == "sponsor_class":
            return f"{s.source_path}.class", str(s.sponsor_class)
        return f"{s.source_path}.name", s.name

    if dimension == "country":
        for loc in record.locations:
            if loc.country_normalized == key:
                return f"{loc.source_path}.country", loc.country or key

    if dimension == "drug":
        for iv in record.interventions:
            if key in iv.names_normalized:
                return f"{iv.source_path}.name", iv.name

    if dimension == "intervention_type":
        for iv in record.interventions:
            if iv.intervention_type == key:
                return f"{iv.source_path}.type", iv.intervention_type

    if dimension == "condition":
        for c in record.conditions:
            if c.origin == "mesh" and c.term_normalized == key:
                return f"{c.source_path}.term", c.term

    if dimension == "has_results":
        return "hasResults", str(record.has_results).lower()

    return None


def citations_for(records: list[TrialRecord], dimension: str, key: str,
                  limit: int = MAX_CITATIONS) -> list[Citation]:
    out = []
    for r in records:
        ev = evidence(r, dimension, key)
        if ev:
            out.append(cite(r, *ev))
            if len(out) >= limit:
                break
    return out