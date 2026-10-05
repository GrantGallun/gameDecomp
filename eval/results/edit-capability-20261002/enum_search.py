"""Deterministic search (`solver.site_edits.search`, no model) on the planted cases.

    python3 enum_search.py TAG [--budget 72] [--classes a,b]   -> E/search_TAG.jsonl, summary on stdout

Run once on today's site_edits (TAG=base), again after adding an edit family, and compare per case.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import json

import run
from run import E, mine
from localize import build_attr

from solver import site_edits, workspace


def one(c, budget, operators=False, gaps=False):
    stem = f"ec_se_{c['class']}"

    def score(code, label, parent_code):
        b = build_attr(c["function"], stem, code)
        if not b["compiled"]:
            return workspace.Attempt(False, 0.0, False, "", b.get("error") or "", "")
        exact = mine.mask(b["dump"]) == c["target"]
        return workspace.Attempt(True, 100.0 if exact else b["score"], exact, b["diff"], "", "",
                                 source_attribution=b["attr"])

    code = c["head"] + c["perturbed_def"] + c["tail"]
    r = site_edits.search(score, code, c["function"], budget=budget, operators=operators, gaps=gaps)
    wins = [t for t in r["trail"] if t.get("complete")]
    proposals = sum(t.get("proposals", 0) for t in r["trail"] if "proposals" in t)
    return {"id": c["id"], "class": c["class"], "exact": r["exact"], "compiles": r["compiles"],
            "winning_edit": wins[0]["edit"] if wins else None, "winning_kind": wins[0]["kind"] if wins else None,
            "proposals_level0": next((t.get("proposals", 0) for t in r["trail"] if "proposals" in t), 0),
            "declined": next((t.get("declined") for t in r["trail"] if t.get("declined")), None),
            "baseline": r["baseline"], "best": r["best"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tag")
    ap.add_argument("--budget", type=int, default=72)
    ap.add_argument("--classes", default="")
    ap.add_argument("--operators", action="store_true")
    ap.add_argument("--gaps", action="store_true")
    ap.add_argument("--cases", default="cases.jsonl")
    a = ap.parse_args()
    cases = [json.loads(l) for l in open(E / a.cases)]
    if a.classes:
        cases = [c for c in cases if c["class"] in a.classes.split(",")]
    out = E / f"search_{a.tag}.jsonl"
    rows = []
    with concurrent.futures.ThreadPoolExecutor(3) as ex, open(out, "w") as f:
        for r in ex.map(lambda c: one(c, a.budget, a.operators, a.gaps), cases):
            rows.append(r)
            f.write(json.dumps(r) + "\n")
            f.flush()
            if "heldout" in a.cases:      # held-out cases are not inspected individually (HELDOUT.md)
                print(r["class"], "EXACT" if r["exact"] else "-", flush=True)
            else:
                print(r["id"], "EXACT " + str(r["winning_edit"]) if r["exact"] else "-", r["compiles"],
                      r["declined"] or "", flush=True)
    by = collections.defaultdict(collections.Counter)
    for r in rows:
        by[r["class"]]["n"] += 1
        by[r["class"]]["exact"] += r["exact"]
    print()
    for cls, b in by.items():
        print(f"{cls:12} {b['exact']}/{b['n']}")
    print(f"{'all':12} {sum(b['exact'] for b in by.values())}/{len(rows)}")


if __name__ == "__main__":
    main()
