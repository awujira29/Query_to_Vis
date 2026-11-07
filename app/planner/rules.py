"""Rule-based planner: the question's wording -> a Plan, with no language model.

This is the fallback when no LLM is configured or when the LLM's plan fails
validation, and the baseline the LLM planner is measured against. It covers the
question classes in the assignment appendix (time trends, distributions,
comparisons, geography, networks) plus histograms and scatter plots, in three steps:

  1. intent      keyword cues, most specific first (scatter > network > comparison > ...)
  2. dimension   the words after a grouping cue ("by", "across", "which", "most common")
  3. entities    the phrases that name trials ("for X", "X trials", "A vs B"), typed as
                 drug, condition, country or sponsor by vocabulary, else by asking the
                 registry which search field finds more trials for the words

Everything it extracts is literally present in the question or the structured
fields, so it never invents an entity. With no intent cue it defaults to a phase
distribution of the trials it identified; with no trials identified at all it
returns None and the service answers "unsupported" rather than guessing.
"""
from __future__ import annotations

import logging
import re

from app.aggregate.measures import label as measure_label
from app.client import ApiError, count_studies
from app.drug_names import _is_specific
from app.labels import DISPLAY
from app.query import Filters
from app.schemas.plan import Measure, NodeType, Plan, SeriesSpec
from app.schemas.records import PHASE_LABELS, Phase
from app.schemas.request import QueryRequest

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

# Ordered: the first matching dimension wins, so specific phrases beat generic words.
DIMENSION_CUES: list[tuple[str, str]] = [
    (r"sponsor (?:class|type|categor\w*)|type of sponsor|funder|funding (?:source|type)|"
     r"industry[- ](?:vs|versus|or|and)|who funds|sponsor(?:ship)? (?:by )?(?:class|type)", "sponsor_class"),
    (r"sponsor|organi[sz]ation|compan(?:y|ies)|institution", "lead_sponsor"),
    (r"intervention type|type of intervention|kinds? of intervention", "intervention_type"),
    (r"study type|observational|interventional", "study_type"),
    (r"phase", "phase"),
    (r"countr|geograph|nation|region|location|where", "country"),
    (r"\bstatus\b|recruitment status|completed vs|terminated", "overall_status"),
    (r"allocation|randomi[sz]", "allocation"),
    (r"masking|blind", "masking"),
    (r"primary purpose|purpose", "primary_purpose"),
    (r"results (?:posted|available|reported)|have results|with results", "has_results"),
    (r"start year|year", "start_year"),
    (r"condition|disease|indication|diagnos", "condition"),
    (r"\bdrugs?\b|intervention|treatment|therap|medication|compound|agent", "drug"),
]

GROUPING_CUES = r"(?:by|across|per|among|between|of|in terms of|according to|in)"
# Where a dimension is named: the words right after a grouping cue.
DIMENSION_WINDOWS = [
    rf"\b(?:distribut\w*|broken down|breakdown|grouped|split|categori[sz]ed|share|proportion|"
    rf"percentage|number of trials)\s+{GROUPING_CUES}\s+(?:the\s+|each\s+|different\s+)?"
    r"([\w\- /]+?)(?=[?.,;]|\s+for\b|\s+in\b|\s+of\b|$)",
    r"\b(?:which|what)\s+(?:are\s+the\s+)?(?:most\s+common\s+|top\s+\d*\s*|leading\s+|biggest\s+|"
    r"largest\s+|main\s+)?([\w\- ]+?)\s+(?:have|has|run|runs|are|is|sponsor|conduct|host|do|does|"
    r"were|was|account|appear|fund|funds)\b",
    r"\b(?:most\s+common|most\s+frequent|top\s+\d*|leading|biggest|largest|main)\s+([\w\- ]+?)"
    r"(?=\s+(?:for|in|of|among|across|used|tested|studied)\b|[?.,;]|$)",
    rf"\b(?:compare|comparison of|compared)\s+(?:the\s+)?([\w\- ]+?)\s+{GROUPING_CUES}\b",
    rf"\b{GROUPING_CUES}\s+(?:the\s+|each\s+|different\s+)?([\w\- /]+?)"
    r"(?=[?.,;]|\s+for\b|\s+in\b|\s+of\b|\s+since\b|\s+over\b|$)",
]

