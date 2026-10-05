"""Give each planted public pair the context the compiler saw, TESTED for sufficiency, plus counterfactual facts.

    python3 context_tasks.py [--limit N] [--jobs 8] [--facts 6]   -> E/public/context.jsonl + context.receipt.json

For every function (rows grouped by repository/file/function from public/single.jsonl, single_same.jsonl, multi.jsonl):
1. Preprocess its translation unit (tools.context_closure: GNU cpp with IDO's own cfe defines) and take the closure.
2. SUFFICIENCY: compile `closure + function` alone with the file's recipe. Its instructions must equal the full-file
   build's (the row's `target`); otherwise the function is excluded, counted, never guessed.
3. MINIMIZE: delta-debug the closure (a declaration goes only if the standalone compile still reproduces the
   instructions exactly), then try dropping each remaining initializer the same way. The raw closure was a median 21k
   characters on a 40-function pilot.
4. Each row's perturbed function is re-derived from the preprocessed perturbed file and must reproduce the row's
   `current` listing in the same minimal context, or the row is excluded.
5. COUNTERFACTUALS: for up to --facts scalar declarations in the context (names the function mentions first), flip
   signedness or width, recompile both spellings, and record whether the SAME/DIFFER label flips. A flip means the fact
   is necessary to answer; no flip is an irrelevant-context control (eval/logic_tasks.py builds both kinds).

The functions shown are re-rendered from the preprocessed AST (macros expanded), because the context is preprocessed;
that is the program the compiler compiled.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import json
import threading
import time
from pathlib import Path

import public_plant as pp
from run import E

from tools import context_closure as cc

OUT = E / "public"
_lock = threading.Lock()
_files: dict[str, tuple] = {}


def tu(build: dict):
    key = build["source_file"]
    with _lock:
        if key in _files:
            return _files[key]
    args = cc.cfe_args(build["compiler"], build["flags"], key, build["cwd"])
    pre = cc.preprocess(Path(key).read_text(encoding="utf-8", errors="replace"), key, build["cwd"], args)
    ast, err = None, None
    if pre is None:
        err = "preprocess-failed"
    else:
        try:
            ast = cc.parse(pre)
        except Exception as e:                  # counted per function, never swallowed
            err = f"parse-failed: {str(e)[:80]}"
    with _lock:
        _files[key] = (args, ast, err)
    return _files[key]


class Tester:
    """Compiles context + function alone and compares the function's listing to an expected one."""

    def __init__(self, build, name):
        self.build, self.name, self.n = build, name, 0

    def listing(self, context: str, function: str, tag: str):
        self.n += 1
        obj = pp.compile_tu(self.build, context + "\n" + function, tag)
        return pp.listing(obj, self.name) if obj else None


