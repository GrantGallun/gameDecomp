"""Reading tasks: compiler-labelled practice for the skill missing-statement repairs fail on.

    python3 reading_tasks.py [--context public/context-v3.jsonl] [--out-rows public/reading-v1.jsonl]
                             [--out-tasks DIR] [--per-fn-train 8] [--per-fn-eval 3] [--jobs 6] [--limit N]

Diagnosis (LOGIC_PILOT.md, 2026-10-04): of 39 missed missing-statement repairs, 16 were assignments (70% of those
fail) and most call misses had the right callee with wrong argument values; giving the model WHERE the statement goes
(the localization A/B) did not help. So the gap is reading WHAT a group of instructions says.

For every function with a checked context (context_tasks.py rows: the function compiles alone in its context exactly
as in its own file), each simple statement (an assignment or call, run._simple_stmt) whose line the compiler's own
line table credits with instructions yields two rows:

  read      the statement's line is blanked (`; /* ? */`, an empty statement, so an unbraced `if` keeps its meaning);
            the prompt shows the TARGET listing with the rows the line table attributes to that line marked `>`; the
            answer is the statement. Graded by substituting it and compiling (logic_grade.grade_read): any statement
            that compiles to the same instructions is right.
  drop      the statement is deleted and the task is an ordinary logic-explain repair (the exam's drop_stmt format),
            from every eligible line instead of the planter's first three candidates.

The line table comes from compiling the REFERENCE function here. That is a teaching aid for training rows only; it
is never available in a real decompile, where attribution comes from the candidate (solver/line_map.py). Both rows
are kept only when blanking/deleting the line changes the listing (the compiler did not erase the statement).
Splits follow the context rows (logic_tasks.split_of: train -> train, dev -> exam, sm64 -> check).
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import hashlib
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import public_plant as pp
import run
from eval import logic_tasks as lt
from eval import repair_prompts
from solver import line_map

MARK = "; /* ? */"
MAX_LISTING = 120          # rows: keeps prompts inside the trainer's 3072-token window
HOLDOUT = {"sm64"}


def stmt_kind(stmt: str) -> str:
    s = stmt.strip()
    if re.match(r"^[A-Za-z_]\w*\s*\(.*\)\s*;$", s):
        return "call"
    if re.search(r"(?<![=!<>])(?:[-+*/%&|^]|<<|>>)?=(?!=)", s):
        return "store" if re.match(r"^\s*(?:\*|[A-Za-z_][\w.\[\]]*(?:->|\.|\[))", s) else "assign"
    return "other"


def pair_id(row_id: str) -> str:
    # General-code rows keep their recipe in the id (general:<recipe>:<project>:...), so one function compiled by
    # several compilers gives distinct tasks.
    return row_id.split(":", 1)[1] if row_id.startswith("general:") else row_id.split(":", 2)[2]


def one_row_per_function(rows: list[dict]) -> list[dict]:
    by = {}
    for r in sorted(rows, key=lambda r: r["id"]):
        if r.get("context") and r.get("original_fn") and r.get("target"):
            by.setdefault((r["repository"], r["variant"], r["file"], r["function"], r.get("recipe")), r)
    return list(by.values())


BMAP: dict = {}


def line_table_object(src: dict, text: str, target: list, obj: Path) -> Path:
    """The object whose `objdump -dl` carries line records. IDO writes them in every build; GCC only with -g, so a
    GCC row is recompiled with -g and refused (ValueError) unless its instructions equal the plain build's."""
    if not src.get("recipe", "").startswith("kmc"):
        return obj
    g = pp.compile_row(src, text, "rdg", BMAP, extra=("-g",))
    if g is None or pp.listing(g, src["function"]) != target:
        raise ValueError("debug build differs")
    return g


def dump_of(obj: Path) -> str:
    return subprocess.run([pp.ws_objdump.find_objdump_executable(), "-dl", *pp.ws_objdump.OBJDUMP_ARGS[1:], str(obj)],
                          capture_output=True, text=True, check=True).stdout


