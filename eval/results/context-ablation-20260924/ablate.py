"""Context ablation (PROTOCOL.md): m2c drafts under four contexts, compiled in the reference TU harness.

    python3 ablate.py [--jobs 4] [--limit 600]   -> E/rows/<fn>.json, then summary.json here
"""
from __future__ import annotations

import collections
import concurrent.futures
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE.parent / "draft-reference-mining-20260924"))
import mine  # noqa: E402
import pairs  # noqa: E402
from solver.m2c_input import normalize_o32_registers  # noqa: E402
from tools import n64_corpus  # noqa: E402
import make_contexts  # noqa: E402
import re  # noqa: E402
import runpy  # noqa: E402
import threading  # noqa: E402

_CTX_LOCK = threading.Lock()
_M2CTX_NS = runpy.run_path(str(pairs.REPO / "tools/m2ctx.py"))
# m2ctx's fixed flags lack the recipe's SDK/audio include paths and defines (`<libaudio.h>` failed to preprocess);
# extend its module-level list in place with the recorded recipe's -I/-D flags.
_M2CTX_NS["CPP_FLAGS"].extend(["-I.", "-Iinclude/PR", "-Isrc/ultra/audio", "-Isrc/ultra/libc", "-DCOMPILING_LIBULTRA",
                               "-DBUILD_VERSION=VERSION_I", "-DF3DEX_GBI"])
_M2CTX = _M2CTX_NS["import_c_file"]


def declarations_only(source: str) -> str:
    """The TU with every function definition reduced to its prototype (as pairs.tu_candidate does for the others)."""
    out, cursor = [], 0
    for rec in n64_corpus.extract_functions(source):
        definition, body = str(rec["definition"]), str(rec["body"])
        start = source.find(definition, cursor)
        out += [source[cursor:start], definition[:len(definition) - len(body)].rstrip() + ";"]
        cursor = start + len(definition)
    out.append(source[cursor:])
    return pairs.ASM_MACRO.sub("", "".join(out))


def context_paths(root: Path) -> dict[str, Path]:
    """Per-TU contexts: m2ctx over the compile root with every definition reduced to a prototype, then the ablations."""
    key = hashlib.sha256(str(root).encode()).hexdigest()[:16]
    paths = {arm: E / "ctx" / f"{key}-{arm}.c" for arm in ARMS}
    with _CTX_LOCK:
        if all(p.exists() for p in paths.values()):
            return paths
        (E / "ctx").mkdir(parents=True, exist_ok=True)
        wrapper = E / "ctx" / f"{key}-wrapper.c"
        wrapper.write_text(declarations_only(pairs.expanded(root)))
        import os
        cwd = os.getcwd()
        os.chdir(pairs.REPO)
        try:
            pre = _M2CTX(str(wrapper))
        finally:
            os.chdir(cwd)
        for arm, text in make_contexts.arms(pre).items():
            paths[arm].write_text(text)
    return paths

E = Path.home() / "decomp/experiments/context-ablation-20260924"
M2C = pairs.REPO / ".venv/bin/m2c"
ARMS = ("NONE", "NOPROTO", "NOLAYOUT", "FULL")


def sample(limit: int) -> list[dict]:
    rows = []
    for path in (mine.E / "rows").glob("*.json"):
        row = json.loads(path.read_text())
        ref, target = row.get("ref") or {}, row.get("target_dump")
        if ref.get("compiled") and target and mine.mask(ref["dump"]) == mine.mask(target):
            rows.append(row)
    rows.sort(key=lambda r: hashlib.sha256(r["function"].encode()).hexdigest())
    return rows[:limit]


