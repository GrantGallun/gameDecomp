"""Self-curriculum: a proposer writes tasks aimed at the trained model's weak spots; the compiler labels them; the
model's own success rate decides which are worth training on.

    python3 self_curriculum.py weak     --grades G1.jsonl [G2 ...] --tasks TASKS.jsonl --out DIR
    python3 self_curriculum.py propose  --out DIR --proposer ollama:gpt-oss:20b|lora:<adapter> [--per-class 200]
    python3 self_curriculum.py filter   --out DIR --solver <adapter> [--k 4]
    -> DIR/weak.json, DIR/rows.jsonl (context-row schema), DIR/candidates.jsonl, DIR/kept.jsonl (training tasks)

The loop (Absolute Zero, Zhao et al. 2025, adapted): PROPOSE edits that a decompiler would plausibly get wrong in the
way the model fails; VERIFY with the compiler, never with a model (a proposer's claim about what an edit does is
discarded; only compile + listing decide the label and the diff); keep tasks by LEARNABILITY: the solver's success
over k samples strictly between 0 and k (a task always solved teaches nothing, one never solved teaches nothing
yet).

Guardrails, fixed here rather than left to configuration:
  - Real code only. Every task edits a TRAIN-split public function from the checked context rows (context_tasks.py);
    the proposer never invents a function, so the curriculum cannot drift into C no game contains.
  - Weak spots come from a PRACTICE set on train-split tasks, never from exam or check grades: targeting the exam's
    failures would teach to the test. `weak` refuses grades from other splits.
  - Scores are only ever the frozen exams. Generated tasks are training data.
  - Novelty: one task per (function, edited function); edits that change nothing are kept as `same` rows (the
    compiler's "these spellings are one program"), not as repair tasks.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from eval import logic_tasks as lt
from eval import repair_prompts

HOLDOUT = {"sm64"}
MAX_PROMPT_CHARS = 9000        # the frozen exams' prompt cap (eval/logic_exam.py freeze)

# What each weak class means, in terms a proposer can aim at. A class without a description is skipped (counted).
CLASS_BRIEF = {
    "drop_stmt": "a statement the draft is missing entirely (a store, a call, or an assignment)",
    "read_store": "a store through a pointer or struct field written with the wrong field, value or width",
    "read_call": "a call with a wrong argument, wrong argument order, or a value passed where an address was meant",
    "read_assign": "an assignment to a local with the wrong expression",
    "arith_op": "an arithmetic or bitwise operator replaced by a similar one",
    "const": "a numeric constant that is slightly wrong",
    "arg_swap": "two call arguments in the wrong order",
    "stmt_swap": "two independent statements in the wrong order",
    "decl_width": "a local or parameter declared with the wrong integer width or signedness",
    "if_invert": "an if/else whose condition is inverted with the arms swapped",
    "temp_return": "a return value routed through a temporary variable",
    "k2": "two separate small mistakes of different kinds",
    "cmp_mirror": "a comparison written mirrored (a < b as b > a)",
    "commute": "the operands of a commutative operator swapped",
}

PROPOSE_TEMPLATE = """\
You are writing practice problems for a model that learns to fix decompiled C so it compiles byte-for-byte like the
original ({compiler} {opt}, MIPS). The model is weak at this kind of mistake: {brief}.

Below is a real original function with its declarations. Write ONE edit that turns it into a plausible draft
containing exactly that kind of mistake: something a decompiler or a careless person could produce, and which a
reader of the compiled instructions could detect and undo. Keep it small. The draft must still compile.

Output only edit lines, numbered as shown:
REPLACE n: <new text> | INSERT AFTER n: <new text> | DELETE n

DECLARATIONS:
```c
{context}
```