def work(src: dict, build: dict, per_fn: int, known_drops: set, tally: collections.Counter):
    name, ctx, fn, target = src["function"], src["context"], src["original_fn"], src["target"]
    if len(target) > MAX_LISTING:
        tally["listing-too-long"] += 1
        return []
    obj = pp.compile_row(src, ctx + "\n" + fn, "rd0", BMAP)
    if obj is None or pp.listing(obj, name) != target:
        tally["original-mismatch"] += 1
        return []
    offset = (ctx + "\n").count("\n")
    try:
        lm = line_map.build(dump_of(line_table_object(src, ctx + "\n" + fn, target, obj)), name, target,
                            line_offset=offset)
    except ValueError:
        tally["no-line-table"] += 1
        return []
    lines = fn.split("\n")
    cands = [i + 1 for i, line in enumerate(lines) if run._simple_stmt(line) and lm.rows_of(i + 1)]
    tally["statements-without-rows"] += sum(1 for line in lines if run._simple_stmt(line)) - len(cands)
    cands.sort(key=lambda n: hashlib.sha256(f"read:{src['id']}:{n}".encode()).hexdigest())
    out, base = [], pair_id(src["id"])
    common = {k: src.get(k) for k in ("repository", "variant", "file", "function", "split", "split_group", "opt",
                                      "recipe", "compiler")}
    for n in cands:
        if len([o for o in out if o["kind"] == "read"]) >= per_fn:
            break
        stmt = lines[n - 1].strip()
        if sum(1 for line in lines if line.strip() == stmt) > 1:
            tally["answer-elsewhere-in-function"] += 1
            continue
        indent = lines[n - 1][:len(lines[n - 1]) - len(lines[n - 1].lstrip())]
        marked = "\n".join(lines[:n - 1] + [indent + MARK] + lines[n:])
        o = pp.compile_row(src, ctx + "\n" + marked, "rdm", BMAP)
        cur = pp.listing(o, name) if o else None
        if cur is None:
            tally["blanked-not-compiling"] += 1
            continue
        if cur == target:
            tally["blanked-invisible"] += 1
            continue
        kind = stmt_kind(stmt)
        tally[f"read-{kind}"] += 1
        out.append({"kind": "read", "id": f"read_{kind}:{n}:{base}", "class": f"read_{kind}", "label": "differ",
                    "source_kind": "public-reading", **common, "context": ctx, "original_fn": fn,
                    "perturbed_fn": marked, "marker_line": n, "answer": stmt, "target": target, "current": cur,
                    "marked_rows": lm.rows_of(n), "diff": pp.mine.gnu_diff(target, cur), "stmt_kind": kind})
        deleted = "\n".join(lines[:n - 1] + lines[n:])
        if (src["function"], deleted) in known_drops:
            tally["drop-already-planted"] += 1
            continue
        o = pp.compile_row(src, ctx + "\n" + deleted, "rdd", BMAP)
        cur_d = pp.listing(o, name) if o else None
        if cur_d is None or cur_d == target:
            tally["drop-not-compiling-or-invisible"] += 1
            continue
        out.append({"kind": "drop", "id": f"drop_stmt:read{n}:{base}", "class": "drop_stmt", "label": "differ",
                    "source_kind": "public-reading-drop", **common, "context": ctx, "original_fn": fn,
                    "perturbed_fn": deleted, "target": target, "current": cur_d,
                    "diff": pp.mine.gnu_diff(target, cur_d), "stmt_kind": kind})
    return out


