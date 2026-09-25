"""BINARY arm (PROTOCOL.md A4): binary-derived context, standalone compile. Same sample as ablate.py.

    python3 run_binary.py [--jobs 4]   -> E/rows_binary/<fn>.json, binary_summary.json here"""
import collections, concurrent.futures, json, subprocess, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ablate  # noqa: E402  (sample, m2c-normalization, E, pairs/mine harness, extended m2ctx)
import binary_context  # noqa: E402
from ablate import pairs, mine, n64_corpus  # noqa: E402

OUT = ablate.E / "rows_binary"
# common.h's public parts only: the reference repo's common.h also includes game/math/geometry.h (PROTOCOL A6)
CLEAN_PRELUDE = ('#include "include_asm.h"\n#include "compiler_diagnostics.h"\n#include <PR/mbi.h>\n'
                 "int sprintf(char *buffer, const char *format, ...);\n")


STRIDE = __import__("re").compile(r"&(\w+) \+ \([^()]*\* (0x[0-9A-Fa-f]+|\d+)\)\)")
# v2 (capability run 2): m2c also writes table indexing as `(i * N) + sym` and `sym + (i * N)`
STRIDE_V2 = [STRIDE, __import__("re").compile(r"\([^()]*\* (0x[0-9A-Fa-f]+|\d+)\) \+ &?(\w+)"),
             __import__("re").compile(r"&?(\w+) \+ \([^()]*\* (0x[0-9A-Fa-f]+|\d+)\)")]


def strides_v2(text: str) -> dict[str, int]:
    out = {}
    for sym, n in STRIDE_V2[0].findall(text):
        out[sym] = int(n, 0)
    for n, sym in STRIDE_V2[1].findall(text):
        out.setdefault(sym, int(n, 0))
    for sym, n in STRIDE_V2[2].findall(text):
        out.setdefault(sym, int(n, 0))
    return {k: v for k, v in out.items() if not k.startswith(("temp_", "var_", "arg", "sp"))}


def one(mirror, row):
    """Pass 1 with the binary context; if not exact and m2c's output indexes a global by a constant stride
    (`(&sym + (i * N))`), pass 2 declares that global as an array of an N-byte element struct."""
    first = attempt(mirror, row, None)
    if first.get("status") == "exact" or not first.get("strides"):
        return first
    second = attempt(mirror, row, first["strides"])
    second["pass"] = 2
    return second if _rank(second) >= _rank(first) else first


def _rank(r):
    return {"exact": 3, "compiled": 2}.get(r.get("status"), 0) * 1000 + (r.get("score") or 0)


def attempt(mirror, row, strides):
    name = row["function"]
    ws = pairs.workspace(mirror, name)
    target = mine.mask(row["target_dump"])
    res = {"function": name, "t_len": len(target)}
    try:
        decls = binary_context.decls(name, (ws / "target.s").read_text(), strides)
    except Exception as exc:  # recorded
        return res | {"status": "context-failed", "error": repr(exc)[-300:]}
    header = CLEAN_PRELUDE + decls
    wrapper = OUT / f"{name}.ctx-wrapper.c"
    wrapper.write_text(header)
    import os
    try:
        with ablate._CTX_LOCK:
            cwd = os.getcwd(); os.chdir(pairs.REPO)
            try:
                pre = ablate._M2CTX(str(wrapper))
            finally:
                os.chdir(cwd)
    except (Exception, SystemExit) as exc:
        return res | {"status": "context-failed", "error": repr(exc)[-300:]}
    ctx = OUT / f"{name}.ctx.c"
    ctx.write_text(pre)
    asm, _ = ablate.normalize_o32_registers((ws / "target.s").read_text())
    with tempfile.TemporaryDirectory(dir=ablate.E) as tmp:
        p = Path(tmp) / "target.s"
        p.write_text(asm)
        r = subprocess.run([str(ablate.M2C), "--target", "mips-ido-c", "--no-cache", "--context", str(ctx), str(p)],
                           cwd=pairs.REPO, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        return res | {"status": "m2c-failed", "error": (r.stderr or r.stdout)[-300:]}
    defs = [d for d in n64_corpus.extract_functions(r.stdout) if d["name"] == name]
    if len(defs) != 1:
        return res | {"status": "no-definition"}
    res["strides"] = {sym: int(n, 0) for sym, n in STRIDE.findall(str(defs[0]["definition"]))}
    code = header + "\n" + str(defs[0]["definition"]) + "\n"
    b = pairs.build(ws, "abl_BINARY", code)
    if not b["compiled"]:
        return res | {"status": "not-compiled", "error": (b.get("error") or "")[-300:], "source": code[-3000:]}
    exact = mine.mask(b["dump"]) == target
    return res | {"status": "exact" if exact else "compiled", "score": b["score"],
                  "source": code if exact else None}


def main():
    jobs = int(sys.argv[sys.argv.index("--jobs") + 1]) if "--jobs" in sys.argv else 4
    OUT.mkdir(parents=True, exist_ok=True)
    mirror = pairs.mirror_repo()
    rows = ablate.sample(600)[150:]          # CHECK only (PROTOCOL A5); DEV rows live in rows_binary_dev
    todo = [r for r in rows if not (OUT / f"{r['function']}.json").exists()]
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        for i, res in enumerate(pool.map(lambda r: one(mirror, r), todo), 1):
            (OUT / f"{res['function']}.json").write_text(json.dumps(res))
            if i % 50 == 0:
                print(i, flush=True)
    done = [json.loads((OUT / f"{r['function']}.json").read_text()) for r in rows]
    c = collections.Counter(d["status"] for d in done)
    by = collections.defaultdict(collections.Counter)
    for d in done:
        by["small" if d["t_len"] < 50 else "medium" if d["t_len"] < 150 else "large"][d["status"]] += 1
    s = {"n": len(done), **c, "exact_rate": round(c["exact"] / len(done), 3),
         "compile_rate": round((c["exact"] + c["compiled"]) / len(done), 3), "by_size": {k: dict(v) for k, v in by.items()}}
    (HERE / "binary_summary.json").write_text(json.dumps(s, indent=1))
    print(json.dumps(s, indent=1))


if __name__ == "__main__":
    main()
