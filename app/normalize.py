"""Normalizer: one raw ClinicalTrials.gov v2 study -> one TrialRecord.

Rules
-----
- Helpers return plain dicts; only normalize_study() builds the TrialRecord,
  so a record is validated once and every problem is reported together.
- One bad field never breaks a record, and one bad record never breaks a query:
  unreadable values are skipped, and normalize_studies() collects failures.
- Raw API text is kept next to every cleaned value, with its exact JSON path,
  so citations can quote the source precisely.
"""
import logging
import re

from pydantic import ValidationError

from app.drug_names import normalize_drug_names
from app.schemas.records import DRUG_LIKE_TYPES, NON_DRUG_NAMES, Phase, TrialRecord

logger = logging.getLogger(__name__)

PROTOCOL = "protocolSection"
SPONSOR_PATH = f"{PROTOCOL}.sponsorCollaboratorsModule"
ARMS_PATH = f"{PROTOCOL}.armsInterventionsModule"
CONTACTS_PATH = f"{PROTOCOL}.contactsLocationsModule"

# Paths of simple fields, used for citations of scalar values.
SCALAR_SOURCES = {
    "nct_id": f"{PROTOCOL}.identificationModule.nctId",
    "brief_title": f"{PROTOCOL}.identificationModule.briefTitle",
    "official_title": f"{PROTOCOL}.identificationModule.officialTitle",
    "overall_status": f"{PROTOCOL}.statusModule.overallStatus",
    "why_stopped": f"{PROTOCOL}.statusModule.whyStopped",
    "study_type": f"{PROTOCOL}.designModule.studyType",
    "phases": f"{PROTOCOL}.designModule.phases",
    "allocation": f"{PROTOCOL}.designModule.designInfo.allocation",
    "primary_purpose": f"{PROTOCOL}.designModule.designInfo.primaryPurpose",
    "masking": f"{PROTOCOL}.designModule.designInfo.maskingInfo.masking",
    "enrollment_count": f"{PROTOCOL}.designModule.enrollmentInfo.count",
    "enrollment_type": f"{PROTOCOL}.designModule.enrollmentInfo.type",
}


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------

LEGAL_SUFFIXES = r"\b(inc|llc|ltd|limited|corp|corporation|co|gmbh|ag|sa|plc)\b\.?"

# Country spellings seen in the API -> one canonical name. Extend as found.
COUNTRY_ALIASES = {
    "turkey (türkiye)": "Turkey",
    "türkiye": "Turkey",
    "korea, republic of": "South Korea",
    "russian federation": "Russia",
    "czech republic": "Czechia",
}


def normalize_term(term: str) -> str:
    """Lowercase and collapse whitespace."""
    return " ".join(term.lower().split())


def normalize_name(name: str) -> str:
    """Organization names: lowercase, drop legal suffixes and punctuation."""
    s = name.lower()
    s = re.sub(LEGAL_SUFFIXES, "", s)
    s = re.sub(r"[.,]", "", s)
    return " ".join(s.split())


def normalize_country(country: str) -> str:
    cleaned = " ".join(country.split())
    return COUNTRY_ALIASES.get(cleaned.lower(), cleaned)


# ---------------------------------------------------------------------------
# Field parsers (each returns plain dicts)
# ---------------------------------------------------------------------------

def parse_date(struct: dict | None, path: str) -> dict | None:
    """API date struct -> PartialDate dict, or None if missing or unreadable."""
    if not struct or not struct.get("date"):
        return None

    raw = struct["date"]
    try:
        numbers = [int(p) for p in raw.split("-")]
    except ValueError:
        return None        # not a date we understand, e.g. "TBD"

    return {
        "raw": raw,
        "year": numbers[0],
        "month": numbers[1] if len(numbers) > 1 else None,
        "day": numbers[2] if len(numbers) > 2 else None,
        "date_type": struct.get("type"),
        "source_path": path,
    }


def parse_phases(design: dict, nct_id: str) -> list[str]:
    """Keep known phase values; skip and log unknown ones instead of failing."""
    known = {p.value for p in Phase}
    phases = []
    for value in design.get("phases", []):
        if value in known:
            phases.append(value)
        else:
            logger.warning("%s: unknown phase %r skipped", nct_id, value)
    return phases


def parse_sponsors(protocol: dict) -> list[dict]:
    module = protocol.get("sponsorCollaboratorsModule", {})
    sponsors = []

    lead = module.get("leadSponsor")
    if lead and lead.get("name"):
        sponsors.append({
            "name": lead["name"],
            "name_normalized": normalize_name(lead["name"]),
            "sponsor_class": lead.get("class"),
            "role": "lead",
            "source_path": f"{SPONSOR_PATH}.leadSponsor",
        })

    for i, collab in enumerate(module.get("collaborators", [])):
        if not collab.get("name"):
            continue
        sponsors.append({
            "name": collab["name"],
            "name_normalized": normalize_name(collab["name"]),
            "sponsor_class": collab.get("class"),
            "role": "collaborator",
            "source_path": f"{SPONSOR_PATH}.collaborators[{i}]",
        })

    return sponsors


def parse_interventions(protocol: dict) -> list[dict]:
    module = protocol.get("armsInterventionsModule", {})
    interventions = []

    for i, item in enumerate(module.get("interventions", [])):
        name = item.get("name")
        if not name:
            continue
        interventions.append({
            "name": name,
            "names_normalized": normalize_drug_names(name),
            "intervention_type": item.get("type", "OTHER"),
            "other_names": item.get("otherNames", []),
            "arm_labels": item.get("armGroupLabels", []),
            "source_path": f"{ARMS_PATH}.interventions[{i}]",
        })

    return interventions


