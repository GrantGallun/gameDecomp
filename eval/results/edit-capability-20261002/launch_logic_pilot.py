"""Launch the authorized pilot detached after the successful integration smoke."""
import json
import os
from pathlib import Path
import subprocess
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
E = Path.home() / "decomp/experiments/edit-capability-20261002"
smoke = E / "logic-pilot-v3-smoke-completion-logits/status.json"
if json.loads(smoke.read_text())["stage"] != "complete":
    raise SystemExit("integration smoke is not complete")
builder = int((E / "public/context-v3.pid").read_text())
command = [str(Path.home() / "decomp/sbk1/.venv/bin/python"), str(HERE / "logic_pilot.py"),
           "--after-pid", str(builder), "--context", str(E / "public/context-v3.jsonl")]
with (E / "logic-pilot-v3.launch.log").open("x") as log:
    process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log,
                               stderr=subprocess.STDOUT, start_new_session=True,
                               env=os.environ | {"PYTHONUNBUFFERED": "1"})
receipt = {"pid": process.pid, "context_builder_pid": builder, "command": command,
           "launched_at": time.time(), "smoke_status": str(smoke),
           "status": str(E / "logic-pilot-v3/status.json")}
(E / "logic-pilot-v3.launch.json").write_text(json.dumps(receipt, indent=2))
print(json.dumps(receipt, indent=2))
