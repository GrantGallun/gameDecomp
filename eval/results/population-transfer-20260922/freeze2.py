"""Stage 2 freeze: code-v2 (owner exposure) and cross-fitted family priors, before any stage-2 compile.

Two arms, each changing ONE thing relative to stage 1's `expanded` arm (same sources, scheduler, budget):
  routed  code-v2 `variants()` unchanged: the 18 unreached solver/rewrites.py owners are exposed.
  prior   code-v2 `variants()` minus owner families (== stage-1 expanded), with `prefer=` set from
          family improvement rates fitted on the OTHER half of the population (cross-fitted, so no
          function's own search informs its ordering).

Pre-registered prior rule: fold = sha256(name) mod 2. For a function in fold k, fit on expanded-arm
edges of fold 1-k at every depth; a family needs >= 10 edges; `improved` = child compiled and scored
above its parent. prefer = families whose rate exceeds the pooled rate, highest first.
"""
import collections
import hashlib
import json
from pathlib import Path
import shutil

OUT = Path(__file__).resolve().parent
SHARED = OUT.parents[2]
STAGE1 = json.loads((OUT / "freeze.json").read_text())
BASE = Path(STAGE1["code_root"])
DEST = BASE.parent / "code-v2"
NATIVE = BASE.parent
OVERLAYS = ["solver/owner_rewrites.py", "solver/regalloc_mutations.py"]
MIN_EDGES = 10


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fold(name: str) -> int:
    return int(sha(name.encode()), 16) % 2


def main():
    assert not DEST.exists() and not (OUT / "freeze2.json").exists()
    report = json.loads((OUT / "report.json").read_text())       # stage 1 must be complete
    rows = [r for r in report["rows"] if r["arm"] == "expanded"]
    assert len(rows) == STAGE1["population"], "stage 1 incomplete"
    shutil.copytree(BASE, DEST, ignore=shutil.ignore_patterns("__pycache__"))
    for relative in OVERLAYS:
        (DEST / relative).write_bytes((SHARED / relative).read_bytes())
    fixed = sorted((DEST / "solver").glob("*.py")) + sorted((DEST / "eval").glob("*.py"))
    fixed += sorted((DEST / "kb").glob("*.py")) + [DEST / "kb/schema.sql",
                                                    DEST / "eval/results/dream-search-20260922/pilot.py"]
    files = {str(p.relative_to(DEST)): sha(p.read_bytes()) for p in fixed}
    changed = sorted(p for p in files if STAGE1["files"].get(p) != files[p])
    assert changed == sorted(OVERLAYS), changed                   # exactly the two overlays differ

    edges = {0: collections.defaultdict(lambda: [0, 0]), 1: collections.defaultdict(lambda: [0, 0])}
    for r in rows:
        if not r.get("world"):
            continue
        world = json.loads(Path(r["world"]).read_text())["world"]
        nodes = {n["id"]: n for n in world["nodes"]}
        for n in world["nodes"]:
            if n["parent"] is None:
                continue
            parent = nodes[n["parent"]]["verdict"]
            improved = bool(n["verdict"]["compiled"] and n["verdict"]["score"] > parent["score"])
            cell = edges[fold(r["function"])][n["family"]]
            cell[0] += improved
            cell[1] += 1
    prefer = {}
    for k in (0, 1):
        train = {fam: v for fam, v in edges[1 - k].items() if v[1] >= MIN_EDGES}
        pooled = sum(v[0] for v in train.values()) / sum(v[1] for v in train.values())
        ranked = sorted(train, key=lambda fam: (-train[fam][0] / train[fam][1], fam))
        prefer[k] = {"pooled_rate": round(pooled, 4),
                     "rates": {fam: [train[fam][0], train[fam][1], round(train[fam][0] / train[fam][1], 4)]
                               for fam in ranked},
                     "prefer": [fam for fam in ranked if train[fam][0] / train[fam][1] > pooled]}
    manifest = {"stage1_freeze_sha256": sha((OUT / "freeze.json").read_bytes()),
                "stage1_report_sha256": sha((OUT / "report.json").read_bytes()),
                "code_root": str(DEST), "files": files, "overlays": OVERLAYS, "changed_vs_stage1": changed,
                "arms": {"routed": "code-v2 variants() unchanged",
                         "prior": "code-v2 variants() minus owner:* families, prefer= cross-fitted"},
                "fold_rule": "int(sha256(name), 16) % 2; fit on the other fold", "min_edges": MIN_EDGES,
                "prefer_by_fold": prefer, "budget_per_arm": STAGE1["budget_per_arm"],
                "max_depth": STAGE1["max_depth"], "scheduler": STAGE1["scheduler"],
                "min_free_gb_on_c": 3.0, "training_eligible": False, "model_calls": 0}
    (OUT / "freeze2.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"changed": changed, "prefer_fold0": prefer[0]["prefer"], "prefer_fold1": prefer[1]["prefer"],
                      "pooled": [prefer[0]["pooled_rate"], prefer[1]["pooled_rate"]]}, indent=1))


if __name__ == "__main__":
    main()
