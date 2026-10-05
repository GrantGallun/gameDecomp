"""Which levels' model attempts solved the named DEVELOPMENT cases."""
import json
import sys

import run

want = set(sys.argv[1].split(","))
for name in ("attempts.jsonl", "attempts_localized.jsonl"):
    for r in map(json.loads, open(run.E / name)):
        if r["id"] in want:
            print(f"{name:26} {r['id']:45} {r['level']:3} {r['status']}")
