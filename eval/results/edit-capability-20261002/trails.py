"""Search pruning by learned priorities, scored as coverage at a budget (eval/coverage.py).

    python3 trails.py collect train            -> E/trails_train.jsonl (every compiled child: parent features, family,
                                                  improved?), using the default search order
    python3 trails.py fit                      -> E/priors_v1.json + priors_v1.json here (fitted on train ONLY)
    python3 trails.py score SPLIT ARM --per-step N   -> E/cov_SPLIT_ARM_pN.jsonl (one row per case, cost-to-exact)
        ARM: base (propose's order) | learned (solver.search_priors.orderer) | drop (learned + dead families removed)
             | tiered (learned, escalate=True: per-step is a tier, a miss pulls the next tier; budget 72)
             | base_tiered (propose's order, escalate=True, budget 72)
    python3 -m eval.coverage E/multi_SPLIT.jsonl E/cov_...jsonl --budgets 8,16,24,36,48,72

The search is site_edits.search(operators=True, gaps=True, depth=3) with budget = 3 * per_step, so a narrower
per-step width IS the pruning: fewer children per level. Held-out rows print class only (HELDOUT_MULTI.md).
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import threading

import run
from run import E, mine
from localize import build_attr

from solver import search_priors, site_edits, workspace

PRIORS = E / "priors_v1.json"


def scorer(c, stem, events=None, certs=None):
    cache: dict[str, workspace.Attempt] = {}
    lock = threading.Lock()

    def score(code, label, parent_code):
        b = build_attr(c["function"], stem, code)
        if not b["compiled"]:
            a = workspace.Attempt(False, 0.0, False, "", b.get("error") or "", "")
        else:
            exact = mine.mask(b["dump"]) == c["target"]
            if exact and certs is not None:
                certs.append(b["certified"])      # masked match: record the certificate (audit 2026-10-03)
            a = workspace.Attempt(True, 100.0 if exact else b["score"], exact, b["diff"], "", "",
                                  source_attribution=b["attr"])
        with lock:
            cache[code] = a
        if events is not None and parent_code is not None and parent_code in cache:
            parent = cache[parent_code]
            events.append({"features": search_priors.features(parent.diff or ""),
                           "family": search_priors.family(label), "compiled": a.compiled,
                           "improved": workspace.repair_complete(a) or
                           (a.compiled and site_edits.gradient(a) < site_edits.gradient(parent))})
        return a
    return score


TIERED_BUDGET = 72        # tiered arms: per-step is the tier; the budget stays the wide arm's 3 x 24


def one(c, arm, per_step, table, events=None):
    order = None
    if arm in ("learned", "drop", "tiered"):
        order = search_priors.orderer(table, drop=arm == "drop")
    tiered = arm in ("tiered", "base_tiered")
    # one stem per case: two cases of one function (k2.0, k2.1) share a workspace and may run concurrently
    certs = []
    score = scorer(c, f"ec_tr_{arm}_{c['id'].split(':')[0].replace('.', '_')}", events, certs)
    code = c["head"] + c["perturbed_def"] + c["tail"]
    r = site_edits.search(score, code, c["function"], budget=TIERED_BUDGET if tiered else 3 * per_step,
                          per_step=per_step, operators=True, gaps=True, reorder=order, escalate=tiered)
    wins = [t for t in r["trail"] if t.get("complete")]
    orig = build_attr(c["function"], f"ec_cert_{arm}_{c['id'].split(':')[0].replace('.', '_')}",
                      c["head"] + c["original_def"] + c["tail"])      # control: the original itself certifies
    return {"id": c["id"], "class": c["class"], "exact": r["exact"], "compiles": r["compiles"],
            "original_certified": bool(orig.get("certified")),
            "certified": bool(r["exact"] and certs and certs[-1]), "masked_matches": len(certs),
            "winning_kind": wins[0]["kind"] if wins else None, "best_gradient": r.get("best_gradient")}


def collect(split, jobs):
    if split != "train":
        raise SystemExit("events are collected on train only: priors may not see dev or held-out")
    cases = [json.loads(line) for line in open(E / f"multi_{split}.jsonl")]

    def work(c):
        ev = []
        row = one(c, "base", 24, None, ev)
        return row | {"events": ev}
    n_ev = 0
    with concurrent.futures.ThreadPoolExecutor(jobs) as ex, open(E / f"trails_{split}.jsonl", "w") as f:
        for row in ex.map(work, cases):
            f.write(json.dumps(row) + "\n")
            f.flush()
            n_ev += len(row["events"])
            print(row["id"], "EXACT" if row["exact"] else "-", row["compiles"], len(row["events"]), flush=True)
    print(f"{len(cases)} cases, {n_ev} events")


def fit():
    src = E / "trails_train.jsonl"
    events = [e for line in open(src) for e in json.loads(line)["events"]]
    table = search_priors.fit(events, sources=[str(src)])
    search_priors.save(table, PRIORS)
    search_priors.save(table, run.HERE / PRIORS.name)
    print(f"{table['events']} events, {len(table['by_feature'])} features, {len(table['overall'])} families")
    for fam, (imp, tried) in sorted(table["overall"].items(), key=lambda kv: -kv[1][1]):
        print(f"  {fam:28} improved {imp:4} / tried {tried:4}")


def score(split, arm, per_step, jobs, suffix=""):
    cases = [json.loads(line) for line in open(E / f"multi_{split}.jsonl")]
    table = search_priors.load(PRIORS) if arm not in ("base", "base_tiered") else None
    out = E / f"cov_{split}_{arm}_p{per_step}{suffix}.jsonl"
    with concurrent.futures.ThreadPoolExecutor(jobs) as ex, open(out, "w") as f:
        for row in ex.map(lambda c: one(c, arm, per_step, table), cases):
            f.write(json.dumps(row) + "\n")
            f.flush()
            print(row["class"] if split == "heldout" else row["id"], "EXACT" if row["exact"] else "-",
                  row["compiles"], flush=True)
    print(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("collect", "fit", "score"))
    ap.add_argument("split", nargs="?")
    ap.add_argument("arm", nargs="?", choices=("base", "learned", "drop", "tiered", "base_tiered"))
    ap.add_argument("--per-step", type=int, default=24)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--suffix", default="", help="output suffix, e.g. _recert: never overwrite a frozen run's rows")
    a = ap.parse_args()
    if a.cmd == "collect":
        collect(a.split, a.jobs)
    elif a.cmd == "fit":
        fit()
    else:
        score(a.split, a.arm, a.per_step, a.jobs, a.suffix)


if __name__ == "__main__":
    main()
