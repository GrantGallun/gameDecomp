"""Scoreboard for the pilot's branch-style arms: each arm vs the control at equal cost, and
vs the plain tree arm on the same functions. Read-only.

    python3 arm_scoreboard.py
"""
import collections
import json
from pathlib import Path

N = Path("/home/grant/decomp/experiments/redraft-pilot-20260927")
ARMS = ["branch", "branch_eq", "branch_gated", "branch_gated_steer_filter_hybrid",
        "rescue_model_v2", "body", "body_s8_r0", "body_s4_r1"]


def load(arm):
    path = N / f"{arm}_results.jsonl"
    if not path.exists():
        return {}
    rows = [json.loads(l) for l in path.read_text().splitlines()]
    return {r["function"]: r for r in rows if r.get("status") == "done"}


def arm_part(r):
    return r.get("branch_arm") or r.get("rescue_arm") or r.get("body_arm") or {}


plain = load("branch")
for arm in ARMS:
    rows = load(arm)
    if not rows:
        continue
    vs_control, vs_plain = collections.Counter(), collections.Counter()
    kinds = collections.Counter()
    matched = 0
    for fn, r in rows.items():
        a = arm_part(r)
        best = max(a.get("best_score") or 0.0, r.get("incumbent_score") or 0.0)
        control = ((r.get("equal_cost") or {}).get("control_at_same_cost") or {}).get("best_score") or 0.0
        # The control starts FROM the incumbent: a control that logged no compiles (it had no
        # moves) still holds the incumbent, not 0. Without this floor an arm "wins" by standing still.
        control = max(control, r.get("incumbent_score") or 0.0)
        matched += bool(a.get("matched"))
        vs_control["better" if best > control + 1e-9 else "worse" if best < control - 1e-9 else "tie"] += 1
        if arm != "branch" and fn in plain:
            p = max(arm_part(plain[fn]).get("best_score") or 0.0, r.get("incumbent_score") or 0.0)
            vs_plain["better" if best > p + 1e-9 else "worse" if best < p - 1e-9 else "tie"] += 1
        for k, v in (a.get("kinds") or r.get("kinds") or {}).items():
            kinds[k] += v
    line = {"arm": arm, "functions": len(rows), "matched": matched,
            "vs_control(b/w/t)": f"{vs_control['better']}/{vs_control['worse']}/{vs_control['tie']}"}
    if vs_plain:
        line["vs_plain_tree(b/w/t)"] = f"{vs_plain['better']}/{vs_plain['worse']}/{vs_plain['tie']}"
    if kinds:
        line["kinds"] = dict(kinds.most_common(12))
    print(json.dumps(line))
