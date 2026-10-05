"""Replay the promoted solver modules on the recorded cases; write fire-test fixtures. No compiles.

1. evidence_site: at each forward-run parent, does solver.evidence_site reproduce the improving candidates
   derive.py/forward.py produced?
2. frontend_type: on the five byte-exact frontend-rejected nodes, does solver.frontend_type_repair fire?
   Its candidates go to probes-promoted-frontend.json for compilation.
"""
import collections
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import evidence_site, frontend_type_repair  # noqa: E402

HERE = Path(__file__).resolve().parent
FIXTURES = Path("/mnt/c/Code/gameDecomp/tests/fixtures")
ROWS = Path.home() / "decomp/experiments/population-transfer-20260922/stage2/rows"


def best_node(name):
    row = json.loads((ROWS / f"{name}--routed.json").read_text())
    world = json.loads(Path(row["world"]).read_text())["world"]
    compiled = [n for n in world["nodes"] if n["verdict"]["compiled"] and not n["verdict"]["exact"]]
    return max(compiled, key=lambda n: n["verdict"]["score"])


def main():
    probes = json.loads((HERE / "probes-forward.json").read_text())
    results = {json.loads(l)["source_sha256"]: json.loads(l)
               for l in (HERE.parent / "population-transfer-20260922/probes.jsonl").read_text().splitlines()
               if l.strip() and '"forward:' in l}
    improving = collections.defaultdict(list)
    for p in probes:
        r = results.get(hashlib.sha256(p["source"].encode()).hexdigest())
        if r and r["compiled"] and r["score"] > p["parent_score"]:
            improving[p["function"]].append((p["label"].split(":", 1)[1].rsplit(":", 1)[0], p["source"], r["score"]))
    stats, fixtures = collections.Counter(), []
    for name, wins in sorted(improving.items()):
        node = best_node(name)
        v = node["verdict"]
        made = {c for _l, c in evidence_site.variants(node["source"], name, v.get("diff") or "", v.get("source_attribution"))}
        for sig, source, score in wins:
            stats["improving"] += 1
            stats["reproduced" if source in made else "lost"] += 1
            if source not in made:
                print("LOST", name, sig)
        cls = sorted({w[0] for w in wins})
        if len(fixtures) < 6 and not any(set(cls) & set(f["classes"]) for f in fixtures):
            fixtures.append({"function": name, "classes": cls, "source": node["source"], "diff": v.get("diff") or "",
                             "source_attribution": v.get("source_attribution"),
                             "improving": [w[1] for w in wins if w[1] in made]})
    print(json.dumps(dict(stats)))
    (FIXTURES / "evidence_site_cases.json").write_text(json.dumps(fixtures, indent=1))
    print("evidence_site fixtures:", [(f["function"], f["classes"]) for f in fixtures])

    hits = json.loads((HERE / "bytes-exact-frontend-rejected.json").read_text())
    frontend_cases, out = [], []
    for name, nodes in sorted(hits.items()):
        world = json.loads(Path(nodes[0]["world"]).read_text())["world"]
        node = next(n for n in world["nodes"] if n["id"] == nodes[0]["id"])
        fe = node["verdict"].get("frontend")
        made = list(frontend_type_repair.variants(node["source"], name, fe))
        print("frontend_type", name, [l for l, _c in made])
        frontend_cases.append({"function": name, "source": node["source"], "frontend": fe})
        for label, c in made:
            out.append({"function": name, "label": f"promoted:{label}", "source": c})
    (FIXTURES / "frontend_type_cases.json").write_text(json.dumps(frontend_cases, indent=1))
    (HERE / "probes-promoted-frontend.json").write_text(json.dumps(out, indent=1))
    print("frontend candidates to compile:", len(out))


if __name__ == "__main__":
    main()
