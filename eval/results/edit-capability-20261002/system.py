"""The whole system on planted cases: deterministic search, then the missing-statement model lane.

    python3 system.py TAG [--cases cases.jsonl]   -> E/system_TAG.jsonl

1. site_edits.search(operators=True, gaps=True), budget 72, no model.
2. If not exact and the diff has target-only rows: solver.missing_statement_llm proposals (4 samples), each compiled;
   then site_edits.search (budget 24) from the best-gradient model child.
A guard asserts that no line of the answer that the perturbed source lacks appears in any prompt.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import json
import threading

import run
from run import E, mine
from localize import build_attr

from solver import missing_statement_llm, site_edits, workspace


def scorer(c, stem, certs=None):
    """`certs` collects the certificate verdict of every masked-listing match (the run's stopping rule is unchanged,
    so a recertification replays the frozen run and only adds the verdict that counts)."""
    def score(code, label, parent_code):
        b = build_attr(c["function"], stem, code)
        if not b["compiled"]:
            return workspace.Attempt(False, 0.0, False, "", b.get("error") or "", "")
        exact = mine.mask(b["dump"]) == c["target"]
        if exact and certs is not None:
            certs.append(b["certified"])
        return workspace.Attempt(True, 100.0 if exact else b["score"], exact, b["diff"], "", "",
                                 source_attribution=b["attr"])
    return score


def answer_guard(c):
    perturbed = {l.strip() for l in c["perturbed_def"].split("\n")}
    hidden = [l.strip() for l in c["original_def"].split("\n") if l.strip() not in perturbed and len(l.strip()) > 6]

    def guard(prompt):
        for line in hidden:
            assert line not in prompt, f"answer line leaked into prompt: {line}"
    return guard


def one(c, endpoint, generate=None):
    certs = []
    out = _one(c, endpoint, certs, generate)
    # Control: the planted case's own original must certify against target.o, or no repair of it can.
    orig = build_attr(c["function"], f"ec_cert_{c['class']}", c["head"] + c["original_def"] + c["tail"])
    return out | {"certified": bool(out["exact"] and certs and certs[-1]), "masked_matches": len(certs),
                  "original_certified": bool(orig.get("certified"))}


def _one(c, endpoint, certs, generate=None):
    score = scorer(c, f"ec_sys_{c['class']}", certs)
    code = c["head"] + c["perturbed_def"] + c["tail"]
    r = site_edits.search(score, code, c["function"], budget=72, operators=True, gaps=True)
    out = {"id": c["id"], "class": c["class"], "stage": "search", "exact": r["exact"], "compiles": r["compiles"]}
    if r["exact"]:
        wins = [t for t in r["trail"] if t.get("complete")]
        return out | {"winning_edit": wins[0]["edit"] if wins else "baseline"}
    best_code = r["source"]
    best = score(best_code, "best", None)
    children = list(missing_statement_llm.variants(best_code, c["function"], best.diff, guard=answer_guard(c),
                                                   endpoint=endpoint, cache_dir=str(E / "llm-cache"),
                                                   generate=generate))
    out["model_children"] = len(children)
    ranked = []
    for label, child in children:
        a = score(child, label, best_code)
        out["compiles"] += 1
        if workspace.repair_complete(a):
            return out | {"stage": "model", "exact": True, "winning_edit": label}
        if a.compiled:
            ranked.append((site_edits.gradient(a), label, child))
    if ranked:
        ranked.sort(key=lambda x: x[0])
        g, label, child = ranked[0]
        if g < site_edits.gradient(best):
            r2 = site_edits.search(score, child, c["function"], budget=24, operators=True, gaps=True)
            out["compiles"] += r2["compiles"]
            if r2["exact"]:
                wins = [t for t in r2["trail"] if t.get("complete")]
                return out | {"stage": "model+search", "exact": True,
                              "winning_edit": f"{label} then {wins[0]['edit'] if wins else '?'}"}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tag")
    ap.add_argument("--cases", default="cases.jsonl")
    ap.add_argument("--recert", action="store_true",
                    help="replay from the generation cache only (no GPU; a cache miss fails) and record certificates")
    a = ap.parse_args()
    import functools
    from solver import llm
    endpoint = llm.host()
    generate = functools.partial(llm.generate, cache_only=True) if a.recert else None
    cases = [json.loads(l) for l in open(E / a.cases)]
    rows, lock = [], threading.Lock()
    with concurrent.futures.ThreadPoolExecutor(3) as ex, open(E / f"system_{a.tag}.jsonl", "w") as f:
        for r in ex.map(lambda c: one(c, endpoint, generate), cases):
            with lock:
                rows.append(r)
                f.write(json.dumps(r) + "\n")
                f.flush()
            print(r["id"] if "heldout" not in a.cases else r["class"], r["stage"],
                  "EXACT" if r["exact"] else "-", "CERT" if r["certified"] else "", r["compiles"], flush=True)
    by = collections.defaultdict(collections.Counter)
    for r in rows:
        by[r["class"]]["n"] += 1
        by[r["class"]]["exact"] += r["exact"]
        by[r["class"]][r["stage"]] += r["exact"]
        by[r["class"]]["certified"] += r["certified"]
    print()
    for cls, b in by.items():
        print(f"{cls:12} {b['exact']}/{b['n']}  (search {b['search']}, model {b['model'] + b['model+search']})")
    print(f"{'all':12} {sum(b['exact'] for b in by.values())}/{len(rows)} masked-listing matches, "
          f"{sum(b['certified'] for b in by.values())} certified")


if __name__ == "__main__":
    main()
