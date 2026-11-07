"""The request a client sends: a question plus optional structured fields.

Only `query` is required. Structured fields are the client's way of pinning down
what the question is about; whatever they say wins over the planner's reading of
the text (see app.planner.apply_structured_fields). Values are normalized here so
the rest of the pipeline sees clean, validated input: phases become Phase enums,
statuses become upper-case API codes, blank strings become None.
"""
import re

from pydantic import BaseModel, Field, field_validator, model_validator

from app.dimensions import DIMENSIONS
from app.labels import DISPLAY
from app.query import Filters
from app.schemas.records import Phase
from app.schemas.response import VizType

STATUSES = tuple(DISPLAY["overall_status"])
_ROMAN = {"I": "1", "II": "2", "III": "3", "IV": "4"}

DEFAULT_MAX_STUDIES = 3000
MAX_STUDIES_LIMIT = 10000


def parse_phase(value: str | Phase) -> Phase:
    """'Phase 2', 'phase2', 'PHASE2', '2', 'II', 'early phase 1', 'N/A' -> Phase."""
    if isinstance(value, Phase):
        return value
    s = re.sub(r"[\s_\-/]", "", str(value).strip().upper())
    s = s.replace("PHASE", "")
    if s in ("NA", "N/A", "NOTAPPLICABLE", "NONE"):
        return Phase.NA
    if s.startswith("EARLY"):
        return Phase.EARLY_PHASE1
    s = _ROMAN.get(s, s)
    if s in ("1", "2", "3", "4"):
        return Phase(f"PHASE{s}")
    raise ValueError(f"unknown phase {value!r}; use one of "
                     f"{[p.value for p in Phase]} or 'Phase 1'..'Phase 4'")


class QueryRequest(BaseModel):
    query: str = Field(min_length=3, max_length=500,
                       description="A natural-language question about clinical trials.")

    # Optional structured fields: what the question is about.
    drug_name: str | None = Field(default=None, max_length=120)
    condition: str | None = Field(default=None, max_length=120)
    sponsor: str | None = Field(default=None, max_length=160)
    country: str | None = Field(default=None, max_length=80)
    trial_phase: list[Phase] = Field(default_factory=list,
                                     description="e.g. ['Phase 2', 'Phase 3']; any spelling.")
    status: list[str] = Field(default_factory=list,
                              description=f"ClinicalTrials.gov overall status codes: {list(STATUSES)}")
    start_year: int | None = Field(default=None, ge=1900, le=2100)
    end_year: int | None = Field(default=None, ge=1900, le=2100)

    # Optional control over the answer.
    visualization_type: VizType | None = Field(
        default=None, description="Force a visualization type instead of letting the planner choose.")
    dimension: str | None = Field(
        default=None, description=f"Force the grouping dimension: one of {sorted(DIMENSIONS)}")
    top_n: int | None = Field(default=None, ge=1, le=100,
                              description="Keep the N largest groups (or nodes per type).")
    max_studies: int = Field(default=DEFAULT_MAX_STUDIES, ge=100, le=MAX_STUDIES_LIMIT,
                             description="Most trials fetched per series; larger is slower.")

    @field_validator("drug_name", "condition", "sponsor", "country", "dimension", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        if isinstance(v, str):
            v = " ".join(v.split())
            return v or None
        return v

    @field_validator("trial_phase", mode="before")
    @classmethod
    def _phases(cls, v):
        if v is None:
            return []
        if isinstance(v, (str, Phase)):
            v = [v]
        return list(dict.fromkeys(parse_phase(p) for p in v))

    @field_validator("status", mode="before")
    @classmethod
    def _statuses(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            v = [v]
        out = []
        for s in v:
            code = re.sub(r"[\s\-]+", "_", str(s).strip().upper())
            if code not in STATUSES:
                raise ValueError(f"unknown status {s!r}; use one of {list(STATUSES)}")
            if code not in out:
                out.append(code)
        return out

    @field_validator("dimension")
    @classmethod
    def _dimension(cls, v):
        if v is not None and v not in DIMENSIONS:
            raise ValueError(f"unknown dimension {v!r}; use one of {sorted(DIMENSIONS)}")
        return v

    @model_validator(mode="after")
    def _years(self):
        if self.start_year and self.end_year and self.end_year < self.start_year:
            raise ValueError("end_year must not be before start_year")
        return self

    def to_filters(self) -> Filters:
        """The structured fields as Filters (the planner adds what the text says)."""
        return Filters(drug=self.drug_name, condition=self.condition, sponsor=self.sponsor,
                       country=self.country, status=list(self.status),
                       phases=list(self.trial_phase), start_year=self.start_year,
                       end_year=self.end_year)

    def structured_summary(self) -> dict:
        """The non-empty structured fields, for prompts and metadata."""
        return self.model_dump(exclude_none=True, exclude_defaults=True, exclude={"query"})
