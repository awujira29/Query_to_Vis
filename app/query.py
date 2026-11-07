"""Filters -> ClinicalTrials.gov query params, with drug and condition names resolved.

Everything that narrows *which* trials are fetched lives here, so every chart uses
the same, verified definition of "trials for X". The resolver turns names into
MeSH-based expressions; structured filters (years, status, phase, country) are
pushed to the API as well, so exact counts and fetched records always agree.
"""
from collections.abc import Callable

from pydantic import BaseModel, Field

from app.resolve import Resolution, merge_params, resolve
from app.schemas.records import Phase


class Filters(BaseModel):
    """Which trials a question is about. Every field is optional."""
    drug: str | None = None
    condition: str | None = None
    sponsor: str | None = None
    country: str | None = None
    status: list[str] = Field(default_factory=list)      # e.g. ["RECRUITING"]
    phases: list[Phase] = Field(default_factory=list)
    start_year: int | None = Field(default=None, ge=1900, le=2100)
    end_year: int | None = Field(default=None, ge=1900, le=2100)


class Query(BaseModel):
    params: dict[str, str]
    resolutions: list[Resolution] = []
    notes: list[str] = []


def build_query(filters: Filters,
                resolver: Callable[[str, str], Resolution] = resolve) -> Query:
    parts: list[dict[str, str]] = []
    resolutions: list[Resolution] = []
    notes: list[str] = []

    for kind, words in (("condition", filters.condition), ("drug", filters.drug)):
        if words:
            res = resolver(kind, words)
            resolutions.append(res)
            parts.append(res.api_params)
            notes.append(res.note)

    if filters.start_year or filters.end_year:
        lo = f"{filters.start_year}-01-01" if filters.start_year else "MIN"
        hi = f"{filters.end_year}-12-31" if filters.end_year else "MAX"
        parts.append({"filter.advanced": f"AREA[StartDate]RANGE[{lo},{hi}]"})

    if filters.phases:
        phases = " OR ".join(p.value for p in filters.phases)
        parts.append({"filter.advanced": f"AREA[Phase]({phases})"})

    if filters.status:
        parts.append({"filter.overallStatus": ",".join(filters.status)})

    if filters.country:
        parts.append({"filter.advanced": f'AREA[LocationCountry]"{filters.country}"'})

    if filters.sponsor:
        # Lead sponsor or collaborator, by name. Not resolved: organisation names
        # have no controlled vocabulary in the registry.
        parts.append({"filter.advanced": f'(AREA[LeadSponsorName]"{filters.sponsor}" '
                                         f'OR AREA[CollaboratorName]"{filters.sponsor}")'})
        notes.append(f"Sponsor matched by name ('{filters.sponsor}'), as lead or collaborator.")

    return Query(params=merge_params(*parts) if parts else {}, resolutions=resolutions,
                 notes=notes)
