"""Entity resolution: the user's words -> verified MeSH terms -> ClinicalTrials.gov query.

Candidate sources, each of which can be wrong in its own way:
  - LLM       proposes MeSH terms by meaning     (wrong: invented or near-miss terms)
  - RxNorm    maps brand names to ingredients    (wrong: research codes, e.g. BMS-936558)
  - exact     the user's words as a MeSH name    (wrong: lay terms, abbreviations)
  - sample    MeSH headings assigned to the trials the words find, sharing a word
              with the query ("breast cancer" -> "Breast Neoplasms"); grounded by
              construction, but blind to synonyms ("heart attack" matches nothing)

One verifier checks every candidate against registry data, whatever its source:
  exists      the term set matches trials in the registry
  coverage    it describes >= MIN_COVERAGE % of trials the user's own words find
  not narrow  its registry size is >= MIN_RATIO of the text search's size
  not broad   no more specific term in the results covers nearly as much

Every candidate term is respelled the way the registry spells it before checking, so
the final query string does not depend on a proposer's capitalization.

The first verified candidate wins, in source order (LLM first: its ranking picked
Breast Neoplasms over Breast Diseases where the numbers tie). If nothing verifies,
the resolver falls back to a field search and says so; it never forces a doubtful term.

Evidence (scripts/evaluate_resolver.py, results in scripts/eval_*.json): with these
thresholds the resolver was right on 86 of 87 gold queries, including 25 of 25 held-out
ones, and 45 of 48 on a fresh set never used for tuning; the misses are queries with no
single honest MeSH term (chronic pain) or misspellings and research codes (alzhiemers,
ONO-4538), which fall back to a field search.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError

from app import llm
from app.client import get_json, offline
from app.filters import CANCER_WORDS, _tokens

logger = logging.getLogger(__name__)

Kind = Literal["condition", "drug"]

SAMPLE_SIZE = 300        # trials sampled from the user's own text search
MIN_COVERAGE = 50        # % of sampled trials the term set must cover (good >= 51, narrow <= 41)
MIN_RATIO = 0.4          # registry size / text-search size; below this the term is too narrow
NEARLY_AS_MUCH = 0.9     # a narrower term covering >= 90% of a broader one's trials...
NESTED = 0.95            # ...and co-occurring with it >= 95% of the time makes the broader one too broad
TIE_BAND = 0.02          # global sizes within 2% are a tie, broken by direct assignment
SUBSUME_RATIO = 3        # in a multi-term candidate, a term containing another term and more
                         # than 3x its registry size is a broader concept riding along: dropped

FIELDS = {
    "condition": {"query": "query.cond", "mesh": "ConditionMeshTerm",
                  "ancestor": "ConditionAncestorTerm", "browse": "conditionBrowseModule"},
    "drug": {"query": "query.intr", "mesh": "InterventionMeshTerm",
             "ancestor": "InterventionAncestorTerm", "browse": "interventionBrowseModule"},
}


# ---------------------------------------------------------------------------
# Result models (pydantic, so they can go straight into response metadata)
# ---------------------------------------------------------------------------

class CandidateCheck(BaseModel):
    terms: list[str]
    source: Literal["llm", "rxnorm", "exact", "sample"]
    reason: str = ""
    passed: bool
    coverage: float              # % of sampled text-search trials covered
    registry: int                # trials in the whole registry with these terms
    ratio: float                 # registry / text-search total
    rejected_because: list[str] = []
    dropped_terms: list[str] = []    # removed from a multi-term candidate before checking


class Resolution(BaseModel):
    kind: Kind
    query: str                                   # the user's words
    status: Literal["resolved", "fallback"]
    terms: list[str] = []                        # verified MeSH terms (empty on fallback)
    source: str | None = None                    # which candidate source won
    text_search_total: int = 0
    candidates: list[CandidateCheck] = []        # everything tried, for transparency
    api_params: dict[str, str] = Field(default_factory=dict)
    note: str = ""


# ---------------------------------------------------------------------------
# Registry access (a class, so tests can swap in a fake)
# ---------------------------------------------------------------------------

@dataclass
class Sample:
    trials: list[set[str]]       # per trial: lowercased MeSH terms + ancestors
    direct: Counter              # lowercased terms assigned directly (not as ancestors)
    total: int                   # text-search total in the registry
    names: dict[str, str] = field(default_factory=dict)   # lowercase -> spelling in the registry

    @property
    def counts(self) -> Counter:
        return Counter(t for tr in self.trials for t in tr)

    def observed_terms(self, n: int = 25) -> list[tuple[str, int, bool]]:
        """Most common terms in the sample: (spelling, % of trials, directly assigned?)."""
        size = max(len(self.trials), 1)
        return [(self.names.get(t, t), round(100 * c / size), self.direct.get(t, 0) > 0)
                for t, c in self.counts.most_common(n)]


class Registry:
    """ClinicalTrials.gov lookups used by the verifier (cached by the client)."""

    def sample(self, kind: Kind, words: str) -> Sample:
        f = FIELDS[kind]
        data = get_json("/studies", {f["query"]: words, "pageSize": SAMPLE_SIZE,
                                     "countTotal": "true",
                                     "fields": f"NCTId,{f['mesh']},{f['ancestor']}"})
        trials, direct, names = [], Counter(), {}
        for s in data.get("studies", []):
            browse = s.get("derivedSection", {}).get(f["browse"], {})
            raw_own = [m["term"] for m in browse.get("meshes", [])]
            raw_anc = [a["term"] for a in browse.get("ancestors", [])]
            names.update({t.lower(): t for t in raw_own + raw_anc})
            own = {t.lower() for t in raw_own}
            direct.update(own)
            trials.append(own | {t.lower() for t in raw_anc})
        return Sample(trials, direct, data.get("totalCount") or 0, names)

    def count(self, kind: Kind, terms: list[str]) -> int:
        data = get_json("/studies", {"filter.advanced": mesh_expression(kind, terms),
                                     "countTotal": "true", "pageSize": 1, "fields": "NCTId"})
        return data.get("totalCount") or 0


def mesh_expression(kind: Kind, terms: list[str]) -> str:
    """Trials whose MeSH term, or any MeSH ancestor, is one of `terms`."""
    f = FIELDS[kind]
    return " OR ".join(f'AREA[{f["mesh"]}]"{t}" OR AREA[{f["ancestor"]}]"{t}"' for t in terms)


def name_expression(names: list[str]) -> str:
    """Trials listing any of `names` as an intervention name or other name."""
    return " OR ".join(f'AREA[InterventionName]"{n}" OR AREA[InterventionOtherName]"{n}"'
                       for n in names)


# ---------------------------------------------------------------------------
# Candidate sources
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    terms: list[str]
    source: Literal["llm", "rxnorm", "exact", "sample"]
    reason: str = ""

    def key(self) -> tuple[str, ...]:
        return tuple(sorted(t.lower() for t in self.terms))


class Proposer(Protocol):
    def propose(self, kind: Kind, words: str, sample: Sample) -> list[Candidate]: ...


class _LLMCandidate(BaseModel):
    terms: list[str] = Field(min_length=1, max_length=3)
    reason: str = ""


class _LLMAnswer(BaseModel):
    candidates: list[_LLMCandidate] = Field(default_factory=list, max_length=3)


# Examples here are deliberately NOT from the evaluation gold set
# (tests/fixtures/resolution_gold.json), so the evaluation measures the model,
# not its ability to copy answers from the prompt.
LLM_SYSTEM = """You map clinical-trial search words to MeSH (Medical Subject Headings),
as stored by ClinicalTrials.gov. Return only a JSON object:
{"candidates": [{"terms": ["..."], "reason": "..."}]}