FUNCTION:
```c
{numbered}
```
"""


def _h(*parts) -> int:
    return int(hashlib.sha256("\x00".join(map(str, parts)).encode()).hexdigest(), 16)


def class_of(task: dict) -> str:
    return task.get("class") or (task["id"].split(":")[1] or task["id"].split(":")[2])


# -- weak -----------------------------------------------------------------------------------------------------------
def weak(grade_paths: list[Path], tasks_paths: list[Path], out: Path, floor: int = 8) -> dict:
    tasks = {t["id"]: t for path in tasks_paths for t in map(json.loads, open(path))}
    n, ok = collections.Counter(), collections.Counter()
    for path in grade_paths:
        for g in map(json.loads, open(path)):
            t = tasks[g["id"]]
            if t["split"] != "train":
                raise SystemExit(f"{g['id']} is a {t['split']} task: weak spots come from train-split practice only")
            if t["kind"] not in ("logic-explain", "logic-read"):
                continue
            c = class_of(t)
            n[c] += 1
            ok[c] += bool(g["rows"])
    profile = {c: {"n": n[c], "solved": ok[c], "fail_rate": round(1 - ok[c] / n[c], 3)}
               for c in n if n[c] >= floor}
    # Weight = failure rate: the share of generated tasks each class gets.
    total = sum(p["fail_rate"] for p in profile.values()) or 1.0
    for p in profile.values():
        p["weight"] = round(p["fail_rate"] / total, 3)
    out.mkdir(parents=True, exist_ok=True)
    (out / "weak.json").write_text(json.dumps(dict(sorted(profile.items(), key=lambda kv: -kv[1]["fail_rate"])),
                                              indent=1))
    return profile


# -- propose --------------------------------------------------------------------------------------------------------
def _caller(spec: str):
    if spec.startswith("lora:"):
        from tools.lora_serve.client import InferenceClient
        client, adapter = InferenceClient("http://127.0.0.1:8101", timeout=900.0), spec[5:]

        def ask(prompt, seed):
            return client.chat([{"role": "user", "content": prompt}], model=None if adapter == "base" else adapter,
                               temperature=0.8, seed=seed, max_tokens=400).text
        return ask, 16
    from solver import llm
    endpoint, model = llm.host(), spec[len("ollama:"):]

    def ask(prompt, seed):
        return llm.generate(endpoint, model, prompt, timeout=600, num_predict=3000, think="low", temperature=0.8,
                            seed=seed)[0]
    return ask, 3


def propose(out: Path, proposer: str, per_class: int, context_rows: Path, anonymize: bool = False) -> dict:
    import threading
    import public_plant as pp
    profile = json.loads((out / "weak.json").read_text())
    rows = [json.loads(line) for line in open(context_rows)]
    fns = {}
    for r in rows:
        if r.get("context") and r.get("original_fn") and r["split"] == "train" and r["repository"] not in HOLDOUT:
            fns.setdefault((r["repository"], r["variant"], r["file"], r["function"]), r)
    fns = sorted(fns.values(), key=lambda r: r["id"])
    bmap = pp.builds()
    ask, jobs = _caller(proposer)
    tally = collections.Counter()
    plans = []
    budget = per_class * len(profile)
    for c, p in profile.items():
        if c not in CLASS_BRIEF:
            tally[f"no-brief:{c}"] += 1
            continue
        for i in range(max(1, round(budget * p["weight"]))):
            plans.append((c, fns[_h("fn", c, i) % len(fns)], i))

    anon_cache, anon_lock = {}, threading.Lock()

    def anonymized(src):
        """The function and context with every searchable name replaced (tools/anonymize.py), recompiled and checked
        to give the same instructions up to symbol names; None (refused) otherwise."""
        from tools import anonymize as an
        with anon_lock:
            if src["id"] in anon_cache:
                return anon_cache[src["id"]]
        try:
            mapping, (ctx, orig) = an.anonymize(src["context"], [src["original_fn"]])
            name = mapping.get(src["function"], src["function"])
            obj = pp.compile_row(src, ctx + "\n" + orig, "an", bmap)
            got = pp.listing(obj, name) if obj else None
            ok = got is not None and an.masked_listing(got) == an.masked_listing(src["target"])
            res = (src | {"context": ctx, "original_fn": orig, "target": got, "function": name,
                          "anonymized_from": src["function"]}) if ok else None
        except Exception:                              # a parse failure refuses the function, counted by the caller
            res = None
        with anon_lock:
            anon_cache[src["id"]] = res
        return res

    def one(plan):
        c, src, i = plan
        if anonymize:
            src = anonymized(src)
            if src is None:
                return None, "anonymize-refused"
        prompt = PROPOSE_TEMPLATE.format(compiler=src.get("compiler") or "IDO 5.3", opt=src.get("opt") or "-O2",
                                         brief=CLASS_BRIEF[c],
                                         context=src["context"].rstrip(), numbered=lt.numbered(src["original_fn"]))
        try:
            text = ask(prompt, _h("seed", c, src["id"], i) % 2**31)
        except Exception as exc:                       # a proposer failure is counted, never a silent empty set
            return None, f"proposer-error:{type(exc).__name__}"
        script = "\n".join(line.strip() for line in text.splitlines() if lt.EDIT.match(line.strip()))
        edited = lt.apply_script(src["original_fn"], script) if script else None
        if edited is None or edited == src["original_fn"]:
            return None, "no-usable-edit"
        obj = pp.compile_row(src, src["context"] + "\n" + edited, "sc", bmap)
        cur = pp.listing(obj, src["function"]) if obj else None
        if cur is None:
            return None, "edit-does-not-compile"
        label = "same" if cur == src["target"] else "differ"
        rid = f"gen_{c}:{_h(src['id'], edited) % 10**12:012d}:{src['id'].split(':', 2)[2]}"
        return ({"id": rid, "class": f"gen_{c}", "target_class": c, "label": label, "source_kind": "self-curriculum",
                 "proposer": proposer, **{k: src.get(k) for k in ("repository", "variant", "file", "function", "split",
                                                                  "split_group", "opt")},
                 "context": src["context"], "original_fn": src["original_fn"], "perturbed_fn": edited,
                 "target": src["target"], "current": cur,
                 "diff": pp.mine.gnu_diff(src["target"], cur) if label == "differ" else "",
                 "anonymized_from": src.get("anonymized_from"),
                 # Kept for the examiner: its prompt and exact output, so proposals that earn a learnability reward
                 # can become its own training data (`examiner`).
                 "propose_prompt": prompt, "propose_text": text}, f"{label}")

    seen, kept = set(), []
    with concurrent.futures.ThreadPoolExecutor(jobs) as ex:
        for row, why in ex.map(one, plans):
            tally[why] += 1
            if row is None:
                continue
            key = (row["function"], row["perturbed_fn"])
            if key in seen:
                tally["duplicate"] += 1
                continue
            seen.add(key)
            kept.append(row)
    (out / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in kept))
    tasks = []
    for row in kept:
        makers = [lt.predict_task] + ([lt.explain_task] if row["label"] == "differ" else [])
        for make in makers:
            try:
                task = make(row)
            except (lt.Leak, lt.TooLong, ValueError):
                tally[f"refused-{make.__name__}"] += 1
                continue
            tasks.append(task | {"id": f"{task['kind']}:{row['id']}", "row_id": row["id"], "variant": "base",
                                 "class": row["class"], "label": row["label"], "split": "train",
                                 "split_group": row.get("split_group"), "repository": row["repository"],
                                 "function": row["function"], "provenance": "self-curriculum",
                                 "prompt_version": repair_prompts.LOGIC_PROMPT_VERSION})
    (out / "candidates.jsonl").write_text("".join(json.dumps(t) + "\n" for t in tasks))
    receipt = {"proposer": proposer, "anonymize": anonymize, "plans": len(plans), "rows": len(kept), "tasks": len(tasks),
               "tally": dict(tally), "by_class": dict(collections.Counter(r["class"] for r in kept)),
               "labels": dict(collections.Counter(r["label"] for r in kept))}
    (out / "propose.json").write_text(json.dumps(receipt, indent=1))
    return receipt


# -- filter ---------------------------------------------------------------------------------------------------------
def learnable(solved: int, k: int) -> bool:
    """Absolute Zero's learnability: kept only when the solver sometimes succeeds and sometimes fails."""
    return 0 < solved < k