def ddmin(items: list, ok) -> list:
    """Smallest subsequence (1-minimal) for which ok(subsequence) holds; ok(items) must hold."""
    n = 2
    while len(items) >= 2:
        size = max(1, len(items) // n)
        chunks = [items[i:i + size] for i in range(0, len(items), size)]
        for chunk in chunks:
            rest = [x for x in items if x not in chunk]
            if ok(rest):
                items, n = rest, max(n - 1, 2)
                break
        else:
            if n >= len(items):
                break
            n = min(len(items), 2 * n)
    return items


def by_function(rows):
    groups = collections.defaultdict(list)
    for r in rows:
        groups[(r["repository"], r["variant"], r["file"], r["function"])].append(r)
    return groups


def work(key, rows, bmap, n_facts):
    t = collections.Counter()
    repo, variant, file, name = key
    build = bmap.get((f"{repo}.{variant}", file))
    if build is None:
        t["no-build-record"] += 1
        return [], t
    _args, ast, err = tu(build)
    if ast is None:
        t[err.split(":")[0]] += 1
        return [], t
    funcdef = cc.find_funcdef(ast, name)
    if funcdef is None:
        t["function-not-in-ast"] += 1
        return [], t
    target = rows[0]["target"]
    tester = Tester(build, name)
    fn_orig = cc.render_function(funcdef)
    nodes = [ast.ext[i] for i in cc.closure(ast, name)]

    def ok(sub, inits=True):
        return tester.listing(cc.render(sub, initializers=inits), fn_orig, "ctx") == target

    if not ok(nodes):
        t["closure-insufficient"] += 1
        return [], t
    nodes = ddmin(nodes, ok)
    # Initializers: drop each one the standalone compile does not need.
    for i, node in enumerate(list(nodes)):
        if getattr(node, "init", None) is not None:
            trial = nodes[:i] + [_without_init(node)] + nodes[i + 1:]
            if ok(trial):
                nodes = trial
    context = cc.render(nodes)
    out = []
    for r in rows:
        pre = cc.preprocess(r_tu(build, r), build["source_file"], build["cwd"], _args)
        text = cc.find_function_text(pre or "", name)
        if text is None:
            t["perturbed-not-found"] += 1
            continue
        try:
            pert_def = cc.find_funcdef(cc.parse(context + "\n" + text), name)
        except Exception:
            pert_def = None
        if pert_def is None:
            t["perturbed-parse-failed"] += 1
            continue
        fn_pert = cc.render_function(pert_def)
        if tester.listing(context, fn_pert, "pert") != r["current"]:
            t["perturbed-context-insufficient"] += 1
            continue
        if fn_pert == fn_orig:
            t["renders-identical"] += 1              # the edit vanished in preprocessing/rendering: nothing to ask
            continue
        row = r | {"context": context, "context_items": len(nodes), "original_fn": fn_orig, "perturbed_fn": fn_pert,
                   "context_check": "standalone listing == full-file listing (original and perturbed)"}
        row["facts"] = counterfactuals(tester, nodes, fn_orig, fn_pert, n_facts, r["label"], r["diff"]) if n_facts else []
        t["admitted"] += 1
        out.append(row)
    t["compiles"] += tester.n
    return out, t


def _without_init(node):
    import copy
    n = copy.copy(node)
    n.init = None
    return n


def r_tu(build, r) -> str:
    """The full file with this row's perturbed definition in place of the original."""
    text = Path(build["source_file"]).read_text(encoding="utf-8", errors="replace")
    return text.replace(r["original_def"], r["perturbed_def"], 1)


def counterfactuals(tester, nodes, fn_a, fn_b, limit, label, diff):
    """For scalar declarations in the context: does flipping one change the answer -- whether A and B compile the
    same (`label_flips`), or which instruction rows differ (`rows_change`)?"""
    from eval.logic_tasks import diff_rows
    mentioned = fn_a + fn_b
    cands = sorted(cc.facts(nodes), key=lambda f: (f[2] not in mentioned, f[5] != "signedness", f[0], f[1]))
    out, seen = [], set()
    for i, k, decl_name, old, new, kind in cands:
        if len(out) >= limit or (decl_name, kind) in seen:
            continue
        seen.add((decl_name, kind))
        mutated = cc.render(cc.mutate(nodes, i, k, new))
        la, lb = tester.listing(mutated, fn_a, "fa"), tester.listing(mutated, fn_b, "fb")
        if la is None or lb is None:
            continue
        new_label = "same" if la == lb else "differ"
        new_diff = "" if la == lb else pp.mine.gnu_diff(la, lb)
        out.append({"name": decl_name, "old": old, "new": new, "kind": kind, "context": mutated,
                    "label": new_label, "diff": new_diff, "label_flips": new_label != label,
                    "rows_change": diff_rows(new_diff) != diff_rows(diff)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="functions")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--facts", type=int, default=6)
    ap.add_argument("--out", default="context")
    ap.add_argument("--inputs", default="single.jsonl,single_same.jsonl,multi.jsonl")
    a = ap.parse_args()
    rows = [json.loads(line) for f in a.inputs.split(",") for line in open(OUT / f)]
    groups = sorted(by_function(rows).items(), key=lambda kv: pp.hashlib.sha256(str(kv[0]).encode()).hexdigest())
    if a.limit:
        groups = groups[:a.limit]
    bmap = pp.builds()
    tally, started, n = collections.Counter(), time.time(), 0
    with concurrent.futures.ThreadPoolExecutor(a.jobs) as ex, open(OUT / f"{a.out}.jsonl", "w") as f:
        for got, t in ex.map(lambda kv: work(kv[0], kv[1], bmap, a.facts), groups):
            tally.update(t)
            for row in got:
                f.write(json.dumps(row) + "\n")
            n += len(got)
            print(len(got), n, f"{time.time() - started:.0f}s", flush=True)
    rows_out = [json.loads(line) for line in open(OUT / f"{a.out}.jsonl")]
    sizes = sorted(len(r["context"]) for r in rows_out) or [0]
    flips = collections.Counter((f["kind"], "label-flips" if f["label_flips"] else
                                 ("rows-change" if f["rows_change"] else "no-change"))
                                for r in rows_out for f in r["facts"])
    receipt = {"functions": len(groups), "rows_in": sum(len(v) for _k, v in groups), "rows_admitted": n,
               "seconds": round(time.time() - started), "tally": dict(tally),
               "context_chars": {"median": sizes[len(sizes) // 2], "p90": sizes[int(len(sizes) * 0.9)],
                                 "max": sizes[-1]},
               "facts": {f"{k}/{what}": v for (k, what), v in sorted(flips.items())},
               "preprocessor": "GNU cpp -P -undef -nostdinc -std=gnu89 + IDO cfe -D/-I", "normalizer": pp.NORMALIZER}
    (OUT / f"{a.out}.receipt.json").write_text(json.dumps(receipt, indent=1))
    print(json.dumps(receipt, indent=1))


if __name__ == "__main__":
    main()