You are given the MeSH terms ClinicalTrials.gov actually assigned to trials that match
the words. Rules:
- Prefer terms from that list, spelled exactly as shown. ClinicalTrials.gov stores MeSH
  preferred headings, which often differ from common names or synonyms
  (e.g. "kidney stones" is stored as "Kidney Calculi").
- Choose the term that means what the user means, at the user's level of specificity:
  "gum disease" is Periodontal Diseases, not its subtype Gingivitis, and not the broader
  Mouth Diseases. A frequent term is not necessarily the right one: broad categories
  appear in many trials.
- A candidate may combine 2-3 terms when trials commonly use either one
  (e.g. ["Anemia", "Anemia, Iron-Deficiency"] for "anemia").
- For drugs, give the ingredient as MeSH stores it, not a brand (Tylenol -> Acetaminophen);
  salt forms are common (a drug may be stored as its "... Hydrochloride" or "... Mesylate").
- Up to 3 candidates, best first. Never invent terms."""


def _describe_sample(sample: Sample) -> str:
    lines = [f"- {name} ({pct}% of trials, {'assigned directly' if direct else 'broader category'})"
             for name, pct, direct in sample.observed_terms()]
    return (f"MeSH terms on {len(sample.trials)} sampled trials matching the words:\n"
            + ("\n".join(lines) if lines else "- (none: these trials have no MeSH terms)"))


class LLMProposer:
    """Proposes MeSH terms grounded in the terms actually found, and revises once on feedback."""

    def _ask(self, prompt: str, words: str) -> list[Candidate]:
        try:
            answer = _LLMAnswer.model_validate(llm.complete_json(LLM_SYSTEM, prompt))
        except (llm.LLMUnavailable, ValidationError) as e:
            logger.warning("LLM proposal failed for %r: %s", words, e)
            return []
        return [Candidate([t.strip() for t in c.terms if t.strip()], "llm", c.reason)
                for c in answer.candidates if any(t.strip() for t in c.terms)]

    def propose(self, kind: Kind, words: str, sample: Sample) -> list[Candidate]:
        return self._ask(f"type: {kind}\nwords: {words}\n\n{_describe_sample(sample)}", words)

    def revise(self, kind: Kind, words: str, sample: Sample,
               rejected: list[CandidateCheck]) -> list[Candidate]:
        """One more try, told exactly why each earlier candidate failed the registry checks."""
        feedback = "\n".join(f"- {c.terms}: {'; '.join(c.rejected_because)}" for c in rejected)
        prompt = (f"type: {kind}\nwords: {words}\n\n{_describe_sample(sample)}\n\n"
                  f"These candidates were checked against the registry and rejected:\n{feedback}\n"
                  "Propose different candidates, preferring exact terms from the list above. "
                  "Fix spelling or naming, but do not switch to a broader category than the "
                  "words mean just to pass the checks: if the precise heading was rejected, "
                  'return {"candidates": []}.')
        return self._ask(prompt, words)


class RxNormProposer:
    """Brand or generic name -> ingredient name via RxNorm. Exact/normalized match only:
    approximate matching resolved BMS-936558 to a different drug in testing."""

    BASE = "https://rxnav.nlm.nih.gov/REST"

    def propose(self, kind: Kind, words: str, sample: Sample | None = None) -> list[Candidate]:
        if kind != "drug" or offline():
            return []
        try:
            ids = httpx.get(f"{self.BASE}/rxcui.json", params={"name": words, "search": 2},
                            timeout=30).json().get("idGroup", {}).get("rxnormId") or []
            if not ids:
                return []
            related = httpx.get(f"{self.BASE}/rxcui/{ids[0]}/related.json",
                                params={"tty": "IN"}, timeout=30).json()
        except (httpx.HTTPError, ValueError) as e:
            logger.info("RxNorm lookup failed for %r: %s", words, e)
            return []
        names = [p["name"] for g in related.get("relatedGroup", {}).get("conceptGroup", []) or []
                 for p in g.get("conceptProperties", []) or []]
        if not names:
            return []
        return [Candidate(names[:3], "rxnorm", f"RxNorm ingredient of '{words}'")]


class ExactProposer:
    """The user's own words, which are often already a MeSH name ("Melanoma")."""

    def propose(self, kind: Kind, words: str, sample: Sample | None = None) -> list[Candidate]:
        return [Candidate([words.strip()], "exact", "the words themselves")]