MEASURE_CUES: list[tuple[str, Measure]] = [
    (r"enrol+ment|enrol+ed|sample size|participants|patients enrolled|how many (?:people|patients|participants)",
     "enrollment_count"),
    (r"duration|how long|length of|time to complet", "duration_months"),
    (r"\bsites?\b|facilit|centers?|centres?", "site_count"),
    (r"number of countries|countries per|how many countries", "country_count"),
    (r"\barms?\b", "arm_count"),
    (r"number of interventions|interventions per", "intervention_count"),
    (r"start year|\byear\b", "start_year"),
]

NODE_CUES: list[tuple[str, NodeType]] = [
    (r"sponsors?\b(?!\s+by)|organi[sz]ation|compan|institution|funder", "sponsor"),
    (r"condition|disease|indication", "condition"),
    (r"drug|intervention|treatment|therap|compound|medication|agent", "drug"),
]


def node_types_in(text: str) -> list[NodeType]:
    """Node types named in the text, in order of first appearance."""
    found = []
    for pattern, node_type in NODE_CUES:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            found.append((m.start(), node_type))
    return [t for _, t in sorted(found)]

STATUS_CUES: list[tuple[str, str]] = [
    (r"\b(?:currently |actively |now )?recruiting\b|open to enrol+ment|enrolling now", "RECRUITING"),
    (r"\bcompleted\b", "COMPLETED"),
    (r"\bterminated\b|stopped early", "TERMINATED"),
    (r"\bwithdrawn\b", "WITHDRAWN"),
    (r"\bsuspended\b", "SUSPENDED"),
    (r"not yet recruiting", "NOT_YET_RECRUITING"),
    (r"active,? not recruiting|ongoing but not recruiting", "ACTIVE_NOT_RECRUITING"),
]

# Words that describe trials or the chart but never name a drug, condition or place.
# Trimmed from the EDGES of a candidate phrase only, so "heart disease" keeps "disease".
EDGE_NOISE = {
    "a", "an", "the", "all", "any", "these", "those", "this", "that", "its", "their", "they",
    "them", "it", "there", "here", "trial", "trials", "study", "studies", "clinical",
    "registered", "ongoing", "active", "recruiting", "completed", "terminated", "withdrawn",
    "suspended", "new", "recent", "current", "currently", "interventional", "observational",
    "randomized", "randomised", "phase", "phases", "early", "late", "stage", "number",
    "count", "many", "most", "more", "much", "how", "what", "which", "where", "when", "who",
    "often", "frequently", "commonly", "typically", "typical", "usually", "involving",
    "testing", "tested", "studied", "treated", "using", "for", "of", "in", "on", "with",
    "about", "across", "by", "per", "over", "time", "year", "years", "since", "from",
    "between", "and", "or", "vs", "versus", "compared", "compare", "comparison", "to",
    "into", "distributed", "distribution", "breakdown", "sponsors", "sponsor", "drug",
    "drugs", "condition", "conditions", "diseases", "treatment", "treatments",
    "intervention", "interventions", "countries", "country", "show", "me", "plot", "chart",
    "graph", "network", "map", "list", "give", "tell", "started", "start", "run", "running",
    "conducted", "exist", "are", "is", "were", "was", "have", "has", "had", "do", "does",
    "did", "been", "being", "be", "results", "together", "combination", "combinations",
    "combined", "co-occur", "cooccur", "relationship", "relationships", "related", "each",
    "every", "total", "overall", "worldwide", "globally", "global", "common", "average",
    "median", "mean", "big", "large", "small", "long", "short", "enrollment", "enrolment",
    "duration", "size", "sizes", "participants", "patients", "people", "subjects", "adults",
    "children", "pediatric", "adult", "status", "statuses", "categories", "category",
    "class", "classes", "types", "type", "kinds", "kind", "funds", "fund", "funding",
    "funded", "located", "sites", "site", "locations", "location", "other", "various",
    "different", "top", "leading", "biggest", "largest", "main", "frequent", "involved",
    "tests", "test", "use", "used", "uses", "this", "these", "given", "paired", "linked",
}

# Connectors inside a captured phrase: only what follows the last one names the entity
# ("sponsor categories across breast cancer" -> "breast cancer").
CONNECTORS = r"\b(?:for|of|involving|testing|evaluating|using|with|on|about|across|among|in|into|by|per|within)\b"

