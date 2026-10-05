"""From the via_camera_global candidate, compile the full mutation stream (with evidence) until exact or 60 compiles."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import regalloc_mutations  # noqa: E402

HERE = Path(__file__).resolve().parent
start = next(p["source"] for p in json.loads((HERE / "probes-callback.json").read_text())
             if p["label"] == "callback:via_camera_global")
result = [json.loads(l) for l in (HERE.parents[0] / "population-transfer-20260922/probes.jsonl").read_text().splitlines()
          if "callback:via_camera_global" in l][-1]
probes, seen = [], {start}
for label, kind, cand in regalloc_mutations.variants(start, "waitCourseSelectRecordsClose", result["diff"]):
    if cand in seen:
        continue
    seen.add(cand)
    probes.append({"function": "waitCourseSelectRecordsClose", "label": f"finish:{kind}:{label}", "source": cand})
    if len(probes) >= 60:
        break
(HERE / "probes-finish.json").write_text(json.dumps(probes, indent=1))
print(len(probes))