class SampleProposer:
    """MeSH headings actually assigned to the sampled trials that contain the user's
    distinctive words: "breast cancer" -> Breast Neoplasms, Triple Negative Breast
    Neoplasms...; a cancer word in the query must be matched by a cancer word in the
    heading, so "Lung Diseases" is never proposed for "lung cancer".

    Grounded by construction (only headings that exist on matching trials), but it
    cannot see synonyms: "heart attack" proposes nothing and the resolver falls back.
    Ranking: directly assigned headings first, then the fewest words ("Melanoma" before
    "Nevi and Melanomas"), then the highest coverage. The verifier still rejects
    parents and subtypes that slip through.
    """

    MAX_CANDIDATES = 4
    TERMS_CONSIDERED = 60

    def propose(self, kind: Kind, words: str, sample: Sample) -> list[Candidate]:
        query = _tokens(words)
        if not query:
            return []
        needs_cancer = bool(query & CANCER_WORDS)
        distinctive = (query - CANCER_WORDS) or query
        ranked = []
        for term, pct, direct in sample.observed_terms(self.TERMS_CONSIDERED):
            term_words = _tokens(term)
            if not distinctive <= term_words:
                continue
            if needs_cancer and not term_words & CANCER_WORDS:
                continue
            ranked.append((not direct, len(term_words), -pct, term))
        ranked.sort()
        return [Candidate([term], "sample", f"MeSH heading on {-pct}% of the sampled trials")
                for _, _, pct, term in ranked[:self.MAX_CANDIDATES]]


