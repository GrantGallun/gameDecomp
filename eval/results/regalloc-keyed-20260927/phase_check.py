"""K3 failed in 7 functions, all at evaluation 100. Hypothesis: the search splits its budget into phase 1
(half, when enabling roots exist) and phase 2 (enabling roots); keyed evaluations are cheaper, so the keyed
arm is still in phase 1 where plain has moved on. Test with depths (phase-2 roots have depth 0):

  - plain's phase-1 labels are a prefix of keyed's phase-1 labels       (the key changed nothing on the path)
  - the rerun's plain path equals the original run's plain path        (the search is deterministic)

    python3 phase_check.py ORIGINAL/results.jsonl RERUN/results.jsonl
"""
import json
import sys

original = {json.loads(l)["function"]: json.loads(l) for l in open(sys.argv[1])}
rerun = [json.loads(l) for l in open(sys.argv[2])]


def phases(arm):
    labels, depths = arm["path"], arm["depths"]
    cut = next((i for i, d in enumerate(depths) if d == 0), len(labels))
    return labels[:cut], labels[cut:]


for r in rerun:
    fn = r["function"]
    plain, keyed = r["arms"]["plain"], r["arms"]["keyed"]
    p1, p2 = phases(plain)
    k1, k2 = phases(keyed)
    prefix = k1[:len(p1)] == p1
    same_as_before = plain["path"] == original[fn]["arms"]["plain"]["path"]
    n = min(len(p2), len(k2))
    print(json.dumps({"function": fn, "plain phase-1": len(p1), "keyed phase-1": len(k1),
                      "plain phase-1 is a prefix of keyed phase-1": prefix,
                      "plain path reproduces the original run": same_as_before,
                      "phase-2 identical over the shorter": p2[:n] == k2[:n],
                      "final best label differs": plain.get("best_label") != keyed.get("best_label")}))
