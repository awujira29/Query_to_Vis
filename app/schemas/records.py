"""Normalized trial record: the flat shape every aggregator reads."""
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, computed_field


# ---------------------------------------------------------------------------
# Phase
# ---------------------------------------------------------------------------

class Phase(str, Enum):
    NA = "NA"
    EARLY_PHASE1 = "EARLY_PHASE1"
    PHASE1 = "PHASE1"
    PHASE2 = "PHASE2"
    PHASE3 = "PHASE3"
    PHASE4 = "PHASE4"


PHASE_ORDER = [
    Phase.EARLY_PHASE1, Phase.PHASE1, Phase.PHASE2,
    Phase.PHASE3, Phase.PHASE4, Phase.NA,
]

PHASE_LABELS = {
    Phase.NA: "Not Applicable",
    Phase.EARLY_PHASE1: "Early Phase 1",
    Phase.PHASE1: "Phase 1",
    Phase.PHASE2: "Phase 2",
    Phase.PHASE3: "Phase 3",
    Phase.PHASE4: "Phase 4",
}

NO_PHASE_LABEL = "No Phase Listed"


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

class DateType(str, Enum):
    ACTUAL = "ACTUAL"
    ESTIMATED = "ESTIMATED"


class PartialDate(BaseModel):
    raw: str                           # exact API text, e.g. "2015-05"
    year: int                          # required: no year, no date
    month: int | None = None           # optional: "2015" has no month
    day: int | None = None             # optional: "2015-05" has no day
    date_type: DateType | None = None  # optional: older records omit it
    source_path: str


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------

class Sponsor(BaseModel):
    name: str                          # exact API text, for citations
    name_normalized: str               # cleaned, for grouping
    sponsor_class: str | None = None   # INDUSTRY, NIH, OTHER...
    role: Literal["lead", "collaborator"]
    source_path: str


DRUG_LIKE_TYPES = {"DRUG", "BIOLOGICAL", "COMBINATION_PRODUCT"}

# Normalized names that are typed as drugs but are not drugs given to patients.
NON_DRUG_NAMES = {"placebo"}


class Intervention(BaseModel):
    name: str                       # exact API text, e.g. "favezelimab/pembrolizumab"
    names_normalized: list[str]     # cleaned, can be several: ["favezelimab", "pembrolizumab"]
    intervention_type: str          # DRUG, BIOLOGICAL, DEVICE, OTHER...
    other_names: list[str] = []     # synonyms: Keytruda, MK-3475
    arm_labels: list[str] = []      # which arms use it
    source_path: str

    @property
    def is_drug_like(self) -> bool:
        return self.intervention_type in DRUG_LIKE_TYPES


class Arm(BaseModel):
    label: str                        # e.g. "Part 3 Dose expansion"
    arm_type: str | None = None       # EXPERIMENTAL, ACTIVE_COMPARATOR, PLACEBO_COMPARATOR...
    intervention_names: list[str] = []  # exact API text, e.g. ["Drug: Pembrolizumab", ...]
    drugs_normalized: list[str] = []  # drug-like interventions given in this arm (no placebo)
    source_path: str


class Condition(BaseModel):
    term: str                             # exact text, e.g. "Lung Non-Small Cell Carcinoma"
    term_normalized: str                  # cleaned, for grouping
    origin: Literal["sponsor", "mesh"]    # where it came from
    source_path: str


class Location(BaseModel):
    facility: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None              # exact API text, e.g. "Turkey (Türkiye)"
    country_normalized: str | None = None   # cleaned, for grouping
    lat: float | None = None
    lon: float | None = None
    source_path: str


class Official(BaseModel):
    name: str                           # e.g. "Grace K Dy"
    affiliation: str | None = None      # e.g. "Roswell Park Cancer Institute"
    role: str | None = None             # PRINCIPAL_INVESTIGATOR, STUDY_CHAIR, STUDY_DIRECTOR
    source_path: str


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------

class TrialRecord(BaseModel):
    # Identity
    nct_id: str = Field(pattern=r"^NCT\d{8}$")
    brief_title: str
    official_title: str | None = None

    # Status and design
    overall_status: str | None = None
    why_stopped: str | None = None
    study_type: str | None = None
    allocation: str | None = None
    primary_purpose: str | None = None
    masking: str | None = None
    phases: list[Phase] = []

    # Dates
    start: PartialDate | None = None
    primary_completion: PartialDate | None = None
    completion: PartialDate | None = None

    # Size
    enrollment_count: int | None = Field(default=None, ge=0)
    enrollment_type: DateType | None = None

    # Who, what, where
    sponsors: list[Sponsor] = []
    officials: list[Official] = []
    conditions: list[Condition] = []
    interventions: list[Intervention] = []
    arms: list[Arm] = []
    locations: list[Location] = []

    has_results: bool = False

    # Paths of simple fields, for citations
    scalar_sources: dict[str, str] = {}

    @computed_field
    @property
    def phase_label(self) -> str:
        if not self.phases:
            return NO_PHASE_LABEL
        ordered = sorted(self.phases, key=PHASE_ORDER.index)
        return "/".join(PHASE_LABELS[p] for p in ordered)

    @computed_field
    @property
    def start_year(self) -> int | None:
        return self.start.year if self.start else None

    @computed_field
    @property
    def duration_months(self) -> int | None:
        end = self.completion or self.primary_completion
        if not self.start or not end:
            return None
        months = (end.year - self.start.year) * 12 + ((end.month or 1) - (self.start.month or 1))
        return months if months >= 0 else None

    @computed_field
    @property
    def countries(self) -> list[str]:
        return sorted({loc.country_normalized for loc in self.locations if loc.country_normalized})

    @computed_field
    @property
    def lead_sponsor(self) -> Sponsor | None:
        return next((s for s in self.sponsors if s.role == "lead"), None)

    @computed_field
    @property
    def drugs(self) -> list[str]:
        return sorted({name for i in self.interventions if i.is_drug_like
                       for name in i.names_normalized if name not in NON_DRUG_NAMES})

    @computed_field
    @property
    def intervention_types(self) -> list[str]:
        return sorted({i.intervention_type for i in self.interventions})

    @computed_field
    @property
    def site_count(self) -> int:
        return len(self.locations)

    @computed_field
    @property
    def country_count(self) -> int:
        return len(self.countries)

    @computed_field
    @property
    def arm_count(self) -> int:
        return len(self.arms)

    @computed_field
    @property
    def intervention_count(self) -> int:
        return len(self.interventions)

    @computed_field
    @property
    def url(self) -> str:
        return f"https://clinicaltrials.gov/study/{self.nct_id}"