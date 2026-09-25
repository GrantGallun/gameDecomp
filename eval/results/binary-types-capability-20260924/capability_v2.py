"""Generator v2 (PROTOCOL-v2.md): identity v4 table elements, stride forms v2, no own prototype in the compile
header. Same harness as capability.py, on every function still without an exact attempt.

    python3 capability.py [--jobs 4]   -> E/rows/<fn>.json, summary.json here
Run with the target repo's venv python (pycparser for m2ctx context handling, as in context-ablation).
"""
import collections
import concurrent.futures
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ABL = HERE.parent / "context-ablation-20260924"
sys.path.insert(0, str(ABL))
import ablate  # noqa: E402
import binary_context  # noqa: E402
import run_binary  # noqa: E402
from ablate import pairs, mine, n64_corpus  # noqa: E402

E = Path.home() / "decomp/experiments/binary-types-capability-20260924/v2"
KB = Path.home() / "decomp/kb-sbk1.sqlite"


def never_exact() -> list[str]:
    conn = sqlite3.connect(f"file:{KB}?mode=ro", uri=True)
    exact = {n for (n,) in conn.execute("select distinct f.name from attempts a join functions f "
                                        "on f.addr=a.func_addr where a.exact=1")}
    ws = {p.name for p in (pairs.REPO / "nonmatchings").iterdir() if p.is_dir()}
    return sorted(ws - exact)


def attempt(mirror, name, strides):
    ws = pairs.workspace(mirror, name)
    res = {"function": name}
    try:
        decls = binary_context.decls(name, (ws / "target.s").read_text(), strides)
    except Exception as exc:
        return res | {"status": "context-failed", "error": repr(exc)[-300:]}
    header = run_binary.CLEAN_PRELUDE + decls
    wrapper = E / "ctx" / f"{name}.ctx-wrapper.c"
    wrapper.write_text(header)
    import os
    try:
        with ablate._CTX_LOCK:
            cwd = os.getcwd()
            os.chdir(pairs.REPO)
            try:
                pre = ablate._M2CTX(str(wrapper))
            finally:
                os.chdir(cwd)
    except (Exception, SystemExit) as exc:
        return res | {"status": "context-failed", "error": repr(exc)[-300:]}
    ctx = E / "ctx" / f"{name}.ctx.c"
    ctx.write_text(pre)
    asm, _ = ablate.normalize_o32_registers((ws / "target.s").read_text())
    with tempfile.TemporaryDirectory(dir=E) as tmp:
        p = Path(tmp) / "target.s"
        p.write_text(asm)
        r = subprocess.run([str(ablate.M2C), "--target", "mips-ido-c", "--no-cache", "--context", str(ctx), str(p)],
                           cwd=pairs.REPO, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        return res | {"status": "m2c-failed", "error": (r.stderr or r.stdout)[-300:]}
    defs = [d for d in n64_corpus.extract_functions(r.stdout) if d["name"] == name]
    if len(defs) != 1:
        return res | {"status": "no-definition"}
    res["strides"] = run_binary.strides_v2(str(defs[0]["definition"]))
    own = re.compile(r"^[^\n]*\b" + re.escape(name) + r"\([^\n]*\);\n", re.M)
    compile_header = run_binary.CLEAN_PRELUDE + own.sub("", decls)      # m2c keeps the prototype; the compile does not
    code = compile_header + "\n" + str(defs[0]["definition"]) + "\n"
    b = pairs.build(ws, "bintypes", code)
    target = ws / "target_object_dump_normalized.s"
    if not b["compiled"] or not target.exists():
        return res | {"status": "not-compiled", "error": (b.get("error") or "")[-300:]}
    t = mine.mask(target.read_text())
    exact = mine.mask(b["dump"]) == t
    return res | {"status": "exact" if exact else "compiled", "score": b["score"], "t_len": len(t),
                  "raw_exact": b["exact"], "source": code}


def one(mirror, name):
    first = attempt(mirror, name, None)
    if first.get("status") == "exact" or not first.get("strides"):
        return first
    second = attempt(mirror, name, first["strides"])
    second["pass"] = 2
    return second if run_binary._rank(second) >= run_binary._rank(first) else first


def main():
    jobs = int(sys.argv[sys.argv.index("--jobs") + 1]) if "--jobs" in sys.argv else 4
    (E / "rows").mkdir(parents=True, exist_ok=True)
    (E / "ctx").mkdir(parents=True, exist_ok=True)
    mirror = pairs.mirror_repo()
    names = never_exact()
    todo = [n for n in names if not (E / "rows" / f"{n}.json").exists()]
    print(len(names), "never-exact;", len(todo), "to run", flush=True)
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        for i, res in enumerate(pool.map(lambda n: one(mirror, n), todo), 1):
            (E / "rows" / f"{res['function']}.json").write_text(json.dumps(res))
            if i % 100 == 0:
                print(i, flush=True)
    rows = [json.loads((E / "rows" / f"{n}.json").read_text()) for n in names]
    c = collections.Counter(r["status"] for r in rows)
    by = collections.defaultdict(collections.Counter)
    for r in rows:
        if "t_len" in r:
            by["small" if r["t_len"] < 50 else "medium" if r["t_len"] < 150 else "large"][r["status"]] += 1
    pop = {json.loads(p.read_text())["function"] for p in
           (Path.home() / "decomp/experiments/restart-round3-20260923/rows").glob("*.json")}
    s = {"never_exact": len(names), **c, "by_size_compiled_rows": {k: dict(v) for k, v in by.items()},
         "exact_functions": sorted(r["function"] for r in rows if r["status"] == "exact"),
         "exact_in_restart_population": sorted(r["function"] for r in rows if r["status"] == "exact" and r["function"] in pop)}
    (HERE / "summary-v2.json").write_text(json.dumps(s, indent=1))
    print(json.dumps({k: v for k, v in s.items() if k != "exact_functions"}, indent=1))


if __name__ == "__main__":
    main()
