"""Planted repair tasks on PUBLIC decomp functions (SM64, MK64, DKR), compiled in their own translation unit.

    python3 public_plant.py [--limit N] [--jobs 6] [--multi] [--out NAME]   -> E/public/NAME.jsonl + NAME.receipt.json

Training supply for the edit/repair model that never touches SBK1: functions come from
~/decomp/public-pairs-20260921-v2 (3,001 IDO 5.3 pairs; the grabber already refused SBK1/SBK2 by URL and by
normalized-hash / near-duplicate against their source). An earlier repair builder accepted 27 of 240 because it
compiled each function ALONE (213 `target_not_self_contained`). Here the damaged definition is written back into a
copy of its own file, beside the original so relative includes resolve, and compiled with that file's recorded
compiler, flags and cwd (builds/<key>.json). Nothing in the repository is executed.

Each task: one visible planted edit (or 2-3 with --multi, each step visible) from PERTURB (run.PERTURB + context-sensitive div_shift, mod_mask; multi uses run.PERTURB). The listing is the
SBK1 harness's own normalizer (nonmatchings/*/objdump.py process_objdump_lines) applied to the function's slice
of the TU object, with branch targets rebased to the function start, so diffs read exactly like SBK1's.
Split is the corpus's own file-grouped split (`split`, `split_group`); never re-split by function.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import run
from run import E, mine

PUBLIC = Path.home() / "decomp/public-pairs-20260921-v2"
OUT = E / "public"
_ws = sorted((Path.home() / "decomp/sbk1/nonmatchings").glob("*/objdump.py"))[0]
_spec = importlib.util.spec_from_file_location("ws_objdump", _ws)
ws_objdump = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ws_objdump)
NORMALIZER = {"path": str(_ws), "sha256": hashlib.sha256(_ws.read_bytes()).hexdigest()}
HEADER = re.compile(r"^([0-9a-f]+) <([^>]+)>:$")
_tls = threading.local()


def listing(obj: Path, name: str) -> list[str] | None:
    """The function's normalized rows, or None when the object cannot be read (treated like a failed compile)."""
    proc = subprocess.run([ws_objdump.find_objdump_executable(), *ws_objdump.OBJDUMP_ARGS, str(obj)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    raw = proc.stdout.splitlines()
    start, out, inside = 0, [], False
    for line in raw:
        m = HEADER.match(line)
        if m:
            inside = m.group(2) == name
            if inside:
                start = int(m.group(1), 16)
            continue
        if inside:
            out.append(line)
    if not out:
        return None
    lines = ws_objdump.process_objdump_lines(["skip"] + out)

    def rebase(line):
        parts = line.split()
        if parts and parts[0] in ws_objdump.BRANCH_INSTRUCTIONS and parts[0] not in ("j", "jal"):
            return re.sub(r"([ ,])([0-9a-f]+)$", lambda m: f"{m.group(1)}{int(m.group(2), 16) - start:x}", line)
        return line
    return [rebase(line) for line in lines]


def compile_tu(build: dict, text: str, tag: str) -> Path | None:
    """Compile `text` as a copy of the file, beside it, with the recorded recipe. Returns the object or None."""
    return compile_tu_err(build, text, tag)[0]


def compile_tu_err(build: dict, text: str, tag: str) -> tuple[Path | None, str]:
    """(object or None, the compiler's own messages). Paths are rewritten to `candidate.c` so a message never names
    a scratch file or the checkout."""
    src = Path(build["source_file"])
    work = Path(os.environ.get("TMPDIR", "/tmp")) / "public_plant"
    work.mkdir(exist_ok=True)
    copy = src.parent / f".ecplant_{os.getpid()}_{threading.get_ident()}_{tag}.c"
    obj = work / f"{os.getpid()}_{threading.get_ident()}_{tag}.o"
    copy.write_text(text, encoding="utf-8")
    obj.unlink(missing_ok=True)       # a stale object from an earlier compile must never stand in for this one
    try:
        r = subprocess.run([build["compiler"], "-c", *build["flags"], "-o", str(obj),
                            str(copy.relative_to(build["cwd"]))], cwd=build["cwd"], capture_output=True, text=True,
                           timeout=300)
    except subprocess.TimeoutExpired:
        return None, "compile timed out"
    finally:
        copy.unlink(missing_ok=True)
    messages = (r.stderr or "").replace(str(copy.relative_to(build["cwd"])), "candidate.c").replace(str(copy),
                                                                                                    "candidate.c")
    return (obj if r.returncode == 0 and obj.exists() else None), messages


def compile_row_err(row: dict, text: str, tag: str, bmap: dict | None = None,
                    extra: tuple[str, ...] = ()) -> tuple[Path | None, str]:
    """Compile `text` the way `row` was compiled: a row with a `recipe` (tools/compiler_recipes.py: general code or
    another compiler) as a standalone unit with that recipe; a game row with its file's recorded build (compile_tu).
    The one entry point every grader and generator uses, so a task is always graded by the compiler it was made
    with."""
    if row.get("recipe"):
        from tools import compiler_recipes as cr
        work = Path(os.environ.get("TMPDIR", "/tmp")) / "public_plant_units"
        return cr.compile_unit(row["recipe"], text, work, tag, extra)
    build = (bmap if bmap is not None else builds())[(f"{row['repository']}.{row['variant']}", row["file"])]
    return compile_tu_err(build, text, tag)


def compile_row(row: dict, text: str, tag: str, bmap: dict | None = None,
                extra: tuple[str, ...] = ()) -> Path | None:
    return compile_row_err(row, text, tag, bmap, extra)[0]


def builds() -> dict:
    out = {}
    for p in (PUBLIC / "builds").glob("*.json"):
        for rel, b in json.loads(p.read_text()).items():
            out[(p.stem, rel)] = b
    return out


SAME_PER_CLASS = 2        # erased edits kept per (function, class): the compiler's "these spellings are one program"

# Context-sensitive spellings: whether the compiler erases them depends on a type the function may not show
# (signed `x / 2` needs a bias, unsigned does not). The planted classes above almost never flip with context (2 of
# 670 single-fact flips on the context pilot), so these supply the "what do I need to know" cases.
POW2 = {2 ** k: k for k in range(1, 16)}


def p_div_shift(lines, rng):
    def repl(m):
        v = int(m.group(3), 0)
        if m.group(2) == "/" and v in POW2:
            return f"{m.group(1)} >> {POW2[v]}"
        if m.group(2) == ">>" and 1 <= v <= 15:
            return f"{m.group(1)} / {2 ** v}"
        return None
    return run._sub_each(lines, rng, rf"({run.ATOM}) (/|>>) (0x[0-9A-Fa-f]+|\d+)(?![\w.])", repl, run._isolated)


def p_mod_mask(lines, rng):
    def repl(m):
        v = int(m.group(3), 0)
        if m.group(2) == "%" and v in POW2:
            return f"{m.group(1)} & {v - 1}"
        if m.group(2) == "&" and v + 1 in POW2 and v > 0:
            return f"{m.group(1)} % {v + 1}"
        return None
    return run._sub_each(lines, rng, rf"({run.ATOM}) (%|&) (0x[0-9A-Fa-f]+|\d+)(?![\w.])", repl, run._isolated)


PERTURB = {**run.PERTURB, "div_shift": p_div_shift, "mod_mask": p_mod_mask}


def opt_of(build: dict) -> str:
    flags = build["flags"]
    return " ".join(f for f in flags if f.startswith("-O") or f in ("-g", "-g2", "-g3", "-mips1", "-mips2", "-mips3"))


def plant_function(pair: dict, build: dict, multi: bool, tally: collections.Counter,
                   same: list | None = None) -> list[dict]:
    name, d = pair["function"], pair["source"]
    tu = Path(build["source_file"]).read_text(encoding="utf-8", errors="replace")
    if tu.count(d) != 1:
        tally["definition-not-unique-in-file"] += 1
        return []
    pos = tu.index(d)
    head, tail = tu[:pos], tu[pos + len(d):]
    obj = compile_tu(build, tu, "orig")
    target = listing(obj, name) if obj else None
    if not target:
        tally["original-not-compiling"] += 1
        return []
    lines0 = run._lines(d)
    try:
        rng0 = run._body_range(lines0)
    except (ValueError, StopIteration):
        tally["no-body"] += 1
        return []
    tasks = []
    plans = ([(c,) for c in sorted(PERTURB)] if not multi else
             [tuple(sorted(run.PERTURB, key=lambda c: hashlib.sha256(f"{name}{k}{i}{c}".encode()).hexdigest())[:k])
              for k in (2, 3) for i in range(3)])
    plans = list(dict.fromkeys(plans))        # a repeated plan made a repeated task id (audit 2026-10-03: 1,023 -> 1,009)
    for seq in plans:
        lines, previous, steps, sites = lines0, target, [], []
        for cls in seq:
            try:
                rng = run._body_range(lines)
            except (ValueError, StopIteration):
                break
            cands = [(nl, s) for nl, s in PERTURB[cls](lines, rng) if all(abs(s - t) > 1 for t in sites)]
            cands.sort(key=lambda c: hashlib.sha256(f"{cls}{name}{c[1]}{len(steps)}".encode()).hexdigest())
            for new_lines, site in cands[:3]:
                o = compile_tu(build, head + "\n".join(new_lines) + tail, cls)
                cur = listing(o, name) if o else None
                if cur is None:
                    tally["step-not-compiling"] += 1
                    continue
                if cur == previous:
                    tally["step-invisible"] += 1
                    # First step only: the pair is (original, one edit) and the compiler erased the edit.
                    if same is not None and not steps and \
                            sum(1 for s in same if s["class"] == cls) < SAME_PER_CLASS:
                        same.append({"id": f"same:{cls}:{len(same)}:{pair['id']}", "class": cls, "label": "same",
                                     "source_kind": "public-planted-erased", "repository": pair["repository"],
                                     "variant": pair["variant"], "file": pair["file"], "function": name,
                                     "split": pair["split"], "split_group": pair["split_group"],
                                     "opt": opt_of(build), "original_def": d, "perturbed_def": "\n".join(new_lines),
                                     "steps": [{"class": cls, "site": site,
                                                "text": new_lines[site].strip() if site < len(new_lines) else ""}],
                                     "target": target, "current": cur, "diff": ""})
                    continue
                steps.append({"class": cls, "site": site,
                              "text": new_lines[site].strip() if site < len(new_lines) else ""})
                lines, previous = new_lines, cur
                sites.append(site)
                break
            else:
                break
        if len(steps) == len(seq) and previous != target:
            k = len(steps)
            cls = steps[0]["class"] if k == 1 else f"k{k}"
            tally[f"planted-{cls if k == 1 else 'k' + str(k)}"] += 1
            tasks.append({"id": f"{cls}:{'+'.join(s['class'] for s in steps) if k > 1 else ''}:{pair['id']}",
                          "class": cls, "classes": [s["class"] for s in steps], "source_kind": "public-planted",
                          "repository": pair["repository"], "variant": pair["variant"], "file": pair["file"],
                          "function": name, "split": pair["split"], "split_group": pair["split_group"],
                          "opt": opt_of(build), "label": "differ", "original_def": d, "perturbed_def": "\n".join(lines), "steps": steps,
                          "target": target, "current": previous, "diff": mine.gnu_diff(target, previous)})
    if not tasks:
        tally["functions-without-task"] += 1
    return tasks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--multi", action="store_true")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    pairs = [json.loads(line) for line in open(PUBLIC / "pairs.jsonl")]
    pairs.sort(key=lambda p: hashlib.sha256(p["id"].encode()).hexdigest())
    if a.limit:
        pairs = pairs[:a.limit]
    bmap = builds()
    name = a.out or ("multi" if a.multi else "single") + (f"_pilot{a.limit}" if a.limit else "")
    tally, started = collections.Counter(), time.time()
    lock = threading.Lock()

    def work(p):
        t = collections.Counter()
        b = bmap.get((Path(p["context_ref"]).stem, p["file"]))
        if b is None:
            t["no-build-record"] += 1
            return [], t
        same = [] if not a.multi else None
        try:
            return plant_function(p, b, a.multi, t, same), t, same or []
        except Exception as e:                 # counted and reported, never swallowed silently
            t[f"error:{type(e).__name__}"] += 1
            return [], t, []

    n = n_same = 0
    with concurrent.futures.ThreadPoolExecutor(a.jobs) as ex, open(OUT / f"{name}.jsonl", "w") as f, \
            open(OUT / f"{name}_same.jsonl", "w") as fs:
        for tasks, t, same in ex.map(work, pairs):
            with lock:
                tally.update(t)
                for task in tasks:
                    f.write(json.dumps(task) + "\n")
                for row in same:
                    fs.write(json.dumps(row) + "\n")
                n += len(tasks)
                n_same += len(same)
            print(len(tasks), n, f"{time.time() - started:.0f}s", flush=True)
    receipt = {"pairs": len(pairs), "tasks": n, "erased_pairs": n_same,
               "seconds": round(time.time() - started), "tally": dict(tally),
               "normalizer": NORMALIZER, "source": str(PUBLIC), "multi": a.multi,
               "by_split": dict(collections.Counter(json.loads(l)["split"] for l in open(OUT / f"{name}.jsonl"))),
               "by_class": dict(collections.Counter(json.loads(l)["class"] for l in open(OUT / f"{name}.jsonl")))}
    (OUT / f"{name}.receipt.json").write_text(json.dumps(receipt, indent=1))
    print(json.dumps(receipt, indent=1))


if __name__ == "__main__":
    main()
