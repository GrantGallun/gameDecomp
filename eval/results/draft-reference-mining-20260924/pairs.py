"""Build (m2c draft, reference) pairs for the MINING split and compile both in the reference TU's context.

Mining split = every SBK1 workspace function MINUS the restart-round3 population (the functions whose coverage is
measured) MINUS every member of a sealed eval set (dev / heldout / cluster / panel keys of eval/sets/*.json). The
excluded functions' reference source is never read: the exclusion is applied before any src/ lookup.

Per mining function, in a copied workspace under ~/decomp/experiments (the target repo and the production KB are
not written):
  ref.c    the function's own TU with every OTHER function definition reduced to its prototype. Harness check: this
           must be byte-exact, or the row is unusable (and the rate is reported).
  draft.c  the same TU context with the function's reference definition replaced by m2c's base.c definition. The
           reference context (types, externs, statics) is what lets a raw m2c draft compile; that is legitimate only
           because these are mining functions. The residual is then m2c's shape gap, with types factored out.
Rows (which contain reference source) stay on the WSL side in E/rows; only aggregates go into the git tree.

    python3 pairs.py [--jobs 4] [--limit N] [--names a,b]
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools import n64_corpus  # noqa: E402

REPO = Path.home() / "decomp/sbk1"
E = Path.home() / "decomp/experiments/draft-reference-mining-20260924"
POPULATION = Path.home() / "decomp/experiments/restart-round3-20260923/rows"
SEALED_KEYS = ("dev", "heldout", "cluster", "panel")
ASM_MACRO = re.compile(r"^\s*(?:#pragma\s+GLOBAL_ASM|INCLUDE_ASM|INCLUDE_RODATA|GLOBAL_ASM)\b.*$", re.M)
WS_FILES = ("target.o", "target.s", ".compiler-target.json")


def _names(x) -> set[str]:
    if isinstance(x, str):
        return {x}
    if isinstance(x, dict):
        for key in ("function", "name"):
            if isinstance(x.get(key), str):
                return {x[key]}
        return set().union(*(_names(v) for v in x.values())) if x else set()
    if isinstance(x, list):
        return set().union(*(_names(v) for v in x)) if x else set()
    return set()


def excluded() -> tuple[set[str], set[str]]:
    population = {p.name.split("--")[0] for p in POPULATION.glob("*.json")}
    sealed: set[str] = set()
    for path in sorted((ROOT / "eval/sets").glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            for key in SEALED_KEYS:
                sealed |= _names(payload.get(key, []))
    return population, sealed


LOCAL_C_INCLUDE = re.compile(r'^[ 	]*#[ 	]*include[ 	]+"([^"]+\.c)"[ 	]*$', re.M)


def expanded(path: Path, depth: int = 0) -> str:
    """A TU's text with its local `#include "x.c"` fragments inlined (libmus compiles player.c, which includes
    player_api.c: the file that DEFINES a function is not always the file that is compiled)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if depth > 8:
        raise ValueError(f"include depth at {path}")

    def inline(m):
        inc = path.parent / m.group(1)
        return expanded(inc, depth + 1) if inc.exists() else m.group(0)
    return LOCAL_C_INCLUDE.sub(inline, text)


def compile_root(name: str) -> Path | None:
    """The TU the target object comes from, per the workspace's recorded compiler target (build/src/x.o)."""
    meta = REPO / "nonmatchings" / name / ".compiler-target.json"
    try:
        target = json.loads(meta.read_text())["target"]
    except (OSError, ValueError, KeyError):
        return None
    root = REPO / re.sub(r"^build/", "", target)
    root = root.with_suffix(".c")
    return root if root.exists() else None


def reference_index(names: set[str]) -> tuple[dict[str, Path], dict[str, str]]:
    """name -> compile root, for names defined exactly once in their expanded root. Only `names` (the mining
    candidates, exclusions already removed) are looked up. Returns (index, reasons for the rest)."""
    index, missing, cache = {}, {}, {}
    for name in sorted(names):
        root = compile_root(name)
        if root is None:
            missing[name] = "no C compile root"
            continue
        if root not in cache:
            cache[root] = [str(r["name"]) for r in n64_corpus.extract_functions(expanded(root))]
        count = cache[root].count(name)
        if count != 1:
            missing[name] = f"defined {count} times in expanded root"
            continue
        index[name] = root
    return index, missing


def tu_candidate(source: str, name: str, replacement: str | None) -> tuple[str, str]:
    """(candidate, reference definition): other definitions -> prototypes; `name` -> replacement (or itself)."""
    out, cursor, ref_def = [], 0, None
    for rec in n64_corpus.extract_functions(source):
        definition, body = str(rec["definition"]), str(rec["body"])
        start = source.find(definition, cursor)
        if start < 0:
            raise ValueError(f"definition of {rec['name']} not found in order")
        out.append(source[cursor:start])
        if rec["name"] == name:
            ref_def = definition
            out.append(replacement if replacement is not None else definition)
        else:
            out.append(definition[:len(definition) - len(body)].rstrip() + ";")
        cursor = start + len(definition)
    out.append(source[cursor:])
    if ref_def is None:
        raise ValueError(f"{name} not defined")
    return ASM_MACRO.sub("", "".join(out)), ref_def


