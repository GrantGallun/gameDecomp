"""Print observed smoke scores and inference timing, without scoring a running arm."""
import json
import statistics
import subprocess
from pathlib import Path

root = Path.home() / "decomp/experiments/edit-capability-20261002"
print(subprocess.run(["ps", "-p", "22540,3769673", "-o", "pid,stat,etime,pcpu,args"], capture_output=True, text=True).stdout)
smoke = root / "logic-pilot-v3-smoke-completion-logits"
for arm in ("base", "mixed"):
    answers = [json.loads(line) for line in (smoke / f"answers_{arm}.jsonl").read_text().splitlines()]
    grades = [json.loads(line) for line in (smoke / f"grades_{arm}.jsonl").read_text().splitlines()]
    print(json.dumps({"arm": arm, "n": len(grades), "label": sum(r["label"] for r in grades),
                      "full": sum(r["rows"] for r in grades), "output_tokens": sum(r["completion_tokens"] for r in answers),
                      "request_wall_ms": [r["wall_ms"] for r in answers],
                      "receipt_tps_median": statistics.median(r["receipt"]["tokens_per_second"] for r in answers),
                      "receipt_tps_range": [min(r["receipt"]["tokens_per_second"] for r in answers),
                                             max(r["receipt"]["tokens_per_second"] for r in answers)],
                      "approx_aggregate_tps": sum(r["completion_tokens"] for r in answers) /
                          (max(r["receipt"]["created"] + r["wall_ms"] / 1000 for r in answers)
                           - min(r["receipt"]["created"] for r in answers)),
                      "per_kind": {kind: {"n": sum(r["kind"] == kind for r in grades),
                                            "full": sum(r["rows"] for r in grades if r["kind"] == kind)}
                                   for kind in sorted({r["kind"] for r in grades})}}, indent=2))
print(subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader"], capture_output=True, text=True).stdout)
