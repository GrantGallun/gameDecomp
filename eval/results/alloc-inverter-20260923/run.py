"""Allocator inverter v1 over every unsolved function: trace -> diagnose -> propose -> compile, up to 3 rounds.

Starts from each unsolved function's best node in the restart round-3 run (code-v9 search). Compiles are logged
through the population probe driver's database; traced compiles use the function's isolated workspace.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import alloc_inverter, uopt_diagnosis, uopt_trace  # noqa: E402

HERE = Path(__file__).resolve().parent
E = Path.home() / "decomp/experiments"
TRACE_CC = Path.home() / "decomp/tools-src/ido-trace/cc"
PT = Path("/mnt/c/Code/gameDecomp/eval/results/population-transfer-20260922")


def workspace(name):
    for run in ("restart-round3-20260923", "restart-round2-20260923", "locality-population-20260923"):
        for arm in ("routed", "locality"):
            repo = E / run / "ws" / name / arm
            if (repo / "nonmatchings" / name).exists():
                return repo
    return None


def build_dump(repo, name, source):
    ws = repo / "nonmatchings" / name
    (ws / f"{name}.c").write_text(source)
    r = subprocess.run(["bash", "-c", f". {repo}/.venv/bin/activate 2>/dev/null; bash build.sh {name}.c"], cwd=ws,
                       capture_output=True, text=True, timeout=300)
    dump = ws / f"{name}_object_dump_normalized.s"
    return dump.read_text(errors="replace") if dump.exists() and r.returncode == 0 else None


def compile_probes(probes):
    path = HERE / "probes-round.json"
    path.write_text(json.dumps(probes))
    subprocess.run(["python3", "probe.py", str(path)], cwd=PT, capture_output=True, text=True)
    import hashlib
    want = {hashlib.sha256(p["source"].encode()).hexdigest(): p for p in probes}
    out = {}
    for line in (PT / "probes.jsonl").read_text().splitlines():
        r = json.loads(line)
        if r["source_sha256"] in want:
            out[r["source_sha256"]] = r
    return out


def main():
    starts = {}
    for path in (E / "restart-round3-20260923/rows").glob("*.json"):
        row = json.loads(path.read_text())
        if row.get("exact") or not row.get("world"):
            continue
        world = json.loads(Path(row["world"]).read_text())["world"]
        best = next(n for n in world["nodes"] if n["id"] == row["best_id"])
        starts[row["function"]] = (best["source"], best["verdict"]["score"])
    log = []
    current = dict(starts)
    for rnd in range(3):
        probes = []
        for name, (source, score) in sorted(current.items()):
            repo = workspace(name)
            if not repo:
                continue
            texts = uopt_diagnosis.traced_compile(repo / "nonmatchings" / name, repo, source, TRACE_CC, name)
            dump = build_dump(repo, name, source) if texts else None
            if not texts or not dump:
                continue
            target = (repo / "nonmatchings" / name / "target_object_dump_normalized.s").read_text(errors="replace")
            report = uopt_diagnosis.diagnose(target, dump, texts["level5"], texts["level6"], texts["ugen"], name)
            proc = uopt_trace.join(texts["level5"], texts["level6"]).get(name)
            if report.get("declined") or not proc:
                continue
            for label, cand in alloc_inverter.propose(source, name, report, proc):
                probes.append({"function": name, "label": f"inverter:r{rnd}:{label}", "source": cand, "parent_score": score})
        if not probes:
            break
        results = compile_probes(probes)
        import hashlib
        improved = {}
        for p in probes:
            r = results.get(hashlib.sha256(p["source"].encode()).hexdigest())
            if not r or not r["compiled"]:
                continue
            log.append({"round": rnd, "function": p["function"], "label": p["label"], "score": r["score"],
                        "parent": p["parent_score"], "exact": r["exact"]})
            if r["exact"] or r["score"] > max(p["parent_score"], improved.get(p["function"], (None, -1))[1]):
                improved[p["function"]] = (p["source"], r["score"])
        print(json.dumps({"round": rnd, "candidates": len(probes), "compiled": sum(1 for l in log if l["round"] == rnd),
                          "improved_functions": len(improved),
                          "exact": sorted(l["function"] for l in log if l["round"] == rnd and l["exact"])}), flush=True)
        current = {f: v for f, v in improved.items() if v[1] < 100}
    (HERE / (sys.argv[1] if len(sys.argv) > 1 else "log.json")).write_text(json.dumps(log, indent=1))
    exact = sorted({l["function"] for l in log if l["exact"]})
    print(json.dumps({"exact_functions": exact, "improved_functions": len({l["function"] for l in log if l["score"] > l["parent"]})}))


if __name__ == "__main__":
    main()
