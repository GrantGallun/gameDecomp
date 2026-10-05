"""Print one exported rationale record (inputs abbreviated)."""
import json
import sys

import run

want = sys.argv[1]
for r in map(json.loads, open(run.E / "rationale_dev.jsonl")):
    if r["id"] == want:
        r["input"] = {"function": r["input"]["function"][:160] + " ...", "diff": "(oracle diff)"}
        for x in r["rationale"]:
            x["means"] = x["means"][:150] + " ..."
        print(json.dumps(r, indent=1))
