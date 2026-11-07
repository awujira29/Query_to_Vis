"""network: TrialRecords -> Graph, for network graphs.

How two nodes are linked depends on their types:
- drug <-> drug: drugs given in the same ARM. Drugs in different arms of one trial are
  compared, not combined (NCT05064059: favezelimab/pembrolizumab versus regorafenib or
  TAS-102), so trial-level co-occurrence would draw false combination edges.
- two different types (sponsor <-> drug, condition <-> drug, sponsor <-> condition):
  entities of the same TRIAL. "sponsor" means the lead sponsor.

Weights count distinct trials, never arms or sites: a trial with four similar arms
counts once. Every node and edge keeps its NCT IDs and a capped sample of citations.

Raw graphs are hairballs, so the graph is pruned: the top_n nodes of each type by
linked trials, edges in at least min_edge_weight trials, at most max_edges edges, and
no node left without an edge. The focus node (e.g. the drug the question filters on)
is always kept. Every threshold and every drop is reported in notes.
"""
import re
from collections import Counter
from itertools import combinations

from app.aggregate.citations import MAX_CITATIONS, cite, evidence
from app.aggregate.count import MAX_IDS_PER_DATUM
from app.dimensions import DIMENSIONS
from app.drug_names import interchangeable, normalize_drug_names
from app.normalize import normalize_name, normalize_term
from app.schemas.records import NON_DRUG_NAMES, TrialRecord
from app.schemas.shapes import Citation, Edge, Graph, Node

# Node type -> the dimension that gives its keys and its citations.
NODE_TYPES = {"drug": "drug", "sponsor": "lead_sponsor", "condition": "condition"}
TYPE_NAMES = {"drug": "drug", "sponsor": "lead sponsor", "condition": "condition"}


def node_id(node_type: str, key: str) -> str:
    return f"{node_type}:{key}"


def focus_id(node_type: str, words: str) -> str | None:
    """Node id for the entity a question is about, e.g. ("drug", "Keytruda") -> "drug:pembrolizumab"."""
    if node_type == "drug":
        names = normalize_drug_names(words)
        return node_id("drug", names[0]) if names else None
    if node_type == "sponsor":
        return node_id("sponsor", normalize_name(words))
    return node_id(node_type, normalize_term(words))


def _spellings(record: TrialRecord, node_type: str, key: str) -> list[str]:
    """Exact API text naming this entity in this record, for display labels."""
    if node_type == "drug":
        return [re.sub(r"[®™©]", "", iv.name).strip() for iv in record.interventions
                if iv.is_drug_like and iv.names_normalized == [key]]
    if node_type == "sponsor":
        return [record.lead_sponsor.name] if record.lead_sponsor else []
    return [c.term for c in record.conditions if c.origin == "mesh" and c.term_normalized == key]


def _label(node_type: str, key: str, spellings: Counter) -> str:
    """Most common exact spelling; for drugs, one that is the name itself ("Pembrolizumab",
    not "Pembrolizumab 200 mg"), else a tidied key ("MK-4830", "Trifluridine/tipiracil")."""
    if node_type == "drug":
        exact = [s for s, _ in spellings.most_common() if s.lower() == key]
        if exact:
            best = exact[0]                     # "aldesleukin" in older records -> "Aldesleukin"
            return best[:1].upper() + best[1:] if best.islower() else best
        return key.upper() if re.search(r"\d", key) and " " not in key else key[:1].upper() + key[1:]
    return spellings.most_common(1)[0][0] if spellings else key


def _evidence(record: TrialRecord, node_type: str, key: str) -> Citation | None:
    ev = evidence(record, NODE_TYPES[node_type], key)
    return cite(record, *ev) if ev else None


def _n_trials(n: int) -> str:
    return f"{n} trial" if n == 1 else f"{n} trials"


def _capped(records: list[TrialRecord]) -> tuple[list[str], bool]:
    ids = [r.nct_id for r in records]
    return ids[:MAX_IDS_PER_DATUM], len(ids) > MAX_IDS_PER_DATUM