def default_proposers() -> list[Proposer]:
    sources: list[Proposer] = [LLMProposer()] if llm.configured() else []
    return sources + [RxNormProposer(), ExactProposer(), SampleProposer()]


# ---------------------------------------------------------------------------
# The verifier
# ---------------------------------------------------------------------------

def _more_specific(term: str, sample: Sample, kind: Kind, registry: Registry) -> str | None:
    """A term in the sample that is narrower than `term` and covers nearly as much, or None.

    Inside the sample: nearly every trial with B also has `term`, and B covers >= 90%.
    Globally: B is not larger than `term` (a melanoma sample makes its parent "Skin
    Diseases" look nested inside it, but Skin Diseases is 9x larger overall). Within
    the 2% tie band, the term assigned directly to more trials wins (MeSH is a
    poly-hierarchy: Myocardial Infarction and its ancestor Necrosis can cover the
    same trials, but only Myocardial Infarction is assigned directly).
    """
    counts = sample.counts
    a = counts.get(term, 0)
    if not a:
        return None
    for b, nb in counts.most_common(60):
        if b == term or nb < NEARLY_AS_MUCH * a or nb > a:
            continue
        if sum(1 for t in sample.trials if b in t and term in t) < NESTED * nb:
            continue
        reg_a, reg_b = registry.count(kind, [term]), registry.count(kind, [b])
        if not reg_a or not reg_b:
            continue
        r = reg_b / reg_a
        if r < 1 - TIE_BAND:
            return b
        if r <= 1 + TIE_BAND and sample.direct.get(b, 0) > sample.direct.get(term, 0):
            return b
    return None


def _inside(b: str, a: str, sample: Sample) -> bool:
    """Nearly every sampled trial with term b also has term a (b sits under a)."""
    nb = sum(1 for t in sample.trials if b in t)
    return nb > 0 and sum(1 for t in sample.trials if b in t and a in t) >= NESTED * nb


def _prune(terms: list[str], kind: Kind, sample: Sample,
           registry: Registry) -> tuple[list[str], list[str]]:
    """Drop terms from a multi-term candidate that would silently broaden it.

    A union of a parent and its child is just the parent, so a candidate like
    ["Neoplasms", "Brain Neoplasms"] would really mean all neoplasms. A term is
    dropped when it contains another term of the same candidate (in the sample)
    and is more than SUBSUME_RATIO times its size in the registry. Close relatives
    (e.g. ["Sleep Apnea Syndromes", "Sleep Apnea, Obstructive"]) are kept.
    Terms with no trials at all are dropped too, as long as others remain.
    """
    terms = list(dict.fromkeys(terms))
    if len(terms) < 2:
        return terms, []
    sizes = {t: registry.count(kind, [t]) for t in terms}
    dropped = []
    for a in terms:
        low_a = a.lower()
        if sizes[a] == 0:
            dropped.append(a)
            continue
        for b in terms:
            if b != a and sizes[b] and sizes[a] > SUBSUME_RATIO * sizes[b] \
                    and _inside(b.lower(), low_a, sample):
                dropped.append(a)
                break
    kept = [t for t in terms if t not in dropped]
    return (kept, dropped) if kept else (terms, [])


