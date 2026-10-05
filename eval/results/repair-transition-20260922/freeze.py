"""Freeze declared development evidence/code before the comparison panel."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

OUT = Path(__file__).resolve().parent
SHARED = OUT.parents[2]
BASE = Path.home() / "decomp/experiments/register-storage-20260922/code-v3"
DEST = Path.home() / "decomp/experiments/repair-transition-20260922/code-v1"
OVERLAYS = ["solver/repair_rules.py", "eval/repair_graph.py", "eval/repair_transitions.py",
            "eval/repair_planner.py", "eval/search_replay.py"]
FOLLOWUP = ["osSetIntMask", "osGetCount", "osGetCompare", "__osGetCause",
            "osSetTimer", "osStopTimer", "__osInsertTimer", "__osTimerInterrupt"]
DEVELOPMENT = ["paired-v3/motivation--osGetThreadPri--expanded.world.json",
               "paired-v3/retention--__osDequeueThread--expanded.world.json",
               "paired-v2/retention--__osDequeueThread--expanded.world.json"]


def main():
    assert not DEST.exists() and not (OUT / "freeze.json").exists()
    shutil.copytree(BASE, DEST, ignore=shutil.ignore_patterns("__pycache__"))
    for relative in OVERLAYS:
        (DEST / relative).write_bytes((SHARED / relative).read_bytes())
    sys.path.insert(0,str(DEST))
    from eval.search_replay import digest, load_world
    from eval.repair_graph import build_graph
    from eval.repair_transitions import fit, validate_model
    paths = [OUT.parent / "register-storage-20260922" / p for p in DEVELOPMENT]
    worlds = [load_world(p) for p in paths]
    graph = build_graph(worlds)
    model = fit(graph,development_targets={w["context"]["target_sha256"] for w in worlds})
    validate_model(model,graph=graph)
    (OUT / "development-graph.json").write_text(json.dumps(graph,indent=2))
    (OUT / "model.json").write_text(json.dumps(model,indent=2))
    fixed = sorted((DEST / "solver").glob("*.py")) + sorted((DEST / "eval").glob("*.py")) + sorted((DEST / "kb").glob("*.py"))
    fixed += [DEST / "kb/schema.sql",DEST / "eval/results/dream-search-20260922/pilot.py"]
    manifest = {"base":str(BASE),"code_root":str(DEST),"overlays":OVERLAYS,
        "files":{str(p.relative_to(DEST)):hashlib.sha256(p.read_bytes()).hexdigest() for p in fixed},
        "development_worlds":{str(p.relative_to(OUT.parent)):digest(w) for p,w in zip(paths,worlds)},
        "graph_sha256":graph["sha256"],"model_sha256":model["sha256"],
        "followup":FOLLOWUP,"budget_per_arm":32,"horizon":2,"preview":8,
        "scheduler":{"name":"depth-1.18754","mode":"depth","quantum":1.18754},
        "nproc":int(subprocess.check_output(["nproc"],text=True)),"workers":1,
        "training_eligible":False,"reference_body_supplied":False,
        "claim_scope":"opt-in controller; two exposed development targets; separate frozen followup names"}
    (OUT / "freeze.json").write_text(json.dumps(manifest,indent=2))
    print(json.dumps({"fixed_files":len(fixed),"nproc":manifest["nproc"],"nodes":len(graph["nodes"]),
                      "edges":len(graph["edges"]),"rows":len(model["rows"]),"followup":FOLLOWUP}))


if __name__ == "__main__":
    main()