def mirror_repo() -> Path:
    """E/repo: symlinks to every top-level entry of the target repo except nonmatchings/ (recipes resolve ../..)."""
    mirror = E / "repo"
    (mirror / "nonmatchings").mkdir(parents=True, exist_ok=True)
    for entry in REPO.iterdir():
        link = mirror / entry.name
        if entry.name != "nonmatchings" and not link.exists() and not link.is_symlink():
            link.symlink_to(entry)
    return mirror


def workspace(mirror: Path, name: str) -> Path:
    src, ws = REPO / "nonmatchings" / name, mirror / "nonmatchings" / name
    if ws.exists():
        return ws
    ws.mkdir(parents=True)
    for entry in src.iterdir():
        if entry.is_symlink():
            (ws / entry.name).symlink_to(os.readlink(entry))
        elif entry.name in WS_FILES or entry.name.startswith(".compiler-"):
            shutil.copy2(entry, ws / entry.name)
    return ws


def build(ws: Path, stem: str, code: str) -> dict:
    (ws / f"{stem}.c").write_text(code)
    recipes = sorted(ws.glob(".compiler-*.sh"))
    script = recipes[0].name if len(recipes) == 1 else "build.sh"
    try:
        r = subprocess.run(["bash", script, f"{stem}.c"], cwd=ws, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return {"compiled": False, "error": "timeout", "recipe": script}
    log = r.stdout + r.stderr
    m = re.search(r"Score: ([\d.]+)%", log)
    dump = ws / f"{stem}_object_dump_normalized.s"
    diff = ws / f"{stem}_diff"
    compiled = r.returncode == 0 and dump.exists() and m is not None
    return {"compiled": compiled, "recipe": script, "score": float(m.group(1)) if m else None,
            "exact": "Verified exact match: yes" in log,
            "dump": dump.read_text() if compiled else None,
            "diff": diff.read_text() if compiled and diff.exists() else None,
            "error": None if compiled else log[-1500:]}


def one(mirror: Path, name: str, tu: Path) -> dict:
    row = {"function": name, "tu": str(tu.relative_to(REPO))}
    base = (REPO / "nonmatchings" / name / "base.c")
    drafts = [r for r in n64_corpus.extract_functions(base.read_text(errors="replace"))
              if r["name"] == name] if base.exists() else []
    source = expanded(tu)
    ws = workspace(mirror, name)
    try:
        ref_code, ref_def = tu_candidate(source, name, None)
    except ValueError as exc:
        return row | {"status": f"no-reference: {exc}"}
    row["ref_def"] = ref_def
    row["ref"] = build(ws, "ref", ref_code)
    target = ws / "target_object_dump_normalized.s"
    row["target_dump"] = target.read_text() if target.exists() else None
    if len(drafts) != 1:
        return row | {"status": "no-draft"}
    row["draft_def"] = str(drafts[0]["definition"])
    draft_code, _ = tu_candidate(source, name, row["draft_def"])
    row["draft"] = build(ws, "draft", draft_code)
    row["status"] = "ok"
    return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--names", default="")
    args = ap.parse_args(argv)
    population, sealed = excluded()
    workspaces = {p.name for p in (REPO / "nonmatchings").iterdir() if p.is_dir()}
    index, missing = reference_index(workspaces - population - sealed)
    mining = sorted(index)
    if args.names:
        mining = [n for n in args.names.split(",") if n in mining]
    if args.limit:
        mining = mining[:args.limit]
    rows_dir = E / "rows"
    rows_dir.mkdir(parents=True, exist_ok=True)
    split = {"population": len(population), "sealed": len(sealed), "workspaces": len(workspaces),
             "no_reference": collections.Counter(v.split(":")[0] for v in missing.values()),
             "mining": len(mining)}
    (E / "split.json").write_text(json.dumps(split | {"population_names": sorted(population),
                                                      "sealed_names": sorted(sealed), "no_reference_names": missing},
                                          indent=1))
    print(json.dumps(split), flush=True)
    mirror = mirror_repo()
    todo = [n for n in mining if not (rows_dir / f"{n}.json").exists()]
    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        futures = {pool.submit(one, mirror, n, index[n]): n for n in todo}
        for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            n = futures[fut]
            try:
                row = fut.result()
            except Exception as exc:  # recorded, never silently dropped
                row = {"function": n, "status": f"crash: {exc!r}"}
            (rows_dir / f"{n}.json").write_text(json.dumps(row))
            ref, draft = row.get("ref") or {}, row.get("draft") or {}
            print(f"[{i}/{len(todo)}] {n} {row['status']} ref={ref.get('score')}{'*' if ref.get('exact') else ''} "
                  f"draft={draft.get('score')}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