def verify(cand: Candidate, kind: Kind, sample: Sample, registry: Registry) -> CandidateCheck:
    kept, dropped = _prune(cand.terms, kind, sample, registry)
    cand = Candidate(kept, cand.source, cand.reason)
    terms = [t.lower() for t in cand.terms]
    n = max(len(sample.trials), 1)
    coverage = 100 * sum(1 for t in sample.trials if any(x in t for x in terms)) / n
    reg = registry.count(kind, cand.terms)
    ratio = reg / sample.total if sample.total else 0.0

    reasons = []
    if reg == 0:
        reasons.append("no trials in the registry")
    if coverage < MIN_COVERAGE:
        reasons.append(f"covers only {coverage:.0f}% of trials found by the words")
    if ratio < MIN_RATIO:
        reasons.append(f"too narrow (registry size {ratio:.2f}x the text search)")
    if not reasons:
        for t in terms:
            narrower = _more_specific(t, sample, kind, registry)
            if narrower:
                reasons.append(f"too broad: '{t}' (more specific term covers nearly as much: "
                               f"{narrower})")

    return CandidateCheck(terms=cand.terms, source=cand.source, reason=cand.reason,
                          passed=not reasons, coverage=round(coverage, 1), registry=reg,
                          ratio=round(ratio, 2), rejected_because=reasons, dropped_terms=dropped)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def resolve(kind: Kind, words: str, proposers: list[Proposer] | None = None,
            registry: Registry | None = None) -> Resolution:
    registry = registry or Registry()
    proposers = default_proposers() if proposers is None else proposers
    sample = registry.sample(kind, words)

    seen: set[tuple[str, ...]] = set()
    checks: list[CandidateCheck] = []

    def try_all(candidates: list[Candidate]) -> CandidateCheck | None:
        for cand in candidates:
            # The registry's own spelling, so "melanoma", "Melanoma" and "MELANOMA" are
            # one candidate and build one query string.
            cand = Candidate([sample.names.get(t.lower(), t) for t in cand.terms],
                             cand.source, cand.reason)
            if not cand.terms or cand.key() in seen:
                continue
            seen.add(cand.key())
            check = verify(cand, kind, sample, registry)
            checks.append(check)
            if check.passed:
                return check
        return None

    for proposer in proposers:
        if hit := try_all(proposer.propose(kind, words, sample)):
            return _resolved(kind, words, hit, sample, checks)

    # Feedback round: proposers that can revise get the rejection reasons once.
    for proposer in proposers:
        revise = getattr(proposer, "revise", None)
        if revise and checks:
            if hit := try_all(revise(kind, words, sample, list(checks))):
                return _resolved(kind, words, hit, sample, checks)
    return _fallback(kind, words, sample, checks)


def condition_text_expression(words: str) -> str:
    """The words as a phrase (with UMLS synonyms) in the trial's own conditions list."""
    return f'AREA[Condition]EXPANSION[Concept]"{words}"'


def _resolved(kind, words, check, sample, checks) -> Resolution:
    expr = mesh_expression(kind, check.terms)
    if kind == "drug":
        # Also match by name: new compounds and code names may lack MeSH terms.
        names = list(dict.fromkeys([words, *check.terms]))
        expr = f"{expr} OR {name_expression(names)}"
    else:
        # Also match the conditions list itself: many records have no MeSH terms
        # assigned (D2 found ~9% of lung cancer trials missing from a MeSH-only query).
        expr = f"{expr} OR {condition_text_expression(words)}"
    note = (f"'{words}' resolved to MeSH {', '.join(repr(t) for t in check.terms)} "
            f"(source: {check.source}; verified: covers {check.coverage:.0f}% of trials found by "
            f"the words, {check.registry:,} trials in the registry, including subtypes).")
    if check.dropped_terms:
        note += (f" Broader terms dropped from the proposal: "
                 f"{', '.join(repr(t) for t in check.dropped_terms)}.")
    return Resolution(kind=kind, query=words, status="resolved", terms=check.terms,
                      source=check.source, text_search_total=sample.total, candidates=checks,
                      api_params={"filter.advanced": f"({expr})"}, note=note)


def _fallback(kind, words, sample, checks) -> Resolution:
    if kind == "drug":
        params = {"filter.advanced": f"({name_expression([words])})"}
        how = "intervention name and other-name fields"
    else:
        params = {"query.cond": f'EXPANSION[Concept] "{words}"'}
        how = "ClinicalTrials.gov concept search (synonyms, full phrase)"
    note = (f"Could not map '{words}' to a verified MeSH term "
            f"({len(checks)} candidate(s) rejected); matched by {how} instead.")
    return Resolution(kind=kind, query=words, status="fallback", text_search_total=sample.total,
                      candidates=checks, api_params=params, note=note)


def merge_params(*parts: dict[str, str]) -> dict[str, str]:
    """Combine query params; filter.advanced expressions are AND-ed together."""
    merged: dict[str, str] = {}
    advanced = []
    for p in parts:
        for k, v in p.items():
            if k == "filter.advanced":
                advanced.append(f"({v})" if not v.startswith("(") else v)
            elif k in merged:
                merged[k] = f"({merged[k]}) AND ({v})"
            else:
                merged[k] = v
    if advanced:
        merged["filter.advanced"] = " AND ".join(advanced)
    return merged