CONDITION_HINTS = re.compile(
    r"cancer|carcinoma|tumou?r|neoplasm|lymphoma|leukemia|leukaemia|sarcoma|melanoma|"
    r"myeloma|glioma|blastoma|disease|disorder|syndrome|infection|itis\b|osis\b|emia\b|"
    r"pathy\b|algia\b|diabet|obes|asthma|covid|hiv|hepatitis|influenza|depress|anxiety|"
    r"schizo|alzheimer|parkinson|dementia|stroke|failure|hypertension|pain|injury|"
    r"fracture|sepsis|fibrosis|psoriasis|eczema|dermatitis|arthritis|sclerosis|anemia|"
    r"anaemia|insomnia|apnea|epilep|migraine|autism|adhd|copd|pneumonia|tuberculosis|"
    r"malaria|pregnan|birth|fertility|menopause|endometriosis|cirrhosis|colitis|crohn|"
    r"celiac|allerg|burn|wound|ulcer|glaucoma|cataract|degeneration|retinopathy|"
    r"deficiency|dystrophy|atrophy|palsy|cardiomyopathy|arrhythmia|fibrillation|"
    r"thrombosis|embolism|aneurysm|hemorrhage|haemorrhage|lupus|gout|kidney|renal|"
    r"liver|hepatic|cardiac|heart|lung|pulmonary|breast|prostate|colorectal|colon|"
    r"gastric|pancrea|ovarian|cervical|bladder|thyroid|skin|brain|bone|blood|vaccine")

DRUG_HINTS = re.compile(
    r"(?:mab|nib|lib|parib|ciclib|lisib|zomib|domide|limus|tide|gliflozin|gliptin|glutide|"
    r"statin|sartan|pril|olol|azole|mycin|cillin|floxacin|vir|setron|dipine|parin|xaban|"
    r"gatran|platin|taxel|rubicin|tecan|trexed|abine|mustine|vedotin|leukin|cept|"
    r"prazole|tidine|triptan|lukast|oxetine|pramine|azepam|zolam|phylline|terol|"
    r"sone|olone|dronate|fenac|profen|coxib|caine|curium)s?\b|"
    r"\b(?:aspirin|metformin|insulin|ibuprofen|acetaminophen|paracetamol|morphine|"
    r"ketamine|lithium|warfarin|heparin|prednisone|dexamethasone|cisplatin|carboplatin|"
    r"paclitaxel|docetaxel|gemcitabine|fluorouracil|capecitabine|oxaliplatin|irinotecan|"
    r"doxorubicin|cyclophosphamide|methotrexate|tamoxifen|letrozole|anastrozole|"
    r"semaglutide|tirzepatide|liraglutide|empagliflozin|dapagliflozin|sitagliptin|"
    r"atorvastatin|rosuvastatin|simvastatin|lisinopril|losartan|amlodipine|metoprolol|"
    r"omeprazole|pantoprazole|sertraline|fluoxetine|escitalopram|duloxetine|bupropion|"
    r"vitamin [a-k]\d?|vitamin d3?|omega-3|probiotics?|cannabidiol|cbd|psilocybin|mdma|"
    r"keytruda|opdivo|yervoy|tecentriq|imfinzi|humira|enbrel|remicade|herceptin|avastin|"
    r"rituxan|revlimid|imbruvica|xtandi|zytiga|lynparza|ibrance|kisqali|verzenio|"
    r"ozempic|wegovy|mounjaro|jardiance|farxiga|eliquis|xarelto|entresto)\b")

