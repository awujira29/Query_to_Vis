"""Builders for raw ClinicalTrials.gov v2 study dicts, shaped like the real API.

Tests build small, fully controlled study sets with these instead of relying on the
network, and run them through the same normalizer as production data.
"""
from __future__ import annotations


def study(n: int, *, title: str = "A trial", phases: list[str] | None = None,
          start: str | None = "2020-03", start_type: str = "ACTUAL",
          completion: str | None = "2023-03", status: str = "COMPLETED",
          study_type: str = "INTERVENTIONAL", enrollment: int | None = 100,
          sponsor: str = "Sponsor A", sponsor_class: str = "INDUSTRY",
          conditions: list[str] = ("Melanoma",), meshes: list[str] = ("Melanoma",),
          arms: dict[str, list[str]] | None = None, countries: list[str] = ("United States",),
          intervention_types: dict[str, str] | None = None) -> dict:
    """One raw study. `arms` maps arm label -> drug names given in it."""
    arms = arms if arms is not None else {"Arm 1": ["Pembrolizumab"]}
    names = sorted({d for drugs in arms.values() for d in drugs})
    types = intervention_types or {}
    protocol = {
        "identificationModule": {"nctId": f"NCT{n:08d}", "briefTitle": title},
        "statusModule": {"overallStatus": status},
        "designModule": {"studyType": study_type, "phases": phases if phases is not None else ["PHASE2"]},
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": sponsor, "class": sponsor_class}},
        "conditionsModule": {"conditions": list(conditions)},
        "armsInterventionsModule": {
            "interventions": [{"type": types.get(d, "DRUG"), "name": d,
                               "armGroupLabels": [a for a, ds in arms.items() if d in ds]}
                              for d in names],
            "armGroups": [{"label": a, "type": "EXPERIMENTAL",
                           "interventionNames": [f"{types.get(d, 'DRUG').capitalize()}: {d}" for d in ds]}
                          for a, ds in arms.items()],
        },
        "contactsLocationsModule": {
            "locations": [{"facility": f"Site {i}", "city": "City", "country": c}
                          for i, c in enumerate(countries)]},
    }
    if start:
        protocol["statusModule"]["startDateStruct"] = {"date": start, "type": start_type}
    if completion:
        protocol["statusModule"]["completionDateStruct"] = {"date": completion, "type": "ACTUAL"}
    if enrollment is not None:
        protocol["designModule"]["enrollmentInfo"] = {"count": enrollment, "type": "ACTUAL"}
    return {"protocolSection": protocol,
            "derivedSection": {"conditionBrowseModule": {"meshes": [{"term": m} for m in meshes]}},
            "hasResults": False}


def fetched(studies: list[dict], total: int | None = None, truncated: bool = False) -> dict:
    """What client.fetch_studies returns."""
    return {"studies": studies, "total_available": total if total is not None else len(studies),
            "truncated": truncated, "api_urls": ["https://clinicaltrials.gov/api/v2/studies?test=1"]}