def network(records: list[TrialRecord], pair: tuple[str, str], focus: str | None = None,
            top_n: int = 20, min_edge_weight: int = 2, max_edges: int = 150) -> Graph:
    """Build a pruned graph linking two node types.

    pair:  ("drug", "drug"), or two different types from NODE_TYPES, e.g. ("sponsor", "drug").
    focus: a node id (see focus_id) that is always kept and flagged.
    top_n: nodes kept per node type, ranked by the number of trials linking them.
    """
    a_type, b_type = pair
    for t in pair:
        if t not in NODE_TYPES:
            raise ValueError(f"unknown node type: {t!r}")
    same = a_type == b_type
    if same and a_type != "drug":
        raise ValueError("same-type networks are only defined for drugs (same-arm combinations)")
    types = [a_type] if same else [a_type, b_type]
    # A drug network about one drug ("what is combined with pembrolizumab?") uses only arms
    # that give it: a control arm of carboplatin + paclitaxel is not a pembrolizumab regimen.
    focus_drug = focus.split(":", 1)[1] if same and focus and focus.startswith("drug:") else None

    edge_trials: dict[tuple[str, str], list[TrialRecord]] = {}
    edge_cites: dict[tuple[str, str], list[Citation]] = {}
    # Same-arm evidence per edge: (drugs in the arm, record position, record, arm). The
    # smallest arms are cited, since "Carboplatin + Paclitaxel" shows a combination more
    # clearly than an arm listing a menu of chemotherapy options.
    arm_evidence: dict[tuple[str, str], list[tuple]] = {}
    spellings: dict[str, Counter] = {}
    skipped: dict[str, set[str]] = {}          # interchangeable class -> trials

    def link(key: tuple[str, str], record: TrialRecord, cites: list[Citation | None]) -> None:
        edge_trials.setdefault(key, []).append(record)
        found = edge_cites.setdefault(key, [])
        cites = [c for c in cites if c]
        if len(found) + len(cites) <= MAX_CITATIONS:
            found.extend(cites)

    for pos, r in enumerate(records):
        if same:
            pairs = {}                          # (drug, drug) -> smallest arm giving both
            for arm in r.arms:
                drugs = sorted(set(arm.drugs_normalized) - NON_DRUG_NAMES)
                if focus_drug and focus_drug not in drugs:
                    continue
                for x, y in combinations(drugs, 2):
                    cls = interchangeable(x, y)
                    if cls:
                        skipped.setdefault(cls, set()).add(r.nct_id)
                    elif (x, y) not in pairs or len(drugs) < len(pairs[(x, y)].drugs_normalized):
                        pairs[(x, y)] = arm
            for (x, y), arm in pairs.items():
                key = (node_id("drug", x), node_id("drug", y))
                edge_trials.setdefault(key, []).append(r)
                arm_evidence.setdefault(key, []).append((len(arm.drugs_normalized), pos, r, arm))
            for k in {d for p in pairs for d in p}:
                spellings.setdefault(node_id("drug", k), Counter()).update(_spellings(r, "drug", k))
        else:
            a_keys = DIMENSIONS[NODE_TYPES[a_type]](r)
            b_keys = DIMENSIONS[NODE_TYPES[b_type]](r)
            for x in a_keys:
                for y in b_keys:
                    link((node_id(a_type, x), node_id(b_type, y)), r,
                         [_evidence(r, a_type, x), _evidence(r, b_type, y)])
            if a_keys and b_keys:
                for t, keys in ((a_type, a_keys), (b_type, b_keys)):
                    for k in keys:
                        spellings.setdefault(node_id(t, k), Counter()).update(_spellings(r, t, k))

    # Trials linking each node to any other (before pruning), in record order.
    position = {r.nct_id: i for i, r in enumerate(records)}
    linked: dict[str, dict[str, TrialRecord]] = {}
    for (u, v), trials in edge_trials.items():
        for nid in (u, v):
            bucket = linked.setdefault(nid, {})
            for r in trials:
                bucket.setdefault(r.nct_id, r)
    linked = {nid: dict(sorted(b.items(), key=lambda kv: position[kv[0]]))
              for nid, b in linked.items()}

    # Prune: top nodes per type, strong edges, then drop nodes left without an edge.
    keep: set[str] = set()
    ranked_counts = {}
    for t in types:
        ranked = sorted((n for n in linked if n.startswith(f"{t}:")),
                        key=lambda n: (-len(linked[n]), n))
        ranked_counts[t] = len(ranked)
        keep.update(ranked[:top_n])
    focus_found = focus in linked if focus else False
    if focus_found:
        keep.add(focus)

    strong = [(k, t) for k, t in edge_trials.items()
              if k[0] in keep and k[1] in keep and len(t) >= min_edge_weight]
    strong.sort(key=lambda kt: (-len(kt[1]), kt[0]))
    over_max = max(0, len(strong) - max_edges)
    strong = strong[:max_edges]

    degree = Counter()
    for (u, v), _ in strong:
        degree[u] += 1
        degree[v] += 1
    shown = [n for n in keep if degree[n] or n == focus]
    orphans = len(keep) - len(shown)

    nodes = []
    for nid in shown:
        t, key = nid.split(":", 1)
        members = list(linked[nid].values())
        ids, truncated = _capped(members)
        cites = [c for c in (_evidence(r, t, key) for r in members[:MAX_CITATIONS * 2]) if c]
        nodes.append(Node(id=nid, label=_label(t, key, spellings.get(nid, Counter())), type=t,
                          weight=len(members), degree=degree[nid], focus=nid == focus,
                          nct_ids=ids, nct_ids_truncated=truncated,
                          citations=cites[:MAX_CITATIONS]))
    nodes.sort(key=lambda n: (not n.focus, types.index(n.type), -n.weight, n.label))

    edges = []
    for (u, v), trials in strong:
        ids, truncated = _capped(trials)
        if same:
            best = sorted(arm_evidence[(u, v)], key=lambda e: (e[0], e[1]))[:MAX_CITATIONS]
            cites = [cite(r, f"{arm.source_path}.interventionNames", ", ".join(arm.intervention_names))
                     for _, _, r, arm in best]
        else:
            cites = edge_cites[(u, v)]
        edges.append(Edge(source=u, target=v, weight=len(trials), nct_ids=ids,
                          nct_ids_truncated=truncated, citations=cites))

    # Notes: how links are made, what was left out, and every pruning threshold.
    notes = []
    in_any_edge = {r.nct_id for trials in edge_trials.values() for r in trials}
    missing = sum(1 for r in records if r.nct_id not in in_any_edge)
    if same:
        notes.append("Edges link drugs given in the same trial arm. Drugs in different arms of "
                     "one trial are being compared, not combined, so they are not linked.")
        focus_name = _label("drug", focus_drug, spellings.get(focus, Counter())) if focus_drug else None
        if focus_drug:
            notes.append(f"Only arms that give {focus_name} are used, so every edge is part of a "
                         f"regimen including {focus_name}; other arms (e.g. control arms) are left out.")
        if missing:
            what = f"{focus_name} with another drug" if focus_drug else "two or more drugs together"
            notes.append(f"{missing} of {len(records)} trials add no edge: no arm gives {what}.")
        if skipped:
            classes = ", ".join(f"{cls} ({_n_trials(len(ids))})" for cls, ids in
                                sorted(skipped.items(), key=lambda kv: (-len(kv[1]), kv[0])))
            notes.append("Not linked: drugs of one class listed in the same arm, which are "
                         f"options rather than a combination (e.g. 'carboplatin or cisplatin'): "
                         f"{classes}.")
        notes.append("Arms that list a choice of drugs ('investigator's choice') can still "
                     "link options that are not in the curated list of interchangeable classes.")
    else:
        notes.append(f"Edges link each {TYPE_NAMES[a_type]} to each {TYPE_NAMES[b_type]} of the "
                     "same trial.")
        if "sponsor" in types:
            notes.append("Sponsors are lead sponsors; collaborators are not counted.")
        if missing:
            gaps = [f"{n} list no {TYPE_NAMES[t]}" for t, n in
                    ((b_type, sum(1 for r in records if not DIMENSIONS[NODE_TYPES[b_type]](r))),
                     (a_type, sum(1 for r in records if not DIMENSIONS[NODE_TYPES[a_type]](r))))
                    if n]
            notes.append(f"{missing} of {len(records)} trials add no edge: {', '.join(gaps)}.")
    if "drug" in types:
        placebo = sum(1 for r in records if any(
            iv.is_drug_like and set(iv.names_normalized) & NON_DRUG_NAMES for iv in r.interventions))
        if placebo:
            notes.append(f"Placebos are not counted as drugs (found in {_n_trials(placebo)}).")
    notes.append("Edge weight is the number of distinct trials with that link; node weight is "
                 "the number of trials linking the node to any other node.")
    if focus and focus_found:
        notes.append(f"'{_label(*focus.split(':', 1), spellings.get(focus, Counter()))}' is the "
                     "focus: the question selects trials for it, so it is always shown and "
                     "links to most other nodes.")
    elif focus:
        notes.append(f"Focus '{focus}' has no links in these trials.")
    kept_desc = " and ".join(f"{min(top_n, ranked_counts[t])} of {ranked_counts[t]} "
                             f"{TYPE_NAMES[t]}s" for t in types)
    notes.append(f"Showing the top {kept_desc} by linked trials, and edges found in at least "
                 f"{min_edge_weight} trials.")
    if over_max:
        notes.append(f"Only the {max_edges} strongest edges are shown ({over_max} more pass "
                     "the threshold).")
    if orphans:
        notes.append(f"{orphans} of the top nodes had no edge above the threshold and were removed.")
    if any(n.nct_ids_truncated for n in nodes) or any(e.nct_ids_truncated for e in edges):
        notes.append(f"NCT ID lists are capped at {MAX_IDS_PER_DATUM} per node or edge; "
                     "weight is always the full count.")

    return Graph(nodes=nodes, edges=edges, directed=False, node_types=types,
                 total_records=len(records), missing_count=missing, notes=notes)
