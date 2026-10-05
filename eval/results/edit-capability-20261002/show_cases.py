"""Print each planted case's original line and its perturbed line, for the named classes."""
import json
import sys

import run

want = set(sys.argv[1].split(",")) if len(sys.argv) > 1 else None
for c in map(json.loads, open(run.E / "cases.jsonl")):
    if want is None or c["class"] in want:
        print(f"{c['id']:70} | {c['original_text']}  =>  {c['site_text']}")
