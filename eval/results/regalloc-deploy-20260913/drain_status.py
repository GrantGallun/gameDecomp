"""One line of drain status for the amendment: STATUS inflight parallel worker_pid."""
import json
import subprocess
import sys
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
out = subprocess.run([sys.executable, "/mnt/c/Code/gameDecomp/eval/campaign_service.py", "--run", str(RUN), "check"],
                     capture_output=True, text=True).stdout
health = json.loads(out)
service = json.loads((RUN / "service.json").read_text())
drained = (health.get("status") == "paused" and not health.get("inflight") and not health.get("parallel_inflight")
           and service.get("status") == "paused" and not service.get("worker_pid"))
print(f"{'DRAINED' if drained else 'waiting'} check={health.get('status')} parallel={len(health.get('parallel_inflight') or [])} "
      f"service={service.get('status')} worker={service.get('worker_pid')}")