def m2c(ws: Path, arm: str, ctx: dict[str, Path]) -> tuple[str | None, str]:
    asm, _aliases = normalize_o32_registers((ws / "target.s").read_text())
    with tempfile.TemporaryDirectory(dir=E) as tmp:
        path = Path(tmp) / "target.s"
        path.write_text(asm)
        cmd = [str(M2C), "--target", "mips-ido-c", "--no-cache"]
        if arm != "NONE":
            cmd += ["--context", str(ctx[arm])]
        r = subprocess.run(cmd + [str(path)], cwd=pairs.REPO, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        return None, (r.stderr or r.stdout)[-400:]
    return r.stdout, ""


def one(mirror: Path, row: dict) -> dict:
    name = row["function"]
    ws = pairs.workspace(mirror, name)
    root = pairs.compile_root(name)
    source = pairs.expanded(root)
    target = mine.mask(row["target_dump"])
    out = {"function": name, "t_len": len(target)}
    try:
        ctx = context_paths(root)
    except (Exception, SystemExit) as exc:  # m2ctx calls sys.exit on failure; recorded, never silently dropped
        return out | {arm: {"status": "context-failed", "error": repr(exc)[-300:]} for arm in ARMS}
    for arm in ARMS:
        text, err = m2c(ws, arm, ctx)
        if text is None:
            out[arm] = {"status": "m2c-failed", "error": err}
            continue
        defs = [r for r in n64_corpus.extract_functions(text) if r["name"] == name]
        if len(defs) != 1:
            out[arm] = {"status": "no-definition"}
            continue
        code, _ = pairs.tu_candidate(source, name, str(defs[0]["definition"]))
        res = pairs.build(ws, f"abl_{arm}", code)
        if not res["compiled"]:
            out[arm] = {"status": "not-compiled", "error": (res.get("error") or "")[-300:]}
            continue
        exact = mine.mask(res["dump"]) == target
        out[arm] = {"status": "exact" if exact else "compiled", "score": res["score"]}
    return out


def summarize(rows: list[dict]) -> dict:
    def bucket(n):
        return "small" if n < 50 else "medium" if n < 150 else "large"
    summ = {}
    for arm in ARMS:
        c = collections.Counter(r[arm]["status"] for r in rows)
        by = collections.defaultdict(collections.Counter)
        for r in rows:
            by[bucket(r["t_len"])][r[arm]["status"]] += 1
        summ[arm] = {"n": len(rows), **c,
                     "exact_rate": round(c["exact"] / len(rows), 3),
                     "compile_rate": round((c["exact"] + c["compiled"]) / len(rows), 3),
                     "by_size": {k: {"n": sum(v.values()), "exact": v["exact"],
                                     "compiled_or_exact": v["exact"] + v["compiled"]} for k, v in by.items()}}
    f, n, nl, npr = (summ[a]["exact"] if "exact" in summ[a] else 0 for a in ("FULL", "NONE", "NOLAYOUT", "NOPROTO"))
    layout_loss, proto_loss = f - nl, f - npr
    summ["reading"] = {"FULL_minus_NONE": f - n, "loss_without_layouts": layout_loss,
                       "loss_without_prototypes": proto_loss,
                       "verdict": ("layouts dominate" if layout_loss >= 2 * proto_loss else
                                   "prototypes dominate" if proto_loss >= 2 * layout_loss else "both")}
    return summ


def main():
    jobs = int(sys.argv[sys.argv.index("--jobs") + 1]) if "--jobs" in sys.argv else 4
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 600
    (E / "rows").mkdir(parents=True, exist_ok=True)
    mirror = pairs.mirror_repo()
    rows = sample(limit)
    todo = [r for r in rows if not (E / "rows" / f"{r['function']}.json").exists()]
    print(len(rows), "sampled;", len(todo), "to run", flush=True)
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        for i, res in enumerate(pool.map(lambda r: one(mirror, r), todo), 1):
            (E / "rows" / f"{res['function']}.json").write_text(json.dumps(res))
            if i % 25 == 0:
                print(i, flush=True)
    done = [json.loads((E / "rows" / f"{r['function']}.json").read_text()) for r in rows]
    s = summarize(done)
    (HERE / "summary.json").write_text(json.dumps(s, indent=1))
    print(json.dumps({a: {k: s[a][k] for k in ("exact_rate", "compile_rate")} for a in ARMS} | {"reading": s["reading"]},
                     indent=1))


if __name__ == "__main__":
    main()