def parse_arms(protocol: dict, interventions: list[dict]) -> list[dict]:
    """Arms with their drug-like interventions, reusing the normalized names."""
    module = protocol.get("armsInterventionsModule", {})
    by_name = {iv["name"].lower(): iv for iv in interventions}

    arms = []
    for i, group in enumerate(module.get("armGroups", [])):
        label = group.get("label")
        if not label:
            continue

        drugs = set()
        refs = group.get("interventionNames", [])
        for ref in refs:
            name = ref.split(":", 1)[-1].strip()      # "Drug: Cisplatin" -> "Cisplatin"
            iv = by_name.get(name.lower())
            if iv and iv["intervention_type"] in DRUG_LIKE_TYPES:
                drugs.update(n for n in iv["names_normalized"] if n not in NON_DRUG_NAMES)

        arms.append({
            "label": label,
            "arm_type": group.get("type"),
            "intervention_names": refs,
            "drugs_normalized": sorted(drugs),
            "source_path": f"{ARMS_PATH}.armGroups[{i}]",
        })

    return arms


def parse_conditions(study: dict) -> list[dict]:
    """Sponsor free-text conditions plus derived MeSH terms, labeled by origin."""
    protocol = study.get(PROTOCOL, {})
    derived = study.get("derivedSection", {})
    conditions = []

    sponsor_terms = protocol.get("conditionsModule", {}).get("conditions", [])
    for i, term in enumerate(sponsor_terms):
        if term:
            conditions.append({
                "term": term,
                "term_normalized": normalize_term(term),
                "origin": "sponsor",
                "source_path": f"{PROTOCOL}.conditionsModule.conditions[{i}]",
            })

    meshes = derived.get("conditionBrowseModule", {}).get("meshes", [])
    for i, mesh in enumerate(meshes):
        term = mesh.get("term")
        if term:
            conditions.append({
                "term": term,
                "term_normalized": normalize_term(term),
                "origin": "mesh",
                "source_path": f"derivedSection.conditionBrowseModule.meshes[{i}]",
            })

    return conditions


def parse_locations(protocol: dict) -> list[dict]:
    module = protocol.get("contactsLocationsModule", {})   # may be {} (no sites yet)
    locations = []

    for i, loc in enumerate(module.get("locations", [])):
        country = loc.get("country")
        geo = loc.get("geoPoint") or {}
        locations.append({
            "facility": loc.get("facility"),
            "city": loc.get("city"),
            "state": loc.get("state"),
            "country": country,
            "country_normalized": normalize_country(country) if country else None,
            "lat": geo.get("lat"),
            "lon": geo.get("lon"),
            "source_path": f"{CONTACTS_PATH}.locations[{i}]",
        })

    return locations


def parse_officials(protocol: dict) -> list[dict]:
    module = protocol.get("contactsLocationsModule", {})
    officials = []

    for i, person in enumerate(module.get("overallOfficials", [])):
        if not person.get("name"):
            continue
        officials.append({
            "name": person["name"],
            "affiliation": person.get("affiliation"),
            "role": person.get("role"),
            "source_path": f"{CONTACTS_PATH}.overallOfficials[{i}]",
        })

    return officials


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def normalize_study(study: dict) -> TrialRecord:
    """Build and validate one TrialRecord. Raises ValidationError if invalid."""
    protocol = study.get(PROTOCOL, {})
    ident = protocol.get("identificationModule", {})
    status = protocol.get("statusModule", {})
    design = protocol.get("designModule", {})
    design_info = design.get("designInfo", {})
    enrollment = design.get("enrollmentInfo", {})
    nct_id = ident.get("nctId", "")

    interventions = parse_interventions(protocol)
    status_path = f"{PROTOCOL}.statusModule"

    return TrialRecord.model_validate({
        # Identity
        "nct_id": nct_id,
        "brief_title": ident.get("briefTitle"),
        "official_title": ident.get("officialTitle"),

        # Status and design
        "overall_status": status.get("overallStatus"),
        "why_stopped": status.get("whyStopped"),
        "study_type": design.get("studyType"),
        "allocation": design_info.get("allocation"),
        "primary_purpose": design_info.get("primaryPurpose"),
        "masking": design_info.get("maskingInfo", {}).get("masking"),
        "phases": parse_phases(design, nct_id),

        # Dates
        "start": parse_date(status.get("startDateStruct"), f"{status_path}.startDateStruct"),
        "primary_completion": parse_date(status.get("primaryCompletionDateStruct"),
                                         f"{status_path}.primaryCompletionDateStruct"),
        "completion": parse_date(status.get("completionDateStruct"),
                                 f"{status_path}.completionDateStruct"),

        # Size
        "enrollment_count": enrollment.get("count"),
        "enrollment_type": enrollment.get("type"),

        # Who, what, where
        "sponsors": parse_sponsors(protocol),
        "officials": parse_officials(protocol),
        "conditions": parse_conditions(study),
        "interventions": interventions,
        "arms": parse_arms(protocol, interventions),
        "locations": parse_locations(protocol),

        "has_results": study.get("hasResults", False),
        "scalar_sources": SCALAR_SOURCES,
    })


def normalize_studies(studies: list[dict]) -> tuple[list[TrialRecord], list[dict]]:
    """Normalize many studies. Invalid ones are skipped and reported, never fatal.

    Returns (records, failures), where each failure is {"nct_id", "errors"}
    so the response metadata can say how many studies were dropped and why.
    """
    records, failures = [], []
    for study in studies:
        try:
            records.append(normalize_study(study))
        except ValidationError as e:
            nct_id = (study.get(PROTOCOL, {})
                      .get("identificationModule", {})
                      .get("nctId", "unknown"))
            logger.warning("%s: skipped, %d validation errors", nct_id, e.error_count())
            failures.append({"nct_id": nct_id, "errors": e.errors(include_url=False)})
    return records, failures