def examiner_reward(solved: int, k: int) -> float:
    """Absolute Zero's proposer reward: 1 - solve rate for a learnable task, 0 for one always or never solved. Hard
    but fair pays most; impossible pays nothing, so an examiner cannot win with garbage or with grader bugs (a task
    the solver can never solve earns 0, the same as a trivial one)."""
    return round(1 - solved / k, 3) if learnable(solved, k) else 0.0


def examiner_data(out: Path, min_reward: float) -> dict:
    """Expert iteration for the examiner: its own proposals that earned a learnability reward become SFT examples
    (prompt -> its exact output). Online RL for the examiner needs the solver served while the examiner trains, which
    one 16 GB card cannot hold; rejection-sampling SFT is the offline equivalent."""
    rows = {r["id"]: r for r in map(json.loads, open(out / "rows.jsonl"))}
    scored = [json.loads(line) for line in open(out / "proposals_scored.jsonl")]
    examples = []
    for s in sorted(scored, key=lambda s: -s["examiner_reward"]):
        if s["examiner_reward"] < min_reward:
            continue
        r = rows[s["row_id"]]
        examples.append({"id": f"{repair_prompts.EXAMINER_KIND}:{r['id']}", "kind": repair_prompts.EXAMINER_KIND,
                         "prompt": r["propose_prompt"], "completion": r["propose_text"].strip(), "split": "train",
                         "class": r["class"], "function": r["function"], "provenance": "self-curriculum-examiner",
                         "examiner_reward": s["examiner_reward"], "prompt_version": repair_prompts.LOGIC_PROMPT_VERSION})
    (out / "examiner_sft.jsonl").write_text("".join(json.dumps(e) + "\n" for e in examples))
    receipt = {"scored": len(scored), "examples": len(examples), "min_reward": min_reward,
               "by_class": dict(collections.Counter(e["class"] for e in examples))}
    (out / "examiner.json").write_text(json.dumps(receipt, indent=1))
    return receipt


