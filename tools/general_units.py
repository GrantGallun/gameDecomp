"""Training units from GENERAL C code (not game decomps), compiled with every available MIPS recipe.

    python3 -m tools.general_units --root ~/decomp/general-c --out general-units.jsonl [--jobs 6]

Game decomps are a sliver of the C there is; any C a period compiler accepts is valid practice for reading compiled
code, and general code is nobody's decomp answer key. Each .c file is preprocessed with GNU cpp against pycparser's
stand-in libc headers (no host GNU extensions), parsed, and split into units: one function plus the context closure
it needs (tools/context_closure.py). Each unit is compiled with each recipe (tools/compiler_recipes.py); a row per
(function, recipe) keeps the function's listing under that compiler as its target.

There is no full-file build to match, so the standalone unit IS the ground truth: whatever the compiler makes of
it. Refusals (preprocess, parse, closure, compile, size) are counted per reason, never silent. These projects are
famous and surely in the base model's pretraining data: rows are TRAIN data only (split "train"), never an exam.
"""
from __future__ import annotations

import argparse
import copy
import collections
import concurrent.futures
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import compiler_recipes as cr
from tools import context_closure as cc

PROJECTS = ("lua", "zlib", "cJSON", "inih", "jsmn", "miniz", "tinyexpr", "linenoise")
DEFINES = ["-D__attribute__(x)=", "-D__extension__=", "-D__restrict=", "-D__restrict__=", "-D__inline=",
           "-D__inline__=", "-Dinline=", "-D__asm__(x)=", "-DNDEBUG"]


def preprocess(path: Path, project_root: Path, fake: Path) -> str | None:
    # The project's own headers first: pycparser's stand-ins include e.g. a `zlib.h` whose `typedef int z_stream`
    # shadowed zlib's real one when searched first.
    cmd = ["cpp", "-P", "-nostdinc", "-I", str(path.parent), "-I", str(project_root), "-I", str(fake), *DEFINES,
           str(path)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return p.stdout if p.returncode == 0 else None


def _external(decl) -> None:
    """Drop `static` / `inline`: a unit's helpers become prototypes (IDO rejects a static function declared and
    never defined; GCC only warns), and IDO emits no symbol for an unreferenced static target."""
    decl.storage = [s for s in decl.storage if s != "static"]
    decl.funcspec = [s for s in decl.funcspec if s != "inline"]


def _c89_context(nodes: list) -> list:
    """Context items as C89 needs them: FuncDef helpers as external prototypes, and one declaration per typedef name
    (the stand-in libc and a project header may both declare one; C89 forbids the repeat, GCC tolerates it)."""
    from pycparser import c_ast
    out, typedefs = [], set()
    for n in nodes:
        if isinstance(n, c_ast.FuncDef):
            n = copy.deepcopy(n)
            _external(n.decl)
        elif isinstance(n, c_ast.Decl) and isinstance(n.type, c_ast.FuncDecl):
            n = copy.deepcopy(n)
            _external(n)
        elif isinstance(n, c_ast.Typedef):
            if n.name in typedefs:
                continue
            typedefs.add(n.name)
        out.append(n)
    return out


def units_of(path: Path, project: str, root: Path, fake: Path, tally: collections.Counter, max_context: int):
    pre = preprocess(path, root / project, fake)
    if pre is None:
        tally["preprocess-failed"] += 1
        return []
    try:
        ast = cc.parse(pre)
    except Exception:
        tally["parse-failed"] += 1
        return []
    out = []
    for e in ast.ext:
        if type(e).__name__ != "FuncDef":
            continue
        name = e.decl.name
        try:
            nodes = _c89_context([ast.ext[i] for i in cc.closure(ast, name)])
            context = cc.render(nodes)
            target = copy.deepcopy(e)
            _external(target.decl)
            fn = cc.render_function(target)
        except Exception:
            tally["closure-failed"] += 1
            continue
        if len(context) > max_context:
            tally["context-too-long"] += 1
            continue
        out.append((name, context, fn))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=Path.home() / "decomp/general-c")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--projects", default=",".join(PROJECTS))
    ap.add_argument("--recipes", default="")
    ap.add_argument("--min-rows", type=int, default=6)
    ap.add_argument("--max-rows", type=int, default=120)
    ap.add_argument("--max-context", type=int, default=5000)
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval/results/edit-capability-20261002"))
    import public_plant as pp
    recipes = [r for r in (a.recipes.split(",") if a.recipes else cr.RECIPES) if cr.available(r)]
    fake = a.root / "pycparser/utils/fake_libc_include"
    tally = collections.Counter()
    units = []
    for project in a.projects.split(","):
        for path in sorted((a.root / project).rglob("*.c")):
            rel = str(path.relative_to(a.root / project))
            for name, context, fn in units_of(path, project, a.root, fake, tally, a.max_context):
                units.append((project, rel, name, context, fn))
    seen = set()
    units = [u for u in units if not ((u[0], u[2], u[4]) in seen or seen.add((u[0], u[2], u[4])))]
    work = Path(tempfile.mkdtemp(prefix="general_units_"))

    def compile_all(u):
        project, rel, name, context, fn = u
        rows, t = [], collections.Counter()
        for recipe in recipes:
            obj, _msg = cr.compile_unit(recipe, context + "\n" + fn, work, f"g{recipe}")
            listing = pp.listing(obj, name) if obj else None
            if listing is None:
                t[f"{recipe}/compile-failed"] += 1
                continue
            if not a.min_rows <= len(listing) <= a.max_rows:
                t[f"{recipe}/size-out-of-range"] += 1
                continue
            r = cr.RECIPES[recipe]
            rows.append({"id": f"general:{recipe}:{project}:{rel}:{name}", "repository": f"general-{project}",
                         "variant": "", "file": rel, "function": name, "split": "train",
                         "split_group": f"general-{project}:{rel}", "recipe": recipe, "compiler": r["compiler"],
                         "opt": r["opt"], "context": context, "original_fn": fn, "target": listing,
                         "source_kind": "general-code"})
            t[f"{recipe}/ok"] += 1
        return rows, t

    n = 0
    with concurrent.futures.ThreadPoolExecutor(a.jobs) as ex, open(a.out, "w") as f:
        for rows, t in ex.map(compile_all, units):
            tally.update(t)
            for row in rows:
                f.write(json.dumps(row) + "\n")
                n += 1
    receipt = {"units": len(units), "rows": n, "recipes": recipes, "tally": dict(sorted(tally.items())),
               "projects": {p: subprocess.run(["git", "-C", str(a.root / p), "rev-parse", "HEAD"], capture_output=True,
                                              text=True).stdout.strip() for p in a.projects.split(",")},
               "rows_sha256": hashlib.sha256(a.out.read_bytes()).hexdigest()}
    a.out.with_suffix(".receipt.json").write_text(json.dumps(receipt, indent=1))
    print(json.dumps(receipt, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
