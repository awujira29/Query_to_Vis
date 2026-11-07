"""Post-filters applied after normalization.

The API's search is broad: query.intr matches a drug mentioned anywhere in a
trial (other names, descriptions...), so some hits barely involve it. Strict
filters keep only trials where the entity is actually an intervention.
"""
import re

from app.drug_names import mentions_drug, normalize_drug_names
from app.schemas.records import TrialRecord


def has_drug(record: TrialRecord, drug: str) -> bool:
    """True if `drug` is a drug-like intervention, by normalized name or synonym.

    Names and other_names go through the same normalization, so codes, brand
    names, routes and combinations all resolve to the generic name.
    """
    terms = set(normalize_drug_names(drug))
    for iv in record.interventions:
        if not iv.is_drug_like:
            continue
        names = set(iv.names_normalized)
        for other in iv.other_names:
            names.update(normalize_drug_names(other))
        if terms & names:
            return True
        # Fallback for free-text names the rules miss; placebos never count.
        if not iv.name.lower().startswith("placebo") and any(
                mentions_drug(text, drug) for text in [iv.name, *iv.other_names]):
            return True
    return False


def require_drug(records: list[TrialRecord], drug: str) -> tuple[list[TrialRecord], str | None]:
    """Keep trials where `drug` is an intervention. Returns (kept, note or None)."""
    kept = [r for r in records if has_drug(r, drug)]
    dropped = len(records) - len(kept)
    note = None
    if dropped:
        note = (f"{dropped} of {len(records)} trials returned by the API search were excluded "
                f"because '{drug}' is not one of their listed drug interventions.")
    return kept, note


# Words that all mean "a cancer" in condition text and MeSH terms.
CANCER_WORDS = {"cancer", "neoplasm", "carcinoma", "adenocarcinoma", "tumor",
                "tumour", "malignancy", "malignant", "oncology"}


def _tokens(text: str) -> set[str]:
    """Lowercase words, hyphens split, simple plural stripping (neoplasms -> neoplasm)."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w[:-1] if len(w) > 4 and w.endswith("s") else w for w in words}


def has_condition(record: TrialRecord, condition: str) -> bool:
    """True if one of the trial's conditions (sponsor text or MeSH) is `condition`.

    "lung cancer" matches "Non-Small Cell Lung Cancer" and the MeSH term
    "Carcinoma, Non-Small-Cell Lung", because every distinctive word ("lung")
    must appear and any cancer word stands in for "cancer". It does not match
    "Asthma" or a COVID-19 registry that the API's broad search returns.
    """
    query = _tokens(condition)
    needs_cancer = bool(query & CANCER_WORDS)
    distinctive = query - CANCER_WORDS
    for c in record.conditions:
        words = _tokens(c.term)
        if not distinctive <= words:
            continue
        if needs_cancer and not words & CANCER_WORDS:
            continue
        return True
    return False


def require_condition(records: list[TrialRecord], condition: str) -> tuple[list[TrialRecord], str | None]:
    """Keep trials that list `condition`. Returns (kept, note or None)."""
    kept = [r for r in records if has_condition(r, condition)]
    dropped = len(records) - len(kept)
    note = None
    if dropped:
        note = (f"{dropped} of {len(records)} trials returned by the API search were excluded "
                f"because '{condition}' is not one of their listed conditions.")
    return kept, note
