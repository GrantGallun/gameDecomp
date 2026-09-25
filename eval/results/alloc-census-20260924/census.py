"""Allocation census of the 130 structurally-correct campaign functions (PROTOCOL.md). Serial; no KB writes."""
import collections, json, sqlite3, sys
from pathlib import Path
sys.path.insert(0, "/mnt/c/Code/gameDecomp")
sys.path.insert(0, "/mnt/c/Code/gameDecomp/eval/results/draft-reference-mining-20260924")
import pairs
from solver import uopt_diagnosis

HERE = Path(__file__).resolve().parent
FR = HERE.parent / "frontier-20260924"
TRACE_CC = Path.home() / "decomp/tools-src/ido-trace/cc"
H = Path.home() / "decomp"


def main():
    fronts = json.loads((FR / "fronts.json").read_text())
    frontier = {r["function"]: r for r in json.loads((FR / "frontier.json").read_text())["rows"]}
    pool = [r["function"] for r in fronts["rows"] if r["structural"] == 0 and r["size"] in ("small", "medium")]
    db = sqlite3.connect(f"file:{H / 'runs/resume-pipeline-20260908/campaign.sqlite'}?mode=ro", uri=True)
    mirror = pairs.mirror_repo()
    rows = []
    for name in pool:
        best = frontier[name]
        source = db.execute("select source_code from attempts where id=?", (best["attempt"],)).fetchone()[0]
        ws = pairs.workspace(mirror, name)
        b = pairs.build(ws, "census", source)
        if not b["compiled"]:
            rows.append({"function": name, "status": "not-compiled-here"})
            continue
        if abs((b["score"] or 0) - best["score"]) > 0.01:
            rows.append({"function": name, "status": "score-not-reproduced", "here": b["score"], "campaign": best["score"]})
            continue
        texts = uopt_diagnosis.traced_compile(ws, mirror, source, TRACE_CC, name)
        if not texts:
            rows.append({"function": name, "status": "no-trace"})
            continue
        target = (ws / "target_object_dump_normalized.s").read_text(errors="replace")
        report = uopt_diagnosis.diagnose(target, b["dump"], texts["level5"], texts["level6"], texts["ugen"], name)
        if not report or report.get("declined"):
            rows.append({"function": name, "status": "declined", "why": (report or {}).get("declined")})
            continue
        wrong = [e for e in report["ranges"] if e["class"] != "ok"]
        rows.append({"function": name, "status": "diagnosed", "size": best["size"], "score": best["score"],
                     "first": wrong[0]["class"] if wrong else "none", "classes": [e["class"] for e in wrong],
                     "kinds": [e.get("kind") for e in wrong], "unattributed": report.get("unattributed")})
        print(name, rows[-1]["first"], rows[-1]["classes"], flush=True)
    diag = [r for r in rows if r["status"] == "diagnosed"]
    first = collections.Counter(r["first"] for r in diag)
    n = len(diag) or 1
    reach = (first["blocked"] + first["ugen_temp"]) / n
    verdict = ("edit-reachable (blocked + ugen_temp >= 50%)" if reach >= 0.5 else
               "research first (selection >= 50%)" if first["selection"] / n >= 0.5 else "mixed")
    out = {"pool": len(pool), "status": dict(collections.Counter(r["status"] for r in rows)),
           "first_wrong": dict(first), "all_wrong": dict(collections.Counter(c for r in diag for c in r["classes"])),
           "first_by_size": {s: dict(collections.Counter(r["first"] for r in diag if r["size"] == s)) for s in ("small", "medium")},
           "verdict": verdict, "rows": rows}
    (HERE / "census.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "rows"}, indent=1))


if __name__ == "__main__":
    main()
