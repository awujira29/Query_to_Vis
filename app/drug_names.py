"""Drug name normalization, shared by the normalizer and the post-filters.

Real intervention names seen in the API, and what they should become:
  "BMS-936558 (Nivolumab)"                     -> ["nivolumab"]
  "Nivolumab IV", "intrathecal nivolumab"      -> ["nivolumab"]
  "Pembrolizumab or Nivolumab"                 -> ["pembrolizumab", "nivolumab"]
  "Relatlimab and nivolumab fixed dose combination (FDC)" -> ["relatlimab", "nivolumab"]
  "Nivolumab-Relatlimab"                       -> ["nivolumab", "relatlimab"]
  "OPDIVO®"                                    -> ["nivolumab"]
  "Placebo matching BMS-936558 (Nivolumab)"    -> ["placebo"]
  "Nivolumab: Cohort 1 (Cutaneous)"            -> ["nivolumab"]
  "Nivolumab & Ipilimumab"                     -> ["nivolumab", "ipilimumab"]
  "Nivolumab Injection [Opdivo]"               -> ["nivolumab"]
  "EU-Opdivo®", "Opdivo-EU (Induction)"        -> ["nivolumab"]
"""
import re

# Codes and brand names -> generic name. Small and curated: extend as found.
SYNONYMS = {
    "bms-936558": "nivolumab", "mdx-1106": "nivolumab", "ono-4538": "nivolumab",
    "opdivo": "nivolumab",
    "mk-3475": "pembrolizumab", "sch 900475": "pembrolizumab",
    "keytruda": "pembrolizumab", "lambrolizumab": "pembrolizumab",
    "mdx-010": "ipilimumab", "bms-734016": "ipilimumab", "yervoy": "ipilimumab",
    "mpdl3280a": "atezolizumab", "tecentriq": "atezolizumab",
    "medi4736": "durvalumab", "imfinzi": "durvalumab",
    "tas-102": "trifluridine/tipiracil", "lonsurf": "trifluridine/tipiracil",
}

# Words describing route, form or dosing, not the drug itself.
QUALIFIERS = r"\b(iv|it|sc|im|po|oral|intravenous|intrathecal|intratumoral|intra-tumoral|" \
             r"subcutaneous|injection|infusion|tablets?|capsules?|fixed dose combination|fdc|" \
             r"monotherapy|therapy)\b"

DOSE = r"\b\d+(\.\d+)?\s*(mg|mcg|µg|g|ml|mg/kg|mg/m2|iu|units?)\b"
COMBO_SPLIT = r"\s*(?:\bwith/without\b|/|\+|&|,|\band\b|\bplus\b|\bor\b|\bwith\b)\s*"

# Sourcing labels in biosimilar trials: "EU-Opdivo", "Opdivo-US", "US- sourced Opdivo".
REGION = r"(^(eu|us|uk)\s*-\s*(sourced\s+)?|\s*-\s*(eu|us|uk)$|\b(eu|us|uk)\s+sourced\b)"

# Too generic to be a drug node.
GENERIC = {"immunotherapy", "chemotherapy", "ici", "standard of care", "soc",
           "investigator's choice", "the combination of radiotherapy",
           "induction", "maintenance", "dose escalation", "dose expansion"}


def _split_hyphen_combo(part: str) -> list[str]:
    """'nivolumab-relatlimab' -> two drugs; 'nab-paclitaxel', 'bms-936558' stay whole."""
    pieces = part.split("-")
    if len(pieces) == 2 and all(p.isalpha() and len(p) >= 6 for p in pieces):
        return pieces
    return [part]


def canonical(name: str) -> str:
    return SYNONYMS.get(name, name)


def normalize_drug_names(name: str) -> list[str]:
    s = re.sub(r"[®™©]", "", name.lower())
    if s.strip().startswith("placebo"):
        return ["placebo"]

    # Text after a colon describes the cohort: "Nivolumab: Cohort 1 (Cutaneous)".
    s = s.split(":", 1)[0]

    # Bracketed text is often the generic or brand name: "BMS-936558 (Nivolumab)",
    # "Nivolumab Injection [Opdivo]". Keep it as extra candidates.
    inside = re.findall(r"[(\[](.*?)[)\]]", s)
    pieces = [re.sub(r"[(\[].*?[)\]]", " ", s)] + inside

    out: list[str] = []
    for piece in pieces:
        piece = re.sub(DOSE, " ", piece)
        for part in re.split(COMBO_SPLIT, piece):
            part = re.sub(QUALIFIERS, " ", part)
            part = re.sub(REGION, " ", part.strip())
            part = " ".join(part.split()).strip(" -.,;:")
            for p in _split_hyphen_combo(part):
                p = canonical(p)
                if p and p not in GENERIC and p not in out:
                    out.append(p)
    return out


def mentions_drug(text: str, drug: str) -> bool:
    """Word-level fallback: does any word in `text` resolve to `drug`?

    Catches names the structured rules miss, e.g. "anti-PD-1 Therapy Nivolumab".
    Splits on everything except letters, digits and hyphens, then also tries the
    hyphen-split pieces, so both "bms-936558" and "eu-opdivo" resolve.
    """
    target = canonical(drug.strip().lower())
    words = re.findall(r"[a-z0-9-]+", re.sub(r"[®™©]", "", text.lower()))
    for word in words:
        for candidate in [word, *word.split("-")]:
            if canonical(candidate) == target:
                return True
    return False