def work_multi(src: dict, build: dict, groups: int, k_max: int, tally: collections.Counter):
    """Multi-blank rows (--multi): 2..k_max statements of one function blanked together, so one prompt carries
    several reads (the context and listing are paid for once) and each blank is graded on its own as well."""
    name, ctx, fn, target = src["function"], src["context"], src["original_fn"], src["target"]
    if len(target) > MAX_LISTING:
        tally["listing-too-long"] += 1
        return []
    obj = pp.compile_row(src, ctx + "\n" + fn, "rm0", BMAP)
    if obj is None or pp.listing(obj, name) != target:
        tally["original-mismatch"] += 1
        return []
    try:
        lm = line_map.build(dump_of(line_table_object(src, ctx + "\n" + fn, target, obj)), name, target,
                            line_offset=(ctx + "\n").count("\n"))
    except ValueError:
        tally["no-line-table"] += 1
        return []
    lines = fn.split("\n")
    cands = [i + 1 for i, line in enumerate(lines) if run._simple_stmt(line) and lm.rows_of(i + 1)
             and sum(1 for other in lines if other.strip() == line.strip()) == 1]
    cands.sort(key=lambda n: hashlib.sha256(f"readm:{src['id']}:{n}".encode()).hexdigest())

    def blank(keep_out):
        out = list(lines)
        for n in keep_out:
            out[n - 1] = out[n - 1][:len(out[n - 1]) - len(out[n - 1].lstrip())] + MARK
        return "\n".join(out)

    def listing_of(text):
        o = pp.compile_row(src, ctx + "\n" + text, "rmb", BMAP)
        return pp.listing(o, name) if o else None

    rows, base, i = [], pair_id(src["id"]), 0
    common = {k: src.get(k) for k in ("repository", "variant", "file", "function", "split", "split_group", "opt",
                                      "recipe", "compiler")}
    while len(rows) < groups:
        k = 2 + hashlib.sha256(f"k:{src['id']}:{i}".encode()).digest()[0] % (k_max - 1)
        group = sorted(cands[i:i + k])
        i += k
        if len(group) < 2:
            break
        cur = listing_of(blank(group))
        if cur is None or cur == target:
            tally["multi-not-compiling-or-invisible"] += 1
            continue
        partial = {}
        for n in group:
            partial[str(n)] = listing_of(blank([m for m in group if m != n])) if len(group) > 1 else target
        if any(v is None or v == cur for v in partial.values()):
            tally["multi-blank-not-separately-visible"] += 1
            continue
        kinds = [stmt_kind(lines[n - 1]) for n in group]
        tally[f"multi-{len(group)}"] += 1
        rows.append({"kind": "readm", "id": f"read_multi:{'-'.join(map(str, group))}:{base}", "class": "read_multi",
                     "label": "differ", "source_kind": "public-reading-multi", **common, "context": ctx,
                     "original_fn": fn, "perturbed_fn": blank(group), "marker_lines": group,
                     "answers": {str(n): lines[n - 1].strip() for n in group},
                     "marked": {str(n): lm.rows_of(n) for n in group}, "target": target, "current": cur,
                     "partial_targets": partial, "stmt_kind": "+".join(sorted(set(kinds))), "stmt_kinds": kinds})
    return rows


def read_multi_task(row: dict) -> dict:
    owner = {r: n for n, rs in row["marked"].items() for r in rs}
    listing = "\n".join((f"{owner[i]}>" if i in owner else "").rjust(4) + " " + r for i, r in enumerate(row["target"]))
    prompt = repair_prompts.logic_read_multi_prompt(compiler=row.get("compiler") or lt.COMPILER,
                                                    opt=row.get("opt") or "-O2",
                                                    context=row["context"], numbered=lt.numbered(row["perturbed_fn"]),
                                                    lines=row["marker_lines"], listing=listing)
    body = prompt.replace(row["context"], "")
    if any(a in body for a in row["answers"].values()):
        raise lt.Leak(row["id"])
    completion = "\n".join(f"Line {n}: {row['answers'][str(n)]}" for n in row["marker_lines"])
    return {"kind": repair_prompts.LOGIC_READ_KIND, "prompt": prompt, "completion": completion}


def read_task(row: dict) -> dict:
    marked = set(row["marked_rows"])
    listing = "\n".join(("> " if i in marked else "  ") + r for i, r in enumerate(row["target"]))
    prompt = repair_prompts.logic_read_prompt(compiler=row.get("compiler") or lt.COMPILER,
                                              opt=row.get("opt") or "-O2",
                                              context=row["context"], numbered=lt.numbered(row["perturbed_fn"]),
                                              line=row["marker_line"], listing=listing)
    if row["answer"] in prompt.replace(row["context"], ""):
        raise lt.Leak(row["id"])
    return {"kind": repair_prompts.LOGIC_READ_KIND, "prompt": prompt, "completion": row["answer"]}


