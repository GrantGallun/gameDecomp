"""Best-first search that may cross a plateau: up to `plateau` consecutive edits that leave the gradient unchanged.

Every other search here keeps a child only if it beats its parent (site_edits.search) or ranks it in a beam by gradient
(regalloc_search). Neither can take a step that changes nothing measurable. But a match can need two edits that are
each neutral alone. loadRaceMotionJointAnimationFrame (2026-09-30), register distance 1:
  - inlining the call into its only use: (0, 0, 1), and the listing is unchanged;
  - putting the loaded offset before the pointer in the addition: (0, 0, 1), unchanged;
  - both together: exact.
The residual is one `addu` operand order. It moves only when the call's value is an expression temporary AND the
loaded operand comes first. A greedy step sees two no-ops.

The search pops the state with the lowest (gradient, plateau steps used). It expands a state with every generator the
project has: regalloc_mutations.variants, site_edits.propose (typed, shape and mined lanes) and
rewrite_library.all_variants. It compiles children in order of how much residual weight their edit touches. A child
that improves resets the plateau count; one that ties is kept with the count plus one, up to `plateau`. Children that
reproduce the parent's listing (inert) are kept too, because an inert edit can be exactly the enabling one.
"""
from __future__ import annotations

import heapq
import itertools


def _children(code: str, function: str, attempt, mined: bool = True, llm_repo=None):
    """(label, kind, child source) from every generator, de-duplicated, each generator's own order kept."""
    from solver import regalloc_mutations, rewrite_library, site_edits
    diff = attempt.diff or ""
    out, seen = [], {code}

    def add(label, kind, child):
        if child and child not in seen:
            seen.add(child)
            out.append((label, kind, child))
    try:
        edits, _receipt = site_edits.propose(code, function, diff, attempt.source_attribution, mined=mined)
        for e in edits:
            add(e.label, e.kind, e.apply(code))
    except Exception:
        pass
    try:
        for label, kind, child in itertools.islice(regalloc_mutations.variants(code, function, diff), 400):
            add(label, kind, child)
    except Exception:
        pass
    try:
        for rule, label, child in rewrite_library.all_variants(code, function):
            add(label, "rewrite:" + rule, child)
    except Exception:
        pass
    if llm_repo is not None:
        from solver import nearmiss_llm
        try:
            for label, kind, child in nearmiss_llm.children(code, function, diff, llm_repo):
                add(label, kind, child)
        except RuntimeError:
            raise                              # contamination is loud
        except Exception:
            pass
    return out


def _site_weight(code: str, child: str, sites) -> float:
    from solver import residual_sites
    if not sites:
        return 0.0
    try:
        region = residual_sites.edit_region(code, child)
    except Exception:
        return 0.0
    return sum(w for line, w in sites.items() if region["start_line"] <= line <= region["end_line"])


def _family(kind: str) -> str:
    kind = str(kind)
    return kind.split(":")[0] if not kind.startswith(("mined:", "rewrite:")) else ":".join(kind.split(":")[:2])


def _order(code: str, kids: list, sites) -> list:
    """Round-robin across families, each family's children ordered by residual weight touched.

    Sorting all children by weight alone let one family fill every slot: on allocMenuRenderScratch (2026-09-30) 139 of
    240 compiles went to `decl` retypings, most of which made things worse."""
    groups: dict[str, list] = {}
    for k in sorted(kids, key=lambda k: -_site_weight(code, k[2], sites)):
        groups.setdefault(_family(k[1]), []).append(k)
    # families with a child on a residual line go first
    queues = sorted(groups.values(), key=lambda q: -_site_weight(code, q[0][2], sites))
    out = []
    for i in range(max(map(len, queues), default=0)):
        out.extend(q[i] for q in queues if i < len(q))
    return out


def search(score, source: str, function: str, *, budget: int = 240, plateau: int = 2, per_state: int = 48,
           mined: bool = True, llm_repo=None) -> dict:
    """`score(code, label, parent_code)` compiles and returns a workspace.Attempt (as site_edits.search)."""
    from solver import site_edits, workspace
    gradient = site_edits.gradient
    base = score(source, "baseline", None)
    trail = []
    if workspace.repair_complete(base):
        return {"exact": True, "source": source, "compiles": 1, "trail": trail,
                "baseline_gradient": [0, 0, 0], "best_gradient": [0, 0, 0]}
    counter = itertools.count()
    heap = [(gradient(base), 0, next(counter), source, base)]
    seen = {source}
    best_code, best = source, base
    spent = 1
    expanded = 0
    while heap and spent < budget:
        g, steps, _n, code, attempt = heapq.heappop(heap)
        expanded += 1
        sites, _status = site_edits.site_lines(attempt.diff or "", attempt.source_attribution)
        kids = _order(code, _children(code, function, attempt, mined=mined, llm_repo=llm_repo), sites)
        for label, kind, child in kids[:per_state]:
            if spent >= budget:
                break
            if child in seen:
                continue
            seen.add(child)
            spent += 1
            a = score(child, f"plateau:{kind}:{label}"[:110], code)
            cg = gradient(a)
            trail.append({"depth_steps": steps, "kind": kind, "label": str(label)[:80], "gradient": list(cg)})
            if workspace.repair_complete(a):
                return {"exact": True, "source": child, "compiles": spent, "trail": trail, "expanded": expanded,
                        "baseline_gradient": list(gradient(base)), "best_gradient": [0, 0, 0]}
            if not a.compiled:
                continue
            if cg < g:
                heapq.heappush(heap, (cg, 0, next(counter), child, a))
            elif cg == g and steps < plateau:
                heapq.heappush(heap, (cg, steps + 1, next(counter), child, a))
            if cg < gradient(best):
                best_code, best = child, a
    return {"exact": False, "source": best_code, "compiles": spent, "trail": trail, "expanded": expanded,
            "baseline_gradient": list(gradient(base)), "best_gradient": list(gradient(best))}