def filter_tasks(out: Path, solver: str, k: int) -> dict:
    import logic_grade as lg
    import public_plant as pp
    from tools.lora_serve.client import InferenceClient
    tasks = [json.loads(line) for line in open(out / "candidates.jsonl")]
    rows = {r["id"]: r for r in map(json.loads, open(out / "rows.jsonl"))}
    bmap = pp.builds()
    client = InferenceClient("http://127.0.0.1:8101", timeout=900.0)

    def one(t):
        # A prompt past the exams' cap is skipped (a 27,875-token candidate crashed round 1's filter); a failing
        # request is an error for that task, counted, not the end of the stage.
        if len(t["prompt"]) > MAX_PROMPT_CHARS:
            return t, None
        solved = 0
        try:
            for i in range(k):
                text = client.chat([{"role": "user", "content": t["prompt"]}],
                                   model=None if solver == "base" else solver,
                                   temperature=0.8, seed=20261005 + i, max_tokens=512).text
                solved += bool(lg.grade(t, text, rows, bmap)["rows"])
        except Exception:
            return t, -1
        return t, solved

    kept, rates, scored = [], collections.Counter(), []
    with concurrent.futures.ThreadPoolExecutor(16) as ex:
        for t, solved in ex.map(one, tasks):
            if solved is None or solved < 0:
                rates[("skipped-too-long" if solved is None else "request-error", 0)] += 1
                continue
            rates[(t["kind"], solved)] += 1
            if learnable(solved, k):
                kept.append(t | {"solver_successes": solved, "solver_k": k})
            if t["kind"] == repair_prompts.LOGIC_EXPLAIN_KIND:
                scored.append({"row_id": t["row_id"], "class": t["class"], "solved": solved, "k": k,
                               "examiner_reward": examiner_reward(solved, k)})
    (out / "kept.jsonl").write_text("".join(json.dumps(t) + "\n" for t in kept))
    (out / "proposals_scored.jsonl").write_text("".join(json.dumps(r) + "\n" for r in scored))
    receipt = {"solver": solver, "k": k, "candidates": len(tasks), "kept": len(kept),
               "solve_histogram": {f"{kind}/{s}": n for (kind, s), n in sorted(rates.items())},
               "kept_by_class": dict(collections.Counter(t["class"] for t in kept)),
               "examiner_reward_mean": round(sum(r["examiner_reward"] for r in scored) / max(1, len(scored)), 3)}
    (out / "filter.json").write_text(json.dumps(receipt, indent=1))
    return receipt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("weak")
    w.add_argument("--grades", nargs="+", type=Path, required=True)
    w.add_argument("--tasks", type=Path, nargs="+", required=True)
    w.add_argument("--out", type=Path, required=True)
    w.add_argument("--floor", type=int, default=8, help="minimum practice items for a class to be profiled")
    p = sub.add_parser("propose")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--proposer", required=True)
    p.add_argument("--per-class", type=int, default=200)
    p.add_argument("--anonymize", action="store_true", help="propose on nameless functions (tools/anonymize.py)")
    p.add_argument("--context", type=Path,
                   default=Path.home() / "decomp/experiments/edit-capability-20261002/public/context-v3.jsonl")
    e = sub.add_parser("examiner")
    e.add_argument("--out", type=Path, required=True)
    e.add_argument("--min-reward", type=float, default=0.25)
    f = sub.add_parser("filter")
    f.add_argument("--out", type=Path, required=True)
    f.add_argument("--solver", required=True)
    f.add_argument("--k", type=int, default=4)
    a = ap.parse_args(argv)
    if a.cmd == "weak":
        print(json.dumps(weak(a.grades, a.tasks, a.out, a.floor), indent=1))
    elif a.cmd == "propose":
        print(json.dumps(propose(a.out, a.proposer, a.per_class, a.context, a.anonymize), indent=1))
    elif a.cmd == "examiner":
        print(json.dumps(examiner_data(a.out, a.min_reward), indent=1))
    else:
        print(json.dumps(filter_tasks(a.out, a.solver, a.k), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
