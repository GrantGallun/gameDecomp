"""Grade the pipeline's own signals against the key. TOOL DEVELOPMENT ONLY.

Key distance (key_distance.py) needs the reference decomp, so it can never be a decompilation
metric, search signal or ranker (memory: key-distance-dev-only). What it CAN do is tell us which
signal the pipeline is allowed to compute tracks movement toward the key:

  score          the similarity score (what the search uses)
  object truth   solver.invariants distance from the diff, level by level (calls, control flow,
                 frame, expressions, operands, registers)

For each signal and each thing it can say about a step (up / same / down), the share of steps
that moved TOWARD the key (alpha-renamed token distance decreased) vs AWAY. The downhill rows
are the question: among steps that cost score, does object truth pick out the ones heading to the key?

    ~/decomp/sbk1/.venv/bin/python key_proxy.py   (cwd holding campaign.sqlite)
"""
import collections
import sqlite3
import sys
from pathlib import Path

from rapidfuzz.distance import Levenshtein

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval.repair_dataset import REFERENCE_SEED_MARKERS  # noqa: E402
from key_distance import REFERENCE, canonical  # noqa: E402
from patterns.commit_provenance import function_definitions  # noqa: E402
from solver import invariants as inv  # noqa: E402

sys.setrecursionlimit(100000)


def main():
    keys = {}
    for path in REFERENCE.rglob("*.c"):
        try:
            for name, text in function_definitions(path.read_text(errors="replace")).items():
                keys.setdefault(name, canonical(text, structure=True))
        except Exception:
            continue
    db = sqlite3.connect("file:campaign.sqlite?mode=ro", uri=True)
    names = dict(db.execute("select addr, name from functions"))
    att = {}
    for aid, addr, score, compiled, exact, strategy in db.execute(
            "select id, func_addr, score, coalesce(compiled,0), coalesce(exact,0), "
            "coalesce(strategy,'') from attempts"):
        att[aid] = (names.get(addr), score or 0.0, compiled, exact, strategy)
    parents, kids = collections.defaultdict(list), collections.defaultdict(list)
    edges = db.execute("select parent_attempt_id, child_attempt_id from attempt_edges").fetchall()
    for p, c in edges:
        parents[c].append(p)
        kids[p].append(c)

    seeded_memo = {}

    def seeded(n):
        if n not in seeded_memo:
            seeded_memo[n] = False
            seeded_memo[n] = any(m in att.get(n, ("", 0, 0, 0, ""))[4]
                                 for m in REFERENCE_SEED_MARKERS) or \
                any(seeded(p) for p in parents.get(n, ()))
        return seeded_memo[n]

    kd, truth = {}, {}

    def key_dist(aid):
        if aid not in kd:
            src = db.execute("select source_code from attempts where id=?", (aid,)).fetchone()[0]
            key, cand = keys.get(att[aid][0]), canonical(src, att[aid][0], structure=True)
            kd[aid] = None if key is None or cand is None else \
                Levenshtein.normalized_distance(cand, key)
        return kd[aid]

    def obj_truth(aid):
        if aid not in truth:
            diff = db.execute("select diff_summary from attempts where id=?", (aid,)).fetchone()[0]
            truth[aid] = inv.distance_from_diff(diff or "")
        return truth[aid]

    table = collections.defaultdict(collections.Counter)
    for p, c in edges:
        if p not in att or c not in att:
            continue
        pa, ca = att[p], att[c]
        if pa[0] != ca[0] or not pa[2] or not ca[2] or pa[3] or ca[3] or seeded(p):
            continue
        dp, dc = key_dist(p), key_dist(c)
        if dp is None or dc is None or dp == dc:
            continue                                   # no movement relative to the key
        toward = "toward" if dc < dp else "away"
        s = "up" if ca[1] > pa[1] else "down" if ca[1] < pa[1] else "same"
        tp, tc = obj_truth(p), obj_truth(c)
        t = "up" if tc < tp else "down" if tc > tp else "same"
        table[("score", s)][toward] += 1
        table[("truth", t)][toward] += 1
        table[("score " + s, "truth " + t)][toward] += 1
    base = sum(table[("score", s)]["toward"] for s in ("up", "same", "down"))
    total = sum(sum(table[("score", s)].values()) for s in ("up", "same", "down"))
    print(f"steps that moved relative to the key: {total}; base rate toward: {base / max(total, 1):.1%}\n")
    print("signal says           steps    moved toward the key")
    for k in [("score", "up"), ("score", "same"), ("score", "down"),
              ("truth", "up"), ("truth", "same"), ("truth", "down")]:
        n = sum(table[k].values())
        print(f"  {k[0]:5s} {k[1]:5s}        {n:8d}    {table[k]['toward'] / max(n, 1):6.1%}")
    print("\nscore x truth")
    for s in ("up", "same", "down"):
        for t in ("up", "same", "down"):
            k = ("score " + s, "truth " + t)
            n = sum(table[k].values())
            if n:
                print(f"  score {s:4s} truth {t:4s}  {n:8d}    {table[k]['toward'] / n:6.1%}")


if __name__ == "__main__":
    main()