def to_tasks(rows: list[dict]) -> tuple[list[dict], collections.Counter]:
    tally, out = collections.Counter(), []
    for row in sorted(rows, key=lambda r: hashlib.sha256(f"order:{r['id']}".encode()).hexdigest()):
        split = lt.split_of(row, HOLDOUT)
        try:
            task = (read_task(row) if row["kind"] == "read" else read_multi_task(row) if row["kind"] == "readm"
                    else lt.explain_task(row))
        except lt.Leak:
            tally["refused-leak"] += 1
            continue
        except lt.TooLong:
            tally["refused-diff-too-long"] += 1
            continue
        except ValueError:
            tally["refused-script"] += 1
            continue
        tally[f"{split}/{task['kind']}/{row['class']}"] += 1
        out.append(task | {"id": f"{task['kind']}:{row['id']}", "row_id": row["id"], "variant": "base",
                           "class": row["class"], "stmt_kind": row["stmt_kind"], "label": "differ", "split": split,
                           "split_group": row.get("split_group"), "repository": row.get("repository"),
                           "function": row.get("function"), "provenance": row["source_kind"],
                           "prompt_version": repair_prompts.LOGIC_PROMPT_VERSION})
    return out, tally


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--context", type=Path, default=pp.OUT / "context-v3.jsonl")
    ap.add_argument("--out-rows", type=Path, default=pp.OUT / "reading-v1.jsonl")
    ap.add_argument("--out-tasks", type=Path, default=pp.OUT / "reading-v1")
    ap.add_argument("--per-fn-train", type=int, default=8)
    ap.add_argument("--per-fn-eval", type=int, default=3)
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--multi", type=int, default=0, help="multi-blank rows only, 2..K statements per prompt")
    ap.add_argument("--groups-train", type=int, default=4)
    ap.add_argument("--groups-eval", type=int, default=2)
    a = ap.parse_args(argv)
    if a.out_rows.exists():
        raise SystemExit(f"{a.out_rows} exists: generated rows are never overwritten")
    src_rows = [json.loads(line) for line in open(a.context)]
    known_drops = {(r["function"], r["perturbed_fn"]) for r in src_rows
                   if r.get("class") == "drop_stmt" and r.get("perturbed_fn")}
    fns = one_row_per_function(src_rows)
    fns.sort(key=lambda r: hashlib.sha256(r["id"].encode()).hexdigest())
    if a.limit:
        fns = fns[:a.limit]
    bmap = pp.builds()
    BMAP.update(bmap)
    tally, lock, started = collections.Counter(), threading.Lock(), time.time()

    def one(src):
        t = collections.Counter()
        b = bmap.get((f"{src['repository']}.{src['variant']}", src["file"]))
        if b is None and not src.get("recipe"):
            t["no-build"] += 1
            return [], t
        is_train = src["split"] == "train" and src["repository"] not in HOLDOUT
        try:
            if a.multi:
                return work_multi(src, b, a.groups_train if is_train else a.groups_eval, a.multi, t), t
            return work(src, b, a.per_fn_train if is_train else a.per_fn_eval, known_drops, t), t
        except Exception as exc:                  # counted and reported, never swallowed silently
            t[f"error:{type(exc).__name__}"] += 1
            return [], t

    rows, done = [], 0
    with concurrent.futures.ThreadPoolExecutor(a.jobs) as ex:
        for out, t in ex.map(one, fns):
            with lock:
                tally.update(t)
                rows += out
                done += 1
                if done % 50 == 0:
                    print(f"{done}/{len(fns)} functions, {len(rows)} rows, {time.time() - started:.0f}s", flush=True)
    a.out_rows.write_text("".join(json.dumps(r) + "\n" for r in rows))
    tasks, ttally = to_tasks(rows)
    a.out_tasks.mkdir(parents=True, exist_ok=True)
    data = "".join(json.dumps(t) + "\n" for t in tasks).encode("utf-8")
    (a.out_tasks / "tasks.jsonl").write_bytes(data)
    manifest = {"functions": len(fns), "rows": len(rows), "tasks": len(tasks), "seconds": round(time.time() - started),
                "rows_sha256": hashlib.sha256(a.out_rows.read_bytes()).hexdigest(),
                "tasks_sha256": hashlib.sha256(data).hexdigest(), "context": str(a.context),
                "tally": dict(tally), "tasks_by": dict(sorted(ttally.items())),
                "prompt_version": repair_prompts.LOGIC_PROMPT_VERSION}
    (a.out_tasks / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(json.dumps(manifest, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
