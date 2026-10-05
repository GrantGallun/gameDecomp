"""Frame-too-big -> inline a declared local, over the unsolved population (PROTOCOL-population.md)."""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import alloc_inverter, frontend_type_repair  # noqa: E402

HERE = Path(__file__).resolve().parent
PT = Path("/mnt/c/Code/gameDecomp/eval/results/population-transfer-20260922")
ROWS = Path.home() / "decomp/experiments/restart-round3-20260923/rows"
PROBE_WS = Path.home() / "decomp/experiments/population-transfer-20260922/probe-ws"
FRAME = re.compile(r"^([-+])addiu\s+sp,sp,-(0x[0-9a-f]+|\d+)$", re.M)
CAP = 8


def frames(diff):
    got = {s: int(v, 0) for s, v in FRAME.findall(diff or "")}
    return got.get("-"), got.get("+")


def local_names(source, name):
    b, e = alloc_inverter._body(source, name)
    return re.findall(r"^[ \t]*(?:[A-Za-z_]\w*[ \t]+)+\**[ \t]*([A-Za-z_]\w*)[ \t]*;[ \t]*$", source[b:e], re.M)


def compile_all(probes):
    if not probes:
        return {}
    path = HERE / "frame_population_probes.json"
    path.write_text(json.dumps(probes))
    subprocess.run(["python3", "probe.py", str(path)], cwd=PT, capture_output=True, text=True)
    want = {hashlib.sha256(p["source"].encode()).hexdigest() for p in probes}
    out = {}
    for line in (PT / "probes.jsonl").read_text().splitlines():
        r = json.loads(line)
        if r["source_sha256"] in want:
            out[r["source_sha256"]] = r
    return out


def frontend_of(name, sha):
    for f in (PROBE_WS / name / sha[:12]).rglob("*.frontend.json"):
        return json.loads(f.read_text())
    return None


def main():
    census = json.loads((HERE / "frame_census.json").read_text())["rows"]
    probes, meta = [], {}
    for path in sorted(ROWS.glob("*.json")):
        row = json.loads(path.read_text())
        if row.get("exact") or not row.get("world"):
            continue
        world = json.loads(Path(row["world"]).read_text())["world"]
        node = next(n for n in world["nodes"] if n["id"] == row["best_id"])
        target, cand = frames(node["verdict"].get("diff"))
        if target is None or cand is None or cand <= target:
            continue
        name, source = row["function"], node["source"]
        n = 0
        for var in local_names(source, name):
            out = alloc_inverter._inline(source, name, var)
            if out and n < CAP:
                n += 1
                probes.append({"function": name, "label": f"frame_inline:{var}", "source": out,
                               "parent_score": node["verdict"]["score"]})
                meta[hashlib.sha256(out.encode()).hexdigest()] = (name, var, target, cand, node["verdict"]["score"])
    fired = sorted({p["function"] for p in probes})
    print(json.dumps({"frame_too_big_functions_fired": len(fired), "candidates": len(probes)}), flush=True)
    results = compile_all(probes)
    rows = []
    followups = []
    for sha, (name, var, target, cand, parent) in meta.items():
        r = results.get(sha)
        if not r:
            continue
        new_target, new_cand = frames(r.get("diff"))
        frame_ok = r["compiled"] and (r["score"] == 100.0 or (new_target is None and new_cand is None))
        rows.append({"function": name, "variable": var, "compiled": r["compiled"], "exact": r["exact"],
                     "score": r["score"], "parent": parent, "frame_before": [target, cand],
                     "frame_after_matches": frame_ok, "source_sha256": sha})
        if r["compiled"] and r["score"] == 100.0 and not r["exact"]:
            followups.append((name, sha))
    # score-100 objects the gate rejected: up to two rounds of frontend_type_repair
    sources = {hashlib.sha256(p["source"].encode()).hexdigest(): p for p in probes}
    repaired = []
    for _round in range(2):
        batch = []
        for name, sha in followups:
            fe = frontend_of(name, sha)
            for label, cand in frontend_type_repair.variants(sources[sha]["source"], name, fe):
                p = {"function": name, "label": f"{sources[sha]['label']}+{label}", "source": cand, "parent_score": 100.0}
                batch.append(p)
                sources[hashlib.sha256(cand.encode()).hexdigest()] = p
        got = compile_all(batch)
        followups = []
        for p in batch:
            sha = hashlib.sha256(p["source"].encode()).hexdigest()
            r = got.get(sha)
            if r:
                repaired.append({"function": p["function"], "label": p["label"], "exact": r["exact"],
                                 "score": r["score"], "source_sha256": sha})
                if r["score"] == 100.0 and not r["exact"]:
                    followups.append((p["function"], sha))
    summary = {"functions_fired": len(fired), "candidates": len(rows),
               "frame_now_matches": sum(r["frame_after_matches"] for r in rows),
               "functions_frame_now_matches": len({r["function"] for r in rows if r["frame_after_matches"]}),
               "score_up": sum(r["score"] > r["parent"] for r in rows if r["compiled"]),
               "score_down": sum(r["score"] < r["parent"] for r in rows if r["compiled"]),
               "exact_objects_before_gate": sorted({r["function"] for r in rows if r["score"] == 100.0}),
               "exact_after_frontend": sorted({r["function"] for r in repaired if r["exact"]}),
               "exact_direct": sorted({r["function"] for r in rows if r["exact"]})}
    print(json.dumps(summary, indent=1))
    (HERE / "frame_population.json").write_text(json.dumps({"summary": summary, "rows": rows,
                                                            "frontend": repaired}, indent=1))


if __name__ == "__main__":
    main()
