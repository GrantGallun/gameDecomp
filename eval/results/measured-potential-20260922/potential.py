"""Pre-registered function-level measured potential, computed before any extended-budget compile.

For each function the stage-2 `routed` arm left non-exact: sum, over its compiled non-exact search nodes,
of (candidates a family still offered but were never compiled from that node) x (that family's measured
improvement rate), with rates fitted on the OTHER fold of functions only. High potential = the budget ran
out while repairs that usually help were still waiting. The hypothesis under test: potential predicts which
functions a larger budget closes. Written to potential.json and hashed into the run's preregistration.
"""
import collections
import hashlib
import json
from pathlib import Path

from offline import OUT, edges, fit, fold, load, supported


def main():
    worlds = load()
    tables = {k: fit(worlds, 1 - k) for k in (0, 1)}        # rates for fold k come from fold 1-k
    tried = collections.defaultdict(collections.Counter)
    for function, arm, parent, children, _kids in edges(worlds):
        if arm == "routed":
            for c in children:
                tried[(function, parent["id"])][c["family"]] += 1
    scores = {}
    for (function, arm), nodes in worlds.items():
        if arm != "routed" or any(n["exact"] for n in nodes.values()):
            continue
        rate = tables[fold(function)][0]
        total, untried = 0.0, 0
        for n in nodes.values():
            for family, offered in (n.get("fires") or {}).items():
                left = max(0, offered - tried[(function, n["id"])][family])
                untried += left
                if left and supported(rate[family]):
                    total += min(left, 8) * rate[family][0] / rate[family][1]
        best = max((n["score"] for n in nodes.values() if n["compiled"]), default=0.0)
        scores[function] = {"potential": round(total, 4), "untried_candidates": untried, "best_score": best}
    ranked = sorted(scores, key=lambda f: -scores[f]["potential"])
    payload = {"rule": __doc__.strip().splitlines()[0], "functions": len(scores), "ranked": ranked, "scores": scores}
    text = json.dumps(payload, indent=1)
    (OUT / "potential.json").write_text(text)
    print(json.dumps({"functions": len(scores), "sha256": hashlib.sha256(text.encode()).hexdigest()[:16],
                      "top": [(f, scores[f]["potential"]) for f in ranked[:8]],
                      "zero_potential": sum(1 for f in ranked if scores[f]["potential"] == 0)}, indent=1))


if __name__ == "__main__":
    main()
