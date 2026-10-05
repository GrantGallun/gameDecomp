"""Print a DEVELOPMENT case's planted line and oracle diff (never run on heldout.jsonl)."""
import json
import sys

import run

assert "heldout" not in " ".join(sys.argv), "held-out cases are not inspected"
want = set(sys.argv[1].split(","))
for c in map(json.loads, open(run.E / "cases.jsonl")):
    if c["class"] in want or c["id"] in want:
        print("=" * 100)
        print(c["id"], "| original:", c["original_text"], "| now:", c["site_text"])
        print(c["diff"])
