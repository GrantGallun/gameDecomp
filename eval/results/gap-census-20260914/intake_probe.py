"""Run the campaign intake for parked functions with main-tree code, isolated (no campaign writes).

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/gap-census-20260914/intake_probe.py FUNCTION...
"""
import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import campaign_workers, completion_campaign as campaign  # noqa: E402

names = sys.argv[1:]
if names[:1] == ["--from-file"]:
    names = Path(names[1]).read_text().split("\n")[0].split()
for function in names:
    native = Path(tempfile.mkdtemp(prefix="intake-probe-"))
    try:
        repo = campaign_workers.isolate(Path.home() / "decomp/sbk1", native / "game", function)
        db = native / "kb.sqlite"
        shutil.copy2(Path.home() / "decomp/kb-sbk1-parkedprobe-20260913.sqlite", db)
        result = campaign._intake(repo=repo, db=db, function=function, node={"status": "pending", "jobs": []},
                                  out=native / "intake.json")
        print(json.dumps({"function": function, "status": result.get("status"), "exact": result.get("exact"),
                          "score": result.get("score"), "blocker": result.get("blocker"),
                          "compiled": (result.get("residual") or {}).get("compiled"),
                          "faults": (result.get("residual") or {}).get("faults")})[:600])
    except Exception:
        print(function, "RAISED")
        traceback.print_exc(limit=6)
    finally:
        shutil.rmtree(native, ignore_errors=True)
