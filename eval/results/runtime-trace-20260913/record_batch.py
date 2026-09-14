"""Record several functions sequentially (Windows Python, repo root as cwd).

Each function gets its own fresh emulator session through eval.project64_trace;
sessions never overlap. `every` spreads the recorded calls across the demo so
they sample different game states, sized from the coverage probe's call counts.
"""
import json
from pathlib import Path
import urllib.request

from eval import project64_trace

OUT = Path("C:/Code/gameDecomp/eval/results/runtime-trace-20260913")
PORTABLE = "C:/Code/gameDecomp/eval/results/runtime-capture-20260912/portable-project64-v4"
ROM = "C:/Code/gameDecomp/eval/results/runtime-capture-20260912/pilot-rom.z64"

# (function, every) -- every = roughly calls-in-4-minutes / 8
BATCH = [
    ("initRaceUiBurstTextParticle", 3),
    ("updateRacePlayerMode16AerialTrick", 9),
    ("getRacePlayerRankingProgress", 90),
    ("getRaceCourseSurfaceHeight", 2400),
    ("drawRacePlayerModel", 90),
]

for function, every in BATCH:
    detail = json.load(urllib.request.urlopen("http://127.0.0.1:8765/api/function?name=" + function, timeout=60))
    (OUT / f"detail-{function}.json").write_text(json.dumps(detail, indent=2))
    job = {"schema_version": 1, "kind": "record", "function": function,
           "entry": detail["address"], "end": detail["address"] + detail["size"],
           "max_calls": 6, "max_events": 200000, "every": every, "duration_seconds": 420,
           "portable_dir": PORTABLE, "rom_path": ROM,
           "output_dir": str(OUT / f"record-{function}-1").replace("\\", "/")}
    receipt = project64_trace.run_job(job)
    print(json.dumps({"function": function, "status": receipt.get("status"),
                      "calls": len(receipt.get("calls", [])), "elapsed": round(receipt.get("elapsed_seconds", 0)),
                      "cleanup": receipt.get("cleanup")}), flush=True)
