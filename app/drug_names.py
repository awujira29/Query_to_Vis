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
  "gemcitabine hydrochloride"                  -> ["gemcitabine"]       salt forms
  "5-FU", "5-Fluorouracil"                     -> ["fluorouracil"]
  "Cisplatin 75 mg/m^2"                        -> ["cisplatin"]         no "m^2" drug
  "FOLFOX"                                     -> ["oxaliplatin", "leucovorin", "fluorouracil"]
  "Olaparib matching placebo"                  -> ["placebo"]
  "Platinum-based chemotherapy"                -> []                    a class, not a drug
  "Recombinant Interferon Alfa-2b", "IFNα2b", "Intron A" -> ["interferon alfa-2b"]
  "PEG-Intron", "Pegylated Interferon Alpha-2b" -> ["peginterferon alfa-2b"]  a separate drug
  "Sacituzumab Govitecan (SG)"                 -> ["sacituzumab govitecan"]  "SG" is no second drug
"""
import re

# Codes, brands and spelling variants -> generic name. Small and curated: extend as found.
SYNONYMS = {
    # checkpoint inhibitors
    "bms-936558": "nivolumab", "mdx-1106": "nivolumab", "ono-4538": "nivolumab",
    "opdivo": "nivolumab",
    "mk-3475": "pembrolizumab", "sch 900475": "pembrolizumab",
    "keytruda": "pembrolizumab", "lambrolizumab": "pembrolizumab",
    "mdx-010": "ipilimumab", "bms-734016": "ipilimumab", "yervoy": "ipilimumab",
    "mpdl3280a": "atezolizumab", "rg7446": "atezolizumab", "tecentriq": "atezolizumab",
    "medi4736": "durvalumab", "imfinzi": "durvalumab",
    "bms-986016": "relatlimab",
    # melanoma BRAF / MEK inhibitors: older trials use research codes
    "gsk2118436": "dabrafenib", "tafinlar": "dabrafenib",
    "gsk1120212": "trametinib", "mekinist": "trametinib",
    "plx4032": "vemurafenib", "rg7204": "vemurafenib", "ro5185426": "vemurafenib",
    "zelboraf": "vemurafenib",
    "gdc-0973": "cobimetinib", "xl518": "cobimetinib", "cotellic": "cobimetinib",
    "lgx818": "encorafenib", "braftovi": "encorafenib",
    "mek162": "binimetinib", "arry-162": "binimetinib", "mektovi": "binimetinib",
    # chemotherapy spellings
    "5-fu": "fluorouracil", "5fu": "fluorouracil", "5 fu": "fluorouracil",
    "5-fluorouracil": "fluorouracil", "5fluorouracil": "fluorouracil",
    "nab paclitaxel": "nab-paclitaxel", "nab- paclitaxel": "nab-paclitaxel",
    "albumin-bound paclitaxel": "nab-paclitaxel", "albumin bound paclitaxel": "nab-paclitaxel",
    "albumin paclitaxel": "nab-paclitaxel", "paclitaxel-albumin": "nab-paclitaxel",
    "nanoparticle albumin-bound paclitaxel": "nab-paclitaxel",
    "nanoparticle albumin-bound-paclitaxel": "nab-paclitaxel",
    "paclitaxel albumin-stabilized nanoparticle formulation": "nab-paclitaxel",
    "abraxane": "nab-paclitaxel",
    "folinic acid": "leucovorin", "calcium folinate": "leucovorin",
    "levofolinate": "levoleucovorin", "calcium levofolinate": "levoleucovorin",
    "pld": "pegylated liposomal doxorubicin", "liposomal doxorubicin": "pegylated liposomal doxorubicin",
    "doxil": "pegylated liposomal doxorubicin", "caelyx": "pegylated liposomal doxorubicin",
    "adriamycin": "doxorubicin", "dtic": "dacarbazine", "tmz": "temozolomide", "temodar": "temozolomide",
    "taxol": "paclitaxel", "taxotere": "docetaxel",
    "tas-102": "trifluridine/tipiracil", "lonsurf": "trifluridine/tipiracil",
    # other oncology abbreviations seen in pembrolizumab and melanoma trials
    "interleukin-2": "aldesleukin", "il-2": "aldesleukin", "il2": "aldesleukin",
    "proleukin": "aldesleukin",
    "ev": "enfortumab vedotin",
    "t-dxd": "trastuzumab deruxtecan", "ds-8201a": "trastuzumab deruxtecan",
    "enhertu": "trastuzumab deruxtecan",
    "dato-dxd": "datopotamab deruxtecan", "ds-1062a": "datopotamab deruxtecan",
    "skb264": "sacituzumab tirumotecan", "mk-2870": "sacituzumab tirumotecan",
    "sac-tmt": "sacituzumab tirumotecan",
    "t-vec": "talimogene laherparepvec", "tvec": "talimogene laherparepvec",
    "imlygic": "talimogene laherparepvec",
    "til": "tumor infiltrating lymphocytes", "tils": "tumor infiltrating lymphocytes",
    "pembro": "pembrolizumab", "nivo": "nivolumab", "ipi": "ipilimumab",
    "regn2810": "cemiplimab", "libtayo": "cemiplimab",
    # interferon brands and codes (spellings are handled by INTERFERON below)
    "intron a": "interferon alfa-2b", "intron-a": "interferon alfa-2b", "intron": "interferon alfa-2b",
    "sch 30500": "interferon alfa-2b", "mk-2958": "interferon alfa-2b",
    "pegintron": "peginterferon alfa-2b", "peg-intron": "peginterferon alfa-2b",
    "peg intron": "peginterferon alfa-2b", "sylatron": "peginterferon alfa-2b",
    "sch 54031": "peginterferon alfa-2b", "mk-4031": "peginterferon alfa-2b",
    "roferon-a": "interferon alfa-2a", "roferon": "interferon alfa-2a",
    "pegasys": "peginterferon alfa-2a", "actimmune": "interferon gamma-1b",
}

# Coformulations written with a separator that would otherwise split them in two.
COFORMULATIONS = [(r"trifluridine\s*(?:/|-|\+|\band\b)\s*tipiracil", "tas-102")]

# Regimen acronyms -> the drugs they stand for.
REGIMENS = {
    "folfox": ["oxaliplatin", "leucovorin", "fluorouracil"],
    "mfolfox6": ["oxaliplatin", "leucovorin", "fluorouracil"],
    "folfox6": ["oxaliplatin", "leucovorin", "fluorouracil"],
    "folfiri": ["irinotecan", "leucovorin", "fluorouracil"],
    "folfirinox": ["oxaliplatin", "irinotecan", "leucovorin", "fluorouracil"],
    "mfolfirinox": ["oxaliplatin", "irinotecan", "leucovorin", "fluorouracil"],
    "capox": ["capecitabine", "oxaliplatin"], "xelox": ["capecitabine", "oxaliplatin"],
    "ac": ["doxorubicin", "cyclophosphamide"], "ec": ["epirubicin", "cyclophosphamide"],
    "cmf": ["cyclophosphamide", "methotrexate", "fluorouracil"],
    "fec": ["fluorouracil", "epirubicin", "cyclophosphamide"],
    "flot": ["fluorouracil", "leucovorin", "oxaliplatin", "docetaxel"],
}

# Interferons are named by type, subtype and pegylation, each spelled many ways:
# "Recombinant Interferon Alfa-2b", "IFN-a2b", "IFNα2b", "interferon alpha -2b" -> "interferon
# alfa-2b"; "PegIFN alfa-2b", "Pegylated Interferon Alpha-2b" -> "peginterferon alfa-2b".
# Pegylated forms stay separate drugs, as in RxNorm and ATC. Only a whole name matches, so
# "VSV expressing interferon beta", "L-IFN adenovirus" and ropeginterferon keep their names.
INTERFERON = re.compile(
    r"^(?:(?:recombinant|human)\s+)*"
    r"(?P<peg>peg(?:ylated)?(?:[\s-]+|(?=interferon|ifn)))?"
    r"(?:interferon|ifn)"
    r"(?:[\s-]*(?P<type>alfa|alpha|α|beta|β|gamma|γ|a(?=[\s-]*\d|$)|g(?=[\s-]*\d)))?"
    r"(?:[\s-]*(?P<sub>[12][ab]|2|n3))?$")
INTERFERON_TYPES = {"alpha": "alfa", "α": "alfa", "a": "alfa", "β": "beta", "γ": "gamma", "g": "gamma"}

# Words describing route, form, dosing or setting, not the drug itself.
QUALIFIERS = r"\b(for injection|for infusion|fixed dose combination|fdc|" \
             r"iv|it|sc|im|po|oral|intravenous|intrathecal|intratumoral|intra-tumoral|" \
             r"subcutaneous|injection|infusion|injectable|product|tablets?|capsules?|i\.v\.?|" \
             r"treatment|alone|dose[- ]dense|\d+(st|nd|rd|th)[- ]line|first[- ]line|second[- ]line|" \
             r"phase\s*(i{1,3}|l{1,3}|[1-4][ab]?)|" \
             r"monotherapy|therapy|chemotherapy|chemo|combination|coformulation|co-formulation|" \
             r"coformulated|high-dose|high dose|low-dose|low dose|standard-dose|" \
             r"q\d+w|weekly|induction|maintenance|auc|regimen|cycles?|dose level \d+|" \
             r"solution|suspension|powder|concentrate|lyophilized)\b"
# Text after these words describes who or how, not what: "pemetrexed for nonsquamous NSCLC",
# "durvalumab followed by durvalumab", "tinostamustine over 60min".
TRAILING = r"(^|\s+)(for|over|every|followed by|given|administered|at|up to)\b.*$"
# FDA suffixes and prefixes on biologics: "deruxtecan-dlnk", "fam-trastuzumab", "bevacizumab-awwb".
BIOLOGIC_AFFIXES = [(r"(mab|can|tin|ine|ept)-[a-z]{4}\b", r"\1"), (r"^(fam|ado)-", "")]

DOSE = r"\b\d+(\.\d+)?\s*(mg/kg|mg/m\^?2|mg/ml|mcg/kg|µg/kg|mg|mcg|µg|ml|g|iu|units?|" \
       r"weeks?|days?|months?|years?|hours?|h|min|minutes)\b"
AUC = r"\b(auc|area under the curve)\s*(of\s*)?[-=]?\s*\d+(\.\d+)?\b"
COMBO_SPLIT = r"\s*(?:\bwith/without\b|/|\+|&|,|;|，|、|；|(?<=\s)-(?=\s)|\band\b|\bplus\b|\bor\b|\bwith\b)\s*"
BRACKETED = r"[(\[（](.*?)[)\]）]"

# Sourcing labels in biosimilar trials: "EU-Opdivo", "Opdivo-US", "US- sourced Opdivo".
REGION = r"(^(eu|us|uk)\s*-\s*(sourced\s+)?|\s*-\s*(eu|us|uk)$|\b(eu|us|uk)\s+sourced\b)"

# Salt forms name the same drug: "pemetrexed disodium", "dexamethasone sodium phosphate".
SALT = r"(\s+(hydrochloride|dihydrochloride|hcl|mesylate|dimesylate|tosylate|ditosylate|" \
       r"besylate|disodium|sodium|potassium|calcium|citrate|maleate|s-malate|malate|tartrate|" \
       r"bitartrate|phosphate|sulfate|sulphate|acetate|succinate|fumarate|bromide|" \
       r"hydrobromide|monohydrate|dihydrate|hydrate|dimethyl sulfoxide))+$"
# ...unless what remains is itself an element: "magnesium sulfate" stays whole.
ELEMENTS = {"sodium", "potassium", "calcium", "magnesium", "zinc", "iron", "ferrous", "ferric",
            "lithium", "copper", "aluminum", "aluminium", "ammonium", "barium", "silver", "gold"}

PLACEBO = re.compile(r"\bplacebos?\b")

# Too generic to be a drug node.
GENERIC = {"immunotherapy", "chemotherapy", "ici", "standard of care", "soc",
           "investigator's choice", "the combination of radiotherapy",
           "induction", "maintenance", "dose escalation", "dose expansion",
           "platinum", "radiotherapy", "radiation", "solution", "saline", "normal saline",
           "saline solution", "normal saline solution", "rescue medication",
           "supportive care", "best supportive care", "supportive care measures",
           "adc", "adcs", "nsai"}
# Words of a class description: "PD-1 inhibitor", "platinum-based", "physician's choice".
# A part containing one keeps only its specific names ("BRAF inhibitor dabrafenib" ->
# "dabrafenib", "Akt Inhibitor MK2206" -> "mk2206"); a pure description is dropped.
GENERIC_WORDS = re.compile(r"\b(inhibitors?|agents?|antibody|antibodies|doublet|"
                           r"choice|based|containing|non|cohorts?)\b")
# A specific name: a research code ("mk2206", "pf-04518600") but not a target ("cd40")...
CODE = re.compile(r"^[a-z]{1,6}-?\d{2,}[a-z0-9]*$")
TARGET = re.compile(r"^(cd|cdk|il|her|tlr|fgfr|pd|ctla|lag|tim|vegfr|parp|pi3k)-?\d+[a-z]?$")
# ...or a word ending in an international nonproprietary name (INN) stem.
INN_STEM = re.compile(r"(mab|nib|lib|platin|taxel|rubicin|tecan|trexed|abine|mustine|parib|"
                      r"ciclib|lisib|vedotin|zomib|domide|limus|relin|relix|lutamide|strozole|"
                      r"sone|olone|stat|cept|leukin|tide|fen|gliflozin|gliptin|glutide|statin|"
                      r"sartan|pril|olol|azole|mycin|cillin|floxacin|vir|setron|dipine|parin|"
                      r"xaban|gatran)$")
UNIT_ONLY = re.compile(r"^(mg|mcg|µg|g|ml|kg|m\^?2|iu|units?)$")
EDGE_WORDS = r"^((the|of|in|for|and|with|without)\s+)+|(\s+(the|of|in|for|and|with))+$"

# Drugs of one class that trials list side by side as options ("carboplatin or cisplatin",
# "pembrolizumab or nivolumab") but never give together. Arm-level co-occurrence would
# otherwise read each such arm as a combination. Small and curated: extend as found.
INTERCHANGEABLE = {
    "platinum agents": {"carboplatin", "cisplatin", "oxaliplatin", "nedaplatin", "lobaplatin"},
    "taxanes": {"paclitaxel", "nab-paclitaxel", "docetaxel", "cabazitaxel"},
    "PD-1/PD-L1 inhibitors": {"pembrolizumab", "nivolumab", "cemiplimab", "dostarlimab",
                              "retifanlimab", "tislelizumab", "toripalimab", "sintilimab",
                              "camrelizumab", "spartalizumab", "serplulimab", "penpulimab",
                              "zimberelimab", "atezolizumab", "durvalumab", "avelumab",
                              "adebrelimab", "sugemalimab", "envafolimab"},
    "CTLA-4 inhibitors": {"ipilimumab", "tremelimumab"},
    "fluoropyrimidines": {"fluorouracil", "capecitabine", "tegafur", "s-1"},
    "anthracyclines": {"doxorubicin", "epirubicin", "pegylated liposomal doxorubicin"},
    "EGFR inhibitors": {"erlotinib", "gefitinib", "afatinib", "osimertinib", "icotinib",
                        "dacomitinib", "almonertinib", "furmonertinib"},
    "BRAF inhibitors": {"vemurafenib", "dabrafenib", "encorafenib"},
    "MEK inhibitors": {"trametinib", "cobimetinib", "binimetinib"},
}


def interchangeable(a: str, b: str) -> str | None:
    """The class name if `a` and `b` are alternatives of one class, else None."""
    for name, members in INTERCHANGEABLE.items():
        if a in members and b in members:
            return name
    return None


def _split_hyphen_combo(part: str) -> list[str]:
    """'nivolumab-relatlimab' -> two drugs; 'nab-paclitaxel', 'bms-936558' stay whole."""
    pieces = part.split("-")
    if len(pieces) == 2 and all(p.isalpha() and len(p) >= 6 for p in pieces):
        return pieces
    return [part]


def _strip_salt(name: str) -> str:
    base = re.sub(SALT, "", name)
    return name if not base or base in ELEMENTS else base


def canonical(name: str) -> str:
    return SYNONYMS.get(name, name)


def interferon(name: str) -> str | None:
    """Canonical interferon name ("ifn-a2b" -> "interferon alfa-2b"), or None."""
    m = INTERFERON.match(name)
    if not m:
        return None
    kind = INTERFERON_TYPES.get(m["type"], m["type"])
    if not kind and m["sub"] and m["sub"].startswith("2"):
        kind = "alfa"                               # "PegIFN-2b": only alfa has 2a/2b
    out = "peginterferon" if m["peg"] else "interferon"
    if kind:
        out += f" {kind}" + (f"-{m['sub']}" if m["sub"] else "")
    return out


def _clean(part: str) -> str:
    part = re.sub(QUALIFIERS, " ", part)
    part = re.sub(TRAILING, "", part)
    for pattern, repl in BIOLOGIC_AFFIXES:
        part = re.sub(pattern, repl, part.strip())
    part = re.sub(REGION, " ", part.strip())
    part = " ".join(part.split()).strip(" -.,;:()[]（）")
    part = re.sub(EDGE_WORDS, "", part)
    return _strip_salt(part)


def _is_specific(word: str) -> bool:
    known = word in SYNONYMS or word in REGIMENS or word in SYNONYMS.values() \
        or any(word in members for members in INTERCHANGEABLE.values())
    code = bool(CODE.match(word)) and not TARGET.match(word)
    return known or code or (len(word) >= 6 and bool(INN_STEM.search(word)))


def _specific_names(part: str) -> str:
    """Keep only the specific names in a class description, or "" if there are none."""
    words = [re.sub(r"-(based|containing)$", "", w) for w in part.split()]
    return " ".join(w for w in words if _is_specific(w))


def _is_weak(name: str) -> bool:
    """A short unknown word: an abbreviation ("sg", "hdi") or a note ("us", "drug", "blank")."""
    return bool(re.fullmatch(r"[a-z]{1,5}", name)) and not _is_specific(name)


def _is_name(p: str) -> bool:
    return bool(p) and p not in GENERIC and not UNIT_ONLY.match(p) \
        and not TARGET.match(p) and bool(re.search(r"[a-z]", p))


def normalize_drug_names(name: str) -> list[str]:
    s = re.sub(r"[®™©]", "", name.lower())
    if s.strip().startswith("placebo"):
        return ["placebo"]

    # Text after a colon describes the cohort: "Nivolumab: Cohort 1 (Cutaneous)".
    # If nothing before it is a drug, the drugs come after: "Combination Therapy: PY314
    # + Pembrolizumab".
    head, colon, tail = s.partition(":")
    if colon and tail.strip():
        return _normalize(head) or _normalize(tail)
    return _normalize(s)


def _normalize(s: str) -> list[str]:
    for pattern, code in COFORMULATIONS:
        s = re.sub(pattern, code, s)

    # Bracketed text is often the generic or brand name: "BMS-936558 (Nivolumab)",
    # "Nivolumab Injection [Opdivo]". Keep it as extra candidates, but an abbreviation
    # ("Sacituzumab Govitecan (SG)", "high dose interferon alfa-2b (HDI)") or a note
    # ("Keytruda (US)") is not a second drug: in an arm it would draw a combination edge
    # between a drug and itself. So when both the text and its brackets give names, short
    # unknown words are dropped, unless nothing else is left.
    outside = _names(re.sub(BRACKETED, " ", s))
    inside = [n for piece in re.findall(BRACKETED, s) for n in _names(piece) if n not in outside]
    out = outside + list(dict.fromkeys(inside))
    if outside and inside:
        out = [n for n in out if not _is_weak(n)] or out
    return out


def _names(text: str) -> list[str]:
    """Drug names in text without brackets: split combinations, clean, resolve synonyms."""
    out: list[str] = []

    def add(p: str) -> None:
        if _is_name(p) and p not in out:
            out.append(p)

    text = re.sub(AUC, " ", re.sub(DOSE, " ", text))
    for part in re.split(COMBO_SPLIT, text):
        if PLACEBO.search(part):
            add("placebo")              # "olaparib matching placebo" is a placebo
            continue
        if " ".join(part.split()) in GENERIC:
            continue                    # checked before cleaning changes the words
        part = _clean(part)
        if ifn := interferon(part):
            add(ifn)
            continue
        if GENERIC_WORDS.search(part):
            part = _specific_names(part)
        if part in REGIMENS:
            for drug in REGIMENS[part]:
                add(drug)
        elif part in SYNONYMS:
            add(SYNONYMS[part])
        else:
            for p in _split_hyphen_combo(part):
                add(canonical(_strip_salt(p)))
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