# Country names as ClinicalTrials.gov spells LocationCountry, with common aliases.
COUNTRIES = {c.lower(): c for c in [
    "United States", "Canada", "Mexico", "Brazil", "Argentina", "Chile", "Colombia", "Peru",
    "United Kingdom", "Ireland", "France", "Germany", "Spain", "Italy", "Portugal",
    "Netherlands", "Belgium", "Switzerland", "Austria", "Denmark", "Sweden", "Norway",
    "Finland", "Poland", "Czechia", "Hungary", "Romania", "Greece", "Israel", "Egypt",
    "South Africa", "Nigeria", "Kenya", "Tanzania", "Uganda", "Ethiopia", "Ghana",
    "Saudi Arabia", "United Arab Emirates", "Iran", "Pakistan", "India", "Bangladesh",
    "China", "Japan", "Taiwan", "Hong Kong", "Singapore", "Malaysia", "Thailand", "Vietnam",
    "Indonesia", "Philippines", "Australia", "New Zealand", "Russian Federation",
    "Turkey (Türkiye)", "Korea, Republic of", "Ukraine", "Serbia", "Bulgaria", "Croatia",
    "Slovakia", "Lithuania", "Latvia", "Estonia", "Cuba", "Puerto Rico",
]}
COUNTRIES.update({
    "usa": "United States", "us": "United States", "u.s.": "United States", "america": "United States",
    "united states of america": "United States", "uk": "United Kingdom", "britain": "United Kingdom",
    "great britain": "United Kingdom", "england": "United Kingdom", "south korea": "Korea, Republic of",
    "korea": "Korea, Republic of", "russia": "Russian Federation", "turkey": "Turkey (Türkiye)",
    "türkiye": "Turkey (Türkiye)", "czech republic": "Czechia", "holland": "Netherlands",
    "uae": "United Arab Emirates",
})


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _search(pattern: str, text: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _with(base: Filters, **changes) -> Filters:
    """A validated copy of `base` with fields changed (model_copy skips validation)."""
    return Filters.model_validate({**base.model_dump(), **changes})


def _dimension_from_words(words: str) -> str | None:
    for pattern, dim in DIMENSION_CUES:
        if _search(pattern, words):
            return dim
    return None


def _clean_entity(phrase: str) -> str:
    """'sponsor categories across the recruiting melanoma' -> 'melanoma'."""
    phrase = re.sub(r"[?.!,;:\"]", " ", phrase)
    parts = re.split(CONNECTORS, phrase, flags=re.IGNORECASE)
    phrase = parts[-1]
    phrase = re.sub(r"\b(?:phase\s*(?:[1-4]|i{1,3}v?|iv)(?:\s*/\s*(?:[1-4]|i{1,3}v?|iv))?)\b", " ",
                    phrase, flags=re.IGNORECASE)
    words = phrase.split()
    while words and words[0].lower().strip("'") in EDGE_NOISE:
        words.pop(0)
    while words and words[-1].lower().strip("'") in EDGE_NOISE:
        words.pop()
    return " ".join(words).strip(" -")


def country_name(words: str) -> str | None:
    return COUNTRIES.get(words.lower().strip())


def _entity_kind(words: str) -> str:
    """'drug', 'condition' or 'country' for a phrase: vocabulary first, registry second."""
    if country_name(words):
        return "country"
    low = words.lower()
    tokens = re.findall(r"[a-z0-9][a-z0-9\-]*", low)
    drug_like = any(_is_specific(t) for t in tokens) or DRUG_HINTS.search(low) is not None
    condition_like = CONDITION_HINTS.search(low) is not None
    if drug_like and not condition_like:
        return "drug"
    if condition_like:
        return "condition"
    try:
        as_drug = count_studies({"query.intr": words})
        as_condition = count_studies({"query.cond": words})
    except ApiError as e:
        logger.info("registry kind check unavailable for %r (%s); assuming condition", words, e)
        return "condition"
    return "drug" if as_drug > as_condition else "condition"


# ---------------------------------------------------------------------------
# Detection steps
# ---------------------------------------------------------------------------

def detect_intent(text: str) -> str | None:
    measures = {m for p, m in MEASURE_CUES if _search(p, text)} - {"start_year"}
    if _search(r"scatter|correlat|plotted against", text) or (
            len(measures) >= 2 and _search(r"relationship|between|against|\bvs\.?\b|versus|and", text)):
        return "scatter"
    if _search(r"network|co-?occur|combin(?:ed|ation)|together with|given with|paired with|"
               r"relationship[s]? between|links? between|connect|partner", text):
        return "network"
    if _search(r"\bwhich\b.+\bwhich\b", text) and len(node_types_in(text)) >= 2:
        return "network"
    if _search(r"\bcompar|\bvs\.?\b|\bversus\b|\bdiffer|side by side", text):
        return "comparison"
    if _search(r"over time|per year|each year|every year|by year|yearly|annual|trend|since \d{4}|"
               r"changed|evolv|growth|timeline|history|\bover the (?:last|past)\b|year by year", text):
        return "trend"
    if _search(r"histogram|distribution of (?:the )?(?:number of )?(?:enrol+ment|duration|sample size|"
               r"sites|arms|participants|countries)|"
               r"typical (?:enrol+ment|size|duration)|how (?:big|large|long)|how many (?:patients|"
               r"participants|people|sites)|enrol+ment sizes?|spread of", text):
        return "histogram"
    if _search(r"distribut|breakdown|broken down|by phase|across|most common|which countr|"
               r"which sponsor|which drug|which condition|top \d|leading|how many|how are|"
               r"what (?:phase|status|type)|share of|proportion|percentage|\bby\b|who funds|"
               r"\bstatus\b|where", text):
        return "distribution"
    return None


def detect_dimension(text: str, intent: str | None) -> str | None:
    for window in DIMENSION_WINDOWS:
        for m in re.finditer(window, text, flags=re.IGNORECASE):
            dim = _dimension_from_words(m.group(1))
            if dim and not (intent == "trend" and dim == "start_year"):
                return dim
    # No grouping cue: a strong dimension word anywhere still counts.
    strong = {"phase", "country", "sponsor_class", "intervention_type", "study_type",
              "overall_status", "allocation", "masking", "has_results"}
    for pattern, dim in DIMENSION_CUES:
        if dim in strong and _search(pattern, text):
            if dim == "overall_status" and _search(r"recruiting trials", text):
                continue                      # "recruiting" is a filter there, not a grouping
            return dim
    return None


def detect_measure(text: str, exclude: Measure | None = None) -> Measure | None:
    for pattern, measure in MEASURE_CUES:
        if measure != exclude and _search(pattern, text):
            return measure
    return None


def detect_statuses(text: str) -> list[str]:
    return [code for pattern, code in STATUS_CUES if _search(pattern, text)]


def detect_years(text: str) -> tuple[int | None, int | None]:
    start = end = None
    if m := re.search(r"\b(?:since|from|after|starting(?: in)?)\s+(\d{4})\b", text, flags=re.IGNORECASE):
        start = int(m.group(1))
    if m := re.search(r"\b(?:until|till|to|through|before|up to)\s+(\d{4})\b", text, flags=re.IGNORECASE):
        end = int(m.group(1))
    if m := re.search(r"\bbetween\s+(\d{4})\s+and\s+(\d{4})\b", text, flags=re.IGNORECASE):
        start, end = int(m.group(1)), int(m.group(2))
    if m := re.search(r"\b(?:over|in) the (?:last|past) (\d+|five|ten|two|three) years?\b", text,
                      flags=re.IGNORECASE):
        from datetime import date
        n = {"two": 2, "three": 3, "five": 5, "ten": 10}.get(m.group(1).lower(), None) or int(m.group(1))
        start = date.today().year - n
    if (m := re.search(r"\bin\s+(\d{4})\b", text, flags=re.IGNORECASE)) and not start and not end:
        start = end = int(m.group(1))
    return start, end


def detect_phases(text: str) -> list[Phase]:
    found: list[Phase] = []
    for m in re.finditer(r"\bphase\s*([1-4]|i{1,3}|iv)\b(?!\s*/)", text, flags=re.IGNORECASE):
        value = {"i": "1", "ii": "2", "iii": "3", "iv": "4"}.get(m.group(1).lower(), m.group(1))
        phase = Phase(f"PHASE{value}")
        if phase not in found:
            found.append(phase)
    return found


# A captured phrase stops at a cue word; "and"/"vs" stay inside so "A and B" is kept whole.
_STOP = (r"(?=\s+(?:trials?|studies|by|across|per|since|over|from|between|started|start|"
         r"changed|each|every|compared|distributed|that|which|where|who|run|have|has|are|is|"
         r"were|was|do|does|as|at|to|in|into|within|during|sponsored|funded)\b|[?.,;:]|$)")

ENTITY_PATTERNS = [
    # "sponsored by X", "funded by X", "run by X" name a sponsor
    (r"\b(?:sponsored|funded|run|conducted|led)\s+by\s+(?P<e>[A-Z][\w\-'&. ]*?)" + _STOP, "sponsor"),
    # "trials for X", "studies of X", "trials involving X", "trials testing X"
    (r"\b(?:trials?|studies|study)\s+(?:for|of|on|about|involving|testing|evaluating|using|with|in)\s+"
     r"(?P<e>[A-Za-z][\w\-' /]*?)" + _STOP, None),
    # "X trials", "recruiting X trials", "phase 3 X studies"
    (r"(?P<e>[A-Za-z][\w\-' /]*?)\s+(?:trials?|studies)\b", None),
    # "for X", "of X", "involving X", "combined with X", "in X" at the end or before a cue
    (r"\b(?:for|of|involving|testing|evaluating|with|using|on|about|combined with|together with|in)\s+"
     r"(?P<e>[A-Za-z][\w\-' /]*?)" + _STOP, None),
]


def extract_entities(text: str) -> list[tuple[str | None, str]]:
    """(forced kind or None, phrase) for every entity phrase, in order of appearance."""
    found: list[tuple[str | None, str]] = []
    seen: set[str] = set()
    for pattern, kind in ENTITY_PATTERNS:
        for m in re.finditer(pattern, text, flags=re.IGNORECASE):
            phrase = m.group("e")
            parts = [phrase] if kind == "sponsor" else re.split(
                r"\s+(?:vs\.?|versus|and|or)\s+|\s*,\s*", phrase)
            for part in parts:
                cleaned = _clean_entity(part)
                if cleaned and (len(cleaned) >= 3 or country_name(cleaned)) and cleaned.lower() not in seen:
                    seen.add(cleaned.lower())
                    found.append((kind, cleaned))
    return found


# ---------------------------------------------------------------------------
# The planner
# ---------------------------------------------------------------------------

DIMENSION_NAMES = {
    "phase": "phase", "overall_status": "status", "sponsor_class": "sponsor class",
    "lead_sponsor": "lead sponsor", "country": "country", "drug": "drug",
    "condition": "condition", "intervention_type": "intervention type",
    "study_type": "study type", "start_year": "start year", "allocation": "allocation",
    "masking": "masking", "primary_purpose": "primary purpose", "has_results": "results posted",
}


def _subject(filters: Filters) -> str:
    """A noun phrase for titles: 'Recruiting melanoma trials with nivolumab in Germany'."""
    words = []
    if filters.status:
        words.append(" and ".join(DISPLAY["overall_status"].get(c, c).lower() for c in filters.status))
    if filters.phases:
        words.append("phase " + "/".join(PHASE_LABELS[p].replace("Phase ", "").lower() for p in filters.phases))
    if filters.condition:
        words.append(filters.condition)
    elif filters.drug:
        words.append(filters.drug)
    words.append("trials")
    if filters.condition and filters.drug:
        words.append(f"with {filters.drug}")
    if filters.sponsor:
        words.append(f"sponsored by {filters.sponsor}")
    if filters.country:
        words.append(f"in {filters.country}")
    text = " ".join(words)
    return text[:1].upper() + text[1:]


TRIAL_WORDS = re.compile(
    r"trial|stud(?:y|ies)|clinical|drug|treatment|therap|intervention|sponsor|phase|recruit|"
    r"enrol+|patient|participant|placebo|disease|condition|cancer|dose|efficacy|safety|"
    r"nct\d+|fda|approv|compound|vaccine|medication|medicine|indication|cohort|arm\b",
    re.IGNORECASE)


def about_trials(text: str, base: Filters) -> bool:
    """A question is in scope if it mentions trials, names a known kind of entity, or
    comes with structured fields; "What is the weather in Boston?" is none of these."""
    if base.model_dump(exclude_defaults=True) or TRIAL_WORDS.search(text):
        return True
    return DRUG_HINTS.search(text.lower()) is not None or CONDITION_HINTS.search(text.lower()) is not None


def rules_plan(request: QueryRequest) -> Plan | None:
    text = " ".join(request.query.split())
    base = request.to_filters()
    if not about_trials(text, base):
        return None

    intent = detect_intent(text)
    dimension = request.dimension or detect_dimension(text, intent)
    start_year, end_year = detect_years(text)

    # Structured fields win; the text fills in what they leave empty.
    base = _with(base,
                 status=base.status or detect_statuses(text),
                 start_year=base.start_year or start_year,
                 end_year=base.end_year or end_year,
                 phases=base.phases or detect_phases(text))

    # Entities named in the text, typed. Skip kinds the structured fields already pin,
    # except in comparisons, where the text names the series.
    typed: list[tuple[str, str]] = []
    for forced, words in extract_entities(text):
        kind = forced or _entity_kind(words)
        if kind == "country":
            words = country_name(words) or words
        if getattr(base, kind) is None or intent == "comparison":
            if (kind, words.lower()) not in {(k, w.lower()) for k, w in typed}:
                typed.append((kind, words))

    if intent == "comparison":
        counts = {k: sum(1 for kk, _ in typed if kk == k) for k, _ in typed}
        axis = next((k for k in ("drug", "condition", "country", "sponsor") if counts.get(k, 0) >= 2), None)
        if axis:
            entities = [(k, w) for k, w in typed if k == axis][:4]
            shared = _with(base, **{axis: None})
            for kind, words in typed:
                if kind != axis and getattr(shared, kind) is None:
                    shared = _with(shared, **{kind: words})
            series = [SeriesSpec(label=w, filters=_with(shared, **{axis: w})) for _, w in entities]
            dimension = dimension or "phase"
            if dimension == axis:
                dimension = "phase"
            if dimension == "start_year":
                return Plan(intent="trend", series=series,
                            title=f"Trials started per year: {' vs '.join(w for _, w in entities)}",
                            interpretation=f"Compare trials per start year for "
                                           f"{' vs '.join(w for _, w in entities)}.")
            return Plan(intent="comparison", series=series, dimension=dimension, top_n=request.top_n,
                        title=f"{DIMENSION_NAMES[dimension].capitalize()}: "
                              f"{' vs '.join(w for _, w in entities)}",
                        interpretation=f"Compare {DIMENSION_NAMES[dimension]} across "
                                       f"{' vs '.join(w for _, w in entities)}.")
        intent = "distribution"               # nothing to compare: describe what we have

    # Single series: the first entity of each kind becomes a filter.
    filters = base
    for kind, words in typed:
        if getattr(filters, kind) is None:
            filters = _with(filters, **{kind: words})
    if not filters.model_dump(exclude_defaults=True):
        return None                           # nothing identifies a set of trials
    subject = _subject(filters)
    label = filters.condition or filters.drug or filters.sponsor or filters.country or "trials"
    series = [SeriesSpec(label=label, filters=filters)]

    if intent == "network":
        nodes = node_types_in(text)
        if filters.drug and _search(r"combin|together|co-?occur|given with|paired", text):
            pair: tuple[NodeType, NodeType] = ("drug", "drug")
        elif len(nodes) >= 2:
            pair = (nodes[0], nodes[1])
        elif nodes and nodes[0] != "drug":
            pair = (nodes[0], "drug")
        else:
            pair = ("drug", "drug") if filters.drug else ("sponsor", "drug")
        title = (f"Drugs given together with {filters.drug}" if pair == ("drug", "drug")
                 else f"{pair[0].capitalize()}s and {pair[1]}s in {subject[:1].lower() + subject[1:]}")
        return Plan(intent="network", series=series, network=pair, top_n=request.top_n, title=title,
                    interpretation=f"Network linking {pair[0]} and {pair[1]} entities of these trials.")

    if intent == "trend" or (dimension == "start_year" and intent in (None, "distribution")):
        return Plan(intent="trend", series=series, title=f"{subject} started per year",
                    interpretation="Number of trials by start year.")

    if intent == "histogram":
        measure = detect_measure(text, exclude="start_year") or "enrollment_count"
        return Plan(intent="histogram", series=series, measure=measure,
                    title=f"{measure_label(measure)} of {subject[:1].lower() + subject[1:]}",
                    interpretation=f"Distribution of {measure_label(measure).lower()} per trial.")

    if intent == "scatter":
        x = detect_measure(text) or "start_year"
        y = detect_measure(text, exclude=x) or ("enrollment_count" if x != "enrollment_count"
                                                 else "duration_months")
        return Plan(intent="scatter", series=series, x=x, y=y, color="phase",
                    title=f"{measure_label(y)} vs {measure_label(x).lower()} for "
                          f"{subject[:1].lower() + subject[1:]}",
                    interpretation=f"One point per trial: {measure_label(x).lower()} against "
                                   f"{measure_label(y).lower()}, colored by phase.")

    dimension = dimension or "phase"
    if (dimension == "drug" and filters.drug and not filters.condition) or \
            (dimension == "condition" and filters.condition and not filters.drug):
        dimension = "phase"                   # "trials for X by X" is not a grouping
    return Plan(intent="distribution", series=series, dimension=dimension, top_n=request.top_n,
                title=f"{subject} by {DIMENSION_NAMES[dimension]}",
                interpretation=f"Trials grouped by {DIMENSION_NAMES[dimension]}.")
