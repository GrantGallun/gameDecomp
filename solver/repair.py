"""Counterexample-guided deterministic repair. The solver's default path.

Every match this project has gained since the corpus was built came from a
deterministic repair driven by the oracle's own residual -- not from a model
sample. That machinery lived in evaluation scripts and was never reachable from
solve(), which imported only context, diagnose, llm, refine, siblings and
workspace. An external review named that as the largest structural gap in the
project, and it was right: the successful path was not in the product.

THE LOOP

    compile -> residual -> propose rewrites -> apply -> RE-DERIVE -> repeat

Two properties matter and both were learned by failing without them:

    COMPOSE. A fix often needs two simultaneous changes. Each of the rewrites
    that closed updateRaceSplitscreenSelectPlayerCountIcons moves the score by
    four hundredths of a point alone; only the pair matches. Greedy single-step
    search cannot see either as progress, which is why five single-step repair
    passes left the match count unmoved.

    RE-DERIVE. Composing two PRECOMPUTED rewrites is not enough. With arguments
    still swapped, `lh a2,0x26` and `lh a2,0x24` normalise alike, so the layout
    pass reads a bogus offset constraint out of what is really a register swap.
    The second rewrite has to come from the residual left by the first.

Score orders the search; it never admits or rejects. A rewrite that scores
WORSE alone can be half of the pair that matches -- the layout repair on
updateEndingLindaExitUntilPhase3C scores 99.205 against a 99.545 baseline and
is essential. Only the oracle decides anything.
"""

from __future__ import annotations

import itertools
from pathlib import Path

from solver import rewrites, workspace


def search(repo: Path, name: str, src: str, ws: Path, conn=None,
           max_pairs: int = 250, verbose: bool = True):
    """Repair `src` until the oracle verifies or the rewrites run out.

    Returns (best_attempt, best_source, log).
    """
    log: list[str] = []
    base = workspace.score(ws, repo, name, src)
    if not base.compiled:
        return base, src, ["baseline did not compile"]

    proposals = rewrites.propose(src, base.diff)
    if not proposals:
        return base, src, ["no applicable rewrite"]

    singles = []
    best_att, best_src = base, src
    for rw in proposals:
        cand = rw(src)
        if cand == src:
            continue
        att = workspace.score(ws, repo, name, cand, conn=conn, func=name,
                              strategy="repair", run_id="repair")
        if not att.compiled:
            continue
        if att.exact:
            return att, cand, log + [f"{rw.label}: EXACT alone"]
        # Kept even when it scores worse -- see the module docstring.
        singles.append((rw, cand, att.score))
        if att.score > best_att.score:
            best_att, best_src = att, cand

    singles.sort(key=lambda t: -t[2])
    log.append(f"{len(proposals)} proposed, {len(singles)} compiling")
    if verbose:
        for rw, _c, sc in singles[:8]:
            print(f"      {rw.label[:44]:44} {sc:8.3f}"
                  f" ({sc - base.score:+.3f})", flush=True)

    tried = 0
    for rw, cand, _sc in singles:
        if tried >= max_pairs:
            break
        first = workspace.score(ws, repo, name, cand)
        if not first.compiled:
            continue
        for rw2 in rewrites.propose(cand, first.diff):
            if tried >= max_pairs:
                break
            cand2 = rw2(cand)
            if cand2 == cand:
                continue
            tried += 1
            att = workspace.score(ws, repo, name, cand2, conn=conn, func=name,
                                  strategy="repair-pair", run_id="repair")
            if not att.compiled:
                continue
            if att.exact:
                log.append(f"PAIR EXACT: {rw.label} then {rw2.label}")
                return att, cand2, log
            if att.score > best_att.score:
                best_att, best_src = att, cand2
                log.append(f"pair improved: {rw.label} then {rw2.label} -> "
                           f"{att.score:.3f}")
    log.append(f"{tried} re-derived pairs tried")
    return best_att, best_src, log


# `itertools` is imported for callers that want to extend the search; keeping
# the reference explicit avoids a lint removal that would break them.
_ = itertools
